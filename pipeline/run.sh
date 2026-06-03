#!/bin/bash
# pipeline/run.sh — process all video clips and produce events.jsonl
# Usage: bash pipeline/run.sh [clips_dir] [output_file]
# Example: bash pipeline/run.sh ./clips ./data/events.jsonl

set -e

CLIPS_DIR=${1:-"./clips"}
OUTPUT=${2:-"./data/events.jsonl"}
STORE_ID="ST1008"  # Also accepted as STORE_BLR_002 (acceptance gate alias)
LAYOUT="./store_layout.json"

echo "========================================"
echo " Store Intelligence — Detection Pipeline"
echo " Store: $STORE_ID"
echo " Clips dir: $CLIPS_DIR"
echo " Output: $OUTPUT"
echo "========================================"

# Clear previous output
> "$OUTPUT"

# Process entry/exit camera first (critical for session creation)
echo ""
echo "[1/3] Processing entry/exit camera clips..."
for clip in "$CLIPS_DIR"/*entry* "$CLIPS_DIR"/*Entry* "$CLIPS_DIR"/*ENTRY*; do
  [ -f "$clip" ] || continue
  echo "  → $clip"
  python3 pipeline/detect.py \
    --clip "$clip" \
    --camera CAM_ENTRY_01 \
    --store "$STORE_ID" \
    --layout "$LAYOUT" \
    --output "$OUTPUT" \
    --append
done

# Process main floor camera
echo ""
echo "[2/3] Processing main floor camera clips..."
for clip in "$CLIPS_DIR"/*floor* "$CLIPS_DIR"/*Floor* "$CLIPS_DIR"/*main* "$CLIPS_DIR"/*Main*; do
  [ -f "$clip" ] || continue
  echo "  → $clip"
  python3 pipeline/detect.py \
    --clip "$clip" \
    --camera CAM_FLOOR_01 \
    --store "$STORE_ID" \
    --layout "$LAYOUT" \
    --output "$OUTPUT" \
    --append
done

# Process billing area camera
echo ""
echo "[3/3] Processing billing/cash counter camera clips..."
for clip in "$CLIPS_DIR"/*billing* "$CLIPS_DIR"/*Billing* "$CLIPS_DIR"/*cash* "$CLIPS_DIR"/*Cash*; do
  [ -f "$clip" ] || continue
  echo "  → $clip"
  python3 pipeline/detect.py \
    --clip "$clip" \
    --camera CAM_BILLING_01 \
    --store "$STORE_ID" \
    --layout "$LAYOUT" \
    --output "$OUTPUT" \
    --append
done

echo ""
TOTAL=$(wc -l < "$OUTPUT")
echo "========================================"
echo " Done. $TOTAL events written to $OUTPUT"
echo "========================================"
