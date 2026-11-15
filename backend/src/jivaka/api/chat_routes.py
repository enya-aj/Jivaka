import json
import logging
from typing import Iterator, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from jivaka.auth.deps import current_user
from jivaka.auth.store import ChatHistory, Conversation, User, get_store
from jivaka.chat.service import ChatEvent, ChatService
from jivaka.config import get_settings
from jivaka.db.falkor_client import get_graph
from jivaka.retrieval.graph_search import load_chunks

logger = logging.getLogger(__name__)

router = APIRouter()

# Earlier messages of a saved conversation given to the model with a new
# question (the most recent ones).
HISTORY_MESSAGES = 20


class ChatRequest(BaseModel):
    question: str = Field(..., max_length=8000)
    # Continue this saved conversation; leave out to start a new one.
    conversation_id: Optional[str] = None

    @field_validator("question")
    @classmethod
    def not_blank(cls, question: str) -> str:
        if not question.strip():
            raise ValueError("the question is empty")
        return question


class ConversationRename(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)


def _history() -> ChatHistory:
    return ChatHistory(get_store())


def _readable_conversation(conversation_id: str, user: User) -> Conversation:
    """The owner and admins (for audits) can read a conversation."""
    conversation = _history().get(conversation_id)
    if conversation is None or (conversation.user_email != user.email and not user.is_admin):
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation


@router.post("/chat")
def chat_endpoint(request: ChatRequest, user: User = Depends(current_user)) -> StreamingResponse:
    """Answers the question as Server-Sent Events: `conversation` (its id
    and title), `status` (searching/writing), `sources` (the numbered chunks
    the answer may cite), `token` (answer text, streamed), then `done` - or
    `error`. The question and answer are saved to the conversation once the
    answer is complete."""
    history = _history()
    if request.conversation_id:
        conversation = history.get(request.conversation_id)
        # Only the owner continues a conversation; admins can only read others'.
        if conversation is None or conversation.user_email != user.email:
            raise HTTPException(status_code=404, detail="Conversation not found")
        earlier = [
            {"role": m["role"], "content": m["content"]} for m in history.messages(conversation.id)
        ][-HISTORY_MESSAGES:]
    else:
        conversation = history.create(user.email, request.question)
        earlier = []

    service = ChatService(
        get_graph(), top_k=get_settings().retrieval_top_k, include_patients=user.can_see_patient_records
    )
    events = service.answer([*earlier, {"role": "user", "content": request.question}])
    return StreamingResponse(
        _sse(events, history, conversation, request.question),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _sse(
    events: Iterator[ChatEvent], history: ChatHistory, conversation: Conversation, question: str
) -> Iterator[str]:
    # A sync generator: Starlette runs it in a worker thread, so the blocking
    # graph queries and Ollama stream don't stall other requests.
    def frame(event_type: str, data) -> str:
        return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    yield frame("conversation", {"id": conversation.id, "title": conversation.title})
    answer: list[str] = []
    sources: list = []
    finished = False
    try:
        for event in events:
            if event.type == "token":
                answer.append(event.data)
            elif event.type == "sources":
                sources = event.data
            elif event.type == "done":
                finished = True
            yield frame(event.type, event.data)
    except Exception as exc:
        logger.exception("Chat answer failed")
        yield frame("error", str(exc))
    finally:
        if finished:
            history.add_exchange(conversation.id, question, "".join(answer), sources)
        elif not history.messages(conversation.id):
            # A brand-new conversation whose first answer failed: drop it
            # rather than leave an empty entry in the list.
            history.delete(conversation.id)


@router.get("/conversations")
def list_conversations(everyone: bool = False, user: User = Depends(current_user)) -> list[dict]:
    """The signed-in user's conversations, newest first. Admins can pass
    everyone=true to list all users' conversations."""
    if everyone and not user.is_admin:
        raise HTTPException(status_code=403, detail="Only admins can read other people's chats.")
    return [c.to_dict() for c in _history().list_for(None if everyone else user.email)]


@router.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: str, user: User = Depends(current_user)) -> dict:
    conversation = _readable_conversation(conversation_id, user)
    return {
        **conversation.to_dict(),
        "messages": _history().messages(conversation.id),
        "read_only": conversation.user_email != user.email,
    }


@router.patch("/conversations/{conversation_id}")
def rename_conversation(
    conversation_id: str, body: ConversationRename, user: User = Depends(current_user)
) -> dict:
    conversation = _readable_conversation(conversation_id, user)
    if conversation.user_email != user.email:
        raise HTTPException(status_code=403, detail="Only the person who had this chat can rename it.")
    _history().rename(conversation.id, body.title)
    return _history().get(conversation.id).to_dict()  # type: ignore[union-attr]


@router.delete("/conversations/{conversation_id}")
def delete_conversation(conversation_id: str, user: User = Depends(current_user)) -> dict:
    conversation = _readable_conversation(conversation_id, user)
    _history().delete(conversation.id)
    return {"deleted": conversation.id}


@router.get("/chunks/{chunk_id}")
def get_chunk_endpoint(chunk_id: str, user: User = Depends(current_user)) -> dict:
    """One source chunk with its document label, entities and definitions,
    for the chat page's citation panel."""
    chunks = load_chunks(get_graph(), [chunk_id], include_patients=user.can_see_patient_records)
    if not chunks:
        raise HTTPException(status_code=404, detail="Chunk not found")
    return chunks[0].to_dict()
