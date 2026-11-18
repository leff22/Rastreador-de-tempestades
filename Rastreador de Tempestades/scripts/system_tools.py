"""Validate core products, calibrate causal associations, and render the sequence."""
from collections import Counter
from dataclasses import replace
from datetime import datetime
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from shapely.geometry import shape

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
    from config import TrackingConfig
    summary, frames = load_sequence(output_root)
    config = TrackingConfig(**{**summary["config"], "output_root": Path(output_root)})
    errors, counts, observed, active = [], Counter(), {}, {}
    previous_time = None
    max_display = 0
    for folder, _ in frames:
        metadata = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
        timestamp = datetime.fromisoformat(metadata["timestamp"])
        if previous_time and timestamp <= previous_time:
            errors.append(f"{folder.name}: non-increasing timestamp")
        previous_time = timestamp
        cores = metadata["cores"]
        ids = [c["core_id"] for c in cores]
        uids = [c["track_uid"] for c in cores]
        cells = [c["cell_id"] for c in cores]
        if any(len(values) != len(set(values)) for values in (ids, uids, cells)):
            errors.append(f"{folder.name}: duplicate IDs")
        for uid in metadata["summary"]["expired_track_uids"]:
            active.pop(uid, None)
        support = {s["detection_id"]: s for s in metadata["support_areas"]}
        for core in cores:
            uid = core["track_uid"]
            label = f"{folder.name}/core {uid}"
            geometry = shape(core["geometry"])
            if (not geometry.is_valid or geometry.is_empty or core["area_km2"] < config.core_min_area_km2 or
                core["intensity_mean"] < config.core_threshold - 1e-5):
                errors.append(f"{label}: invalid intense footprint")
            owner = support.get(core["support_detection_id"])
            if core["support_detection_id"] is not None and (owner is None or
                core["cell_id"] not in owner["cell_ids"] or not shape(owner["geometry"]).covers(geometry)):
                errors.append(f"{label}: invalid displacement support")
            for other_uid, display in active.items():
                if other_uid != uid and display == core["core_id"]:
                    errors.append(f"{label}: reused a live display ID")
            association = core["association"]
            counts[association["method"]] += 1
            prior = observed.get(uid)
            if association["method"] == "birth":
                if prior:
                    errors.append(f"{label}: reused track UID")
            elif prior is None or prior["timestamp"] != association["previous_timestamp"]:
                errors.append(f"{label}: missing preceding observation")
            else:
                if prior["core_id"] != core["core_id"]:
                    errors.append(f"{label}: changed live ID")
                elapsed = (timestamp - datetime.fromisoformat(prior["timestamp"])).total_seconds() / 60
                distance = displacement(prior["centroid_lonlat"], core["centroid_lonlat"])[2]
                if elapsed > config.max_gap_minutes or distance / elapsed * .06 > config.max_centroid_speed_kmh + 1e-5:
                    errors.append(f"{label}: implausible association")
                a = shape(prior["geometry"])
                from shapely import affinity, transform
                from src.storm_detection import AREA_TRANSFORM
                from src.core_tracking import overlap
                a = transform(a, AREA_TRANSFORM, interleaved=False)
                b = transform(geometry, AREA_TRANSFORM, interleaved=False)
                translation = association["translation"]
                if translation:
                    a = affinity.translate(a, xoff=translation["xoff_m"], yoff=translation["yoff_m"])
                if not math.isclose(overlap(a, b), association["overlap"], abs_tol=1e-8):
                    errors.append(f"{label}: overlap differs from published geometry")
            history = core["history_lonlat"]
            times = [datetime.fromisoformat(p["timestamp"]) for p in history]
            if not times or times[-1] != timestamp or any(a >= b for a, b in zip(times, times[1:])):
                errors.append(f"{label}: invalid causal history")
            motion = core["motion"]
            if motion != motion_from_history(history, config, core.get("motion_steps")):
                errors.append(f"{label}: motion differs from history")
            steps = core.get("motion_steps", [])
            if prior and not (core["lineage"]["split"] or core["lineage"]["merge"]) and steps:
                from src.core_tracking import motion_step
                from shapely import transform
                from src.storm_detection import AREA_TRANSFORM
                before_shape = transform(shape(prior["geometry"]), AREA_TRANSFORM, interleaved=False)
                after_shape = transform(geometry, AREA_TRANSFORM, interleaved=False)
                expected_step = motion_step(prior, core, before_shape, after_shape,
                                            steps[-1]["registration"], timestamp, config)
                if steps[-1] != expected_step:
                    errors.append(f"{label}: support motion differs from published geometry")
            if len(steps) != min(2, len(history)-1):
                errors.append(f"{label}: wrong number of causal motion intervals")
            trend = core["trend"]
            expected = trend_geometry(core, motion, config)
            if (trend is None) != (expected is None):
                errors.append(f"{label}: unexpected corridor availability")
            if trend:
                counts["corridors"] += 1
                if not shape(trend["geometry"]).is_valid or motion["uses_future_data"] or motion["observed_intervals"] != 2:
                    errors.append(f"{label}: invalid causal corridor")
                for key in ("length_km", "near_width_km", "far_width_km"):
                    if trend[key] != getattr(config, "corridor_" + key):
                        errors.append(f"{label}: non-fixed corridor dimension")
                axis = trend["axis"]["coordinates"]
                bearing, _, length = GEOD.inv(*axis[0], *axis[1])
                delta = abs((bearing - motion["bearing_deg"] + 180) % 360 - 180)
                if abs(length / 1000 - config.corridor_length_km) > .02 or delta > .2:
                    errors.append(f"{label}: wrong corridor orientation or length")
            if (core["lineage"]["split"] or core["lineage"]["merge"]) and (motion or trend):
                errors.append(f"{label}: topology did not reset motion")
            observed[uid] = {**core, "timestamp": metadata["timestamp"]}
            active[uid] = core["core_id"]
            max_display = max(max_display, core["core_id"])
        counts["cores"] += len(cores)
        counts["support_observations"] += len(support)
        for event in metadata["summary"]["events"]:
            counts[event["type"]] += event.get("candidate_count", 1)
        for filename in ("cells.geojson", "support.geojson", "trends.geojson", "tracks.geojson"):
            payload = json.loads((folder / filename).read_text(encoding="utf-8"))
            if any(not shape(f["geometry"]).is_valid for f in payload["features"]):
                errors.append(f"{folder.name}: invalid {filename}")
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        if any(not (folder / layer["path"]).is_file() for layer in manifest["layers"].values()):
            errors.append(f"{folder.name}: missing manifest layer")
    report = {"passed": not errors, "total_frames": len(frames), "counts": dict(counts),
              "distinct_track_uids": len(observed), "max_display_core_id": max_display, "errors": errors,
              "scope": "Structural and causal consistency; identities lack meteorological ground truth."}
    write_json(Path(output_root) / "validation_report.json", report)
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
                draw.line([project(p) for p in core["trend"]["axis"]["coordinates"]], fill=color, width=3)
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
        images.append(canvas.convert("P", palette=Image.Palette.ADAPTIVE, colors=256))
        canvas.close()
    path = Path(output_root) / "core_tracking_preview.gif"
    images[0].save(path, save_all=True, append_images=images[1:],
                   duration=summary["config"]["gif_frame_duration_ms"], loop=0, disposal=2)
    for image in images:
        image.close()
    print(path, flush=True)
    return path
