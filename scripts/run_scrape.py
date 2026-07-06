#!/usr/bin/env python
"""CLI entrypoint for the end-to-end scrape -> ingest workflow described in
the spec's "End-to-End Validation" section: pick a platform + mode +
target, run the matching scraper, and ingest the results (normalize,
dedupe, persist, embed).

Usage:
    python scripts/run_scrape.py instagram posts nasa --limit 50
    python scripts/run_scrape.py twitter hashtag climate --limit 100
    python scripts/run_scrape.py youtube comments dQw4w9WgXcQ --limit 200
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.logging import get_logger
from app.services.scrape_service import ScrapeService

logger = get_logger(__name__)

_MODES = {
    "profile": "scrape_profile",
    "posts": "scrape_posts",
    "comments": "scrape_comments",
    "hashtag": "scrape_hashtag",
    "keyword": "scrape_keyword",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("platform", choices=["instagram", "twitter", "youtube"])
    parser.add_argument("mode", choices=sorted(_MODES.keys()))
    parser.add_argument(
        "target", help="username, post URL/ID, hashtag, or keyword depending on mode"
    )
    parser.add_argument(
        "--limit", type=int, default=50, help="max items to fetch (ignored for 'profile')"
    )
    return parser.parse_args()


async def run(platform: str, mode: str, target: str, limit: int) -> None:
    service = ScrapeService()
    method_name = _MODES[mode]
    method = getattr(service, method_name)
    kwargs = {} if mode == "profile" else {"limit": limit}
    report = await method(platform, target, **kwargs)

    print(f"Job {report.job_id}: {report.total_records} records processed")
    print(
        f"  authors={report.authors_upserted} channels={report.channels_upserted} "
        f"posts={report.posts_upserted} videos={report.videos_upserted} "
        f"comments={report.comments_upserted}"
    )
    print(
        f"  media={report.media_created} hashtags_linked={report.hashtags_linked} "
        f"mentions={report.mentions_created} engagement={report.engagement_upserted}"
    )
    print(f"  embeddings_generated={report.embeddings_generated}")
    if report.errors:
        print(f"  {len(report.errors)} error(s):")
        for error in report.errors:
            print(f"    - {error}")


def main() -> None:
    args = parse_args()
    asyncio.run(run(args.platform, args.mode, args.target, args.limit))


if __name__ == "__main__":
    main()
