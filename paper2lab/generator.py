"""Generate honest, runnable experiment skeletons from an experiment blueprint.

The generator deliberately emits a *mock* experiment.  It never invents an
implementation for a paper's model: unresolved pieces are recorded as TODOs in
the generated README and config.  The generated project only uses Python's
standard library, so it is useful on a clean Windows machine as a starting
point for a real reproduction.
"""

from __future__ import annotations

import json
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from ._interop import as_text, get_field, json_safe, safe_filename, to_plain


@dataclass
class GeneratedExperiment:
    """Result of :class:`ExperimentSkeletonGenerator.generate`."""

    project_dir: Path
    files: list[Path]
    blueprint_id: str = ""
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_dir": str(self.project_dir),
            "files": [str(path) for path in self.files],
            "blueprint_id": self.blueprint_id,
            "warnings": list(self.warnings),
        }


def _mapping(value: Any) -> dict[str, Any]:
    plain = to_plain(value)
    return dict(plain) if isinstance(plain, Mapping) else {}


def _first(value: Any, *names: str, default: Any = None) -> Any:
    return get_field(value, *names, default=default)


def _metric_mapping(value: Any) -> dict[str, Any]:
    """Normalize metric fields from common blueprint shapes."""

    if value is None:
        return {}
    plain = to_plain(value)
    if isinstance(plain, Mapping):
        # A single metric record is also accepted: {name, paper_value, ...}.
        if "name" in plain and any(k in plain for k in ("paper_value", "paper", "local_value", "local")):
            return {as_text(plain.get("name"), "metric"): dict(plain)}
        return {str(k): v for k, v in plain.items()}
    if isinstance(plain, list):
        output: dict[str, Any] = {}
        for item in plain:
            # Blueprint metrics are commonly a list of plain names (the
            # canonical dataclass shape), while imported JSON may contain
            # structured records.  Preserve names in both cases instead of
            # collapsing every string into ``metric_1``.
            if isinstance(item, Mapping):
                row = _mapping(item)
                name = _first(row, "name", "metric", "id", default="metric_" + str(len(output) + 1))
                output[as_text(name)] = row
            else:
                name = as_text(item, "metric_" + str(len(output) + 1)).strip()
                if name:
                    output[name] = item
        return output
    return {}


def _extract_paper_metrics(blueprint: Any) -> dict[str, Any]:
    candidates = (
        "paper_reported_result",
        "paper_result",
        "paper_results",
        "expected_result",
        "expected_results",
        "metrics",
    )
    for name in candidates:
        # ``metrics`` is a declaration (often ``["accuracy", "recall"]``),
        # not necessarily a set of paper-reported values.  It is handled
        # separately below so the generated config does not mislabel a metric
        # definition such as ``{"direction": "higher"}`` as a paper result.
        if name == "metrics":
            continue
        value = _first(blueprint, name, default=None)
        if value is not None:
            metrics = _metric_mapping(value)
            if metrics and any(_has_reported_value(record) for record in metrics.values()):
                # If ``metrics`` is a list of definitions rather than result
                # values, only retain values explicitly marked as paper/local.
                return metrics
    return {}


def _has_reported_value(record: Any) -> bool:
    """Whether a metric record carries an actual reported/local value."""

    if isinstance(record, (int, float)) and not isinstance(record, bool):
        return True
    if isinstance(record, Mapping):
        return any(
            key in record
            for key in (
                "paper",
                "paper_value",
                "reported",
                "reported_value",
                "local",
                "local_value",
                "result",
                "value",
                "expected",
            )
        )
    return False


def _normalise_metric_value(record: Any, key: str) -> Any:
    if isinstance(record, Mapping):
        aliases = {
            "paper": ("paper", "paper_value", "reported", "reported_value", "expected", "value"),
            "local": ("local", "local_value", "result", "value"),
        }
        for alias in aliases.get(key, (key,)):
            if alias in record:
                return record[alias]
    return record if key == "paper" else None


def _mock_metric_config(blueprint: Any) -> dict[str, dict[str, Any]]:
    paper_metrics = _extract_paper_metrics(blueprint)
    local_overrides = _metric_mapping(
        _first(blueprint, "local_result", "local_results", default=None)
    )
    output: dict[str, dict[str, Any]] = {}
    for name, record in paper_metrics.items():
        paper = _normalise_metric_value(record, "paper")
        local = _normalise_metric_value(record, "local")
        override = local_overrides.get(name)
        if override is not None:
            local = _normalise_metric_value(override, "local")
            if local is None and not isinstance(override, Mapping):
                local = override
        output[str(name)] = {"paper": json_safe(paper), "local": json_safe(local)}
    # A blueprint may only provide local metrics.  Preserve those as useful
    # output rather than silently dropping them.
    for name, record in local_overrides.items():
        output.setdefault(str(name), {"paper": None, "local": json_safe(_normalise_metric_value(record, "local"))})
    # Preserve metric names even when the paper has not reported numeric
    # values.  The generated runner will produce an explicitly synthetic value
    # for these names, allowing the workflow to be exercised without implying
    # that a paper result was available.
    declared = _first(blueprint, "metrics", default=[])
    if isinstance(declared, Mapping):
        declared_names = declared.keys()
    elif isinstance(declared, (list, tuple, set, frozenset)):
        declared_names = [
            _first(item, "name", "metric", "id", default=item) if isinstance(item, Mapping) else item
            for item in declared
        ]
    else:
        declared_names = []
    for name in declared_names:
        if name is not None:
            output.setdefault(str(name), {"paper": None, "local": None})
    return output


def _generated_run_py() -> str:
    return textwrap.dedent(
        '''\
        """Run the generated Paper2Lab mock experiment.

        This file is intentionally a deterministic placeholder.  Replace the
        ``run_mock`` function with the paper's actual implementation once the
        TODOs in README.md have been resolved.
        """
        from __future__ import annotations

        import argparse
        import json
        import os
        import random
        import sys
        import time
        from pathlib import Path


        def load_config(path: Path):
            # config.yaml is JSON-compatible YAML, which keeps this skeleton
            # dependency-free while remaining easy to edit with a YAML tool.
            return json.loads(path.read_text(encoding="utf-8"))


        def _mock_value(name, record, rng):
            if isinstance(record, dict) and record.get("local") is not None:
                return record["local"]
            paper = record.get("paper") if isinstance(record, dict) else record
            if isinstance(paper, (int, float)) and not isinstance(paper, bool):
                # A small deterministic discrepancy makes the comparison view
                # useful without claiming that a model was reproduced.
                if str(name).lower() in {"accuracy", "f1", "f1_score", "auc", "exact_match"}:
                    delta = -0.5 if abs(float(paper)) > 1 else -0.005
                else:
                    delta = -abs(paper) * 0.005
                return round(paper + delta, 6)
            return round(rng.random(), 6)


        def run_mock(config, seed):
            rng = random.Random(seed)
            metrics = config.get("mock", {}).get("metrics", {})
            result = {
                "status": "completed",
                "mode": "mock",
                "seed": seed,
                "metrics": {name: _mock_value(name, record, rng) for name, record in metrics.items()},
                "notes": ["Mock result only; paper implementation was not assumed."],
            }
            return result


        def main(argv=None):
            parser = argparse.ArgumentParser(description="Run a Paper2Lab mock experiment")
            parser.add_argument("--config", default="config.yaml")
            parser.add_argument("--output", default="result.json")
            parser.add_argument("--seed", type=int, default=None)
            args = parser.parse_args(argv)
            config_path = Path(args.config)
            output_path = Path(args.output)
            config = load_config(config_path)
            seed = args.seed if args.seed is not None else int(config.get("seed", 42))
            started = time.time()
            result = run_mock(config, seed)
            result["runtime_seconds"] = round(time.time() - started, 6)
            result["config"] = str(config_path)
            result["environment"] = {"python": sys.version.split()[0], "platform": sys.platform}
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(result, ensure_ascii=False))
            return 0


        if __name__ == "__main__":
            raise SystemExit(main())
        ''')


def _generated_evaluator_py() -> str:
    return textwrap.dedent(
        '''\
        """Minimal evaluator hook for a generated experiment."""
        from __future__ import annotations

        import json
        from pathlib import Path


        def load_result(path="result.json"):
            return json.loads(Path(path).read_text(encoding="utf-8"))


        def evaluate(result):
            """Return metric values; replace with paper-specific evaluation."""
            return dict(result.get("metrics", {}))


        if __name__ == "__main__":
            print(json.dumps(evaluate(load_result()), ensure_ascii=False, indent=2))
        ''')


class ExperimentSkeletonGenerator:
    """Create a new, independent experiment directory from a blueprint."""

    REQUIRED_DIRS = ("experiments", "configs", "datasets", "models", "evaluators", "results", "reports", "tests")

    def __init__(self, *, app_name: str = "Paper2Lab", schema_version: str = "paper2lab.experiment.v1") -> None:
        self.app_name = app_name
        self.schema_version = schema_version

    def generate(
        self,
        blueprint: Any,
        destination: str | Path,
        *,
        experiment_name: str | None = None,
        overwrite: bool = False,
    ) -> GeneratedExperiment:
        """Generate a project under ``destination``.

        ``destination`` is treated as a parent directory.  The generated
        project gets a safe name derived from the blueprint title or ID.
        """

        raw = _mapping(blueprint)
        blueprint_id = as_text(_first(blueprint, "id", "blueprint_id", "experiment_id", default=""))
        title = experiment_name or as_text(
            _first(blueprint, "name", "title", "research_question", default="experiment"), "experiment"
        )
        project_dir = Path(destination).expanduser().resolve() / safe_filename(title)
        if project_dir.exists() and any(project_dir.iterdir()) and not overwrite:
            raise FileExistsError(f"Experiment directory already exists: {project_dir}")
        project_dir.mkdir(parents=True, exist_ok=True)
        for directory in self.REQUIRED_DIRS:
            (project_dir / directory).mkdir(parents=True, exist_ok=True)

        model = _first(blueprint, "model", "model_name", default="TODO: specify model")
        dataset = _first(blueprint, "dataset", "dataset_name", default="TODO: specify dataset")
        baseline = _first(blueprint, "baseline", "baselines", default=[])
        metrics = _metric_mapping(_first(blueprint, "metrics", default=None))
        paper_metrics = _mock_metric_config(blueprint)
        config = {
            "schema_version": self.schema_version,
            "experiment_id": blueprint_id or safe_filename(title),
            "name": title,
            "seed": int(_first(blueprint, "seed", default=42) or 42),
            "research_question": _first(blueprint, "research_question", "question", default=""),
            "hypothesis": _first(blueprint, "hypothesis", default=""),
            "implementation_plan": json_safe(_first(blueprint, "implementation_plan", "plan", default=[])),
            "independent_variables": json_safe(_first(blueprint, "independent_variables", "variables", default=[])),
            "dependent_variables": json_safe(_first(blueprint, "dependent_variables", default=[])),
            "controls": json_safe(_first(blueprint, "controls", default=[])),
            "dataset": json_safe(dataset),
            "model": json_safe(model),
            "baseline": json_safe(baseline),
            "metrics": json_safe(metrics),
            "paper_reported_results": json_safe(paper_metrics),
            "mock": {
                "enabled": True,
                "metrics": paper_metrics,
                "TODO": "Replace mock implementation with the paper implementation.",
            },
        }
        files: list[Path] = []

        def write(relative: str, content: str) -> None:
            path = project_dir / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            files.append(path)

        # JSON is valid YAML 1.2, giving users a dependency-free generated
        # config while preserving the requested config.yaml filename.
        config_text = json.dumps(config, ensure_ascii=False, indent=2) + "\n"
        write("config.yaml", config_text)
        # Keep a copy in the requested ``configs/`` directory as well.  The
        # root file preserves compatibility with older generated projects,
        # while the directory copy makes the scaffold layout self-explanatory
        # and ready for multiple experiment configs.
        write("configs/config.yaml", config_text)
        write("run.py", _generated_run_py())
        write("evaluator.py", _generated_evaluator_py())
        write("README.md", self._readme(config, raw))
        write("tests/test_smoke.py", self._smoke_test())
        write("reports/.gitkeep", "")
        write("results/.gitkeep", "")
        warnings = self._warnings(config)
        return GeneratedExperiment(project_dir=project_dir, files=files, blueprint_id=blueprint_id, warnings=warnings)

    @staticmethod
    def _warnings(config: Mapping[str, Any]) -> list[str]:
        warnings: list[str] = []
        for field in ("dataset", "model", "research_question", "hypothesis"):
            value = config.get(field)
            if value in (None, "", [], {}, "TODO: specify model", "TODO: specify dataset"):
                warnings.append(f"TODO: provide {field}")
        if not config.get("metrics"):
            warnings.append("TODO: define evaluation metrics")
        return warnings

    @staticmethod
    def _readme(config: Mapping[str, Any], raw: Mapping[str, Any]) -> str:
        return textwrap.dedent(
            f"""\
            # {config.get('name', 'Paper2Lab Experiment')}

            Generated by Paper2Lab as an **honest experiment skeleton**.

            This project is runnable with the Python standard library only:

            ```text
            python run.py --seed {config.get('seed', 42)}
            ```

            The command runs a deterministic **Mock Experiment** and writes
            `result.json`. It does not claim to implement the paper's model.
            Replace the TODOs below before drawing scientific conclusions.

            ## Blueprint

            - Research question: {as_text(config.get('research_question'), 'TODO')}
            - Hypothesis: {as_text(config.get('hypothesis'), 'TODO')}
            - Implementation plan: {as_text(config.get('implementation_plan'), 'TODO')}
            - Dataset: {as_text(config.get('dataset'), 'TODO')}
            - Model: {as_text(config.get('model'), 'TODO')}
            - Baseline: {as_text(config.get('baseline'), 'TODO')}
            - Metrics: {as_text(list(config.get('metrics', {})), 'TODO')}

            ## TODO before real reproduction

            - [ ] Obtain and document the legally available dataset and split.
            - [ ] Implement the paper model in `models/` (no implementation is assumed here).
            - [ ] Implement preprocessing and training in `experiments/`.
            - [ ] Replace the mock evaluator with metric-specific evaluation.
            - [ ] Record environment, dependency versions, hardware, and seed policy.
            - [ ] Compare local results to the paper only after matching protocol and data.

            ## Files

            - `config.yaml` — JSON-compatible YAML experiment configuration.
            - `run.py` — deterministic standard-library mock runner.
            - `evaluator.py` — evaluator hook.
            - `results/` — result artifacts.
            - `reports/` — generated reproduction reports.
            """)

    @staticmethod
    def _smoke_test() -> str:
        return textwrap.dedent(
            '''\
            import json
            import subprocess
            import sys
            from pathlib import Path


            def test_generated_mock_run():
                root = Path(__file__).resolve().parents[1]
                output = root / "result.json"
                completed = subprocess.run(
                    [sys.executable, str(root / "run.py"), "--output", str(output)],
                    cwd=root,
                    check=True,
                    capture_output=True,
                    text=True,
                )
                assert completed.returncode == 0
                assert json.loads(output.read_text(encoding="utf-8"))["status"] == "completed"
            ''')


def generate_experiment_skeleton(
    blueprint: Any,
    destination: str | Path,
    *,
    experiment_name: str | None = None,
    overwrite: bool = False,
) -> GeneratedExperiment:
    """Convenience function wrapping :class:`ExperimentSkeletonGenerator`."""

    return ExperimentSkeletonGenerator().generate(
        blueprint,
        destination,
        experiment_name=experiment_name,
        overwrite=overwrite,
    )


# Historical integrations called this component ``CodeSkeletonGenerator``;
# retain that name without importing the application blueprint module.
CodeSkeletonGenerator = ExperimentSkeletonGenerator


__all__ = ["GeneratedExperiment", "ExperimentSkeletonGenerator", "CodeSkeletonGenerator", "generate_experiment_skeleton"]
