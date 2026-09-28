"""Local speech-to-text for the workbench composer (optional).

Voice input only fills the text box. The transcript comes back as plain text; the
person reads it, fixes it if needed, and presses send themselves. Nothing in this
module calls the language model, routes a task, or writes a file — the audio is
decoded from memory and dropped when the request ends.

Engine: faster-whisper on CPU (int8). It is an optional dependency
(``pip install -r requirements-asr.txt``). When it is missing or broken, ``status()``
says so and the page falls back to the browser's own recognizer, which the UI
discloses before first use.

The model (~460 MB on first use) is loaded by ``prepare()`` in a background thread,
never inside a transcription request: the page waits for ``state == "ready"`` before
it starts recording, so a slow or failed download never costs the person a dictation.

The domain prompt: Whisper mishears trade terms as everyday words (高柜 → 高贵,
坍落度 → 贪落度). ``build_prompt`` puts a fixed term list into Whisper's
``initial_prompt``. ``eval/asr`` measures what that list does and does not fix.
"""
from __future__ import annotations

import gc
import io
import os
import threading
import time
from importlib.util import find_spec
from pathlib import Path

LEXICON_PATH = Path(__file__).with_name("asr_lexicon.txt")
GENERIC_PROMPT = "以下是普通话的句子。"
DOMAIN = "涉及土木施工、投标与装柜物流"
SAMPLE_RATE = 16_000
MAX_AUDIO_BYTES = 8 * 1024 * 1024
MIN_SECONDS = 0.3
# Why 20 s, not 30: CTranslate2 stops each Whisper window at 224 new tokens, timestamps
# included, whatever the prompt length. Mandarin runs ~1.4 tokens per character; at a
# fast 6 characters a second, 20 s is ~170 text tokens plus timestamps, inside the cap.
# At 30 s a fast talker could overrun it, and faster-whisper then decodes a second
# window whose prompt no longer holds the whole term list, or drops the tail.
MAX_SECONDS = 20.0
# The page stops recording one second early, but timers run late (Chrome aligns them
# to 1 s in a background tab) and encoders pad. Audio up to MAX + GRACE is trimmed to
# MAX instead of rejected, so a late stop never throws away the whole dictation.
GRACE_SECONDS = 2.0
# Whisper keeps at most 223 prompt tokens and silently drops the rest from the front.
PROMPT_TOKEN_LIMIT = 223
DEFAULT_MODEL = "small"
MODEL_NAME = os.getenv("CB_ASR_MODEL", DEFAULT_MODEL)
DEVICE = "cpu"
COMPUTE_TYPE = "int8"
DECODE_OPTIONS = {"language": "zh", "beam_size": 5, "vad_filter": True}
# Containers MediaRecorder produces (Chrome/Edge webm, Firefox ogg, Safari mp4) plus wav.
# Anything else is refused before a single frame is decoded.
ALLOWED_FORMATS = {"matroska", "webm", "ogg", "wav", "mov", "mp4", "m4a"}
MIN_RATE, MAX_RATE, MAX_CHANNELS = 8_000, 192_000, 8
LOAD_RETRY_SECONDS = 300
RUN_WAIT_SECONDS = 60


class AsrUnavailable(RuntimeError):
    """The local engine is missing, broken, or failed while running."""


class AsrInputError(ValueError):
    """The upload is empty, not an allowed audio container, too short, or too long."""


class AsrTooLarge(AsrInputError):
    """The upload exceeds MAX_AUDIO_BYTES."""


class AsrNotReady(RuntimeError):
    """The model is not loaded yet; call prepare() and wait for state == 'ready'."""


class AsrBusy(RuntimeError):
    """Another transcription held the CPU for longer than RUN_WAIT_SECONDS."""


# ---------------- prompt ----------------

def load_lexicon(path: Path = LEXICON_PATH) -> list[str]:
    """Terms from the lexicon file: one per line, ``#`` starts a comment, duplicates dropped."""
    terms: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        term = line.split("#", 1)[0].strip()
        if term and term not in terms:
            terms.append(term)
    return terms


def build_prompt(terms: list[str] | None) -> str:
    """What ships: generic Mandarin cue + domain + the term list. Without terms, only the cue."""
    if not terms:
        return GENERIC_PROMPT
    return GENERIC_PROMPT + DOMAIN + "：" + "、".join(terms) + "。"


def domain_prompt() -> str:
    """The shipped prompt minus the term list; eval/asr uses it to separate the two effects."""
    return GENERIC_PROMPT + DOMAIN + "。"


# ---------------- engine probe ----------------

_probe_lock = threading.Lock()
_probe_error: str | None = None


def engine_error() -> str:
    """Empty when the engine imports; otherwise a short reason. Imported once, then cached.

    find_spec alone is not enough: a package that is present but cannot load its native
    parts (ctranslate2, onnxruntime for VAD) would report available and then fail with 500.
    """
    global _probe_error
    if find_spec("faster_whisper") is None:
        return "本机未安装 faster-whisper"
    if _probe_error is None:
        with _probe_lock:
            if _probe_error is None:
                try:
                    import av  # noqa: F401
                    import faster_whisper  # noqa: F401  (loads ctranslate2, tokenizers)
                    import numpy  # noqa: F401
                    import onnxruntime  # noqa: F401  (the VAD filter needs it)

                    _probe_error = ""
                except Exception as exc:
                    _probe_error = f"语音识别引擎无法加载：{type(exc).__name__}"
    return _probe_error


def engine_installed() -> bool:
    return engine_error() == ""


# ---------------- model lifecycle ----------------

_load_lock = threading.Lock()
_run_lock = threading.Lock()
_model = None
_loading = False
_load_error: tuple[str, float] | None = None


def _recent_failure() -> str:
    if _load_error and time.time() - _load_error[1] < LOAD_RETRY_SECONDS:
        return _load_error[0]
    return ""


def model_cached() -> bool:
    """Is the model on disk? No network call."""
    try:
        from faster_whisper.utils import download_model

        download_model(MODEL_NAME, local_files_only=True)
        return True
    except Exception:
        return False


def model_state() -> str:
    """ready | loading | failed | cached (on disk, not loaded) | missing (needs a download)."""
    if _model is not None:
        return "ready"
    if _loading:
        return "loading"
    if _recent_failure():
        return "failed"
    return "cached" if model_cached() else "missing"


def _load() -> None:
    global _model, _loading, _load_error
    try:
        from faster_whisper import WhisperModel

        _model = WhisperModel(MODEL_NAME, device=DEVICE, compute_type=COMPUTE_TYPE)
        _load_error = None
    except Exception as exc:  # download failure, blocked network, corrupt cache, unsupported CPU
        _load_error = (f"语音识别模型加载失败：{type(exc).__name__}", time.time())
    finally:
        _loading = False


def prepare() -> str:
    """Start loading (and, if needed, downloading) the model in the background. Idempotent."""
    global _loading
    reason = engine_error()
    if reason:
        raise AsrUnavailable(reason)
    with _load_lock:
        if _model is not None or _loading:
            return model_state()
        if _recent_failure():
            return "failed"
        _loading = True
        threading.Thread(target=_load, name="asr-model-load", daemon=True).start()
    return "loading"


def _get_model():
    if _model is not None:
        return _model
    failure = _recent_failure()
    if failure:
        raise AsrUnavailable(failure)
    prepare()
    raise AsrNotReady("识别模型正在准备，准备好之前先不要录音")


def status() -> dict:
    reason = engine_error()
    terms = 0
    if not reason:
        try:
            terms = len(load_lexicon())
        except OSError:
            reason = "缺少术语表 demo/asr_lexicon.txt"
    available = not reason
    state = model_state() if available else "unavailable"
    return {
        "available": available,
        "state": state,
        "engine": "faster-whisper",
        "model": MODEL_NAME,
        "loaded": _model is not None,
        "load_error": _recent_failure(),
        "lexicon_terms": terms,
        "max_seconds": MAX_SECONDS,
        "stores_audio": False,
        "reason": reason + ("，页面会改用浏览器自带识别" if reason else ""),
    }


# ---------------- decoding ----------------

def check_size(n_bytes: int) -> None:
    if n_bytes <= 0:
        raise AsrInputError("没有收到录音")
    if n_bytes > MAX_AUDIO_BYTES:
        raise AsrTooLarge("录音不能超过 8 MB")


def _audio_frames(container):
    """Decoded frames of the first audio stream, skipping frames FFmpeg flags as invalid."""
    import av

    frames = container.decode(audio=0)
    while True:
        try:
            yield next(frames)
        except StopIteration:
            return
        except av.error.InvalidDataError:
            continue


def decode(data: bytes):
    """Bytes of a MediaRecorder container (webm/ogg/mp4) or wav → 16 kHz mono float32, at most MAX_SECONDS.

    Guards, all before the audio can grow in memory:
    - FFmpeg may only read the in-memory pipe (protocol_whitelist): demuxers such as SDP or
      concat would otherwise open UDP ports or local files on the attacker's say-so.
    - The container must be one of ALLOWED_FORMATS.
    - Each frame's own sample rate must be 8–192 kHz and its duration is added up at the
      source rate, so a header claiming 20 Hz cannot turn 60 KB into gigabytes of 16 kHz audio.
    - Decoding stops as soon as the source passes MAX_SECONDS + GRACE_SECONDS.
    """
    check_size(len(data))
    reason = engine_error()
    if reason:
        raise AsrUnavailable(reason)
    import av
    import numpy as np

    too_long = AsrInputError(f"一段录音最长 {MAX_SECONDS:g} 秒，请分段说（再点一次语音，文字会接在后面）")
    hard_seconds = MAX_SECONDS + GRACE_SECONDS
    resampler = av.audio.resampler.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
    pieces: list = []
    source_seconds = 0.0
    try:
        with av.open(io.BytesIO(data), mode="r", metadata_errors="ignore",
                     options={"protocol_whitelist": "pipe"}) as container:
            if not set(container.format.name.split(",")) & ALLOWED_FORMATS:
                raise AsrInputError("不支持这种音频格式")
            if not container.streams.audio:
                raise AsrInputError("录音里没有音轨")
            for frame in _audio_frames(container):
                rate = frame.sample_rate or 0
                if not MIN_RATE <= rate <= MAX_RATE or len(frame.layout.channels) > MAX_CHANNELS:
                    raise AsrInputError("无法解码这段录音")
                source_seconds += frame.samples / rate
                if source_seconds > hard_seconds:
                    raise too_long
                pieces.extend(p.to_ndarray().reshape(-1) for p in resampler.resample(frame))
            pieces.extend(p.to_ndarray().reshape(-1) for p in resampler.resample(None))
    except AsrInputError:
        raise
    except Exception as exc:
        raise AsrInputError("无法解码这段录音") from exc
    finally:
        del resampler
        gc.collect()  # faster-whisper does the same: resampler objects are not freed otherwise
    audio = (np.concatenate(pieces) if pieces else np.zeros(0, dtype=np.int16)).astype(np.float32) / 32768.0
    audio = audio[: int(MAX_SECONDS * SAMPLE_RATE)]  # a late stop inside the grace window is trimmed, not refused
    if len(audio) / SAMPLE_RATE < MIN_SECONDS:
        raise AsrInputError("录音太短，请说完一句再停")
    return audio


# ---------------- transcription ----------------

def run_model_detail(model, audio, prompt: str | None, *, language: str = "zh") -> dict:
    """The one decode call shared by the endpoint and eval/asr, so the eval measures what ships.

    Also reports whether Whisper fell back to sampling (temperature > 0), which is random."""
    if language not in {"zh", "en"}:
        raise AsrInputError("Unsupported speech language")
    segments = list(model.transcribe(audio, initial_prompt=prompt, **{**DECODE_OPTIONS, "language": language})[0])
    return {
        "text": "".join(segment.text for segment in segments).strip(),
        "temperature": max((getattr(s, "temperature", 0.0) or 0.0 for s in segments), default=0.0),
        "avg_logprob": min((getattr(s, "avg_logprob", 0.0) for s in segments), default=0.0),
    }


def run_model(model, audio, prompt: str | None, *, language: str = "zh") -> str:
    return run_model_detail(model, audio, prompt, language=language)["text"]


def transcribe(data: bytes, *, use_lexicon: bool = True, language: str = "zh") -> dict:
    if language not in {"zh", "en"}:
        raise AsrInputError("Unsupported speech language")
    check_size(len(data))  # before the decoder, so the guard does not depend on which decoder runs
    try:
        prompt = ("English speech about civil engineering, tender review, facade panels and container packing."
                  if language == "en" else build_prompt(load_lexicon() if use_lexicon else None))
    except OSError as exc:
        raise AsrUnavailable("缺少术语表 demo/asr_lexicon.txt") from exc
    audio = decode(data)
    model = _get_model()
    if not _run_lock.acquire(timeout=RUN_WAIT_SECONDS):
        raise AsrBusy("正在识别上一段，请稍候再试")
    try:
        started = time.perf_counter()
        try:
            text = run_model(model, audio, prompt, language="en") if language == "en" else run_model(model, audio, prompt)
        except Exception as exc:  # native runtime failure, e.g. the VAD's onnxruntime
            raise AsrUnavailable(f"本机识别运行失败：{type(exc).__name__}") from exc
        elapsed = time.perf_counter() - started
    finally:
        _run_lock.release()
    return {
        "text": text,
        "engine": "faster-whisper",
        "model": MODEL_NAME,
        "audio_seconds": round(len(audio) / SAMPLE_RATE, 2),
        "elapsed_seconds": round(elapsed, 2),
        "lexicon": use_lexicon and language == "zh",
        "language": language,
    }
