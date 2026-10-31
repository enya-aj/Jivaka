import json
import logging
from typing import Iterator, Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from jivaka.chat.service import ChatEvent, ChatService
from jivaka.config import get_settings
from jivaka.db.falkor_client import get_graph
from jivaka.retrieval.graph_search import load_chunks

logger = logging.getLogger(__name__)

router = APIRouter()


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(..., max_length=8000)


class ChatRequest(BaseModel):
    # The whole conversation so far, oldest first, ending with the new
    # question. The server keeps no history of its own.
    messages: list[ChatMessage] = Field(..., min_length=1, max_length=50)

    @field_validator("messages")
    @classmethod
    def ends_with_question(cls, messages: list[ChatMessage]) -> list[ChatMessage]:
        if messages[-1].role != "user" or not messages[-1].content.strip():
            raise ValueError("the last message must be a non-empty user question")
        return messages


@router.post("/chat")
def chat_endpoint(request: ChatRequest) -> StreamingResponse:
    """Answers the last question as Server-Sent Events: `status`
    (searching/writing), `sources` (the numbered chunks the answer may cite),
    `token` (answer text, streamed), then `done` - or `error`."""
    service = ChatService(get_graph(), top_k=get_settings().retrieval_top_k)
    events = service.answer([m.model_dump() for m in request.messages])
    return StreamingResponse(
        _sse(events),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _sse(events: Iterator[ChatEvent]) -> Iterator[str]:
    # A sync generator: Starlette runs it in a worker thread, so the blocking
    # graph queries and Ollama stream don't stall other requests.
    try:
        for event in events:
            yield f"event: {event.type}\ndata: {json.dumps(event.data, ensure_ascii=False)}\n\n"
    except Exception as exc:
        logger.exception("Chat answer failed")
        yield f"event: error\ndata: {json.dumps(str(exc), ensure_ascii=False)}\n\n"


@router.get("/chunks/{chunk_id}")
def get_chunk_endpoint(chunk_id: str) -> dict:
    """One source chunk with its document label, entities and definitions,
    for the chat page's citation panel."""
    chunks = load_chunks(get_graph(), [chunk_id])
    if not chunks:
        raise HTTPException(status_code=404, detail="Chunk not found")
    return chunks[0].to_dict()
