"""Reusable prompt templates for the AI assistant.

Kept as plain `.format()`-style string templates (not a templating engine)
because every prompt here is short, has a fixed set of named placeholders,
and pulling in Jinja2/langchain-prompts for this would be exactly the
"unnecessary framework" the spec warns against.
"""

from __future__ import annotations

SQL_GENERATION_PROMPT = """You are a PostgreSQL expert generating a single read-only query against a \
social media analytics database.

Schema:
{schema}

Rules:
- Output ONLY the SQL statement, no explanation, no markdown fences.
- Only SELECT or WITH statements are allowed. Never write INSERT/UPDATE/DELETE/DDL.
- Always filter `deleted_at is null` on tables that have that column, unless the question \
explicitly asks about deleted/removed content.
- Prefer explicit column lists over `select *`.
- Use ILIKE for case-insensitive text matching.
- Limit results to a reasonable number (default 20) unless the question asks for a count/aggregate.

Conversation context (may be empty):
{conversation_context}

Question: {question}

SQL:"""

SUMMARIZATION_PROMPT = """Summarize the following social media content in a concise, neutral tone. \
Focus on the main themes, sentiment, and any notable engagement patterns. Cite specific posts by \
their platform and author when relevant.

Content:
{content}

Summary:"""

TREND_ANALYSIS_PROMPT = """Analyze the following data for trends (topics, hashtags, engagement over \
time, or platform differences). Call out the top 3-5 patterns with supporting numbers.

Data:
{data}

Trend analysis:"""

SENTIMENT_ANALYSIS_PROMPT = """Classify the overall sentiment (positive/negative/neutral/mixed) of \
the following content, and briefly justify the classification with 1-2 supporting quotes.

Content:
{content}

Sentiment analysis:"""

CROSS_PLATFORM_COMPARISON_PROMPT = """Compare engagement and discussion patterns for the same topic \
across platforms, using the data below. Highlight differences in volume, sentiment, and audience \
reaction.

Platform data:
{platform_data}

Comparison:"""

ASSISTANT_SYSTEM_PROMPT = """You are a social media intelligence assistant. You answer questions \
using ONLY the retrieved records and SQL results provided to you in the user turn — never invent \
facts, numbers, or posts that are not present in that context. When you use a specific record, cite \
it inline as (platform, author/channel, short id). If the provided context does not contain enough \
information to answer, say so plainly instead of guessing. Keep answers concise and directly \
responsive to the question."""

CONVERSATION_MEMORY_PROMPT = """Previous conversation turns (most recent last):
{history}

Use this history only to resolve references (e.g. "that post", "the second one") in the current \
question — do not repeat information from it unless it is directly relevant to a new answer."""
