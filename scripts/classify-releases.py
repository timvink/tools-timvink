#!/usr/bin/env python3
"""Classify new AI lab articles as model releases using the Claude API.

Reads new-articles.json + tracked-models.json and writes detected-releases.json,
a flat list of detected releases. merge-detected-releases.py does the dedupe and
merge into ai-releases.json.

Articles are sent in batches so one oversized backlog can't blow a single
response; the model is asked for JSON matching SCHEMA via structured outputs, so
the reply is guaranteed parseable.
"""

import json
import os
import re
import sys
from pathlib import Path

import anthropic

DATA_DIR = Path(__file__).resolve().parent.parent / "site" / "ai-race" / "data"
NEW_ARTICLES_FILE = DATA_DIR / "new-articles.json"
TRACKED_MODELS_FILE = DATA_DIR / "tracked-models.json"
DETECTED_FILE = DATA_DIR / "detected-releases.json"

MODEL = "claude-sonnet-5-5"
MAX_TOKENS = 16000

ARTICLES_PER_BATCH = 40
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


def classify(client: anthropic.Anthropic, batch: list[dict], tracked: dict) -> list[dict]:
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

    # fallbacks="default" re-runs a safety-classifier decline on Anthropic's
    # recommended substitute model inside the same call.
    response = client.beta.messages.create(
        model=MODEL,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        thinking={"type": "adaptive"},
        output_config={
            "effort": "low",
            "format": {"type": "json_schema", "schema": SCHEMA},
        },
        messages=[{"role": "user", "content": user_content}],
    )

    if response.stop_reason == "refusal":
        raise RuntimeError(f"Claude refused to classify this batch: {response.stop_details}")
    if response.stop_reason == "max_tokens":
        raise RuntimeError(
            f"Response hit max_tokens ({MAX_TOKENS}); reduce ARTICLES_PER_BATCH."
        )

    text = next((b.text for b in response.content if b.type == "text"), None)
    if text is None:
        raise RuntimeError(f"No text block in response (stop_reason={response.stop_reason})")

    return json.loads(text)["releases"]


def main() -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY is not set.", file=sys.stderr)
        return 1

    # Unattended weekly run: retry a little harder than the SDK default of 2.
    client = anthropic.Anthropic(max_retries=4)

    with NEW_ARTICLES_FILE.open() as f:
        articles = json.load(f)
    with TRACKED_MODELS_FILE.open() as f:
        tracked = json.load(f)

    detected: list[dict] = []
    try:
        for start in range(0, len(articles), ARTICLES_PER_BATCH):
            batch = articles[start : start + ARTICLES_PER_BATCH]
            found = classify(client, batch, tracked)
            print(f"Batch {start // ARTICLES_PER_BATCH + 1}: {len(batch)} articles -> {len(found)} release(s)")
            detected.extend(found)
    except anthropic.APIStatusError as e:
        print(f"ERROR: Claude API returned {e.status_code} ({e.type}): {e.message}", file=sys.stderr)
        return 1
    except anthropic.APIConnectionError as e:
        print(f"ERROR: could not reach the Claude API: {e}", file=sys.stderr)
        return 1

    with DETECTED_FILE.open("w") as f:
        json.dump(detected, f, indent=2, ensure_ascii=False)
        f.write("\n")

    for entry in detected:
        print(f"  detected: [{entry['company_id']}] {entry['name']} ({entry['date']})")
    print(f"Wrote {len(detected)} detected release(s) to {DETECTED_FILE.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
