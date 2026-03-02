#!/usr/bin/env python3
"""
Update the AI releases data file.

- Always updates the `last_updated` timestamp.
- Queries the OpenRouter API for new model releases from tracked providers.
- Adds newly discovered models that aren't already in the curated list.
- Preserves all existing curated entries (never removes anything).
"""

import json
import re
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

DATA_FILE = Path(__file__).parent.parent / "site" / "ai-race" / "data" / "ai-releases.json"

# OpenRouter provider prefix → our company id
PROVIDER_MAP = {
    "anthropic": "anthropic",
    "openai": "openai",
    "google": "google",
    "x-ai": "xai",
}

# Model ID substrings that mark a model as a variant / side-project / non-chat model.
# Any model whose ID contains one of these fragments is skipped.
SKIP_FRAGMENTS = [
    # Modality / utility
    "embed", "embedding", "moderation", "whisper", "dall-e", "tts", "speech",
    "image-preview", "-image",
    # Legacy / older series we don't track
    "davinci", "babbage", "curie", "ada",
    "gpt-3.5",
    # Open-source side-projects (not the main race)
    "gemma",       # Google's open weights family
    "gpt-oss",     # OpenAI open-source models
    "grok-code", "grok code",  # xAI coding variant
    # Fine-tune / variant markers
    "fine-tune", "ft:", "ft-",
    # Deployment variants (same model, different config)
    ":free", ":extended", ":thinking", ":online", ":exacto",
    # Dated snapshot suffixes like -20241120, -0314, -0613, -0125
    # (we keep the base model but skip old snapshots)
    "-0314", "-0613", "-0125",
    # Preview suffixes for small updates
    "-vision-preview",
    # Audio model (not a chat/reasoning model)
    "gpt-audio", "-audio",
    # Search preview variants (minor feature add-on, not a new model)
    "-search-preview",
    # Deep research variants
    "-deep-research",
    # Safeguard models
    "safeguard",
    # "instruct" variants of base models (usually same model)
    "-instruct",
]

# Regex for dated version suffixes we want to skip, e.g. gpt-4o-2024-11-20
DATED_SNAPSHOT_RE = re.compile(r"-\d{4}-\d{2}-\d{2}$")


def fetch_openrouter_models() -> list[dict]:
    """Fetch all models from the OpenRouter public API (no auth required)."""
    url = "https://openrouter.ai/api/v1/models"
    req = urllib.request.Request(url, headers={"User-Agent": "ai-race-updater/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read()
    return json.loads(body).get("data", [])


def is_skip(model_id: str) -> bool:
    """Return True if the model should be ignored."""
    lower = model_id.lower()
    if any(f in lower for f in SKIP_FRAGMENTS):
        return True
    # Skip dated snapshots like openai/gpt-4o-2024-11-20
    slug = model_id.split("/", 1)[-1] if "/" in model_id else model_id
    if DATED_SNAPSHOT_RE.search(slug):
        return True
    return False


def normalise_name(name: str) -> str:
    """Strip company prefix, normalise hyphens/underscores, and lowercase.

    Converts "Anthropic: Claude 3 Haiku"  → "claude 3 haiku"
    Converts "xAI: Grok 3 Mini"           → "grok 3 mini"
    Converts "Grok-3 Mini"                → "grok 3 mini"
    """
    # Remove "Company: " prefix if present
    if ": " in name:
        name = name.split(": ", 1)[1]
    # Normalise hyphens / underscores to spaces and collapse whitespace
    name = re.sub(r"[-_]", " ", name)
    name = re.sub(r"\s+", " ", name)
    return name.strip().lower()


def collect_known_ids(data: dict) -> set[str]:
    """Collect all openrouter_id values already present in the data."""
    ids: set[str] = set()
    for company in data["companies"]:
        for release in company["releases"]:
            oid = release.get("openrouter_id")
            if oid:
                ids.add(oid)
    return ids


def collect_known_normalised_names(data: dict) -> dict[str, set[str]]:
    """Normalised (lowercase, prefix-stripped) release names per company."""
    result: dict[str, set[str]] = {}
    for company in data["companies"]:
        result[company["id"]] = {normalise_name(r["name"]) for r in company["releases"]}
    return result


def timestamp_to_date(ts: int | float | None) -> str:
    if not ts:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%d")


def main() -> int:
    if not DATA_FILE.exists():
        print(f"ERROR: data file not found: {DATA_FILE}", file=sys.stderr)
        return 1

    with DATA_FILE.open() as f:
        data = json.load(f)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    data["last_updated"] = today
    print(f"Updated last_updated → {today}")

    # ── Fetch from OpenRouter ─────────────────────────────────────────────
    try:
        models = fetch_openrouter_models()
        print(f"Fetched {len(models)} models from OpenRouter")
    except (urllib.error.URLError, OSError) as exc:
        print(f"WARNING: Could not fetch OpenRouter models: {exc}", file=sys.stderr)
        with DATA_FILE.open("w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        print("Saved (timestamp only).")
        return 0

    known_ids   = collect_known_ids(data)
    known_names = collect_known_normalised_names(data)
    company_idx = {co["id"]: co for co in data["companies"]}

    added = 0
    for model in models:
        model_id = model.get("id", "")
        if "/" not in model_id:
            continue
        provider, _slug = model_id.split("/", 1)

        company_id = PROVIDER_MAP.get(provider)
        if not company_id:
            continue
        if model_id in known_ids:
            continue
        if is_skip(model_id):
            continue

        # Human-readable name
        name = (model.get("name") or _slug.replace("-", " ").title()).strip()
        # Strip leading "Provider: " the API sometimes adds
        norm = normalise_name(name)

        # Skip if a release with same normalised name already exists
        existing = known_names.get(company_id, set())
        if norm in existing:
            continue
        # Skip if the new name is a direct variant of an existing entry:
        # "gpt 5 chat" starts with "gpt 5 " → variant of GPT-5.
        # "gpt 5.3 codex" does NOT start with "gpt 5 " → genuinely new model. ✓
        if any(norm.startswith(ex + " ") or ex.startswith(norm + " ") for ex in existing):
            continue

        date_str = timestamp_to_date(model.get("created"))
        # Only add models from 2023 onward
        if int(date_str[:4]) < 2023:
            continue

        description = (model.get("description") or "").strip()
        # Strip markdown links from description
        description = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", description)
        if len(description) > 220:
            description = description[:217] + "…"

        new_release = {
            "name":          name,
            "date":          date_str,
            "tier":          "minor",
            "description":   description,
            "openrouter_id": model_id,
        }

        company = company_idx.get(company_id)
        if company is None:
            continue

        company["releases"].append(new_release)
        known_names.setdefault(company_id, set()).add(norm)
        known_ids.add(model_id)
        added += 1
        print(f"  + [{company_id}] {name}  ({date_str})")

    # Sort releases chronologically within each company
    for company in data["companies"]:
        company["releases"].sort(key=lambda r: r["date"])

    with DATA_FILE.open("w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"Done — {added} new release(s) added.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
