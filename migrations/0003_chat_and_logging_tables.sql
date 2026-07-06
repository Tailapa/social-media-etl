-- Chat / assistant persistence: users, conversations, messages, query_logs,
-- assistant_logs. Every user query and AI response is stored here per the
-- project requirement.

create table if not exists users (
    id uuid primary key default uuid_generate_v4(),
    email text unique,
    display_name text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);
drop trigger if exists trg_users_updated_at on users;
create trigger trg_users_updated_at before update on users
    for each row execute function set_updated_at();

create table if not exists conversations (
    id uuid primary key default uuid_generate_v4(),
    user_id uuid references users(id) on delete set null,
    title text,
    is_archived boolean not null default false,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    deleted_at timestamptz
);
create index if not exists idx_conversations_user on conversations(user_id) where deleted_at is null;
drop trigger if exists trg_conversations_updated_at on conversations;
create trigger trg_conversations_updated_at before update on conversations
    for each row execute function set_updated_at();

create table if not exists messages (
    id uuid primary key default uuid_generate_v4(),
    conversation_id uuid not null references conversations(id) on delete cascade,
    role text not null check (role in ('user', 'assistant', 'system')),
    content text not null,
    sources text[] not null default '{}',
    sql_generated text,
    model_used text,
    execution_time_ms numeric check (execution_time_ms is null or execution_time_ms >= 0),
    prompt_tokens integer check (prompt_tokens is null or prompt_tokens >= 0),
    completion_tokens integer check (completion_tokens is null or completion_tokens >= 0),
    created_at timestamptz not null default now()
);
create index if not exists idx_messages_conversation on messages(conversation_id, created_at);

create table if not exists query_logs (
    id uuid primary key default uuid_generate_v4(),
    conversation_id uuid references conversations(id) on delete set null,
    query_text text not null,
    retrieved_document_ids text[] not null default '{}',
    filters_applied jsonb not null default '{}'::jsonb,
    latency_ms numeric check (latency_ms is null or latency_ms >= 0),
    created_at timestamptz not null default now()
);
create index if not exists idx_query_logs_conversation on query_logs(conversation_id);
create index if not exists idx_query_logs_created_at on query_logs(created_at desc);

create table if not exists assistant_logs (
    id uuid primary key default uuid_generate_v4(),
    conversation_id uuid references conversations(id) on delete set null,
    message_id uuid references messages(id) on delete set null,
    prompt_used text not null,
    sql_generated text,
    model_used text not null,
    execution_time_ms numeric check (execution_time_ms is null or execution_time_ms >= 0),
    token_usage jsonb not null default '{}'::jsonb,
    error text,
    created_at timestamptz not null default now()
);
create index if not exists idx_assistant_logs_conversation on assistant_logs(conversation_id);

create table if not exists scrape_jobs (
    id uuid primary key default uuid_generate_v4(),
    platform text not null references platforms(name),
    job_type text not null,
    status text not null default 'pending'
        check (status in ('pending', 'running', 'succeeded', 'failed', 'partial')),
    target text,
    started_at timestamptz,
    finished_at timestamptz,
    records_scraped integer not null default 0,
    error text,
    created_at timestamptz not null default now()
);
create index if not exists idx_scrape_jobs_platform on scrape_jobs(platform, created_at desc);
