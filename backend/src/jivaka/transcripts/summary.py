"""Summaries of doctor-patient conversation transcripts: a SOAP note plus a
short plain-language summary, written by the local Ollama model from the
de-identified transcript.

Long transcripts are summarized in two steps: the key clinical points of
each part first, then the SOAP note from all of those points, so no single
prompt outgrows the model's context window."""

import json
import logging
import time
from typing import Optional

from falkordb import Graph

from jivaka.ingestion.extraction.llm_client import OllamaClient

logger = logging.getLogger(__name__)

# Characters of transcript per prompt. llama3-class models have an 8k-token
# window; this leaves room for the instructions and the answer, including
# Thai, which takes more tokens per character than English.
PART_CHARS = 6000

SOAP_FIELDS = ("subjective", "objective", "assessment", "plan")

_LANGUAGE_RULE = (
    "Write in the language the conversation mostly uses (Thai or English). "
    "Keep drug names, doses and medical terms as spoken, and add the English "
    "term in brackets after a Thai one when that helps a clinician."
)

SUMMARY_PROMPT = """You are a clinical documentation assistant. Below is a
de-identified transcript of a conversation between a clinician and a
patient. Names and identifiers have been replaced with placeholders such as
[NAME] or [ID]; keep them as they are and never guess real ones.

Write a SOAP note and a short plain-language summary. Use only what was
said. If something was not discussed, write "Not discussed" (or "ไม่ได้กล่าวถึง"
in Thai) instead of guessing. {language_rule}

Reply with JSON only, in exactly this shape:
{{"language": "th" or "en",
  "subjective": "symptoms, history and concerns the patient reported",
  "objective": "examination findings, vital signs, test results mentioned",
  "assessment": "the clinician's impression or diagnoses discussed",
  "plan": "treatments, prescriptions, tests, advice and follow-up agreed",
  "plain_summary": "3 to 5 sentences a patient could understand"}}

Transcript:
{transcript}
"""

POINTS_PROMPT = """Below is part {part} of {parts} of a de-identified
transcript of a clinician-patient conversation. List the clinically
relevant points from this part only: symptoms, history, findings, test
results, diagnoses, medications, advice and follow-up. Keep placeholders
such as [NAME] as they are. {language_rule}

Reply with JSON only: {{"points": ["...", "..."]}}

Transcript part:
{transcript}
"""

SOAP_FROM_POINTS_PROMPT = """Below are the clinically relevant points from a
long, de-identified clinician-patient conversation, in the order they came
up. Write a SOAP note and a short plain-language summary from them. Use only
these points; write "Not discussed" (or "ไม่ได้กล่าวถึง" in Thai) for anything
missing. {language_rule}

Reply with JSON only, in exactly this shape:
{{"language": "th" or "en", "subjective": "...", "objective": "...",
  "assessment": "...", "plan": "...", "plain_summary": "..."}}

Points:
{points}
"""


def _split(text: str, size: int) -> list[str]:
    """Pieces of about `size` characters, cut at line breaks."""
    parts: list[str] = []
    current: list[str] = []
    length = 0
    for line in text.splitlines():
        if current and length + len(line) > size:
            parts.append("\n".join(current))
            current, length = [], 0
        current.append(line)
        length += len(line) + 1
    if current:
        parts.append("\n".join(current))
    return [p for p in parts if p.strip()]


def _as_text(value) -> str:
    if isinstance(value, list):
        return "\n".join(f"- {_as_text(v)}" for v in value)
    if isinstance(value, dict):
        return "; ".join(f"{k}: {_as_text(v)}" for k, v in value.items())
    return str(value or "").strip()


def _clean(raw: dict) -> dict:
    summary = {field: _as_text(raw.get(field)) for field in (*SOAP_FIELDS, "plain_summary")}
    language = str(raw.get("language") or "").lower()
    summary["language"] = "th" if language.startswith("th") else "en"
    return summary


def summarize_transcript(text: str, llm: Optional[OllamaClient] = None) -> dict:
    """SOAP note + plain summary of a (de-identified) transcript."""
    llm = llm or OllamaClient()
    parts = _split(text, PART_CHARS)
    if not parts:
        raise ValueError("The transcript is empty.")
    if len(parts) == 1:
        raw = llm.generate_json(SUMMARY_PROMPT.format(language_rule=_LANGUAGE_RULE, transcript=parts[0]))
        return _clean(raw if isinstance(raw, dict) else {})

    points: list[str] = []
    for number, part in enumerate(parts, start=1):
        raw = llm.generate_json(
            POINTS_PROMPT.format(part=number, parts=len(parts), language_rule=_LANGUAGE_RULE, transcript=part)
        )
        found = raw.get("points", []) if isinstance(raw, dict) else []
        points.extend(_as_text(p) for p in found if _as_text(p))
    raw = llm.generate_json(
        SOAP_FROM_POINTS_PROMPT.format(
            language_rule=_LANGUAGE_RULE, points="\n".join(f"- {p}" for p in points) or "(none)"
        )
    )
    return _clean(raw if isinstance(raw, dict) else {})


def transcript_text(graph: Graph, doc_id: str) -> str:
    """The de-identified transcript, rebuilt from its chunks in order."""
    rows = graph.query(
        "MATCH (:Document {id: $id})-[:HAS_CHUNK]->(c:Chunk) WHERE c.deidentified = true "
        "RETURN c.text ORDER BY c.order",
        params={"id": doc_id},
    ).result_set
    return "\n".join(row[0] for row in rows)


def summarize_document(graph: Graph, doc_id: str, llm: Optional[OllamaClient] = None) -> dict:
    """Summarizes a transcript document and stores the result on it as
    `summary` (JSON text), with `summary_status` done or failed."""
    llm = llm or OllamaClient()
    graph.query(
        "MATCH (d:Document {id: $id}) SET d.summary_status = 'running'", params={"id": doc_id}
    )
    try:
        summary = summarize_transcript(transcript_text(graph, doc_id), llm)
    except Exception as exc:
        logger.exception("Summarizing transcript %s failed", doc_id)
        graph.query(
            "MATCH (d:Document {id: $id}) SET d.summary_status = 'failed', d.summary_error = $error",
            params={"id": doc_id, "error": str(exc)[:500]},
        )
        raise
    summary["model"] = getattr(llm, "model", "")
    summary["created_at"] = time.time()
    graph.query(
        "MATCH (d:Document {id: $id}) SET d.summary = $summary, d.summary_status = 'done', d.summary_error = ''",
        params={"id": doc_id, "summary": json.dumps(summary, ensure_ascii=False)},
    )
    return summary


def stored_summary(props: dict) -> Optional[dict]:
    raw = props.get("summary")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None
