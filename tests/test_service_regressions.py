"""Regression coverage for restart and multi-blueprint report provenance."""

from __future__ import annotations

from pathlib import Path
import sys
import time

from paper2lab import Paper2LabService
from paper2lab.runner import ExperimentRunner


def test_default_workspace_rehydrates_run_index_after_restart(tmp_path: Path, monkeypatch) -> None:
    """A newly constructed service must expose persisted runs to the UI."""

    workspace = tmp_path / "workspace"
    monkeypatch.setenv("PAPER2LAB_WORKSPACE", str(workspace))

    first = Paper2LabService()
    paper = first.create_sample_paper()
    claim = first.extract_claims(paper.paper_id)[0]
    blueprint = first.create_blueprint(paper.paper_id, claim.claim_id)
    run = first.run_experiment(paper.paper_id, blueprint.blueprint_id, seed=17)

    reopened = Paper2LabService()
    assert any(item.run_id == run.run_id for item in reopened.list_runs())
    # The Tk workbench reads this index when populating its bottom Runs pane.
    assert run.run_id in reopened.runs


def test_report_uses_run_for_requested_blueprint(tmp_path: Path) -> None:
    """A report for Blueprint A must not attach Blueprint B's run provenance."""

    service = Paper2LabService(tmp_path / "workspace")
    paper = service.create_sample_paper()
    claims = service.extract_claims(paper.paper_id)
    first_blueprint = service.create_blueprint(paper.paper_id, claims[0].claim_id)
    first_run = service.run_experiment(paper.paper_id, first_blueprint.blueprint_id, seed=11)

    second_blueprint = service.create_blueprint(paper.paper_id, claims[1].claim_id)
    second_run = service.run_experiment(paper.paper_id, second_blueprint.blueprint_id, seed=22)
    assert first_run.run_id != second_run.run_id

    report = service.generate_report(
        paper.paper_id,
        first_blueprint.blueprint_id,
        output_dir=tmp_path / "report",
    )
    markdown = Path(report["markdown"]).read_text(encoding="utf-8")
    assert f"- Seed: `{first_run.seed}`" in markdown
    assert f"- Seed: `{second_run.seed}`" not in markdown


def test_cancel_queued_process_before_worker_registers_child(tmp_path: Path, monkeypatch) -> None:
    """Cancel intent must survive the tiny start/worker hand-off window."""

    runner = ExperimentRunner()
    original_execute = runner._execute

    def delayed_execute(request):
        # Make the queued state deterministic: ``start`` has registered the
        # request, but the worker has not yet created its child process.
        time.sleep(0.05)
        return original_execute(request)

    monkeypatch.setattr(runner, "_execute", delayed_execute)
    run_id = runner.start(
        tmp_path,
        command=[sys.executable, "-c", "import time; time.sleep(1)"],
    )
    assert runner.cancel(run_id)
    # The cancellation intent is visible immediately, even before the
    # delayed worker has created a child process or persisted its record.
    assert runner.status(run_id) == "cancelled"
    result = runner.wait(run_id, timeout=3)
    assert result is not None
    assert result.status == "cancelled"


def test_service_cancel_does_not_get_overwritten_by_late_worker(tmp_path: Path) -> None:
    """The persisted service record remains cancelled after worker cleanup."""

    service = Paper2LabService(tmp_path / "workspace")
    paper = service.create_sample_paper()
    claim = service.extract_claims(paper.paper_id)[0]
    blueprint = service.create_blueprint(paper.paper_id, claim.claim_id)
    run = service.start_experiment(
        paper.paper_id,
        blueprint.blueprint_id,
        experiment_dir=tmp_path / "experiment",
        command=[sys.executable, "-c", "import time; time.sleep(1)"],
    )

    cancelled = service.cancel_run(run.run_id)
    assert cancelled.status.value == "cancelled"
    finished = service.wait_experiment(run.run_id, timeout=3)
    assert finished.status.value == "cancelled"


def test_service_immediate_cancel_stays_cancelled(tmp_path: Path) -> None:
    """The service facade must preserve a cancel clicked right after Start."""

    service = Paper2LabService(tmp_path / "workspace")
    paper = service.create_sample_paper()
    claim = service.extract_claims(paper.paper_id)[0]
    blueprint = service.create_blueprint(paper.paper_id, claim.claim_id)
    run = service.start_experiment(
        paper.paper_id,
        blueprint.blueprint_id,
        experiment_dir=tmp_path / "experiment",
        command=[sys.executable, "-c", "import time; time.sleep(0.6)"],
    )
    service.cancel_run(run.run_id)
    finished = service.wait_experiment(run.run_id, timeout=3)
    assert finished.status.value == "cancelled"
