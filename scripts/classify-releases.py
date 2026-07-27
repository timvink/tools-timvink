#!/usr/bin/env python3
"""Classify new AI lab articles as model releases using GitHub Models.

Reads new-articles.json + tracked-models.json and writes detected-releases.json
in the same shape the LLM step used to produce. Inference runs on GitHub Models,
which is free for the repo's GITHUB_TOKEN (needs `models: read` permission).

Articles are sent in batches so a large backlog can't blow the free tier's
per-request input limit. merge-detected-releases.py does the dedupe and merge.
"""

import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "site" / "ai-race" / "data"
NEW_ARTICLES_FILE = DATA_DIR / "new-articles.json"
TRACKED_MODELS_FILE = DATA_DIR / "tracked-models.json"
DETECTED_FILE = DATA_DIR / "detected-releases.json"

ENDPOINT = "https://models.github.ai/inference/chat/completions"
MODEL = "openai/gpt-4.1-mini"

# Free tier allows 8000 input tokens per request. Batch conservatively.
ARTICLES_PER_BATCH = 15
DESCRIPTION_LIMIT = 300

SYSTEM_PROMPT = """\
You identify announcements of new AI models in tech-blog articles.

A model release is a new model version callable via API or usable in a product \
(e.g. "GPT-5.4", "Gemini 3.2 Pro", "Claude Opus 5").
Do NOT count: feature updates, API/platform changes, research papers, safety \
reports, product launches, partnership announcements, or tool/app announcements.

Rules:
- Judge primarily from the title.
- Skip any model already listed as tracked — including minor naming variants.
- If one article announces several models (e.g. "mini and nano"), emit one entry per model.
- "company_id" must be one of the provided ids; skip the article if none fits.
- "date" and "url" must be copied verbatim from the article.
- "tier": "flagship" for a new generation or major capability leap, "major" for a \
significant update or new size, "minor" for an incremental iteration.
- "description": under 200 characters.
- Emit an empty list when nothing qualifies. Never invent a model."""

SCHEMA = {
    "type": "object",
    "properties": {
        "releases": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "company_id": {"type": "string"},
                    "name": {"type": "string"},
                    "date": {"type": "string"},
                    "tier": {"type": "string", "enum": ["flagship", "major", "minor"]},
                    "description": {"type": "string"},
                    "url": {"type": "string"},
                },
                "required": ["company_id", "name", "date", "tier", "description", "url"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["releases"],
    "additionalProperties": False,
}


def clean(text: str) -> str:
    """Strip HTML tags (some feeds embed <img>) and clamp length."""
    return re.sub(r"<[^>]+>", "", text or "").strip()[:DESCRIPTION_LIMIT]


def classify(batch: list[dict], tracked: dict, token: str) -> list[dict]:
    user_content = json.dumps(
        {
            "companies": {
                cid: {"name": c["name"], "tracked_models": c["tracked_models"]}
                for cid, c in tracked.items()
            },
            "articles": [
                {
                    "title": a["title"],
                    "url": a["url"],
                    "date": a["date"],
                    "description": clean(a.get("description", "")),
                    "source": a.get("source", ""),
                }
                for a in batch
            ],
        },
        ensure_ascii=False,
    )

    payload = {
        "model": MODEL,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "releases", "strict": True, "schema": SCHEMA},
        },
    }

    request = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            body = json.load(response)
    except urllib.error.HTTPError as e:
        print(f"ERROR: GitHub Models returned {e.code}: {e.read().decode()[:500]}", file=sys.stderr)
        raise

    return json.loads(body["choices"][0]["message"]["content"])["releases"]


def main() -> int:
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("ERROR: GITHUB_TOKEN is not set.", file=sys.stderr)
        return 1

    with NEW_ARTICLES_FILE.open() as f:
        articles = json.load(f)
    with TRACKED_MODELS_FILE.open() as f:
        tracked = json.load(f)

    detected: list[dict] = []
    for start in range(0, len(articles), ARTICLES_PER_BATCH):
        batch = articles[start : start + ARTICLES_PER_BATCH]
        found = classify(batch, tracked, token)
        print(f"Batch {start // ARTICLES_PER_BATCH + 1}: {len(batch)} articles -> {len(found)} release(s)")
        detected.extend(found)

    with DETECTED_FILE.open("w") as f:
        json.dump(detected, f, indent=2, ensure_ascii=False)
        f.write("\n")

    for entry in detected:
        print(f"  detected: [{entry['company_id']}] {entry['name']} ({entry['date']})")
    print(f"Wrote {len(detected)} detected release(s) to {DETECTED_FILE.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
