# Project Prompt: Multi-Platform Social Media Intelligence Platform using Apify, Supabase, Pydantic, Gradio & AI Agent

You are an expert Python software architect specializing in data engineering, LLM applications, AI agents, Supabase, Apify, and production-grade software design.

Design and implement a complete, modular, production-ready Python project.

The project should follow clean architecture, proper separation of concerns, typed models, logging, configuration management, and be easily extensible.

---

# Objective

Build a Social Media Intelligence Platform that:

1. Scrapes content from
   - Instagram
   - X (Twitter)
   - YouTube

using the Apify API.

2. Extracts structured information including

- Post content
- Caption
- Comments
- Replies
- Likes
- Views
- Shares (if available)
- Hashtags
- Mentions
- URLs
- Timestamp
- Language
- Media URLs
- Image URLs
- Video URLs
- Channel/User/Profile metadata
- Engagement metrics
- Platform-specific metadata
- Any additional metadata exposed by Apify

3. Normalize all scraped data into common Pydantic models.

4. Store the normalized data into Supabase.

5. Build an AI assistant that answers questions using the stored data.

6. Build a Gradio Chat UI.

7. Store every user query and AI response into Supabase.

The architecture should support future addition of new platforms like:

- Reddit
- LinkedIn
- Facebook
- TikTok
- News websites

without changing existing code.

---

# Tech Stack

Use

- Python 3.12+
- Apify API
- Supabase
- Pydantic v2
- SQLAlchemy (optional)
- httpx
- AsyncIO
- Gradio
- OpenAI SDK (Responses API)
- LangChain only if genuinely useful
- python-dotenv
- Rich logging
- Loguru
- Pandas
- Tenacity
- Uvicorn/FastAPI (optional backend)

Do NOT use unnecessary frameworks.

---

# Project Structure

Design a scalable folder structure such as

project/

    app/

        config/

        database/

        models/

            pydantic/

            db/

        apify/

            instagram/

            twitter/

            youtube/

        services/

        repositories/

        ingestion/

        normalization/

        embeddings/

        retrieval/

        ai/

        prompts/

        gradio/

        utils/

        logging/

    scripts/

    tests/

    docs/

    .env

    requirements.txt

    README.md

---

# Configuration

Create a centralized configuration system using Pydantic Settings.

Support

- APIFY_API_TOKEN
- SUPABASE_URL
- SUPABASE_KEY
- OPENAI_API_KEY

and any future API keys.

---

# Scraping Layer

Implement separate scraper modules for

Instagram

X

YouTube

Each scraper should support

- Profile scraping
- Post scraping
- Keyword search
- Hashtag search
- Comment extraction
- Metadata extraction

The scraper layer should expose a common interface such as

BaseScraper

with methods like

scrape_profile()

scrape_posts()

scrape_comments()

scrape_hashtag()

scrape_keyword()

Return only typed Pydantic objects.

---

# Pydantic Models

Design normalized models.

Examples

Platform

Author

Post

Comment

Media

Hashtag

Mention

Engagement

Channel

Video

Thread

Reply

QueryLog

Conversation

ChatMessage

EmbeddingDocument

Every model should

- be fully typed
- include validators
- include computed fields where useful

---

# Database Design

Design a normalized relational schema in Supabase.

Suggested tables

platforms

authors

posts

comments

media

hashtags

mentions

post_hashtags

engagement

channels

videos

users

conversations

messages

query_logs

assistant_logs

embeddings

documents

Include

Primary Keys

Foreign Keys

Indexes

Unique Constraints

Created_at

Updated_at

Soft delete support

---

# Repository Layer

Implement repositories for every entity.

Examples

PostRepository

CommentRepository

AuthorRepository

ConversationRepository

QueryRepository

Repositories should completely isolate database logic.

---

# Ingestion Pipeline

Pipeline

Apify

↓

Raw JSON

↓

Validation

↓

Pydantic

↓

Normalization

↓

Deduplication

↓

Supabase

↓

Embedding generation

↓

Vector storage

---

# Embedding Pipeline

Create embeddings for

Posts

Comments

Captions

Descriptions

Video transcripts (when available)

Store embeddings for semantic search.

Design it so embedding providers can easily be swapped.

---

# Retrieval Layer

Implement hybrid retrieval supporting

Keyword search

Metadata filtering

Semantic search

Time filtering

Platform filtering

Author filtering

Hashtag filtering

Popularity filtering

---

# AI Assistant

Create an intelligent assistant capable of answering questions like

"What were the most liked Instagram posts this month?"

"Summarize discussions about AI."

"Which hashtags are trending?"

"Show YouTube videos discussing climate change."

"What are the common sentiments about OpenAI?"

"What were the most controversial tweets?"

"Compare Instagram and X engagement."

The assistant should

Understand the schema

Generate SQL when appropriate

Use semantic search when needed

Retrieve relevant records

Generate natural language responses

Cite retrieved records

---

# Prompt Engineering

Create reusable prompt templates for

SQL generation

Summarization

Trend analysis

Sentiment analysis

Cross-platform comparison

Conversation memory

---

# Chat Interface

Build a Gradio interface.

Features

Modern chat UI

Conversation history

Streaming responses

Clear chat

New conversation

Conversation sidebar

Search conversations

Export conversation

Dark mode friendly

---

# Conversation Storage

Store every interaction.

Tables

conversations

messages

query_logs

assistant_logs

Each message should include

conversation_id

user_message

assistant_response

retrieved_documents

execution_time

model_used

timestamp

SQL generated (if any)

Sources used

---

# Analytics Dashboard

Create simple dashboards showing

Number of scraped posts

Platform distribution

Most active authors

Trending hashtags

Posting frequency

Engagement metrics

Top keywords

Sentiment distribution

Recent scraping jobs

---

# Logging

Implement structured logging.

Log

API calls

Retries

Failures

Database inserts

Embeddings

AI requests

User chats

Errors

Performance metrics

---

# Error Handling

Implement

Retry logic

Rate limit handling

Graceful failures

Validation errors

Partial ingestion recovery

Dead-letter queue support

---

# Testing

Include

Unit tests

Integration tests

Mock Apify responses

Mock Supabase

Mock OpenAI

---

# Documentation

Generate

README

Architecture diagram

ER diagram

Sequence diagrams

Setup guide

Deployment guide

Environment variable documentation

API documentation

Developer guide

---

# Future Extensibility

Design plugin interfaces so future connectors can be added for

Reddit

LinkedIn

TikTok

Facebook

RSS

News APIs

without modifying the existing architecture.

---

# Code Quality Requirements

- Strong type hints everywhere.
- Pydantic v2 models.
- Async where appropriate.
- Comprehensive docstrings.
- SOLID principles.
- Repository pattern.
- Dependency injection where useful.
- Minimal code duplication.
- Production-ready folder organization.
- Rich comments explaining architectural decisions.

---

# Final Deliverable

Generate the complete project incrementally.

For each phase:

1. Explain the architecture.
2. Generate the folder structure.
3. Generate the code.
4. Explain design decisions.
5. Wait for confirmation before proceeding to the next phase.

Begin with Phase 1:
- High-level architecture
- Folder structure
- Database schema
- Pydantic model design
- Data flow diagram


# Success Criteria

The project is considered complete only if all the following criteria are satisfied.

---

# 1. Architecture

## Success Criteria

- [ ] Clean Architecture is followed.
- [ ] Responsibilities are clearly separated.
- [ ] Every module has a single responsibility.
- [ ] Project is easily extensible.
- [ ] New platforms can be added with minimal code changes.
- [ ] No circular imports.
- [ ] Configuration is centralized.

---

# 2. Code Quality

## Success Criteria

- [ ] Python 3.12+ compatible.
- [ ] Fully type hinted.
- [ ] Pydantic v2 used throughout.
- [ ] Async I/O used where appropriate.
- [ ] Proper logging implemented.
- [ ] No duplicated business logic.
- [ ] SOLID principles followed.
- [ ] Repository pattern implemented.
- [ ] Services contain business logic only.
- [ ] Database logic isolated.
- [ ] Functions remain concise and modular.
- [ ] Code passes Ruff, Black, and MyPy.

---

# 3. Apify Integration

## Success Criteria

### Instagram

- [ ] Profile scraping works.
- [ ] Post scraping works.
- [ ] Hashtag scraping works.
- [ ] Comment scraping works.
- [ ] Metadata extraction works.

### X

- [ ] Profile scraping works.
- [ ] Tweet scraping works.
- [ ] Keyword search works.
- [ ] Hashtag search works.
- [ ] Comment/reply scraping works.

### YouTube

- [ ] Channel scraping works.
- [ ] Video scraping works.
- [ ] Comment scraping works.
- [ ] Metadata extraction works.
- [ ] Transcript extraction supported when available.

### General

- [ ] API failures handled gracefully.
- [ ] Automatic retries implemented.
- [ ] Rate limits respected.
- [ ] Failed jobs logged.
- [ ] Scrapers return normalized Pydantic models.

---

# 4. Data Extraction

## Success Criteria

Each supported platform extracts whenever available:

- [ ] Content
- [ ] Caption
- [ ] Description
- [ ] Comments
- [ ] Replies
- [ ] Likes
- [ ] Views
- [ ] Shares
- [ ] Hashtags
- [ ] Mentions
- [ ] URLs
- [ ] Media
- [ ] Images
- [ ] Videos
- [ ] Timestamp
- [ ] Language
- [ ] Author metadata
- [ ] Engagement metrics
- [ ] Platform-specific metadata

---

# 5. Pydantic Models

## Success Criteria

- [ ] Every entity represented by a typed model.
- [ ] Validators implemented.
- [ ] Optional fields handled correctly.
- [ ] Nested models supported.
- [ ] Serialization works.
- [ ] Deserialization works.
- [ ] Validation catches malformed data.

---

# 6. Data Normalization

## Success Criteria

- [ ] Platform-specific fields mapped into unified schema.
- [ ] Duplicate authors merged.
- [ ] Duplicate posts detected.
- [ ] Duplicate comments detected.
- [ ] Relationships preserved.
- [ ] Missing values handled correctly.

---

# 7. Database (Supabase)

## Success Criteria

### Tables

- [ ] Authors
- [ ] Posts
- [ ] Comments
- [ ] Media
- [ ] Hashtags
- [ ] Mentions
- [ ] Engagement
- [ ] Conversations
- [ ] Messages
- [ ] Query Logs
- [ ] Assistant Logs
- [ ] Embeddings

### Database Design

- [ ] Foreign keys exist.
- [ ] Primary keys exist.
- [ ] Proper indexing.
- [ ] Unique constraints.
- [ ] Cascading relationships.
- [ ] Soft deletes supported.
- [ ] Timestamp columns included.

---

# 8. Ingestion Pipeline

## Success Criteria

Pipeline executes as

Apify

↓

Raw JSON

↓

Validation

↓

Normalization

↓

Deduplication

↓

Database

↓

Embedding Generation

↓

Vector Storage

Checks

- [ ] Pipeline fully automated.
- [ ] Invalid records skipped safely.
- [ ] Pipeline resumable.
- [ ] Progress reporting available.
- [ ] Failures logged.

---

# 9. Embedding Pipeline

## Success Criteria

- [ ] Posts embedded.
- [ ] Comments embedded.
- [ ] Captions embedded.
- [ ] Descriptions embedded.
- [ ] Duplicate embeddings avoided.
- [ ] Batch processing supported.
- [ ] Embeddings linked to source records.

---

# 10. Retrieval Engine

## Success Criteria

Supports

- [ ] Keyword search
- [ ] Semantic search
- [ ] Platform filtering
- [ ] Date filtering
- [ ] Author filtering
- [ ] Hashtag filtering
- [ ] Popularity filtering
- [ ] Combined filters

Results should

- [ ] Return ranked results.
- [ ] Include metadata.
- [ ] Include confidence scores where applicable.

---

# 11. AI Assistant

## Success Criteria

Assistant can answer

- [ ] Trending hashtags
- [ ] Most liked posts
- [ ] Platform comparisons
- [ ] User summaries
- [ ] Author summaries
- [ ] Topic summaries
- [ ] Sentiment analysis
- [ ] Time-series analysis
- [ ] Cross-platform insights

Assistant behavior

- [ ] Retrieves relevant documents.
- [ ] Cites retrieved sources.
- [ ] Avoids hallucinations.
- [ ] Uses SQL when appropriate.
- [ ] Uses semantic retrieval when appropriate.
- [ ] Supports conversation memory.

---

# 12. Chat Interface

## Success Criteria

Gradio interface includes

- [ ] Chat window
- [ ] Conversation history
- [ ] New chat
- [ ] Clear chat
- [ ] Streaming responses
- [ ] Markdown rendering
- [ ] Copy responses
- [ ] Export chat
- [ ] Search conversations
- [ ] Responsive layout

---

# 13. Conversation Logging

## Success Criteria

Every interaction stores

- [ ] Conversation ID
- [ ] User query
- [ ] Assistant response
- [ ] Retrieved documents
- [ ] Generated SQL
- [ ] Prompt used
- [ ] Model used
- [ ] Execution time
- [ ] Token usage
- [ ] Timestamp

---

# 14. Analytics

## Success Criteria

Dashboard shows

- [ ] Total posts
- [ ] Total comments
- [ ] Platform distribution
- [ ] Trending hashtags
- [ ] Most active users
- [ ] Engagement trends
- [ ] Daily scraping statistics
- [ ] AI query statistics

---

# 15. Logging

## Success Criteria

Logs include

- [ ] API requests
- [ ] API responses
- [ ] Retries
- [ ] Errors
- [ ] Database inserts
- [ ] Database updates
- [ ] AI requests
- [ ] Retrieval latency
- [ ] Embedding generation
- [ ] User conversations

---

# 16. Error Handling

## Success Criteria

- [ ] Graceful API failures.
- [ ] Retry mechanism.
- [ ] Timeout handling.
- [ ] Validation failures logged.
- [ ] Corrupted records skipped.
- [ ] Partial pipeline recovery.
- [ ] Database rollback on failure.
- [ ] User-friendly error messages.

---

# 17. Performance

## Success Criteria

- [ ] Supports concurrent scraping.
- [ ] Batch database inserts.
- [ ] Async requests.
- [ ] Pagination support.
- [ ] Memory efficient.
- [ ] Handles large datasets (>100k records).

---

# 18. Security

## Success Criteria

- [ ] Secrets stored in .env.
- [ ] No hardcoded credentials.
- [ ] Input validation.
- [ ] SQL injection protection.
- [ ] Safe prompt construction.
- [ ] Rate limiting respected.
- [ ] Sensitive logs masked.

---

# 19. Testing

## Success Criteria

Coverage includes

- [ ] Unit tests
- [ ] Integration tests
- [ ] Repository tests
- [ ] API mocks
- [ ] AI assistant tests
- [ ] Database tests
- [ ] Retrieval tests
- [ ] Pipeline tests

Minimum

- [ ] ≥90% code coverage.

---

# 20. Documentation

## Success Criteria

Project contains

- [ ] README
- [ ] Installation guide
- [ ] Architecture document
- [ ] ER diagram
- [ ] Sequence diagrams
- [ ] Configuration guide
- [ ] API documentation
- [ ] Developer guide
- [ ] Deployment guide
- [ ] Example workflows

---

# 21. Extensibility

## Success Criteria

Adding a new platform should require only:

- [ ] Creating a new scraper.
- [ ] Registering it.
- [ ] Adding platform-specific mappings.
- [ ] No modification to existing core services.
- [ ] No database redesign.
- [ ] No AI assistant changes.

---

# 22. End-to-End Validation

The following workflow completes successfully without manual intervention:

- [ ] User specifies a platform and search criteria.
- [ ] Apify scraper collects data.
- [ ] Raw JSON is validated.
- [ ] Data is normalized.
- [ ] Records are deduplicated.
- [ ] Data is stored in Supabase.
- [ ] Embeddings are generated.
- [ ] AI assistant indexes new data.
- [ ] User queries the assistant.
- [ ] Relevant documents are retrieved.
- [ ] Assistant generates an accurate response.
- [ ] Query and response are stored in Supabase.
- [ ] Analytics dashboard reflects the new data.

---

# 23. Production Readiness Checklist

- [ ] All tests pass.
- [ ] Linting passes.
- [ ] Type checking passes.
- [ ] Docker support included (optional but recommended).
- [ ] CI/CD workflow included.
- [ ] Comprehensive logging.
- [ ] Robust error handling.
- [ ] Performance benchmark documented.
- [ ] No critical TODOs remain.
- [ ] Codebase is modular, maintainable, and ready for deployment.