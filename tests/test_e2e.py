"""Offline acceptance tests for the Paper2Lab sample workflow.

These tests deliberately exercise the stable UI/backend compatibility API so
they remain useful while the richer research engine evolves.  They never
contact a network service or a paid model provider.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from paper2lab.ui import CompatBackend  # noqa: E402
from paper2lab.parser import DocumentParser  # noqa: E402
from paper2lab.service import Paper2LabService  # noqa: E402


SAMPLE = ROOT / "sample" / "sample_paper.md"


class Paper2LabEndToEndTests(unittest.TestCase):
    def _import_and_blueprint(self, backend: CompatBackend):
        paper = backend.import_paper(SAMPLE)
        sections = backend.parse_structure(paper.id)
        claims = backend.extract_claims(paper.id)
        blueprint = backend.create_blueprint(paper.id, claims[0]["id"])
        return paper, sections, claims, blueprint

    def test_import_structure_claim_and_blueprint(self) -> None:
        backend = CompatBackend()
        paper, sections, claims, blueprint = self._import_and_blueprint(backend)

        self.assertIn("EchoRAG", paper.title)
        self.assertGreaterEqual(len(sections), 8)
        section_names = {row["name"] for row in sections}
        self.assertTrue({"Abstract", "Method", "Experiment", "Results"}.issubset(section_names))
        self.assertGreaterEqual(len(claims), 1)
        self.assertEqual(blueprint["claim_id"], claims[0]["id"])
        self.assertTrue(blueprint["research_question"])
        self.assertTrue(blueprint["hypothesis"])
        self.assertIn("accuracy", blueprint["paper_reported_result"])

        # The richer document parser is also exercised against the same local
        # fixture so metadata survives import, not just the reader text.
        parsed = DocumentParser().parse_file(SAMPLE)
        self.assertEqual(parsed.metadata.title, paper.title)
        self.assertIn("Fictional Workshop", parsed.metadata.venue)
        self.assertEqual(parsed.metadata.year, 2026)

    def test_skeleton_mock_run_compare_and_report(self) -> None:
        backend = CompatBackend()
        paper, _sections, _claims, blueprint = self._import_and_blueprint(backend)

        with tempfile.TemporaryDirectory(prefix="paper2lab-e2e-") as tmp:
            generated = Path(backend.generate_skeleton(paper.id, blueprint["id"], tmp))
            # The compatibility backend places config.yaml below configs; the
            # richer generator may place it at the project root.  Both forms
            # are accepted while preserving the required file name.
            self.assertTrue((generated / "run.py").is_file())
            self.assertTrue((generated / "evaluator.py").is_file())
            self.assertTrue((generated / "README.md").is_file())
            self.assertTrue((generated / "config.yaml").is_file() or (generated / "configs" / "config.yaml").is_file())
            for folder in ("experiments", "datasets", "models", "evaluators", "results", "reports", "tests"):
                self.assertTrue((generated / folder).is_dir(), folder)

            run = backend.run_experiment(paper.id, blueprint["id"], seed=7)
            self.assertEqual(run.status, "Completed")
            self.assertEqual(run.environment.get("offline"), True)
            self.assertEqual(run.result.get("source"), "MockExperiment")

            differences = backend.compare_results(paper.id, blueprint["id"])
            self.assertIn("accuracy", differences)
            self.assertIn("absolute", differences["accuracy"])
            self.assertIn("relative", differences["accuracy"])

            report_path = Path(
                backend.generate_report(
                    paper.id,
                    blueprint["id"],
                    output_dir=str(Path(tmp) / "reports"),
                )
            )
            self.assertTrue(report_path.is_file())
            report_text = report_path.read_text(encoding="utf-8")
            for heading in ("Reproduction Summary", "What Was Reproduced", "Differences", "Next Experiments"):
                self.assertIn(heading, report_text)

    def test_close_reopen_restores_all_data(self) -> None:
        backend = CompatBackend()
        paper, _sections, _claims, blueprint = self._import_and_blueprint(backend)
        backend.run_experiment(paper.id, blueprint["id"], seed=7)
        backend.compare_results(paper.id, blueprint["id"])

        with tempfile.TemporaryDirectory(prefix="paper2lab-restart-") as tmp:
            project = Path(tmp) / "roundtrip.paper2lab.json"
            backend.save_project(project)
            # Verify the saved artifact is human-readable JSON before loading.
            payload = json.loads(project.read_text(encoding="utf-8"))
            self.assertEqual(len(payload["papers"]), 1)
            self.assertEqual(len(payload["runs"]), 1)

            reopened = CompatBackend()
            reopened.load_project(project)
            restored = reopened.get_paper(paper.id)
            self.assertIsNotNone(restored)
            assert restored is not None
            self.assertEqual(restored.title, paper.title)
            self.assertEqual(len(restored.sections), len(paper.sections))
            self.assertEqual(len(restored.claims), len(paper.claims))
            self.assertEqual(len(restored.blueprints), 1)
            self.assertEqual(restored.blueprints[0]["id"], blueprint["id"])
            self.assertEqual(len(reopened.runs), 1)

    def test_domain_service_end_to_end(self) -> None:
        """Exercise the model-backed service, not only the UI compatibility layer."""

        with tempfile.TemporaryDirectory(prefix="paper2lab-service-") as tmp:
            root = Path(tmp)
            service = Paper2LabService(root / "workspace")
            paper = service.import_paper(SAMPLE)
            sections = service.parse_structure(paper.paper_id)
            claims = service.extract_claims(paper.paper_id)
            blueprint = service.create_blueprint(paper.paper_id, claims[0].claim_id)
            self.assertGreaterEqual(len(sections), 10)
            self.assertGreaterEqual(len(claims), 1)
            self.assertTrue(blueprint.paper_reported_result)

            skeleton = service.generate_skeleton(
                paper.paper_id,
                blueprint.blueprint_id,
                root / "generated_experiment",
            )
            self.assertTrue((skeleton / "config.yaml").is_file())
            self.assertTrue((skeleton / "run.py").is_file())
            self.assertTrue((skeleton / "evaluator.py").is_file())
            self.assertIn("TODO", (skeleton / "README.md").read_text(encoding="utf-8"))
            skeleton_result = skeleton / "results" / "results.json"
            generated_run = subprocess.run(
                [sys.executable, str(skeleton / "run.py"), "--seed", "7", "--output", str(skeleton_result)],
                cwd=skeleton,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(generated_run.returncode, 0, generated_run.stderr)
            self.assertTrue(skeleton_result.is_file())

            run = service.run_experiment(paper.paper_id, blueprint.blueprint_id, seed=7)
            self.assertEqual(getattr(run.status, "value", run.status), "succeeded")
            self.assertEqual(run.result.get("mode"), "mock")
            self.assertTrue(run.result.get("metrics"))

            comparisons = service.compare_results(paper.paper_id, blueprint.blueprint_id)
            self.assertGreaterEqual(len(comparisons), 1)
            self.assertTrue(all(getattr(row, "absolute_difference", None) is not None for row in comparisons))

            report_files = service.generate_report(
                paper.paper_id,
                blueprint.blueprint_id,
                root / "reports",
            )
            self.assertTrue(Path(report_files["markdown"]).is_file())
            self.assertTrue(Path(report_files["html"]).is_file())
            report_text = Path(report_files["markdown"]).read_text(encoding="utf-8")
            self.assertIn("Paper vs Local Result", report_text)

            saved = Path(service.save_project(root / "saved.paper2lab.json"))
            reopened = Paper2LabService(root / "reopened")
            reopened.load_project(saved)
            restored = reopened.get_paper(paper.paper_id)
            self.assertIsNotNone(restored)
            assert restored is not None
            self.assertEqual(restored.metadata.title, paper.metadata.title)
            self.assertEqual(len(restored.blueprints), 1)
            self.assertEqual(len(restored.runs), 1)

    def test_demo_script_reports_pass_and_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory(prefix="paper2lab-demo-cli-") as tmp:
            proc = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "run_demo.py"), "--workspace", tmp, "--fresh"],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            if proc.returncode != 0:
                self.fail(f"run_demo.py failed ({proc.returncode}):\n{proc.stdout}\n{proc.stderr}")
            summary = json.loads(proc.stdout)
            self.assertEqual(summary["status"], "PASS")
            self.assertTrue(summary["offline"])
            self.assertIn(summary["run_status"], {"Completed", "succeeded"})
            self.assertTrue(Path(summary["project_file"]).is_file())
            self.assertTrue(Path(summary["report"]).is_file())
            self.assertEqual(summary["title"], summary["restored_title"])
            self.assertGreaterEqual(summary["restored_blueprints"], 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
