"""Portable JSON persistence for Paper2Lab projects.

The store is intentionally file based: a project can be copied to another
Windows machine, inspected in a text editor, and recovered without a service.
Writes are atomic and the original paper text is retained for re-parsing.
"""

from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Iterable, Iterator, Mapping

from .models import Claim, ExperimentBlueprint, FormulaNote, Paper, RunRecord, model_from_dict, utc_now


SCHEMA_VERSION = "paper2lab.project.v1"


class ProjectStore:
    """Persist papers and their derived experiment records in one project.

    ``root`` may be a directory or a ``.paper2lab.json`` file.  The latter is
    useful for Save/Open dialogs; directories additionally receive an
    ``artifacts`` folder for generated experiment projects and reports.
    """

    def __init__(self, root: str | os.PathLike[str] = "paper2lab_project") -> None:
        requested = Path(root).expanduser()
        if requested.suffix.lower() in {".json", ".paper2lab"} or requested.name.endswith(".paper2lab.json"):
            self.root = requested.parent.resolve()
            self.path = requested.resolve()
        else:
            self.root = requested.resolve()
            self.path = self.root / "paper2lab.project.json"
        self.artifacts_dir = self.root / "artifacts"
        self.papers: dict[str, Paper] = {}
        self.extra: dict[str, Any] = {}
        self.root.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            self.load()

    @property
    def project_path(self) -> Path:
        return self.path

    def load(self, path: str | os.PathLike[str] | None = None) -> "ProjectStore":
        if path is not None:
            requested = Path(path).expanduser().resolve()
            self.path = requested
            self.root = requested.parent
            self.artifacts_dir = self.root / "artifacts"
        if not self.path.exists():
            return self
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        self.papers = {p.paper_id: p for p in (Paper.from_dict(v) for v in payload.get("papers", []))}
        self.extra = dict(payload.get("extra", {}))
        return self

    def save(self, path: str | os.PathLike[str] | None = None) -> Path:
        if path is not None:
            requested = Path(path).expanduser().resolve()
            self.path = requested
            self.root = requested.parent
            self.artifacts_dir = self.root / "artifacts"
        self.root.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": SCHEMA_VERSION,
            "saved_at": utc_now(),
            "papers": [paper.to_dict() for paper in self.papers.values()],
            "extra": self.extra,
        }
        # Atomic replacement avoids half-written projects after a power loss.
        fd, temporary = tempfile.mkstemp(prefix=".paper2lab-", suffix=".tmp", dir=str(self.root))
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return self.path

    def add_paper(self, paper: Paper, persist: bool = True) -> Paper:
        self.papers[paper.paper_id] = paper
        if persist:
            self.save()
        return paper

    save_paper = add_paper

    def update_paper(self, paper: Paper, persist: bool = True) -> Paper:
        return self.add_paper(paper, persist=persist)

    def get_paper(self, paper_id: str) -> Paper | None:
        return self.papers.get(paper_id)

    def require_paper(self, paper_id: str) -> Paper:
        paper = self.get_paper(paper_id)
        if paper is None:
            raise KeyError(f"Unknown paper: {paper_id}")
        return paper

    def list_papers(self) -> list[Paper]:
        return list(self.papers.values())

    def delete_paper(self, paper_id: str, persist: bool = True) -> bool:
        deleted = self.papers.pop(paper_id, None) is not None
        if deleted and persist:
            self.save()
        return deleted

    def search(self, query: str, *, paper_id: str | None = None) -> list[Paper]:
        needle = (query or "").casefold().strip()
        candidates = [self.require_paper(paper_id)] if paper_id else self.papers.values()
        if not needle:
            return list(candidates)
        result: list[Paper] = []
        for paper in candidates:
            haystack = json.dumps(paper.to_dict(), ensure_ascii=False).casefold()
            if needle in haystack:
                result.append(paper)
        return result

    def save_claim(self, claim: Claim, persist: bool = True) -> Claim:
        paper = self.require_paper(claim.paper_id)
        existing = next((i for i, item in enumerate(paper.claims) if item.claim_id == claim.claim_id), None)
        if existing is None:
            paper.claims.append(claim)
        else:
            paper.claims[existing] = claim
        paper.updated_at = utc_now()
        if persist:
            self.save()
        return claim

    def save_blueprint(self, blueprint: ExperimentBlueprint, persist: bool = True) -> ExperimentBlueprint:
        paper = self.require_paper(blueprint.paper_id)
        existing = next((i for i, item in enumerate(paper.blueprints) if item.blueprint_id == blueprint.blueprint_id), None)
        if existing is None:
            paper.blueprints.append(blueprint)
        else:
            paper.blueprints[existing] = blueprint
        paper.updated_at = utc_now()
        if persist:
            self.save()
        return blueprint

    def save_note(self, note: FormulaNote, persist: bool = True) -> FormulaNote:
        paper = self.require_paper(note.paper_id)
        existing = next((i for i, item in enumerate(paper.formula_notes) if item.note_id == note.note_id), None)
        if existing is None:
            paper.formula_notes.append(note)
        else:
            paper.formula_notes[existing] = note
        paper.updated_at = utc_now()
        if persist:
            self.save()
        return note

    def save_run(self, run: RunRecord, paper_id: str, persist: bool = True) -> RunRecord:
        paper = self.require_paper(paper_id)
        existing = next((i for i, item in enumerate(paper.runs) if item.run_id == run.run_id), None)
        if existing is None:
            paper.runs.append(run)
        else:
            paper.runs[existing] = run
        paper.updated_at = utc_now()
        if persist:
            self.save()
        return run

    def paper_content_hash(self, paper: Paper) -> str:
        return sha256(paper.raw_text.encode("utf-8")).hexdigest()

    def artifact_path(self, *parts: str) -> Path:
        base = self.artifacts_dir.resolve()
        target = base.joinpath(*parts).resolve()
        try:
            target.relative_to(base)
        except ValueError as exc:
            raise ValueError("Artifact path must remain inside the project artifacts directory") from exc
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def export_json(self, path: str | os.PathLike[str]) -> Path:
        return self.save(path)

    @classmethod
    def open(cls, path: str | os.PathLike[str]) -> "ProjectStore":
        return cls(path)


# Short alias used by integrations.
Storage = ProjectStore
