"""Fixed ASR child process. Audio arrives on stdin and is never written to disk."""
from __future__ import annotations

import json
import sys
from . import asr


def main() -> None:
    command = sys.argv[1] if len(sys.argv) == 2 else ""
    try:
        reason = asr.engine_error()
        if reason:
            raise asr.AsrUnavailable(reason)
        from faster_whisper import WhisperModel

        if command == "prepare":
            # Only this explicit command may download. Exit releases model memory.
            WhisperModel(asr.MODEL_NAME, device=asr.DEVICE, compute_type=asr.COMPUTE_TYPE)
            result = {"ok": True, "state": "ready"}
        elif command == "transcribe":
            audio = sys.stdin.buffer.read(asr.MAX_AUDIO_BYTES + 1)
            asr.check_size(len(audio))
            # No implicit prepare()/download path can run during a transcription.
            asr._model = WhisperModel(asr.MODEL_NAME, device=asr.DEVICE,
                                     compute_type=asr.COMPUTE_TYPE, local_files_only=True)
            result = {"ok": True, **asr.transcribe(audio)}
        else:
            raise ValueError("unknown ASR worker command")
    except asr.AsrTooLarge:
        result = {"ok": False, "status": 413, "detail": "录音不能超过 8 MB"}
    except asr.AsrInputError as exc:
        result = {"ok": False, "status": 400, "detail": str(exc)}
    except Exception as exc:
        result = {"ok": False, "status": 503, "detail": "本机识别子进程不可用：" + type(exc).__name__}
    print(json.dumps(result, ensure_ascii=True), flush=True)


if __name__ == "__main__":
    main()
