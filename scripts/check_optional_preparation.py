"""Fixed, offline native smoke checks for an optional worker environment.

Run with the candidate interpreter: python -I -B this_file --profile <profile>.
Only tiny in-memory fixtures are used. This does not load speech model weights,
download assets, contact a provider, open a microphone, or inspect product state.
The installer bounds the subprocess; the CP-SAT check also bounds its own solve.
"""

from __future__ import annotations

import importlib
import io
import json
import sys
import wave
from pathlib import Path
from typing import Callable


PROFILES = frozenset({"core", "speech", "planning", "speech-planning"})
_EXCEPTIONS = frozenset({
    "ModuleNotFoundError", "ImportError", "OSError", "RuntimeError",
    "ValueError", "TypeError", "AttributeError", "AssertionError",
    "EntitiesForbidden", "DTDForbidden", "ExternalReferenceForbidden",
})


def _optional_import(name: str):
    module = importlib.import_module(name)
    origin = getattr(module, "__file__", None)
    assert origin and Path(origin).resolve().is_relative_to(Path(sys.prefix).resolve())
    return module


def _failed(check: str, module: str, exc: Exception) -> dict:
    # Exception messages can disclose DLL paths, environment settings or user
    # directories. Even an unusual exception class name is not echoed.
    name = type(exc).__name__
    kind = "module_missing" if isinstance(exc, ModuleNotFoundError) else (
        "native_load_failed" if isinstance(exc, (ImportError, OSError))
        else "operation_failed"
    )
    return {"check": check, "module": module, "passed": False,
            "error": kind, "exception": name if name in _EXCEPTIONS else "Exception"}


def _ortools() -> None:
    cp = _optional_import("ortools.sat.python.cp_model")
    model = cp.CpModel()
    x, y = model.NewIntVar(0, 10, "x"), model.NewIntVar(0, 10, "y")
    model.Add(x + y == 7)
    model.Add(x >= 2)
    model.Minimize(2 * x + y)
    solver = cp.CpSolver()
    solver.parameters.max_time_in_seconds = 5.0
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = 0
    assert solver.Solve(model) == cp.OPTIMAL
    assert (solver.Value(x), solver.Value(y), solver.ObjectiveValue()) == (2, 5, 9)


def _networkx() -> None:
    nx = _optional_import("networkx")
    graph = nx.DiGraph()
    graph.add_weighted_edges_from([("start", "a", 2), ("a", "end", 4),
                                   ("start", "end", 9)])
    assert nx.is_directed_acyclic_graph(graph)
    assert nx.shortest_path(graph, "start", "end", weight="weight") == ["start", "a", "end"]
    assert nx.shortest_path_length(graph, "start", "end", weight="weight") == 6


def _defusedxml() -> None:
    xml = _optional_import("defusedxml.ElementTree")
    common = _optional_import("defusedxml.common")
    root = xml.fromstring('<project><task duration="3">fixed</task></project>')
    assert root.tag == "project" and root.find("task").get("duration") == "3"
    try:
        xml.fromstring('<!DOCTYPE project [<!ENTITY forbidden "fixed">]><project>&forbidden;</project>')
    except common.EntitiesForbidden:
        return
    raise AssertionError("entity rejection failed")


def _faster_whisper() -> None:
    module = _optional_import("faster_whisper")
    assert callable(module.WhisperModel)  # Deliberately do not instantiate it.


def _ctranslate2() -> None:
    module = _optional_import("ctranslate2")
    supported = module.get_supported_compute_types("cpu")
    assert isinstance(supported, (set, frozenset, list, tuple)) and {"float32", "int8"}.issubset(supported)


def _av() -> None:
    av = _optional_import("av")
    audio = io.BytesIO()
    with wave.open(audio, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(16000)
        writer.writeframes(b"\x01\x00" * 3200)
    audio.seek(0)
    samples = 0
    with av.open(audio, mode="r", format="wav") as container:
        assert len(container.streams.audio) == 1
        for frame in container.decode(audio=0):
            assert frame.sample_rate == 16000 and frame.samples > 0
            assert len(frame.planes) == 1 and bytes(frame.planes[0])[:2] == b"\x01\x00"
            samples += frame.samples
    assert samples == 3200


def _onnxruntime() -> None:
    module = _optional_import("onnxruntime")
    assert "CPUExecutionProvider" in module.get_available_providers()


def _tokenizers() -> None:
    module = _optional_import("tokenizers")
    models = _optional_import("tokenizers.models")
    split = _optional_import("tokenizers.pre_tokenizers")
    tokenizer = module.Tokenizer(models.WordLevel({"[UNK]": 0, "panel": 1, "review": 2}, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = split.Whitespace()
    encoded = tokenizer.encode("panel review")
    assert encoded.ids == [1, 2] and encoded.tokens == ["panel", "review"]
    assert tokenizer.decode(encoded.ids) == "panel review"
    assert tokenizer.encode("other").ids == [0]


_PLANNING: tuple[tuple[str, str, Callable[[], None]], ...] = (
    ("known_optimal_schedule", "ortools", _ortools),
    ("directed_shortest_path", "networkx", _networkx),
    ("safe_xml_and_entity_rejection", "defusedxml", _defusedxml),
)
_SPEECH: tuple[tuple[str, str, Callable[[], None]], ...] = (
    ("engine_import_only", "faster_whisper", _faster_whisper),
    ("cpu_compute_types", "ctranslate2", _ctranslate2),
    ("in_memory_pcm_decode", "av", _av),
    ("cpu_provider_available", "onnxruntime", _onnxruntime),
    ("fixed_wordlevel_encode_decode", "tokenizers", _tokenizers),
)


def check_profile(profile: str) -> dict:
    if profile not in PROFILES:
        return {"passed": False, "profile": None, "checks": [
            {"check": "arguments", "module": "stdlib", "passed": False, "error": "invalid_arguments"}
        ]}
    selected = (() if profile == "core" else
                _PLANNING if profile == "planning" else
                _SPEECH if profile == "speech" else _PLANNING + _SPEECH)
    checks = []
    for name, module, operation in selected:
        try:
            operation()
        except Exception as exc:
            checks.append(_failed(name, module, exc))
        else:
            checks.append({"check": name, "module": module, "passed": True})
    if not selected:
        checks.append({"check": "no_optional_packages_requested", "module": "stdlib", "passed": True})
    return {"passed": all(item["passed"] for item in checks), "profile": profile, "checks": checks}


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    profile = args[1] if len(args) == 2 and args[0] == "--profile" else ""
    result = check_profile(profile)
    # The fixed schema has at most eight checks; no diagnostic stdout or raw
    # exception text is mixed into this machine-readable result.
    print(json.dumps(result, ensure_ascii=True, separators=(",", ":")))
    return 0 if result["passed"] else 2 if result["profile"] is None else 1


if __name__ == "__main__":
    raise SystemExit(main())
