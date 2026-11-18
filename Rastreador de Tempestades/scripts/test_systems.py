import sys
sys.dont_write_bytecode = True
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import unittest
from datetime import datetime, timedelta
from dataclasses import replace

import numpy as np
from rasterio.coords import BoundingBox
from rasterio.crs import CRS
from rasterio.transform import from_origin
from shapely.geometry import box, shape, mapping

from config import DEFAULT_CONFIG
from src.geotiff_reader import GeoTiffFrame
from src.storm_detection import StormDetection, detect_storms, detect_support_areas, polygon_area
from src.core_tracking import CoreTracker, GEOD, motion_from_history, motion_step, trend_geometry
from shapely import transform
from src.storm_detection import AREA_TRANSFORM

START = datetime(2026, 11, 15, 11)


def storm(bounds, cid=1):
    geometry = box(*bounds)
    return {"detection_id": cid, "cell_ids": [cid], "geometry": mapping(geometry),
            "centroid_lonlat": {"lon": geometry.centroid.x, "lat": geometry.centroid.y},
            "area_km2": polygon_area(mapping(geometry)), "area_px": 100,
            "intensity_mean": 37, "intensity_max": 48}


def detection(*storms):
    cores = []
    for s in storms:
        core = dict(s, cell_id=s["cell_ids"][0], intensity_mean=45)
        core.pop("cell_ids")
        core.pop("detection_id")
        cores.append(core)
    return StormDetection(cores, list(storms))


class TrackingTests(unittest.TestCase):
    def setUp(self):
        self.tracker = CoreTracker(DEFAULT_CONFIG)

    def update(self, minute, *storms):
        return self.tracker.update(detection(*storms), START + timedelta(minutes=minute))

    def test_growth_preserves_identity(self):
        first = self.update(0, storm((-50, -25, -49.8, -24.8))).cores[0]
        grown = self.update(20, storm((-50.1, -25.1, -49.7, -24.7))).cores[0]
        self.assertEqual(first["track_uid"], grown["track_uid"])
        self.assertEqual(grown["association"]["method"], "overlap")
        self.assertIsNone(first["trend"])

    def test_stationary_support_corrects_growth_and_conflict_hides_corridor(self):
        cfg = replace(DEFAULT_CONFIG, direction_mode="support_checked")
        before = detection(storm((-50, -25, -49.8, -24.8))).cores[0]
        before["history_lonlat"] = [{**before["centroid_lonlat"], "timestamp": START.isoformat()}]
        grown = detection(storm((-50, -25, -49.7, -24.8))).cores[0]
        a = transform(shape(before["geometry"]), AREA_TRANSFORM, interleaved=False)
        b = transform(shape(grown["geometry"]), AREA_TRANSFORM, interleaved=False)
        reg = {"xoff": 0, "yoff": 0, "overlap": 1, "area_ratio": 1, "peak_margin": .1}
        step = motion_step(before, grown, a, b, reg, START+timedelta(minutes=20), cfg)
        self.assertEqual(step["source"], "support_translation")
        self.assertAlmostEqual(step["east_m"], 0)
        uncertain = motion_step(before, grown, a, b, {**reg, "peak_margin": .001},
                                START+timedelta(minutes=20), cfg)
        self.assertEqual(uncertain["source"], "centroid")
        self.assertEqual(uncertain["assessment"], "support_uncertain")
        self.assertGreater(uncertain["east_m"], 1000)
        points = [{**before["centroid_lonlat"], "timestamp": (START+timedelta(minutes=i*20)).isoformat()}
                  for i in range(3)]
        steps = [{**step, "before_timestamp": points[i]["timestamp"], "timestamp": points[i+1]["timestamp"]}
                 for i in range(2)]
        self.assertIsNone(motion_from_history(points, cfg, steps))
        conflict = motion_step(before, grown, a, b, {**reg, "xoff": 40000}, START+timedelta(minutes=20), cfg)
        self.assertEqual(conflict["assessment"], "core_translation_conflict")
        points[1]["lon"] += .05
        points[2]["lon"] += .1
        steps = [{**conflict, "before_timestamp": points[i]["timestamp"], "timestamp": points[i+1]["timestamp"]}
                 for i in range(2)]
        motion = motion_from_history(points, cfg, steps)
        self.assertTrue(motion["support_conflict"])
        self.assertIsNone(trend_geometry(grown, motion, cfg))
        steps[0]["timestamp"] = points[2]["timestamp"]
        self.assertIsNone(motion_from_history(points, cfg, steps))

    def test_checked_direction_keeps_associations_and_causal_steps(self):
        centroid = CoreTracker(replace(DEFAULT_CONFIG, direction_mode="centroid"))
        checked = CoreTracker(replace(DEFAULT_CONFIG, direction_mode="support_checked"))
        for i in range(4):
            frame = detection(storm((-50+i*.05, -25, -49.8+i*.05, -24.8)))
            timestamp = START + timedelta(minutes=i*20)
            baseline = centroid.update(frame, timestamp).cores[0]
            corrected = checked.update(frame, timestamp).cores[0]
            self.assertEqual(baseline["association"], corrected["association"])
            self.assertEqual(baseline["track_uid"], corrected["track_uid"])
            self.assertEqual(baseline["history_lonlat"], corrected["history_lonlat"])
            self.assertEqual(len(corrected["motion_steps"]), min(i, 2))
            if i >= 2:
                self.assertAlmostEqual(corrected["motion"]["bearing_deg"], 90, delta=.1)
                self.assertFalse(corrected["motion"]["uses_future_data"])
                self.assertEqual(corrected["motion"]["source_timestamps"][-1], timestamp.isoformat())

    def test_split_dominant_child_merge_lineage_and_motion_reset(self):
        first = self.update(0, storm((-50, -25, -49.6, -24.8))).cores[0]
        split = self.update(20, storm((-50, -25, -49.9, -24.8), 1),
                            storm((-49.85, -25, -49.6, -24.8), 2))
        self.assertEqual(split.cores[1]["track_uid"], first["track_uid"])
        self.assertNotEqual(split.cores[0]["track_uid"], first["track_uid"])
        self.assertEqual(split.events[0]["type"], "split")
        self.assertTrue(all(s["motion"] is None for s in split.cores))
        merged = self.update(40, storm((-50, -25, -49.6, -24.8)))
        self.assertEqual(merged.cores[0]["track_uid"], first["track_uid"])
        self.assertEqual(len(merged.cores[0]["lineage"]["parent_track_uids"]), 2)
        self.assertIsNone(merged.cores[0]["trend"])
        self.assertFalse(merged.missing_track_uids)

    def test_dropout_and_expiry(self):
        original = self.update(0, storm((-50, -25, -49.8, -24.8))).cores[0]
        self.assertEqual(self.update(20).missing_track_uids, [original["track_uid"]])
        self.assertEqual(self.update(40, storm((-50, -25, -49.8, -24.8))).cores[0]["track_uid"], original["track_uid"])
        self.update(60)
        self.update(80)
        last = self.update(100, storm((-50, -25, -49.8, -24.8)))
        self.assertIn(original["track_uid"], last.expired_track_uids)
        self.assertNotEqual(last.cores[0]["track_uid"], original["track_uid"])
        self.assertEqual(last.cores[0]["core_id"], original["core_id"])

    def test_causal_eastward_trapezoid_and_unequal_interval(self):
        self.update(0, storm((-50, -25, -49.8, -24.8)))
        second = self.update(20, storm((-49.95, -25, -49.75, -24.8))).cores[0]
        third = self.update(60, storm((-49.85, -25, -49.65, -24.8))).cores[0]
        self.assertIsNone(second["motion"])
        self.assertAlmostEqual(third["motion"]["bearing_deg"], 90, delta=.1)
        self.assertEqual(third["motion"]["observed_intervals"], 2)
        self.assertEqual(third["motion"]["mean_interval_minutes"], 30)
        self.assertFalse(third["motion"]["uses_future_data"])
        axis = third["trend"]["axis"]["coordinates"]
        bearing, _, distance = GEOD.inv(*axis[0], *axis[1])
        self.assertAlmostEqual(bearing, 90, delta=.2)
        self.assertAlmostEqual(distance / 1000, DEFAULT_CONFIG.corridor_length_km, delta=.01)
        corners = third["trend"]["geometry"]["coordinates"][0]
        self.assertAlmostEqual(GEOD.inv(*corners[0], *corners[3])[2]/1000, 10, delta=.01)
        self.assertAlmostEqual(GEOD.inv(*corners[1], *corners[2])[2]/1000, 20, delta=.01)
        self.assertTrue(shape(third["trend"]["geometry"]).is_valid)

    def test_translated_overlap_and_ambiguity(self):
        # Establish a fast but plausible eastward movement with a small direct overlap.
        self.update(0, storm((-50, -25, -49.8, -24.8)))
        prior = self.update(20, storm((-49.83, -25, -49.63, -24.8))).cores[0]
        recovered = self.update(60, storm((-49.49, -25, -49.29, -24.8))).cores[0]
        self.assertEqual(recovered["track_uid"], prior["track_uid"])
        self.assertEqual(recovered["association"]["method"], "support_translation")
        tracker = CoreTracker(DEFAULT_CONFIG)
        tracker.update(detection(storm((-50, -25, -49.8, -24.8))), START)
        tracker.update(detection(storm((-49.83, -25, -49.63, -24.8))), START + timedelta(minutes=20))
        ambiguous = tracker.update(detection(storm((-49.49, -25, -49.40, -24.8), 1),
                                            storm((-49.39, -25, -49.29, -24.8), 2)),
                                   START + timedelta(minutes=60))
        self.assertTrue(all(s["association"]["method"] == "birth" for s in ambiguous.cores))

    def test_missing_or_nonmonotonic_timestamp_rejected(self):
        with self.assertRaises(ValueError):
            self.tracker.update(detection(), None)
        self.update(0)
        with self.assertRaises(ValueError):
            self.update(0)

    def test_implausible_jump_not_inherited(self):
        sid = self.update(0, storm((-50, -25, -49.8, -24.8))).cores[0]["track_uid"]
        self.assertNotEqual(self.update(20, storm((-45, -25, -44.8, -24.8))).cores[0]["track_uid"], sid)

    def test_shared_broad_area_does_not_merge_core_identities(self):
        left = storm((-50, -25, -49.9, -24.9), 1)
        right = storm((-49.8, -25, -49.7, -24.9), 2)
        first_detection = detection(left, right)
        broad = storm((-50.2, -25.2, -49.5, -24.7))
        broad["cell_ids"] = [1, 2]
        first_detection.storms = [broad]
        first = self.tracker.update(first_detection, START)
        changed = detection(left, right)
        # Expansion of shared support must not affect core geometry or topology.
        broad = storm((-50.4, -25.4, -49.3, -24.5))
        broad["cell_ids"] = [1, 2]
        changed.storms = [broad]
        second = self.tracker.update(changed, START + timedelta(minutes=20))
        self.assertEqual([c["track_uid"] for c in first.cores], [c["track_uid"] for c in second.cores])
        self.assertEqual([c["geometry"] for c in first.cores], [c["geometry"] for c in second.cores])
        self.assertFalse(second.events)

    def test_three_point_heading_rejects_reversal_and_ignores_older_points(self):
        config = replace(DEFAULT_CONFIG, motion_recent_weight=1, direction_mode="centroid")
        points = [{"lon": lon, "lat": -25, "timestamp": (START + timedelta(minutes=i*20)).isoformat()}
                  for i, lon in enumerate([-51, -50, -49.9, -50])]
        reversal = motion_from_history(points, config)
        self.assertEqual(reversal["confidence"], "low")
        self.assertIsNone(trend_geometry(detection(storm((-50, -25, -49.8, -24.8))).cores[0],
                                         reversal, config))
        moving = points[:-1]
        moving.append({"lon": -49.8, "lat": -25, "timestamp": (START+timedelta(minutes=60)).isoformat()})
        motion = motion_from_history(moving, config)
        moving[0]["lon"] = -30
        self.assertEqual(motion, motion_from_history(moving, config))

    def test_history_translation_without_support(self):
        tracker = CoreTracker(replace(DEFAULT_CONFIG, use_support_motion=False))
        frames = [(-50, -49.8), (-49.83, -49.63), (-49.66, -49.46), (-49.32, -49.12)]
        results = [tracker.update(detection(storm((a, -25, b, -24.8))),
                                  START+timedelta(minutes=minute))
                   for (a, b), minute in zip(frames, [0, 20, 40, 80])]
        self.assertEqual(results[-1].cores[0]["track_uid"], results[0].cores[0]["track_uid"])
        self.assertEqual(results[-1].cores[0]["association"]["method"], "history_translation")


class SegmentationTests(unittest.TestCase):
    def test_valid_core_survives_without_accepted_support(self):
        data = np.zeros((60, 60), np.float32)
        data[10:16, 10:16] = 45
        transform = from_origin(-50, -24, .01, .01)
        frame = GeoTiffFrame(Path("synthetic.tif"), "synthetic", START, data, np.ones_like(data, bool),
                             -1, 60, 60, CRS.from_epsg(4326), BoundingBox(-50, -24.6, -49.4, -24),
                             transform, .01, .01, 1, 0, 45)
        detected = detect_storms(frame, DEFAULT_CONFIG)
        self.assertEqual(len(detected.cores), 1)
        self.assertFalse(detected.storms)
        tracker = CoreTracker(DEFAULT_CONFIG)
        first = tracker.update(detected, START).cores[0]
        second = tracker.update(detected, START+timedelta(minutes=20)).cores[0]
        self.assertIsNone(second["support_detection_id"])
        self.assertEqual(first["track_uid"], second["track_uid"])

    def test_shared_edges_with_irregular_decimal_grid(self):
        data = np.zeros((60, 60), np.float32)
        data[5:30, 5:30] = 32
        data[15:25, 20:30] = 45  # Core touches the same right edge from a different crop.
        transform = from_origin(-52.762012345678, 5.691023456789, .0107145309721, .0107145309721)
        frame = GeoTiffFrame(Path("synthetic.tif"), "synthetic", START, data, np.ones_like(data, bool),
                             -1, 60, 60, CRS.from_epsg(4326), BoundingBox(-53, 5, -52, 6),
                             transform, .01, .01, 1, 0, 45)
        detected = detect_storms(frame, DEFAULT_CONFIG)
        self.assertTrue(shape(detected.storms[0]["geometry"]).covers(shape(detected.cores[0]["geometry"])))

    def test_thresholds_nested_cores_holes_and_no_core_rejection(self):
        data = np.zeros((60, 60), np.float32)
        data[5:25, 5:25] = 32
        data[8:13, 8:13] = 40
        data[17:22, 17:22] = 45
        data[14:16, 14:16] = 0  # Preserve holes in the outer footprint.
        data[35:55, 35:55] = 39  # Broad echo without an intense core is excluded.
        transform = from_origin(-50, -24, .01, .01)
        frame = GeoTiffFrame(Path("synthetic.tif"), "synthetic", START, data, np.ones_like(data, bool),
                             -1, 60, 60, CRS.from_epsg(4326), BoundingBox(-50, -24.6, -49.4, -24),
                             transform, .01, .01, 1, 0, 45)
        config = replace(DEFAULT_CONFIG, core_min_area_km2=5, storm_min_area_km2=10)
        detected = detect_storms(frame, config)
        self.assertEqual(len(detected.storms), 1)
        self.assertEqual(len(detected.cores), 2)
        outer = shape(detected.storms[0]["geometry"])
        self.assertEqual(len(outer.interiors), 1)
        self.assertTrue(all(outer.covers(shape(c["geometry"])) for c in detected.cores))
        self.assertTrue(all(c["intensity_mean"] >= 40 for c in detected.cores))
        self.assertEqual(detected.storms, detect_support_areas(frame, detected.cores, config))
        for threshold in (30, 35):
            candidate = replace(config, storm_threshold=threshold)
            self.assertEqual(detect_storms(frame, candidate).cores, detected.cores)
            self.assertEqual(detect_storms(frame, candidate).storms, detect_support_areas(frame, detected.cores, candidate))


if __name__ == "__main__":
    unittest.main()
