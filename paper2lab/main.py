"""Command-line entry point for the Paper2Lab desktop app."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

try:  # ``python -m paper2lab.main`` is preferred; direct script use is handy on Windows.
    from .ui import CompatBackend, launch
except ImportError:  # pragma: no cover - exercised only when launched as a file.
    # When a Windows shortcut points at ``paper2lab/main.py`` Python puts the
    # package directory (rather than its parent) on sys.path.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from paper2lab.ui import CompatBackend, launch  # type: ignore


def self_test() -> int:
    """Exercise the complete offline sample workflow without opening a window."""

    # Keep the smoke test hermetic: a double-clicked executable should not
    # leave generated test projects beside the installation or source tree.
    with tempfile.TemporaryDirectory(prefix="paper2lab-ui-self-test-") as tmp:
        root = Path(tmp)
        backend = CompatBackend()
        paper = backend.create_sample_paper()
        assert paper.sections, "sample sections were not parsed"
        claims = backend.extract_claims(paper.id)
        assert claims, "sample claims were not extracted"
        blueprint = backend.create_blueprint(paper.id, claims[0]["id"])
        skeleton = backend.generate_skeleton(paper.id, blueprint["id"], output_dir=str(root / blueprint["id"]))
        run = backend.run_experiment(paper.id, blueprint["id"])
        assert run.status == "Completed"
        diff = backend.compare_results(paper.id, blueprint["id"])
        report = backend.generate_report(paper.id, blueprint["id"], output_dir=str(root / "reports"))
        project = root / "roundtrip.paper2lab.json"
        backend.save_project(project)
        reopened = CompatBackend()
        reopened.load_project(project)
        assert reopened.get_paper(paper.id) is not None
        summary = {"status": "PASS", "paper": paper.id, "sections": len(paper.sections), "claims": len(claims), "skeleton": skeleton, "run": run.id, "difference": diff, "report": report}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def service_self_test() -> int:
    """Exercise the canonical model-backed service without opening Tk."""
    from paper2lab.service import Paper2LabService

    with tempfile.TemporaryDirectory(prefix="paper2lab-service-self-test-") as tmp:
        service = Paper2LabService(Path(tmp) / "workspace")
        paper = service.create_sample_paper()
        claims = service.extract_claims(paper.paper_id)
        blueprint = service.create_blueprint(paper.paper_id, claims[0].claim_id)
        service.generate_skeleton(paper.paper_id, blueprint.blueprint_id, Path(tmp) / "experiment")
        run = service.run_experiment(paper.paper_id, blueprint.blueprint_id, seed=7)
        comparisons = service.compare_results(paper.paper_id, blueprint.blueprint_id)
        reports = service.generate_report(paper.paper_id, blueprint.blueprint_id, Path(tmp) / "reports")
        if not claims or not run.result or not comparisons or not Path(reports["markdown"]).is_file():
            raise RuntimeError("canonical service self-test did not complete")
        print(json.dumps({
            "status": "PASS",
            "backend": "Paper2LabService",
            "sections": len(paper.sections),
            "claims": len(claims),
            "metrics": run.result.get("metrics", {}),
            "report": reports,
        }, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Paper2Lab — 论文复现实验工厂")
    parser.add_argument("--self-test", action="store_true", help="run the offline sample workflow without starting Tk")
    parser.add_argument("--service-self-test", action="store_true", help="run the canonical model-backed service workflow")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    if args.service_self_test:
        return service_self_test()
    launch()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
