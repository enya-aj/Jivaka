"""Rule-based identifier recognizers for Thai and English clinical text.

These catch the structured identifiers (IDs, phone numbers, emails, dates,
addresses) and names introduced by a title or a "Name:" label. Free-text
names without such a cue are left to the NER backends in ner.py.
"""

import re
from dataclasses import dataclass
from typing import Iterable

PERSON = "PERSON"
DATE = "DATE"
PHONE = "PHONE"
EMAIL = "EMAIL"
ID = "ID"
ADDRESS = "ADDRESS"
PLACE = "PLACE"


@dataclass(frozen=True)
class Span:
    start: int
    end: int
    kind: str


# Arabic or Thai digits.
_D = "[0-9๐-๙]"
_THAI = "฀-๿"

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")

# 13-digit Thai national ID, with or without the usual 1-4-5-2-1 grouping.
_THAI_ID_RE = re.compile(rf"(?<![0-9๐-๙]){_D}[ -]?{_D}{{4}}[ -]?{_D}{{5}}[ -]?{_D}{{2}}[ -]?{_D}(?![0-9๐-๙])")

_PHONE_RES = [
    # Thai mobile/landline: 0X-XXX-XXXX, 02-XXX-XXXX, +66 X XXXX XXXX.
    re.compile(rf"(?<![0-9๐-๙])(?:\+66[ -]?|0){_D}{{1,2}}[ -]?{_D}{{3}}[ -]?{_D}{{3,4}}(?![0-9๐-๙])"),
    # North-American style (555) 123-4567 / 555-123-4567.
    re.compile(r"(?<!\d)(?:\+1[ -]?)?\(?\d{3}\)?[ -]\d{3}-\d{4}(?!\d)"),
]

# Hospital numbers after a label. Short codes are matched case-sensitively so
# the English word "an" isn't taken for an admission number; every value must
# contain a digit.
_ID_VALUE = r"([A-Za-z0-9๐-๙][A-Za-z0-9๐-๙/\-]{2,})"
_ID_LABEL_RES = [
    re.compile(rf"\b(?:HN|AN|VN|EN|MRN|CID|ID)\s*(?:no\.?|#)?\s*[:：.#]?\s*{_ID_VALUE}"),
    re.compile(
        rf"(?:hospital\s+(?:no|number)|medical\s+record\s+(?:no|number)|patient\s+id|"
        rf"เลขประจำตัวผู้ป่วย|เลขบัตรประชาชน|เลขประจำตัวประชาชน|เลขที่ผู้ป่วย)"
        rf"\s*[:：.#]?\s*{_ID_VALUE}",
        re.IGNORECASE,
    ),
]

_EN_MONTH = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|"
    r"Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?"
)
_TH_MONTH = (
    r"(?:มกราคม|กุมภาพันธ์|มีนาคม|เมษายน|พฤษภาคม|มิถุนายน|กรกฎาคม|สิงหาคม|กันยายน|ตุลาคม|"
    r"พฤศจิกายน|ธันวาคม|ม\.ค\.|ก\.พ\.|มี\.ค\.|เม\.ย\.|พ\.ค\.|มิ\.ย\.|ก\.ค\.|ส\.ค\.|ก\.ย\.|"
    r"ต\.ค\.|พ\.ย\.|ธ\.ค\.)"
)
_DATE_RES = [
    # 03/04/2024, 3-4-67, 03.04.2567 (Buddhist Era years are just bigger numbers).
    re.compile(rf"(?<![0-9๐-๙]){_D}{{1,2}}[/.\-]{_D}{{1,2}}[/.\-](?:{_D}{{4}}|{_D}{{2}})(?![0-9๐-๙])"),
    re.compile(r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)"),
    re.compile(rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+{_EN_MONTH},?\s+\d{{2,4}}\b"),
    re.compile(rf"\b{_EN_MONTH}\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{2,4}}\b"),
    re.compile(rf"\b{_EN_MONTH}\s+\d{{4}}\b"),
    re.compile(
        rf"(?:{_D}{{1,2}}\s*)?{_TH_MONTH}\s*(?:(?:พ\.ศ\.|ค\.ศ\.)\s*)?{_D}{{2,4}}"
        rf"|{_D}{{1,2}}\s*{_TH_MONTH}"
    ),
]

_ADDRESS_RE = re.compile(r"(?im)(?:\baddress\s*[:：]|ที่อยู่\s*[:：]?)[ \t]*(\S[^\n]*)")

# Names after a "Name:" style label: everything to the end of the line.
_NAME_LABEL_RE = re.compile(
    r"(?im)^[ \t]*(?:patient\s+name|full\s+name|name|ชื่อ-นามสกุล|ชื่อ-สกุล|ชื่อผู้ป่วย|ชื่อ)"
    r"[ \t]*[:：][ \t]*(\S[^\n]{0,60})"
)

# English titles keep the title and redact the name after it.
_EN_TITLE_RE = re.compile(r"\b(?:Mr|Mrs|Ms|Miss|Mx|Dr|Prof)\.?\s+([A-Z][A-Za-z'\-]+(?:\s+[A-Z][A-Za-z'\-]+)?)")

# Thai titles are written straight onto the first name, with the surname
# after a space. นาย/นาง are also the start of นายแพทย์ (doctor) and
# นางพยาบาล (nurse), which are not names.
_TH_TITLE_RE = re.compile(
    rf"(?:นางสาว|เด็กชาย|เด็กหญิง|ทพญ\.|น\.ส\.|ด\.ช\.|ด\.ญ\.|นพ\.|พญ\.|ทพ\.|ภก\.|ภญ\.|พว\.|ดร\.|"
    rf"ผศ\.|รศ\.|นาย(?!แพทย์)|นาง(?!พยาบาล))[ \t]*([{_THAI}]+(?:[ \t]+[{_THAI}]+)?)"
)


def _whole(regexes: Iterable[re.Pattern], text: str, kind: str) -> list[Span]:
    return [Span(m.start(), m.end(), kind) for regex in regexes for m in regex.finditer(text)]


def _group(regexes: Iterable[re.Pattern], text: str, kind: str, require_digit: bool = False) -> list[Span]:
    spans = []
    for regex in regexes:
        for m in regex.finditer(text):
            value = m.group(1).rstrip()
            if require_digit and not re.search("[0-9๐-๙]", value):
                continue
            spans.append(Span(m.start(1), m.start(1) + len(value), kind))
    return spans


def find_pattern_spans(text: str, extra_id_patterns: Iterable[re.Pattern] = ()) -> list[Span]:
    spans: list[Span] = []
    spans += _whole([_EMAIL_RE], text, EMAIL)
    spans += _whole([_THAI_ID_RE], text, ID)
    spans += _whole(_PHONE_RES, text, PHONE)
    spans += _group(_ID_LABEL_RES, text, ID, require_digit=True)
    spans += _whole(extra_id_patterns, text, ID)
    spans += _whole(_DATE_RES, text, DATE)
    spans += _group([_ADDRESS_RE], text, ADDRESS)
    spans += _group([_NAME_LABEL_RE, _EN_TITLE_RE, _TH_TITLE_RE], text, PERSON)
    return spans
