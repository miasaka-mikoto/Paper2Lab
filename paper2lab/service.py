"""Application service coordinating Paper2Lab's offline pipeline."""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Mapping

from .blueprint import BlueprintBuilder, CodeSkeletonGenerator
from .claims import RuleBasedClaimExtractor
from .compare import compare_results
from .models import FormulaNote, Paper, PaperMetadata, RunRecord, RunStatus, utc_now
from .parser import DocumentParser
from .providers import LLMProvider, MockLLMProvider, ProviderRegistry, default_registry
from .report import write_report
from .reproducibility import assess_reproducibility
from .runner import ExperimentRunner, MockExperimentRunner, RunResult
from .storage import ProjectStore


PACKAGED_SAMPLE_FALLBACK = """# Paper2Lab Offline Sample\n\n# Abstract\nWe present a fictional deterministic retrieval memory and report an accuracy result.\n\n# Method\nThe method retrieves top-k notes using token overlap.\n\n# Dataset\nA fictional synthetic evaluation set is used.\n\n# Experiment\nWe compare the method with a no-memory baseline using a fixed seed.\n\n# Results\nThe paper reports accuracy 0.814.\n"""


class Paper2LabService:
    """High-level use-case API used by scripts, tests, and future UI adapters.

    Every operation is local and deterministic unless the caller supplies a
    custom runner/provider.  The service stores source text and all derived
    records in a portable JSON project.
    """

    def __init__(
        self,
        workspace: str | Path | None = None,
        *,
        root: str | Path | None = None,
        provider: LLMProvider | None = None,
        runner: MockExperimentRunner | None = None,
        process_runner: ExperimentRunner | None = None,
    ) -> None:
        # ``root`` and ``provider`` are explicit aliases for integrations that
        # construct services from a settings object.  They remain optional so
        # the small ``Paper2LabService()`` entry point stays unchanged.
        if root is not None:
            workspace = root
        if workspace is None:
            # Keep user projects out of the source tree when the desktop app
            # is launched by double-clicking the Windows executable.
            configured = os.environ.get("PAPER2LAB_WORKSPACE")
            base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home() / ".local" / "share")
            workspace = Path(configured) if configured else Path(base) / "Paper2Lab"
        self.store = ProjectStore(workspace)
        self.parser = DocumentParser()
        self.extractor = RuleBasedClaimExtractor()
        self.builder = BlueprintBuilder()
        self.generator = CodeSkeletonGenerator()
        self.provider_registry: ProviderRegistry = default_registry(seed=42)
        if provider is not None:
            provider_name = str(getattr(provider, "name", "custom") or "custom")
            self.provider_registry.register(provider_name, provider)
        self.provider = provider or self.provider_registry.get("mock")
        # ``providers`` is a short integration-friendly alias used by future
        # settings panels; all entries remain local/deferred in this build.
        self.providers = self.provider_registry
        self.mock_runner = runner or MockExperimentRunner()
        # The synchronous mock runner remains the default path used by the
        # sample/demo.  ``process_runner`` is a separate, opt-in subprocess
        # engine for generated skeletons so desktop controls can genuinely
        # pause, resume, cancel, wait, and retry a long experiment.
        self.process_runner = process_runner or ExperimentRunner()
        # Compatibility adapters commonly look for ``runner`` or
        # ``experiment_runner``.  Keep both names explicit without replacing
        # the existing ``mock_runner`` contract.
        self.experiment_runner = self.process_runner
        self._process_specs: dict[str, dict[str, Any]] = {}
        self.logs: list[str] = []
        # ``ProjectStore`` eagerly loads an existing default workspace during
        # construction.  Hydrate the run index from those persisted records
        # as well, otherwise a close/reopen cycle would restore the paper and
        # blueprint data but leave the desktop Runs panel empty until a new
        # run is started.
        self.runs: dict[str, RunRecord] = {
            run.run_id: run
            for stored_paper in self.store.papers.values()
            for run in stored_paper.runs
        }

    @property
    def papers(self) -> dict[str, Paper]:
        return self.store.papers

    def _log(self, text: str) -> None:
        self.logs.append(text)

    def list_papers(self) -> list[Paper]:
        return self.store.list_papers()

    def get_paper(self, paper_id: str) -> Paper | None:
        return self.store.get_paper(paper_id)

    def search(self, query: str) -> list[Paper]:
        """Full-text search across title, metadata, source, and derived data."""
        return self.store.search(query)

    search_papers = search

    def list_providers(self) -> list[str]:
        """Return configured provider names without making any request."""
        return self.provider_registry.names()

    def get_provider(self, name: str = "mock") -> LLMProvider:
        aliases = {
            "rule": "rule-based",
            "rulebased": "rule-based",
            "rule_based": "rule-based",
            "rulebasedextractor": "rule-based",
            "mock-llm": "mock",
            "mock_llm": "mock",
        }
        normalized = str(name or "mock").casefold()
        return self.provider_registry.get(aliases.get(normalized, normalized))

    # ------------------------------------------------------------------
    # Library/editor convenience APIs
    # ------------------------------------------------------------------
    def update_metadata(self, paper_id: str, **changes: Any) -> Paper:
        """Update editable Paper Library metadata and persist it.

        The UI can pass either canonical snake_case names or the short labels
        used by import forms (``path``, ``author``, ``keyword`` and ``tag``).
        Unknown keys are rejected instead of silently disappearing from the
        project file.
        """

        paper = self.store.require_paper(paper_id)
        aliases = {
            "name": "title",
            "author": "authors",
            "path": "local_path",
            "keyword": "keywords",
            "tag": "tags",
            "note": "notes",
        }
        allowed = {"title", "authors", "year", "venue", "abstract", "keywords", "notes", "tags", "local_path"}
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
            setattr(paper.metadata, canonical, value)
        paper.updated_at = utc_now()
        self.store.update_paper(paper)
        return paper

    update_paper_metadata = update_metadata

    def set_notes(self, paper_id: str, notes: str) -> Paper:
        return self.update_metadata(paper_id, notes=notes)

    def set_tags(self, paper_id: str, tags: Any) -> Paper:
        return self.update_metadata(paper_id, tags=tags)

    def list_claims(self, paper_id: str) -> list[Any]:
        return list(self.store.require_paper(paper_id).claims)

    def list_blueprints(self, paper_id: str) -> list[Any]:
        return list(self.store.require_paper(paper_id).blueprints)

    def list_formula_notes(self, paper_id: str) -> list[FormulaNote]:
        return list(self.store.require_paper(paper_id).formula_notes)

    def list_runs(self, paper_id: str | None = None) -> list[RunRecord]:
        papers = [self.store.require_paper(paper_id)] if paper_id else self.store.list_papers()
        return [run for paper in papers for run in paper.runs]

    def import_paper(self, path: str | Path, *, copy_into_project: bool = False) -> Paper:
        source = Path(path).expanduser().resolve()
        parsed = self.parser.parse_file(source)
        if copy_into_project:
            destination = self.store.root / "sources" / source.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            parsed.metadata.local_path = str(destination)
        paper = Paper(metadata=parsed.metadata, raw_text=parsed.text, source_format=parsed.source_format, sections=parsed.sections)
        self.store.add_paper(paper)
        self._log(f"Imported {paper.metadata.title}")
        return paper

    def create_sample_paper(self, path: str | Path | None = None) -> Paper:
        if path:
            source = Path(path)
        else:
            bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
            source = bundle_root / "sample" / "sample_paper.md"
            if not source.exists():
                source = Path(__file__).resolve().parents[1] / "sample" / "sample_paper.md"
        if not source.exists():
            parsed = self.parser.parse_text(PACKAGED_SAMPLE_FALLBACK, "markdown", filename="sample_paper.md")
            paper = Paper(metadata=parsed.metadata, raw_text=parsed.text, source_format=parsed.source_format, sections=parsed.sections)
            self.store.add_paper(paper)
            self._log("Created packaged fallback sample paper")
            return paper
        return self.import_paper(source)

    def parse_structure(self, paper_id: str, *, preserve_manual: bool = False) -> list[Any]:
        paper = self.store.require_paper(paper_id)
        previous_sections = list(paper.sections)
        parsed = self.parser.parse_text(paper.raw_text, paper.source_format, filename=paper.metadata.local_path)
        # Re-parse the immutable raw document.  Explicitly requested manual
        # sections are retained by order/title when callers opt in.  Keeping
        # the stable section id also preserves claim references in the UI.
        paper.metadata.abstract = parsed.metadata.abstract or paper.metadata.abstract
        paper.metadata.local_path = paper.metadata.local_path or parsed.metadata.local_path
        if preserve_manual:
            used: set[str] = set()
            merged: list[Any] = []
            for index, section in enumerate(parsed.sections):
                candidate = next((item for item in previous_sections if item.section_id not in used and item.order == index), None)
                if candidate is None:
                    candidate = next((item for item in previous_sections if item.section_id not in used and item.title.casefold() == section.title.casefold()), None)
                if candidate is not None:
                    used.add(candidate.section_id)
                    section.section_id = candidate.section_id
                    # A changed title/content is considered a manual edit
                    # when preserve_manual=True; source parsing remains the
                    # default behavior when callers want a clean refresh.
                    if candidate.title.strip() and candidate.title.casefold() != section.title.casefold():
                        section.title = candidate.title
                    if candidate.content.strip() and candidate.content != section.content:
                        section.content = candidate.content
                merged.append(section)
            merged.extend(item for item in previous_sections if item.section_id not in used)
            paper.sections = merged
        else:
            paper.sections = parsed.sections
        self.store.update_paper(paper)
        return paper.sections

    def rename_section(self, paper_id: str, section_id: str, new_title: str) -> Any:
        """Persist a manual chapter-title correction without changing source text."""
        return self.edit_section(paper_id, section_id, new_title=new_title)

    def edit_section(
        self,
        paper_id: str,
        section_id: str,
        new_title: str | Mapping[str, Any] | None = None,
        content: str | None = None,
        **changes: Any,
    ) -> Any:
        """Edit a parsed section's title and/or body.

        ``new_title`` may also be a mapping such as ``{"title": ..., 
        "content": ...}``, which makes this method convenient for a form
        binding while retaining the three-argument rename API.
        """
        paper = self.store.require_paper(paper_id)
        section = next((item for item in paper.sections if item.section_id == section_id), None)
        if section is None:
            raise KeyError(f"Unknown section: {section_id}")
        payload: dict[str, Any] = dict(new_title) if isinstance(new_title, Mapping) else {}
        if not isinstance(new_title, Mapping) and new_title is not None:
            payload["title"] = new_title
        payload.update(changes)
        title = payload.get("title", payload.get("name", section.title))
        title = str(title or "").strip()
        if not title:
            raise ValueError("Section title cannot be empty")
        section.title = title
        if content is not None:
            section.content = str(content)
        elif "content" in payload or "text" in payload:
            section.content = str(payload.get("content", payload.get("text", "")) or "")
        if "level" in payload:
            try:
                section.level = int(payload["level"])
            except (TypeError, ValueError) as exc:
                raise ValueError("Section level must be an integer") from exc
        paper.updated_at = utc_now()
        self.store.update_paper(paper)
        return section

    parse_paper = parse_structure
    reparse = parse_structure

    def extract_claims(self, paper_id: str, *, provider: str = "rule", replace: bool = True) -> list[Any]:
        paper = self.store.require_paper(paper_id)
        if not paper.sections:
            self.parse_structure(paper_id)
        selected_name = str(provider or "rule").casefold()
        selected = self.get_provider(selected_name)
        # The offline build intentionally keeps extraction deterministic even
        # when the Mock provider is selected.  Reserved real-model names are
        # surfaced explicitly instead of silently falling back to heuristics.
        if getattr(selected, "name", "") not in {"mock", "rule-based"}:
            raise NotImplementedError(
                f"Provider '{provider}' is reserved but not implemented in the offline build."
            )
        claims = self.extractor.extract(paper)
        if replace:
            # Claim IDs are durable references used by blueprints, graph
            # edges, and editor forms.  A fresh extraction naturally creates
            # fresh dataclass instances, but replacing a paper's candidates
            # must not invalidate references when the source sentence is
            # unchanged.  Reuse an existing ID by normalized text first and
            # source span second (the latter also survives a manual claim
            # text correction).  Matching is one-to-one so duplicate
            # sentences retain distinct IDs in their original order.
            by_text: dict[str, list[Any]] = {}
            by_span: dict[tuple[str, tuple[int, int]], list[Any]] = {}
            used_previous_ids: set[str] = set()
            for previous in paper.claims:
                key = re.sub(r"\W+", " ", str(getattr(previous, "claim_text", "")).casefold()).strip()
                if key:
                    by_text.setdefault(key, []).append(previous)
                span = getattr(previous, "source_span", None)
                section = str(getattr(previous, "section", ""))
                if isinstance(span, (tuple, list)) and len(span) == 2:
                    try:
                        span_key = (section.casefold(), (int(span[0]), int(span[1])))
                    except (TypeError, ValueError):
                        span_key = None
                    if span_key is not None:
                        by_span.setdefault(span_key, []).append(previous)
            for candidate in claims:
                key = re.sub(r"\W+", " ", str(getattr(candidate, "claim_text", "")).casefold()).strip()
                previous = by_text.get(key, [])
                while previous and str(getattr(previous[0], "claim_id", "")) in used_previous_ids:
                    previous.pop(0)
                matched = previous.pop(0) if previous else None
                if matched is None:
                    span = getattr(candidate, "source_span", None)
                    section = str(getattr(candidate, "section", ""))
                    if isinstance(span, (tuple, list)) and len(span) == 2:
                        try:
                            span_key = (section.casefold(), (int(span[0]), int(span[1])))
                        except (TypeError, ValueError):
                            span_key = None
                        span_candidates = by_span.get(span_key, []) if span_key is not None else []
                        while span_candidates and str(getattr(span_candidates[0], "claim_id", "")) in used_previous_ids:
                            span_candidates.pop(0)
                        matched = span_candidates.pop(0) if span_candidates else None
                if matched is not None:
                    candidate.claim_id = matched.claim_id
                    used_previous_ids.add(str(matched.claim_id))
            paper.claims = claims
        else:
            seen = {claim.claim_text.casefold() for claim in paper.claims}
            paper.claims.extend(claim for claim in claims if claim.claim_text.casefold() not in seen)
        self.store.update_paper(paper)
        self._log(f"Extracted {len(claims)} claims with {provider} provider")
        return claims if replace else paper.claims

    def get_claim(self, paper_id: str, claim_id: str) -> Any:
        paper = self.store.require_paper(paper_id)
        return next((claim for claim in paper.claims if claim.claim_id == claim_id), None)

    def update_claim(self, paper_id: str, claim_id: str, **changes: Any) -> Any:
        """Persist researcher corrections to an extracted claim."""
        paper = self.store.require_paper(paper_id)
        claim = next((item for item in paper.claims if item.claim_id == claim_id), None)
        if claim is None:
            raise KeyError(f"Unknown claim: {claim_id}")
        aliases = {"text": "claim_text", "kind": "claim_type", "type": "claim_type", "id": "claim_id"}
        allowed = {field.name for field in __import__("dataclasses").fields(claim)}
        for key, value in changes.items():
            key = aliases.get(key, key)
            if key not in allowed or key in {"claim_id", "paper_id"}:
                raise ValueError(f"Unknown or immutable claim field: {key}")
            if key == "claim_type":
                from .models import ClaimType

                try:
                    value = ClaimType(value)
                except (TypeError, ValueError):
                    raise ValueError(f"Unknown claim type: {value}")
            setattr(claim, key, value)
        paper.updated_at = utc_now()
        self.store.update_paper(paper)
        return claim

    def add_formula_note(self, paper_id: str, kind: str, content: str, explanation: str = "") -> FormulaNote:
        """Store a formula, pseudocode, algorithm, or parameter note."""
        note = FormulaNote(kind=kind, content=content, explanation=explanation, paper_id=paper_id)
        self.store.save_note(note)
        return note

    def update_formula_note(self, paper_id: str, note_id: str, **changes: Any) -> FormulaNote:
        paper = self.store.require_paper(paper_id)
        note = next((item for item in paper.formula_notes if item.note_id == note_id), None)
        if note is None:
            raise KeyError(f"Unknown formula note: {note_id}")
        for key in ("kind", "content", "explanation"):
            if key in changes:
                setattr(note, key, str(changes[key] or ""))
        unknown = set(changes) - {"kind", "content", "explanation"}
        if unknown:
            raise ValueError(f"Unknown formula note fields: {', '.join(sorted(unknown))}")
        paper.updated_at = utc_now()
        self.store.update_paper(paper)
        return note

    def delete_formula_note(self, paper_id: str, note_id: str) -> bool:
        paper = self.store.require_paper(paper_id)
        before = len(paper.formula_notes)
        paper.formula_notes = [item for item in paper.formula_notes if item.note_id != note_id]
        deleted = len(paper.formula_notes) != before
        if deleted:
            paper.updated_at = utc_now()
            self.store.update_paper(paper)
        return deleted

    def validate_blueprint(self, paper_id: str, blueprint_id: str | None = None) -> list[str]:
        blueprint = self.get_blueprint(paper_id, blueprint_id)
        if blueprint is None:
            raise ValueError("No experiment blueprint")
        return self.builder.validate(blueprint)

    def create_blueprint(self, paper_id: str, claim_id: str | None = None, **overrides: Any) -> Any:
        paper = self.store.require_paper(paper_id)
        if not paper.claims:
            self.extract_claims(paper_id)
        if not paper.claims:
            raise ValueError("No extractable claims are available; review the paper and add a claim before creating an experiment blueprint.")
        if claim_id is None:
            claim = paper.claims[0]
        else:
            claim = next((c for c in paper.claims if c.claim_id == claim_id), None)
            if claim is None:
                raise KeyError(f"Unknown claim: {claim_id}")
        blueprint = self.builder.build(claim, paper, **overrides)
        paper.blueprints.append(blueprint)
        self.store.update_paper(paper)
        return blueprint

    def get_blueprint(self, paper_id: str, blueprint_id: str | None = None) -> Any:
        paper = self.store.require_paper(paper_id)
        return next((b for b in paper.blueprints if not blueprint_id or b.blueprint_id == blueprint_id), None)

    def update_blueprint(self, paper_id: str, blueprint_id: str, **changes: Any) -> Any:
        """Persist edits made in the Experiment Blueprint form."""
        paper = self.store.require_paper(paper_id)
        blueprint = next((item for item in paper.blueprints if item.blueprint_id == blueprint_id), None)
        if blueprint is None:
            raise KeyError(f"Unknown blueprint: {blueprint_id}")
        allowed = {field.name for field in __import__("dataclasses").fields(blueprint)}
        aliases = {"id": "blueprint_id", "plan": "implementation_plan", "paper_result": "paper_reported_result", "local_results": "local_result"}
        for key, value in changes.items():
            key = aliases.get(key, key)
            if key not in allowed or key in {"blueprint_id", "paper_id", "claim_id", "created_at"}:
                raise ValueError(f"Unknown or immutable blueprint field: {key}")
            setattr(blueprint, key, value)
        blueprint.updated_at = utc_now()
        self.store.update_paper(paper)
        return blueprint

    def generate_skeleton(self, paper_id: str, blueprint_id: str | None = None, output_dir: str | Path | None = None) -> Path:
        paper = self.store.require_paper(paper_id)
        blueprint = self.get_blueprint(paper_id, blueprint_id) or self.create_blueprint(paper_id)
        target = Path(output_dir) if output_dir else self.store.artifact_path("experiments", blueprint.blueprint_id)
        result = self.generator.generate(blueprint, target, overwrite=True)
        self._log(f"Generated skeleton {result}")
        return result

    def generate_experiment(
        self,
        paper_id: str,
        blueprint_id: str | None = None,
        target_dir: str | Path | None = None,
        *,
        overwrite: bool = True,
    ) -> Path:
        """Compatibility name for the paper→experiment code-generation step."""
        paper = self.store.require_paper(paper_id)
        blueprint = self.get_blueprint(paper_id, blueprint_id) or self.create_blueprint(paper_id)
        target = Path(target_dir) if target_dir else self.store.artifact_path("experiments", blueprint.blueprint_id)
        result = self.generator.generate(blueprint, target, overwrite=overwrite)
        self._log(f"Generated experiment {result}")
        return result

    def run_experiment(self, paper_id: str, blueprint_id: str | None = None, seed: int = 42) -> RunRecord:
        paper = self.store.require_paper(paper_id)
        blueprint = self.get_blueprint(paper_id, blueprint_id) or self.create_blueprint(paper_id)
        result = self.mock_runner.run(blueprint, seed=seed)
        run = RunRecord(
            blueprint_id=blueprint.blueprint_id,
            status=self._process_status(getattr(result, "status", "completed")),
            stdout=result.stdout,
            stderr=getattr(result, "stderr", "") or "",
            runtime_seconds=result.runtime_seconds,
            config=result.config if isinstance(result.config, dict) else {},
            seed=result.seed if getattr(result, "seed", None) is not None else seed,
            result=result.result if isinstance(result.result, dict) else {},
            environment=result.environment,
            run_id=result.run_id,
            started_at=result.started_at,
            finished_at=result.finished_at,
            error=getattr(result, "error", "") or "",
        )
        paper.runs.append(run)
        if run.status == RunStatus.SUCCEEDED:
            metrics = (run.result or {}).get("metrics", {})
            if isinstance(metrics, dict):
                blueprint.local_result = metrics
                blueprint.updated_at = utc_now()
        self.runs[run.run_id] = run
        self.store.update_paper(paper)
        self._log(f"Mock run {run.run_id} completed")
        return run

    def run_mock_experiment(self, paper_id: str, blueprint_id: str | None = None, *, seed: int = 42) -> RunRecord:
        """Explicit alias making the offline nature of this runner clear."""
        return self.run_experiment(paper_id, blueprint_id, seed=seed)

    # ------------------------------------------------------------------
    # Generated-process runner
    # ------------------------------------------------------------------
    # ``run_experiment`` above intentionally stays synchronous and Mock-only
    # for backwards compatibility with the offline demo.  The methods below
    # provide the real lifecycle used by a desktop Run/Pause/Cancel/Retry
    # control bar.  They execute only local commands and never invoke a model
    # or network service.

    @staticmethod
    def _process_status(status: str) -> RunStatus:
        normalized = str(status or "").casefold()
        return {
            "queued": RunStatus.QUEUED,
            "running": RunStatus.RUNNING,
            "paused": RunStatus.PAUSED,
            "cancelled": RunStatus.CANCELLED,
            "completed": RunStatus.SUCCEEDED,
            "succeeded": RunStatus.SUCCEEDED,
            "timeout": RunStatus.FAILED,
            "failed": RunStatus.FAILED,
        }.get(normalized, RunStatus.FAILED)

    @staticmethod
    def _request_config(path: Path | None, seed: int) -> dict[str, Any]:
        """Load a JSON-compatible generated config without requiring PyYAML."""
        if path is not None and path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    payload.setdefault("seed", seed)
                    return payload
            except (OSError, ValueError):
                # User-authored YAML is still valid input to the child process;
                # retain an auditable path/seed rather than failing the run
                # record just because this optional preview cannot parse it.
                pass
        return {"config_path": str(path) if path else "", "seed": seed}

    def _persist_run(self, paper: Paper, run: RunRecord) -> RunRecord:
        """Update the in-memory index and atomically persist one run."""
        self.runs[run.run_id] = run
        self.store.save_run(run, paper.paper_id)
        return run

    def _apply_process_result(self, run_id: str, result: RunResult) -> RunRecord:
        """Copy a subprocess result into the canonical domain record."""
        run, paper = self._find_run(run_id)
        run.status = self._process_status(result.status)
        run.stdout = result.stdout or ""
        run.stderr = result.stderr or ""
        run.runtime_seconds = result.runtime_seconds
        if isinstance(result.config, dict):
            run.config = result.config
        run.seed = result.seed if result.seed is not None else run.seed
        if isinstance(result.result, dict):
            run.result = result.result
        run.environment = dict(result.environment or {})
        run.started_at = result.started_at or run.started_at
        run.finished_at = result.finished_at or run.finished_at
        run.error = result.error or ""
        blueprint = next((item for item in paper.blueprints if item.blueprint_id == run.blueprint_id), None)
        if blueprint is not None and run.status == RunStatus.SUCCEEDED and isinstance(run.result, dict):
            metrics = run.result.get("metrics")
            if not isinstance(metrics, dict):
                # ``CodeSkeletonGenerator`` predates the richer generator and
                # writes metric names at the result root.  Normalize that
                # shape here so comparison/reporting still receives a clear
                # metric mapping without rewriting user-generated scripts.
                metadata = {"status", "mode", "seed", "todo", "runtime_seconds", "config", "environment", "notes"}
                metrics = {
                    str(name): value
                    for name, value in run.result.items()
                    if name not in metadata and isinstance(value, (int, float)) and not isinstance(value, bool)
                }
            if isinstance(metrics, dict):
                blueprint.local_result = metrics
                blueprint.updated_at = utc_now()
        self._persist_run(paper, run)
        return run

    def _refresh_process_run(self, run_id: str) -> RunRecord:
        """Refresh a process-backed run if its worker has completed."""
        run, _paper = self._find_run(run_id)
        result = self.process_runner.get_result(run_id)
        if result is not None:
            return self._apply_process_result(run_id, result)
        # Keep a paused/running state in sync with the process engine when a
        # caller polls before ``wait``.
        state = self.process_runner.status(run_id)
        mapped = {
            "queued": RunStatus.QUEUED,
            "running": RunStatus.RUNNING,
            "paused": RunStatus.PAUSED,
        }.get(state)
        if mapped is not None and run.status != mapped:
            run.status = mapped
            _run, paper = self._find_run(run_id)
            self._persist_run(paper, run)
        return run

    def start_experiment(
        self,
        paper_id: str,
        blueprint_id: str | None = None,
        *,
        seed: int = 42,
        experiment_dir: str | Path | None = None,
        command: list[str] | tuple[str, ...] | None = None,
        config_path: str | Path | None = None,
        timeout: float | None = None,
        env: Mapping[str, str] | None = None,
    ) -> RunRecord:
        """Start a generated experiment in a local subprocess.

        The returned record is persisted immediately with ``running`` status;
        call :meth:`wait_experiment` to collect stdout/stderr and the result.
        If no directory is supplied, a transparent mock skeleton is generated
        below the project's ``artifacts/experiments`` directory.
        """
        paper = self.store.require_paper(paper_id)
        blueprint = self.get_blueprint(paper_id, blueprint_id) or self.create_blueprint(paper_id)
        target = Path(experiment_dir).expanduser().resolve() if experiment_dir else self.store.artifact_path("experiments", blueprint.blueprint_id)
        if not target.exists() or not (target / "run.py").exists():
            # The canonical generator writes directly into ``target`` and
            # marks unresolved paper-specific code with TODOs.
            self.generator.generate(blueprint, target, overwrite=False)
        if not target.is_dir():
            raise NotADirectoryError(target)

        resolved_config = Path(config_path).expanduser().resolve() if config_path else target / "config.yaml"
        # Give every process attempt its own result path.  This prevents a
        # retry from racing with or overwriting a prior result.json.
        pending_output = target / "runs" / "pending.result.json"
        request = self.process_runner.make_request(
            target,
            command=command,
            config_path=resolved_config,
            output_path=pending_output,
            seed=seed,
            timeout=timeout,
            env=env,
        )
        unique_output = target / "runs" / f"{request.run_id}.result.json"
        request.output_path = unique_output
        if command is None and request.command:
            # ``make_request`` appends ``--seed <n>`` after the output pair;
            # replacing the final token would accidentally turn the seed into
            # a filesystem path.  Locate the flag explicitly instead.
            try:
                output_index = request.command.index("--output")
                request.command[output_index + 1] = str(unique_output)
            except (ValueError, IndexError):
                pass
        config = self._request_config(resolved_config, seed)
        run = RunRecord(
            blueprint_id=blueprint.blueprint_id,
            status=RunStatus.QUEUED,
            config=config,
            seed=seed,
            run_id=request.run_id,
        )
        paper.runs.append(run)
        self._persist_run(paper, run)
        self._process_specs[run.run_id] = {
            "paper_id": paper.paper_id,
            "blueprint_id": blueprint.blueprint_id,
            "experiment_dir": str(target),
            "command": list(command) if command is not None else None,
            "config_path": str(resolved_config),
            "timeout": timeout,
            "env": dict(env or {}),
        }
        try:
            actual_id = self.process_runner.start(
                target,
                command=request.command,
                config_path=resolved_config,
                output_path=unique_output,
                seed=seed,
                timeout=timeout,
                env=env,
                run_id=request.run_id,
            )
            if actual_id != run.run_id:
                # Defensive guard for a custom runner that allocates IDs.
                self._process_specs[actual_id] = self._process_specs.pop(run.run_id)
                run.run_id = actual_id
            run.status = RunStatus.RUNNING
            run.started_at = utc_now()
            self._persist_run(paper, run)
        except Exception as exc:
            run.status = RunStatus.FAILED
            run.error = str(exc)
            run.finished_at = utc_now()
            self._persist_run(paper, run)
            raise
        # Very short skeletons can finish between ``start`` and return.  Pick
        # up such a result eagerly while retaining the non-blocking API.
        return self._refresh_process_run(run.run_id)

    # Natural aliases for adapters and callers that use ``start_run`` naming.
    start_run = start_experiment
    run_generated_experiment = start_experiment

    def wait_experiment(self, run_id: str, timeout: float | None = None) -> RunRecord:
        """Wait for a process-backed run and persist its terminal record."""
        run, _paper = self._find_run(run_id)
        if run_id in self._process_specs:
            self.process_runner.wait(run_id, timeout=timeout)
            return self._refresh_process_run(run_id)
        return run

    wait_run = wait_experiment

    def _find_run(self, run_id: str, paper_id: str | None = None) -> tuple[RunRecord, Paper]:
        papers = [self.store.require_paper(paper_id)] if paper_id else self.store.list_papers()
        for paper in papers:
            for run in paper.runs:
                if run.run_id == run_id:
                    return run, paper
        raise KeyError(f"Unknown run: {run_id}")

    def pause_run(self, run_id: str, paper_id: str | None = None) -> RunRecord:
        """Pause a process-backed run, or persist a compatibility transition."""
        run, paper = self._find_run(run_id, paper_id)
        if run_id in self._process_specs:
            refreshed = self._refresh_process_run(run_id)
            if refreshed.status in {RunStatus.RUNNING, RunStatus.QUEUED} and self.process_runner.pause(run_id):
                run.status = RunStatus.PAUSED
                self._persist_run(paper, run)
            return self._refresh_process_run(run_id)
        if run.status == RunStatus.RUNNING:
            run.status = RunStatus.PAUSED
            self._persist_run(paper, run)
        return run

    def resume_run(self, run_id: str, paper_id: str | None = None) -> RunRecord:
        """Resume a paused local subprocess, or a compatibility record."""
        run, paper = self._find_run(run_id, paper_id)
        if run_id in self._process_specs:
            if self.process_runner.resume(run_id):
                run.status = RunStatus.RUNNING
                self._persist_run(paper, run)
            return self._refresh_process_run(run_id)
        if run.status == RunStatus.PAUSED:
            run.status = RunStatus.RUNNING
            self._persist_run(paper, run)
        return run

    def cancel_run(self, run_id: str, paper_id: str | None = None) -> RunRecord:
        run, paper = self._find_run(run_id, paper_id)
        if run_id in self._process_specs:
            refreshed = self._refresh_process_run(run_id)
            if refreshed.status in {RunStatus.RUNNING, RunStatus.PAUSED, RunStatus.QUEUED}:
                self.process_runner.cancel(run_id)
                self.process_runner.wait(run_id, timeout=3.0)
                refreshed = self._refresh_process_run(run_id)
                if refreshed.status in {RunStatus.RUNNING, RunStatus.PAUSED, RunStatus.QUEUED}:
                    run.status = RunStatus.CANCELLED
                    run.finished_at = utc_now()
                    self._persist_run(paper, run)
                return refreshed
            return refreshed
        if run.status in {RunStatus.RUNNING, RunStatus.PAUSED, RunStatus.QUEUED}:
            run.status = RunStatus.CANCELLED
            run.finished_at = utc_now()
            self._persist_run(paper, run)
        return run

    def retry_run(self, run_id: str, paper_id: str | None = None) -> RunRecord:
        run, paper = self._find_run(run_id, paper_id)
        if run_id in self._process_specs:
            current = self._refresh_process_run(run_id)
            if current.status in {RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.PAUSED}:
                raise RuntimeError("Cannot retry an active experiment; wait or cancel it first")
            spec = self._process_specs[run_id]
            return self.start_experiment(
                paper.paper_id,
                run.blueprint_id,
                seed=run.seed or 42,
                experiment_dir=spec.get("experiment_dir"),
                command=spec.get("command"),
                config_path=spec.get("config_path"),
                timeout=spec.get("timeout"),
                env=spec.get("env") or {},
            )
        return self.run_experiment(paper.paper_id, run.blueprint_id, seed=run.seed or 42)

    def compare_results(self, paper_id: str, blueprint_id: str | None = None) -> list[Any]:
        blueprint = self.get_blueprint(paper_id, blueprint_id)
        if blueprint is None:
            raise ValueError("No experiment blueprint")
        rows = compare_results(blueprint.paper_reported_result, blueprint.local_result)
        blueprint.difference = {row.metric: row.to_dict() for row in rows}
        self.store.update_paper(self.store.require_paper(paper_id))
        return rows

    def reproducibility_score(self, paper_id: str, evidence: Mapping[str, Any] | None = None) -> Any:
        paper = self.store.require_paper(paper_id)
        return assess_reproducibility(paper.to_dict(), evidence=evidence)

    score_reproducibility = reproducibility_score

    def generate_report(
        self,
        paper_id: str,
        blueprint_id: str | None = None,
        output_dir: str | Path | None = None,
        *,
        format: str | None = None,
    ) -> dict[str, str] | str:
        paper = self.store.require_paper(paper_id)
        blueprint = self.get_blueprint(paper_id, blueprint_id)
        if blueprint is None:
            raise ValueError("No experiment blueprint")
        comparisons = self.compare_results(paper_id, blueprint.blueprint_id)
        # A paper can contain several blueprints.  Attach provenance from the
        # most recent run of this blueprint only; using the paper-wide last
        # run could silently put another experiment's result in the report.
        run = next(
            (candidate for candidate in reversed(paper.runs) if candidate.blueprint_id == blueprint.blueprint_id),
            None,
        )
        score = self.reproducibility_score(paper_id)
        target = Path(output_dir) if output_dir else self.store.artifact_path("reports", blueprint.blueprint_id)
        files = write_report(target, paper, blueprint, comparisons, run=run, reproducibility=score)
        self._log(f"Report written to {target}")
        if format:
            key = "html" if format.lower() in {"html", "htm"} else "markdown"
            return files[key]
        return files

    def research_graph(self, paper_id: str) -> dict[str, list[dict[str, Any]]]:
        """Return a small node/edge graph for the UI's graph panel/export."""
        paper = self.store.require_paper(paper_id)
        nodes: list[dict[str, Any]] = [{"id": paper.paper_id, "type": "Paper", "label": paper.metadata.title}]
        edges: list[dict[str, Any]] = []
        seen: set[str] = {paper.paper_id}

        def add_node(node_id: str, node_type: str, label: str) -> None:
            if node_id not in seen:
                nodes.append({"id": node_id, "type": node_type, "label": label})
                seen.add(node_id)

        for claim in paper.claims:
            add_node(claim.claim_id, "Claim", claim.claim_text)
            edges.append({"source": paper.paper_id, "target": claim.claim_id, "relation": "SUPPORTS"})
        for blueprint in paper.blueprints:
            add_node(blueprint.blueprint_id, "Experiment", blueprint.research_question)
            if blueprint.claim_id:
                edges.append({"source": blueprint.claim_id, "target": blueprint.blueprint_id, "relation": "TESTED_BY"})
            method_id = f"{blueprint.blueprint_id}:method"
            dataset_id = f"{blueprint.blueprint_id}:dataset"
            add_node(method_id, "Method", blueprint.model)
            add_node(dataset_id, "Dataset", blueprint.dataset)
            edges.extend([
                {"source": blueprint.blueprint_id, "target": method_id, "relation": "USES_METHOD"},
                {"source": blueprint.blueprint_id, "target": dataset_id, "relation": "USES_DATASET"},
            ])
            for metric in blueprint.metrics:
                node_id = f"{blueprint.blueprint_id}:metric:{metric}"
                add_node(node_id, "Metric", metric)
                edges.append({"source": blueprint.blueprint_id, "target": node_id, "relation": "MEASURES"})
            # Keep paper-reported and local values as distinct Result nodes;
            # otherwise a graph can imply that a mock/local number came from
            # the source paper.  Both remain linked to their Metric node.
            reported = blueprint.paper_reported_result or {}
            local = blueprint.local_result or {}
            metric_names = list(dict.fromkeys([*blueprint.metrics, *reported.keys(), *local.keys()]))
            for metric in metric_names:
                metric_id = f"{blueprint.blueprint_id}:metric:{metric}"
                add_node(metric_id, "Metric", str(metric))
                if metric not in blueprint.metrics:
                    edges.append({"source": blueprint.blueprint_id, "target": metric_id, "relation": "MEASURES"})
                if metric in reported:
                    result_id = f"{blueprint.blueprint_id}:result:paper:{metric}"
                    add_node(result_id, "Result", f"Paper {metric}: {reported[metric]}")
                    edges.extend([
                        {"source": paper.paper_id, "target": result_id, "relation": "REPORTS_RESULT"},
                        {"source": metric_id, "target": result_id, "relation": "HAS_VALUE"},
                    ])
                if metric in local:
                    result_id = f"{blueprint.blueprint_id}:result:local:{metric}"
                    add_node(result_id, "Result", f"Local {metric}: {local[metric]}")
                    edges.extend([
                        {"source": blueprint.blueprint_id, "target": result_id, "relation": "PRODUCES"},
                        {"source": metric_id, "target": result_id, "relation": "HAS_VALUE"},
                    ])
        return {"nodes": nodes, "edges": edges}

    graph = research_graph
    get_research_graph = research_graph

    def save_project(self, path: str | Path | None = None) -> str:
        return str(self.store.save(path))

    def load_project(self, path: str | Path) -> None:
        self.store.load(path)
        self.runs = {run.run_id: run for paper in self.store.papers.values() for run in paper.runs}
        self._log(f"Loaded project {path}")

    save = save_project
    load = load_project


__all__ = ["Paper2LabService"]
