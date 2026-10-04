"""Evidence-based reproducibility checklist for a paper.

The scorer does not decide whether a paper's claims are true.  It only records
whether the supplied paper metadata or user-provided evidence explicitly
addresses eight reproducibility dimensions.  Missing and unclear evidence are
kept separate so a user can review the source manually.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

from ._interop import as_text, get_field, to_plain


CRITERIA: tuple[tuple[str, str], ...] = (
    ("dataset_available", "Dataset available"),
    ("code_available", "Code available"),
    ("hyperparameters_clear", "Hyperparameters clear"),
    ("model_clear", "Model clear"),
    ("metric_clear", "Metric clear"),
    ("seed_specified", "Seed specified"),
    ("environment_specified", "Environment specified"),
    ("training_procedure_clear", "Training procedure clear"),
)


@dataclass
class CriterionAssessment:
    key: str
    label: str
    state: str = "unclear"  # available, missing, unclear
    source: str = ""
    note: str = ""

    @property
    def available(self) -> bool:
        return self.state == "available"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ReproducibilityAssessment:
    score: float
    available_count: int
    total: int
    criteria: list[CriterionAssessment] = field(default_factory=list)
    caveat: str = "This is an evidence checklist, not a judgment of scientific truth."

    @property
    def percentage(self) -> float:
        return self.score

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "available_count": self.available_count,
            "total": self.total,
            "criteria": [item.to_dict() for item in self.criteria],
            "caveat": self.caveat,
        }


def _present(value: Any) -> bool:
    if value is None or value is False:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, frozenset, Mapping)):
        return bool(value)
    return True


def _explicit_state(value: Any) -> str | None:
    if isinstance(value, bool):
        return "available" if value else "missing"
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"yes", "true", "available", "provided", "clear", "specified", "included", "present"}:
            return "available"
        if lowered in {"no", "false", "missing", "unavailable", "absent", "not provided", "unclear"}:
            return "missing" if lowered != "unclear" else "unclear"
    return None


def _find(paper: Any, *names: str) -> tuple[Any, str]:
    for name in names:
        value = get_field(paper, name, default=None)
        if value is not None:
            return value, name
    return None, ""


def _heuristic_from_text(text: str, key: str) -> tuple[str, str, str] | None:
    """Return conservative evidence from explicit wording in paper text."""

    if not text:
        return None
    # These patterns identify an explicit statement in supplied text; they do
    # not verify that a URL, dataset, or seed actually works.
    patterns: dict[str, tuple[tuple[str, str], ...]] = {
        "dataset_available": ((r"dataset\s+(?:is\s+)?(?:public|available|released)", "text mentions a public/available dataset"),),
        "code_available": ((r"code\s+(?:is\s+)?(?:public|available|released)", "text mentions public/available code"), (r"github\.com|gitlab\.com|codeberg\.org", "text contains a code repository URL")),
        "hyperparameters_clear": ((r"hyperparameters?\s+(?:are\s+)?(?:listed|provided|shown|detailed)", "text says hyperparameters are provided"),),
        "model_clear": ((r"model\s+(?:architecture|configuration)\s+(?:is\s+)?(?:described|detailed|specified)", "text says model configuration is described"),),
        "metric_clear": ((r"evaluation metrics?\s+(?:are\s+)?(?:defined|specified|reported)", "text specifies evaluation metrics"),),
        "seed_specified": ((r"random seed\s*[:=]\s*\d+|seed\s*[:=]\s*\d+", "text contains an explicit seed"),),
        "environment_specified": ((r"(?:software|hardware|environment)\s+(?:configuration|setup|details)\s+(?:is\s+)?(?:provided|listed|described)", "text describes the environment"),),
        "training_procedure_clear": ((r"training procedure\s+(?:is\s+)?(?:described|detailed|specified)", "text describes the training procedure"),),
    }
    for pattern, note in patterns.get(key, ()):
        if re.search(pattern, text, flags=re.IGNORECASE):
            return "available", "paper_text", note
    return None


def assess_reproducibility(paper: Any = None, evidence: Mapping[str, Any] | None = None) -> ReproducibilityAssessment:
    """Build an eight-dimension reproducibility assessment.

    Explicit ``evidence`` overrides metadata and may contain booleans, the
    strings ``available``/``missing``/``unclear``, or a dict with ``state``,
    ``source`` and ``note``.
    """

    evidence = evidence or {}
    plain = to_plain(paper)
    text = ""
    if isinstance(plain, Mapping):
        text = "\n".join(
            as_text(plain.get(key, ""))
            for key in ("full_text", "text", "raw_text", "abstract", "notes", "method", "experiment")
        )
        sections = plain.get("sections", [])
        if isinstance(sections, (list, tuple)):
            text += "\n" + "\n".join(
                as_text(section.get("content", section.get("text", "")))
                for section in sections
                if isinstance(section, Mapping)
            )
    elif isinstance(plain, str):
        text = plain
    criteria: list[CriterionAssessment] = []
    for key, label in CRITERIA:
        supplied = evidence.get(key) if isinstance(evidence, Mapping) else None
        if isinstance(supplied, Mapping):
            state = _explicit_state(supplied.get("state")) or ("available" if supplied.get("available") is True else "missing" if supplied.get("available") is False else "unclear")
            criteria.append(CriterionAssessment(key, label, state, as_text(supplied.get("source", "user evidence")), as_text(supplied.get("note", ""))))
            continue
        state = _explicit_state(supplied)
        if state is not None:
            criteria.append(CriterionAssessment(key, label, state, "user evidence"))
            continue
        # Direct metadata aliases are accepted, but absent fields remain
        # unclear rather than being called false.
        aliases = {
            "dataset_available": ("dataset_available", "dataset_url", "dataset", "data"),
            "code_available": ("code_available", "code_url", "repository", "repo_url", "code"),
            "hyperparameters_clear": ("hyperparameters_clear", "hyperparameters", "hyperparams"),
            "model_clear": ("model_clear", "model", "model_name", "architecture"),
            "metric_clear": ("metric_clear", "metrics", "evaluation_metrics"),
            "seed_specified": ("seed_specified", "seed", "random_seed"),
            "environment_specified": ("environment_specified", "environment", "hardware", "software"),
            "training_procedure_clear": ("training_procedure_clear", "training_procedure", "training", "optimizer"),
        }
        value, source = _find(plain, *aliases[key])
        if value is not None:
            explicit = _explicit_state(value)
            state = explicit or ("available" if _present(value) else "missing")
            criteria.append(CriterionAssessment(key, label, state, source))
            continue
        heuristic = _heuristic_from_text(text, key)
        if heuristic:
            criteria.append(CriterionAssessment(key, label, heuristic[0], heuristic[1], heuristic[2]))
        else:
            criteria.append(CriterionAssessment(key, label, "unclear", "", "No explicit evidence supplied."))
    available_count = sum(item.available for item in criteria)
    total = len(criteria)
    return ReproducibilityAssessment(
        score=round(available_count / total * 100, 2) if total else 0.0,
        available_count=available_count,
        total=total,
        criteria=criteria,
    )


def score_reproducibility(paper: Any = None, evidence: Mapping[str, Any] | None = None) -> ReproducibilityAssessment:
    """Compatibility alias used by the UI and older integrations."""

    return assess_reproducibility(paper, evidence)


__all__ = ["CRITERIA", "CriterionAssessment", "ReproducibilityAssessment", "assess_reproducibility", "score_reproducibility"]
