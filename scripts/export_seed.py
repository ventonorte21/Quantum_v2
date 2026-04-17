"""
Export current MongoDB config collections to seed_data/*.json files.

Run this locally (in dev) BEFORE redeploying whenever you've made config
changes in production that you want to preserve across future redeploys.

Usage:
    python3 scripts/export_seed.py

The script connects to MONGO_URL (default: localhost:27017) and writes
current documents from the config collections to seed_data/*.json.
Timestamps and internal fields are stripped so the seed files stay clean.
"""
import asyncio
import json
import os
import sys
from pathlib import Path

from motor.motor_asyncio import AsyncIOMotorClient

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME   = os.environ.get("DB_NAME", "quantum_trading")

SEED_DIR = Path(__file__).parent.parent / "seed_data"

EXPORT_MAP = {
    "scalp_config":           SEED_DIR / "scalp_config.json",
    "scalp_combined_schedule": SEED_DIR / "scalp_combined_schedule.json",
    "scalp_tune_schedule":    SEED_DIR / "scalp_tune_schedule.json",
}

STRIP_FIELDS = {"_id", "created_at", "updated_at", "last_run_at", "last_updated"}


def clean(doc: dict) -> dict:
    return {k: v for k, v in doc.items() if k not in STRIP_FIELDS}


async def export_all():
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]

    SEED_DIR.mkdir(exist_ok=True)

    for collection_name, seed_file in EXPORT_MAP.items():
        docs = await db[collection_name].find({}, {"_id": 0}).to_list(None)
        if not docs:
            print(f"[export] SKIP {collection_name} — empty collection")
            continue

        cleaned = [clean(d) for d in docs]
        with open(seed_file, "w") as f:
            json.dump(cleaned, f, indent=2, default=str)
        print(f"[export] OK   {collection_name} → {seed_file}  ({len(cleaned)} doc(s))")

    client.close()
    print("\nDone. Commit seed_data/ before redeploying to lock in these settings.")


if __name__ == "__main__":
    asyncio.run(export_all())
