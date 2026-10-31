import logging
from dataclasses import dataclass
from typing import Any, Iterator, Optional, Sequence

from falkordb import Graph

from jivaka.chat.prompt import TRANSLATE_PROMPT, build_messages
from jivaka.ingestion.extraction.llm_client import OllamaClient
from jivaka.ingestion.text_utils import normalize_term
from jivaka.retrieval.graph_search import RetrievedChunk, search
from jivaka.retrieval.terms import extract_terms, find_pseudonyms, has_thai

logger = logging.getLogger(__name__)

# Earlier user questions consulted for a follow-up ("what about its side
# effects?") that names no entity or patient of its own.
FOLLOW_UP_LOOKBACK = 2

NO_SOURCES_EN = "I couldn't find anything about this in the ingested documents, so I can't answer it."
NO_SOURCES_TH = "ไม่พบข้อมูลเกี่ยวกับคำถามนี้ในเอกสารที่นำเข้า จึงไม่สามารถตอบได้"


@dataclass
class ChatEvent:
    """One server-sent event: status (what's happening), sources (the
    numbered chunks the answer may cite), token (answer text), done."""

    type: str
    data: Any


class ChatService:
    def __init__(self, graph: Graph, llm: Optional[OllamaClient] = None, top_k: int = 6):
        self.graph = graph
        self.llm = llm or OllamaClient()
        self.top_k = top_k

    def answer(self, messages: Sequence[dict]) -> Iterator[ChatEvent]:
        question = messages[-1]["content"]
        history = list(messages[:-1])

        yield ChatEvent("status", "searching")
        sources = self.retrieve(question, history)
        yield ChatEvent("sources", [dict(number=i, **c.to_dict()) for i, c in enumerate(sources, start=1)])

        if not sources:
            yield ChatEvent("token", NO_SOURCES_TH if has_thai(question) else NO_SOURCES_EN)
            yield ChatEvent("done", None)
            return

        yield ChatEvent("status", "writing")
        for piece in self.llm.chat_stream(build_messages(history, question, sources)):
            yield ChatEvent("token", piece)
        yield ChatEvent("done", None)

    def retrieve(self, question: str, history: Sequence[dict]) -> list[RetrievedChunk]:
        earlier = [m["content"] for m in history if m.get("role") == "user"][-FOLLOW_UP_LOOKBACK:]

        terms = self._terms(question)
        pseudonyms = find_pseudonyms(question)
        if not pseudonyms:
            # A follow-up keeps talking about the patient named just before.
            for text in reversed(earlier):
                pseudonyms = find_pseudonyms(text)
                if pseudonyms:
                    break

        results = search(self.graph, terms, pseudonyms, self.top_k)
        if not results and earlier:
            follow_up_terms = list(terms)
            for text in earlier:
                follow_up_terms += self._terms(text)
            results = search(self.graph, list(dict.fromkeys(follow_up_terms)), pseudonyms, self.top_k)
        if not results and pseudonyms:
            # "Summarise P-0042" names no entity: give that patient's
            # records in order instead.
            results = search(self.graph, [], pseudonyms, self.top_k)
        return results

    def _terms(self, text: str) -> list[str]:
        terms = extract_terms(text)
        if has_thai(text):
            # Most textbooks are English, so Thai questions also need the
            # English names of their medical terms to match anything.
            terms += self._translated_terms(text)
        return list(dict.fromkeys(terms))

    def _translated_terms(self, text: str) -> list[str]:
        try:
            raw = self.llm.generate_json(TRANSLATE_PROMPT.format(question=text))
        except Exception:
            logger.exception("Translating question terms failed; matching on the Thai words only")
            return []
        terms = raw.get("terms", []) if isinstance(raw, dict) else []
        return [normalize_term(str(t)) for t in terms if str(t).strip()]
