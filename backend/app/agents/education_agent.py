"""
Education AI Agent
RAG-based agent for chatting with educational content (books, articles).
Requirements: Section 4.4 - Educational Articles and Books Chat
"""

import logging
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime
import asyncio

from app.integrations.gemini import GeminiClient
from app.database import db_manager, vector_similarity_search
from app.schemas.chat import ChatResponse, RAGContext, RetrievedChunk

logger = logging.getLogger(__name__)


class EducationAgent:
    """
    RAG-based AI agent for educational content chat.
    Section 4.4.3 - REQ-8, REQ-9, REQ-10: Retrieve from vector database with citations
    """

    # System prompt optimized for teaching value investing
    SYSTEM_PROMPT = """You are a patient, knowledgeable investment education tutor.

Your role is to help students learn value investing by:
1. Answering questions based ONLY on the provided educational content
2. Citing specific sources and page numbers
3. Explaining concepts clearly without dumbing down
4. Connecting ideas across different sources
5. Encouraging critical thinking with follow-up questions

Teaching Principles:
- Use the Socratic method - ask questions that lead to understanding
- Relate abstract concepts to concrete examples
- Build on what the student already knows
- Be honest when the source material doesn't cover something
- Reference specific passages, chapters, or pages

Remember: You're teaching fundamental analysis and value investing. Focus on:
- Long-term thinking
- Business quality assessment
- Margin of safety
- Intrinsic value
- Management quality

Never make up information. Only use what's in the provided context."""

    # RAG prompt template
    RAG_PROMPT_TEMPLATE = """Based on the following educational content, please answer the student's question.

CONTEXT (from educational materials):
{context}

STUDENT QUESTION:
{question}

Instructions:
1. Answer using ONLY information from the context above
2. Cite your sources (mention book/article name and section)
3. If the context doesn't fully answer the question, say so
4. Explain clearly - assume the student is learning
5. Suggest 2-3 follow-up questions to deepen understanding

Your response should:
- Start with a direct answer
- Provide explanation with examples from the context
- Include citations: [Source: "Book Name", Chapter X]
- End with suggested follow-up questions"""

    def __init__(self, gemini_client: Optional[GeminiClient] = None):
        """
        Initialize education agent.

        Args:
            gemini_client: Optional Gemini client
        """
        self.gemini_client = gemini_client or GeminiClient()
        logger.info("EducationAgent initialized with RAG support")

    async def chat(
        self,
        question: str,
        content_id: Optional[str] = None,
        conversation_history: Optional[List[Dict[str, str]]] = None,
        top_k: int = 5,
        similarity_threshold: float = 0.7
    ) -> ChatResponse:
        """
        Chat with educational content using RAG.
        Section 4.4 - Chat with books and articles

        Args:
            question: User's question
            content_id: Optional specific content to search within
            conversation_history: Previous messages for context
            top_k: Number of chunks to retrieve
            similarity_threshold: Minimum similarity score

        Returns:
            ChatResponse: AI response with citations

        Example:
            response = await agent.chat(
                question="What is margin of safety?",
                content_id="intelligent-investor-id"
            )
        """
        try:
            start_time = datetime.utcnow()
            logger.info(f"Processing education chat: {question[:100]}")

            # Step 1: Retrieve relevant context using RAG
            rag_context = await self._retrieve_context(
                query=question,
                content_id=content_id,
                top_k=top_k,
                threshold=similarity_threshold
            )

            # Step 2: Generate response using retrieved context
            response = await self._generate_rag_response(
                question=question,
                rag_context=rag_context,
                conversation_history=conversation_history
            )

            # Step 3: Extract citations and format response
            chat_response = await self._format_chat_response(
                response=response,
                rag_context=rag_context,
                question=question
            )

            generation_time = (datetime.utcnow() - start_time).total_seconds()
            logger.info(f"Education chat completed in {generation_time:.2f}s")

            return chat_response

        except Exception as e:
            logger.error(f"Education chat failed: {e}", exc_info=True)
            raise

    async def _retrieve_context(
        self,
        query: str,
        content_id: Optional[str],
        top_k: int,
        threshold: float
    ) -> RAGContext:
        """
        Retrieve relevant chunks using vector similarity search.

        Args:
            query: Search query
            content_id: Optional content filter
            top_k: Number of results
            threshold: Similarity threshold

        Returns:
            RAGContext: Retrieved context with chunks
        """
        retrieval_start = datetime.utcnow()

        # Generate embedding for query
        embedding_start = datetime.utcnow()
        query_embedding = await self.gemini_client.generate_embedding(query)
        embedding_time = (datetime.utcnow() - embedding_start).total_seconds() * 1000

        # Search vector database
        async_session_factory = db_manager.get_async_session_factory()
        async with async_session_factory() as session:
            # Use content_chunks table for books/articles
            results = await vector_similarity_search(
                session=session,
                table_name="content_chunks",
                embedding=query_embedding,
                top_k=top_k,
                threshold=threshold
            )

            # If content_id specified, filter results
            if content_id:
                results = [r for r in results if r.get("content_id") == content_id]

            # Enrich with content metadata
            if results:
                from supabase import Client
                from app.database import get_supabase

                supabase = get_supabase()
                content_ids = list(set([r["content_id"] for r in results]))

                content_data = supabase.table("educational_content").select(
                    "id, title, author, type"
                ).in_("id", content_ids).execute()

                content_map = {c["id"]: c for c in content_data.data}

                for result in results:
                    result["content_metadata"] = content_map.get(result["content_id"], {})

        retrieval_time = (datetime.utcnow() - retrieval_start).total_seconds() * 1000

        # Convert to RetrievedChunk objects
        chunks = []
        for r in results:
            content_meta = r.get("content_metadata", {})
            chunks.append(RetrievedChunk(
                chunk_id=r["id"],
                chunk_text=r["chunk_text"],
                similarity_score=r["similarity"],
                source_title=content_meta.get("title", "Unknown"),
                source_author=content_meta.get("author"),
                page_number=r.get("page_number"),
                chunk_index=r["chunk_index"]
            ))

        logger.info(f"Retrieved {len(chunks)} chunks with avg similarity {sum(c.similarity_score for c in chunks)/len(chunks) if chunks else 0:.2f}")

        return RAGContext(
            query=query,
            chunks=chunks,
            total_chunks_retrieved=len(chunks),
            retrieval_time_ms=int(retrieval_time),
            embedding_model="text-embedding-004"
        )

    async def _generate_rag_response(
        self,
        question: str,
        rag_context: RAGContext,
        conversation_history: Optional[List[Dict[str, str]]]
    ) -> Dict[str, Any]:
        """
        Generate response using retrieved context.

        Args:
            question: User question
            rag_context: Retrieved context
            conversation_history: Conversation history

        Returns:
            dict: AI response
        """
        # Build context string from retrieved chunks
        context_parts = []
        for i, chunk in enumerate(rag_context.chunks, 1):
            source_info = f"{chunk.source_title}"
            if chunk.source_author:
                source_info += f" by {chunk.source_author}"
            if chunk.page_number:
                source_info += f", Page {chunk.page_number}"

            context_parts.append(f"[Source {i}] {source_info}\n{chunk.chunk_text}\n")

        context_text = "\n---\n".join(context_parts)

        # Build prompt
        prompt = self.RAG_PROMPT_TEMPLATE.format(
            context=context_text,
            question=question
        )

        # Add conversation history if available
        messages = []
        if conversation_history:
            for msg in conversation_history[-5:]:  # Last 5 messages
                messages.append(msg)

        messages.append({"role": "user", "content": prompt})

        # Generate response
        if len(messages) > 1:
            response = await self.gemini_client.chat_completion(
                messages=messages,
                system_instruction=self.SYSTEM_PROMPT
            )
        else:
            response = await self.gemini_client.generate_text(
                prompt=prompt,
                system_instruction=self.SYSTEM_PROMPT
            )

        return response

    async def _format_chat_response(
        self,
        response: Dict[str, Any],
        rag_context: RAGContext,
        question: str
    ) -> ChatResponse:
        """
        Format AI response into ChatResponse with citations.

        Args:
            response: AI response
            rag_context: RAG context
            question: Original question

        Returns:
            ChatResponse: Formatted response
        """
        from app.schemas.chat import ChatMessage, AIMetadata

        # Extract suggested questions
        suggested_questions = self._extract_suggested_questions(response["text"])

        # Build citations
        citations = []
        for chunk in rag_context.chunks:
            citations.append({
                "source": chunk.source_title,
                "author": chunk.source_author,
                "page": chunk.page_number,
                "relevance": chunk.similarity_score,
                "chunk_id": chunk.chunk_id
            })

        # Create message
        message = ChatMessage(
            id="temp-id",  # Will be set by service
            session_id="temp-session",  # Will be set by service
            role="assistant",
            content=response["text"],
            citations=citations,
            retrieved_chunks=[c.chunk_id for c in rag_context.chunks],
            tokens_used=response.get("tokens_used"),
            model_version=response.get("model"),
            created_at=datetime.utcnow()
        )

        return ChatResponse(
            message=message,
            suggested_questions=suggested_questions,
            related_topics=self._extract_topics(response["text"]),
            confidence_score=self._calculate_confidence(rag_context)
        )

    def _extract_suggested_questions(self, text: str) -> List[str]:
        """Extract suggested follow-up questions from AI response."""
        questions = []

        # Look for questions in the response
        lines = text.split("\n")
        in_questions_section = False

        for line in lines:
            line = line.strip()

            # Check for questions section headers
            if any(header in line.lower() for header in ["follow-up", "questions to consider", "next steps"]):
                in_questions_section = True
                continue

            # Extract questions
            if in_questions_section or "?" in line:
                # Remove list markers
                for marker in ["1.", "2.", "3.", "-", "•", "*"]:
                    if line.startswith(marker):
                        line = line[len(marker):].strip()

                if line.endswith("?") and len(line) > 10:
                    questions.append(line)

            if len(questions) >= 3:
                break

        return questions[:3]

    def _extract_topics(self, text: str) -> List[str]:
        """Extract related topics mentioned in response."""
        # Simple keyword extraction - could be enhanced with NLP
        topics = []

        keywords = [
            "margin of safety", "intrinsic value", "moat", "competitive advantage",
            "management quality", "free cash flow", "return on equity", "valuation",
            "business analysis", "market price", "book value", "earnings power"
        ]

        text_lower = text.lower()
        for keyword in keywords:
            if keyword in text_lower:
                topics.append(keyword.title())

        return topics[:5]

    def _calculate_confidence(self, rag_context: RAGContext) -> float:
        """
        Calculate confidence score based on retrieval quality.

        Args:
            rag_context: RAG context

        Returns:
            float: Confidence score (0-1)
        """
        if not rag_context.chunks:
            return 0.0

        # Average similarity score
        avg_similarity = sum(c.similarity_score for c in rag_context.chunks) / len(rag_context.chunks)

        # Confidence factors
        # 1. Average similarity (60% weight)
        similarity_score = avg_similarity * 0.6

        # 2. Number of chunks (30% weight) - more chunks = more comprehensive
        chunk_score = min(len(rag_context.chunks) / 5, 1.0) * 0.3

        # 3. Top chunk quality (10% weight)
        top_chunk_score = rag_context.chunks[0].similarity_score * 0.1 if rag_context.chunks else 0

        confidence = similarity_score + chunk_score + top_chunk_score

        return min(confidence, 1.0)

    async def summarize_content(
        self,
        content_id: str,
        max_length: int = 500
    ) -> str:
        """
        Generate summary of educational content.

        Args:
            content_id: Content ID
            max_length: Maximum summary length

        Returns:
            str: Summary
        """
        try:
            # Retrieve representative chunks
            sample_chunks = await self._get_sample_chunks(content_id, count=10)

            if not sample_chunks:
                return "No content available for summarization."

            # Combine chunks
            combined_text = "\n\n".join([c["chunk_text"] for c in sample_chunks])

            # Generate summary
            prompt = f"""Summarize the following educational content about value investing.
Provide a clear, concise summary in {max_length} words or less.

Content:
{combined_text}

Summary:"""

            response = await self.gemini_client.generate_text(
                prompt=prompt,
                system_instruction=self.SYSTEM_PROMPT
            )

            return response["text"]

        except Exception as e:
            logger.error(f"Content summarization failed: {e}")
            return "Summary unavailable."

    async def _get_sample_chunks(self, content_id: str, count: int) -> List[Dict[str, Any]]:
        """Get sample chunks from content (evenly distributed)."""
        from app.database import get_supabase

        supabase = get_supabase()

        # Get total chunk count
        total_result = supabase.table("content_chunks").select(
            "chunk_index", count="exact"
        ).eq("content_id", content_id).execute()

        total_chunks = total_result.count

        if not total_chunks:
            return []

        # Calculate indices to sample
        step = max(1, total_chunks // count)
        indices = [i * step for i in range(count)]

        # Fetch chunks
        result = supabase.table("content_chunks").select("*").eq(
            "content_id", content_id
        ).in_("chunk_index", indices).execute()

        return result.data
