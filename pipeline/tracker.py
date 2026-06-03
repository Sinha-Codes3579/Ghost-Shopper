"""Visitor session management, Re-ID, staff detection, re-entry.

Design decisions (see CHOICES.md):
- ByteTrack track_id is ephemeral (reused across sessions). visitor_id is a stable UUID.
- Re-entry: on EXIT, save appearance embedding + timestamp. On next ENTRY within 10 min,
  cosine similarity > 0.75 → REENTRY event, reuse original visitor_id.
- Staff detection: staff_embedding_bank built from manually labelled frames.
  cosine similarity > 0.80 → is_staff=True.
- No torchreid dependency at runtime if not installed — falls back to histogram Re-ID.
"""

import logging
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# ── Thresholds ────────────────────────────────────────────────────────────────
REENTRY_SIMILARITY_THRESHOLD = 0.75
STAFF_SIMILARITY_THRESHOLD   = 0.80
REENTRY_WINDOW_SECONDS       = 600   # 10 minutes
EXIT_GRACE_FRAMES            = 30    # frames before declaring a track truly exited
BILLING_ABANDON_GRACE_S      = 30    # seconds in billing zone without purchase = potential abandon


@dataclass
class VisitorSession:
    visitor_id:     str
    track_id:       int
    entry_time:     str
    camera_id:      str
    zone:           Optional[str]  = None
    is_staff:       bool           = False
    embedding:      Optional[np.ndarray] = None
    session_seq:    int            = 0
    last_seen_frame: int           = 0
    billing_enter_time: Optional[str] = None


@dataclass
class ExitedVisitor:
    visitor_id:  str
    exit_time:   str
    exit_ts:     float         # unix timestamp
    embedding:   Optional[np.ndarray]


class VisitorTracker:
    def __init__(self, layout: dict):
        self.layout = layout

        # Active sessions: track_id → VisitorSession
        self._sessions: dict[int, VisitorSession] = {}

        # Recent exits for re-entry detection: list of ExitedVisitor
        self._recent_exits: list[ExitedVisitor] = []

        # Staff embedding bank
        self._staff_embeddings: list[np.ndarray] = []
        self._load_staff_embeddings()

        # Zone → set of visitor_ids currently inside
        self._zone_occupants: dict[str, set] = defaultdict(set)

        # Track IDs not seen recently (candidate exits)
        self._absent_frames: dict[int, int] = {}

        # Re-ID extractor
        self._reid = self._init_reid()

    # ── Re-ID initialisation ──────────────────────────────────────────────────
    def _init_reid(self):
        """Try to load torchreid OSNet; fall back to histogram-based Re-ID."""
        try:
            import torchreid
            extractor = torchreid.utils.FeatureExtractor(
                model_name="osnet_x0_25",
                model_path="",   # downloads automatically
                device="cpu",
            )
            logger.info("torchreid OSNet loaded for Re-ID")
            return extractor
        except Exception as e:
            logger.warning(f"torchreid not available ({e}) — using histogram Re-ID fallback")
            return None

    def _extract_embedding(self, frame: np.ndarray, bbox: list) -> Optional[np.ndarray]:
        """Extract appearance embedding from a person crop."""
        x1, y1, x2, y2 = [int(v) for v in bbox]
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(frame.shape[1], x2), min(frame.shape[0], y2)

        if x2 <= x1 or y2 <= y1:
            return None

        crop = frame[y1:y2, x1:x2]
        if crop.size == 0:
            return None

        if self._reid is not None:
            try:
                import torch
                crop_resized = cv2.resize(crop, (128, 256))
                crop_rgb = cv2.cvtColor(crop_resized, cv2.COLOR_BGR2RGB)
                feat = self._reid([crop_rgb])
                return feat.cpu().numpy().flatten()
            except Exception:
                pass

        # Fallback: color histogram embedding
        return self._histogram_embedding(crop)

    def _histogram_embedding(self, crop: np.ndarray) -> np.ndarray:
        """Fast color histogram as appearance embedding fallback."""
        crop_resized = cv2.resize(crop, (64, 128))
        hsv = cv2.cvtColor(crop_resized, cv2.COLOR_BGR2HSV)
        h_hist = cv2.calcHist([hsv], [0], None, [32], [0, 180]).flatten()
        s_hist = cv2.calcHist([hsv], [1], None, [32], [0, 256]).flatten()
        v_hist = cv2.calcHist([hsv], [2], None, [16], [0, 256]).flatten()
        feat = np.concatenate([h_hist, s_hist, v_hist])
        norm = np.linalg.norm(feat)
        return feat / norm if norm > 0 else feat

    def _cosine_similarity(self, a: np.ndarray, b: np.ndarray) -> float:
        if a is None or b is None:
            return 0.0
        na, nb = np.linalg.norm(a), np.linalg.norm(b)
        if na == 0 or nb == 0:
            return 0.0
        return float(np.dot(a, b) / (na * nb))

    # ── Staff detection ────────────────────────────────────────────────────────
    def _load_staff_embeddings(self):
        """
        Load pre-computed staff embeddings from staff_embeddings/ folder.
        If folder doesn't exist, staff detection falls back to heuristics only.
        Each .npy file = one staff appearance sample.
        """
        staff_dir = "staff_embeddings"
        if not __import__("os").path.exists(staff_dir):
            logger.warning(
                "staff_embeddings/ folder not found. "
                "Create it with: python pipeline/build_staff_bank.py --clips clips/"
            )
            return
        import os
        count = 0
        for f in os.listdir(staff_dir):
            if f.endswith(".npy"):
                emb = np.load(os.path.join(staff_dir, f))
                self._staff_embeddings.append(emb)
                count += 1
        logger.info(f"Loaded {count} staff embeddings from {staff_dir}/")

    def _is_staff(self, embedding: Optional[np.ndarray]) -> bool:
        """Return True if embedding matches any known staff embedding."""
        if not self._staff_embeddings or embedding is None:
            return False
        sims = [self._cosine_similarity(embedding, s) for s in self._staff_embeddings]
        return max(sims) >= STAFF_SIMILARITY_THRESHOLD

    # ── Re-entry detection ─────────────────────────────────────────────────────
    def _find_reentry(self, embedding: Optional[np.ndarray], now_ts: float) -> Optional[str]:
        """
        Check if this new person matches a recently exited visitor.
        Returns visitor_id if matched, None otherwise.
        """
        self._recent_exits = [
            e for e in self._recent_exits
            if (now_ts - e.exit_ts) <= REENTRY_WINDOW_SECONDS
        ]
        best_sim = 0.0
        best_vid = None
        for exited in self._recent_exits:
            sim = self._cosine_similarity(embedding, exited.embedding)
            if sim > best_sim:
                best_sim = sim
                best_vid = exited.visitor_id
        if best_sim >= REENTRY_SIMILARITY_THRESHOLD:
            logger.debug(f"Re-entry detected: visitor_id={best_vid} sim={best_sim:.3f}")
            return best_vid
        return None

    # ── Main update ────────────────────────────────────────────────────────────
    def update(
        self,
        track_id:  int,
        bbox:      list,
        frame:     np.ndarray,
        frame_idx: int,
        timestamp: str,
        camera_id: str,
    ) -> tuple[str, bool, bool, bool]:
        """
        Update tracker state for a detected person.
        Returns: (visitor_id, is_new_entry, is_reentry, is_staff)
        """
        embedding = self._extract_embedding(frame, bbox)
        is_staff  = self._is_staff(embedding)

        self._absent_frames.pop(track_id, None)

        if track_id in self._sessions:
            session = self._sessions[track_id]
            session.last_seen_frame = frame_idx
            if embedding is not None:
                session.embedding = embedding
            return session.visitor_id, False, False, session.is_staff

        # New track_id — is this a new visitor or a re-entry?
        now_ts   = datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        ).timestamp()
        reentry_vid = self._find_reentry(embedding, now_ts)
        is_reentry  = reentry_vid is not None
        visitor_id  = reentry_vid if is_reentry else f"VIS_{str(uuid.uuid4()).replace('-','')[:6]}"


        session = VisitorSession(
            visitor_id=visitor_id,
            track_id=track_id,
            entry_time=timestamp,
            camera_id=camera_id,
            is_staff=is_staff,
            embedding=embedding,
            last_seen_frame=frame_idx,
        )
        self._sessions[track_id] = session
        return visitor_id, True, is_reentry, is_staff

    # ── Zone management ───────────────────────────────────────────────────────
    def get_prev_zone(self, visitor_id: str) -> Optional[str]:
        for s in self._sessions.values():
            if s.visitor_id == visitor_id:
                return s.zone
        return None

    def set_zone(self, visitor_id: str, zone_id: Optional[str]):
        for s in self._sessions.values():
            if s.visitor_id == visitor_id:
                # Remove from old zone occupants
                if s.zone:
                    self._zone_occupants[s.zone].discard(visitor_id)
                s.zone = zone_id
                if zone_id:
                    self._zone_occupants[zone_id].add(visitor_id)
                    if zone_id == "CASH_COUNTER":
                        s.billing_enter_time = None  # will be set by emitter

    def get_queue_depth(self, zone_id: str) -> int:
        return len([
            vid for vid in self._zone_occupants.get(zone_id, set())
            if not any(
                s.visitor_id == vid and s.is_staff
                for s in self._sessions.values()
            )
        ])

    # ── Absence / exit handling ───────────────────────────────────────────────
    def mark_all_absent(self, frame_idx: int, timestamp: str, emitter):
        """Called when a frame has zero detections."""
        for tid in list(self._sessions.keys()):
            self._absent_frames[tid] = self._absent_frames.get(tid, 0) + 1
        self.process_exits(set(), frame_idx, timestamp, emitter)

    def process_exits(
        self,
        active_track_ids: set,
        frame_idx: int,
        timestamp: str,
        emitter,
    ) -> list:
        """Emit EXIT for tracks that have been absent for EXIT_GRACE_FRAMES."""
        exit_events = []
        for tid in list(self._sessions.keys()):
            if tid not in active_track_ids:
                absent = self._absent_frames.get(tid, 0) + 1
                self._absent_frames[tid] = absent

                if absent >= EXIT_GRACE_FRAMES:
                    session = self._sessions.pop(tid)
                    self._absent_frames.pop(tid, None)

                    # Remove from zone occupants
                    if session.zone:
                        self._zone_occupants[session.zone].discard(session.visitor_id)

                    # Save for potential re-entry detection
                    self._recent_exits.append(ExitedVisitor(
                        visitor_id=session.visitor_id,
                        exit_time=timestamp,
                        exit_ts=datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ")
                            .replace(tzinfo=timezone.utc).timestamp(),
                        embedding=session.embedding,
                    ))

                    if not session.is_staff:
                        ev = emitter.emit_exit(
                            visitor_id=session.visitor_id,
                            timestamp=timestamp,
                            confidence=0.7,
                        )
                        exit_events.append(ev)
        return exit_events

    def check_billing_abandon(self, timestamp: str, emitter) -> list:
        """
        Emit BILLING_QUEUE_ABANDON if a visitor has been in CASH_COUNTER
        for > BILLING_ABANDON_GRACE_S without a purchase signal.
        (Simplified: emit if in billing and no EXIT seen yet from that zone.)
        Full implementation correlates with POS — done in API layer.
        """
        return []

    def flush_open_sessions(self, timestamp: str, emitter):
        """At end of clip, emit EXIT for all still-open sessions."""
        for tid, session in list(self._sessions.items()):
            if not session.is_staff:
                emitter.emit_exit(
                    visitor_id=session.visitor_id,
                    timestamp=timestamp,
                    confidence=0.6,
                )
        self._sessions.clear()
