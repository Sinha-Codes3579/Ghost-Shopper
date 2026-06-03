"""Person detection + tracking for Store Intelligence.

Stack: YOLOv8n (detection) + ByteTrack (tracking) + Shapely (zone mapping)
Usage:
    python pipeline/detect.py \
        --clip clips/CAM_3_entry.mp4 \
        --camera CAM_ENTRY_01 \
        --store ST1008 \
        --layout store_layout.json \
        --output data/events.jsonl
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

from emit import EventEmitter
from tracker import VisitorTracker
from zones import ZoneMapper

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────
PERSON_CLASS_ID   = 0        # COCO class 0 = person
DETECTION_CONF    = 0.35     # min detection confidence for YOLO
PROCESS_EVERY_N   = 3        # process every Nth frame (speed vs accuracy)
DWELL_INTERVAL_S  = 30       # emit ZONE_DWELL every 30 seconds of continuous stay
FPS_DEFAULT       = 15.0     # fallback if cap.get(FPS) returns 0


def parse_args():
    p = argparse.ArgumentParser(description="CCTV detection pipeline")
    p.add_argument("--clip",    required=True,  help="Path to video clip")
    p.add_argument("--camera",  required=True,  help="Camera ID (CAM_ENTRY_01 etc.)")
    p.add_argument("--store",   required=True,  help="Store ID (ST1008)")
    p.add_argument("--layout",  required=True,  help="Path to store_layout.json")
    p.add_argument("--output",  required=True,  help="Output JSONL file path")
    p.add_argument("--append",  action="store_true", help="Append to output file")
    p.add_argument("--clip-start", default="2026-04-10T11:00:00Z",
                   help="ISO-8601 wall-clock time of clip frame 0")
    return p.parse_args()


def load_model():
    """Load YOLOv8n — downloads automatically on first run (~6MB)."""
    try:
        from ultralytics import YOLO
        model = YOLO("yolov8n.pt")
        logger.info("YOLOv8n loaded")
        return model
    except ImportError:
        logger.error("ultralytics not installed. Run: pip install ultralytics")
        sys.exit(1)


def frame_to_timestamp(frame_idx: int, fps: float, clip_start: datetime) -> str:
    """Convert frame index to ISO-8601 UTC timestamp."""
    offset_seconds = frame_idx / fps
    ts = clip_start.timestamp() + offset_seconds
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def process_clip(args):
    # ── Load dependencies ────────────────────────────────────────────────────
    model      = load_model()
    layout     = json.loads(Path(args.layout).read_text())
    zone_mapper = ZoneMapper(layout, args.camera)
    tracker    = VisitorTracker(layout)
    emitter    = EventEmitter(
        store_id=args.store,
        camera_id=args.camera,
        output_path=args.output,
        append=args.append,
    )

    clip_start = datetime.strptime(args.clip_start, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc
    )

    # ── Open video ───────────────────────────────────────────────────────────
    cap = cv2.VideoCapture(args.clip)
    if not cap.isOpened():
        logger.error(f"Cannot open clip: {args.clip}")
        sys.exit(1)

    fps        = cap.get(cv2.CAP_PROP_FPS) or FPS_DEFAULT
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration_s = total_frames / fps
    logger.info(
        f"Clip: {args.clip} | FPS={fps:.1f} | "
        f"Frames={total_frames} | Duration={duration_s:.0f}s"
    )

    frame_idx   = 0
    event_count = 0

    # ── Per-visitor dwell tracking ────────────────────────────────────────────
    # {visitor_id: {zone_id: last_dwell_emit_frame}}
    dwell_tracker: dict[str, dict[str, int]] = {}

    # ── Main loop ─────────────────────────────────────────────────────────────
    while True:
        ret, frame = cap.read()
        if not ret:
            break

        frame_idx += 1

        # Skip frames for speed
        if frame_idx % PROCESS_EVERY_N != 0:
            continue

        timestamp = frame_to_timestamp(frame_idx, fps, clip_start)

        # ── Run YOLOv8 + ByteTrack ────────────────────────────────────────────
        results = model.track(
            frame,
            persist=True,
            classes=[PERSON_CLASS_ID],
            conf=DETECTION_CONF,
            iou=0.5,
            tracker="bytetrack.yaml",
            verbose=False,
        )

        if results is None or len(results) == 0:
            continue

        result = results[0]
        boxes  = result.boxes

        if boxes is None or boxes.id is None:
            # No tracked persons this frame
            tracker.mark_all_absent(frame_idx, timestamp, emitter)
            continue

        # ── Process each tracked person ───────────────────────────────────────
        active_track_ids = set()

        for i, track_id in enumerate(boxes.id.int().tolist()):
            bbox       = boxes.xyxy[i].tolist()      # [x1,y1,x2,y2]
            confidence = float(boxes.conf[i])
            cx         = (bbox[0] + bbox[2]) / 2
            cy         = (bbox[1] + bbox[3]) / 2

            active_track_ids.add(track_id)

            # ── Visitor identity ───────────────────────────────────────────────
            visitor_id, is_new_entry, is_reentry, is_staff = tracker.update(
                track_id=track_id,
                bbox=bbox,
                frame=frame,
                frame_idx=frame_idx,
                timestamp=timestamp,
                camera_id=args.camera,
            )

            # ── Entry / re-entry events ───────────────────────────────────────
            if is_new_entry:
                ev = emitter.emit_entry(
                    visitor_id=visitor_id,
                    timestamp=timestamp,
                    confidence=confidence,
                    is_staff=is_staff,
                    is_reentry=is_reentry,
                )
                event_count += 1
                dwell_tracker[visitor_id] = {}

            if is_staff:
                continue  # track staff but exclude from zone events

            # ── Zone detection ────────────────────────────────────────────────
            current_zone = zone_mapper.get_zone(cx, cy, frame.shape)
            prev_zone    = tracker.get_prev_zone(visitor_id)

            if current_zone != prev_zone:
                # Zone exit
                if prev_zone is not None:
                    ev = emitter.emit_zone_exit(
                        visitor_id=visitor_id,
                        timestamp=timestamp,
                        zone_id=prev_zone,
                        confidence=confidence,
                        is_staff=is_staff,
                    )
                    event_count += 1

                # Zone enter
                if current_zone is not None:
                    ev = emitter.emit_zone_enter(
                        visitor_id=visitor_id,
                        timestamp=timestamp,
                        zone_id=current_zone,
                        confidence=confidence,
                        is_staff=is_staff,
                        queue_depth=tracker.get_queue_depth(current_zone),
                    )
                    event_count += 1

                    # Billing queue join
                    if current_zone == "CASH_COUNTER":
                        qd = tracker.get_queue_depth("CASH_COUNTER")
                        if qd > 0:
                            emitter.emit_billing_queue_join(
                                visitor_id=visitor_id,
                                timestamp=timestamp,
                                queue_depth=qd,
                                confidence=confidence,
                            )
                            event_count += 1

                tracker.set_zone(visitor_id, current_zone)

            # ── Dwell events (every 30s of continuous zone presence) ───────────
            if current_zone is not None:
                frames_per_dwell = int(DWELL_INTERVAL_S * fps / PROCESS_EVERY_N)
                vid_dwell = dwell_tracker.setdefault(visitor_id, {})
                last_emit  = vid_dwell.get(current_zone, frame_idx)
                if (frame_idx - last_emit) >= frames_per_dwell:
                    dwell_ms = int(DWELL_INTERVAL_S * 1000)
                    emitter.emit_zone_dwell(
                        visitor_id=visitor_id,
                        timestamp=timestamp,
                        zone_id=current_zone,
                        dwell_ms=dwell_ms,
                        confidence=confidence,
                        is_staff=is_staff,
                    )
                    vid_dwell[current_zone] = frame_idx
                    event_count += 1

        # ── Handle exits for disappeared tracks ───────────────────────────────
        exit_events = tracker.process_exits(
            active_track_ids=active_track_ids,
            frame_idx=frame_idx,
            timestamp=timestamp,
            emitter=emitter,
        )
        event_count += len(exit_events)

        # ── Billing queue abandon detection ───────────────────────────────────
        abandon_events = tracker.check_billing_abandon(
            timestamp=timestamp,
            emitter=emitter,
        )
        event_count += len(abandon_events)

        # Progress log every 500 frames
        if frame_idx % 500 == 0:
            pct = frame_idx / total_frames * 100 if total_frames > 0 else 0
            logger.info(f"  Frame {frame_idx}/{total_frames} ({pct:.0f}%) — {event_count} events so far")

    cap.release()

    # ── Flush any remaining open sessions as EXIT ─────────────────────────────
    final_ts = frame_to_timestamp(frame_idx, fps, clip_start)
    tracker.flush_open_sessions(final_ts, emitter)

    emitter.close()
    logger.info(f"Done. {event_count} events written to {args.output}")
    return event_count


if __name__ == "__main__":
    args = parse_args()
    process_clip(args)
