from __future__ import annotations

import json
from pathlib import Path

from paper2lab.generator import generate_experiment_skeleton
from paper2lab.models import ComparisonStatus, MetricComparison
from paper2lab.parser import DocumentParser
from paper2lab.report import build_markdown_report, markdown_to_html


def test_html_meta_and_visible_text_are_separated(tmp_path: Path) -> None:
    source = tmp_path / "paper.html"
    source.write_text(
        """<!doctype html><html><head>
        <title>HTML Paper</title>
        <meta name='citation_author' content='Ada Lovelace'>
        <meta name='citation_author' content='Grace Hopper'>
        <meta name='citation_publication_date' content='2024-05-01'>
        <meta name='citation_conference_title' content='Offline Workshop'>
        <meta name='keywords' content='agents, memory'>
        </head><body><h1>Abstract</h1><p>We test a deterministic memory.</p></body></html>""",
        encoding="utf-8",
    )
    parsed = DocumentParser().parse_file(source)
    assert parsed.metadata.title == "HTML Paper"
    assert parsed.metadata.authors == ["Ada Lovelace", "Grace Hopper"]
    assert parsed.metadata.year == 2024
    assert parsed.metadata.venue == "Offline Workshop"
    assert parsed.metadata.keywords == ["agents", "memory"]
    assert "HTML Paper" not in parsed.text
    assert parsed.metadata.abstract == "We test a deterministic memory."


def test_txt_metadata_and_pdf_tj_fallback(tmp_path: Path) -> None:
    parser = DocumentParser()
    parsed = parser.parse_text(
        "Title: A Plain Paper\nAuthors: A. One; B. Two\nYear: 2023\nVenue: Local\nAbstract: Short abstract",
        "TXT",
        filename="fallback.txt",
    )
    assert parsed.metadata.title == "A Plain Paper"
    assert parsed.metadata.authors == ["A. One", "B. Two"]
    assert parsed.metadata.year == 2023
    assert parsed.metadata.venue == "Local"
    assert parsed.metadata.abstract == "Short abstract"

    # Force the dependency-free PDF text path with Tj/TJ/hex operators.
    pdf = tmp_path / "tiny.pdf"
    pdf.write_bytes(b"%PDF-1.4\n(Hello\\040PDF) Tj\n[(World) 10 (TJ)] TJ\n<54657374> Tj\n")
    text, warnings = parser._read_pdf(pdf)
    assert "Hello PDF" in text
    assert "World" in text and "TJ" in text
    assert "Test" in text
    assert warnings

    # Text-only PDF fixtures may retain Markdown-style hash headings.  They
    # should still enter the canonical structure parser instead of becoming
    # one opaque Full Text section.
    parsed_pdf = parser.parse_text("# Abstract\nA.\n# Method\nB.", "pdf", filename="hash-headings.pdf")
    assert [section.title for section in parsed_pdf.sections] == ["Abstract", "Method"]


def test_txt_colon_headings_are_sections() -> None:
    parsed = DocumentParser().parse_text(
        "Title: Colon Headings\n\nAbstract:\nA short abstract.\n\nMethod:\nA documented method.",
        "txt",
        filename="colon.txt",
    )
    assert [section.title for section in parsed.sections] == ["Front Matter", "Abstract", "Method"]
    assert parsed.metadata.abstract == "A short abstract."
    assert parsed.sections[-1].content == "A documented method."

    inline = DocumentParser().parse_text(
        "Title: Inline Headings\nAbstract: One-line abstract.\nMethod: One-line method.",
        "txt",
        filename="inline.txt",
    )
    assert [section.title for section in inline.sections] == ["Front Matter", "Abstract", "Method"]
    assert inline.metadata.title == "Inline Headings"
    assert inline.sections[1].content == "One-line abstract."


def test_report_normalizes_domain_comparison_and_keeps_provenance() -> None:
    comparison = MetricComparison(
        metric="accuracy",
        paper_result=0.81,
        local_result=0.80,
        absolute_difference=-0.01,
        relative_difference=-0.0123,
        status=ComparisonStatus.DIFFERENT,
    )
    run = {
        "status": "succeeded",
        "seed": 7,
        "runtime_seconds": 0.125,
        "config": {"dataset": "fictional"},
        "environment": {"platform": "test"},
        "result": {"mode": "mock"},
        "stdout": "finished",
        "stderr": "",
    }
    markdown = build_markdown_report(
        {"title": "Paper"},
        {"blueprint_id": "bp-1", "implementation_plan": []},
        [comparison],
        run=run,
    )
    assert "| accuracy | 0.81 | 0.8 | -0.01" in markdown
    assert "Run Provenance" in markdown
    assert '"fictional"' in markdown
    html = markdown_to_html("# H\n\n- one\n- two\n\nparagraph\n\n|A|B|\n|-|-|\n|1|2|\n\nnext")
    assert html.count("<ul>") == 1
    assert "<ul>\n<ul>" not in html
    assert "</ul>\n<p>paragraph</p>" in html
    assert "</table>\n<p>next</p>" in html


def test_generator_preserves_declared_metric_names_without_fake_paper_values(tmp_path: Path) -> None:
    generated = generate_experiment_skeleton(
        {
            "blueprint_id": "bp",
            "name": "Metric declarations",
            "metrics": ["accuracy", "recall"],
        },
        tmp_path,
    )
    assert (generated.project_dir / "configs" / "config.yaml").is_file()
    config = json.loads((generated.project_dir / "config.yaml").read_text(encoding="utf-8"))
    assert set(config["mock"]["metrics"]) == {"accuracy", "recall"}
    assert config["mock"]["metrics"]["accuracy"]["paper"] is None
    output = generated.project_dir / "results" / "nested" / "result.json"
    import subprocess
    import sys

    completed = subprocess.run(
        [sys.executable, str(generated.project_dir / "run.py"), "--output", str(output)],
        cwd=generated.project_dir,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert output.is_file()
