"""Intense-core identity; the broad reflectivity mask only estimates displacement."""
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import datetime
import math

import numpy as np
from pyproj import Geod, Transformer
from rasterio.features import rasterize
from rasterio.transform import from_origin
from scipy.optimize import linear_sum_assignment
from scipy.signal import fftconvolve
from shapely import affinity, transform
from shapely.geometry import Polygon, mapping, shape

from .storm_detection import AREA_TRANSFORM

GEOD = Geod(ellps="WGS84")
AREA_INVERSE = Transformer.from_crs(6933, 4326, always_xy=True).transform


def displacement(start, end):
    bearing, _, distance = GEOD.inv(start["lon"], start["lat"], end["lon"], end["lat"])
    angle = math.radians(bearing)
    return distance * math.sin(angle), distance * math.cos(angle), distance


def overlap(left, right):
    area = min(left.area, right.area)
    return left.intersection(right).area / area if area > 0 else 0.0


def motion_from_history(history, config, steps=None):
    recent = history[-3:]
    if len(recent) < 3:
        return None
    vectors = []
    sources, assessments = [], []
    for index, (before, after) in enumerate(zip(recent, recent[1:])):
        minutes = (datetime.fromisoformat(after["timestamp"]) -
                   datetime.fromisoformat(before["timestamp"])).total_seconds() / 60
        if minutes <= 0 or minutes > config.max_gap_minutes:
            return None
        east, north, distance = displacement(before, after)
        source, assessment = "centroid", "centroid_baseline"
        if config.direction_mode == "support_checked":
            aligned = (steps or [])[-2:]
            if len(aligned) != 2:
                return None
            step = aligned[index]
            if step["before_timestamp"] != before["timestamp"] or step["timestamp"] != after["timestamp"]:
                return None
            source, assessment = step["source"], step["assessment"]
            east, north = step["east_m"], step["north_m"]
            distance = math.hypot(east, north)
        sources.append(source)
        assessments.append(assessment)
        vectors.append((east / minutes, north / minutes, distance, minutes))
    weights = [1.0, config.motion_recent_weight]
    east, north = np.average(np.array(vectors)[:, :2], axis=0, weights=weights)
    speed = float(np.hypot(east, north))
    mean_speed = float(np.average([v[2] / v[3] for v in vectors], weights=weights))
    mean_length = float(np.mean([v[2] for v in vectors])) / 1000
    coherence = min(1.0, speed / max(mean_speed, 1e-9))
    if mean_length < config.motion_min_displacement_km or speed < 1e-6:
        return None
    return {
        "bearing_deg": float(math.degrees(math.atan2(east, north)) % 360),
        "speed_kmh": speed * .06, "mean_displacement_km": mean_length,
        "mean_interval_minutes": float(np.mean([v[3] for v in vectors])),
        "direction_coherence": coherence,
        "direction_spread_deg": math.degrees(math.acos(coherence)),
        "confidence": "moderate" if coherence >= config.motion_min_coherence else "low",
        "observed_intervals": 2, "uses_future_data": False,
        "source_timestamps": [p["timestamp"] for p in recent],
        "direction_method": "weighted_two_observed_velocities",
        "interval_sources": sources, "support_assessments": assessments,
        "support_conflict": "core_translation_conflict" in assessments,
    }


def trend_geometry(core, motion, config):
    """Fixed dimensions in local geodesic metres, anchored to the core's front edge."""
    if motion is None or motion["direction_coherence"] < config.motion_min_coherence or motion.get("support_conflict"):
        return None
    center = core["centroid_lonlat"]
    angle = math.radians(motion["bearing_deg"])
    forward = np.array([math.sin(angle), math.cos(angle)])
    cross = np.array([math.cos(angle), -math.sin(angle)])
    geometry = shape(core["geometry"])
    polygons = [geometry] if geometry.geom_type == "Polygon" else list(geometry.geoms)
    points = np.array([displacement(center, {"lon": lon, "lat": lat})[:2]
                       for polygon in polygons for lon, lat in polygon.exterior.coords])
    base = max(0.0, float((points @ forward).max()))
    length = config.corridor_length_km * 1000
    near = config.corridor_near_width_km * 500
    far = config.corridor_far_width_km * 500
    anchor_lon, anchor_lat, _ = GEOD.fwd(center["lon"], center["lat"], motion["bearing_deg"], base)

    def lonlat(along, across):
        offset = forward * along + cross * across
        bearing = math.degrees(math.atan2(offset[0], offset[1]))
        lon, lat, _ = GEOD.fwd(anchor_lon, anchor_lat, bearing, float(np.linalg.norm(offset)))
        return [lon, lat]

    corners = [lonlat(0, -near), lonlat(length, -far),
               lonlat(length, far), lonlat(0, near)]
    return {
        "geometry": mapping(Polygon(corners)),
        "axis": {"type": "LineString", "coordinates": [lonlat(0, 0), lonlat(length, 0)]},
        "length_km": config.corridor_length_km,
        "near_width_km": config.corridor_near_width_km,
        "far_width_km": config.corridor_far_width_km,
        "anchor": "intense_core_forward_edge",
        "interpretation": "fixed_direction_corridor",
    }


def register_support(before, after, max_distance_m, resolution_m):
    """Bounded binary-mask cross correlation; registration never assigns a core ID."""
    a, b = before.bounds, after.bounds
    if a[2] + max_distance_m < b[0] or b[2] + max_distance_m < a[0] or \
       a[3] + max_distance_m < b[1] or b[3] + max_distance_m < a[1]:
        return None
    west, south = min(a[0], b[0]), min(a[1], b[1])
    east, north = max(a[2], b[2]), max(a[3], b[3])
    resolution = max(resolution_m, (east - west) / 256, (north - south) / 256)
    width, height = max(2, math.ceil((east - west) / resolution)), max(2, math.ceil((north - south) / resolution))
    grid = from_origin(west, north, resolution, resolution)
    masks = [rasterize([(g, 1)], out_shape=(height, width), transform=grid,
                       dtype="uint8").astype(float) for g in (before, after)]
    # Very small masks are unreliable at this resolution.
    if min(m.sum() for m in masks) < 12:
        return None
    correlation = fftconvolve(masks[1], masks[0][::-1, ::-1], mode="full")
    yy, xx = np.indices(correlation.shape)
    dx, dy = (xx - width + 1) * resolution, -(yy - height + 1) * resolution
    correlation[np.hypot(dx, dy) > max_distance_m] = -1
    index = np.unravel_index(np.argmax(correlation), correlation.shape)
    peak = float(correlation[index])
    alternatives = correlation.copy()
    alternatives[np.hypot(xx - index[1], yy - index[0]) <= 3] = -1
    peak_margin = max(0.0, peak - float(alternatives.max())) / min(m.sum() for m in masks)
    xoff, yoff = float(dx[index]), float(dy[index])
    translated = affinity.translate(before, xoff=xoff, yoff=yoff)
    score = overlap(translated, after)
    # Broad area growth must not masquerade as a high quality registration.
    area_ratio = min(before.area, after.area) / max(before.area, after.area)
    return {"xoff": xoff, "yoff": yoff, "overlap": score,
            "area_ratio": area_ratio, "resolution_km": resolution / 1000,
            "peak_margin": float(peak_margin)}


def motion_step(before, after, before_shape, after_shape, registration, timestamp, config):
    """Preserve observed centroids; assess transport using only the preceding interval."""
    center = before["centroid_lonlat"]
    east, north, _ = displacement(center, after["centroid_lonlat"])
    step = {"before_timestamp": before["history_lonlat"][-1]["timestamp"],
            "timestamp": timestamp.isoformat(), "east_m": east, "north_m": north,
            "source": "centroid", "assessment": "support_absent",
            "registration": registration}
    if registration is None:
        return step
    reg = registration
    if (reg["overlap"] < config.support_direction_min_overlap or
            reg["peak_margin"] < config.support_direction_min_peak_margin or
            reg["area_ratio"] < config.association_min_area_ratio):
        step["assessment"] = "support_uncertain"
        return step
    x, y = AREA_TRANSFORM(center["lon"], center["lat"])
    lon, lat = AREA_INVERSE(x + reg["xoff"], y + reg["yoff"])
    east, north, distance = displacement(center, {"lon": lon, "lat": lat})
    minutes = (timestamp - datetime.fromisoformat(step["before_timestamp"])).total_seconds() / 60
    if distance / minutes * .06 > config.max_centroid_speed_kmh:
        step["assessment"] = "support_speed_rejected"
        return step
    core_score = overlap(affinity.translate(before_shape, xoff=reg["xoff"], yoff=reg["yoff"]), after_shape)
    step["translated_core_overlap"] = core_score
    if core_score < config.overlap_threshold:
        step["assessment"] = "core_translation_conflict"
        return step
    step.update(east_m=east, north_m=north, source="support_translation", assessment="support_confirmed")
    return step


@dataclass
class CoreFrame:
    cores: list[dict]
    support_areas: list[dict]
    events: list[dict]
    missing_track_uids: list[int]
    expired_track_uids: list[int]

    def to_summary(self):
        return {"total_cores": len(self.cores), "total_support_areas": len(self.support_areas),
                "new_cores": sum(c["association"]["method"] == "birth" for c in self.cores),
                "events": self.events, "missing_track_uids": self.missing_track_uids,
                "expired_track_uids": self.expired_track_uids}


class CoreTracker:
    def __init__(self, config, registration_cache=None):
        self.config = config
        self.active = {}
        self.next_uid = 1
        self.last_timestamp = None
        self.registration_cache = registration_cache if registration_cache is not None else {}

    def registration(self, previous, support_id, supports, timestamp):
        cfg = self.config
        if not cfg.use_support_motion or previous["support"] is None or support_id is None:
            return None
        elapsed = (timestamp - previous["timestamp"]).total_seconds() / 60
        key = (previous["timestamp"].isoformat(), timestamp.isoformat(), previous["support_id"], support_id,
               cfg.storm_threshold, cfg.storm_min_area_km2, cfg.max_centroid_speed_kmh, cfg.support_resolution_km)
        if key not in self.registration_cache:
            self.registration_cache[key] = register_support(
                previous["support"], supports[support_id],
                cfg.max_centroid_speed_kmh * elapsed / .06, cfg.support_resolution_km * 1000)
        return self.registration_cache[key]

    def update(self, detection, timestamp):
        if timestamp is None or (self.last_timestamp is not None and timestamp <= self.last_timestamp):
            raise ValueError("Observation timestamps must increase strictly")
        self.last_timestamp = timestamp
        cfg = self.config
        expired = [uid for uid, p in self.active.items()
                   if (timestamp - p["timestamp"]).total_seconds() / 60 > cfg.max_gap_minutes]
        for uid in expired:
            del self.active[uid]
        previous = list(self.active.values())
        current = deepcopy(detection.cores)
        supports = {s["detection_id"]: transform(shape(s["geometry"]), AREA_TRANSFORM, interleaved=False)
                    for s in detection.storms}
        owner = {cid: s["detection_id"] for s in detection.storms for cid in s["cell_ids"]}
        shapes = [transform(shape(c["geometry"]), AREA_TRANSFORM, interleaved=False) for c in current]
        n, m = len(previous), len(current)
        direct, fallback = np.zeros((n, m)), np.zeros((n, m))
        methods, translations = {}, {}
        for i, p in enumerate(previous):
            elapsed = (timestamp - p["timestamp"]).total_seconds() / 60
            for j, c in enumerate(current):
                distance = displacement(p["core"]["centroid_lonlat"], c["centroid_lonlat"])[2]
                if distance / elapsed * .06 > cfg.max_centroid_speed_kmh:
                    continue
                direct[i, j] = overlap(p["shape"], shapes[j])
        direct[direct < cfg.overlap_threshold] = 0
        parents = {j: list(np.flatnonzero(direct[:, j])) for j in range(m)}
        children = {i: list(np.flatnonzero(direct[i])) for i in range(n)}
        for i, p in enumerate(previous):
            if children[i]:
                continue
            elapsed = (timestamp - p["timestamp"]).total_seconds() / 60
            center = p["core"]["centroid_lonlat"]
            # Association stays identical across direction modes for paired evaluation.
            history_motion = motion_from_history(p["core"]["history_lonlat"], replace(cfg, direction_mode="centroid"))
            for j, c in enumerate(current):
                if parents[j]:
                    continue
                distance = displacement(center, c["centroid_lonlat"])[2]
                ratio = min(p["shape"].area, shapes[j].area) / max(p["shape"].area, shapes[j].area)
                if (distance / elapsed * .06 > cfg.max_centroid_speed_kmh or
                    ratio < cfg.association_min_area_ratio or
                    abs(p["core"]["intensity_max"] - c["intensity_max"]) > cfg.association_max_intensity_delta):
                    continue
                candidates = []
                support_id = owner.get(c["cell_id"])
                if cfg.use_support_motion and p["support"] is not None and support_id is not None:
                    reg = self.registration(p, support_id, supports, timestamp)
                    if reg and reg["overlap"] >= cfg.support_min_overlap and reg["area_ratio"] >= .25:
                        candidates.append(("support_translation", reg["xoff"], reg["yoff"], reg))
                if cfg.use_history_fallback and history_motion and history_motion["confidence"] == "moderate":
                    lon, lat, _ = GEOD.fwd(center["lon"], center["lat"], history_motion["bearing_deg"],
                                          history_motion["speed_kmh"] * elapsed / .06)
                    x0, y0 = AREA_TRANSFORM(center["lon"], center["lat"])
                    x1, y1 = AREA_TRANSFORM(lon, lat)
                    candidates.append(("history_translation", x1 - x0, y1 - y0, None))
                for method, xoff, yoff, reg in candidates:
                    score = overlap(affinity.translate(p["shape"], xoff=xoff, yoff=yoff), shapes[j])
                    if score > fallback[i, j]:
                        fallback[i, j] = score
                        methods[i, j] = method
                        translations[i, j] = {"xoff_m": xoff, "yoff_m": yoff, "support_registration": reg}
        scores = direct.copy()
        rejected = 0
        for i, j in zip(*np.where(fallback >= cfg.overlap_threshold)):
            rival = max(np.delete(fallback[i], j).max(initial=0), np.delete(fallback[:, j], i).max(initial=0))
            if fallback[i, j] - rival >= cfg.fallback_ambiguity_margin:
                scores[i, j] = fallback[i, j]
            else:
                rejected += 1
        assigned = {}
        if n and m:
            shared = scores * np.minimum(np.array([p["shape"].area for p in previous])[:, None],
                                        np.array([g.area for g in shapes])[None, :])
            dominance = shared / max(1, shared.max())
            rows, cols = linear_sum_assignment(-((scores > 0) * (min(n, m) + 1) + dominance))
            assigned = {int(j): int(i) for i, j in zip(rows, cols) if scores[i, j] > 0}
        # Reserve all surviving numbers, including missing tracks and tracks retiring this frame.
        reserved = {p["core"]["core_id"] for p in previous}
        events, used = [], set()
        for j, c in enumerate(current):
            i = assigned.get(j)
            if i is None:
                uid = self.next_uid
                self.next_uid += 1
                cid = 1
                while cid in reserved:
                    cid += 1
                reserved.add(cid)
                history, steps, method = [], [], "birth"
            else:
                uid = previous[i]["core"]["track_uid"]
                cid = previous[i]["core"]["core_id"]
                history = deepcopy(previous[i]["core"]["history_lonlat"])
                steps = deepcopy(previous[i]["core"].get("motion_steps", []))
                method = "overlap" if direct[i, j] else methods[i, j]
            split = any(len(children[p]) > 1 for p in parents[j])
            merge = len(parents[j]) > 1
            if split or merge:
                history = []
                steps = []
            elif i is not None:
                reg = self.registration(previous[i], owner.get(c["cell_id"]), supports, timestamp)
                steps.append(motion_step(previous[i]["core"], c, previous[i]["shape"], shapes[j], reg, timestamp, cfg))
                steps = steps[-2:]
            history.append({**c["centroid_lonlat"], "timestamp": timestamp.isoformat()})
            c.update(core_id=cid, track_uid=uid, id_scope="active_lifetime",
                     status="new" if i is None else "active", history_lonlat=history, motion_steps=steps,
                     lineage={"parent_track_uids": [previous[p]["core"]["track_uid"] for p in parents[j]],
                              "split": split, "merge": merge},
                     association={"method": method, "overlap": float(scores[i, j]) if i is not None else None,
                                  "previous_timestamp": previous[i]["timestamp"].isoformat() if i is not None else None,
                                  "translation": translations.get((i, j)) if method.endswith("translation") else None},
                     support_detection_id=owner.get(c["cell_id"]))
            c["motion"] = motion_from_history(history, cfg, steps)
            c["trend"] = trend_geometry(c, c["motion"], cfg)
            used.add(uid)
            self.active[uid] = {"core": c, "shape": shapes[j], "timestamp": timestamp,
                                "support": supports.get(owner.get(c["cell_id"])), "support_id": owner.get(c["cell_id"])}
            if merge:
                events.append({"type": "merge", "parent_track_uids": c["lineage"]["parent_track_uids"],
                               "child_track_uid": uid})
        for i, destinations in children.items():
            uid = previous[i]["core"]["track_uid"]
            if len(destinations) > 1:
                events.append({"type": "split", "parent_track_uid": uid,
                               "child_track_uids": [current[j]["track_uid"] for j in destinations]})
            if destinations and uid not in used:
                self.active.pop(uid, None)
                expired.append(uid)
        if rejected:
            events.append({"type": "ambiguous_translation_rejected", "candidate_count": rejected})
        return CoreFrame(current, deepcopy(detection.storms), events, sorted(set(self.active) - used), sorted(expired))
