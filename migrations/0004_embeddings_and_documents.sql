-- Semantic search storage. `documents` holds every piece of embeddable text
-- (post captions, comments, video transcripts, ...) with a generated
-- tsvector for keyword search; `embeddings` holds the vector for each
-- document so keyword and semantic search share the same source-of-truth
-- row via documents.id = embeddings.source_id.
--
-- Dimensions default to 1536 (OpenAI text-embedding-3-small). If the
-- embedding provider changes, add a migration to alter the column - the
-- application's EMBEDDING_DIMENSIONS setting must match this value.

create table if not exists documents (
    id uuid primary key default uuid_generate_v4(),
    source_type text not null,
    source_id uuid not null,
    platform text not null references platforms(name),
    content text not null,
    search_vector tsvector generated always as (to_tsvector('english', content)) stored,
    metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    unique (source_type, source_id)
);
create index if not exists idx_documents_search on documents using gin (search_vector);
create index if not exists idx_documents_platform on documents(platform);

create table if not exists embeddings (
    id uuid primary key default uuid_generate_v4(),
    document_id uuid not null references documents(id) on delete cascade,
    source_type text not null,
    source_id uuid not null,
    platform text not null references platforms(name),
    model text not null,
    dimensions integer not null,
    checksum text not null,
    vector vector(1536),
    metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    unique (source_id, source_type, model)
);
create index if not exists idx_embeddings_document on embeddings(document_id);
create index if not exists idx_embeddings_checksum on embeddings(checksum);
-- IVFFlat approximate-nearest-neighbor index for cosine similarity search.
-- `lists` should be tuned to sqrt(row_count) in production; 100 is a
-- reasonable default until the table grows past ~1M rows.
create index if not exists idx_embeddings_vector on embeddings
    using ivfflat (vector vector_cosine_ops) with (lists = 100);

-- RPC used by the retrieval layer for semantic search: returns the top-N
-- documents by cosine similarity, optionally filtered by platform.
create or replace function match_embeddings(
    query_embedding vector(1536),
    match_count integer default 10,
    filter_platform text default null
)
returns table (
    document_id uuid,
    source_type text,
    source_id uuid,
    platform text,
    content text,
    similarity float
)
language sql stable
as $$
    select
        d.id as document_id,
        e.source_type,
        e.source_id,
        e.platform,
        d.content,
        1 - (e.vector <=> query_embedding) as similarity
    from embeddings e
    join documents d on d.id = e.document_id
    where filter_platform is null or e.platform = filter_platform
    order by e.vector <=> query_embedding
    limit match_count;
$$;
