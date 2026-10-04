"""Tkinter desktop workbench for Paper2Lab.

This module owns presentation and a small compatibility backend.  A richer
backend can be supplied by the rest of the project without making the GUI
depend on a particular implementation: ``BackendAdapter`` discovers a core
service when one is available and falls back to ``CompatBackend`` otherwise.

The fallback is deliberately deterministic and local-only.  It is enough to
walk a sample paper through import, section/claim extraction, blueprint
creation, mock execution, comparison and report generation while the research
engine is being developed.
"""

from __future__ import annotations

import html
import importlib
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, MutableMapping, Optional

try:  # Tkinter is part of the Python standard library on supported Windows builds.
    import tkinter as tk
    from tkinter import filedialog, messagebox, simpledialog, ttk
except Exception:  # pragma: no cover - permits importing the data layer headlessly.
    tk = None  # type: ignore[assignment]
    filedialog = messagebox = simpledialog = ttk = None  # type: ignore[assignment]


APP_TITLE = "Paper2Lab — 论文复现实验工厂"
SUPPORTED_SUFFIXES = {".pdf", ".md", ".markdown", ".txt", ".html", ".htm"}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _json_default(value: Any) -> Any:
    """Serialize canonical dataclasses/enums in the reader panes."""
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return value.to_dict()
    enum_value = getattr(value, "value", None)
    if enum_value is not None and isinstance(enum_value, (str, int, float, bool)):
        return enum_value
    if hasattr(value, "__dict__"):
        return {key: item for key, item in vars(value).items() if not key.startswith("_")}
    return str(value)


def _field(value: Any, *names: str, default: Any = "") -> Any:
    """Read a field from either fallback dictionaries or domain dataclasses.

    The production service uses ``paper_id``/``claim_id`` and nested metadata,
    while the compatibility backend intentionally uses short UI-friendly keys.
    Keeping the normalization here lets one workbench render both forms.
    """

    if value is None:
        return default
    for name in names:
        if isinstance(value, Mapping) and name in value:
            return value[name]
        if hasattr(value, name):
            return getattr(value, name)
    metadata = value.get("metadata") if isinstance(value, Mapping) else getattr(value, "metadata", None)
    if metadata is not None and metadata is not value:
        for name in names:
            if isinstance(metadata, Mapping) and name in metadata:
                return metadata[name]
            if hasattr(metadata, name):
                return getattr(metadata, name)
    return default


def _id_of(value: Any, *kinds: str) -> str:
    names: list[str] = []
    for kind in kinds:
        names.extend((kind, f"{kind}_id"))
    result = _field(value, *names, default="")
    return str(result or "")


def _items(value: Any, *names: str) -> list[Any]:
    result = _field(value, *names, default=[])
    if result is None:
        return []
    if isinstance(result, (list, tuple, set)):
        return list(result)
    return list(result) if not isinstance(result, (str, bytes, Mapping)) and hasattr(result, "__iter__") else [result]


def _plain_text(path: Path) -> str:
    """Read an allowed local paper format without external packages.

    PDF extraction is intentionally conservative.  If a PDF parser is present
    in the host application, the discovered backend gets the first opportunity
    to read it.  The fallback still records the file and displays a useful
    placeholder instead of pretending that binary bytes are paper text.
    """

    suffix = path.suffix.lower()
    if suffix in {".html", ".htm"}:
        raw = path.read_text(encoding="utf-8", errors="replace")
        raw = re.sub(r"<script\b[^>]*>.*?</script>", " ", raw, flags=re.I | re.S)
        raw = re.sub(r"<style\b[^>]*>.*?</style>", " ", raw, flags=re.I | re.S)
        # Preserve block boundaries so the lightweight section parser can
        # still recognise ``<h2>Method</h2>`` and paragraph headings.
        raw = re.sub(r"<\s*/?\s*(?:br|p|div|h[1-6]|li|tr)\b[^>]*>", "\n", raw, flags=re.I)
        clean = html.unescape(re.sub(r"<[^>]+>", " ", raw))
        clean = re.sub(r"[ \t]+", " ", clean)
        clean = re.sub(r"\n{3,}", "\n\n", clean)
        return clean.strip()
    if suffix == ".pdf":
        # Do not add a dependency just for the fallback.  A few PDF text runs
        # are recoverable, which makes simple fixture PDFs useful in the demo.
        data = path.read_bytes()
        try:
            from pypdf import PdfReader  # type: ignore

            return "\n".join((p.extract_text() or "") for p in PdfReader(str(path)).pages).strip()
        except Exception:
            decoded = data.decode("latin-1", errors="ignore")
            chunks = re.findall(r"[A-Za-z][A-Za-z0-9 ,.;:'()\-]{8,}", decoded)
            return "\n".join(chunks[:200]).strip() or "[PDF imported; text extraction is unavailable in this installation.]"
    return path.read_text(encoding="utf-8", errors="replace")


def _title_from_text(text: str, fallback: str) -> str:
    for line in text.splitlines():
        line = re.sub(r"^[#\s]+", "", line).strip()
        if line and len(line) > 2:
            return line[:180]
    return fallback


@dataclass
class PaperRecord:
    id: str
    title: str
    authors: str = ""
    year: str = ""
    venue: str = ""
    abstract: str = ""
    keywords: list[str] = field(default_factory=list)
    notes: str = ""
    tags: list[str] = field(default_factory=list)
    local_path: str = ""
    text: str = ""
    sections: list[dict[str, Any]] = field(default_factory=list)
    claims: list[dict[str, Any]] = field(default_factory=list)
    blueprints: list[dict[str, Any]] = field(default_factory=list)
    formulas: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)


@dataclass
class RunRecord:
    id: str
    blueprint_id: str
    status: str
    started_at: str
    finished_at: str = ""
    stdout: str = ""
    stderr: str = ""
    runtime_seconds: float = 0.0
    seed: int = 42
    result: dict[str, Any] = field(default_factory=dict)
    environment: dict[str, Any] = field(default_factory=dict)


class CompatBackend:
    """Small deterministic local backend used when no engine is installed."""

    SECTION_NAMES = (
        "Abstract",
        "Introduction",
        "Related Work",
        "Method",
        "Dataset",
        "Experiment",
        "Metrics",
        "Results",
        "Ablation",
        "Limitations",
        "Conclusion",
        "Appendix",
    )

    def __init__(self) -> None:
        self.papers: dict[str, PaperRecord] = {}
        self.runs: dict[str, RunRecord] = {}
        self.run_to_paper: dict[str, str] = {}
        self.logs: list[str] = []
        self._log("Compatibility backend ready (local deterministic mode).")

    def _log(self, message: str) -> None:
        self.logs.append(f"[{_now()}] {message}")

    def list_papers(self) -> list[PaperRecord]:
        return list(self.papers.values())

    def get_paper(self, paper_id: str) -> Optional[PaperRecord]:
        return self.papers.get(paper_id)

    def update_metadata(self, paper_id: str, **changes: Any) -> PaperRecord:
        """Update editable library metadata in the compatibility backend.

        The desktop UI uses the same small editor for the canonical service
        and this fallback backend.  Keeping the aliases here means a project
        opened on a minimal Python installation does not silently lose title,
        author, keyword, tag, or note edits.
        """
        paper = self._require_paper(paper_id)
        aliases = {
            "name": "title",
            "author": "authors",
            "path": "local_path",
            "keyword": "keywords",
            "tag": "tags",
            "note": "notes",
        }
        allowed = {
            "title", "authors", "year", "venue", "abstract", "keywords",
            "notes", "tags", "local_path",
        }
        for key, value in changes.items():
            canonical = aliases.get(key, key)
            if canonical not in allowed:
                raise ValueError(f"Unknown paper metadata field: {key}")
            if canonical in {"authors", "keywords", "tags"}:
                if isinstance(value, str):
                    value = [part.strip() for part in value.replace("\n", ",").split(",") if part.strip()]
                elif value is None:
                    value = []
                elif not isinstance(value, list):
                    value = list(value) if isinstance(value, (tuple, set, frozenset)) else [value]
            if canonical == "year":
                if value in (None, ""):
                    value = None
                else:
                    try:
                        value = int(value)
                    except (TypeError, ValueError) as exc:
                        raise ValueError("Paper year must be an integer or empty") from exc
            setattr(paper, canonical, value)
        paper.updated_at = _now()
        self._log(f"Updated metadata for {paper.title}.")
        return paper

    update_paper_metadata = update_metadata

    def search(self, query: str) -> list[PaperRecord]:
        """Full-text search used by the library filter and headless callers."""
        needle = (query or "").casefold().strip()
        if not needle:
            return self.list_papers()
        matches: list[PaperRecord] = []
        for paper in self.papers.values():
            haystack = "\n".join(
                str(value)
                for value in (
                    paper.title,
                    paper.authors,
                    paper.year,
                    paper.venue,
                    paper.abstract,
                    paper.keywords,
                    paper.notes,
                    paper.text,
                    paper.local_path,
                    paper.tags,
                )
            ).casefold()
            if needle in haystack:
                matches.append(paper)
        return matches

    search_papers = search

    def import_paper(self, path: str | os.PathLike[str]) -> PaperRecord:
        source = Path(path)
        if source.suffix.lower() not in SUPPORTED_SUFFIXES:
            raise ValueError(f"Unsupported paper format: {source.suffix or '(no extension)'}")
        text = _plain_text(source)
        paper = PaperRecord(
            id=_uid("paper"),
            title=_title_from_text(text, source.stem.replace("_", " ")),
            local_path=str(source.resolve()),
            text=text,
        )
        paper.abstract = self._find_abstract(text)
        self.papers[paper.id] = paper
        self.parse_structure(paper.id)
        self._log(f"Imported paper: {paper.title}")
        return paper

    def create_sample_paper(self) -> PaperRecord:
        text = SAMPLE_PAPER_TEXT
        paper = PaperRecord(
            id="sample-paper",
            title="A Tiny Retrieval Memory Improves Tool-Use Reliability",
            authors="Paper2Lab Demo Authors",
            year="2026",
            venue="Synthetic Workshop",
            abstract=self._find_abstract(text),
            keywords=["retrieval", "memory", "tool use"],
            tags=["sample", "mock"],
            text=text,
        )
        self.papers[paper.id] = paper
        self.parse_structure(paper.id)
        self.extract_claims(paper.id)
        self._log("Created built-in fictional sample paper (no copyrighted content).")
        return paper

    def _find_abstract(self, text: str) -> str:
        match = re.search(r"(?is)\babstract\b\s*[:\-]?\s*(.*?)(?=\n\s*(?:1\.?\s*)?introduction\b|\n\s*keywords?\b|\Z)", text)
        return re.sub(r"\s+", " ", match.group(1)).strip()[:1200] if match else ""

    def parse_structure(self, paper_id: str) -> list[dict[str, Any]]:
        paper = self._require_paper(paper_id)
        text = paper.text
        # Recognise common markdown headings and numbered/plain headings.
        heading_re = re.compile(
            r"(?im)^(?:#{1,6}\s*|\d+(?:\.\d+)*[.)]?\s+)?(" + "|".join(map(re.escape, self.SECTION_NAMES)) + r")\s*:?[ \t]*$"
        )
        matches = list(heading_re.finditer(text))
        sections: list[dict[str, Any]] = []
        if not matches and text:
            # A plain file is still represented as a single user-editable section.
            sections = [{"id": _uid("section"), "name": "Full Text", "start": 0, "end": len(text), "text": text}]
        else:
            for i, match in enumerate(matches):
                end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
                body = text[match.end() : end].strip()
                sections.append({"id": _uid("section"), "name": match.group(1), "start": match.start(), "end": end, "text": body})
        paper.sections = sections
        paper.updated_at = _now()
        self._log(f"Parsed {len(sections)} sections for {paper.title}.")
        return sections

    def rename_section(self, paper_id: str, section_id: str, new_title: str) -> dict[str, Any]:
        paper = self._require_paper(paper_id)
        title = (new_title or "").strip()
        if not title:
            raise ValueError("Section title cannot be empty")
        for section in paper.sections:
            if section.get("id") == section_id:
                section["name"] = title
                paper.updated_at = _now()
                self._log(f"Renamed section {section_id} to {title}.")
                return section
        raise KeyError(f"Unknown section: {section_id}")

    edit_section = rename_section

    def extract_claims(self, paper_id: str) -> list[dict[str, Any]]:
        paper = self._require_paper(paper_id)
        claims: list[dict[str, Any]] = []
        # Deliberately transparent heuristics: sentences containing contribution
        # verbs are surfaced for human review, never presented as ground truth.
        pattern = re.compile(r"(?i)(?:we (?:show|find|demonstrate|propose|observe)|our (?:method|results|approach)|improv(?:es|ed)|outperform|reduces?)")
        for section in paper.sections:
            for sentence in re.split(r"(?<=[.!?])\s+|\n+", section.get("text", "")):
                sentence = sentence.strip(" -*\t")
                if len(sentence) < 24 or not pattern.search(sentence):
                    continue
                claims.append(
                    {
                        "id": _uid("claim"),
                        "claim_text": sentence[:500],
                        "evidence": f"Section: {section['name']}",
                        "section": section["name"],
                        "importance": "Main" if len(claims) == 0 else "Supporting",
                        "reproducible": "Needs review",
                        "required_experiment": "Recreate the reported comparison",
                        "kind": "Main Claim" if len(claims) == 0 else "Supporting Claim",
                    }
                )
        if not claims:
            claims.append(
                {
                    "id": _uid("claim"),
                    "claim_text": "The paper reports an experimentally testable result.",
                    "evidence": "Generated fallback; inspect source text.",
                    "section": "Full Text",
                    "importance": "Supporting",
                    "reproducible": "Needs review",
                    "required_experiment": "Define a local evaluation.",
                    "kind": "Supporting Claim",
                }
            )
        paper.claims = claims
        paper.updated_at = _now()
        self._log(f"Extracted {len(claims)} reviewable claims for {paper.title}.")
        return claims

    def create_blueprint(self, paper_id: str, claim_id: str | None = None) -> dict[str, Any]:
        paper = self._require_paper(paper_id)
        if not paper.claims:
            self.extract_claims(paper_id)
        claim = next((c for c in paper.claims if c["id"] == claim_id), paper.claims[0])
        blueprint = {
            "id": _uid("blueprint"),
            "claim_id": claim["id"],
            "research_question": f"Can we reproduce: {claim['claim_text']}",
            "hypothesis": "The proposed method will match the paper's reported direction under the stated controls.",
            "independent_variables": ["method", "retrieval_memory"],
            "dependent_variables": ["accuracy"],
            "controls": ["same synthetic dataset", "fixed seed"],
            "dataset": "paper-reported dataset (availability to be verified)",
            "model": "TODO: specify the paper model",
            "baseline": "TODO: implement the paper baseline",
            "metrics": ["accuracy"],
            "expected_result": "Improvement in the reported direction",
            "implementation_plan": [
                "Verify the fictional dataset split and metric protocol.",
                "Implement the method and baseline; keep missing code as TODO.",
                "Run a fixed-seed mock evaluation and record stdout/environment.",
                "Compare local values with the paper-reported result and document causes.",
            ],
            "paper_reported_result": {"accuracy": 0.814},
            "local_result": {},
            "difference": {},
            "status": "Draft",
            "created_at": _now(),
        }
        paper.blueprints.append(blueprint)
        paper.updated_at = _now()
        self._log(f"Created experiment blueprint {blueprint['id']}.")
        return blueprint

    def generate_skeleton(self, paper_id: str, blueprint_id: str | None = None, output_dir: str | None = None) -> str:
        paper = self._require_paper(paper_id)
        blueprint = next((b for b in paper.blueprints if b["id"] == blueprint_id), paper.blueprints[-1] if paper.blueprints else None)
        if blueprint is None:
            blueprint = self.create_blueprint(paper_id)
        target = Path(output_dir or Path.cwd() / "paper2lab_experiments" / blueprint["id"])
        for folder in ("experiments", "configs", "datasets", "models", "evaluators", "results", "reports", "tests"):
            (target / folder).mkdir(parents=True, exist_ok=True)
        (target / "configs" / "config.yaml").write_text(
            "# Generated by Paper2Lab; review all TODO fields before running.\n"
            f"experiment_id: {blueprint['id']}\nseed: 42\nmetric: accuracy\n", encoding="utf-8"
        )
        (target / "run.py").write_text(
            "\"\"\"Explicit mock runner; replace TODO implementation with the paper model.\"\"\"\n"
            "# TODO: implement the model and dataset described by the paper.\n"
            "from pathlib import Path\n"
            "print('Paper2Lab skeleton: mock run only')\n", encoding="utf-8"
        )
        (target / "evaluator.py").write_text(
            "# TODO: implement the paper's evaluator and metric details.\n"
            "def evaluate(predictions, labels):\n    raise NotImplementedError('Paper-specific evaluator required')\n", encoding="utf-8"
        )
        (target / "README.md").write_text(
            f"# Paper2Lab experiment {blueprint['id']}\n\n"
            "This directory is a transparent scaffold. TODO markers identify paper-specific work.\n", encoding="utf-8"
        )
        self._log(f"Generated experiment skeleton at {target}.")
        return str(target)

    def run_experiment(self, paper_id: str, blueprint_id: str | None = None, seed: int = 42) -> RunRecord:
        paper = self._require_paper(paper_id)
        blueprint = next((b for b in paper.blueprints if b["id"] == blueprint_id), paper.blueprints[-1] if paper.blueprints else None)
        if blueprint is None:
            blueprint = self.create_blueprint(paper_id)
        started = time.perf_counter()
        run = RunRecord(id=_uid("run"), blueprint_id=blueprint["id"], status="Running", started_at=_now(), seed=seed)
        # deterministic mock signal, explicitly labelled as synthetic.
        time.sleep(0.02)
        local_accuracy = 0.809
        run.status = "Completed"
        run.finished_at = _now()
        run.runtime_seconds = round(time.perf_counter() - started, 4)
        run.stdout = "MockExperiment: deterministic synthetic evaluation completed.\n"
        run.result = {"accuracy": local_accuracy, "source": "MockExperiment"}
        run.environment = {"python": sys.version.split()[0], "provider": "Mock LLM / RuleBased", "offline": True}
        self.runs[run.id] = run
        self.run_to_paper[run.id] = paper.id
        blueprint["local_result"] = run.result.copy()
        blueprint["status"] = "Executed (mock)"
        paper.updated_at = _now()
        self._log(f"Completed mock run {run.id}; accuracy={local_accuracy:.3f}.")
        return run

    def pause_run(self, run_id: str | None = None) -> bool:
        """Best-effort lifecycle hook; a future process runner can override it."""
        run = self.runs.get(run_id or "")
        if run and run.status == "Running":
            run.status = "Paused"
            self._log(f"Paused run {run.id}.")
            return True
        self._log("Pause requested, but no running compatibility run exists.")
        return False

    def resume_run(self, run_id: str | None = None) -> bool:
        """Resume a paused compatibility run.

        The compatibility backend is deliberately synchronous, so this only
        models the visible lifecycle state.  Keeping the method here is
        important because the same toolbar is used with both this fallback
        and the canonical subprocess service; a Resume click must not turn
        into a missing-method error when the app is running in offline mode.
        """
        run = self.runs.get(run_id or "")
        if run and run.status == "Paused":
            run.status = "Running"
            self._log(f"Resumed run {run.id}.")
            return True
        self._log("Resume requested, but no paused compatibility run exists.")
        return False

    def cancel_run(self, run_id: str | None = None) -> bool:
        run = self.runs.get(run_id or "")
        if run and run.status in {"Running", "Paused"}:
            run.status = "Cancelled"
            run.finished_at = _now()
            self._log(f"Cancelled run {run.id}.")
            return True
        self._log("Cancel requested, but no active compatibility run exists.")
        return False

    def retry_run(self, run_id: str | None = None) -> RunRecord | None:
        old = self.runs.get(run_id or "")
        paper_id = self.run_to_paper.get(run_id or "")
        if old and paper_id:
            self._log(f"Retrying run {old.id}.")
            return self.run_experiment(paper_id, old.blueprint_id, seed=old.seed)
        self._log("Retry requested, but the run is not known to the compatibility backend.")
        return None

    def compare_results(self, paper_id: str, blueprint_id: str | None = None) -> dict[str, Any]:
        paper = self._require_paper(paper_id)
        blueprint = next((b for b in paper.blueprints if b["id"] == blueprint_id), paper.blueprints[-1] if paper.blueprints else None)
        if blueprint is None:
            raise ValueError("Create an experiment blueprint first.")
        paper_result = blueprint.get("paper_reported_result", {}) or {}
        local_result = blueprint.get("local_result", {}) or {}
        diff: dict[str, dict[str, Any]] = {}
        for key, expected in paper_result.items():
            actual = local_result.get(key)
            if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
                absolute = round(actual - expected, 6)
                relative = round(absolute / expected, 6) if expected else None
                diff[key] = {"paper": expected, "local": actual, "absolute": absolute, "relative": relative, "status": "Close" if abs(absolute) <= 0.01 else "Review"}
        blueprint["difference"] = diff
        self._log(f"Compared paper and local results for {blueprint['id']}.")
        return diff

    def generate_report(self, paper_id: str, blueprint_id: str | None = None, output_dir: str | None = None) -> str:
        paper = self._require_paper(paper_id)
        blueprint = next((b for b in paper.blueprints if b["id"] == blueprint_id), paper.blueprints[-1] if paper.blueprints else None)
        if blueprint is None:
            raise ValueError("Create an experiment blueprint first.")
        differences = blueprint.get("difference") or self.compare_results(paper_id, blueprint["id"])
        lines = [
            f"# Reproduction Report: {paper.title}",
            "",
            "> Generated locally by Paper2Lab. Mock results are synthetic and are not evidence about the paper.",
            "",
            "## Reproduction Summary",
            f"- Blueprint: `{blueprint['id']}`",
            f"- Status: {blueprint.get('status', 'Draft')}",
            "",
            "## What Was Reproduced",
            "- The experiment workflow and result-comparison path were exercised with a deterministic mock provider.",
            "",
            "## What Was Not Reproduced",
            "- The paper-specific model, dataset, training procedure, and evaluator remain TODO until verified.",
            "",
            "## Differences",
        ]
        if differences:
            for metric, values in differences.items():
                lines.append(f"- **{metric}** — paper `{values['paper']}`, local `{values['local']}`, absolute `{values['absolute']}`, relative `{values['relative']}` ({values['status']}).")
        else:
            lines.append("- No comparable numeric results are available yet.")
        lines += [
            "",
            "## Possible Causes",
            "- Synthetic data/provider and unimplemented paper-specific details.",
            "",
            "## Limitations",
            "- Rule-based extraction is a review aid, not a claim of scientific truth.",
            "",
            "## Next Experiments",
            "- Verify dataset split, model checkpoint, hyperparameters, seed, and metric implementation against the source paper.",
        ]
        report = "\n".join(lines) + "\n"
        target_dir = Path(output_dir or Path.cwd() / "paper2lab_reports")
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{blueprint['id']}_reproduction_report.md"
        target.write_text(report, encoding="utf-8")
        # Keep an HTML companion without requiring a template engine.  The
        # Markdown file remains the canonical editable artifact.
        html_target = target.with_suffix(".html")
        html_target.write_text(
            "<!doctype html><meta charset='utf-8'><title>Paper2Lab Report</title>"
            "<style>body{font-family:Segoe UI,Arial;max-width:960px;margin:2rem auto;line-height:1.55}"
            "pre{white-space:pre-wrap;background:#f5f7fa;padding:1rem;border-radius:8px}</style>"
            f"<h1>{html.escape(paper.title)}</h1><pre>{html.escape(report)}</pre>",
            encoding="utf-8",
        )
        self._log(f"Generated report at {target}.")
        return str(target)

    def save_project(self, path: str | os.PathLike[str]) -> str:
        payload = {
            "version": 1,
            "saved_at": _now(),
            "papers": [asdict(p) for p in self.papers.values()],
            "runs": [asdict(r) for r in self.runs.values()],
            "run_to_paper": dict(self.run_to_paper),
        }
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self._log(f"Saved project to {target}.")
        return str(target)

    def load_project(self, path: str | os.PathLike[str]) -> None:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        self.papers = {p["id"]: PaperRecord(**p) for p in payload.get("papers", [])}
        self.runs = {r["id"]: RunRecord(**r) for r in payload.get("runs", [])}
        self.run_to_paper = dict(payload.get("run_to_paper", {}))
        if not self.run_to_paper:
            for paper in self.papers.values():
                blueprint_ids = {b.get("id") for b in paper.blueprints}
                for run in self.runs.values():
                    if run.blueprint_id in blueprint_ids:
                        self.run_to_paper[run.id] = paper.id
        self._log(f"Loaded project from {path}.")

    def _require_paper(self, paper_id: str) -> PaperRecord:
        paper = self.papers.get(paper_id)
        if paper is None:
            raise KeyError(f"Unknown paper: {paper_id}")
        return paper


class BackendAdapter:
    """Discover an optional project backend while retaining a stable UI API."""

    def __init__(self, backend: Any | None = None) -> None:
        self.engine = backend or self._discover() or CompatBackend()

    @staticmethod
    def _discover() -> Any | None:
        candidates = ("paper2lab.core", "paper2lab.backend", "paper2lab.service", "paper2lab.services", "paper2lab.engine")
        classes = ("Paper2LabCore", "Paper2LabService", "ResearchEngine", "ProjectStore")
        for module_name in candidates:
            try:
                module = importlib.import_module(module_name)
            except Exception:
                continue
            for class_name in classes:
                cls = getattr(module, class_name, None)
                if cls is None:
                    continue
                try:
                    if class_name == "Paper2LabService":
                        # A Windows shortcut may start in a read-only install
                        # directory.  Keep the default project in a writable
                        # per-user location unless the caller overrides it.
                        workspace = os.environ.get("PAPER2LAB_WORKSPACE")
                        if not workspace:
                            base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home() / ".local" / "share")
                            workspace = str(Path(base) / "Paper2Lab")
                        candidate = cls(workspace)
                    else:
                        candidate = cls()
                    # A storage class may share a name with a service but not
                    # provide the UI workflow.  Do not select a partial
                    # object and fail later when a button is clicked.
                    required = ("list_papers", "get_paper", "import_paper", "create_blueprint")
                    if all(callable(getattr(candidate, name, None)) for name in required):
                        return candidate
                except Exception:
                    continue
        return None

    def __getattr__(self, name: str) -> Any:
        return getattr(self.engine, name)

    def pause_run(self, run_id: str) -> bool:
        method = getattr(self.engine, "pause_run", None)
        if callable(method):
            return bool(method(run_id))
        runner = getattr(self.engine, "runner", None) or getattr(self.engine, "experiment_runner", None)
        method = getattr(runner, "pause", None)
        return bool(method(run_id)) if callable(method) else False

    def cancel_run(self, run_id: str) -> bool:
        method = getattr(self.engine, "cancel_run", None)
        if callable(method):
            return bool(method(run_id))
        runner = getattr(self.engine, "runner", None) or getattr(self.engine, "experiment_runner", None)
        method = getattr(runner, "cancel", None)
        return bool(method(run_id)) if callable(method) else False

    def resume_run(self, run_id: str) -> Any:
        """Resume through either a service method or a runner fallback."""
        method = getattr(self.engine, "resume_run", None)
        if callable(method):
            return bool(method(run_id))
        runner = getattr(self.engine, "runner", None) or getattr(self.engine, "experiment_runner", None)
        method = getattr(runner, "resume", None)
        return bool(method(run_id)) if callable(method) else False

    def retry_run(self, run_id: str) -> Any:
        method = getattr(self.engine, "retry_run", None)
        if callable(method):
            return method(run_id)
        # The canonical service keeps completed runs on their paper.  Locate
        # the owning blueprint and ask the service for a fresh mock run.
        list_papers = getattr(self.engine, "list_papers", None)
        run_experiment = getattr(self.engine, "run_experiment", None)
        if callable(list_papers) and callable(run_experiment):
            for paper in list_papers():
                for run in _items(paper, "runs"):
                    if _id_of(run, "id", "run") == str(run_id):
                        return run_experiment(_id_of(paper, "id", "paper"), _field(run, "blueprint_id", default=""), seed=_field(run, "seed", default=42) or 42)
        return None

    @property
    def is_compat(self) -> bool:
        return isinstance(self.engine, CompatBackend)


class Paper2LabApp:
    """Main three-column Paper2Lab workbench."""

    def __init__(self, root: Any, backend: BackendAdapter | Any | None = None) -> None:
        if tk is None or ttk is None:  # pragma: no cover
            raise RuntimeError("Tkinter is not available in this Python installation.")
        self.root = root
        self.backend = backend if isinstance(backend, BackendAdapter) else BackendAdapter(backend)
        self.selected_paper_id: str | None = None
        self.selected_claim_id: str | None = None
        self.selected_blueprint_id: str | None = None
        self.selected_run_id: str | None = None
        self._build_variables()
        self._build_menu()
        self._build_layout()
        self._refresh_all()
        self._drain_backend_logs()

    def _build_variables(self) -> None:
        mode = "离线兼容模式 / Offline compatibility" if self.backend.is_compat else "本地引擎 / Local engine"
        self.status_var = tk.StringVar(value=f"就绪 / Ready · {mode}")
        self.paper_filter_var = tk.StringVar()
        self.metadata_var = tk.StringVar(value="未选择论文 / No paper selected")

    def _build_menu(self) -> None:
        menu = tk.Menu(self.root)
        file_menu = tk.Menu(menu, tearoff=False)
        file_menu.add_command(label="导入论文… / Import", command=self.import_paper)
        file_menu.add_command(label="打开项目… / Open Project", command=self.open_project)
        file_menu.add_command(label="保存项目… / Save Project", command=self.save_project)
        file_menu.add_separator()
        file_menu.add_command(label="退出 / Exit", command=self.root.destroy)
        menu.add_cascade(label="文件 / File", menu=file_menu)
        tools_menu = tk.Menu(menu, tearoff=False)
        tools_menu.add_command(label="创建 Sample Paper", command=self.create_sample)
        tools_menu.add_command(label="运行完整 Demo 流程", command=self.run_demo_flow)
        menu.add_cascade(label="工具 / Tools", menu=tools_menu)
        self.root.config(menu=menu)

    def _build_layout(self) -> None:
        self.root.title(APP_TITLE)
        self.root.minsize(1100, 700)
        self.root.geometry("1440x900")
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        outer = ttk.Frame(self.root, padding=8)
        outer.pack(fill="both", expand=True)
        toolbar = ttk.Frame(outer)
        toolbar.pack(fill="x", pady=(0, 6))
        ttk.Label(toolbar, text="Paper2Lab", font=("Segoe UI", 16, "bold")).pack(side="left")
        ttk.Label(toolbar, text="论文 → 实验工程工作台", foreground="#667085").pack(side="left", padx=(12, 0))
        ttk.Button(toolbar, text="导入论文", command=self.import_paper).pack(side="right", padx=2)
        ttk.Button(toolbar, text="Sample Paper", command=self.create_sample).pack(side="right", padx=2)
        ttk.Button(toolbar, text="保存", command=self.save_project).pack(side="right", padx=2)

        body = ttk.PanedWindow(outer, orient="horizontal")
        body.pack(fill="both", expand=True)
        self.left = ttk.Frame(body, padding=(0, 0, 6, 0), width=250)
        self.center = ttk.Frame(body, padding=(6, 0, 6, 0), width=650)
        self.right = ttk.Frame(body, padding=(6, 0, 0, 0), width=330)
        body.add(self.left, weight=1)
        body.add(self.center, weight=3)
        body.add(self.right, weight=1)
        self._build_left()
        self._build_center()
        self._build_right()

        bottom = ttk.LabelFrame(outer, text="Runs / Logs", padding=4)
        bottom.pack(fill="both", expand=False, pady=(6, 0))
        bottom.rowconfigure(0, weight=1)
        bottom.columnconfigure(0, weight=1)
        self.bottom_notebook = ttk.Notebook(bottom, height=150)
        self.bottom_notebook.grid(row=0, column=0, sticky="nsew")
        runs_frame = ttk.Frame(self.bottom_notebook)
        logs_frame = ttk.Frame(self.bottom_notebook)
        self.bottom_notebook.add(runs_frame, text="Runs")
        self.bottom_notebook.add(logs_frame, text="Logs")
        self.runs_tree = self._tree(runs_frame, ("id", "blueprint", "status", "runtime", "result"), (130, 140, 110, 90, 420))
        self.runs_tree.pack(fill="both", expand=True)
        self.runs_tree.bind("<<TreeviewSelect>>", self._on_run_select)
        self.log_text = self._text_widget(
            logs_frame,
            height=6,
            wrap="word",
            state="disabled",
            background="#111827",
            foreground="#d1fae5",
        )

        ttk.Label(outer, textvariable=self.status_var, anchor="w").pack(fill="x", pady=(4, 0))

    def _build_left(self) -> None:
        ttk.Label(self.left, text="Paper Library", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        filter_row = ttk.Frame(self.left)
        filter_row.pack(fill="x", pady=5)
        ttk.Entry(filter_row, textvariable=self.paper_filter_var).pack(side="left", fill="x", expand=True)
        ttk.Button(filter_row, text="筛选", width=5, command=self.refresh_papers).pack(side="left", padx=(4, 0))
        paper_list_frame = ttk.Frame(self.left)
        paper_list_frame.pack(fill="both", expand=True)
        self.paper_list = tk.Listbox(paper_list_frame, exportselection=False, activestyle="dotbox")
        paper_scroll = ttk.Scrollbar(paper_list_frame, orient="vertical", command=self.paper_list.yview)
        self.paper_list.configure(yscrollcommand=paper_scroll.set)
        self.paper_list.pack(side="left", fill="both", expand=True)
        paper_scroll.pack(side="right", fill="y")
        self.paper_list.bind("<<ListboxSelect>>", self._on_paper_select)
        actions = ttk.Frame(self.left)
        actions.pack(fill="x", pady=(6, 0))
        for text, command in (
            ("导入", self.import_paper),
            ("解析章节", self.parse_structure),
            ("提取 Claim", self.extract_claims),
            ("创建 Blueprint", self.create_blueprint),
            ("生成骨架", self.generate_skeleton),
            ("运行 Mock", self.run_mock),
            ("运行骨架", self.run_generated),
            ("暂停 Run", self.pause_run),
            ("恢复 Run", self.resume_run),
            ("取消 Run", self.cancel_run),
            ("重试 Run", self.retry_run),
            ("比较结果", self.compare_results),
            ("生成报告", self.generate_report),
        ):
            ttk.Button(actions, text=text, command=command).pack(fill="x", pady=1)

    def _build_center(self) -> None:
        self.center_header = ttk.Label(self.center, text="Reader / Experiment", font=("Segoe UI", 11, "bold"))
        self.center_header.pack(anchor="w")
        self.center_notebook = ttk.Notebook(self.center)
        self.center_notebook.pack(fill="both", expand=True, pady=(5, 0))
        reader_frame = ttk.Frame(self.center_notebook)
        structure_frame = ttk.Frame(self.center_notebook)
        experiment_frame = ttk.Frame(self.center_notebook)
        formula_frame = ttk.Frame(self.center_notebook)
        self.center_notebook.add(reader_frame, text="Reader")
        self.center_notebook.add(structure_frame, text="Structure")
        self.center_notebook.add(experiment_frame, text="Experiment")
        self.center_notebook.add(formula_frame, text="Formula / Algorithm")
        self.reader_text = self._text_widget(reader_frame, wrap="word", state="disabled", font=("Consolas", 10))
        self.structure_tree = self._tree(structure_frame, ("section", "chars"), (300, 100))
        self.structure_tree.pack(fill="both", expand=True)
        self.structure_tree.bind("<Double-1>", self._edit_section_name)
        exp_toolbar = ttk.Frame(experiment_frame)
        exp_toolbar.pack(fill="x", pady=(0, 5))
        ttk.Button(exp_toolbar, text="生成骨架", command=self.generate_skeleton).pack(side="left")
        ttk.Button(exp_toolbar, text="运行 Mock", command=self.run_mock).pack(side="left", padx=4)
        ttk.Button(exp_toolbar, text="运行骨架", command=self.run_generated).pack(side="left", padx=4)
        ttk.Button(exp_toolbar, text="比较", command=self.compare_results).pack(side="left")
        ttk.Button(exp_toolbar, text="报告", command=self.generate_report).pack(side="left", padx=4)
        self.experiment_text = self._text_widget(experiment_frame, wrap="word", state="disabled", font=("Consolas", 10))
        self.formula_text = self._text_widget(formula_frame, wrap="word", font=("Consolas", 10))
        self.formula_text.insert("1.0", "Formula / pseudocode notes are kept with the paper.\nSelect a paper to inspect or edit notes.")
        self.formula_text.bind("<FocusOut>", self._save_formula_notes)

    def _build_right(self) -> None:
        ttk.Label(self.right, text="Claims / Notes / Metadata", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        self.right_notebook = ttk.Notebook(self.right)
        self.right_notebook.pack(fill="both", expand=True, pady=(5, 0))
        claims_frame = ttk.Frame(self.right_notebook)
        notes_frame = ttk.Frame(self.right_notebook)
        metadata_frame = ttk.Frame(self.right_notebook)
        graph_frame = ttk.Frame(self.right_notebook)
        self.right_notebook.add(claims_frame, text="Claims")
        self.right_notebook.add(notes_frame, text="Notes")
        self.right_notebook.add(metadata_frame, text="Metadata")
        self.right_notebook.add(graph_frame, text="Research Graph")
        self.claims_tree = self._tree(claims_frame, ("kind", "claim"), (120, 320))
        self.claims_tree.pack(fill="both", expand=True)
        self.claims_tree.bind("<<TreeviewSelect>>", self._on_claim_select)
        ttk.Button(claims_frame, text="以选中 Claim 建立 Blueprint", command=self.create_blueprint).pack(fill="x", pady=(5, 0))
        self.notes_text = self._text_widget(notes_frame, wrap="word")
        self.notes_text.bind("<FocusOut>", self._save_notes)
        metadata_toolbar = ttk.Frame(metadata_frame)
        metadata_toolbar.pack(fill="x", pady=(0, 4))
        ttk.Label(
            metadata_toolbar,
            text="可编辑 JSON；离开编辑框或点击保存后写入项目 / Editable metadata",
            foreground="#667085",
        ).pack(side="left")
        ttk.Button(metadata_toolbar, text="保存 Metadata", command=self._save_metadata).pack(side="right")
        self.metadata_text = self._text_widget(metadata_frame, wrap="word")
        self.metadata_text.bind("<FocusOut>", self._save_metadata)
        self.graph_tree = self._tree(graph_frame, ("node", "relation", "target"), (130, 110, 220))
        self.graph_tree.pack(fill="both", expand=True)

    @staticmethod
    def _tree(parent: Any, columns: tuple[str, ...], widths: tuple[int, ...]) -> Any:
        tree = ttk.Treeview(parent, columns=columns, show="headings", selectmode="browse")
        for name, width in zip(columns, widths):
            tree.heading(name, text=name.title())
            tree.column(name, width=width, anchor="w", stretch=True)
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        return tree

    @staticmethod
    def _text_widget(parent: Any, **kwargs: Any) -> Any:
        """Create a Text pane with a vertical scrollbar for long papers/logs."""
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True)
        text = tk.Text(frame, **kwargs)
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scrollbar.set)
        text.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        return text

    def _call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        fn = getattr(self.backend, method, None)
        if fn is None:
            raise RuntimeError(f"Backend does not implement {method}().")
        return fn(*args, **kwargs)

    def _selected_paper(self) -> Any | None:
        if self.selected_paper_id:
            return self.backend.get_paper(self.selected_paper_id)
        return None

    def _refresh_all(self) -> None:
        self.refresh_papers()
        self._refresh_runs()
        self._refresh_logs()

    def refresh_papers(self) -> None:
        query = self.paper_filter_var.get().lower().strip()
        self.paper_list.delete(0, tk.END)
        self._paper_rows: list[Any] = []
        for paper in self._call("list_papers"):
            searchable = " ".join(
                str(_field(paper, field, default=""))
                for field in ("title", "local_path", "text", "raw_text", "authors", "abstract", "notes", "tags", "keywords")
            ).lower()
            if query and query not in searchable:
                continue
            self._paper_rows.append(paper)
            self.paper_list.insert(tk.END, f"{_field(paper, 'title', default='Untitled')}  [{_id_of(paper, 'id', 'paper')}]")
        if self._paper_rows:
            index = next((i for i, p in enumerate(self._paper_rows) if _id_of(p, "id", "paper") == self.selected_paper_id), 0)
            self.paper_list.selection_set(index)
            self.paper_list.event_generate("<<ListboxSelect>>")

    def _on_paper_select(self, _event: Any = None) -> None:
        selection = self.paper_list.curselection()
        if not selection:
            return
        paper = self._paper_rows[selection[0]]
        self.selected_paper_id = _id_of(paper, "id", "paper")
        self.selected_claim_id = None
        blueprints = _items(paper, "blueprints")
        self.selected_blueprint_id = _id_of(blueprints[-1], "id", "blueprint") if blueprints else None
        self._render_paper(paper)
        self.status_var.set(f"已选择：{_field(paper, 'title', default='Untitled')}")

    def _render_paper(self, paper: Any) -> None:
        self._set_text(self.reader_text, _field(paper, "text", "raw_text", default=""))
        self._set_text(self.notes_text, _field(paper, "notes", default=""))
        sections = _items(paper, "sections")
        claims = _items(paper, "claims")
        blueprints = _items(paper, "blueprints")
        metadata = {
            "Title": _field(paper, "title", default=""),
            "Authors": _field(paper, "authors", default=[]),
            "Year": _field(paper, "year", default=""),
            "Venue": _field(paper, "venue", default=""),
            "Abstract": _field(paper, "abstract", default=""),
            "Keywords": _field(paper, "keywords", default=[]),
            "Notes": _field(paper, "notes", default=""),
            "Tags": _field(paper, "tags", default=[]),
            "Local Path": _field(paper, "local_path", default=""),
            "Sections": len(sections),
            "Claims": len(claims),
            "Blueprints": len(blueprints),
        }
        # Surface the evidence-only reproducibility checklist in the same
        # metadata pane when the canonical service provides it.  The score is
        # deliberately read-only here; users can add explicit evidence through
        # the service API without the UI ever pretending to judge truth.
        scorer = getattr(self.backend, "reproducibility_score", None)
        if callable(scorer):
            try:
                assessment = scorer(_id_of(paper, "id", "paper"))
                if hasattr(assessment, "to_dict"):
                    assessment = assessment.to_dict()
                elif not isinstance(assessment, Mapping):
                    assessment = {"score": _field(assessment, "score", default="")}
                metadata["Reproducibility"] = assessment
            except Exception as exc:
                self._append_log(f"Reproducibility score unavailable: {exc}")
        self._set_text(self.metadata_text, json.dumps(metadata, ensure_ascii=False, indent=2))
        formulas = _items(paper, "formulas", "formula_notes")
        formula_payload = formulas if formulas else [{"kind": "TODO", "content": "No formula/pseudocode notes captured yet.", "explanation": "Add and verify notes against the source paper."}]
        self._set_text(self.formula_text, json.dumps(formula_payload, ensure_ascii=False, indent=2, default=_json_default))
        self.structure_tree.delete(*self.structure_tree.get_children())
        for section in sections:
            section_id = _id_of(section, "id", "section") or _uid("section")
            section_name = _field(section, "name", "title", default="Section")
            section_text = _field(section, "text", "content", default="")
            self.structure_tree.insert("", "end", iid=section_id, values=(section_name, len(section_text)))
        self.claims_tree.delete(*self.claims_tree.get_children())
        for claim in claims:
            claim_id = _id_of(claim, "id", "claim") or _uid("claim")
            claim_kind = _field(claim, "kind", "claim_type", default="Claim")
            if hasattr(claim_kind, "value"):
                claim_kind = claim_kind.value
            claim_text = _field(claim, "claim_text", "text", default="")
            self.claims_tree.insert("", "end", iid=claim_id, values=(claim_kind, str(claim_text)[:180]))
        self.graph_tree.delete(*self.graph_tree.get_children())
        graph_method = getattr(self.backend, "research_graph", None)
        graph = None
        if callable(graph_method):
            try:
                graph = graph_method(_id_of(paper, "id", "paper"))
            except Exception as exc:
                self._append_log(f"Research graph fallback: {exc}")
        if isinstance(graph, Mapping) and isinstance(graph.get("nodes"), list) and isinstance(graph.get("edges"), list):
            node_by_id = {str(node.get("id")): node for node in graph["nodes"] if isinstance(node, Mapping)}
            for edge in graph["edges"]:
                if not isinstance(edge, Mapping):
                    continue
                source = node_by_id.get(str(edge.get("source")), {})
                target = node_by_id.get(str(edge.get("target")), {})
                source_text = f"{source.get('type', 'Node')}: {source.get('label', edge.get('source', ''))}"
                target_text = f"{target.get('type', 'Node')}: {target.get('label', edge.get('target', ''))}"
                self.graph_tree.insert(
                    "", "end", values=(source_text[:150], str(edge.get("relation", "relates")), target_text[:220])
                )
        else:
            # Older adapters do not expose a graph API; retain a transparent
            # table representation instead of hiding the relationships.
            paper_title = str(_field(paper, "title", default=""))
            self.graph_tree.insert("", "end", values=("Paper", "contains", paper_title[:120]))
            for claim in claims:
                claim_id = _id_of(claim, "id", "claim")
                claim_text = _field(claim, "claim_text", "text", default="")
                self.graph_tree.insert("", "end", values=(f"Claim {str(claim_id)[:12]}", "supports", str(claim_text)[:120]))
            for blueprint in blueprints:
                bp_id = _id_of(blueprint, "id", "blueprint")
                claim_id = _field(blueprint, "claim_id", default="")
                self.graph_tree.insert("", "end", values=(f"Blueprint {str(bp_id)[:12]}", "tests", f"Claim {str(claim_id)[:12]}"))
        exp = blueprints[-1] if blueprints else None
        self._set_text(self.experiment_text, json.dumps(exp, ensure_ascii=False, indent=2, default=_json_default) if exp else "尚未创建 Experiment Blueprint。\n请选择 Claim 后点击创建。")

    def _set_text(self, widget: Any, value: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", tk.END)
        widget.insert("1.0", value or "")
        # Reader/metadata/experiment panes are read-only, while Notes and the
        # Formula / Algorithm notebook are deliberately editable.  Keeping
        # the latter enabled is important: selecting a paper must not turn a
        # user's formula explanation into a view-only snapshot.
        editable = any(widget is candidate for candidate in (self.notes_text, self.formula_text, self.metadata_text))
        widget.configure(state="normal" if editable else "disabled")

    def _on_claim_select(self, _event: Any = None) -> None:
        selection = self.claims_tree.selection()
        self.selected_claim_id = selection[0] if selection else None

    def _edit_section_name(self, _event: Any = None) -> None:
        paper = self._selected_paper()
        selected = self.structure_tree.selection()
        if paper is None or not selected or simpledialog is None:
            self.status_var.set("请先选择要修正的章节。")
            return
        section_id = selected[0]
        current = self.structure_tree.item(section_id, "values")
        old_title = current[0] if current else ""
        new_title = simpledialog.askstring("Paper2Lab", "修正章节名称 / Section title:", initialvalue=old_title, parent=self.root)
        if new_title and new_title.strip() and new_title.strip() != old_title:
            self._action("修正章节", lambda: self._call("rename_section", _id_of(paper, "id", "paper"), section_id, new_title.strip()))

    def _save_notes(self, _event: Any = None) -> None:
        paper = self._selected_paper()
        if paper is not None:
            notes = self.notes_text.get("1.0", tk.END).strip()
            try:
                self._call("update_metadata", _id_of(paper, "id", "paper"), notes=notes)
            except (AttributeError, RuntimeError):
                # Older third-party adapters may expose only a mutable paper;
                # preserve the edit rather than making the UI unusable.
                if hasattr(paper, "notes"):
                    paper.notes = notes
                elif getattr(paper, "metadata", None) is not None:
                    paper.metadata.notes = notes
                if hasattr(paper, "updated_at"):
                    paper.updated_at = _now()

    def _save_metadata(self, _event: Any = None) -> None:
        """Validate and persist the editable Metadata pane.

        The pane intentionally uses a small JSON object instead of a large
        form: it keeps all required Paper Library fields visible, works for
        both the canonical service and the compatibility backend, and remains
        inspectable in a saved project.  Derived counts are ignored on write.
        """
        paper = self._selected_paper()
        if paper is None or not hasattr(self, "metadata_text"):
            return
        raw = self.metadata_text.get("1.0", tk.END).strip()
        if not raw:
            return
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, TypeError) as exc:
            self._append_log(f"Metadata not saved (invalid JSON): {exc}")
            return
        if not isinstance(payload, Mapping):
            self._append_log("Metadata not saved: expected a JSON object.")
            return
        keys = {
            "Title": "title",
            "Authors": "authors",
            "Year": "year",
            "Venue": "venue",
            "Abstract": "abstract",
            "Keywords": "keywords",
            "Notes": "notes",
            "Tags": "tags",
            "Local Path": "local_path",
        }
        changes = {keys[key]: value for key, value in payload.items() if key in keys}
        if not changes:
            return
        try:
            self._call("update_metadata", _id_of(paper, "id", "paper"), **changes)
        except Exception as exc:
            self._append_log(f"Metadata not saved: {exc}")
            return
        self._append_log("Metadata saved.")

    def _save_formula_notes(self, _event: Any = None) -> None:
        paper = self._selected_paper()
        if paper is None:
            return
        raw = self.formula_text.get("1.0", tk.END).strip()
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                if hasattr(paper, "formulas"):
                    paper.formulas = parsed
                elif hasattr(paper, "formula_notes"):
                    paper.formula_notes = parsed
                paper.updated_at = _now()
                # The canonical store writes atomically; use it when the
                # selected backend exposes one so a close/reopen round-trip
                # does not depend on the user remembering the toolbar Save.
                engine = getattr(self.backend, "engine", self.backend)
                store = getattr(engine, "store", None)
                updater = getattr(store, "update_paper", None)
                if callable(updater):
                    updater(paper)
        except (json.JSONDecodeError, TypeError):
            # Free-form notes are intentionally tolerated; they are not thrown
            # away merely because they are not JSON yet.
            pass

    def _action(self, label: str, fn: Callable[[], Any]) -> Any:
        try:
            result = fn()
            self._refresh_all()
            self.status_var.set(f"完成：{label}")
            return result
        except Exception as exc:
            self.status_var.set(f"失败：{label} — {exc}")
            self._append_log(f"ERROR {label}: {exc}")
            try:
                messagebox.showerror("Paper2Lab", f"{label}失败：\n{exc}")
            except Exception:
                pass
            return None

    def import_paper(self) -> Any:
        if filedialog is None:
            return None
        path = filedialog.askopenfilename(
            title="导入论文 / Import paper",
            filetypes=[("Paper files", "*.pdf *.md *.markdown *.txt *.html *.htm"), ("All files", "*.*")],
        )
        if not path:
            return None
        return self._action("导入论文", lambda: self._import_and_select(path))

    def _import_and_select(self, path: str) -> Any:
        paper = self._call("import_paper", path)
        self.selected_paper_id = _id_of(paper, "id", "paper")
        return paper

    def create_sample(self) -> Any:
        return self._action("创建 Sample Paper", self._create_sample_and_select)

    def _create_sample_and_select(self) -> Any:
        paper = self._call("create_sample_paper")
        self.selected_paper_id = _id_of(paper, "id", "paper")
        return paper

    def parse_structure(self) -> Any:
        paper = self._selected_paper()
        if paper is None:
            return self._action("解析章节", lambda: (_ for _ in ()).throw(ValueError("请先选择论文。")))
        paper_id = _id_of(paper, "id", "paper")

        def parse() -> Any:
            # Preserve a researcher's manual title/body corrections when the
            # canonical parser supports that opt-in.  The compatibility
            # backend predates the keyword, so retain its plain call as a
            # narrow fallback rather than making the UI backend-specific.
            try:
                return self._call("parse_structure", paper_id, preserve_manual=True)
            except TypeError as exc:
                if "preserve_manual" not in str(exc):
                    raise
                return self._call("parse_structure", paper_id)

        return self._action("解析章节", parse)

    def extract_claims(self) -> Any:
        paper = self._selected_paper()
        if paper is None:
            return self._action("提取 Claim", lambda: (_ for _ in ()).throw(ValueError("请先选择论文。")))
        paper_id = _id_of(paper, "id", "paper")
        return self._action("提取 Claim", lambda: self._call("extract_claims", paper_id))

    def create_blueprint(self) -> Any:
        paper = self._selected_paper()
        if paper is None:
            return self._action("创建 Blueprint", lambda: (_ for _ in ()).throw(ValueError("请先选择论文。")))
        return self._action("创建 Blueprint", lambda: self._create_blueprint_for(paper))

    def _create_blueprint_for(self, paper: Any) -> Any:
        paper_id = _id_of(paper, "id", "paper")
        blueprint = self._call("create_blueprint", paper_id, self.selected_claim_id)
        self.selected_blueprint_id = _id_of(blueprint, "id", "blueprint")
        return blueprint

    def generate_skeleton(self) -> Any:
        paper = self._selected_paper()
        if paper is None:
            return self._action("生成骨架", lambda: (_ for _ in ()).throw(ValueError("请先选择论文。")))
        blueprints = _items(paper, "blueprints")
        blueprint_id = self.selected_blueprint_id or (_id_of(blueprints[-1], "id", "blueprint") if blueprints else None)
        paper_id = _id_of(paper, "id", "paper")
        return self._action("生成骨架", lambda: self._call("generate_skeleton", paper_id, blueprint_id))

    def run_mock(self) -> Any:
        paper = self._selected_paper()
        if paper is None:
            return self._action("运行 Mock", lambda: (_ for _ in ()).throw(ValueError("请先选择论文。")))
        blueprints = _items(paper, "blueprints")
        blueprint_id = self.selected_blueprint_id or (_id_of(blueprints[-1], "id", "blueprint") if blueprints else None)
        paper_id = _id_of(paper, "id", "paper")

        def run() -> Any:
            record = self._call("run_experiment", paper_id, blueprint_id)
            self.selected_run_id = _id_of(record, "id", "run") or str(_field(record, "run_id", default=""))
            return record

        return self._action("运行 Mock", run)

    def run_generated(self) -> Any:
        """Start the generated local skeleton through ExperimentRunner.

        The canonical service returns immediately with a queued/running
        record; the small polling loop below keeps the Runs panel current
        without freezing Tkinter.  Compatibility backends that predate the
        process runner fall back to their deterministic Mock path.
        """
        paper = self._selected_paper()
        if paper is None:
            return self._action("运行骨架", lambda: (_ for _ in ()).throw(ValueError("请先选择论文。")))
        blueprints = _items(paper, "blueprints")
        blueprint_id = self.selected_blueprint_id or (_id_of(blueprints[-1], "id", "blueprint") if blueprints else None)
        paper_id = _id_of(paper, "id", "paper")
        starter = getattr(self.backend, "start_experiment", None)
        if not callable(starter):
            return self._action("运行骨架", lambda: self._call("run_experiment", paper_id, blueprint_id))

        def start() -> Any:
            record = starter(paper_id, blueprint_id)
            self.selected_run_id = _id_of(record, "id", "run")
            return record

        record = self._action("运行骨架", start)
        run_id = _id_of(record, "id", "run") if record is not None else self.selected_run_id
        if run_id and callable(getattr(self.backend, "wait_experiment", None)):
            self.selected_run_id = run_id
            self._poll_generated_run(run_id)
        return record

    def _poll_generated_run(self, run_id: str) -> None:
        if not getattr(self.root, "winfo_exists", lambda: True)():
            return
        try:
            try:
                current = self.backend.wait_experiment(run_id, timeout=0)
            except TypeError as exc:
                # A small third-party adapter may expose ``wait_run(id)``
                # without a timeout keyword.  Keep polling non-blocking when
                # possible, but do not make that adapter unusable.
                if "timeout" not in str(exc):
                    raise
                current = self.backend.wait_experiment(run_id)
        except Exception as exc:
            self._append_log(f"Run polling failed: {exc}")
            return
        self._refresh_runs()
        if hasattr(self, "log_text"):
            self._refresh_logs()
        if current is None:
            self.root.after(250, lambda: self._poll_generated_run(run_id))
            return
        status = _field(current, "status", default="")
        status = getattr(status, "value", status)
        if str(status).casefold() in {"queued", "running", "paused"}:
            self.root.after(250, lambda: self._poll_generated_run(run_id))
        else:
            self.status_var.set(f"运行完成：{run_id}")

    def _on_run_select(self, _event: Any = None) -> None:
        selection = self.runs_tree.selection()
        self.selected_run_id = selection[0] if selection else None
        if hasattr(self, "log_text"):
            self._refresh_logs()

    def pause_run(self) -> Any:
        run_id = self.selected_run_id
        if not run_id:
            return self._action("暂停 Run", lambda: (_ for _ in ()).throw(ValueError("请先在底部 Runs 中选择运行记录。")))
        return self._action("暂停 Run", lambda: self._call("pause_run", run_id))

    def resume_run(self) -> Any:
        run_id = self.selected_run_id
        if not run_id:
            return self._action("恢复 Run", lambda: (_ for _ in ()).throw(ValueError("请先在底部 Runs 中选择运行记录。")))
        return self._action("恢复 Run", lambda: self._call("resume_run", run_id))

    def cancel_run(self) -> Any:
        run_id = self.selected_run_id
        if not run_id:
            return self._action("取消 Run", lambda: (_ for _ in ()).throw(ValueError("请先在底部 Runs 中选择运行记录。")))
        return self._action("取消 Run", lambda: self._call("cancel_run", run_id))

    def retry_run(self) -> Any:
        run_id = self.selected_run_id
        if not run_id:
            return self._action("重试 Run", lambda: (_ for _ in ()).throw(ValueError("请先在底部 Runs 中选择运行记录。")))

        def retry() -> Any:
            record = self._call("retry_run", run_id)
            if record is not None:
                self.selected_run_id = _id_of(record, "id", "run") or str(_field(record, "run_id", default=""))
            return record

        return self._action("重试 Run", retry)

    def compare_results(self) -> Any:
        paper = self._selected_paper()
        if paper is None:
            return self._action("比较结果", lambda: (_ for _ in ()).throw(ValueError("请先选择论文。")))
        blueprints = _items(paper, "blueprints")
        blueprint_id = self.selected_blueprint_id or (_id_of(blueprints[-1], "id", "blueprint") if blueprints else None)
        paper_id = _id_of(paper, "id", "paper")
        return self._action("比较结果", lambda: self._call("compare_results", paper_id, blueprint_id))

    def generate_report(self) -> Any:
        paper = self._selected_paper()
        if paper is None:
            return self._action("生成报告", lambda: (_ for _ in ()).throw(ValueError("请先选择论文。")))
        blueprints = _items(paper, "blueprints")
        blueprint_id = self.selected_blueprint_id or (_id_of(blueprints[-1], "id", "blueprint") if blueprints else None)
        paper_id = _id_of(paper, "id", "paper")
        return self._action("生成报告", lambda: self._call("generate_report", paper_id, blueprint_id))

    def save_project(self) -> Any:
        if filedialog is None:
            return None
        path = filedialog.asksaveasfilename(title="保存 Paper2Lab 项目", defaultextension=".paper2lab.json", filetypes=[("Paper2Lab project", "*.paper2lab.json"), ("JSON", "*.json")])
        if not path:
            return None
        return self._action("保存项目", lambda: self._call("save_project", path))

    def open_project(self) -> Any:
        if filedialog is None:
            return None
        path = filedialog.askopenfilename(title="打开 Paper2Lab 项目", filetypes=[("Paper2Lab project", "*.paper2lab.json *.json"), ("JSON", "*.json")])
        if not path:
            return None
        return self._action("打开项目", lambda: self._open_and_select(path))

    def _open_and_select(self, path: str) -> Any:
        self._call("load_project", path)
        papers = self._call("list_papers")
        self.selected_paper_id = _id_of(papers[0], "id", "paper") if papers else None
        return papers

    def run_demo_flow(self) -> Any:
        def flow() -> str:
            paper = self._call("create_sample_paper")
            paper_id = _id_of(paper, "id", "paper")
            self.selected_paper_id = paper_id
            self._call("parse_structure", paper_id)
            claims = self._call("extract_claims", paper_id)
            claim_id = _id_of(claims[0], "id", "claim") if claims else None
            blueprint = self._call("create_blueprint", paper_id, claim_id)
            blueprint_id = _id_of(blueprint, "id", "blueprint")
            self.selected_blueprint_id = blueprint_id
            self._call("generate_skeleton", paper_id, blueprint_id)
            self._call("run_experiment", paper_id, blueprint_id)
            self._call("compare_results", paper_id, blueprint_id)
            report = self._call("generate_report", paper_id, blueprint_id)
            return report

        return self._action("完整 Demo 流程", flow)

    def _refresh_runs(self) -> None:
        self.runs_tree.delete(*self.runs_tree.get_children())
        # The canonical service restores RunRecord objects inside each paper
        # when it opens its persistent ProjectStore, while its convenience
        # ``runs`` index is intentionally rebuilt only as new runs are
        # started.  Prefer the stable list API so a close/reopen immediately
        # repopulates the Runs panel instead of appearing empty.  Keep the
        # mapping/list fallback for the lightweight compatibility backend and
        # older adapters.
        list_runs = getattr(self.backend, "list_runs", None)
        if callable(list_runs):
            try:
                values = list(list_runs())
            except Exception as exc:
                if hasattr(self, "log_text"):
                    self._append_log(f"Run list fallback: {exc}")
                runs = getattr(self.backend, "runs", {})
                values = list(runs.values()) if isinstance(runs, Mapping) else list(runs or [])
        else:
            runs = getattr(self.backend, "runs", {})
            values = list(runs.values()) if isinstance(runs, Mapping) else list(runs or [])
        for run in values:
            result = _field(run, "result", default={})
            run_id = _id_of(run, "id", "run") or str(_field(run, "run_id", default=""))
            status = _field(run, "status", default="")
            if hasattr(status, "value"):
                status = status.value
            blueprint_id = _field(run, "blueprint_id", default="")
            runtime = _field(run, "runtime_seconds", default="")
            item = self.runs_tree.insert("", "end", iid=str(run_id), values=(run_id, blueprint_id, status, runtime, json.dumps(result, ensure_ascii=False, default=_json_default)))
            if run_id == self.selected_run_id:
                self.runs_tree.selection_set(item)

    def _append_log(self, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert(tk.END, f"[{_now()}] {message}\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state="disabled")

    def _selected_run_record(self) -> Any | None:
        """Return the selected run from canonical or compatibility storage."""
        run_id = self.selected_run_id
        if not run_id:
            return None
        list_runs = getattr(self.backend, "list_runs", None)
        if callable(list_runs):
            try:
                records = list(list_runs())
            except Exception:
                records = []
        else:
            runs = getattr(self.backend, "runs", {})
            records = list(runs.values()) if isinstance(runs, Mapping) else list(runs or [])
        return next(
            (
                run
                for run in records
                if (_id_of(run, "id", "run") or str(_field(run, "run_id", default=""))) == str(run_id)
            ),
            None,
        )

    def _refresh_logs(self) -> None:
        logs = getattr(self.backend, "logs", [])
        # A reopened canonical service restores stdout/stderr on RunRecord but
        # intentionally keeps its transient UI log buffer empty. Surface
        # those persisted streams in the Logs tab so a close/reopen cycle
        # still exposes the audit trail without inventing new log entries.
        if not logs:
            list_runs = getattr(self.backend, "list_runs", None)
            if callable(list_runs):
                try:
                    restored_lines: list[str] = []
                    for run in list_runs():
                        run_id = _id_of(run, "id", "run") or str(_field(run, "run_id", default=""))
                        stdout = str(_field(run, "stdout", default="") or "").strip()
                        stderr = str(_field(run, "stderr", default="") or "").strip()
                        if stdout:
                            restored_lines.append(f"[restored {run_id}] stdout\n{stdout}")
                        if stderr:
                            restored_lines.append(f"[restored {run_id}] stderr\n{stderr}")
                    logs = restored_lines
                except Exception as exc:
                    self._append_log(f"Persisted log restore failed: {exc}")
        selected = self._selected_run_record()
        if selected is not None:
            run_id = _id_of(selected, "id", "run") or str(_field(selected, "run_id", default=""))
            status = _field(selected, "status", default="")
            if hasattr(status, "value"):
                status = status.value
            # Keep the persisted audit payload visible when a researcher
            # selects a row: stdout/stderr are often the fastest way to spot
            # a TODO or environment mismatch, while config/seed/environment
            # make the run reproducible from the desktop itself.
            selected_lines = [
                "",
                f"--- Run {run_id} · {status} ---",
                f"runtime_seconds: {_field(selected, 'runtime_seconds', default='')}",
                f"seed: {_field(selected, 'seed', default='')}",
                "stdout:",
                str(_field(selected, "stdout", default="") or "(empty)"),
                "stderr:",
                str(_field(selected, "stderr", default="") or "(empty)"),
                "result:",
                json.dumps(_field(selected, "result", default={}) or {}, ensure_ascii=False, indent=2, default=_json_default),
                "config:",
                json.dumps(_field(selected, "config", default={}) or {}, ensure_ascii=False, indent=2, default=_json_default),
                "environment:",
                json.dumps(_field(selected, "environment", default={}) or {}, ensure_ascii=False, indent=2, default=_json_default),
            ]
            logs = list(logs or []) + selected_lines
        if logs:
            self.log_text.configure(state="normal")
            self.log_text.delete("1.0", tk.END)
            self.log_text.insert("1.0", "\n".join(logs) + "\n")
            self.log_text.see(tk.END)
            self.log_text.configure(state="disabled")

    def _drain_backend_logs(self) -> None:
        self._refresh_logs()


SAMPLE_PAPER_TEXT = """# A Tiny Retrieval Memory Improves Tool-Use Reliability

## Abstract
We propose a small retrieval memory for tool-using agents. On a fictional task suite, our method improves accuracy while keeping the tool budget fixed.

Keywords: retrieval, memory, tool use

## Introduction
Tool-use agents often repeat failed actions. We show that retrieving a compact record of prior outcomes reduces repeated failures.

## Method
The method stores successful and failed tool traces, then retrieves the nearest trace before planning. The model and implementation are intentionally left as TODOs for this sample.

## Dataset
We use the fictional ToolBench-Mini dataset with 200 synthetic tasks and a fixed seed.

## Experiment
We compare a no-memory baseline with retrieval memory under the same tool budget.

## Metrics
We report task accuracy and repeated-action rate.

## Results
Our method reaches 81.4% accuracy compared with 75.0% for the baseline.

## Limitations
This fictional paper does not establish claims about real-world systems.

## Conclusion
The sample demonstrates a transparent paper-to-experiment workflow.
"""


def launch(backend: Any | None = None) -> Any:
    """Launch the GUI and return the ``Tk`` root after the event loop exits."""

    if tk is None:
        raise RuntimeError("Tkinter is not available.")
    root = tk.Tk()
    Paper2LabApp(root, backend=backend)
    root.mainloop()
    return root


__all__ = [
    "APP_TITLE",
    "BackendAdapter",
    "CompatBackend",
    "Paper2LabApp",
    "PaperRecord",
    "RunRecord",
    "SAMPLE_PAPER_TEXT",
    "launch",
]
