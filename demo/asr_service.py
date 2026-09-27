"""Cancelable, single-flight local ASR router for the Rust domain sidecar.

Preparation is explicit. Each transcription owns one fixed child process, with
an allowlisted environment and cached model only. Cancellation and disconnect
kill and reap that process before releasing the slot. This is process lifecycle
isolation, not a claim of OS network/filesystem confinement.
"""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import sys
import time
import uuid

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect
from . import asr

ROOT = Path(__file__).resolve().parents[1]
REQUEST_SECONDS = 90
PREPARE_SECONDS = 900


def worker_environment(command: str) -> dict[str, str]:
    # Never forward the parent API keys, proxy credentials, dotenv or arbitrary
    # Python import/startup configuration to the speech worker.
    names = {"SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "HOME", "USERPROFILE",
             "LOCALAPPDATA", "APPDATA", "HF_HOME", "HUGGINGFACE_HUB_CACHE", "XDG_CACHE_HOME"}
    env = {key: value for key, value in os.environ.items() if key.upper() in names}
    env.update(PYTHONUTF8="1", PYTHON_DOTENV_DISABLED="1", HF_HUB_DISABLE_TELEMETRY="1")
    env["CB_ASR_MODEL"] = asr.MODEL_NAME
    if command in {"transcribe", "transcribe-en"}:
        env.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    return env


@dataclass
class Job:
    request_id: str
    process: object = None
    cancelled: bool = False
    finished: asyncio.Event = field(default_factory=asyncio.Event)


class AsrService:
    def __init__(self):
        self.ready = False
        self.load_error = ""
        self.preparing = None
        self.prepare_process = None
        self.active: Job | None = None
        self.seen: OrderedDict[str, tuple[float, str]] = OrderedDict()

    def remember(self, request_id: str, state: str) -> None:
        self.seen[request_id] = (time.monotonic(), state)
        self.seen.move_to_end(request_id)
        while len(self.seen) > 256:
            self.seen.popitem(last=False)

    def prior(self, request_id: str) -> str:
        record = self.seen.get(request_id)
        return record[1] if record and time.monotonic() - record[0] < 600 else ""

    async def spawn(self, command: str):
        return await asyncio.create_subprocess_exec(sys.executable, "-m", "demo.asr_worker", command,
            cwd=str(ROOT), env=worker_environment(command), stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)

    @staticmethod
    async def reap(process) -> None:
        if process is not None:
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            await process.wait()

    async def status(self) -> dict:
        info = await run_in_threadpool(asr.status)
        if info["available"]:
            info["state"] = "loading" if self.preparing else "ready" if self.ready else "failed" if self.load_error else "cached" if info["state"] in {"ready", "cached"} else "missing"
        info.update(ok=True, loaded=False, load_error=self.load_error, supports_cancel=True,
                    cancellation="process", max_concurrent=1, active=bool(self.active),
                    worker_cached_only=True, os_confinement=False)
        return info

    @staticmethod
    def decode_result(output: bytes) -> dict:
        try:
            result = json.loads(output)
            if not isinstance(result, dict) or not isinstance(result.get("ok"), bool):
                raise ValueError()
            return result
        except (ValueError, UnicodeError):
            return {"ok": False, "status": 503, "detail": "本机识别子进程未返回有效结果"}

    async def _prepare(self) -> None:
        process = None
        try:
            process = await self.spawn("prepare")
            self.prepare_process = process
            output, _ = await asyncio.wait_for(process.communicate(), PREPARE_SECONDS)
            result = self.decode_result(output)
            self.ready = process.returncode == 0 and result["ok"]
            self.load_error = "" if self.ready else result.get("detail", "模型准备失败")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.load_error = "模型准备失败：" + type(exc).__name__
        finally:
            await self.reap(process)
            self.prepare_process = None
            self.preparing = None

    async def prepare(self) -> dict:
        info = await self.status()
        if not info["available"]:
            raise HTTPException(503, info["reason"])
        if self.ready:
            return {"ok": True, "state": "ready"}
        if not self.preparing:
            self.load_error = ""
            self.preparing = asyncio.create_task(self._prepare())
        return {"ok": True, "state": "loading"}

    async def cancel(self, request_id: str) -> dict:
        job = self.active
        if job and job.request_id == request_id:
            job.cancelled = True
            self.remember(request_id, "cancelled")
            await self.reap(job.process)
            await job.finished.wait()
            return {"ok": True, "request_id": request_id, "status": "cancelled", "process_reaped": True}
        previous = self.prior(request_id)
        if not previous:
            # Cancel may win the race against upload/spawn. Reserve the ID so a
            # late request cannot start after a successful cancellation response.
            self.remember(request_id, "cancelled")
        return {"ok": True, "request_id": request_id, "status": previous or "cancelled", "process_reaped": True}

    async def transcribe(self, request: Request, request_id: str) -> dict:
        language = request.headers.get("x-civil-asr-language", "zh")
        if language not in {"zh", "en"}:
            raise HTTPException(400, "Unsupported speech language")
        info = await self.status()
        if not info["available"]:
            raise HTTPException(503, info["reason"])
        if not self.ready:
            raise HTTPException(409, "请先准备本机识别模型，再开始录音")
        if self.prior(request_id):
            raise HTTPException(409, "这次语音请求已结束或已取消，请重新录音")
        if self.active:
            raise HTTPException(409, "正在识别上一段，请稍候再试")
        job = Job(request_id)
        self.active = job
        self.remember(request_id, "running")
        communication = None
        try:
            audio = bytearray()
            async def receive():
                async for chunk in request.stream():
                    if len(audio) + len(chunk) > asr.MAX_AUDIO_BYTES:
                        raise HTTPException(413, "录音不能超过 8 MB")
                    audio.extend(chunk)
            await asyncio.wait_for(receive(), 15)
            try:
                asr.check_size(len(audio))
            except asr.AsrInputError as exc:
                raise HTTPException(400, str(exc)) from exc
            if job.cancelled:
                raise HTTPException(409, "语音请求已取消")
            job.process = await self.spawn("transcribe-en" if language == "en" else "transcribe")
            if job.cancelled:
                raise HTTPException(409, "语音请求已取消")
            communication = asyncio.create_task(job.process.communicate(bytes(audio)))
            audio.clear()
            started = time.monotonic()
            while not communication.done():
                if job.cancelled or await request.is_disconnected():
                    job.cancelled = True
                    raise HTTPException(409, "语音请求已取消")
                if time.monotonic() - started > REQUEST_SECONDS:
                    raise HTTPException(504, "本机识别超时，已停止转写进程")
                await asyncio.wait({communication}, timeout=0.1)
            output, _ = await communication
            if job.cancelled:
                raise HTTPException(409, "语音请求已取消")
            result = self.decode_result(output)
            if job.process.returncode != 0 or not result["ok"]:
                raise HTTPException(result.get("status", 503), result.get("detail", "本机识别失败"))
            self.remember(request_id, "completed")
            return {**result, "request_id": request_id}
        except ClientDisconnect as exc:
            job.cancelled = True
            raise HTTPException(409, "录音上传已取消") from exc
        except asyncio.TimeoutError as exc:
            raise HTTPException(408, "录音上传超时") from exc
        finally:
            await self.reap(job.process)
            if communication and not communication.done():
                communication.cancel()
            if communication:
                await asyncio.gather(communication, return_exceptions=True)
            if self.prior(request_id) == "running":
                self.remember(request_id, "cancelled" if job.cancelled else "failed")
            if self.active is job:
                self.active = None
            job.finished.set()

    async def shutdown(self):
        if self.active:
            await self.cancel(self.active.request_id)
        if self.preparing:
            task = self.preparing
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


def valid_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value):
        raise HTTPException(400, "无效的语音请求标识")
    return value


service = AsrService()


@asynccontextmanager
async def lifespan(app):
    try:
        yield
    finally:
        await service.shutdown()


router = APIRouter(lifespan=lifespan)


@router.get("/api/asr/status")
async def status():
    return await service.status()


@router.post("/api/asr/prepare")
async def prepare():
    return await service.prepare()


@router.post("/api/asr")
async def transcribe(request: Request):
    return await service.transcribe(request, valid_id(request.headers.get("x-civil-asr-id") or uuid.uuid4().hex))


@router.post("/api/asr/{request_id}/cancel")
async def cancel(request_id: str):
    return await service.cancel(valid_id(request_id))

