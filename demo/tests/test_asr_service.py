"""Offline ASR lifecycle tests, including real child-process kill and reap."""
from __future__ import annotations

import asyncio
import io
import json
import sys
import types
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from demo import asr_service as module
from demo.asr_service import AsrService, worker_environment


class Request:
    disconnected = False

    async def stream(self):
        yield b"fixture-audio"

    async def is_disconnected(self):
        return self.disconnected


def ready_service():
    service = AsrService()
    service.ready = True
    async def status():
        return {"available": True, "state": "ready"}
    service.status = status
    return service


async def child(service, started, *, immediate=False):
    process = await asyncio.create_subprocess_exec(sys.executable, "-c",
        "import sys,time;sys.stdin.buffer.read();" + ("print('{\"ok\":true,\"text\":\"fixture\"}')" if immediate else "time.sleep(60)"),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        env=worker_environment("transcribe"))
    started.set_result(process)
    return process


def test_cancel_kills_and_reaps_real_child_and_rejects_late_reuse():
    async def scenario():
        service = ready_service(); started = asyncio.get_running_loop().create_future()
        service.spawn = lambda command: child(service, started)
        request = asyncio.create_task(service.transcribe(Request(), "request-one"))
        process = await started
        result = await service.cancel("request-one")
        assert result["process_reaped"] and process.returncode is not None
        with pytest.raises(HTTPException) as error:
            await request
        assert error.value.status_code == 409 and service.active is None
        with pytest.raises(HTTPException) as reused:
            await service.transcribe(Request(), "request-one")
        assert reused.value.status_code == 409
    asyncio.run(scenario())


def test_disconnect_kills_and_reaps_real_child():
    async def scenario():
        service = ready_service(); started = asyncio.get_running_loop().create_future(); req = Request()
        service.spawn = lambda command: child(service, started)
        task = asyncio.create_task(service.transcribe(req, "disconnect")); process = await started
        req.disconnected = True
        with pytest.raises(HTTPException):
            await task
        assert process.returncode is not None and service.active is None
    asyncio.run(scenario())


def test_single_flight_and_cancel_before_spawn_are_bounded():
    async def scenario():
        service = ready_service(); started = asyncio.get_running_loop().create_future()
        service.spawn = lambda command: child(service, started)
        task = asyncio.create_task(service.transcribe(Request(), "one")); await started
        try:
            with pytest.raises(HTTPException) as busy:
                await service.transcribe(Request(), "two")
            assert busy.value.status_code == 409
            await service.cancel("not-started")
        finally:
            await service.cancel("one")
            await asyncio.gather(task, return_exceptions=True)
        with pytest.raises(HTTPException) as late:
            await service.transcribe(Request(), "not-started")
        assert late.value.status_code == 409
    asyncio.run(scenario())


def test_success_and_input_guard_and_explicit_preparation():
    async def scenario():
        service = ready_service(); started = asyncio.get_running_loop().create_future()
        service.spawn = lambda command: child(service, started, immediate=True)
        result = await service.transcribe(Request(), "completed")
        assert result["text"] == "fixture" and result["request_id"] == "completed"
        assert service.active is None and (await started).returncode == 0
        service.ready = False
        with pytest.raises(HTTPException) as unprepared:
            await service.transcribe(Request(), "unprepared")
        assert unprepared.value.status_code == 409
        service.ready = True
        with patch.object(module.asr, "MAX_AUDIO_BYTES", 2):
            with pytest.raises(HTTPException) as large:
                await service.transcribe(Request(), "too-large")
            assert large.value.status_code == 413
        assert service.active is None
    asyncio.run(scenario())


def test_worker_environment_does_not_forward_model_keys_or_proxy_credentials(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")
    monkeypatch.setenv("HTTPS_PROXY", "https://test-secret.invalid")
    monkeypatch.setenv("PYTHONPATH", "untrusted")
    env = worker_environment("transcribe")
    assert not any("KEY" in key or "TOKEN" in key or "PROXY" in key for key in env)
    assert "PYTHONPATH" not in env and env["HF_HUB_OFFLINE"] == "1"


def test_transcription_worker_uses_only_cached_model(monkeypatch, capsys):
    from demo import asr_worker
    calls = []
    monkeypatch.setattr(module.asr, "engine_error", lambda: "")
    monkeypatch.setattr(module.asr, "_model", None)
    monkeypatch.setattr(module.asr, "transcribe", lambda data: {"text": "cached fixture"})
    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=lambda *args, **kwargs: calls.append(kwargs)))
    monkeypatch.setattr(sys, "stdin", types.SimpleNamespace(buffer=io.BytesIO(b"audio")))
    monkeypatch.setattr(sys, "argv", ["asr_worker", "transcribe"])
    asr_worker.main()
    assert calls[0]["local_files_only"] is True
    assert json.loads(capsys.readouterr().out)["text"] == "cached fixture"


def test_missing_dependency_status_never_claims_ready(monkeypatch):
    monkeypatch.setattr(module.asr, "status", lambda: {"available": False, "state": "unavailable", "reason": "missing dependency"})
    service = AsrService(); service.ready = True
    status = asyncio.run(service.status())
    assert not status["available"] and status["state"] == "unavailable"
    assert status["supports_cancel"] and status["os_confinement"] is False


def test_router_preserves_protocol_and_validates_request_ids(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    service = ready_service()
    async def spawn(command):
        future = asyncio.get_running_loop().create_future()
        return await child(service, future, immediate=True)
    service.spawn = spawn
    monkeypatch.setattr(module, "service", service)
    app = FastAPI(); app.include_router(module.router)
    with TestClient(app) as client:
        assert client.get("/api/asr/status").json()["state"] == "ready"
        assert client.post("/api/asr/prepare").json()["state"] == "ready"
        reply = client.post("/api/asr", content=b"audio", headers={"X-Civil-ASR-ID": "http-one"})
        assert reply.status_code == 200 and reply.json()["request_id"] == "http-one"
        assert client.post("/api/asr/http-one/cancel").json()["status"] == "completed"
        assert client.post("/api/asr/pre-cancel/cancel").json()["status"] == "cancelled"
        assert client.post("/api/asr", content=b"audio", headers={"X-Civil-ASR-ID": "pre-cancel"}).status_code == 409
        assert client.post("/api/asr", content=b"audio", headers={"X-Civil-ASR-ID": "bad/id"}).status_code == 400
