"""Offline document import and section parsing.

PDF extraction uses an optional installed PDF reader when available and a
small, conservative text-operator fallback otherwise.  The fallback is not
presented as a full PDF implementation; it is enough for text-only sample
papers and leaves an explicit note when a scanned PDF has no extractable text.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
import html
import re
import ast
from typing import Any, Mapping

from .models import PaperMetadata, Section


SECTION_ALIASES = {
    "abstract": "Abstract",
    "summary": "Abstract",
    "introduction": "Introduction",
    "intro": "Introduction",
    "related work": "Related Work",
    "background": "Related Work",
    "method": "Method",
    "methods": "Method",
    "methodology": "Method",
    "approach": "Method",
    "model": "Method",
    "algorithm": "Method",
    "experiment": "Experiment",
    "experiments": "Experiment",
    "experimental setup": "Experiment",
    "setup": "Experiment",
    "evaluation": "Metrics",
    "experimental results": "Results",
    "dataset": "Dataset",
    "datasets": "Dataset",
    "data": "Dataset",
    "metric": "Metrics",
    "metrics": "Metrics",
    "results": "Results",
    "result": "Results",
    "ablation": "Ablation",
    "ablation study": "Ablation",
    "limitations": "Limitations",
    "limitation": "Limitations",
    "conclusion": "Conclusion",
    "conclusions": "Conclusion",
    "discussion": "Conclusion",
    "appendix": "Appendix",
    "supplementary": "Appendix",
}


def canonical_section_title(title: str) -> str | None:
    clean = re.sub(r"^[\d.\s]+", "", title.strip().lower())
    # Plain-text exports frequently spell section headings as ``Method:`` or
    # ``1. Method:``.  Treat a trailing colon as heading punctuation while
    # leaving inline prose such as ``Method: we use ...`` untouched.
    clean = re.sub(r"\s*[:：]\s*$", "", clean)
    clean = re.sub(r"\s+", " ", clean)
    return SECTION_ALIASES.get(clean)


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title: str = ""
        # Keep common citation/meta tags separate from visible body text.  A
        # surprising number of publisher HTML exports put the useful paper
        # metadata only in ``<meta>`` elements, so relying on the first body
        # line would lose authors/year/venue/keywords on import.
        self.meta: dict[str, list[str]] = {}
        self._in_title = False
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag == "meta":
            attrs_map = {str(key).lower(): (value or "").strip() for key, value in attrs}
            key = attrs_map.get("name") or attrs_map.get("property") or attrs_map.get("itemprop")
            value = attrs_map.get("content", "")
            if key and value:
                self.meta.setdefault(key.casefold(), []).append(value)
        if tag in {"script", "style", "noscript", "svg"}:
            self._skip += 1
        if tag == "title":
            self._in_title = True
        if tag in {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "svg"} and self._skip:
            self._skip -= 1
        if tag == "title":
            self._in_title = False
        if tag in {"p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        if self._in_title:
            self.title += data
            # ``<title>`` is metadata, not visible paper body content.  Do
            # not inject it into a synthetic Front Matter section or claim
            # extraction input.
            return
        self.parts.append(data)

    def handle_entityref(self, name: str) -> None:
        # ``convert_charrefs=True`` normally handles this, but keeping the
        # explicit method makes behaviour deterministic for malformed HTML.
        self.handle_data(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self.handle_data(f"&#{name};")

    def text(self) -> str:
        value = html.unescape("".join(self.parts))
        value = re.sub(r"[ \t]+", " ", value)
        value = re.sub(r"\n{3,}", "\n\n", value)
        return value.strip()


@dataclass
class ParsedDocument:
    text: str
    source_format: str
    metadata: PaperMetadata = field(default_factory=PaperMetadata)
    sections: list[Section] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def title(self) -> str:
        return self.metadata.title


class DocumentParser:
    """Parse PDF/Markdown/TXT/HTML into a common representation."""

    supported_extensions = {".pdf", ".md", ".markdown", ".txt", ".html", ".htm"}

    def parse_file(self, path: str | Path) -> ParsedDocument:
        source = Path(path)
        suffix = source.suffix.lower()
        if suffix not in self.supported_extensions:
            raise ValueError(f"Unsupported document format: {suffix or '<none>'}")
        if suffix == ".pdf":
            text, warnings = self._read_pdf(source)
            parsed = self.parse_text(text, "pdf", filename=source.name)
            parsed.warnings.extend(warnings)
        else:
            raw = source.read_text(encoding="utf-8", errors="replace")
            if suffix in {".html", ".htm"}:
                extractor = _HTMLTextExtractor()
                extractor.feed(raw)
                text = extractor.text()
                # Parse the extracted visible text once.  Feeding the plain
                # text back through the HTML parser would be harmless for
                # most files, but it used to duplicate/retain ``<title>``
                # content as a fake front-matter section.
                parsed = self.parse_text(text, "html", filename=source.name)
                # The document title is authoritative metadata even when the
                # visible body starts with a paragraph (the generic metadata
                # heuristic would otherwise mistake that paragraph for the
                # paper title).  A visible H1 can still be edited later.
                self._apply_html_metadata(parsed.metadata, extractor.title, extractor.meta)
            else:
                fmt = "markdown" if suffix in {".md", ".markdown"} else "txt"
                parsed = self.parse_text(raw, fmt, filename=source.name)
        parsed.metadata.local_path = str(source.resolve())
        return parsed

    def parse_text(self, text: str, source_format: str = "txt", filename: str = "") -> ParsedDocument:
        # Callers in plugins commonly pass ``.md``/``MD`` or ``.HTML``.  A
        # normalized format keeps heading detection consistent while retaining
        # a friendly canonical value in the returned document.
        source_format = (source_format or "txt").strip().lower().lstrip(".")
        if source_format in {"md", "markdown"}:
            source_format = "markdown"
        elif source_format in {"htm", "html"}:
            source_format = "html"
        elif source_format == "pdf":
            source_format = "pdf"
        else:
            source_format = "txt"
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        html_title = ""
        html_meta: dict[str, list[str]] = {}
        if source_format == "html":
            extractor = _HTMLTextExtractor()
            extractor.feed(normalized)
            normalized = extractor.text()
            html_title = re.sub(r"\s+", " ", extractor.title).strip()
            html_meta = extractor.meta
        sections = self._parse_sections(normalized, source_format)
        metadata = self._extract_metadata(normalized, sections, filename)
        if source_format == "html":
            self._apply_html_metadata(metadata, html_title, html_meta)
        abstract = next((s.content.strip() for s in sections if s.title == "Abstract"), "")
        if abstract:
            metadata.abstract = abstract
        return ParsedDocument(normalized, source_format, metadata, sections)

    @staticmethod
    def _apply_html_metadata(metadata: PaperMetadata, title: str = "", meta: Mapping[str, list[str]] | None = None) -> None:
        """Merge common HTML citation metadata into :class:`PaperMetadata`.

        The merge is deliberately conservative: explicit ``<title>``/meta
        values win over weak first-line heuristics, while a parsed Abstract
        section is applied by :meth:`parse_text` afterwards and therefore
        remains authoritative for the abstract body.
        """

        values = meta or {}

        def first(*keys: str) -> str:
            for key in keys:
                candidates = values.get(key.casefold(), [])
                for candidate in candidates:
                    clean = re.sub(r"\s+", " ", str(candidate)).strip()
                    if clean:
                        return clean
            return ""

        explicit_title = re.sub(r"\s+", " ", title or "").strip() or first(
            "citation_title", "dc.title", "dcterms.title", "og:title", "title"
        )
        if explicit_title:
            metadata.title = explicit_title

        authors: list[str] = []
        for key in ("citation_author", "author", "dc.creator", "dcterms.creator"):
            for value in values.get(key, []):
                clean = re.sub(r"\s+", " ", str(value)).strip()
                if clean and clean not in authors:
                    authors.append(clean)
            if authors:
                break
        if authors:
            metadata.authors = authors

        year_text = first(
            "citation_publication_date", "citation_date", "date", "dc.date", "dcterms.issued", "publication_date"
        )
        if year_text:
            match = re.search(r"\b(19\d{2}|20\d{2})\b", year_text)
            if match:
                metadata.year = int(match.group(1))

        venue = first(
            "citation_journal_title", "citation_conference_title", "citation_book_title", "dc.source", "dcterms.source", "venue"
        )
        if venue:
            metadata.venue = venue

        keywords: list[str] = []
        for key in ("keywords", "citation_keywords", "dc.subject", "dcterms.subject"):
            for value in values.get(key, []):
                keywords.extend(part.strip() for part in re.split(r",|;", str(value)) if part.strip())
            if keywords:
                break
        if keywords:
            metadata.keywords = list(dict.fromkeys(keywords))

        abstract = first("citation_abstract", "description", "og:description", "dc.description", "dcterms.description")
        if abstract and not metadata.abstract:
            metadata.abstract = abstract

    def _read_pdf(self, path: Path) -> tuple[str, list[str]]:
        warnings: list[str] = []
        # Optional readers are used only when already installed; no network or
        # package installation is attempted.
        for module_name in ("pypdf", "PyPDF2"):
            try:
                module = __import__(module_name)
                reader = module.PdfReader(str(path))
                pages = [(page.extract_text() or "") for page in reader.pages]
                text = "\n\n".join(pages)
                if text.strip():
                    return text, warnings
            except Exception:
                continue
        # PyMuPDF exposes a different import name (``fitz``).  Keep it as a
        # second optional reader so installations that already have PyMuPDF
        # get proper page extraction before the conservative byte fallback;
        # this never installs a dependency or contacts a remote service.
        try:
            fitz = __import__("fitz")  # type: ignore

            document = fitz.open(str(path))
            try:
                pages = [page.get_text("text") or "" for page in document]
            finally:
                document.close()
            text = "\n\n".join(pages)
            if text.strip():
                return text, warnings
        except Exception:
            pass
        data = path.read_bytes()
        # Basic extraction of literal strings in PDF text operators.  It is
        # intentionally conservative and does not claim OCR support.
        chunks: list[str] = []

        def decode_literal(value: bytes) -> str:
            # PDF literal strings use backslash escapes (including octal
            # bytes); handling these here makes the fallback useful for
            # simple text-only PDFs when pypdf is unavailable.
            out = bytearray()
            index = 0
            escapes = {ord("n"): 10, ord("r"): 13, ord("t"): 9, ord("b"): 8, ord("f"): 12}
            while index < len(value):
                byte = value[index]
                if byte != 92:  # backslash
                    out.append(byte)
                    index += 1
                    continue
                index += 1
                if index >= len(value):
                    break
                escaped = value[index]
                if escaped in escapes:
                    out.append(escapes[escaped])
                    index += 1
                elif escaped in (40, 41, 92):  # (), \
                    out.append(escaped)
                    index += 1
                elif 48 <= escaped <= 55:
                    digits = bytes([escaped])
                    index += 1
                    for _ in range(2):
                        if index < len(value) and 48 <= value[index] <= 55:
                            digits += bytes([value[index]])
                            index += 1
                        else:
                            break
                    try:
                        out.append(int(digits, 8) & 0xFF)
                    except ValueError:
                        out.extend(digits)
                elif escaped in (10, 13):
                    # A backslash followed by a line break is a continuation.
                    if escaped == 13 and index + 1 < len(value) and value[index + 1] == 10:
                        index += 1
                    index += 1
                else:
                    out.append(escaped)
                    index += 1
            try:
                return bytes(out).decode("utf-8")
            except UnicodeDecodeError:
                return bytes(out).decode("latin-1", errors="replace")

        # ``Tj`` and apostrophe operators contain one literal string.
        for match in re.finditer(rb"\(((?:\\.|[^()])*)\)\s*(?:Tj|')", data):
            chunks.append(decode_literal(match.group(1)))
        # ``TJ`` contains an array of literals and spacing numbers.  Keep
        # only string operands and join them without trying to infer layout.
        for match in re.finditer(rb"\[((?:\\.|[^\]])*)\]\s*TJ", data, re.I):
            chunks.extend(decode_literal(item.group(1)) for item in re.finditer(rb"\(((?:\\.|[^()])*)\)", match.group(1)))
        # A small hex-string fallback covers producers that emit ``<...> Tj``.
        for match in re.finditer(rb"<([0-9A-Fa-f\s]+)>\s*(?:Tj|')", data):
            compact = re.sub(rb"\s+", b"", match.group(1))
            if len(compact) % 2:
                compact += b"0"
            try:
                chunks.append(bytes.fromhex(compact.decode("ascii")).decode("utf-8", errors="replace"))
            except (ValueError, UnicodeDecodeError):
                continue
        text = "\n".join(chunks)
        if not text.strip():
            warnings.append("No text layer found; scanned/image-only PDF may require OCR.")
        else:
            warnings.append("Used lightweight PDF text fallback; verify section boundaries.")
        return text, warnings

    def _parse_sections(self, text: str, source_format: str) -> list[Section]:
        lines = text.splitlines()
        headings: list[tuple[int, int, str]] = []
        inline_bodies: dict[int, str] = {}
        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                continue
            # Lightweight PDF text extraction can preserve a Markdown-style
            # hash heading (for example when a legal text-only fixture was
            # authored from Markdown before PDF conversion).  Treat those
            # markers like Markdown headings in PDF mode as well; ordinary
            # PDF/plain headings continue through the canonical-name regex
            # below.
            match = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", stripped) if source_format in {"markdown", "md", "pdf"} else None
            if match:
                heading_title = match.group(2).strip()
                # A leading Markdown H1 is normally the document title, not
                # an experiment section.  Metadata extraction retains it.
                first_content = next((n for n, value in enumerate(lines) if value.strip()), 0)
                if (i == first_content or not headings) and len(match.group(1)) == 1 and canonical_section_title(heading_title) is None:
                    continue
                headings.append((i, len(match.group(1)), heading_title))
                continue
            # TXT/HTML exports often put the first sentence on the same line
            # as a heading (Abstract: ...). Split only when the prefix is a
            # known canonical section name; ordinary metadata such as
            # Title: ... remains front matter.
            inline = re.match(
                r"^(?:\d+(?:\.\d+)*[.)]?\s+)?(.+?)\s*[:：]\s+(.+?)\s*$",
                stripped,
            )
            if inline and canonical_section_title(inline.group(1)):
                heading_title = inline.group(1).strip()
                headings.append((i, 1, heading_title))
                inline_bodies[i] = inline.group(2).strip()
                continue
            # HTML has already become text; detect common numbered headings.
            if re.match(r"^(?:\d+(?:\.\d+)*[.)]?\s+)?[A-Z][A-Za-z][A-Za-z &/\-]{1,70}\s*[:：]?$", stripped):
                canonical = canonical_section_title(stripped)
                if canonical:
                    level = 1 if not re.match(r"^\d+\.\d+", stripped) else 2
                    headings.append((i, level, stripped))
            # Setext Markdown headings.
            if i > 0 and re.match(r"^\s*(=+|-+)\s*$", stripped) and lines[i - 1].strip():
                prev = lines[i - 1].strip()
                headings.append((i - 1, 1 if stripped.startswith("=") else 2, prev))
        # Deduplicate headings introduced by multiple heuristics.
        unique: list[tuple[int, int, str]] = []
        seen: set[tuple[int, str]] = set()
        for item in sorted(headings, key=lambda x: (x[0], x[1])):
            key = (item[0], item[2].lower())
            if key not in seen:
                unique.append(item)
                seen.add(key)
        if not unique:
            content = text.strip()
            return [Section("Full Text", content, 1, 0, start_line=1, end_line=len(lines))] if content else []
        sections: list[Section] = []
        for index, (line_no, level, title) in enumerate(unique):
            end = unique[index + 1][0] if index + 1 < len(unique) else len(lines)
            body_start = line_no + 1
            # Setext underline is not content.
            if body_start < len(lines) and re.match(r"^\s*(=+|-+)\s*$", lines[body_start]):
                body_start += 1
            content = "\n".join(lines[body_start:end]).strip()
            if line_no in inline_bodies:
                content = "\n".join(part for part in (inline_bodies[line_no], content) if part).strip()
            canonical = canonical_section_title(title) or title.strip().rstrip(".")
            sections.append(Section(canonical, content, level, index, start_line=line_no + 1, end_line=end))
        # Prefix material before first heading is retained as a synthetic
        # front-matter section, which keeps evidence traceable.
        if unique[0][0] > 0:
            prefix = "\n".join(lines[: unique[0][0]]).strip()
            if prefix:
                sections.insert(0, Section("Front Matter", prefix, 1, 0, start_line=1, end_line=unique[0][0]))
                for n, section in enumerate(sections):
                    section.order = n
        return sections

    def _extract_metadata(self, text: str, sections: list[Section], filename: str) -> PaperMetadata:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        title = ""
        explicit_title = ""
        for line in lines[:20]:
            match = re.match(r"title\s*:\s*(.+)$", line, re.I)
            if match:
                explicit_title = match.group(1).strip().strip('"\'')
                break
        if explicit_title:
            title = explicit_title
        else:
            # Do not mistake ``# Abstract`` (or the first sentence after an
            # Abstract heading in TXT/HTML) for the paper title.  Keep title
            # candidates strictly in front matter once a section heading is
            # encountered.
            in_body = False
            for line in lines[:20]:
                candidate = re.sub(r"^#+\s*", "", line).strip() if line.startswith("#") else line
                if canonical_section_title(candidate):
                    in_body = True
                    continue
                inline_heading = re.match(
                    r"^(?:\d+(?:\.\d+)*[.)]?\s+)?(.+?)\s*[:：]\s+.+$",
                    candidate,
                )
                if inline_heading and canonical_section_title(inline_heading.group(1)):
                    in_body = True
                    continue
                if in_body:
                    continue
                if line.startswith("#"):
                    if len(candidate) >= 4:
                        title = candidate
                        break
                if re.match(r"(?:authors?|by|year|venue|keywords?|abstract|title)\s*:", line, re.I):
                    continue
                if len(candidate) >= 4:
                    title = candidate
                    break
        if not title and filename:
            title = Path(filename).stem.replace("_", " ").replace("-", " ").strip()
        authors: list[str] = []
        venue = ""
        explicit_year: int | None = None
        explicit_abstract = ""
        for line in lines[:20]:
            m = re.match(r"(?:authors?|by)\s*:\s*(.+)$", line, re.I)
            if m:
                raw_authors = m.group(1).strip()
                try:
                    parsed_authors = ast.literal_eval(raw_authors)
                except (ValueError, SyntaxError):
                    parsed_authors = None
                if isinstance(parsed_authors, (list, tuple)):
                    authors = [str(a).strip() for a in parsed_authors if str(a).strip()]
                else:
                    authors = [a.strip() for a in re.split(r",|;|\band\b", raw_authors) if a.strip()]
                break
        for line in lines[:30]:
            m = re.match(r"venue\s*:\s*(.+)$", line, re.I)
            if m:
                venue = m.group(1).strip().strip('"\'')
                break
        for line in lines[:30]:
            m = re.match(r"abstract\s*:\s*(.+)$", line, re.I)
            if m:
                explicit_abstract = m.group(1).strip()
                break
        for line in lines[:30]:
            m = re.match(r"(?:publication\s+)?year\s*:\s*(.+)$", line, re.I)
            if m:
                year_match = re.search(r"\b(19\d{2}|20\d{2})\b", m.group(1))
                if year_match:
                    explicit_year = int(year_match.group(1))
                    break
        year_match = re.search(r"\b(19\d{2}|20\d{2})\b", " ".join(lines[:20]))
        keywords: list[str] = []
        for line in lines[:30]:
            m = re.match(r"keywords?\s*:\s*(.+)$", line, re.I)
            if m:
                raw_keywords = m.group(1).strip()
                try:
                    parsed_keywords = ast.literal_eval(raw_keywords)
                except (ValueError, SyntaxError):
                    parsed_keywords = None
                if isinstance(parsed_keywords, (list, tuple)):
                    keywords = [str(k).strip() for k in parsed_keywords if str(k).strip()]
                else:
                    keywords = [k.strip() for k in re.split(r",|;", raw_keywords) if k.strip()]
                break
        return PaperMetadata(
            title=title or "Untitled paper",
            authors=authors,
            year=explicit_year if explicit_year is not None else (int(year_match.group(1)) if year_match else None),
            venue=venue,
            abstract=explicit_abstract,
            keywords=keywords,
        )


# Friendly aliases for integrations and earlier prototypes.
PaperParser = DocumentParser
