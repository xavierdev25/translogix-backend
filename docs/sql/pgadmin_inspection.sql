-- TransLogix AI: read-only inspection queries for pgAdmin (database "translogix").
-- None of these modify data. Run them one by one.

-- 1. Extensions (pgvector must appear as "vector").
SELECT extname, extversion FROM pg_extension ORDER BY extname;

-- 2. Applied migration.
SELECT version_num FROM alembic_version;

-- 3. Tables and approximate row counts.
SELECT relname AS tabla, n_live_tup AS filas
FROM pg_stat_user_tables ORDER BY relname;

-- 4. Primary keys, foreign keys and unique constraints.
SELECT tc.table_name, tc.constraint_type, tc.constraint_name,
       string_agg(kcu.column_name, ', ') AS columnas,
       max(ccu.table_name) AS tabla_referenciada
FROM information_schema.table_constraints tc
JOIN information_schema.key_column_usage kcu
  ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
LEFT JOIN information_schema.constraint_column_usage ccu
  ON tc.constraint_type = 'FOREIGN KEY' AND ccu.constraint_name = tc.constraint_name
WHERE tc.table_schema = 'public'
  AND tc.constraint_type IN ('PRIMARY KEY', 'FOREIGN KEY', 'UNIQUE')
GROUP BY 1, 2, 3 ORDER BY 1, 2;

-- 5. Indexes.
SELECT tablename, indexname, indexdef
FROM pg_indexes WHERE schemaname = 'public' ORDER BY tablename, indexname;

-- 6. Columns and types (embedding is vector(1024)).
SELECT table_name, column_name, data_type, udt_name, is_nullable
FROM information_schema.columns
WHERE table_schema = 'public' ORDER BY table_name, ordinal_position;

-- 7. RAG index: chunks per document.
SELECT d.doc_code, d.doc_title, count(c.chunk_id) AS chunks, d.ingested_at
FROM rag_documents d LEFT JOIN rag_chunks c USING (doc_code)
GROUP BY d.doc_code, d.doc_title, d.ingested_at ORDER BY d.doc_code;

-- 8. Embedding dimension actually stored.
SELECT doc_code, vector_dims(embedding) AS dim, count(*)
FROM rag_chunks GROUP BY 1, 2;

-- 9. Conversations by channel and status (the user id appears only as an HMAC hash).
SELECT channel, status, count(*) FROM conversations GROUP BY 1, 2 ORDER BY 1, 2;

-- 10. Handoffs by queue, priority and status.
SELECT status, payload_json->>'cola_destino' AS cola, payload_json->>'prioridad' AS prioridad,
       count(*)
FROM handoffs GROUP BY 1, 2, 3 ORDER BY 1, 2;

-- 11. Audit trail: intent, services, LLM use and latency per turn.
SELECT created_at, channel, intent, services_called, llm_used, latency_ms
FROM audit_events ORDER BY created_at DESC LIMIT 50;

-- 12. Idempotency: no (channel, message_id, direction) should repeat (expected: 0 rows).
SELECT channel, message_id, direction, count(*)
FROM messages GROUP BY 1, 2, 3 HAVING count(*) > 1;

-- 13. PII check over persisted JSON (expected: 0). Adjust the pattern to your fixtures.
SELECT 'messages' AS tabla, count(*) FROM messages
WHERE content_json::text ~ '\+57[0-9]{10}|Placa [A-Z]{3}-[0-9]{3}'
UNION ALL
SELECT 'handoffs', count(*) FROM handoffs
WHERE payload_json::text ~ '\+57[0-9]{10}|Placa [A-Z]{3}-[0-9]{3}';
