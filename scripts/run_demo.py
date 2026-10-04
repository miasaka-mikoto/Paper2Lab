#!/usr/bin/env python3
"""Run Paper2Lab's complete offline sample workflow.

The default path uses :class:`paper2lab.service.Paper2LabService`, the
model-backed standard-library service.  ``--compat`` can exercise the Tk
fallback backend as a second smoke path.  Both paths import the fictional
sample paper, parse its structure, extract claims, create a blueprint, emit an
honest experiment scaffold, run the mock experiment, compare values, write a
reproduction report, and finally round-trip the project through JSON.

No network request or paid model API is made by this script.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from paper2lab.service import Paper2LabService  # noqa: E402
from paper2lab.ui import CompatBackend  # noqa: E402


def _jsonable(value: Any) -> Any:
    """Convert the small backend records to JSON without extra dependencies."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    try:
        return _jsonable(asdict(value))
    except (TypeError, ValueError):
        pass
    attrs = getattr(value, "__dict__", None)
    if isinstance(attrs, dict):
        return _jsonable(attrs)
    return str(value)


def _prepare_workspace(workspace: str | Path | None, fresh: bool) -> Path:
    target = Path(workspace) if workspace else ROOT / "artifacts" / "paper2lab_demo"
    target = target.expanduser().resolve()
    if fresh and target.exists():
        # This is deliberately scoped to the demo directory selected by the
        # caller; normal invocations never remove existing user files.
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    return target


def _write_summary(target: Path, summary: dict[str, Any]) -> dict[str, Any]:
    (target / "demo_summary.json").write_text(
        json.dumps(_jsonable(summary), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def _run_service(target: Path) -> dict[str, Any]:
    """Run the canonical model-backed service workflow."""

    sample = ROOT / "sample" / "sample_paper.md"
    service = Paper2LabService(target / "service_workspace")
    paper = service.import_paper(sample)
    sections = service.parse_structure(paper.paper_id)
    claims = service.extract_claims(paper.paper_id)
    blueprint = service.create_blueprint(paper.paper_id, claims[0].claim_id if claims else None)

    skeleton_dir = target / "sample_experiment"
    generated = service.generate_skeleton(paper.paper_id, blueprint.blueprint_id, skeleton_dir)
    skeleton_output = skeleton_dir / "results" / "results.json"
    skeleton_proc = subprocess.run(
        [sys.executable, str(skeleton_dir / "run.py"), "--seed", "7", "--output", str(skeleton_output)],
        cwd=skeleton_dir,
        text=True,
        capture_output=True,
        check=False,
    )
    if skeleton_proc.returncode != 0:
        raise RuntimeError(f"generated skeleton failed: {skeleton_proc.stderr}")
    run = service.run_experiment(paper.paper_id, blueprint.blueprint_id, seed=7)
    difference_rows = service.compare_results(paper.paper_id, blueprint.blueprint_id)
    differences = {row.metric: row.to_dict() for row in difference_rows}
    report_files = service.generate_report(
        paper.paper_id,
        blueprint.blueprint_id,
        output_dir=str(target / "reports"),
    )

    project_file = target / "sample_project.paper2lab.json"
    service.save_project(project_file)

    # A fresh backend models closing and reopening the desktop application.
    reopened = Paper2LabService(target / "reopened_workspace")
    reopened.load_project(project_file)
    restored = reopened.get_paper(paper.paper_id)
    if restored is None:
        raise RuntimeError("round-trip restore did not recover the imported paper")

    summary: dict[str, Any] = {
        "status": "PASS",
        "backend": "Paper2LabService",
        "offline": True,
        "sample_paper": str(sample),
        "paper_id": paper.paper_id,
        "title": paper.title,
        "sections": len(sections),
        "claims": len(claims),
        "blueprint_id": blueprint.blueprint_id,
        "skeleton_dir": str(generated),
        "skeleton_run_status": "PASS",
        "skeleton_result": json.loads(skeleton_output.read_text(encoding="utf-8")),
        "run_id": run.run_id,
        # Keep the human-facing demo summary compatible with the desktop
        # fallback (which historically displayed ``Completed``), while the
        # persisted domain RunRecord retains the precise ``succeeded`` enum.
        "run_status": "Completed" if getattr(run.status, "value", run.status) in {"succeeded", "completed"} else getattr(run.status, "value", run.status),
        "result": run.result,
        "differences": differences,
        "report": report_files["markdown"],
        "report_html": report_files["html"],
        "project_file": str(project_file),
        "restored_title": restored.metadata.title,
        "restored_blueprints": len(restored.blueprints),
    }
    return _write_summary(target, summary)


def _run_compat(target: Path) -> dict[str, Any]:
    """Run the UI compatibility backend as a secondary smoke path."""

    sample = ROOT / "sample" / "sample_paper.md"
    backend = CompatBackend()
    paper = backend.import_paper(sample)
    sections = backend.parse_structure(paper.id)
    claims = backend.extract_claims(paper.id)
    blueprint = backend.create_blueprint(paper.id, claims[0]["id"] if claims else None)

    skeleton_dir = target / "sample_experiment"
    generated = backend.generate_skeleton(paper.id, blueprint["id"], str(skeleton_dir))
    run = backend.run_experiment(paper.id, blueprint["id"], seed=7)
    differences = backend.compare_results(paper.id, blueprint["id"])
    report = backend.generate_report(
        paper.id,
        blueprint["id"],
        output_dir=str(target / "reports"),
    )

    project_file = target / "sample_project.paper2lab.json"
    backend.save_project(project_file)

    reopened = CompatBackend()
    reopened.load_project(project_file)
    restored = reopened.get_paper(paper.id)
    if restored is None:
        raise RuntimeError("round-trip restore did not recover the imported paper")

    summary: dict[str, Any] = {
        "status": "PASS",
        "backend": "CompatBackend",
        "offline": True,
        "sample_paper": str(sample),
        "paper_id": paper.id,
        "title": paper.title,
        "sections": len(sections),
        "claims": len(claims),
        "blueprint_id": blueprint["id"],
        "skeleton_dir": generated,
        "run_id": run.id,
        "run_status": run.status,
        "result": run.result,
        "differences": differences,
        "report": report,
        "project_file": str(project_file),
        "restored_title": restored.title,
        "restored_blueprints": len(restored.blueprints),
    }
    return _write_summary(target, summary)


def run_demo(
    workspace: str | Path | None = None,
    *,
    fresh: bool = False,
    compat: bool = False,
) -> dict[str, Any]:
    """Execute the sample workflow and return a machine-readable summary.

    The canonical service is used by default.  ``compat=True`` is useful for
    checking the UI fallback independently.
    """

    target = _prepare_workspace(workspace, fresh)
    return _run_compat(target) if compat else _run_service(target)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Paper2Lab offline sample workflow")
    parser.add_argument(
        "--workspace",
        type=Path,
        default=None,
        help="output directory (default: artifacts/paper2lab_demo)",
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="clear only the selected demo output directory before running",
    )
    parser.add_argument(
        "--compat",
        action="store_true",
        help="use the Tk compatibility backend instead of Paper2LabService",
    )
    args = parser.parse_args(argv)
    summary = run_demo(args.workspace, fresh=args.fresh, compat=args.compat)
    print(json.dumps(_jsonable(summary), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by subprocess tests
    raise SystemExit(main())
