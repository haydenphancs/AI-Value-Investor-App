"""Chat schemas matching DB chat_sessions + chat_messages tables."""

from pydantic import BaseModel
from typing import Optional, List, Any


class CreateChatSessionRequest(BaseModel):
    stock_id: Optional[str] = None  # ticker symbol, optional


class SendChatMessageRequest(BaseModel):
    message: str


class ChatMessageResponse(BaseModel):
    id: str
    session_id: str
    role: str
    content: str
    rich_content: Optional[Any] = None
    citations: Optional[List[Any]] = None
    tokens_used: Optional[int] = None
    created_at: str


class ChatSessionResponse(BaseModel):
    id: str
    title: Optional[str] = None
    session_type: Optional[str] = "NORMAL"
    stock_id: Optional[str] = None
    preview_message: Optional[str] = None
    message_count: int = 0
    is_saved: bool = False
    created_at: str
    last_message_at: Optional[str] = None


class ChatHistoryResponse(BaseModel):
    session: ChatSessionResponse
    messages: List[ChatMessageResponse]
