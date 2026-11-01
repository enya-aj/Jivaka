"""Readers that turn structured or marked-up text files (HTML, XML, JSON,
Markdown, CSV/TSV) into clean plain text for the chunkers.

Every reader returns text whose paragraphs are separated by blank lines,
since that is what the chunkers split on. Structured data (JSON objects, XML
records, CSV rows) becomes one paragraph per record, written as
"field: value" lines so the extraction LLM still sees what each value means.
"""

import csv
import io
import json
import re
import xml.etree.ElementTree as ET
from typing import Any, Iterable

from bs4 import BeautifulSoup, Comment

HTML_EXTENSIONS = {".html", ".htm", ".xhtml"}
XML_EXTENSIONS = {".xml"}
JSON_EXTENSIONS = {".json"}
JSON_LINES_EXTENSIONS = {".jsonl", ".ndjson"}
MARKDOWN_EXTENSIONS = {".md", ".markdown"}
CSV_EXTENSIONS = {".csv"}
TSV_EXTENSIONS = {".tsv"}
STRUCTURED_TEXT_EXTENSIONS = (
    HTML_EXTENSIONS
    | XML_EXTENSIONS
    | JSON_EXTENSIONS
    | JSON_LINES_EXTENSIONS
    | MARKDOWN_EXTENSIONS
    | CSV_EXTENSIONS
    | TSV_EXTENSIONS
)

# Strings longer than this read as prose rather than a field value, so they
# get a paragraph of their own instead of a "field: value" line.
_PROSE_MIN_CHARS = 200

_HORIZONTAL_WS_RE = re.compile(r"[ \t\r\f\v ]+")
_MANY_NEWLINES_RE = re.compile(r"\n{3,}")


class FileReadError(ValueError):
    """The file has a supported extension but its contents can't be parsed."""


def decode_text(raw: bytes) -> str:
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", errors="replace")
    return raw.decode("utf-8-sig", errors="replace")


def tidy(text: str) -> str:
    """Collapses runs of spaces within lines and runs of blank lines, keeping
    single blank lines as paragraph breaks."""
    lines = [_HORIZONTAL_WS_RE.sub(" ", line).strip() for line in text.split("\n")]
    return _MANY_NEWLINES_RE.sub("\n\n", "\n".join(lines)).strip()


def read_structured_text(suffix: str, raw: bytes) -> str:
    if suffix in HTML_EXTENSIONS:
        return html_to_text(raw)
    if suffix in XML_EXTENSIONS:
        return xml_to_text(raw)
    if suffix in JSON_EXTENSIONS:
        return json_to_text(raw)
    if suffix in JSON_LINES_EXTENSIONS:
        return json_lines_to_text(raw)
    if suffix in MARKDOWN_EXTENSIONS:
        return markdown_to_text(decode_text(raw))
    if suffix in CSV_EXTENSIONS:
        return delimited_to_text(decode_text(raw), delimiter=None)
    if suffix in TSV_EXTENSIONS:
        return delimited_to_text(decode_text(raw), delimiter="\t")
    raise ValueError(f"No reader for '{suffix}'")


# --- HTML -------------------------------------------------------------------

_HTML_DROP_TAGS = ["script", "style", "noscript", "template", "svg", "iframe", "nav", "footer", "form"]
_HTML_BLOCK_TAGS = [
    "address", "article", "aside", "blockquote", "caption", "dd", "details", "div",
    "dl", "dt", "figcaption", "figure", "h1", "h2", "h3", "h4", "h5", "h6", "header",
    "hr", "li", "main", "ol", "p", "pre", "section", "summary", "table", "tr", "ul",
]


def html_to_text(html: bytes | str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for comment in soup.find_all(string=lambda s: isinstance(s, Comment)):
        comment.extract()
    for tag in soup.find_all(_HTML_DROP_TAGS):
        tag.decompose()

    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    if soup.head:
        soup.head.decompose()

    for br in soup.find_all("br"):
        br.replace_with("\n")
    for cell in soup.find_all(["td", "th"]):
        cell.insert_after(" | ")
    for tag in soup.find_all(_HTML_BLOCK_TAGS):
        tag.insert_before("\n\n")
        tag.insert_after("\n\n")

    body = tidy(soup.get_text())
    # Table rows end with a dangling cell separator.
    body = re.sub(r"\s*\|\s*$", "", body, flags=re.MULTILINE)
    if title and not body.startswith(title):
        body = f"{title}\n\n{body}"
    return body.strip()


# --- XML --------------------------------------------------------------------

# Elements that hold prose: their text is kept as-is, without a "tag:" label.
_XML_PROSE_TAGS = {
    "p", "para", "paragraph", "title", "article-title", "subtitle", "caption",
    "abstract", "li", "list-item", "item", "td", "th", "label", "text",
    "summary", "description", "sentence", "note", "quote",
}


def _local_name(tag: Any) -> str:
    if not isinstance(tag, str):  # comments, processing instructions
        return ""
    return tag.rsplit("}", 1)[-1]


def _has_mixed_content(elem: ET.Element) -> bool:
    if elem.text and elem.text.strip():
        return True
    return any(child.tail and child.tail.strip() for child in elem)


def _clean_inline(text: str) -> str:
    return _HORIZONTAL_WS_RE.sub(" ", text.replace("\n", " ")).strip()


def xml_to_text(raw: bytes) -> str:
    # The stdlib parser (expat) does not fetch external entities or DTDs, so
    # an uploaded file can't pull in local files or URLs.
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise FileReadError(f"Could not parse XML: {exc}") from exc

    paragraphs: list[str] = []
    _walk_xml(root, paragraphs)
    return "\n\n".join(paragraphs)


def _walk_xml(elem: ET.Element, paragraphs: list[str]) -> None:
    name = _local_name(elem.tag)
    if not name:
        return

    # Mixed content (text with inline markup, e.g. <p>a <i>b</i> c</p>) is
    # one paragraph of prose.
    if _has_mixed_content(elem) and len(elem):
        text = _clean_inline("".join(elem.itertext()))
        if text:
            paragraphs.append(text)
        return

    children = [child for child in elem if _local_name(child.tag)]
    if not children:
        text = _clean_inline(elem.text or "")
        if text:
            is_prose = name.lower() in _XML_PROSE_TAGS or len(text) >= _PROSE_MIN_CHARS
            paragraphs.append(text if is_prose else f"{name}: {text}")
        return

    # A record: short leaf fields become one paragraph of "field: value"
    # lines; prose and nested elements follow, in document order.
    record: list[str] = []
    rest: list[ET.Element] = []
    for child in children:
        child_name = _local_name(child.tag)
        is_leaf = not any(_local_name(c.tag) for c in child)
        text = _clean_inline(child.text or "") if is_leaf else ""
        if is_leaf and text and child_name.lower() not in _XML_PROSE_TAGS and len(text) < _PROSE_MIN_CHARS:
            record.append(f"{child_name}: {text}")
        else:
            rest.append(child)
    if record:
        paragraphs.append("\n".join(record))
    for child in rest:
        _walk_xml(child, paragraphs)


# --- JSON -------------------------------------------------------------------


def json_to_text(raw: bytes) -> str:
    try:
        data = json.loads(decode_text(raw))
    except json.JSONDecodeError as exc:
        raise FileReadError(f"Could not parse JSON: {exc}") from exc
    paragraphs: list[str] = []
    _walk_json(data, "", paragraphs)
    return "\n\n".join(paragraphs)


def json_lines_to_text(raw: bytes) -> str:
    paragraphs: list[str] = []
    for line_number, line in enumerate(decode_text(raw).splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise FileReadError(f"Could not parse JSON on line {line_number}: {exc}") from exc
        _walk_json(record, "", paragraphs)
    return "\n\n".join(paragraphs)


def _is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _scalar_text(value: Any) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if value is None:
        return ""
    return _clean_inline(str(value)) if isinstance(value, str) else str(value)


def _walk_json(node: Any, label: str, paragraphs: list[str]) -> None:
    if _is_scalar(node):
        text = _scalar_text(node)
        if text:
            paragraphs.append(f"{label}: {text}" if label and len(text) < _PROSE_MIN_CHARS else text)
        return

    if isinstance(node, list):
        if all(_is_scalar(item) for item in node):
            values = [t for t in (_scalar_text(item) for item in node) if t]
            if values and all(len(v) < _PROSE_MIN_CHARS for v in values):
                paragraphs.append(f"{label}: {', '.join(values)}" if label else ", ".join(values))
            else:
                paragraphs.extend(values)
            return
        for item in node:
            _walk_json(item, label, paragraphs)
        return

    # A dict: short scalar fields form one "field: value" paragraph, headed
    # by where it sits in the file; long strings and nested values follow.
    record: list[str] = []
    deferred: list[tuple[str, Any]] = []
    for key, value in node.items():
        key = str(key)
        if _is_scalar(value):
            text = _scalar_text(value)
            if not text:
                continue
            if len(text) < _PROSE_MIN_CHARS:
                record.append(f"{key}: {text}")
            else:
                deferred.append((key, value))
        elif isinstance(value, list) and all(_is_scalar(item) for item in value):
            values = [t for t in (_scalar_text(item) for item in value) if t]
            joined = ", ".join(values)
            if values and len(joined) < _PROSE_MIN_CHARS:
                record.append(f"{key}: {joined}")
            elif values:
                deferred.append((key, value))
        else:
            deferred.append((key, value))

    if record:
        paragraphs.append("\n".join(([label] if label else []) + record))
    for key, value in deferred:
        child_label = f"{label} > {key}" if label else key
        if isinstance(value, str):
            paragraphs.append(f"{child_label}:\n{_clean_inline(value)}")
        else:
            _walk_json(value, child_label, paragraphs)


# --- Markdown ---------------------------------------------------------------

_MD_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"<!--.*?-->", re.DOTALL), ""),  # HTML comments
    (re.compile(r"^[ \t]*\[[^\]]+\]:[ \t]+\S+.*$", re.MULTILINE), ""),    # link reference definitions
    (re.compile(r"!\[([^\]]*)\]\([^)]*\)"), r"\1"),                 # images -> alt text
    (re.compile(r"\[([^\]]+)\]\([^)]*\)"), r"\1"),                  # inline links -> link text
    (re.compile(r"\[([^\]]+)\]\[[^\]]*\]"), r"\1"),                 # reference links -> link text
    (re.compile(r"<(https?://[^>]+)>"), r"\1"),                     # autolinks
    (re.compile(r"</?[A-Za-z][^>]*>"), ""),                         # inline HTML tags
    (re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+(.*?)[ \t]*#*[ \t]*$", re.MULTILINE), r"\1"),  # ATX headings
    (re.compile(r"^[ \t]{0,3}(=+|-+)[ \t]*$", re.MULTILINE), ""),         # setext underlines, rules
    (re.compile(r"^[ \t]{0,3}([*_][ \t]*){3,}$", re.MULTILINE), ""),      # *** / ___ rules
    (re.compile(r"^[ \t]{0,3}>[ \t]?", re.MULTILINE), ""),                # blockquotes
    (re.compile(r"^([ \t]*)[-*+][ \t]+\[[ xX]\][ \t]+", re.MULTILINE), r"\1"),  # task list boxes
    (re.compile(r"^([ \t]*)[-*+][ \t]+", re.MULTILINE), r"\1"),           # bullets
    (re.compile(r"^[ \t]*\|?[ \t]*:?-{3,}:?[ \t]*(\|[ \t]*:?-{3,}:?[ \t]*)*\|?[ \t]*\n?", re.MULTILINE), ""),  # table rules
    (re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1"), r"\2"),          # bold
    (re.compile(r"(?<![\w*])\*(?=\S)(.+?)(?<=\S)\*(?![\w*])"), r"\1"),  # *italic*
    (re.compile(r"(?<![\w_])_(?=\S)(.+?)(?<=\S)_(?![\w_])"), r"\1"),    # _italic_
    (re.compile(r"~~(.+?)~~"), r"\1"),                              # strikethrough
    (re.compile(r"`([^`]+)`"), r"\1"),                              # inline code
]

_MD_FRONT_MATTER_RE = re.compile(r"\A---[ \t]*\n.*?\n---[ \t]*\n", re.DOTALL)
_MD_FENCE_RE = re.compile(r"^[ \t]*(```|~~~).*$", re.MULTILINE)
_MD_TABLE_ROW_RE = re.compile(r"^[ \t]*\|(.*)\|[ \t]*$", re.MULTILINE)


def markdown_to_text(markdown: str) -> str:
    text = _MD_FRONT_MATTER_RE.sub("", markdown.replace("\r\n", "\n"))
    # Keep code block contents, drop the fence lines.
    text = _MD_FENCE_RE.sub("", text)
    for pattern, replacement in _MD_RULES:
        text = pattern.sub(replacement, text)
    text = _MD_TABLE_ROW_RE.sub(lambda m: " | ".join(c.strip() for c in m.group(1).split("|")), text)
    return tidy(text)


# --- CSV / TSV --------------------------------------------------------------


def delimited_to_text(text: str, delimiter: str | None) -> str:
    """One paragraph per row, as "column: value" lines using the header row."""
    if delimiter is None:
        try:
            delimiter = csv.Sniffer().sniff(text[:8192], delimiters=",;\t|").delimiter
        except csv.Error:
            delimiter = ","

    csv.field_size_limit(16 * 1024 * 1024)
    rows: Iterable[list[str]] = csv.reader(io.StringIO(text), delimiter=delimiter)
    header: list[str] | None = None
    paragraphs: list[str] = []
    try:
        for row in rows:
            cells = [_clean_inline(cell) for cell in row]
            if not any(cells):
                continue
            if header is None:
                header = [cell or f"column {i}" for i, cell in enumerate(cells, start=1)]
                continue
            lines = []
            for i, cell in enumerate(cells):
                if not cell:
                    continue
                column = header[i] if i < len(header) else f"column {i + 1}"
                lines.append(f"{column}: {cell}")
            if lines:
                paragraphs.append("\n".join(lines))
    except csv.Error as exc:
        raise FileReadError(f"Could not parse delimited file: {exc}") from exc
    return "\n\n".join(paragraphs)
