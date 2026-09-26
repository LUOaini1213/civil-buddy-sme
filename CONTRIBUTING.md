# Contributing to Civil Buddy

Thanks for interest in **Civil Buddy** (土木工作台 + packing engine).

## Ground rules

1. **Tools compute; models route.** Do not add paths where the LLM invents coordinates, counts, or weights.
2. Prefer **`agent_mode=steps`** for production behavior; treat `llm_toolcall` as experimental / shadow.
3. Every behavior change should be **observable** (trace step or artifact) and, when possible, **tested** (`scripts/smoke_agent_product.py` or a tiny eval).
4. No secrets, customer raw dumps, or API keys in the repo. Use `.env` locally (see `.env.example`).

## Dev setup

```bash
git clone https://github.com/LUOaini1213/civil-buddy-sme.git
cd civil-buddy-sme
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate   # Unix
python -m pip install -r requirements-dev.txt
```

## Checks before a PR

Use Python 3.11+ and Node.js 22. `npm run check` selects the repository `.venv`
when present; set `PYTHON` to an executable path to override it. All check names
and timeouts live in `scripts/check_project.py`, which can also run directly
with your selected Python interpreter.

```bash
npm run check
npm run check -- --list
npm run check -- --only chat-stream,runtime-threads,trace-artifacts
```

The gate clears model credentials, disables `.env` loading and Python assertion
optimization, runs each check with a timeout, and reports all failures at the end.
Its default job folder is `output/check-project/jobs`, so a configured working
job folder is not passed to the checks.
It covers the existing policy/skill gates plus the conversation transport,
per-task concurrency, knowledge-base writes, Office documents and trace exports.

For changes across the API, packing engine or Rust workbench:

```bash
cargo fetch --locked --manifest-path workbench/Cargo.toml  # first-time dependency cache
npm run check:full
python -m pytest demo/tests -q --basetemp=output/pytest-local
```

The Rust check uses `--locked --offline`; missing cached dependencies are a
failure, not a skipped test. The HTTP fixtures use an `output/` temporary root
because the product sandbox rejects business-file writes outside authorized roots.
Set `PYTHON_DOTENV_DISABLED=1` before running individual Python tests directly
if your local `.env` contains a live model configuration.

## One-shot demo

```bash
python scripts/demo_one_shot.py
# equivalent core:
python scripts/smoke_agent_product.py
```

Optional:

```bash
python scripts/demo_one_shot.py --closed-loop
python scripts/eval_workteams_cli.py --tiny-only
uvicorn gateway.app:app --reload --host 127.0.0.1 --port 8000
```

## Branch & PR

1. Fork / branch from `main`.
2. Keep PRs focused (one harness concern per PR when possible).
3. Fill the PR template: **what / why / how tested**.
4. Link related issues.

Suggested labels: `runtime`, `tools`, `eval`, `trace`, `docs`, `gateway`.

## Issue types

Use GitHub templates:

| Template | Use when |
|----------|----------|
| Bug report | Broken pipeline, illegal tools, crash |
| Feature request | New tool, gate, KPI |
| Harness design | Architecture / first-principles discussion |
| Phase1 / Phase2 | Legacy domain task splits (cartonize vs containerize) |

## Code map (short)

| Path | Role |
|------|------|
| `packing_assistant/harness.py` | Runtime facade |
| `packing_assistant/teams/` | Orchestrator + subagents |
| `packing_assistant/tools/` | Deterministic tools |
| `packing_assistant/tool_registry.py` | Whitelist |
| `docs/harness-design.md` | Design decisions (tool/HITL/eval) — interview sheet |
| `docs/architecture-as-harness.md` | Harness vocabulary map |
| `docs/ARCHITECTURE.md` | Domain architecture |
| `GOOD_FIRST_ISSUES.md` | Starter tasks for contributors |

## License / scope

MIT. See [LICENSE](LICENSE).  
Portfolio + research prototype. Not a warranty of production packing accuracy.  
By contributing you agree your patches may be redistributed with the project.
