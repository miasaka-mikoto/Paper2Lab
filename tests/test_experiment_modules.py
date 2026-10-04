"""Focused tests for Paper2Lab's standard-library experiment engine."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

from paper2lab.compare import compare_results
from paper2lab.generator import generate_experiment_skeleton
from paper2lab.reproducibility import assess_reproducibility
from paper2lab.runner import ExperimentRunner, MockExperimentRunner
from paper2lab.providers import DeferredProvider, FUTURE_PROVIDER_NAMES
from paper2lab.service import Paper2LabService
from paper2lab.models import RunStatus


def _wait_for(predicate, timeout: float = 4.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


def test_generate_and_execute_skeleton(tmp_path: Path) -> None:
    blueprint = {
        "blueprint_id": "bp_test",
        "name": "Offline Accuracy Check",
        "research_question": "Does the method improve accuracy?",
        "hypothesis": "Accuracy improves.",
        "dataset": "fictional-set",
        "model": "TODO: implement paper model",
        "metrics": {"accuracy": {"direction": "higher"}},
        "paper_reported_result": {"accuracy": 81.4},
    }
    generated = generate_experiment_skeleton(blueprint, tmp_path)
    assert (generated.project_dir / "config.yaml").is_file()
    assert (generated.project_dir / "run.py").is_file()
    assert (generated.project_dir / "evaluator.py").is_file()
    assert (generated.project_dir / "README.md").is_file()
    for name in ("experiments", "configs", "datasets", "models", "evaluators", "results", "reports", "tests"):
        assert (generated.project_dir / name).is_dir()

    run = ExperimentRunner().run(generated.project_dir, seed=7)
    assert run.status == "completed"
    assert run.returncode == 0
    assert run.result["mode"] == "mock"
    assert run.result["metrics"]["accuracy"] == 80.9
    assert (generated.project_dir / "runs" / f"{run.run_id}.json").is_file()


def test_compare_and_reproducibility_are_explicit() -> None:
    rows = compare_results(
        {"accuracy": 81.4, "loss": 0.4},
        {"accuracy": 80.9, "loss": 0.7},
        directions={"loss": "lower"},
    )
    assert rows[0].absolute_difference == -0.5
    assert rows[0].relative_difference is not None
    assert rows[1].status == "LOCAL_WORSE"

    assessment = assess_reproducibility(
        {"dataset": "sample", "model": "MockNet", "metrics": ["accuracy"], "seed": 42},
        evidence={"code_available": {"state": "unclear", "note": "No repository supplied"}},
    )
    assert assessment.total == 8
    assert assessment.available_count == 4
    assert assessment.score == 50.0
    assert any(row.state == "unclear" for row in assessment.criteria)


def test_mock_runner_never_calls_network() -> None:
    result = MockExperimentRunner().run(
        {"paper_reported_result": {"accuracy": 81.4}, "local_result": {}}, seed=7
    )
    assert result.status == "completed"
    assert result.result["metrics"]["accuracy"] == 80.9
    assert "paid API" in result.result["notes"][0]
    assert "openai" in FUTURE_PROVIDER_NAMES
    try:
        DeferredProvider("openai").generate("offline")
    except NotImplementedError:
        pass
    else:
        raise AssertionError("deferred provider must not silently call a network API")


def test_process_runner_timeout_cancel_and_retry_are_auditable(tmp_path: Path) -> None:
    runner = ExperimentRunner()
    # A timeout uses the same terminate path as cancel, but must retain the
    # more specific timeout terminal state.
    timed = runner.start(
        tmp_path,
        command=[sys.executable, "-c", "import time; time.sleep(0.5)"],
        timeout=0.05,
    )
    timed_result = runner.wait(timed, timeout=4)
    assert timed_result is not None
    assert timed_result.status == "timeout"
    assert "exceeded timeout" in timed_result.error

    active = runner.start(
        tmp_path,
        command=[sys.executable, "-c", "import time; time.sleep(2)"],
    )
    assert runner.status(active) in {"queued", "running"}
    assert _wait_for(lambda: runner.status(active) == "running")
    # Retrying an active process is rejected to avoid concurrent output-file
    # corruption; after cancellation a retry receives a new run ID.
    assert runner.retry(active) is None
    assert runner.cancel(active)
    cancelled = runner.wait(active, timeout=4)
    assert cancelled is not None
    assert cancelled.status == "cancelled"
    retried = runner.retry(active)
    assert retried and retried != active
    retried_result = runner.wait(retried, timeout=4)
    assert retried_result is not None
    assert retried_result.status == "completed"
    assert (tmp_path / "runs" / f"{active}.json").is_file()
    assert (tmp_path / "runs" / f"{retried}.json").is_file()


def test_service_process_runner_lifecycle_persists_and_retries(tmp_path: Path) -> None:
    sample = Path(__file__).resolve().parents[1] / "sample" / "sample_paper.md"
    service = Paper2LabService(tmp_path / "workspace")
    paper = service.import_paper(sample)
    service.parse_structure(paper.paper_id)
    claims = service.extract_claims(paper.paper_id)
    blueprint = service.create_blueprint(paper.paper_id, claims[0].claim_id)

    run = service.start_experiment(
        paper.paper_id,
        blueprint.blueprint_id,
        seed=7,
        experiment_dir=tmp_path / "generated_experiment",
    )
    assert run.status in {RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.SUCCEEDED}
    completed = service.wait_experiment(run.run_id, timeout=8)
    assert completed.status.value == "succeeded"
    assert completed.stdout
    assert completed.environment
    assert completed.result.get("metrics") or any(
        isinstance(value, (int, float)) for value in completed.result.values()
    )
    assert service.get_blueprint(paper.paper_id, blueprint.blueprint_id).local_result

    retried = service.retry_run(run.run_id)
    retried_done = service.wait_experiment(retried.run_id, timeout=8)
    assert retried.run_id != run.run_id
    assert retried_done.status.value == "succeeded"
    saved = json.loads((tmp_path / "workspace" / "paper2lab.project.json").read_text(encoding="utf-8"))
    assert len(saved["papers"][0]["runs"]) == 2
