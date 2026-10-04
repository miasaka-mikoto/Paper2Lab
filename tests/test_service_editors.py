"""Regression tests for the paper/experiment editor APIs."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from paper2lab import Paper2LabService
from paper2lab.models import FormulaNote, Paper, RunStatus
from paper2lab.ui import Paper2LabApp


def test_metadata_and_manual_section_edits_survive_reparse(tmp_path: Path) -> None:
    service = Paper2LabService(tmp_path / "workspace")
    paper = service.create_sample_paper()
    service.update_metadata(paper.paper_id, authors="A, B", tags="demo,review", year="2025", notes="check")
    assert paper.authors == ["A", "B"]
    assert paper.tags == ["demo", "review"]
    section = service.parse_structure(paper.paper_id)[0]
    service.edit_section(paper.paper_id, section.section_id, {"title": "Reviewed", "content": "Manual correction"})
    service.parse_structure(paper.paper_id, preserve_manual=True)
    assert service.store.require_paper(paper.paper_id).sections[0].title == "Reviewed"
    assert service.store.require_paper(paper.paper_id).sections[0].content == "Manual correction"


def test_editor_records_and_result_provenance_graph(tmp_path: Path) -> None:
    service = Paper2LabService(tmp_path / "workspace")
    paper = service.create_sample_paper()
    claim = service.extract_claims(paper.paper_id)[0]
    service.update_claim(paper.paper_id, claim.claim_id, kind="main", text="Reviewed claim")
    note = service.add_formula_note(paper.paper_id, "formula", "x = 1")
    service.update_formula_note(paper.paper_id, note.note_id, explanation="reviewed")
    blueprint = service.create_blueprint(paper.paper_id, claim.claim_id)
    service.update_blueprint(paper.paper_id, blueprint.blueprint_id, plan=["verify"])
    service.run_experiment(paper.paper_id, blueprint.blueprint_id)
    graph = service.research_graph(paper.paper_id)
    result_labels = [node["label"] for node in graph["nodes"] if node["type"] == "Result"]
    assert any(label.startswith("Paper ") for label in result_labels)
    assert any(label.startswith("Local ") for label in result_labels)
    assert any(edge["relation"] == "REPORTS_RESULT" for edge in graph["edges"])
    assert any(edge["relation"] == "PRODUCES" for edge in graph["edges"])


def test_reextract_claims_preserves_ids_and_blueprint_references(tmp_path: Path) -> None:
    """Refreshing candidates must not orphan editor/blueprint references."""

    service = Paper2LabService(tmp_path / "workspace")
    paper = service.create_sample_paper()
    first = service.extract_claims(paper.paper_id)
    blueprint = service.create_blueprint(paper.paper_id, first[0].claim_id)
    first_ids = {claim.claim_id for claim in first}

    # Selecting another offline provider still uses deterministic extraction,
    # but should retain the durable IDs already exposed to the UI.
    refreshed = service.extract_claims(paper.paper_id, provider="mock")
    assert {claim.claim_id for claim in refreshed} == first_ids
    assert service.get_claim(paper.paper_id, first[0].claim_id) is not None
    assert service.get_blueprint(paper.paper_id, blueprint.blueprint_id).claim_id == first[0].claim_id
    service.update_claim(paper.paper_id, first[0].claim_id, evidence="reviewed")


def test_blueprint_creation_reports_missing_or_unknown_claim(tmp_path: Path) -> None:
    service = Paper2LabService(tmp_path / "workspace")
    source = tmp_path / "plain.txt"
    source.write_text("A plain note without a testable claim marker.", encoding="utf-8")
    paper = service.import_paper(source)
    try:
        service.create_blueprint(paper.paper_id)
    except ValueError as exc:
        assert "No extractable claims" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("expected a clear missing-claim error")

    sample = service.create_sample_paper()
    claims = service.extract_claims(sample.paper_id)
    try:
        service.create_blueprint(sample.paper_id, "claim_missing")
    except KeyError as exc:
        assert "claim_missing" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("expected an unknown-claim error")


def test_clearing_optional_year_normalizes_to_none(tmp_path: Path) -> None:
    service = Paper2LabService(tmp_path / "workspace")
    paper = service.create_sample_paper()
    service.update_metadata(paper.paper_id, year="")
    assert paper.year is None


def test_legacy_short_model_records_are_readable() -> None:
    paper = Paper.from_dict(
        {
            "id": "paper_legacy",
            "title": "Legacy",
            "authors": "A, B",
            "sections": [{"id": "section_1", "name": "Method", "text": "body"}],
            "claims": [{"id": "claim_1", "text": "claim", "kind": "main"}],
            "runs": [{"id": "run_1", "blueprint_id": "bp_1", "status": "completed"}],
        }
    )
    assert paper.paper_id == "paper_legacy"
    assert paper.authors == ["A", "B"]
    assert paper.sections[0].title == "Method"
    assert paper.claims[0].kind == "main"
    assert paper.runs[0].status is RunStatus.SUCCEEDED


def test_ui_formula_json_mappings_become_formula_notes(tmp_path: Path) -> None:
    """The editable Formula pane must not leave raw dicts in the model."""

    service = Paper2LabService(tmp_path / "workspace")
    paper = service.create_sample_paper()

    class TextStub:
        def get(self, *_args):
            return '{"kind": "algorithm", "content": "score = overlap", "explanation": "reviewed"}'

    app_stub = SimpleNamespace(
        formula_text=TextStub(),
        selected_paper_id=paper.paper_id,
        # Pass the raw service rather than BackendAdapter to cover the
        # lightweight integration path used by embedders.
        backend=service,
    )
    app_stub._selected_paper = lambda: paper

    Paper2LabApp._save_formula_notes(app_stub)

    assert len(paper.formula_notes) == 0  # a JSON object is intentionally not a list

    class ListTextStub(TextStub):
        def get(self, *_args):
            return '[{"kind": "algorithm", "content": "score = overlap", "explanation": "reviewed"}]'

    app_stub.formula_text = ListTextStub()
    Paper2LabApp._save_formula_notes(app_stub)
    assert len(paper.formula_notes) == 1
    assert isinstance(paper.formula_notes[0], FormulaNote)
    assert paper.formula_notes[0].paper_id == paper.paper_id

    reopened = Paper2LabService(tmp_path / "reopened")
    reopened.load_project(service.save_project())
    restored = reopened.get_paper(paper.paper_id)
    assert restored is not None
    assert isinstance(restored.formula_notes[0], FormulaNote)
