"""Maps person bounding box centroids to store zones.

Uses shapely point-in-polygon. Zone polygons in store_layout.json are defined
in the coordinate space of the Brigade Road floor plan image (940x451px).

At runtime, we scale the floor plan coordinates to match the actual video
frame dimensions, since CAM_1 etc. may be 1080p or different resolutions.
"""

import logging
from typing import Optional

from shapely.geometry import Point, Polygon

logger = logging.getLogger(__name__)

CAMERA_ZONE_COVERAGE = {
    "CAM_ENTRY_01":  ["ENTRY_EXIT", "FOH"],
    "CAM_FLOOR_01":  ["FOH", "SKINCARE_WALL", "MAKEUP_WALL", "FRAGRANCE", "NAIL_UNIT", "MAKEUP_UNIT"],
    "CAM_FLOOR_02":  ["FOH", "SKINCARE_WALL", "MAKEUP_WALL", "FRAGRANCE", "NAIL_UNIT", "MAKEUP_UNIT"],
    "CAM_FLOOR_03":  ["CASH_COUNTER", "PMU", "FOH"],
    "CAM_BILLING_01":["CASH_COUNTER", "PMU", "MAKEUP_UNIT"],
}

PRIORITY_ORDER = [
    "CASH_COUNTER", "PMU",
    "NAIL_UNIT", "FRAGRANCE", "MAKEUP_UNIT",
    "SKINCARE_WALL", "MAKEUP_WALL",
    "FOH",
]


class ZoneMapper:
    def __init__(self, layout: dict, camera_id: str):
        self.camera_id = camera_id
        self.layout_w  = layout["image_dimensions"]["width"]
        self.layout_h  = layout["image_dimensions"]["height"]

        allowed = CAMERA_ZONE_COVERAGE.get(camera_id)
        self._polygons: dict[str, Polygon] = {}

        for zone in layout["zones"]:
            zid = zone["zone_id"]
            if zid == "ENTRY_EXIT":
                continue
            if allowed and zid not in allowed:
                continue
            pts = zone["polygon"]
            if len(pts) >= 3:
                self._polygons[zid] = Polygon(pts)

        self._entry_line = None
        for zone in layout["zones"]:
            if zone["zone_id"] == "ENTRY_EXIT" and "entry_line" in zone:
                self._entry_line = zone["entry_line"]
                break

        logger.info(f"ZoneMapper camera={camera_id} zones={list(self._polygons.keys())}")

    def get_zone(self, cx: float, cy: float, frame_shape: tuple) -> Optional[str]:
        """Scale centroid to layout coords, return first matching zone."""
        fh, fw = frame_shape[:2]
        lx = cx * (self.layout_w / fw)
        ly = cy * (self.layout_h / fh)
        pt = Point(lx, ly)

        for zid in PRIORITY_ORDER:
            if zid in self._polygons and self._polygons[zid].contains(pt):
                return zid
        for zid, poly in self._polygons.items():
            if zid not in PRIORITY_ORDER and poly.contains(pt):
                return zid
        return None

    def get_entry_line_x(self, frame_w: int) -> Optional[int]:
        if self._entry_line is None:
            return None
        return int(self._entry_line["x"] * frame_w / self.layout_w)

    def is_entry_camera(self) -> bool:
        return self.camera_id == "CAM_ENTRY_01"
