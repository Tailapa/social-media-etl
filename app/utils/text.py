"""Text extraction helpers shared by every normalizer: hashtags, mentions,
URLs, and a lightweight language guess. Kept dependency-free (stdlib regex)
so normalization never blocks on an external NLP service.
"""

from __future__ import annotations

import re

_HASHTAG_RE = re.compile(r"(?<!\w)#(\w+)", re.UNICODE)
_MENTION_RE = re.compile(r"(?<!\w)@(\w+)", re.UNICODE)
_URL_RE = re.compile(r"https?://[^\s<>\"']+")

# A tiny heuristic language detector: good enough to populate `language`
# without pulling in a heavyweight model dependency. Falls back to "und"
# (undetermined) per ISO 639-2 convention.
_ASCII_RE = re.compile(r"^[\x00-\x7F]*$")


def extract_hashtags(text: str | None) -> list[str]:
    if not text:
        return []
    seen: dict[str, None] = {}
    for match in _HASHTAG_RE.findall(text):
        seen.setdefault(match.lower(), None)
    return list(seen.keys())


def extract_mentions(text: str | None) -> list[str]:
    if not text:
        return []
    seen: dict[str, None] = {}
    for match in _MENTION_RE.findall(text):
        seen.setdefault(match.lower(), None)
    return list(seen.keys())


def extract_urls(text: str | None) -> list[str]:
    if not text:
        return []
    seen: dict[str, None] = {}
    for match in _URL_RE.findall(text):
        seen.setdefault(match.rstrip(").,"), None)
    return list(seen.keys())


def guess_language(text: str | None) -> str:
    """Cheap heuristic: ASCII-only text is assumed English, otherwise
    "und" (undetermined). Real language detection can be swapped in later
    without touching callers, since this is the single seam they go through.
    """
    if not text or not text.strip():
        return "und"
    return "en" if _ASCII_RE.match(text) else "und"
