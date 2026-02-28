"""
Chat Service — RAG pipeline using Supabase pgvector + Gemini.
"""

import logging
from typing import Dict, Any, Optional, List

from app.database import get_supabase
from app.integrations.gemini import get_gemini_client
from app.config import settings

logger = logging.getLogger(__name__)


class ChatService:
    def __init__(self):
        self.supabase = get_supabase()
        self.gemini = get_gemini_client()

    async def generate_response(
        self,
        session_id: str,
        user_message: str,
        session_type: str = "NORMAL",
        stock_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Generate AI response with RAG context retrieval.
        1. Generate embedding of user message
        2. Search relevant chunks via Supabase RPC
        3. Build context-enhanced prompt
        4. Generate response with Gemini
        5. Return content + citations
        """
        # Step 1: Get conversation history for context
        history = self._get_recent_messages(session_id, limit=10)

        # Step 2: Retrieve RAG context
        chunks = []
        citations = []
        try:
            query_embedding = await self.gemini.generate_embedding(
                user_message, model_name="models/text-embedding-004"
            )

            # Search relevant chunks based on session type
            if stock_id:
                chunks = self._search_filing_chunks(query_embedding, stock_id)
            else:
                chunks = self._search_all_chunks(query_embedding)

            # Build citations
            for i, chunk in enumerate(chunks):
                citations.append({
                    "index": i + 1,
                    "source": chunk.get("section_title", "Document"),
                    "text": chunk.get("chunk_text", "")[:200],
                })
        except Exception as e:
            logger.warning(f"RAG retrieval failed, proceeding without context: {e}")

        # Step 3: Build prompt
        system_instruction = self._build_system_instruction(session_type, stock_id)
        prompt = self._build_prompt(user_message, history, chunks)

        # Step 4: Generate response
        response = await self.gemini.generate_text(
            prompt=prompt,
            system_instruction=system_instruction,
        )

        return {
            "content": response["text"],
            "citations": citations if citations else None,
            "tokens_used": response.get("tokens_used"),
        }

    def _get_recent_messages(self, session_id: str, limit: int = 10) -> List[Dict]:
        try:
            result = self.supabase.table("chat_messages").select(
                "role, content"
            ).eq("session_id", session_id).order(
                "created_at", desc=True
            ).limit(limit).execute()

            return list(reversed(result.data)) if result.data else []
        except Exception:
            return []

    def _search_filing_chunks(self, embedding: List[float], ticker: str) -> List[Dict]:
        """Search company filing chunks for a specific ticker."""
        try:
            result = self.supabase.rpc("search_filing_chunks", {
                "query_embedding": embedding,
                "match_threshold": settings.VECTOR_SIMILARITY_THRESHOLD,
                "match_count": settings.RAG_TOP_K_RESULTS,
                "filter_ticker": ticker.upper(),
            }).execute()
            return result.data or []
        except Exception as e:
            logger.warning(f"Filing chunk search failed: {e}")
            return []

    def _search_all_chunks(self, embedding: List[float]) -> List[Dict]:
        """Search all chunk types (books, articles, filings)."""
        try:
            result = self.supabase.rpc("search_all_chunks", {
                "query_embedding": embedding,
                "match_threshold": settings.VECTOR_SIMILARITY_THRESHOLD,
                "match_count": settings.RAG_TOP_K_RESULTS,
            }).execute()
            return result.data or []
        except Exception as e:
            logger.warning(f"All chunk search failed: {e}")
            return []

    def _build_system_instruction(self, session_type: str, stock_id: Optional[str]) -> str:
        base = (
            "You are Caydex, an AI assistant specializing in value investing education. "
            "Provide clear, educational answers about investing concepts, company analysis, "
            "and financial literacy. Always remind users this is educational, not financial advice."
        )
        if stock_id:
            base += f"\nYou are currently helping analyze {stock_id}. Use the provided financial data and filings context."
        return base

    def _build_prompt(
        self, user_message: str, history: List[Dict], chunks: List[Dict],
    ) -> str:
        parts = []

        # Add RAG context
        if chunks:
            context_text = "\n\n---\n\n".join(
                c.get("chunk_text", "") for c in chunks[:5]
            )
            parts.append(f"RELEVANT CONTEXT:\n{context_text}\n\n---\n")

        # Add conversation history
        if history:
            conv = "\n".join(
                f"{'User' if m['role'] == 'user' else 'Assistant'}: {m['content'][:300]}"
                for m in history[-6:]  # last 6 messages
            )
            parts.append(f"CONVERSATION HISTORY:\n{conv}\n\n---\n")

        parts.append(f"USER MESSAGE:\n{user_message}")

        if chunks:
            parts.append(
                "\nProvide a comprehensive answer. Cite the context where relevant "
                "using [1], [2], etc. for specific claims."
            )

        return "\n".join(parts)
