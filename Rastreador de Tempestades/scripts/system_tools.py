"""Validate core products, calibrate causal associations, and render the sequence."""
from collections import Counter
from dataclasses import replace
from datetime import datetime
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from rasterio.features import geometry_mask
from rasterio.transform import from_bounds
from shapely.geometry import Point, Polygon, mapping, shape

from src.products import write_json
from src.storm_detection import StormDetection
from src.core_tracking import CoreTracker, GEOD, displacement, motion_from_history, trend_geometry

ROOT = Path(__file__).resolve().parents[1]
COLORS = ["#5df4ff", "#ffcf5a", "#ff7b7b", "#66f2a3", "#8ba7ff", "#f48fff"]


def load_sequence(output_root):
    output_root = Path(output_root)
    summary = json.loads((output_root / "sequence_summary.json").read_text(encoding="utf-8"))
    return summary, [(output_root / datetime.fromisoformat(entry["frame"]["timestamp"]).strftime("%Y%m%d_%H%M"),
                      entry) for entry in summary["frames"]]


def validate_outputs(output_root):
    from dataclasses import fields
    from config import TrackingConfig

    output_root = Path(output_root)
    summary, frames = load_sequence(output_root)
    allowed = {item.name for item in fields(TrackingConfig)}
    config_data = {key: value for key, value in summary["config"].items() if key in allowed}
    config = TrackingConfig(**{**config_data, "output_root": output_root})
    region = Polygon(config.coverage_polygon)
    if not region.is_valid or region.is_empty:
        raise ValueError("Configured coverage polygon is invalid")
    tolerance_region = region.buffer(1e-9)
    errors, counts = [], Counter()
    previous_time = None

    def check_inside(geometry, label):
        if geometry.is_empty or not tolerance_region.covers(geometry):
            errors.append(f"{label}: geometry escapes coverage polygon")

    for folder, entry in frames:
        metadata = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        timestamp = datetime.fromisoformat(metadata["timestamp"])
        if previous_time and timestamp <= previous_time:
            errors.append(f"{folder.name}: non-increasing timestamp")
        previous_time = timestamp

        if metadata.get("crs") != "EPSG:4326" or manifest.get("crs") != "EPSG:4326":
            errors.append(f"{folder.name}: publication CRS is not EPSG:4326")
        if manifest.get("timestamp") != metadata.get("timestamp") or manifest.get("frame_name") != metadata.get("frame_name"):
            errors.append(f"{folder.name}: manifest and metadata frame identity differ")
        if metadata.get("coverage_type") != "polygon" or manifest.get("coverage_type") != "polygon":
            errors.append(f"{folder.name}: missing polygon coverage declaration")
        try:
            metadata_region = shape(metadata["coverage_polygon"])
            manifest_region = shape(manifest["coverage_polygon"])
            if not metadata_region.equals(region) or not manifest_region.equals(region):
                errors.append(f"{folder.name}: published coverage differs from configured polygon")
        except (KeyError, TypeError, ValueError):
            errors.append(f"{folder.name}: invalid or missing coverage polygon")

        cores = metadata.get("cores", [])
        supports = metadata.get("support_areas", [])
        ids = [core["core_id"] for core in cores]
        uids = [core["track_uid"] for core in cores]
        cells = [core["cell_id"] for core in cores]
        if any(len(values) != len(set(values)) for values in (ids, uids, cells)):
            errors.append(f"{folder.name}: duplicate IDs")
        core_by_cell = {core["cell_id"]: core for core in cores}
        support_by_id = {item["detection_id"]: item for item in supports}

        for core in cores:
            label = f"{folder.name}/core {core['track_uid']}"
            geometry = shape(core["geometry"])
            if not geometry.is_valid or geometry.is_empty or geometry.area <= 0:
                errors.append(f"{label}: invalid clipped core footprint")
            check_inside(geometry, label)
            center = core.get("centroid_lonlat", {})
            if not center or not tolerance_region.covers(Point(center["lon"], center["lat"])):
                errors.append(f"{label}: published centroid is outside coverage")
            if core.get("centroid_source") not in ("observed", "clipped_geometry"):
                errors.append(f"{label}: missing published centroid provenance")
            history = core.get("history_lonlat", [])
            times = [datetime.fromisoformat(point["timestamp"]) for point in history]
            if any(a >= b for a, b in zip(times, times[1:])) or any(time > timestamp for time in times):
                errors.append(f"{label}: invalid published causal history")
            if any(not tolerance_region.covers(Point(point["lon"], point["lat"])) for point in history):
                errors.append(f"{label}: history contains coordinates outside coverage")
            support_id = core.get("support_detection_id")
            owner = support_by_id.get(support_id)
            if support_id is not None and (owner is None or core["cell_id"] not in owner["cell_ids"] or
                                           not shape(owner["geometry"]).covers(geometry)):
                errors.append(f"{label}: invalid clipped displacement support")
            motion = core.get("motion")
            if motion and (motion.get("uses_future_data") or motion.get("observed_intervals") != 2 or
                           len(motion.get("source_timestamps", [])) != 3 or
                           any(datetime.fromisoformat(value) > timestamp for value in motion["source_timestamps"])):
                errors.append(f"{label}: invalid causal motion metadata")
            trend = core.get("trend")
            if trend:
                trend_geometry = shape(trend["geometry"])
                if not trend_geometry.is_valid or trend_geometry.is_empty:
                    errors.append(f"{label}: invalid clipped trend geometry")
                check_inside(trend_geometry, label + "/trend")
                if trend.get("axis"):
                    check_inside(shape(trend["axis"]), label + "/trend axis")
                for key in ("length_km", "near_width_km", "far_width_km"):
                    if not math.isclose(trend[key], getattr(config, "corridor_" + key), abs_tol=1e-9):
                        errors.append(f"{label}: non-fixed corridor dimension")

        for support in supports:
            geometry = shape(support["geometry"])
            if not geometry.is_valid or geometry.is_empty or geometry.area <= 0:
                errors.append(f"{folder.name}/support {support['detection_id']}: invalid clipped geometry")
            check_inside(geometry, f"{folder.name}/support {support['detection_id']}")
            if any(cell_id not in core_by_cell for cell_id in support["cell_ids"]):
                errors.append(f"{folder.name}/support {support['detection_id']}: references unpublished core")

        expected_counts = {
            "cells": len(cores), "support": len(supports),
            "trends": sum(core.get("trend") is not None for core in cores),
        }
        for filename, layer_key in (("cells.geojson", "cells"), ("support.geojson", "support"),
                                    ("trends.geojson", "trends"), ("tracks.geojson", "tracks")):
            payload = json.loads((folder / filename).read_text(encoding="utf-8"))
            features = payload.get("features", [])
            for feature in features:
                geometry = shape(feature["geometry"])
                if not geometry.is_valid or geometry.is_empty:
                    errors.append(f"{folder.name}: invalid geometry in {filename}")
                check_inside(geometry, f"{folder.name}/{filename}")
            layer = manifest.get("layers", {}).get(layer_key, {})
            if layer.get("feature_count") != len(features):
                errors.append(f"{folder.name}: {filename} feature count differs from manifest")
            if layer_key in expected_counts and expected_counts[layer_key] != len(features):
                errors.append(f"{folder.name}: {filename} count differs from metadata")
            counts[layer_key] += len(features)

        support_payload = json.loads((folder / "support.geojson").read_text(encoding="utf-8"))
        support_features = {feature["properties"]["detection_id"]: feature
                            for feature in support_payload["features"]}
        for support in supports:
            feature = support_features.get(support["detection_id"])
            if feature is None or not shape(feature["geometry"]).equals(shape(support["geometry"])):
                errors.append(f"{folder.name}: support GeoJSON differs from metadata geometry")
        trend_payload = json.loads((folder / "trends.geojson").read_text(encoding="utf-8"))
        trend_features = {feature["properties"]["track_uid"]: feature
                          for feature in trend_payload["features"]}
        for core in cores:
            if core.get("trend"):
                feature = trend_features.get(core["track_uid"])
                if feature is None or not shape(feature["geometry"]).equals(shape(core["trend"]["geometry"])):
                    errors.append(f"{folder.name}: trends GeoJSON differs from metadata geometry")
        cells_payload = json.loads((folder / "cells.geojson").read_text(encoding="utf-8"))
        cell_features = {feature["properties"]["cell_id"]: feature for feature in cells_payload["features"]}
        for core in cores:
            feature = cell_features.get(core["cell_id"])
            if feature is None or not shape(feature["geometry"]).equals(shape(core["geometry"])):
                errors.append(f"{folder.name}: cells GeoJSON differs from metadata geometry")

        reported = metadata.get("summary", {})
        if (reported.get("total_cores") != len(cores) or
                reported.get("total_support_areas") != len(supports) or
                reported.get("published_trends") != expected_counts["trends"]):
            errors.append(f"{folder.name}: metadata summary counts are inconsistent")
        sequence_counts = (
            entry.get("segmentation", {}).get("total_cores"),
            entry.get("segmentation", {}).get("total_storms"),
            entry.get("tracking", {}).get("total_cores"),
            entry.get("tracking", {}).get("total_support_areas"),
        )
        if sequence_counts != (len(cores), len(supports), len(cores), len(supports)):
            errors.append(f"{folder.name}: sequence summary counts differ from published metadata")
        frame_bounds = entry.get("frame", {}).get("bounds", {})
        if frame_bounds and not all(math.isclose(frame_bounds[key], value, abs_tol=1e-9)
                                     for key, value in zip(("bottom", "left", "top", "right"),
                                                           (manifest["bounds"][0][0], manifest["bounds"][0][1],
                                                            manifest["bounds"][1][0], manifest["bounds"][1][1]))):
            errors.append(f"{folder.name}: sequence summary bounds differ from manifest")
        if entry.get("frame", {}).get("width") != manifest.get("raster", {}).get("width") or \
                entry.get("frame", {}).get("height") != manifest.get("raster", {}).get("height"):
            errors.append(f"{folder.name}: sequence summary raster dimensions differ from manifest")
        counts["cores"] += len(cores)
        counts["support_observations"] += len(supports)

        raster = manifest.get("raster", {})
        try:
            with Image.open(folder / manifest["layers"]["radar"]["path"]) as image:
                rgba = np.asarray(image.convert("RGBA"))
                if image.size != (raster["width"], raster["height"]):
                    errors.append(f"{folder.name}: radar dimensions differ from manifest")
                (south, west), (north, east) = manifest["bounds"]
                if manifest["bounds"] != raster["bounds"]:
                    errors.append(f"{folder.name}: manifest and raster bounds differ")
                affine = from_bounds(west, south, east, north, raster["width"], raster["height"])
                inside = geometry_mask([mapping(region)], out_shape=(raster["height"], raster["width"]),
                                       transform=affine, invert=True, all_touched=False)
                alpha = rgba[..., 3] > 0
                if np.any(alpha & ~inside):
                    errors.append(f"{folder.name}: radar has visible pixels outside coverage polygon")
                if int(alpha.sum()) != raster.get("rendered_pixels"):
                    errors.append(f"{folder.name}: radar visible-pixel count differs from manifest")
        except (KeyError, OSError, ValueError) as error:
            errors.append(f"{folder.name}: radar validation failed ({error})")

        if any(not (folder / layer["path"]).is_file() for layer in manifest.get("layers", {}).values()):
            errors.append(f"{folder.name}: missing manifest layer")

    report = {"passed": not errors, "total_frames": len(frames), "counts": dict(counts),
              "errors": errors,
              "scope": "Published geometry, raster mask, metadata consistency and causal metadata; "
                       "tracking associations use uncropped cache geometries."}
    write_json(output_root / "validation_report.json", report)
    print(f"Validation: {len(frames)} frames, {len(errors)} errors", flush=True)
    if errors:
        raise ValueError("\n".join(errors[:20]))
    return report


def distribution(values):
    return {"n": len(values), "median": float(np.median(values)) if values else None,
            "p90": float(np.percentile(values, 90)) if values else None}


def evaluate(cache, config, registrations):
    tracker = CoreTracker(config, registrations)
    counts, lengths, observed = Counter(), Counter(), {}
    angle_errors, innovations, translated_innovations = [], [], []
    max_display = 0
    snapshots = []
    for item in cache:
        timestamp = datetime.fromisoformat(item["timestamp"])
        result = tracker.update(StormDetection(item["cores"], item["storms"]), timestamp)
        snapshots.append(result.cores)
        for c in result.cores:
            uid = c["track_uid"]
            lengths[uid] += 1
            counts[c["association"]["method"]] += 1
            max_display = max(max_display, c["core_id"])
            prior = observed.get(uid)
            # Only evaluation reads the next observation; tracker has already emitted its prediction.
            if prior and not (c["lineage"]["split"] or c["lineage"]["merge"]) and prior["motion"]:
                minutes = (timestamp - datetime.fromisoformat(prior["history_lonlat"][-1]["timestamp"])).total_seconds() / 60
                east, north, distance = displacement(prior["centroid_lonlat"], c["centroid_lonlat"])
                if distance >= config.motion_min_displacement_km * 1000:
                    bearing = math.degrees(math.atan2(east, north)) % 360
                    angle_errors.append(abs((bearing - prior["motion"]["bearing_deg"] + 180) % 360 - 180))
                    lon, lat, _ = GEOD.fwd(prior["centroid_lonlat"]["lon"], prior["centroid_lonlat"]["lat"],
                                          prior["motion"]["bearing_deg"], prior["motion"]["speed_kmh"] * minutes / .06)
                    innovations.append(displacement({"lon": lon, "lat": lat}, c["centroid_lonlat"])[2] / 1000)
            if prior and c["association"]["translation"]:
                from src.storm_detection import AREA_TRANSFORM
                x0, y0 = AREA_TRANSFORM(prior["centroid_lonlat"]["lon"], prior["centroid_lonlat"]["lat"])
                x1, y1 = AREA_TRANSFORM(c["centroid_lonlat"]["lon"], c["centroid_lonlat"]["lat"])
                translation = c["association"]["translation"]
                translated_innovations.append(math.hypot(x1-x0-translation["xoff_m"],
                                                        y1-y0-translation["yoff_m"]) / 1000)
            observed[uid] = c
            counts["corridors"] += c["trend"] is not None
        for event in result.events:
            counts[event["type"]] += event.get("candidate_count", 1)
    row = {"overlap_threshold": config.overlap_threshold, "motion_min_coherence": config.motion_min_coherence,
           "use_support_motion": config.use_support_motion,
           "use_history_fallback": config.use_history_fallback, "distinct_track_uids": len(lengths),
           "max_display_core_id": max_display, **dict(counts),
           "single_observation_tracks": sum(v == 1 for v in lengths.values()),
           "tracks_ge_3_observations": sum(v >= 3 for v in lengths.values()),
           "track_observation_count": distribution(list(lengths.values())),
           "next_direction_error_deg": distribution(angle_errors),
           "next_centroid_error_km": distribution(innovations),
           "translation_centroid_residual_km": distribution(translated_innovations)}
    return row, snapshots


def compare_overlaps(cache_dir, output_root, config):
    summary, _ = load_sequence(output_root)
    cache = []
    for entry in summary["frames"]:
        path = Path(cache_dir) / (entry["frame"]["frame_name"] + ".json")
        item = json.loads(path.read_text(encoding="utf-8"))
        if (item["signature"]["version"] != 3 or
            item["signature"]["thresholds"] != [config.core_threshold, config.storm_threshold] or
            item["signature"]["areas"] != [config.core_min_area_km2, config.storm_min_area_km2]):
            raise ValueError(f"Detection cache configuration mismatch: {path}")
        cache.append(item)
    experiments = [
        ("direct_10", replace(config, overlap_threshold=.10, use_support_motion=False, use_history_fallback=False)),
        ("history_10", replace(config, overlap_threshold=.10, use_support_motion=False, use_history_fallback=True)),
        ("support_10", replace(config, overlap_threshold=.10)),
        ("support_20", replace(config, overlap_threshold=.20)),
        ("support_30", replace(config, overlap_threshold=.30)),
    ]
    registrations, rows, selected = {}, [], None
    for name, candidate in experiments:
        row, snapshots = evaluate(cache, candidate, registrations)
        rows.append({"experiment": name, **row})
        if candidate.overlap_threshold == config.overlap_threshold and candidate.use_support_motion:
            selected = snapshots
        print(f"{name}: {row['distinct_track_uids']} tracks, {row['single_observation_tracks']} single observations",
              flush=True)
    if selected is None:
        _, selected = evaluate(cache, config, registrations)
    # Heading weights evaluated on the SAME associations to avoid candidate-population confounding.
    headings = []
    for weight in (1.0, 2.0, 3.0):
        for coherence in (.70, .85):
            cfg = replace(config, motion_recent_weight=weight, motion_min_coherence=coherence)
            observed, angles, next_errors = {}, [], []
            for cores in selected:
                for c in cores:
                    before = observed.get(c["track_uid"])
                    if before and not(c["lineage"]["split"] or c["lineage"]["merge"]):
                        motion = motion_from_history(before["history_lonlat"], cfg, before.get("motion_steps"))
                        if motion and motion["direction_coherence"] >= coherence:
                            east, north, distance = displacement(before["centroid_lonlat"], c["centroid_lonlat"])
                            if distance >= cfg.motion_min_displacement_km * 1000:
                                bearing = math.degrees(math.atan2(east, north)) % 360
                                angles.append(abs((bearing - motion["bearing_deg"] + 180) % 360 - 180))
                                minutes = (datetime.fromisoformat(c["history_lonlat"][-1]["timestamp"]) -
                                           datetime.fromisoformat(before["history_lonlat"][-1]["timestamp"])).total_seconds()/60
                                lon, lat, _ = GEOD.fwd(before["centroid_lonlat"]["lon"], before["centroid_lonlat"]["lat"],
                                                      motion["bearing_deg"], motion["speed_kmh"] * minutes / .06)
                                next_errors.append(displacement({"lon": lon, "lat": lat}, c["centroid_lonlat"])[2]/1000)
                    observed[c["track_uid"]] = c
            headings.append({"recent_weight": weight, "min_coherence": coherence,
                             "next_direction_error_deg": distribution(angles),
                             "next_centroid_error_km": distribution(next_errors)})
    sizes = []
    # Dimensions are display settings; quantify past-step distance coverage, not forecast skill.
    steps = [displacement(c["history_lonlat"][-2], c["history_lonlat"][-1])[2]/1000
             for cores in selected for c in cores if len(c["history_lonlat"]) >= 2]
    for length in (10, 20, 30):
        sizes.append({"length_km": length, "near_width_km": 10, "far_width_km": 20,
                      "fraction_observed_steps_le_length": float(np.mean(np.array(steps) <= length)) if steps else None})
    payload = {
        "frames": len(cache), "definition": "Core intersection / smaller core area in EPSG:6933",
        "association_results": rows, "heading_results_same_associations": headings,
        "corridor_display_sizes": sizes, "observed_step_km": distribution(steps),
        "selected_config": config.to_dict(),
        "limits": ["Unlabeled sequence: fewer births is not proof of identity accuracy.",
                   "Direction errors use subsequent associated centroids only during evaluation.",
                   "Same sequence used for tuning and evaluation; no independent validation set.",
                   "Heading comparisons share associations; coherence filters change evaluation sample sizes.",
                   "Growth, splitting and shape changes can move centroids without equivalent transport.",
                   "Corridor dimensions are fixed display settings, not calibrated forecast horizons."],
    }
    write_json(Path(output_root) / "calibration_report.json", payload)
    return payload


def rings(geometry):
    polygons = [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
    return [ring for polygon in polygons for ring in polygon]


def line_parts(geometry):
    if geometry["type"] == "LineString":
        return [geometry["coordinates"]]
    if geometry["type"] == "MultiLineString":
        return geometry["coordinates"]
    return []


def generate_gif(output_root):
    summary, frames = load_sequence(output_root)
    basemap = json.loads((ROOT / "preview" / "assets" / "brazil-states.geojson").read_text(encoding="utf-8"))
    images = []
    width, height = 1000, 1000
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/segoeui.ttf", 20)
        small = ImageFont.truetype("C:/Windows/Fonts/segoeui.ttf", 14)
    except OSError:
        font = small = ImageFont.load_default()
    for folder, _ in frames:
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        metadata = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
        (south, west), (north, east) = manifest["bounds"]

        def project(point):
            return ((point[0] - west) / (east - west) * width,
                    (north - point[1]) / (north - south) * height)

        canvas = Image.new("RGBA", (width, height), "#08111f")
        draw = ImageDraw.Draw(canvas)
        for feature in basemap["features"]:
            for ring in rings(feature["geometry"]):
                draw.line([project(p) for p in ring], fill="#33485e", width=1)
        with Image.open(folder / "radar.png") as source:
            canvas.alpha_composite(source.convert("RGBA").resize((width, height)))
        draw = ImageDraw.Draw(canvas, "RGBA")
        for core in metadata["cores"]:
            color = COLORS[(core["core_id"] - 1) % len(COLORS)]
            for ring in rings(core["geometry"]):
                draw.line([project(p) for p in ring], fill=color, width=2)
            if core["trend"]:
                for ring in rings(core["trend"]["geometry"]):
                    draw.line([project(p) for p in ring], fill=color, width=2)
                if core["trend"].get("axis"):
                    for line in line_parts(core["trend"]["axis"]):
                        draw.line([project(p) for p in line], fill=color, width=3)
            center = core["centroid_lonlat"]
            point = project((center["lon"], center["lat"]))
            if 0 <= point[0] < width and 70 <= point[1] < height - 55:
                draw.text(point, str(core["core_id"]), fill="white", font=small,
                          stroke_width=2, stroke_fill="#08111f")
        draw.rectangle((0, 0, width, 65), fill="#08111f")
        draw.text((24, 12), "Nucleos intensos  |  " + metadata["timestamp"].replace("T", " "),
                  fill="#eef4ff", font=font)
        draw.rectangle((0, height - 52, width, height), fill="#08111f")
        cfg = summary["config"]
        draw.text((24, height - 38),
                  f"Nucleo >=40 dBZ  /  Trapezio {cfg['corridor_length_km']:g} km  /  Direcao: t, t-1, t-2",
                  fill="#bacbdd", font=small)
        coverage = shape(manifest["coverage_polygon"])
        mask = Image.new("L", (width, height), 0)
        mask_draw = ImageDraw.Draw(mask)
        for polygon in ([coverage] if coverage.geom_type == "Polygon" else coverage.geoms):
            mask_draw.polygon([project(point) for point in polygon.exterior.coords], fill=255)
            for interior in polygon.interiors:
                mask_draw.polygon([project(point) for point in interior.coords], fill=0)
        outside = Image.new("RGBA", (width, height), "#08111f")
        clipped_canvas = Image.composite(canvas, outside, mask)
        canvas.close()
        mask.close()
        outside.close()
        canvas = clipped_canvas
        images.append(canvas.convert("P", palette=Image.Palette.ADAPTIVE, colors=256))
        canvas.close()
    path = Path(output_root) / "core_tracking_preview.gif"
    images[0].save(path, save_all=True, append_images=images[1:],
                   duration=summary["config"]["gif_frame_duration_ms"], loop=0, disposal=2)
    for image in images:
        image.close()
    print(path, flush=True)
    return path
