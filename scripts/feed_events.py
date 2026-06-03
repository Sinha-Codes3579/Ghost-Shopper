"""Feed events.jsonl into the API in batches.
Handles large files by chunking into batches of 500.

Usage:
    python scripts/feed_events.py --events data/events.jsonl --api http://localhost:8000
"""

import argparse
import json
import sys
import time
import httpx

BATCH_SIZE = 500


def feed(events_path: str, api_url: str):
    batch, total_sent, total_accepted = [], 0, 0

    def flush(b):
        nonlocal total_sent, total_accepted
        try:
            r = httpx.post(
                f"{api_url}/events/ingest",
                json={"events": b},
                timeout=30,
            )
            if r.status_code == 200:
                data = r.json()
                total_accepted += data.get("accepted", 0)
                dups = data.get("duplicates", 0)
                rej  = data.get("rejected", 0)
                print(f"  Batch {total_sent//BATCH_SIZE+1}: "
                      f"accepted={data.get('accepted',0)} "
                      f"duplicates={dups} rejected={rej}")
            else:
                print(f"  ERROR {r.status_code}: {r.text[:200]}")
        except Exception as e:
            print(f"  Connection error: {e}")

    try:
        with open(events_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    batch.append(json.loads(line))
                except json.JSONDecodeError as e:
                    print(f"  Skipping malformed line: {e}")
                    continue

                if len(batch) >= BATCH_SIZE:
                    flush(batch)
                    total_sent += len(batch)
                    batch = []
                    time.sleep(0.05)  # gentle rate limiting

        if batch:
            flush(batch)
            total_sent += len(batch)

    except FileNotFoundError:
        print(f"ERROR: events file not found: {events_path}")
        sys.exit(1)

    print(f"\nDone. {total_sent} events sent, {total_accepted} accepted.")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--events", required=True, help="Path to events.jsonl")
    p.add_argument("--api",    default="http://localhost:8000")
    args = p.parse_args()
    feed(args.events, args.api)
