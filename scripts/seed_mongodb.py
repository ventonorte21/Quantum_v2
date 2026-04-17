"""
Seed MongoDB with default configuration files from seed_data/.
Only inserts documents into empty collections — never overwrites existing data.

Run order in start_production.sh: AFTER MongoDB is ready, BEFORE backend starts.
"""
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from motor.motor_asyncio import AsyncIOMotorClient

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME   = os.environ.get("DB_NAME", "quantum_trading")

SEED_DIR = Path(__file__).parent.parent / "seed_data"

SEED_FILES = {
    "scalp_config":           SEED_DIR / "scalp_config.json",
    "scalp_combined_schedule": SEED_DIR / "scalp_combined_schedule.json",
    "scalp_tune_schedule":    SEED_DIR / "scalp_tune_schedule.json",
}


async def seed():
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]

    seeded_any = False
    for collection_name, seed_file in SEED_FILES.items():
        if not seed_file.exists():
            print(f"[seed] SKIP {collection_name} — seed file not found: {seed_file}")
            continue

        count = await db[collection_name].count_documents({})
        if count > 0:
            print(f"[seed] SKIP {collection_name} — already has {count} document(s)")
            continue

        with open(seed_file) as f:
            docs = json.load(f)

        if not docs:
            print(f"[seed] SKIP {collection_name} — seed file is empty")
            continue

        now = datetime.now(timezone.utc)
        for doc in docs:
            doc.setdefault("created_at", now.isoformat())
            doc.setdefault("updated_at", now.isoformat())

        await db[collection_name].insert_many(docs)
        print(f"[seed] OK   {collection_name} — inserted {len(docs)} document(s)")
        seeded_any = True

    if not seeded_any:
        print("[seed] Nothing to seed — all collections already populated")

    client.close()


if __name__ == "__main__":
    asyncio.run(seed())
