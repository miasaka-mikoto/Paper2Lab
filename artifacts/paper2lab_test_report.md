# Paper2Lab Acceptance Test Report

Status: **PASS**
Generated: `2026-10-04T15:50:53.643030+00:00`

| Check | Result |
|---|---|
| `/opt/codex/runtimes/codex-primary-runtime/dependencies/python/bin/python -m pytest -q` | PASS |
| `/opt/codex/runtimes/codex-primary-runtime/dependencies/python/bin/python -m paper2lab --self-test` | PASS |
| `/opt/codex/runtimes/codex-primary-runtime/dependencies/python/bin/python -m paper2lab --service-self-test` | PASS |
| `/opt/codex/runtimes/codex-primary-runtime/dependencies/python/bin/python scripts/run_demo.py --workspace artifacts/paper2lab_demo --fresh` | PASS |
| `/opt/codex/runtimes/codex-primary-runtime/dependencies/python/bin/python scripts/run_demo.py --workspace artifacts/paper2lab_demo_compat --fresh --compat` | PASS |
| `/opt/codex/runtimes/codex-primary-runtime/dependencies/python/bin/python -m py_compile /workspace/scratch/a9ac8a328983/paper2lab/__init__.py /workspace/scratch/a9ac8a328983/paper2lab/__main__.py /workspace/scratch/a9ac8a328983/paper2lab/_interop.py /workspace/scratch/a9ac8a328983/paper2lab/app.py /workspace/scratch/a9ac8a328983/paper2lab/blueprint.py /workspace/scratch/a9ac8a328983/paper2lab/claims.py /workspace/scratch/a9ac8a328983/paper2lab/compare.py /workspace/scratch/a9ac8a328983/paper2lab/extractor.py /workspace/scratch/a9ac8a328983/paper2lab/generator.py /workspace/scratch/a9ac8a328983/paper2lab/main.py /workspace/scratch/a9ac8a328983/paper2lab/models.py /workspace/scratch/a9ac8a328983/paper2lab/parser.py /workspace/scratch/a9ac8a328983/paper2lab/providers.py /workspace/scratch/a9ac8a328983/paper2lab/report.py /workspace/scratch/a9ac8a328983/paper2lab/reproducibility.py /workspace/scratch/a9ac8a328983/paper2lab/runner.py /workspace/scratch/a9ac8a328983/paper2lab/service.py /workspace/scratch/a9ac8a328983/paper2lab/storage.py /workspace/scratch/a9ac8a328983/paper2lab/ui.py` | PASS |

## Coverage
- PDF/Markdown/TXT/HTML import parser
- Section recognition and manual rename API
- RuleBasedExtractor and MockLLMProvider
- Claim → Blueprint → skeleton generation
- Mock execution, comparison, reproducibility checklist
- Async subprocess Run/Pause/Resume/Cancel/Retry lifecycle and audit persistence
- Editable metadata/section/Claim/Blueprint/formula records and result provenance graph
- Markdown + HTML reproduction report
- Save/close/reopen round trip
- Tkinter UI import-safe compilation

No external network service or paid LLM API is used.
