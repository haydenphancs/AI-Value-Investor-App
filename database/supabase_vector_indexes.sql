-- =====================================================
-- Vector Indexes for Semantic Search
-- Run this AFTER loading educational content chunks
-- =====================================================

-- IMPORTANT: Only create vector indexes after you have data!
-- Creating indexes on empty tables is fast but inefficient.
-- Wait until you have at least 1000+ chunks loaded.

-- =====================================================
-- CHECK DATA BEFORE CREATING INDEXES
-- =====================================================

DO $$
DECLARE
    content_chunk_count INTEGER;
    article_chunk_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO content_chunk_count FROM content_chunks;
    SELECT COUNT(*) INTO article_chunk_count FROM article_chunks;
    
    RAISE NOTICE '=================================================';
    RAISE NOTICE 'Current Data Status:';
    RAISE NOTICE 'Content chunks: %', content_chunk_count;
    RAISE NOTICE 'Article chunks: %', article_chunk_count;
    RAISE NOTICE '=================================================';
    
    IF content_chunk_count < 100 THEN
        RAISE WARNING 'Content chunks count is low (%). Consider loading more data before creating indexes.', content_chunk_count;
    END IF;
    
    IF article_chunk_count < 100 THEN
        RAISE WARNING 'Article chunks count is low (%). Consider loading more data before creating indexes.', article_chunk_count;
    END IF;
END $$;

-- =====================================================
-- VECTOR INDEX CONFIGURATIONS
-- =====================================================

-- IVF-Flat is good for datasets < 1M vectors
-- For larger datasets, consider HNSW (when pgvector supports it in Supabase)

-- Number of lists for IVF index
-- Rule of thumb: lists = sqrt(number_of_rows)
-- For 10k rows: lists = 100
-- For 100k rows: lists = 316
-- For 1M rows: lists = 1000

-- =====================================================
-- CREATE VECTOR INDEXES
-- =====================================================

-- Index for content_chunks (books)
-- Adjust 'lists' parameter based on your data size
CREATE INDEX IF NOT EXISTS idx_content_chunks_embedding_ivfflat
ON content_chunks
USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 100);

-- Index for article_chunks
CREATE INDEX IF NOT EXISTS idx_article_chunks_embedding_ivfflat
ON article_chunks
USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 100);

-- =====================================================
-- ANALYZE TABLES FOR QUERY OPTIMIZATION
-- =====================================================

ANALYZE content_chunks;
ANALYZE article_chunks;

-- =====================================================
-- TEST VECTOR SEARCH PERFORMANCE
-- =====================================================

-- Create a test function
CREATE OR REPLACE FUNCTION test_vector_search()
RETURNS TABLE (
    chunk_text TEXT,
    similarity NUMERIC
) AS $$
BEGIN
    -- Generate a random vector for testing
    RETURN QUERY
    SELECT 
        cc.chunk_text,
        (1 - (cc.embedding <=> '[0.1, 0.2, 0.3]'::vector(3)))::NUMERIC as similarity
    FROM content_chunks cc
    LIMIT 5;
EXCEPTION
    WHEN OTHERS THEN
        RAISE NOTICE 'Test search failed. This is normal if you have no data yet.';
        RETURN;
END;
$$ LANGUAGE plpgsql;

-- =====================================================
-- OPTIMAL SEARCH QUERIES
-- =====================================================

-- Query template for content search
COMMENT ON TABLE content_chunks IS '
Optimal search query:

SELECT 
    cc.chunk_text,
    cc.section_title,
    ec.title as book_title,
    ec.author,
    1 - (cc.embedding <=> $1::vector) AS similarity
FROM content_chunks cc
JOIN educational_content ec ON cc.content_id = ec.id
WHERE 1 - (cc.embedding <=> $1::vector) > 0.7  -- Similarity threshold
ORDER BY cc.embedding <=> $1::vector
LIMIT 5;

Notes:
- Use <=> operator for cosine distance (faster with index)
- Lower distance = higher similarity
- Typical threshold: 0.7-0.8 for good matches
';

-- Query template for article search
COMMENT ON TABLE article_chunks IS '
Optimal search query:

SELECT 
    ac.chunk_text,
    ac.section_title,
    1 - (ac.embedding <=> $1::vector) AS similarity
FROM article_chunks ac
WHERE 1 - (ac.embedding <=> $1::vector) > 0.7
ORDER BY ac.embedding <=> $1::vector
LIMIT 5;
';

-- =====================================================
-- MAINTENANCE FUNCTIONS
-- =====================================================

-- Rebuild vector indexes (run periodically if data grows significantly)
CREATE OR REPLACE FUNCTION rebuild_vector_indexes()
RETURNS void AS $$
BEGIN
    RAISE NOTICE 'Reindexing content_chunks...';
    REINDEX INDEX CONCURRENTLY idx_content_chunks_embedding_ivfflat;
    
    RAISE NOTICE 'Reindexing article_chunks...';
    REINDEX INDEX CONCURRENTLY idx_article_chunks_embedding_ivfflat;
    
    RAISE NOTICE 'Analyzing tables...';
    ANALYZE content_chunks;
    ANALYZE article_chunks;
    
    RAISE NOTICE 'Vector indexes rebuilt successfully!';
END;
$$ LANGUAGE plpgsql;

-- Check index sizes
CREATE OR REPLACE FUNCTION check_vector_index_sizes()
RETURNS TABLE (
    index_name TEXT,
    table_name TEXT,
    index_size TEXT,
    table_size TEXT
) AS $$
BEGIN
    RETURN QUERY
    SELECT 
        indexname::TEXT,
        tablename::TEXT,
        pg_size_pretty(pg_relation_size(indexname::regclass)) as index_size,
        pg_size_pretty(pg_relation_size(tablename::regclass)) as table_size
    FROM pg_indexes
    WHERE schemaname = 'public'
    AND indexname LIKE '%embedding%';
END;
$$ LANGUAGE plpgsql;

-- =====================================================
-- PERFORMANCE TUNING
-- =====================================================

-- Set probes for IVF search (higher = more accurate but slower)
-- Default is 1, max is number of lists
-- For better recall, increase probes (e.g., 10-20)
-- SET ivfflat.probes = 10;

-- Create view for monitoring vector search performance
CREATE OR REPLACE VIEW vector_search_stats AS
SELECT 
    'content_chunks' as table_name,
    COUNT(*) as total_vectors,
    AVG(token_count) as avg_tokens,
    COUNT(DISTINCT content_id) as unique_contents
FROM content_chunks
UNION ALL
SELECT 
    'article_chunks' as table_name,
    COUNT(*) as total_vectors,
    AVG(token_count) as avg_tokens,
    COUNT(DISTINCT article_id) as unique_contents
FROM article_chunks;

-- =====================================================
-- VERIFICATION AND RECOMMENDATIONS
-- =====================================================

DO $$
DECLARE
    content_count INTEGER;
    article_count INTEGER;
    recommended_lists INTEGER;
BEGIN
    SELECT COUNT(*) INTO content_count FROM content_chunks;
    SELECT COUNT(*) INTO article_count FROM article_chunks;
    
    RAISE NOTICE '=================================================';
    RAISE NOTICE 'Vector Index Setup Complete!';
    RAISE NOTICE '=================================================';
    RAISE NOTICE 'Content chunks indexed: %', content_count;
    RAISE NOTICE 'Article chunks indexed: %', article_count;
    RAISE NOTICE '';
    
    -- Recommendations
    IF content_count > 10000 THEN
        recommended_lists := SQRT(content_count)::INTEGER;
        RAISE NOTICE 'RECOMMENDATION: Your content_chunks table has grown.';
        RAISE NOTICE 'Consider recreating the index with lists = % for better performance', recommended_lists;
        RAISE NOTICE 'Run: DROP INDEX idx_content_chunks_embedding_ivfflat;';
        RAISE NOTICE '     CREATE INDEX idx_content_chunks_embedding_ivfflat';
        RAISE NOTICE '     ON content_chunks USING ivfflat (embedding vector_cosine_ops)';
        RAISE NOTICE '     WITH (lists = %);', recommended_lists;
        RAISE NOTICE '';
    END IF;
    
    IF article_count > 10000 THEN
        recommended_lists := SQRT(article_count)::INTEGER;
        RAISE NOTICE 'RECOMMENDATION: Your article_chunks table has grown.';
        RAISE NOTICE 'Consider recreating the index with lists = % for better performance', recommended_lists;
        RAISE NOTICE '';
    END IF;
    
    RAISE NOTICE 'Performance Tips:';
    RAISE NOTICE '1. Run SELECT check_vector_index_sizes(); to monitor index sizes';
    RAISE NOTICE '2. Run SELECT * FROM vector_search_stats; to see data distribution';
    RAISE NOTICE '3. Increase ivfflat.probes for better recall (SET ivfflat.probes = 10)';
    RAISE NOTICE '4. Rebuild indexes monthly: SELECT rebuild_vector_indexes();';
    RAISE NOTICE '=================================================';
END $$;

-- =====================================================
-- EXAMPLE USAGE
-- =====================================================

-- Example: Search for content about "value investing"
-- 
-- Step 1: Generate embedding (in your application)
-- embedding = openai.embeddings.create(
--     model="text-embedding-ada-002",
--     input="value investing strategies"
-- ).data[0].embedding
--
-- Step 2: Search database
-- SELECT 
--     cc.chunk_text,
--     ec.title,
--     1 - (cc.embedding <=> $1::vector) AS similarity
-- FROM content_chunks cc
-- JOIN educational_content ec ON cc.content_id = ec.id
-- ORDER BY cc.embedding <=> $1::vector
-- LIMIT 5;
