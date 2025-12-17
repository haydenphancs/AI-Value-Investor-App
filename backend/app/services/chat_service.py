"""
Chat Service
Business logic for AI chat sessions with RAG support.
Requirements: Section 4.4 - Educational Articles and Books Chat
"""

import logging
from typing import Dict, Any, List, Optional
from datetime import datetime

from supabase import Client

from app.agents.education_agent import EducationAgent
from app.integrations.gemini import GeminiClient
from app.schemas.chat import ChatResponse, ChatMessage, SessionType
from app.schemas.common import AIMetadata

logger = logging.getLogger(__name__)


class ChatService:
    """
    Service for AI chat sessions with RAG support.
    Section 4.4 - Chat with educational content and stock analysis.
    """

    def __init__(
        self,
        supabase: Client,
        education_agent: Optional[EducationAgent] = None,
        gemini_client: Optional[GeminiClient] = None
    ):
        """
        Initialize chat service.

        Args:
            supabase: Supabase client
            education_agent: Education agent for RAG
            gemini_client: Gemini client for general chat
        """
        self.supabase = supabase
        self.education_agent = education_agent or EducationAgent()
        self.gemini_client = gemini_client or GeminiClient()
        logger.info("ChatService initialized")

    async def generate_response(
        self,
        session_id: str,
        user_message: str,
        session_type: SessionType,
        content_id: Optional[str] = None,
        stock_id: Optional[str] = None
    ) -> ChatResponse:
        """
        Generate AI response for chat message.
        Routes to appropriate agent based on session type.

        Args:
            session_id: Chat session ID
            user_message: User's message
            session_type: Type of chat session
            content_id: Educational content ID (for education chat)
            stock_id: Stock ID (for stock analysis chat)

        Returns:
            ChatResponse: AI response with metadata

        Example:
            response = await service.generate_response(
                session_id="session-123",
                user_message="What is margin of safety?",
                session_type=SessionType.EDUCATION,
                content_id="intelligent-investor-id"
            )
        """
        try:
            logger.info(f"Generating response for session {session_id} (type: {session_type.value})")

            # Get conversation history
            history = await self._get_conversation_history(session_id, limit=10)

            # Route to appropriate handler
            if session_type == SessionType.EDUCATION:
                response = await self._handle_education_chat(
                    user_message=user_message,
                    content_id=content_id,
                    history=history
                )
            elif session_type == SessionType.STOCK_ANALYSIS:
                response = await self._handle_stock_analysis_chat(
                    user_message=user_message,
                    stock_id=stock_id,
                    history=history
                )
            else:
                response = await self._handle_general_chat(
                    user_message=user_message,
                    history=history
                )

            # Save messages to database
            await self._save_messages(
                session_id=session_id,
                user_message=user_message,
                ai_response=response
            )

            logger.info(f"Response generated for session {session_id}")

            return response

        except Exception as e:
            logger.error(f"Failed to generate response: {e}", exc_info=True)
            raise

    async def _handle_education_chat(
        self,
        user_message: str,
        content_id: Optional[str],
        history: List[Dict[str, str]]
    ) -> ChatResponse:
        """
        Handle education chat with RAG.
        Section 4.4 - Chat with books and articles.

        Args:
            user_message: User message
            content_id: Content ID
            history: Conversation history

        Returns:
            ChatResponse: AI response
        """
        logger.info("Handling education chat with RAG")

        # Use education agent for RAG-based response
        response = await self.education_agent.chat(
            question=user_message,
            content_id=content_id,
            conversation_history=history,
            top_k=5,
            similarity_threshold=0.7
        )

        return response

    async def _handle_stock_analysis_chat(
        self,
        user_message: str,
        stock_id: Optional[str],
        history: List[Dict[str, str]]
    ) -> ChatResponse:
        """
        Handle stock analysis chat.
        Section 4.4 - Company fundamental information chat.

        Args:
            user_message: User message
            stock_id: Stock ID
            history: Conversation history

        Returns:
            ChatResponse: AI response
        """
        logger.info(f"Handling stock analysis chat for stock {stock_id}")

        # Get stock details
        stock_data = await self._get_stock_context(stock_id)

        # Build context-aware prompt
        system_prompt = """You are a financial analyst helping users understand company fundamentals.

Your role:
1. Explain financial metrics in plain English
2. Answer questions about the company's business and performance
3. Focus on long-term fundamentals, not short-term trading
4. Be honest about limitations of available data

Use the provided company data to answer questions accurately.
If you don't have specific data, say so clearly."""

        context_prompt = f"""Company Context:
{stock_data}

User Question: {user_message}

Provide a clear, educational answer focusing on fundamentals."""

        # Generate response
        messages = []
        for msg in history[-5:]:
            messages.append(msg)

        messages.append({"role": "user", "content": context_prompt})

        ai_response = await self.gemini_client.chat_completion(
            messages=messages,
            system_instruction=system_prompt
        )

        # Build ChatResponse
        from app.schemas.chat import ChatMessage

        message = ChatMessage(
            id="temp-id",
            session_id="temp-session",
            role="assistant",
            content=ai_response["text"],
            tokens_used=ai_response.get("tokens_used"),
            model_version=ai_response.get("model"),
            created_at=datetime.utcnow()
        )

        return ChatResponse(
            message=message,
            suggested_questions=self._generate_stock_questions(stock_data),
            confidence_score=0.8  # Fixed confidence for stock analysis
        )

    async def _handle_general_chat(
        self,
        user_message: str,
        history: List[Dict[str, str]]
    ) -> ChatResponse:
        """
        Handle general investment chat.

        Args:
            user_message: User message
            history: Conversation history

        Returns:
            ChatResponse: AI response
        """
        logger.info("Handling general chat")

        system_prompt = """You are a knowledgeable investment advisor focused on value investing.

Your role:
1. Help users learn value investing principles
2. Explain financial concepts clearly
3. Encourage long-term thinking
4. Discourage speculation and market timing

Be helpful, educational, and focused on fundamentals."""

        messages = []
        for msg in history[-5:]:
            messages.append(msg)

        messages.append({"role": "user", "content": user_message})

        ai_response = await self.gemini_client.chat_completion(
            messages=messages,
            system_instruction=system_prompt
        )

        from app.schemas.chat import ChatMessage

        message = ChatMessage(
            id="temp-id",
            session_id="temp-session",
            role="assistant",
            content=ai_response["text"],
            tokens_used=ai_response.get("tokens_used"),
            model_version=ai_response.get("model"),
            created_at=datetime.utcnow()
        )

        return ChatResponse(
            message=message,
            suggested_questions=["What is intrinsic value?", "How do I find good companies?"],
            confidence_score=0.9
        )

    async def _get_conversation_history(
        self,
        session_id: str,
        limit: int = 10
    ) -> List[Dict[str, str]]:
        """
        Get conversation history for context.

        Args:
            session_id: Session ID
            limit: Maximum messages

        Returns:
            list: Message history
        """
        try:
            result = self.supabase.table("chat_messages").select(
                "role, content"
            ).eq("session_id", session_id).order(
                "created_at", desc=False
            ).limit(limit).execute()

            return result.data

        except Exception as e:
            logger.warning(f"Failed to get conversation history: {e}")
            return []

    async def _get_stock_context(self, stock_id: Optional[str]) -> str:
        """
        Get stock context for analysis chat.

        Args:
            stock_id: Stock ID

        Returns:
            str: Formatted stock context
        """
        if not stock_id:
            return "No stock data available."

        try:
            # Get stock details
            stock = self.supabase.table("stocks").select("*").eq(
                "id", stock_id
            ).single().execute()

            if not stock.data:
                return "Stock not found."

            # Get recent fundamentals
            fundamentals = self.supabase.table("company_fundamentals").select(
                "*"
            ).eq("stock_id", stock_id).order(
                "fiscal_year", desc=True
            ).limit(3).execute()

            # Build context
            context_parts = [
                f"Company: {stock.data['company_name']} ({stock.data['ticker']})",
                f"Sector: {stock.data.get('sector', 'N/A')}",
                f"Industry: {stock.data.get('industry', 'N/A')}"
            ]

            if stock.data.get("description"):
                context_parts.append(f"Description: {stock.data['description'][:500]}")

            if fundamentals.data:
                recent = fundamentals.data[0]
                context_parts.append(f"\nRecent Financials (FY{recent.get('fiscal_year')}):")
                context_parts.append(f"  Revenue: ${recent.get('revenue', 0):,.0f}")
                context_parts.append(f"  Net Income: ${recent.get('net_income', 0):,.0f}")
                context_parts.append(f"  ROE: {recent.get('roe', 0)*100:.1f}%")

            return "\n".join(context_parts)

        except Exception as e:
            logger.error(f"Failed to get stock context: {e}")
            return "Error loading stock data."

    async def _save_messages(
        self,
        session_id: str,
        user_message: str,
        ai_response: ChatResponse
    ):
        """
        Save user and AI messages to database.

        Args:
            session_id: Session ID
            user_message: User's message
            ai_response: AI response
        """
        try:
            # Save user message
            self.supabase.table("chat_messages").insert({
                "session_id": session_id,
                "role": "user",
                "content": user_message
            }).execute()

            # Save AI message
            ai_msg = ai_response.message

            self.supabase.table("chat_messages").insert({
                "session_id": session_id,
                "role": "assistant",
                "content": ai_msg.content,
                "citations": ai_msg.citations,
                "retrieved_chunks": ai_msg.retrieved_chunks,
                "tokens_used": ai_msg.tokens_used,
                "model_version": ai_msg.model_version
            }).execute()

        except Exception as e:
            logger.error(f"Failed to save messages: {e}")

    def _generate_stock_questions(self, stock_context: str) -> List[str]:
        """Generate suggested questions based on stock context."""
        return [
            "What are the key financial metrics?",
            "How has revenue been trending?",
            "What are the main business risks?",
            "How does ROE compare to peers?"
        ]

    async def get_embedding(self, text: str) -> List[float]:
        """
        Get text embedding for RAG.

        Args:
            text: Text to embed

        Returns:
            list: Embedding vector
        """
        return await self.gemini_client.generate_embedding(text)
