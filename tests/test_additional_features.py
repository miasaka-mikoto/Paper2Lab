from __future__ import annotations

from pathlib import Path

from paper2lab import Paper2LabService, RuleBasedExtractor
from paper2lab.ui import CompatBackend


def test_section_edit_graph_and_formula_note(tmp_path: Path) -> None:
    service = Paper2LabService(tmp_path / "workspace")
    paper = service.create_sample_paper()
    service.extract_claims(paper.paper_id)
    section = service.parse_structure(paper.paper_id)[0]
    service.rename_section(paper.paper_id, section.section_id, "Front Matter (Reviewed)")
    service.add_formula_note(paper.paper_id, "algorithm", "score = |query ∩ key|", "Token overlap used by the fictional sample.")
    blueprint = service.create_blueprint(paper.paper_id)
    service.run_experiment(paper.paper_id, blueprint.blueprint_id, seed=7)
    graph = service.research_graph(paper.paper_id)
    types = {node["type"] for node in graph["nodes"]}
    assert {"Paper", "Claim", "Experiment", "Method", "Dataset", "Metric", "Result"}.issubset(types)
    assert any(edge["relation"] == "PRODUCES" for edge in graph["edges"])
    assert service.reproducibility_score(paper.paper_id).total == 8
    assert RuleBasedExtractor is not None


def test_compat_full_text_search_and_section_edit() -> None:
    backend = CompatBackend()
    paper = backend.create_sample_paper()
    assert backend.search("repeated failures")[0].id == paper.id
    section = paper.sections[0]
    backend.rename_section(paper.id, section["id"], "Reviewed")
    assert paper.sections[0]["name"] == "Reviewed"

