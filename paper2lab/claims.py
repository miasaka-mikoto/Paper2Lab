"""Deterministic claim extraction for offline development builds."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable, Sequence

from .models import Claim, ClaimType, Paper, Section
from .parser import ParsedDocument


_CLAIM_MARKERS = re.compile(
    r"\b(?:we\s+(?:show|find|demonstrate|propose|introduce|present|observe)|"
    r"our\s+(?:method|model|approach|results?)|results?\s+(?:show|indicate|demonstrate)|"
    r"achieves?|outperform(?:s|ed)?|improv(?:e|es|ed|ement)|reduces?|increases?|"
    r"significantly|state[- ]of[- ]the[- ]art|we\s+report)\b",
    re.I,
)
_SPECULATION = re.compile(r"\b(?:may|might|could|possibly|suggests?|future work|we hypothesize)\b", re.I)
_ENGINEERING = re.compile(r"\b(?:batch size|learning rate|optimizer|epochs?|implementation|hardware|parameter|seed|beam size|temperature)\b", re.I)
_RESULT_NUMBER = re.compile(r"(?:\d+(?:\.\d+)?\s*%|\b(?:accuracy|f1|f1[- ]score|bleu|rouge|loss|latency|recall|precision|auc|score)\b\s*(?:of|=|:)?\s*\d+(?:\.\d+)?)", re.I)


def _sentences(text: str) -> Iterable[tuple[int, int, str]]:
    """Yield sentence spans while preserving decimal numbers.

    A regex such as ``[^.]+\\.`` splits ``0.814`` into two fake sentences,
    which in turn corrupts evidence and reported metric extraction.  The tiny
    scanner below only treats a full stop as punctuation when it is not the
    decimal point between two digits.
    """
    start = 0
    length = len(text)
    for index, char in enumerate(text):
        boundary = char in "!?。！？"
        if char == "\n":
            # PDF/Markdown extraction often wraps one sentence over several
            # visual lines.  Treat only paragraph/list/heading boundaries as
            # sentence boundaries; a lowercase continuation is joined below.
            next_match = re.search(r"\S", text[index + 1 :])
            next_char = next_match.group(0) if next_match else ""
            line_start = index + 1
            while line_start < length and text[line_start] in " \t":
                line_start += 1
            line = text[line_start : line_start + 80]
            previous_is_newline = index > 0 and text[index - 1] == "\n"
            boundary = previous_is_newline or bool(re.match(r"(?:#{1,6}\s|\d+[.)]\s|[-*+]\s|\|)", line))
            # A blank line is a reliable paragraph boundary.  Do not split a
            # normal wrapped line merely because it happens to contain '\n'.
            if not boundary and next_char and next_char.isupper() and len(text[start:index].strip()) > 80:
                boundary = True
        if char == ".":
            prev_is_digit = index > 0 and text[index - 1].isdigit()
            next_is_digit = index + 1 < length and text[index + 1].isdigit()
            boundary = not (prev_is_digit and next_is_digit)
        if not boundary:
            continue
        end = index + 1
        raw = text[start:end]
        value = re.sub(r"\s+", " ", raw).strip()
        if value:
            # Keep offsets tied to the original text, not the normalized copy.
            leading = len(raw) - len(raw.lstrip())
            trailing = len(raw.rstrip())
            yield start + leading, start + trailing, value
        start = end
    if start < length:
        raw = text[start:]
        value = re.sub(r"\s+", " ", raw).strip()
        if value:
            leading = len(raw) - len(raw.lstrip())
            trailing = len(raw.rstrip())
            yield start + leading, start + trailing, value


def _section_for_offset(sections: Sequence[Section], offset: int, full_text: str) -> str:
    # Section line offsets are enough for UI display; when possible use body
    # search to identify the owning section without inventing offsets.
    for section in sections:
        if section.content and section.content in full_text:
            start = full_text.find(section.content)
            if start <= offset <= start + len(section.content):
                return section.title
    return sections[0].title if sections else ""


def _required_experiment(sentence: str, section: str) -> str:
    lower = sentence.lower()
    if any(token in lower for token in ("retrieval", "rag", "document", "knowledge base")):
        return "Evaluate retrieval and answer quality on the stated corpus; compare against no-retrieval baseline."
    if any(token in lower for token in ("memory", "history", "long context")):
        return "Evaluate performance with and without the proposed memory component across context lengths."
    if any(token in lower for token in ("agent", "tool", "planning")):
        return "Run the task suite with the proposed agent policy and a tool-free/simple-policy baseline."
    if section.lower() in {"results", "ablation", "experiment"}:
        return "Re-run the reported evaluation with the same dataset, metric, seed, and baseline where available."
    return "Design a controlled experiment that isolates the claimed method from the baseline."


@dataclass
class RuleBasedClaimExtractor:
    """Extract likely claims without network calls or a language model.

    This is deliberately a candidate generator.  Confidence and
    reproducibility are left explicit so the researcher can correct them.
    """

    min_length: int = 25
    max_claims: int = 100

    def extract(self, source: ParsedDocument | Paper | str, paper_id: str = "") -> list[Claim]:
        if isinstance(source, ParsedDocument):
            text, sections, pid = source.text, source.sections, paper_id
        elif isinstance(source, Paper):
            text, sections, pid = source.raw_text, source.sections, source.paper_id
            paper_id = paper_id or pid
        else:
            text, sections, pid = source, [], paper_id
        claims: list[Claim] = []
        seen: set[str] = set()
        for start, end, sentence in _sentences(text):
            if len(sentence) < self.min_length or sentence.lower() in seen:
                continue
            section = _section_for_offset(sections, start, text)
            marker = bool(_CLAIM_MARKERS.search(sentence))
            numeric = bool(_RESULT_NUMBER.search(sentence))
            # Abstract, results, conclusions, and ablations are high-value
            # regions; engineering prose is kept only if it states a concrete
            # parameter/result that a user may wish to reproduce.
            high_value_section = section.lower() in {"abstract", "results", "ablation", "conclusion", "experiment"}
            if not (marker or numeric or high_value_section):
                continue
            if _SPECULATION.search(sentence):
                claim_type, importance = ClaimType.SPECULATION, 0.3
            elif _ENGINEERING.search(sentence) and not marker:
                claim_type, importance = ClaimType.ENGINEERING_DETAIL, 0.45
            elif section.lower() in {"abstract", "conclusion"} and marker:
                claim_type, importance = ClaimType.MAIN, 0.9
            else:
                claim_type, importance = ClaimType.SUPPORTING, 0.7
            # Do not assert reproducibility when the paper does not state the
            # required details.  None is shown as “not assessed” in the UI.
            reproducible: bool | None
            # Assess evidence locally instead of marking every claim
            # reproducible merely because another section mentions a seed.
            detail_markers = sum(
                bool(pattern.search(sentence))
                for pattern in (_ENGINEERING, re.compile(r"\b(?:dataset|code|seed|hyperparameter|training)\b", re.I))
            )
            reproducible = True if detail_markers >= 2 else None
            claim = Claim(
                claim_text=sentence,
                paper_id=paper_id or pid,
                evidence=sentence,
                section=section,
                importance=importance,
                reproducible=reproducible,
                required_experiment=_required_experiment(sentence, section),
                claim_type=claim_type,
                source_span=(start, end),
                confidence=0.86 if marker else 0.62,
            )
            key = re.sub(r"\W+", " ", sentence.lower()).strip()
            if key not in seen:
                claims.append(claim)
                seen.add(key)
            if len(claims) >= self.max_claims:
                break
        return claims

    __call__ = extract


# Compatibility alias used by older integrations.
ClaimExtractor = RuleBasedClaimExtractor
RuleBasedExtractor = RuleBasedClaimExtractor


def extract_claims(source: ParsedDocument | Paper | str, paper_id: str = "") -> list[Claim]:
    return RuleBasedClaimExtractor().extract(source, paper_id=paper_id)
