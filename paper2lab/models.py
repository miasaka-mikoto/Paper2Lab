"""Serializable domain models used by Paper2Lab.

The models are intentionally plain dataclasses.  They can be persisted as
JSON without a database and remain readable by future versions of the app.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from enum import Enum
from typing import Any, ClassVar, Mapping, TypeVar
import datetime as _dt
import re
import uuid


def _first_value(data: Mapping[str, Any], *names: str, default: Any = None) -> Any:
    """Return the first present field, accepting common serialized aliases.

    Project files are intentionally human-readable and older preview builds
    used short keys such as ``id``/``text``.  Keeping this tiny migration
    helper in the model layer lets the storage loader read those files without
    changing the current canonical schema.
    """

    lowered = {str(key).casefold(): value for key, value in data.items()}
    for name in names:
        if name in data:
            return data[name]
        value = lowered.get(name.casefold())
        if value is not None:
            return value
    return default


def _list_value(value: Any) -> list[Any]:
    """Normalize a scalar/comma-separated value into a list."""

    if value is None:
        return []
    if isinstance(value, (list, tuple, set, frozenset)):
        return list(value)
    if isinstance(value, str):
        return [part.strip() for part in value.replace("\n", ",").split(",") if part.strip()]
    return [value]


def utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class ClaimType(str, Enum):
    MAIN = "main"
    SUPPORTING = "supporting"
    ENGINEERING_DETAIL = "engineering_detail"
    SPECULATION = "speculation"


class RunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    CANCELLED = "cancelled"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ComparisonStatus(str, Enum):
    MATCH = "match"
    CLOSE = "close"
    DIFFERENT = "different"
    MISSING = "missing"


T = TypeVar("T")


def _encode(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return value.to_dict()
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_encode(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _encode(v) for k, v in value.items()}
    return value


def _decode_enum(enum_type: type[Enum], value: Any) -> Any:
    if isinstance(value, enum_type):
        return value
    if isinstance(value, str):
        normalized = value.strip().casefold().replace("-", "_").replace(" ", "_")
        # Runner/UI preview files used ``completed`` for a successful run;
        # the canonical domain name is ``succeeded``.
        if enum_type is RunStatus and normalized in {"completed", "complete", "success", "successful"}:
            normalized = RunStatus.SUCCEEDED.value
        if enum_type is RunStatus and normalized in {"canceled", "cancel"}:
            normalized = RunStatus.CANCELLED.value
        value = normalized
        for member in enum_type:
            if member.name.casefold() == normalized or str(member.value).casefold() == normalized:
                return member
    try:
        return enum_type(value)
    except (TypeError, ValueError):
        return value


class Serializable:
    """Mixin giving every model stable JSON conversion helpers."""

    def to_dict(self) -> dict[str, Any]:
        return {f.name: _encode(getattr(self, f.name)) for f in fields(self)}

    # The desktop adapter and generated integrations use small mapping-like
    # accessors (``row["id"]``/``row.get("text")``).  Supporting them here
    # keeps the canonical dataclasses usable without maintaining a second set
    # of DTOs in the UI.
    def __getitem__(self, key: str) -> Any:
        if hasattr(self, key):
            return getattr(self, key)
        aliases: dict[str, Any] = {
            "id": next((name for name in ("paper_id", "claim_id", "blueprint_id", "section_id", "note_id", "run_id") if hasattr(self, name)), None),
            "name": "title" if hasattr(self, "title") else None,
            "text": "content" if hasattr(self, "content") else "claim_text" if hasattr(self, "claim_text") else "raw_text" if hasattr(self, "raw_text") else None,
            "kind": "claim_type" if hasattr(self, "claim_type") else None,
        }
        target = aliases.get(key)
        if target:
            value = getattr(self, target)
            return value.value if isinstance(value, Enum) else value
        raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default

    @classmethod
    def from_dict(cls: type[T], data: Mapping[str, Any]) -> T:
        # Most nested conversion is supplied by concrete classes.  This
        # generic implementation is useful for the small value objects.
        allowed = {f.name for f in fields(cls)}
        return cls(**{k: data[k] for k in allowed if k in data})  # type: ignore[arg-type]


@dataclass
class Section(Serializable):
    title: str
    content: str = ""
    level: int = 1
    order: int = 0
    section_id: str = field(default_factory=lambda: new_id("sec"))
    start_line: int | None = None
    end_line: int | None = None

    @property
    def name(self) -> str:
        return self.title

    @property
    def id(self) -> str:
        return self.section_id

    @property
    def text(self) -> str:
        return self.content

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Section":
        values = dict(data)
        # Accept records emitted by the compatibility UI (id/name/text) as
        # well as the canonical section_id/title/content representation.
        values.setdefault("section_id", _first_value(values, "id", "section_id", default=new_id("sec")))
        values.setdefault("title", _first_value(values, "name", "title", default="Untitled section"))
        values.setdefault("content", _first_value(values, "text", "content", "raw_text", default=""))
        values.setdefault("level", _first_value(values, "level", default=1))
        values.setdefault("order", _first_value(values, "order", default=0))
        allowed = {f.name for f in fields(cls)}
        return cls(**{key: values[key] for key in allowed if key in values})


@dataclass
class PaperMetadata(Serializable):
    title: str = "Untitled paper"
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    venue: str = ""
    abstract: str = ""
    keywords: list[str] = field(default_factory=list)
    notes: str = ""
    tags: list[str] = field(default_factory=list)
    local_path: str = ""

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PaperMetadata":
        values = dict(data)
        title = _first_value(values, "title", "name", "Title", default="Untitled paper")
        year = _first_value(values, "year", "Year", default=None)
        if isinstance(year, str):
            match = re.search(r"(?:19|20)\d{2}", year)
            year = int(match.group(0)) if match else None
        elif year is not None:
            try:
                year = int(year)
            except (TypeError, ValueError):
                year = None
        return cls(
            title=str(title or "Untitled paper"),
            authors=[str(item).strip() for item in _list_value(_first_value(values, "authors", "author", "Authors", default=[])) if str(item).strip()],
            year=year,
            venue=str(_first_value(values, "venue", "Venue", default="") or ""),
            abstract=str(_first_value(values, "abstract", "Abstract", default="") or ""),
            keywords=[str(item).strip() for item in _list_value(_first_value(values, "keywords", "keyword", "Keywords", default=[])) if str(item).strip()],
            notes=str(_first_value(values, "notes", "note", "Notes", default="") or ""),
            tags=[str(item).strip() for item in _list_value(_first_value(values, "tags", "tag", "Tags", default=[])) if str(item).strip()],
            local_path=str(_first_value(values, "local_path", "path", "Local Path", default="") or ""),
        )


@dataclass
class FormulaNote(Serializable):
    kind: str = "formula"  # formula, pseudocode, algorithm, parameter
    content: str = ""
    explanation: str = ""
    note_id: str = field(default_factory=lambda: new_id("note"))
    paper_id: str = ""

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "FormulaNote":
        values = dict(data)
        return cls(
            kind=str(_first_value(values, "kind", "type", default="formula") or "formula"),
            content=str(_first_value(values, "content", "text", "formula", "algorithm", default="") or ""),
            explanation=str(_first_value(values, "explanation", "note", "description", default="") or ""),
            note_id=str(_first_value(values, "note_id", "id", default=new_id("note"))),
            paper_id=str(_first_value(values, "paper_id", "paper", default="") or ""),
        )


@dataclass
class Claim(Serializable):
    claim_text: str
    paper_id: str = ""
    evidence: str = ""
    section: str = ""
    importance: float = 0.5
    # ``None`` means the paper has not supplied enough evidence for an
    # assessment; the extractor must never silently turn missing details into
    # a positive reproducibility judgment.
    reproducible: bool | None = None
    required_experiment: str = ""
    claim_type: ClaimType = ClaimType.SUPPORTING
    source_span: tuple[int, int] | None = None
    confidence: float = 0.5
    claim_id: str = field(default_factory=lambda: new_id("claim"))

    @property
    def text(self) -> str:
        """Compatibility alias used by table and UI code."""
        return self.claim_text

    @text.setter
    def text(self, value: str) -> None:
        self.claim_text = value

    @property
    def id(self) -> str:
        return self.claim_id

    @property
    def kind(self) -> str:
        return self.claim_type.value if isinstance(self.claim_type, Enum) else str(self.claim_type)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Claim":
        values = dict(data)
        if "claim_text" not in values:
            values["claim_text"] = _first_value(values, "text", "claim", "claim_text", default="")
        if "claim_id" not in values:
            values["claim_id"] = _first_value(values, "id", "claim_id", default=new_id("claim"))
        if "claim_type" not in values:
            values["claim_type"] = _first_value(values, "kind", "type", "claim_type", default=ClaimType.SUPPORTING)
        values["claim_type"] = _decode_enum(ClaimType, values["claim_type"])
        if "reproducible" not in values and "reproducibility" in values:
            values["reproducible"] = values["reproducibility"]
        if isinstance(values.get("source_span"), list):
            values["source_span"] = tuple(values["source_span"])
        return super(Claim, cls).from_dict(values)


@dataclass
class ExperimentBlueprint(Serializable):
    paper_id: str = ""
    claim_id: str = ""
    research_question: str = ""
    hypothesis: str = ""
    independent_variables: list[str] = field(default_factory=list)
    dependent_variables: list[str] = field(default_factory=list)
    controls: list[str] = field(default_factory=list)
    dataset: str = "Not specified"
    model: str = "Not specified"
    baseline: str = "Ablation or simple baseline"
    metrics: list[str] = field(default_factory=lambda: ["accuracy"])
    expected_result: str = ""
    implementation_plan: list[str] = field(default_factory=list)
    paper_reported_result: dict[str, Any] = field(default_factory=dict)
    local_result: dict[str, Any] = field(default_factory=dict)
    difference: dict[str, Any] = field(default_factory=dict)
    status: str = "draft"
    notes: str = ""
    blueprint_id: str = field(default_factory=lambda: new_id("bp"))
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    @property
    def id(self) -> str:
        return self.blueprint_id

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ExperimentBlueprint":
        values = dict(data)
        if "blueprint_id" not in values:
            values["blueprint_id"] = _first_value(values, "id", "blueprint_id", default=new_id("bp"))
        # ``plan`` was used by an early JSON export; preserve it when loading.
        if "implementation_plan" not in values and "plan" in values:
            values["implementation_plan"] = values["plan"]
        if "metrics" in values and isinstance(values["metrics"], Mapping):
            values["metrics"] = [
                str(item.get("name", item.get("metric", key))) if isinstance(item, Mapping) else str(key)
                for key, item in values["metrics"].items()
            ]
        return super(ExperimentBlueprint, cls).from_dict(values)


@dataclass
class MetricComparison(Serializable):
    metric: str
    paper_result: Any = None
    local_result: Any = None
    absolute_difference: float | None = None
    relative_difference: float | None = None
    status: ComparisonStatus = ComparisonStatus.MISSING

    @property
    def paper_value(self) -> Any:
        return self.paper_result

    @property
    def local_value(self) -> Any:
        return self.local_result

    @property
    def display_status(self) -> str:
        return {
            ComparisonStatus.MATCH: "MATCH",
            ComparisonStatus.CLOSE: "CLOSE",
            ComparisonStatus.DIFFERENT: "DIFFERENT",
            ComparisonStatus.MISSING: "MISSING",
        }.get(self.status, "UNCOMPARABLE")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "MetricComparison":
        values = dict(data)
        if "paper_result" not in values:
            values["paper_result"] = _first_value(values, "paper", "paper_value", default=None)
        if "local_result" not in values:
            values["local_result"] = _first_value(values, "local", "local_value", default=None)
        if "absolute_difference" not in values:
            values["absolute_difference"] = _first_value(values, "absolute", "absolute_difference", default=None)
        if "relative_difference" not in values:
            values["relative_difference"] = _first_value(values, "relative", "relative_difference", default=None)
        if "status" in values:
            values["status"] = _decode_enum(ComparisonStatus, values["status"])
        return super(MetricComparison, cls).from_dict(values)


@dataclass
class RunRecord(Serializable):
    blueprint_id: str
    status: RunStatus = RunStatus.QUEUED
    stdout: str = ""
    stderr: str = ""
    runtime_seconds: float = 0.0
    config: dict[str, Any] = field(default_factory=dict)
    seed: int | None = None
    result: dict[str, Any] = field(default_factory=dict)
    environment: dict[str, Any] = field(default_factory=dict)
    run_id: str = field(default_factory=lambda: new_id("run"))
    started_at: str = ""
    finished_at: str = ""
    error: str = ""

    @property
    def id(self) -> str:
        return self.run_id

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RunRecord":
        values = dict(data)
        if "run_id" not in values:
            values["run_id"] = _first_value(values, "id", "run_id", default=new_id("run"))
        if "status" in values:
            values["status"] = _decode_enum(RunStatus, values["status"])
        return super(RunRecord, cls).from_dict(values)


@dataclass
class Paper(Serializable):
    metadata: PaperMetadata = field(default_factory=PaperMetadata)
    raw_text: str = ""
    source_format: str = "txt"
    sections: list[Section] = field(default_factory=list)
    claims: list[Claim] = field(default_factory=list)
    formula_notes: list[FormulaNote] = field(default_factory=list)
    blueprints: list[ExperimentBlueprint] = field(default_factory=list)
    runs: list[RunRecord] = field(default_factory=list)
    paper_id: str = field(default_factory=lambda: new_id("paper"))
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    @property
    def id(self) -> str:
        return self.paper_id

    @property
    def text(self) -> str:
        return self.raw_text

    @property
    def authors(self) -> list[str]:
        return self.metadata.authors

    @property
    def year(self) -> int | None:
        return self.metadata.year

    @property
    def venue(self) -> str:
        return self.metadata.venue

    @property
    def abstract(self) -> str:
        return self.metadata.abstract

    @property
    def keywords(self) -> list[str]:
        return self.metadata.keywords

    @property
    def notes(self) -> str:
        return self.metadata.notes

    @notes.setter
    def notes(self, value: str) -> None:
        self.metadata.notes = value

    @property
    def tags(self) -> list[str]:
        return self.metadata.tags

    @property
    def local_path(self) -> str:
        return self.metadata.local_path

    @property
    def formulas(self) -> list[FormulaNote]:
        return self.formula_notes

    @formulas.setter
    def formulas(self, values: list[Any]) -> None:
        converted: list[FormulaNote] = []
        for value in values or []:
            if isinstance(value, FormulaNote):
                converted.append(value)
            elif isinstance(value, Mapping):
                payload = dict(value)
                payload.setdefault("paper_id", self.paper_id)
                converted.append(FormulaNote.from_dict(payload))
            else:
                converted.append(FormulaNote(content=str(value), paper_id=self.paper_id))
        self.formula_notes = converted

    @property
    def title(self) -> str:
        return self.metadata.title

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Paper":
        values = dict(data)
        metadata = values.get("metadata")
        if not isinstance(metadata, Mapping):
            # Permit a flat paper record from lightweight integrations while
            # keeping the canonical nested metadata representation on save.
            metadata = values
        values["metadata"] = PaperMetadata.from_dict(metadata)
        values["paper_id"] = _first_value(values, "paper_id", "id", default=new_id("paper"))
        values["raw_text"] = _first_value(values, "raw_text", "text", "content", default="")
        values["sections"] = [Section.from_dict(v) for v in _list_value(values.get("sections", [])) if isinstance(v, Mapping)]
        values["claims"] = [Claim.from_dict(v) for v in _list_value(values.get("claims", [])) if isinstance(v, Mapping)]
        formula_values = values.get("formula_notes", values.get("formulas", []))
        values["formula_notes"] = [FormulaNote.from_dict(v) for v in _list_value(formula_values) if isinstance(v, Mapping)]
        values["blueprints"] = [ExperimentBlueprint.from_dict(v) for v in _list_value(values.get("blueprints", [])) if isinstance(v, Mapping)]
        values["runs"] = [RunRecord.from_dict(v) for v in _list_value(values.get("runs", [])) if isinstance(v, Mapping)]
        return super(Paper, cls).from_dict(values)


def model_from_dict(model_type: type[T], data: Mapping[str, Any]) -> T:
    """Public helper used by storage and integrations."""
    return model_type.from_dict(data)  # type: ignore[attr-defined]
