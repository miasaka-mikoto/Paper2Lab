"""Compare local result JSON with paper-reported values."""
from __future__ import annotations

def compare(paper: dict, local: dict) -> dict:
    paper_metrics = paper.get("metrics", paper) if isinstance(paper, dict) else {}
    local_metrics = local.get("metrics", local) if isinstance(local, dict) else {}
    output = {}
    for metric, expected in paper_metrics.items():
        actual = local_metrics.get(metric)
        if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
            delta = actual - expected
            output[metric] = {"paper": expected, "local": actual, "difference": delta, "status": "match" if abs(delta) < 1e-3 else "different"}
        else:
            output[metric] = {"paper": expected, "local": actual, "status": "missing" if actual is None else "uncomparable"}
    return output
