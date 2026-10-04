"""Reproduction report generation for Paper2Lab.

Reports deliberately distinguish source-paper numbers from local numbers and
carry a short limitations section whenever a mock run is involved.  Both
Markdown and a dependency-free HTML rendering are supported.
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping

from ._interop import as_text, get_field, to_plain
from .compare import comparison_summary


def _rows(comparisons: Iterable[Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in comparisons:
        plain = to_plain(item)
        if isinstance(plain, Mapping):
            row = dict(plain)
            # ``compare.MetricComparison`` and the serializable domain model
            # use different long-form aliases.  Normalize them once here so
            # reports never render an apparently empty result table merely
            # because the caller supplied the other model type.
            row.setdefault("metric", row.get("name", ""))
            row.setdefault("paper_value", row.get("paper_result", row.get("paper")))
            row.setdefault("local_value", row.get("local_result", row.get("local")))
            row.setdefault("absolute_difference", row.get("absolute", row.get("difference")))
            row.setdefault("relative_difference", row.get("relative"))
            status = row.get("status", "")
            status_value = getattr(status, "value", status)
            status_text = str(status_value or "").upper()
            # Domain enum values are lower-case (match/missing/...), while
            # the comparison engine uses explicit uppercase statuses.
            row["status"] = {
                "MATCH": "MATCH",
                "CLOSE": "CLOSE",
                "DIFFERENT": "DIFFERENT",
                "LOCAL_BETTER": "LOCAL_BETTER",
                "LOCAL_WORSE": "LOCAL_WORSE",
                "MISSING": "MISSING",
                "MISSING_PAPER": "MISSING_PAPER",
                "MISSING_LOCAL": "MISSING_LOCAL",
                "UNCOMPARABLE": "UNCOMPARABLE",
            }.get(status_text, status_text or "UNCOMPARABLE")
            result.append(row)
    return result


def _json_block(value: Any, *, limit: int = 6000) -> str:
    """Return bounded, deterministic JSON/text for provenance sections."""

    plain = to_plain(value)
    if plain in (None, "", {}, []):
        return ""
    if isinstance(plain, str):
        text = plain
    else:
        try:
            text = json.dumps(plain, ensure_ascii=False, indent=2, sort_keys=True)
        except (TypeError, ValueError):
            text = str(plain)
    if len(text) > limit:
        return text[:limit] + "\n… [truncated]"
    return text


def build_markdown_report(
    paper: Any,
    blueprint: Any,
    comparisons: Iterable[Any] = (),
    *,
    run: Any | None = None,
    reproducibility: Any | None = None,
) -> str:
    title = as_text(get_field(paper, "title", default="Untitled paper"), "Untitled paper")
    blueprint_id = as_text(get_field(blueprint, "blueprint_id", "id", default="unknown"))
    rows = _rows(comparisons)
    summary = comparison_summary(rows)
    lines = [
        f"# Reproduction Report: {title}",
        "",
        "> Generated locally by Paper2Lab. A Mock run is a workflow check, not evidence that the paper was reproduced.",
        "",
        "## Reproduction Summary",
        f"- Blueprint: `{blueprint_id}`",
        f"- Comparable metrics: {summary.get('comparable', 0)} / {summary.get('total', 0)}",
        f"- Matching metrics: {summary.get('matches', 0)}",
    ]
    if run is not None:
        run_status = get_field(run, "status", default="unknown")
        run_status = getattr(run_status, "value", run_status)
        lines += [
            f"- Run status: `{as_text(run_status)}`",
            f"- Seed: `{as_text(get_field(run, 'seed', default='not recorded'))}`",
            f"- Runtime (seconds): `{as_text(get_field(run, 'runtime_seconds', default='not recorded'))}`",
        ]
    if reproducibility is not None:
        score = get_field(reproducibility, "score", default=None)
        lines.append(f"- Reproducibility evidence score: `{score if score is not None else 'not assessed'}`")
    plan = get_field(blueprint, "implementation_plan", "plan", default=[])
    if plan:
        lines += ["", "## Implementation Plan"]
        for step in plan if isinstance(plan, (list, tuple)) else [plan]:
            lines.append(f"- {as_text(step)}")
    lines += ["", "## What Was Reproduced", "- Paper ingestion, section parsing, claim review, blueprint creation, and result comparison were exercised locally."]
    mode = as_text(get_field(run, "result", default={}))
    if "mock" in mode.lower() or "synthetic" in mode.lower():
        lines.append("- A deterministic offline Mock Experiment completed; no paid API or external service was called.")
    lines += ["", "## What Was Not Reproduced", "- Any paper-specific model, dataset, checkpoint, training code, or evaluator that is marked TODO.", "", "## Differences", "", "### Paper vs Local Result"]
    if rows:
        lines += ["", "| Metric | Paper | Local | Absolute Difference | Relative Difference | Status |", "|---|---:|---:|---:|---:|---|"]
        for row in rows:
            lines.append(
                "| {metric} | {paper} | {local} | {absolute} | {relative} | {status} |".format(
                    metric=row.get("metric", ""),
                    paper=row.get("paper_value", row.get("paper_result", row.get("paper", ""))),
                    local=row.get("local_value", row.get("local_result", row.get("local", ""))),
                    absolute=row.get("absolute_difference", row.get("absolute", "")),
                    relative=row.get("relative_difference", row.get("relative", "")),
                    status={
                        "MATCH": "MATCH",
                        "CLOSE": "CLOSE",
                        "LOCAL_BETTER": "CLOSE (local better)",
                        "LOCAL_WORSE": "DIFFERENT (local worse)",
                        "DIFFERENT": "DIFFERENT",
                        "MISSING_PAPER": "MISSING (paper)",
                        "MISSING_LOCAL": "MISSING (local)",
                    }.get(str(row.get("status", "")), row.get("status", "")),
                )
            )
    else:
        lines.append("\nNo comparable numeric results are available yet.")
    lines += [
        "",
        "## Possible Causes",
        "- Dataset split, preprocessing, model implementation, seed, hardware, and metric protocol may differ.",
        "- Synthetic or mock values must not be interpreted as scientific evidence.",
        "",
        "## Limitations",
        "- Rule-based extraction proposes review candidates and does not establish that a claim is true.",
        "- Reproducibility scoring counts explicit evidence fields only; it does not judge paper quality or honesty.",
        "",
        "## Next Experiments",
        "- Replace every TODO with a documented implementation, then rerun with the paper's legal dataset and protocol.",
        "- Record environment, dependency versions, seed policy, and full stdout/stderr for each run.",
        "",
    ]
    # Preserve execution provenance in the report itself.  The project JSON
    # remains the source of truth, but a standalone Markdown/HTML report
    # should still explain exactly which run/config produced the local value.
    if run is not None:
        provenance = ["", "## Run Provenance"]
        config_text = _json_block(get_field(run, "config", default=None))
        environment_text = _json_block(get_field(run, "environment", default=None))
        result_text = _json_block(get_field(run, "result", default=None))
        if config_text:
            provenance += ["", "### Config", "```json", config_text, "```"]
        if environment_text:
            provenance += ["", "### Environment", "```json", environment_text, "```"]
        if result_text:
            provenance += ["", "### Result Payload", "```json", result_text, "```"]
        stdout = _json_block(get_field(run, "stdout", default=""))
        stderr = _json_block(get_field(run, "stderr", default=""))
        if stdout:
            provenance += ["", "### stdout", "```text", stdout, "```"]
        if stderr:
            provenance += ["", "### stderr", "```text", stderr, "```"]
        if len(provenance) > 2:
            lines.extend(provenance)
    return "\n".join(lines)


def markdown_to_html(markdown: str, *, title: str = "Paper2Lab Reproduction Report") -> str:
    """Render the report with a tiny safe Markdown subset (no dependency).

    The previous renderer accumulated ``<ul>``/``<table>`` state in a list of
    chunks and could accidentally nest lists or put a following paragraph
    inside a table.  This line-oriented renderer closes each block before a
    different block begins, while intentionally keeping the supported syntax
    small and dependency-free for frozen Windows builds.
    """

    output: list[str] = []
    list_open = False
    table_open = False
    quote_open = False
    code_open = False
    code_lines: list[str] = []

    def close_list() -> None:
        nonlocal list_open
        if list_open:
            output.append("</ul>")
            list_open = False

    def close_table() -> None:
        nonlocal table_open
        if table_open:
            output.append("</tbody></table>")
            table_open = False

    def close_quote() -> None:
        nonlocal quote_open
        if quote_open:
            output.append("</blockquote>")
            quote_open = False

    def close_code() -> None:
        nonlocal code_open, code_lines
        if code_open:
            output.append("<pre><code>" + html.escape("\n".join(code_lines)) + "</code></pre>")
            code_lines = []
            code_open = False

    for raw in markdown.splitlines():
        line = raw.rstrip()
        if line.startswith("```"):
            if code_open:
                close_code()
            else:
                close_list()
                close_table()
                close_quote()
                code_open = True
            continue
        if code_open:
            code_lines.append(line)
            continue
        if not line.strip():
            # Blank lines delimit blocks.  Delaying paragraph creation keeps
            # the output compact while guaranteeing valid nesting.
            close_list()
            close_table()
            close_quote()
            continue

        if line.startswith("|"):
            close_list()
            close_quote()
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if all(set(c) <= {"-", ":"} for c in cells):
                continue
            if not table_open:
                output.append(
                    "<table><thead><tr>"
                    + "".join(f"<th>{html.escape(c)}</th>" for c in cells)
                    + "</tr></thead><tbody>"
                )
                table_open = True
            else:
                output.append("<tr>" + "".join(f"<td>{html.escape(c)}</td>" for c in cells) + "</tr>")
            continue

        close_table()
        if line.startswith(">"):
            close_list()
            if not quote_open:
                output.append("<blockquote>")
                quote_open = True
            output.append(f"<p>{html.escape(line[1:].lstrip())}</p>")
            continue
        close_quote()

        heading = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if heading:
            close_list()
            level = min(len(heading.group(1)), 6)
            output.append(f"<h{level}>{html.escape(heading.group(2))}</h{level}>")
            continue
        if line.startswith("- "):
            if not list_open:
                output.append("<ul>")
                list_open = True
            output.append(f"<li>{html.escape(line[2:].strip())}</li>")
            continue

        close_list()
        output.append(f"<p>{html.escape(line)}</p>")

    close_list()
    close_table()
    close_quote()
    close_code()
    body = "\n".join(output)
    return "<!doctype html><html><head><meta charset='utf-8'><title>{}</title><style>body{{font-family:Segoe UI,Arial,sans-serif;max-width:980px;margin:40px auto;line-height:1.55;color:#1f2937}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #d1d5db;padding:6px;text-align:left}}th{{background:#f3f4f6}}blockquote{{background:#f9fafb;padding:10px;border-left:4px solid #94a3b8}}</style></head><body>{}</body></html>".format(html.escape(title), body)


def write_report(output_dir: str | Path, paper: Any, blueprint: Any, comparisons: Iterable[Any] = (), *, run: Any | None = None, reproducibility: Any | None = None, basename: str = "reproduction_report") -> dict[str, str]:
    target = Path(output_dir).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    markdown = build_markdown_report(paper, blueprint, comparisons, run=run, reproducibility=reproducibility)
    title = as_text(get_field(paper, "title", default="Paper2Lab"), "Paper2Lab")
    md_path = target / f"{basename}.md"
    html_path = target / f"{basename}.html"
    md_path.write_text(markdown, encoding="utf-8")
    html_path.write_text(markdown_to_html(markdown, title=title), encoding="utf-8")
    return {"markdown": str(md_path), "html": str(html_path)}


def _unpack_report_data(data: Any, **kwargs: Any) -> tuple[Any, Any, Any, Any, Any]:
    """Accept either explicit arguments or a report-data mapping.

    This adapter keeps the concise ``generate_*_report(data)`` API available
    to future UI code while retaining the richer, typed ``build_*`` API.
    """

    plain = to_plain(data)
    if isinstance(plain, Mapping) and any(k in plain for k in ("paper", "blueprint", "comparisons", "runs", "reproducibility")):
        paper = plain.get("paper", {})
        blueprint = plain.get("blueprint", (plain.get("blueprints") or [{}])[0] if plain.get("blueprints") else {})
        comparisons = plain.get("comparisons", [])
        run = plain.get("run", (plain.get("runs") or [None])[0])
        reproducibility = plain.get("reproducibility")
        return paper, blueprint, comparisons, run, reproducibility
    return data, kwargs.pop("blueprint", {}), kwargs.pop("comparisons", []), kwargs.pop("run", None), kwargs.pop("reproducibility", None)


def generate_markdown_report(data: Any = None, *args: Any, output_path: str | Path | None = None, **kwargs: Any) -> str:
    """Compatibility façade returning a Markdown reproduction report."""

    if data is None and "paper" in kwargs:
        # Keyword-oriented calls mirror the long-form builder signature.
        data = kwargs.pop("paper")
        if "blueprint" not in kwargs and "blueprints" in kwargs:
            kwargs["blueprint"] = (kwargs.get("blueprints") or [{}])[0]
    if args:
        blueprint = args[0]
        comparisons = args[1] if len(args) > 1 else kwargs.pop("comparisons", [])
        run = args[2] if len(args) > 2 else kwargs.pop("run", None)
        reproducibility = args[3] if len(args) > 3 else kwargs.pop("reproducibility", None)
        paper = data
    else:
        paper, blueprint, comparisons, run, reproducibility = _unpack_report_data(data, **kwargs)
    report = build_markdown_report(paper, blueprint, comparisons, run=run, reproducibility=reproducibility)
    if output_path:
        Path(output_path).write_text(report, encoding="utf-8")
    return report


def generate_html_report(data: Any = None, *args: Any, output_path: str | Path | None = None, **kwargs: Any) -> str:
    """Compatibility façade returning a self-contained HTML report."""

    markdown = generate_markdown_report(data, *args, **kwargs)
    plain_data = to_plain(data)
    if isinstance(plain_data, Mapping) and isinstance(plain_data.get("paper"), Mapping):
        plain_data = plain_data["paper"]
    title = as_text(get_field(plain_data, "title", default="Paper2Lab Reproduction Report"), "Paper2Lab Reproduction Report")
    report = markdown_to_html(markdown, title=title)
    if output_path:
        Path(output_path).write_text(report, encoding="utf-8")
    return report


class ReportGenerator:
    """Object-oriented façade for desktop integrations."""

    def generate(self, data: Any, *, format: str = "markdown", output_path: str | Path | None = None, **kwargs: Any) -> str:
        if format.lower() in {"html", "htm"}:
            return generate_html_report(data, output_path=output_path, **kwargs)
        return generate_markdown_report(data, output_path=output_path, **kwargs)


__all__ = [
    "build_markdown_report",
    "markdown_to_html",
    "write_report",
    "generate_markdown_report",
    "generate_html_report",
    "ReportGenerator",
]
