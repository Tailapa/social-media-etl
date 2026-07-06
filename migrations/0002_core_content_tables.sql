-- Core content schema: platforms, authors, channels, posts, videos, media,
-- hashtags, mentions, engagement, comments.
--
-- Conventions applied uniformly:
--   * uuid primary keys (generated client-side by Pydantic, defaulted here too)
--   * created_at/updated_at on every table, updated_at maintained by trigger
--   * deleted_at nullable timestamp = soft delete (never hard-delete content)
--   * unique(platform, platform_native_id) = the natural dedup key

create table if not exists platforms (
    id uuid primary key default uuid_generate_v4(),
    name text unique not null,
    display_name text not null,
    is_active boolean not null default true,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists authors (
    id uuid primary key default uuid_generate_v4(),
    platform text not null references platforms(name),
    platform_user_id text not null,
    username text not null,
    display_name text,
    bio text,
    profile_url text,
    avatar_url text,
    is_verified boolean not null default false,
    is_private boolean not null default false,
    follower_count bigint check (follower_count is null or follower_count >= 0),
    following_count bigint check (following_count is null or following_count >= 0),
    post_count bigint check (post_count is null or post_count >= 0),
    location text,
    external_url text,
    platform_metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    deleted_at timestamptz,
    unique (platform, platform_user_id)
);
create index if not exists idx_authors_platform on authors(platform) where deleted_at is null;
create index if not exists idx_authors_username on authors using gin (username gin_trgm_ops);

create table if not exists channels (
    id uuid primary key default uuid_generate_v4(),
    platform text not null references platforms(name),
    platform_channel_id text not null,
    author_id uuid not null references authors(id) on delete cascade,
    name text not null,
    description text,
    subscriber_count bigint check (subscriber_count is null or subscriber_count >= 0),
    video_count bigint check (video_count is null or video_count >= 0),
    total_views bigint check (total_views is null or total_views >= 0),
    country text,
    platform_metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    deleted_at timestamptz,
    unique (platform, platform_channel_id)
);
create index if not exists idx_channels_author on channels(author_id);

create table if not exists posts (
    id uuid primary key default uuid_generate_v4(),
    platform text not null references platforms(name),
    platform_post_id text not null,
    author_id uuid not null references authors(id) on delete cascade,
    content_type text not null,
    caption text,
    content text,
    language text,
    url text,
    hashtags text[] not null default '{}',
    mentions text[] not null default '{}',
    urls text[] not null default '{}',
    posted_at timestamptz,
    is_pinned boolean not null default false,
    is_sponsored boolean not null default false,
    location text,
    platform_metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    deleted_at timestamptz,
    unique (platform, platform_post_id)
);
create index if not exists idx_posts_platform on posts(platform) where deleted_at is null;
create index if not exists idx_posts_author on posts(author_id);
create index if not exists idx_posts_posted_at on posts(posted_at desc);
create index if not exists idx_posts_hashtags on posts using gin (hashtags);
create index if not exists idx_posts_caption_trgm on posts using gin (caption gin_trgm_ops);

create table if not exists videos (
    id uuid primary key default uuid_generate_v4(),
    platform text not null references platforms(name),
    platform_video_id text not null,
    channel_id uuid not null references channels(id) on delete cascade,
    post_id uuid references posts(id) on delete set null,
    title text not null,
    description text,
    transcript text,
    duration_seconds numeric check (duration_seconds is null or duration_seconds >= 0),
    thumbnail_url text,
    video_url text,
    published_at timestamptz,
    language text,
    platform_metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    deleted_at timestamptz,
    unique (platform, platform_video_id)
);
create index if not exists idx_videos_channel on videos(channel_id);
create index if not exists idx_videos_published_at on videos(published_at desc);

create table if not exists media (
    id uuid primary key default uuid_generate_v4(),
    post_id uuid references posts(id) on delete cascade,
    media_type text not null,
    url text not null,
    thumbnail_url text,
    width integer check (width is null or width >= 0),
    height integer check (height is null or height >= 0),
    duration_seconds numeric check (duration_seconds is null or duration_seconds >= 0),
    file_size_bytes bigint check (file_size_bytes is null or file_size_bytes >= 0),
    alt_text text,
    order_index integer not null default 0,
    created_at timestamptz not null default now()
);
create index if not exists idx_media_post on media(post_id);

create table if not exists hashtags (
    id uuid primary key default uuid_generate_v4(),
    tag text unique not null,
    created_at timestamptz not null default now()
);

create table if not exists post_hashtags (
    post_id uuid not null references posts(id) on delete cascade,
    hashtag_id uuid not null references hashtags(id) on delete cascade,
    primary key (post_id, hashtag_id)
);
create index if not exists idx_post_hashtags_hashtag on post_hashtags(hashtag_id);

create table if not exists comments (
    id uuid primary key default uuid_generate_v4(),
    platform text not null references platforms(name),
    platform_comment_id text not null,
    post_id uuid not null references posts(id) on delete cascade,
    author_id uuid not null references authors(id) on delete cascade,
    parent_comment_id uuid references comments(id) on delete cascade,
    content text not null,
    language text,
    likes bigint check (likes is null or likes >= 0),
    reply_count bigint check (reply_count is null or reply_count >= 0),
    hashtags text[] not null default '{}',
    mentions text[] not null default '{}',
    posted_at timestamptz,
    platform_metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    deleted_at timestamptz,
    unique (platform, platform_comment_id)
);
create index if not exists idx_comments_post on comments(post_id);
create index if not exists idx_comments_parent on comments(parent_comment_id);
create index if not exists idx_comments_author on comments(author_id);

create table if not exists mentions (
    id uuid primary key default uuid_generate_v4(),
    post_id uuid references posts(id) on delete cascade,
    comment_id uuid references comments(id) on delete cascade,
    username text not null,
    created_at timestamptz not null default now(),
    constraint mentions_target_check check (post_id is not null or comment_id is not null)
);
create index if not exists idx_mentions_username on mentions(username);

create table if not exists engagement (
    id uuid primary key default uuid_generate_v4(),
    post_id uuid not null unique references posts(id) on delete cascade,
    likes bigint check (likes is null or likes >= 0),
    views bigint check (views is null or views >= 0),
    shares bigint check (shares is null or shares >= 0),
    comments_count bigint check (comments_count is null or comments_count >= 0),
    saves bigint check (saves is null or saves >= 0),
    reactions jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);
create index if not exists idx_engagement_likes on engagement(likes desc);

-- updated_at triggers. Postgres has no `CREATE TRIGGER IF NOT EXISTS`, so
-- each is dropped first -- this keeps the whole migration file safe to
-- re-run (via `scripts/run_migrations.py`), matching every `create table`/
-- `create index` above already being idempotent.
drop trigger if exists trg_authors_updated_at on authors;
create trigger trg_authors_updated_at before update on authors
    for each row execute function set_updated_at();
drop trigger if exists trg_channels_updated_at on channels;
create trigger trg_channels_updated_at before update on channels
    for each row execute function set_updated_at();
drop trigger if exists trg_posts_updated_at on posts;
create trigger trg_posts_updated_at before update on posts
    for each row execute function set_updated_at();
drop trigger if exists trg_videos_updated_at on videos;
create trigger trg_videos_updated_at before update on videos
    for each row execute function set_updated_at();
drop trigger if exists trg_comments_updated_at on comments;
create trigger trg_comments_updated_at before update on comments
    for each row execute function set_updated_at();
drop trigger if exists trg_engagement_updated_at on engagement;
create trigger trg_engagement_updated_at before update on engagement
    for each row execute function set_updated_at();
drop trigger if exists trg_platforms_updated_at on platforms;
create trigger trg_platforms_updated_at before update on platforms
    for each row execute function set_updated_at();

insert into platforms (name, display_name) values
    ('instagram', 'Instagram'),
    ('twitter', 'X (Twitter)'),
    ('youtube', 'YouTube'),
    ('reddit', 'Reddit'),
    ('linkedin', 'LinkedIn'),
    ('facebook', 'Facebook'),
    ('tiktok', 'TikTok'),
    ('news', 'News')
on conflict (name) do nothing;
