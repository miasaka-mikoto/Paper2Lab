"""Compare reported paper metrics with local experiment results."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping

from ._interop import get_field, to_plain


@dataclass
class MetricComparison:
    metric: str
    paper_value: Any = None
    local_value: Any = None
    absolute_difference: float | None = None
    relative_difference: float | None = None
    status: str = "UNCOMPARABLE"
    tolerance: float = 0.01
    direction: str = "higher"
    note: str = ""

    # Compatibility aliases matching ``paper2lab.models.MetricComparison``
    # and the dictionary labels used by the lightweight UI backend.
    @property
    def paper_result(self) -> Any:
        return self.paper_value

    @property
    def local_result(self) -> Any:
        return self.local_value

    @property
    def absolute(self) -> float | None:
        return self.absolute_difference

    @property
    def relative(self) -> float | None:
        return self.relative_difference

    @property
    def display_status(self) -> str:
        """Small vocabulary used in the report/UI without hiding detail."""
        return {
            "MATCH": "MATCH",
            "LOCAL_BETTER": "CLOSE",
            "LOCAL_WORSE": "DIFFERENT",
            "MISSING_PAPER": "MISSING",
            "MISSING_LOCAL": "MISSING",
        }.get(self.status, "UNCOMPARABLE")

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        # Keep both verbose model names and concise report/UI names.  The
        # duplicate values make JSON artifacts readable across old and new
        # Paper2Lab versions without requiring a migration.
        value.update(
            {
                "paper": self.paper_value,
                "local": self.local_value,
                "absolute": self.absolute_difference,
                "relative": self.relative_difference,
                "display_status": self.display_status,
            }
        )
        return value


def _value(record: Any, side: str) -> Any:
    if isinstance(record, Mapping):
        aliases = {
            "paper": ("paper", "paper_value", "reported", "reported_value", "expected"),
            "local": ("local", "local_value", "result", "value"),
        }
        for key in aliases[side]:
            if key in record:
                return record[key]
    # A plain numeric mapping value is itself the metric value on either side.
    # Structured rows are handled by the aliases above.
    return record


def _metrics(value: Any, side: str) -> dict[str, Any]:
    plain = to_plain(value)
    if plain is None:
        return {}
    if isinstance(plain, Mapping):
        # Accept one metric row as well as {metric_name: value}.
        if "name" in plain and any(k in plain for k in ("paper", "paper_value", "local", "local_value")):
            return {str(plain["name"]): _value(plain, side)}
        metadata_keys = {"status", "mode", "seed", "notes", "runtime_seconds", "config", "environment", "source", "stdout", "stderr"}
        return {str(k): _value(v, side) for k, v in plain.items() if str(k) not in metadata_keys}
    if isinstance(plain, list):
        result: dict[str, Any] = {}
        for i, item in enumerate(plain):
            if isinstance(item, Mapping):
                name = item.get("name", item.get("metric", item.get("id", f"metric_{i + 1}")))
                result[str(name)] = _value(item, side)
        return result
    return {}


def _numeric(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def compare_metric(
    metric: str,
    paper_value: Any,
    local_value: Any,
    *,
    tolerance: float = 0.01,
    direction: str = "higher",
) -> MetricComparison:
    """Compare one metric without inferring scientific validity.

    ``tolerance`` is relative when values have a non-zero paper value and
    absolute when the paper value is zero.  The raw absolute and relative
    differences are always retained for auditability.
    """

    direction = str(direction or "higher").lower()
    if paper_value is None:
        return MetricComparison(metric, None, local_value, status="MISSING_PAPER", tolerance=tolerance, direction=direction)
    if local_value is None:
        return MetricComparison(metric, paper_value, None, status="MISSING_LOCAL", tolerance=tolerance, direction=direction)
    if not (_numeric(paper_value) and _numeric(local_value)):
        return MetricComparison(
            metric,
            paper_value,
            local_value,
            status="UNCOMPARABLE",
            tolerance=tolerance,
            direction=direction,
            note="Values are not finite numeric values.",
        )
    paper = float(paper_value)
    local = float(local_value)
    absolute = local - paper
    relative = absolute / abs(paper) if paper != 0 else None
    distance = abs(relative) if relative is not None else abs(absolute)
    if distance <= max(0.0, float(tolerance)):
        status = "MATCH"
    else:
        better = absolute > 0 if direction not in {"lower", "min", "minimum"} else absolute < 0
        status = "LOCAL_BETTER" if better else "LOCAL_WORSE"
    return MetricComparison(
        metric=metric,
        paper_value=paper_value,
        local_value=local_value,
        absolute_difference=round(absolute, 10),
        relative_difference=round(relative, 10) if relative is not None else None,
        status=status,
        tolerance=tolerance,
        direction=direction,
    )


def compare_results(
    paper_results: Any,
    local_results: Any,
    *,
    tolerances: Mapping[str, float] | None = None,
    directions: Mapping[str, str] | None = None,
    tolerance: float = 0.01,
    direction: str = "higher",
) -> list[MetricComparison]:
    """Compare all metric names found on either side.

    Inputs may be dictionaries, lists of metric records, result objects with a
    ``metrics`` field, or a single metric record.
    """

    def unwrap(value: Any) -> Any:
        candidate = get_field(value, "metrics", "results", default=None)
        return candidate if candidate is not None else value

    paper = _metrics(unwrap(paper_results), "paper")
    local = _metrics(unwrap(local_results), "local")
    names = list(dict.fromkeys([*paper.keys(), *local.keys()]))
    tolerances = tolerances or {}
    directions = directions or {}
    return [
        compare_metric(
            name,
            paper.get(name),
            local.get(name),
            tolerance=float(tolerances.get(name, tolerance)),
            direction=directions.get(name, direction),
        )
        for name in names
    ]


def comparison_summary(comparisons: Iterable[MetricComparison | Mapping[str, Any]]) -> dict[str, Any]:
    rows = [to_plain(row) for row in comparisons]
    statuses = [str(row.get("status", "UNCOMPARABLE")) for row in rows if isinstance(row, Mapping)]
    comparable = [s for s in statuses if s not in {"MISSING_PAPER", "MISSING_LOCAL", "UNCOMPARABLE"}]
    matches = sum(s == "MATCH" for s in statuses)
    return {
        "total": len(statuses),
        "matches": matches,
        "comparable": len(comparable),
        "status_counts": {status: statuses.count(status) for status in sorted(set(statuses))},
        "match_rate": matches / len(comparable) if comparable else None,
    }


__all__ = ["MetricComparison", "compare_metric", "compare_results", "comparison_summary"]
