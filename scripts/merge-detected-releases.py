#!/usr/bin/env python3
"""Merge LLM-detected model releases into ai-releases.json.

The LLM only classifies articles and writes detected-releases.json — a flat list
of {company_id, name, date, tier, description, url}. All the deterministic work
(validation, dedupe, chronological insert, last_updated) happens here so the
model never has to read or rewrite the full data file.

ai-releases.json is only rewritten when something is actually added, so the
workflow does not commit a no-op diff every day.
"""

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "site" / "ai-race" / "data"
RELEASES_FILE = DATA_DIR / "ai-releases.json"
DETECTED_FILE = DATA_DIR / "detected-releases.json"

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
VALID_TIERS = {"flagship", "major", "minor"}


def normalise_name(name: str) -> str:
    """Case/punctuation-insensitive key for dedupe: "Claude-Opus 5" -> "claude opus 5"."""
    return re.sub(r"[\s\-_]+", " ", name).strip().lower()


def main() -> int:
    if not DETECTED_FILE.exists():
        print(f"No {DETECTED_FILE.name} — nothing to merge.")
        return 0

    with DETECTED_FILE.open() as f:
        detected = json.load(f)
    if not isinstance(detected, list):
        print(f"ERROR: {DETECTED_FILE.name} must be a JSON array.", file=sys.stderr)
        return 1

    with RELEASES_FILE.open() as f:
        data = json.load(f)

    companies = {company["id"]: company for company in data["companies"]}
    known = {
        cid: {normalise_name(r["name"]) for r in company["releases"]}
        for cid, company in companies.items()
    }

    added: set[str] = set()
    for entry in detected:
        company_id = entry.get("company_id")
        name = (entry.get("name") or "").strip()
        date = (entry.get("date") or "").strip()

        if company_id not in companies:
            print(f"  skip: unknown company_id {company_id!r} for {name!r}")
            continue
        if not name or not DATE_RE.match(date):
            print(f"  skip: missing name or bad date in {entry!r}")
            continue
        if normalise_name(name) in known[company_id]:
            print(f"  skip: [{company_id}] {name} already tracked")
            continue

        tier = entry.get("tier")
        release = {
            "name": name,
            "date": date,
            "tier": tier if tier in VALID_TIERS else "major",
            "description": (entry.get("description") or "").strip(),
            "url": (entry.get("url") or "").strip(),
        }
        companies[company_id]["releases"].append(release)
        known[company_id].add(normalise_name(name))
        added.add(company_id)
        print(f"  + [{company_id}] {name} ({date})")

    if not added:
        print("No new releases to add.")
        return 0

    for company_id in added:
        companies[company_id]["releases"].sort(key=lambda r: r["date"])

    data["last_updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    with RELEASES_FILE.open("w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"Merged into {RELEASES_FILE.name}; last_updated -> {data['last_updated']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
