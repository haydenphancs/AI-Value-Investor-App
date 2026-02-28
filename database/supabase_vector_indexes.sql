-- =====================================================
-- Caydex - Vector Indexes for Semantic Search (RAG)
-- Run AFTER loading embedding data into chunk tables
-- Version: 3.0
-- Date: February 28, 2026
-- =====================================================
--
-- IMPORTANT: Only create vector indexes AFTER loading data.
-- Empty or near-empty tables won't benefit from indexing.
-- Wait until you have at least a few hundred chunks loaded.
--
-- Index type: HNSW (Hierarchical Navigable Small World)
--   - Better recall than IVFFlat at same speed
--   - No training step required
--   - Supported in pgvector 0.5+ (Supabase default)
--   - Uses cosine distance (vector_cosine_ops)
--
-- Embedding dimension: 1536
--   - Compatible with: OpenAI text-embedding-ada-002,
--     text-embedding-3-small, and similar models
-- =====================================================

-- =====================================================
-- PRE-FLIGHT CHECK
-- =====================================================

DO $$
DECLARE
    book_count INT;
    article_count INT;
    filing_count INT;
BEGIN
    SELECT COUNT(*) INTO book_count FROM book_chunks;
    SELECT COUNT(*) INTO article_count FROM article_chunks;
    SELECT COUNT(*) INTO filing_count FROM company_filing_chunks;

    RAISE NOTICE '=================================================';
    RAISE NOTICE 'Vector Data Status:';
    RAISE NOTICE '  book_chunks:            %', book_count;
    RAISE NOTICE '  article_chunks:         %', article_count;
    RAISE NOTICE '  company_filing_chunks:  %', filing_count;
    RAISE NOTICE '=================================================';

    IF book_count = 0 AND article_count = 0 AND filing_count = 0 THEN
        RAISE WARNING 'All chunk tables are empty. Indexes will be created but consider loading data first.';
    END IF;
END $$;

-- =====================================================
-- HNSW INDEXES (recommended for pgvector 0.5+)
-- =====================================================
-- m = 16: number of bi-directional links per node (default)
-- ef_construction = 64: size of dynamic candidate list during build (default)
-- Higher values = better recall but slower build & more memory

-- Book chunks index
CREATE INDEX IF NOT EXISTS idx_book_chunks_embedding_hnsw
ON book_chunks
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);

-- Article chunks index
CREATE INDEX IF NOT EXISTS idx_article_chunks_embedding_hnsw
ON article_chunks
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);

-- Company filing chunks index
CREATE INDEX IF NOT EXISTS idx_filing_chunks_embedding_hnsw
ON company_filing_chunks
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);

-- =====================================================
-- ANALYZE TABLES
-- =====================================================

ANALYZE book_chunks;
ANALYZE article_chunks;
ANALYZE company_filing_chunks;

-- =====================================================
-- SEARCH FUNCTIONS
-- =====================================================

-- Search book chunks by embedding similarity
CREATE OR REPLACE FUNCTION search_book_chunks(
    query_embedding vector(1536),
    match_threshold FLOAT DEFAULT 0.7,
    match_count INT DEFAULT 5,
    filter_book_id UUID DEFAULT NULL
)
RETURNS TABLE (
    id UUID,
    book_id UUID,
    book_title TEXT,
    book_author TEXT,
    chapter_number INT,
    section_title TEXT,
    chunk_text TEXT,
    similarity FLOAT
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        bc.id,
        bc.book_id,
        b.title AS book_title,
        b.author AS book_author,
        bc.chapter_number,
        bc.section_title,
        bc.chunk_text,
        (1 - (bc.embedding <=> query_embedding))::FLOAT AS similarity
    FROM book_chunks bc
    JOIN books b ON bc.book_id = b.id
    WHERE (1 - (bc.embedding <=> query_embedding)) > match_threshold
      AND (filter_book_id IS NULL OR bc.book_id = filter_book_id)
    ORDER BY bc.embedding <=> query_embedding
    LIMIT match_count;
END;
$$ LANGUAGE plpgsql STABLE;

COMMENT ON FUNCTION search_book_chunks IS 'Semantic search across book content. Returns top matches above similarity threshold.';

-- Search article chunks by embedding similarity
CREATE OR REPLACE FUNCTION search_article_chunks(
    query_embedding vector(1536),
    match_threshold FLOAT DEFAULT 0.7,
    match_count INT DEFAULT 5
)
RETURNS TABLE (
    id UUID,
    article_id UUID,
    section_title TEXT,
    chunk_text TEXT,
    similarity FLOAT
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        ac.id,
        ac.article_id,
        ac.section_title,
        ac.chunk_text,
        (1 - (ac.embedding <=> query_embedding))::FLOAT AS similarity
    FROM article_chunks ac
    WHERE (1 - (ac.embedding <=> query_embedding)) > match_threshold
    ORDER BY ac.embedding <=> query_embedding
    LIMIT match_count;
END;
$$ LANGUAGE plpgsql STABLE;

-- Search company filing chunks by embedding similarity
CREATE OR REPLACE FUNCTION search_filing_chunks(
    query_embedding vector(1536),
    match_threshold FLOAT DEFAULT 0.7,
    match_count INT DEFAULT 5,
    filter_ticker TEXT DEFAULT NULL,
    filter_filing_type TEXT DEFAULT NULL
)
RETURNS TABLE (
    id UUID,
    ticker TEXT,
    filing_type TEXT,
    fiscal_year INT,
    fiscal_quarter INT,
    section_title TEXT,
    chunk_text TEXT,
    similarity FLOAT
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        cfc.id,
        cfc.ticker,
        cfc.filing_type,
        cfc.fiscal_year,
        cfc.fiscal_quarter,
        cfc.section_title,
        cfc.chunk_text,
        (1 - (cfc.embedding <=> query_embedding))::FLOAT AS similarity
    FROM company_filing_chunks cfc
    WHERE (1 - (cfc.embedding <=> query_embedding)) > match_threshold
      AND (filter_ticker IS NULL OR cfc.ticker = filter_ticker)
      AND (filter_filing_type IS NULL OR cfc.filing_type = filter_filing_type)
    ORDER BY cfc.embedding <=> query_embedding
    LIMIT match_count;
END;
$$ LANGUAGE plpgsql STABLE;

COMMENT ON FUNCTION search_filing_chunks IS 'Semantic search across SEC filing chunks. Optionally filter by ticker and filing type.';

-- Multi-source RAG search (searches all chunk tables at once)
CREATE OR REPLACE FUNCTION search_all_chunks(
    query_embedding vector(1536),
    match_threshold FLOAT DEFAULT 0.7,
    match_count INT DEFAULT 10
)
RETURNS TABLE (
    source_type TEXT,
    source_id UUID,
    source_label TEXT,
    section_title TEXT,
    chunk_text TEXT,
    similarity FLOAT
) AS $$
BEGIN
    RETURN QUERY
    (
        SELECT
            'book'::TEXT AS source_type,
            bc.book_id AS source_id,
            b.title || ' by ' || b.author AS source_label,
            bc.section_title,
            bc.chunk_text,
            (1 - (bc.embedding <=> query_embedding))::FLOAT AS similarity
        FROM book_chunks bc
        JOIN books b ON bc.book_id = b.id
        WHERE (1 - (bc.embedding <=> query_embedding)) > match_threshold
    )
    UNION ALL
    (
        SELECT
            'article'::TEXT,
            ac.article_id,
            'Article' AS source_label,
            ac.section_title,
            ac.chunk_text,
            (1 - (ac.embedding <=> query_embedding))::FLOAT
        FROM article_chunks ac
        WHERE (1 - (ac.embedding <=> query_embedding)) > match_threshold
    )
    UNION ALL
    (
        SELECT
            'filing'::TEXT,
            cfc.id,
            cfc.ticker || ' ' || cfc.filing_type || ' ' || cfc.fiscal_year::TEXT AS source_label,
            cfc.section_title,
            cfc.chunk_text,
            (1 - (cfc.embedding <=> query_embedding))::FLOAT
        FROM company_filing_chunks cfc
        WHERE (1 - (cfc.embedding <=> query_embedding)) > match_threshold
    )
    ORDER BY similarity DESC
    LIMIT match_count;
END;
$$ LANGUAGE plpgsql STABLE;

COMMENT ON FUNCTION search_all_chunks IS 'Cross-source RAG search across books, articles, and SEC filings.';

-- =====================================================
-- MONITORING
-- =====================================================

CREATE OR REPLACE VIEW vector_search_stats AS
SELECT
    'book_chunks' AS table_name,
    COUNT(*) AS total_vectors,
    COUNT(*) FILTER (WHERE embedding IS NOT NULL) AS indexed_vectors,
    COALESCE(AVG(token_count), 0) AS avg_tokens,
    COUNT(DISTINCT book_id) AS unique_sources
FROM book_chunks
UNION ALL
SELECT
    'article_chunks',
    COUNT(*),
    COUNT(*) FILTER (WHERE embedding IS NOT NULL),
    COALESCE(AVG(token_count), 0),
    COUNT(DISTINCT article_id)
FROM article_chunks
UNION ALL
SELECT
    'company_filing_chunks',
    COUNT(*),
    COUNT(*) FILTER (WHERE embedding IS NOT NULL),
    COALESCE(AVG(token_count), 0),
    COUNT(DISTINCT ticker)
FROM company_filing_chunks;

-- =====================================================
-- VERIFICATION
-- =====================================================

DO $$
DECLARE
    idx_count INT;
BEGIN
    SELECT COUNT(*) INTO idx_count
    FROM pg_indexes
    WHERE schemaname = 'public'
    AND indexname LIKE '%embedding%';

    RAISE NOTICE '=================================================';
    RAISE NOTICE 'Vector Index Setup Complete';
    RAISE NOTICE 'HNSW indexes created: %', idx_count;
    RAISE NOTICE '';
    RAISE NOTICE 'Available search functions:';
    RAISE NOTICE '  search_book_chunks(embedding, threshold, limit, book_id)';
    RAISE NOTICE '  search_article_chunks(embedding, threshold, limit)';
    RAISE NOTICE '  search_filing_chunks(embedding, threshold, limit, ticker, filing_type)';
    RAISE NOTICE '  search_all_chunks(embedding, threshold, limit)';
    RAISE NOTICE '';
    RAISE NOTICE 'Monitoring:';
    RAISE NOTICE '  SELECT * FROM vector_search_stats;';
    RAISE NOTICE '';
    RAISE NOTICE 'Performance tuning:';
    RAISE NOTICE '  SET hnsw.ef_search = 40;  -- default 40, increase for better recall';
    RAISE NOTICE '=================================================';
END $$;
