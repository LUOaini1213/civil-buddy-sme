"""Voice input backend: /api/asr contract, failure mapping, decoder guards, lexicon prompt budget.

Endpoint tests swap in a fake model; nothing here loads Whisper. Decoder tests need
faster-whisper (CI installs requirements-asr.txt) and skip themselves without it.
The frontend's "fill, never send" behaviour is exercised in test_voice_js.py.
"""

from __future__ import annotations

import io
import re
import sys
import time
import wave
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import asr  # noqa: E402

STATIC = ROOT / "static"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import app
    import uploads

    monkeypatch.setattr(app, "OUT_ROOT", tmp_path / "out")
    monkeypatch.setattr(uploads, "UPLOAD_ROOT", tmp_path / "out")  # attachments live inside the session dir
    return TestClient(app.app, raise_server_exceptions=False)


class _Segment:
    def __init__(self, text: str, temperature: float = 0.0):
        self.text = text
        self.temperature = temperature
        self.avg_logprob = -0.2


class _FakeModel:
    def __init__(self, pieces, error: Exception | None = None):
        self.pieces = pieces
        self.error = error
        self.calls = []

    def transcribe(self, audio, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return iter(_Segment(p) for p in self.pieces), None


@pytest.fixture()
def fake_engine(monkeypatch):
    np = pytest.importorskip("numpy")
    model = _FakeModel(["请核对这批货能不能装进", "一个高柜。"])
    monkeypatch.setattr(asr, "engine_error", lambda: "")
    monkeypatch.setattr(asr, "decode", lambda data: np.zeros(asr.SAMPLE_RATE * 2, dtype="float32"))
    monkeypatch.setattr(asr, "_get_model", lambda: model)
    return model


def _wav(seconds: float, rate: int = asr.SAMPLE_RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x01\x00" * int(seconds * rate))
    return buf.getvalue()


# ---------- endpoint contract ----------

def test_health_and_status_agree(client):
    caps = client.get("/api/health").json()["capabilities"]
    status = client.get("/api/asr/status").json()
    assert caps["asr"] is asr.engine_installed()
    assert status["available"] is (asr.engine_error() == "")
    assert status["stores_audio"] is False and status["max_seconds"] == asr.MAX_SECONDS


def test_status_says_when_engine_missing(client, monkeypatch):
    monkeypatch.setattr(asr, "engine_error", lambda: "本机未安装 faster-whisper")
    body = client.get("/api/asr/status").json()
    assert body["available"] is False and body["state"] == "unavailable"
    assert "浏览器" in body["reason"]


def test_transcribe_returns_text_and_uses_lexicon(client, fake_engine, tmp_path):
    r = client.post("/api/asr", content=b"fake-webm-bytes", headers={"Content-Type": "audio/webm"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["text"] == "请核对这批货能不能装进一个高柜。"
    assert body["lexicon"] is True and body["audio_seconds"] == 2.0
    call = fake_engine.calls[0]
    assert call["language"] == "zh"
    assert call["initial_prompt"] == asr.build_prompt(asr.load_lexicon())
    assert "高柜" in call["initial_prompt"] and "坍落度" in call["initial_prompt"]
    # Text only: no chat, no session, no saved upload, no output file anywhere under the test roots.
    assert not [p for p in tmp_path.rglob("*") if p.is_file()]


def test_empty_upload_is_rejected(client, fake_engine):
    r = client.post("/api/asr", content=b"")
    assert r.status_code == 400 and "没有收到录音" in r.json()["detail"]


def test_oversize_upload_never_reaches_transcribe(client, monkeypatch):
    calls = []
    monkeypatch.setattr(asr, "MAX_AUDIO_BYTES", 16)
    monkeypatch.setattr(asr, "transcribe", lambda data, **k: calls.append(len(data)) or {"text": ""})
    r = client.post("/api/asr", content=b"x" * 17)
    assert r.status_code == 413 and calls == []  # the endpoint's own streaming cap, not transcribe's


def test_missing_engine_returns_503(client, monkeypatch):
    monkeypatch.setattr(asr, "engine_error", lambda: "本机未安装 faster-whisper")
    r = client.post("/api/asr", content=_wav(1.0))
    assert r.status_code == 503 and "faster-whisper" in r.json()["detail"]


def test_broken_native_dependency_is_503_not_500(client, monkeypatch):
    """faster-whisper present but onnxruntime (VAD) cannot import: the page must see 503 and fall back."""
    monkeypatch.setitem(sys.modules, "onnxruntime", None)
    monkeypatch.setattr(asr, "_probe_error", None)
    status = client.get("/api/asr/status").json()
    assert status["available"] is False
    r = client.post("/api/asr", content=_wav(1.0))
    assert r.status_code == 503, r.text
    assert r.headers["content-type"].startswith("application/json")


def test_runtime_failure_in_the_model_is_503(client, fake_engine, monkeypatch):
    monkeypatch.setattr(asr, "_get_model", lambda: _FakeModel([], RuntimeError("VAD needs onnxruntime")))
    r = client.post("/api/asr", content=b"audio")
    assert r.status_code == 503 and "RuntimeError" in r.json()["detail"]


def test_unexpected_error_is_503_json(client, monkeypatch):
    def boom(data, **kwargs):
        raise KeyError("surprise")

    monkeypatch.setattr(asr, "transcribe", boom)
    r = client.post("/api/asr", content=b"audio")
    assert r.status_code == 503 and r.headers["content-type"].startswith("application/json")


def test_not_ready_returns_409_and_starts_preparing(client, monkeypatch):
    np = pytest.importorskip("numpy")
    started = []
    monkeypatch.setattr(asr, "engine_error", lambda: "")
    monkeypatch.setattr(asr, "decode", lambda data: np.zeros(asr.SAMPLE_RATE, dtype="float32"))
    monkeypatch.setattr(asr, "_model", None)
    monkeypatch.setattr(asr, "_load_error", None)
    monkeypatch.setattr(asr, "prepare", lambda: started.append(1) or "loading")
    r = client.post("/api/asr", content=b"audio")
    assert r.status_code == 409 and started == [1]


def test_busy_returns_429(client, fake_engine, monkeypatch):
    monkeypatch.setattr(asr, "RUN_WAIT_SECONDS", 0.05)
    assert asr._run_lock.acquire(timeout=1)
    try:
        r = client.post("/api/asr", content=b"audio")
    finally:
        asr._run_lock.release()
    assert r.status_code == 429


def test_prepare_endpoint(client, monkeypatch):
    monkeypatch.setattr(asr, "prepare", lambda: "loading")
    assert client.post("/api/asr/prepare").json() == {"ok": True, "state": "loading"}

    def unavailable():
        raise asr.AsrUnavailable("本机未安装 faster-whisper")

    monkeypatch.setattr(asr, "prepare", unavailable)
    assert client.post("/api/asr/prepare").status_code == 503


def test_model_load_failure_is_remembered_not_retried_per_request(monkeypatch):
    fw = pytest.importorskip("faster_whisper")
    attempts = []

    class Broken:
        def __init__(self, *a, **k):
            attempts.append(1)
            raise OSError("huggingface.co unreachable")

    monkeypatch.setattr(fw, "WhisperModel", Broken)
    monkeypatch.setattr(asr, "_model", None)
    monkeypatch.setattr(asr, "_load_error", None)
    monkeypatch.setattr(asr, "_loading", True)
    asr._load()
    assert asr.model_state() == "failed" and asr.status()["load_error"]
    for _ in range(3):
        with pytest.raises(asr.AsrUnavailable):
            asr._get_model()
        assert asr.prepare() == "failed"
    assert attempts == [1]


def test_run_model_is_the_shared_decode_path():
    model = _FakeModel([" a", "b "])
    assert asr.run_model(model, [0.0], "提示") == "ab"
    assert model.calls[0] == {"initial_prompt": "提示", **asr.DECODE_OPTIONS}
    detail = asr.run_model_detail(_FakeModel(["x"]), [0.0], None)
    assert detail["temperature"] == 0.0


# ---------- lexicon prompt budget ----------

def test_lexicon_loads_without_comments_or_duplicates(tmp_path):
    f = tmp_path / "lex.txt"
    f.write_text("# 注释\n高柜\n\n高柜  # 重复\n坍落度\n", encoding="utf-8")
    assert asr.load_lexicon(f) == ["高柜", "坍落度"]
    assert asr.build_prompt([]) == asr.GENERIC_PROMPT
    shipped = asr.build_prompt(["高柜"])
    assert shipped.startswith(asr.domain_prompt()[:-1]) and shipped.endswith("高柜。")


def test_missing_lexicon_is_unavailable_not_500(client, fake_engine, monkeypatch, tmp_path):
    monkeypatch.setattr(asr, "LEXICON_PATH", tmp_path / "gone.txt")
    monkeypatch.setattr(asr, "load_lexicon", lambda path=None: (tmp_path / "gone.txt").read_text())
    assert client.get("/api/asr/status").json()["available"] is False
    assert client.post("/api/asr", content=b"audio").status_code == 503


def test_prompt_length_proxy_holds_in_ci():
    # Real count (Whisper tokenizer, eval/asr/run_eval.py): 164 characters -> 222 tokens,
    # limit 223. The tokenizer is not in CI, so hold the character length that was measured.
    # Adding a term fails here on purpose: re-measure with the real tokenizer first.
    assert len(asr.build_prompt(asr.load_lexicon())) <= 164


def test_prompt_fits_real_tokenizer():
    fw = pytest.importorskip("faster_whisper")
    try:
        model = fw.WhisperModel(asr.MODEL_NAME, device="cpu", compute_type="int8", local_files_only=True)
    except Exception:
        pytest.skip("model not cached locally")
    prompt = asr.build_prompt(asr.load_lexicon())
    # faster-whisper encodes " " + prompt.strip() and keeps the last PROMPT_TOKEN_LIMIT tokens.
    n = len(model.hf_tokenizer.encode(" " + prompt.strip(), add_special_tokens=False).ids)
    assert n <= asr.PROMPT_TOKEN_LIMIT, n


# ---------- real decoder guards ----------

@pytest.fixture()
def real_decoder():
    pytest.importorskip("faster_whisper")
    if asr.engine_error():
        pytest.skip(asr.engine_error())


def _peak_mb(fn):
    import tracemalloc

    tracemalloc.start()
    try:
        fn()
    finally:
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
    return peak / 1048576


def test_decode_rejects_undecodable_bytes(real_decoder):
    with pytest.raises(asr.AsrInputError):
        asr.decode(b"this is not audio at all")


def test_decode_limits_trim_inside_grace_and_reject_beyond(real_decoder):
    assert len(asr.decode(_wav(1.0))) == asr.SAMPLE_RATE
    limit = int(asr.MAX_SECONDS * asr.SAMPLE_RATE)
    # A stop that lands a little late (background-tab timer, encoder padding) keeps the dictation.
    assert len(asr.decode(_wav(asr.MAX_SECONDS + 0.06))) == limit
    assert len(asr.decode(_wav(asr.MAX_SECONDS + asr.GRACE_SECONDS - 0.2))) == limit
    with pytest.raises(asr.AsrInputError, match="太短"):
        asr.decode(_wav(0.1))
    with pytest.raises(asr.AsrInputError, match=f"{asr.MAX_SECONDS:g} 秒"):
        asr.decode(_wav(asr.MAX_SECONDS + asr.GRACE_SECONDS + 1))


def _low_bitrate_opus(seconds: int) -> bytes:
    """Minutes of near-silence at 6 kbps: small upload, long audio."""
    av = pytest.importorskip("av")
    np = pytest.importorskip("numpy")
    buf = io.BytesIO()
    with av.open(buf, "w", format="webm") as dst:
        stream = dst.add_stream("libopus", rate=48_000, layout="mono")
        stream.bit_rate = 6_000
        block = np.zeros((1, 48_000), dtype=np.int16)
        block[0, ::97] = 40  # not pure digital silence, so the encoder keeps emitting frames
        for i in range(seconds):
            frame = av.AudioFrame.from_ndarray(block, format="s16", layout="mono")
            frame.sample_rate = 48_000
            frame.pts = i * 48_000
            for packet in stream.encode(frame):
                dst.mux(packet)
        for packet in stream.encode(None):
            dst.mux(packet)
    return buf.getvalue()


def test_decode_stops_early_on_long_low_bitrate_audio(real_decoder):
    """8 MB of 6 kbps opus is hours of audio; decoding it all before the length check
    would allocate hundreds of MB. Measured: the old decode_audio-then-check peaked at 46 MB
    for this 5-minute clip; stopping at the limit stays near 1 MB."""
    data = _low_bitrate_opus(300)
    assert len(data) < asr.MAX_AUDIO_BYTES

    def run():
        with pytest.raises(asr.AsrInputError, match="秒"):
            asr.decode(data)

    assert _peak_mb(run) < 8


def test_decode_refuses_tiny_sample_rate_headers(real_decoder):
    """A WAV header claiming 20 Hz turns 60 KB into ~24M samples at 16 kHz (512 MB peak before)."""
    data = _wav(1500, rate=20)

    def run():
        with pytest.raises(asr.AsrInputError):
            asr.decode(data)

    assert _peak_mb(run) < 8
    # The per-frame duration check above is what bounds memory; the 8–192 kHz range is a second
    # layer. It alone rejects a short file whose header rate no microphone produces.
    with pytest.raises(asr.AsrInputError):
        asr.decode(_wav(2.0, rate=100))


def test_decode_refuses_network_and_file_demuxers(real_decoder):
    """SDP made FFmpeg bind a UDP port and wait ~20 s; concat reads local files. Pipe-only now."""
    sdp = b"v=0\r\no=- 0 0 IN IP4 127.0.0.1\r\ns=x\r\nc=IN IP4 127.0.0.1\r\nt=0 0\r\nm=audio 55311 RTP/AVP 0\r\n"
    concat = b"ffconcat version 1.0\nfile 'C:/Windows/win.ini'\n"
    for body in (sdp, concat):
        started = time.perf_counter()
        with pytest.raises(asr.AsrInputError):
            asr.decode(body)
        assert time.perf_counter() - started < 2


def test_decode_refuses_containers_outside_the_allowlist(real_decoder):
    av = pytest.importorskip("av")
    np = pytest.importorskip("numpy")
    buf = io.BytesIO()
    with av.open(buf, "w", format="flac") as dst:
        stream = dst.add_stream("flac", rate=16_000, layout="mono")
        frame = av.AudioFrame.from_ndarray(np.zeros((1, 16_000), dtype=np.int16), format="s16", layout="mono")
        frame.sample_rate = 16_000
        for packet in stream.encode(frame):
            dst.mux(packet)
        for packet in stream.encode(None):
            dst.mux(packet)
    with pytest.raises(asr.AsrInputError, match="不支持"):
        asr.decode(buf.getvalue())


def test_real_decode_path_writes_no_file(real_decoder, monkeypatch):
    """docs promise the audio is never written; watch every open() for writing during a real decode."""
    writes = []
    watching = [True]

    def hook(event, args):
        if watching[0] and event == "open" and len(args) > 1 and isinstance(args[1], str) and set(args[1]) & set("wax+"):
            writes.append(args[0])

    sys.addaudithook(hook)
    monkeypatch.setattr(asr, "_get_model", lambda: _FakeModel(["好"]))
    try:
        assert asr.transcribe(_wav(1.0))["text"] == "好"
    finally:
        watching[0] = False
    assert writes == []


# ---------- page wiring ----------

def test_page_loads_voice_after_app_and_keeps_status_in_a11y_tree():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert 'id="btnVoice"' in html and 'id="voiceInterim"' in html and 'id="voiceLabel"' in html
    button = re.search(r'<button type="button" id="btnVoice".*?</button>', html, re.S).group(0)
    assert "<svg" in button and 'aria-label="语音输入"' in button  # microphone icon, still named for screen readers
    assert html.index("/static/app.js") < html.index("/static/voice.js")
    status = re.search(r'<p id="voiceStatus"[^>]*>', html).group(0)
    assert 'aria-live="polite"' in status and "hidden" not in status


def test_client_stops_before_the_server_limit():
    js = (STATIC / "voice.js").read_text(encoding="utf-8")
    seconds = int(re.search(r"const MAX_SECONDS = (\d+);", js).group(1))
    assert seconds == asr.MAX_SECONDS
    assert "const MAX_MS = (MAX_SECONDS - 1) * 1000;" in js  # 1 s early, plus the server's grace


def test_english_locale_controls_decode_without_mandarin_lexicon(client, fake_engine):
    response = client.post("/api/asr", content=b"synthetic-audio", headers={"x-civil-asr-language": "en"})
    assert response.status_code == 200
    assert response.json()["language"] == "en"
    assert response.json()["lexicon"] is False
    assert fake_engine.calls[0]["language"] == "en"
    assert "English speech" in fake_engine.calls[0]["initial_prompt"]


def test_unsupported_speech_language_is_refused_before_decode(client, fake_engine):
    response = client.post("/api/asr", content=b"synthetic-audio", headers={"x-civil-asr-language": "invalid"})
    assert response.status_code == 400
    assert fake_engine.calls == []
