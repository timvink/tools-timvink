#!/usr/bin/env python3
"""Fetch RSS feeds and identify new articles not yet processed by the blog checker.

Maintains a list of previously seen URLs in checked-blog-urls.json so that
only genuinely new articles are sent to the (expensive) LLM for processing.
"""

import json
import os
import sys
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "site" / "ai-race" / "data"
CHECKED_URLS_FILE = DATA_DIR / "checked-blog-urls.json"

FEEDS = [
    {"source": "OpenAI", "url": "https://openai.com/blog/rss.xml"},
    {"source": "Google DeepMind", "url": "https://blog.google/technology/ai/rss/"},
]


def fetch_rss_entries(feed_url: str) -> list[dict]:
    """Fetch and parse an RSS feed, returning a list of entry dicts."""
    req = urllib.request.Request(
        feed_url, headers={"User-Agent": "ai-release-checker/1.0"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        xml_data = resp.read()
    root = ET.fromstring(xml_data)
    entries = []
    for item in root.iter("item"):
        title = item.findtext("title", "").strip()
        link = item.findtext("link", "").strip()
        if link:
            entries.append({"title": title, "url": link})
    return entries


def main() -> None:
    # Load previously checked URLs
    if CHECKED_URLS_FILE.exists():
        with open(CHECKED_URLS_FILE) as f:
            checked_urls = set(json.load(f))
    else:
        checked_urls = set()

    all_current_urls = set()
    new_articles = []

    for feed in FEEDS:
        try:
            entries = fetch_rss_entries(feed["url"])
            for entry in entries:
                all_current_urls.add(entry["url"])
                if entry["url"] not in checked_urls:
                    entry["source"] = feed["source"]
                    new_articles.append(entry)
        except Exception as e:
            print(
                f"Warning: could not fetch {feed['source']} RSS: {e}", file=sys.stderr
            )

    # Save union of previous + current URLs for next run
    updated_checked = sorted(checked_urls | all_current_urls)
    with open(CHECKED_URLS_FILE, "w") as f:
        json.dump(updated_checked, f, indent=2)
        f.write("\n")

    # Write new articles for Claude to process
    with open("site/ai-race/data/new-articles.json", "w") as f:
        json.dump(new_articles, f, indent=2)

    print(f"Found {len(new_articles)} new article(s) to check")

    # Set GitHub Actions outputs
    github_output = os.environ.get("GITHUB_OUTPUT", "")
    if github_output:
        with open(github_output, "a") as f:
            f.write(f"has_new_articles={'true' if new_articles else 'false'}\n")
            f.write(f"article_count={len(new_articles)}\n")


if __name__ == "__main__":
    main()
