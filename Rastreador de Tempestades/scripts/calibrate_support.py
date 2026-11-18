"""Six support/speed trials; paired headings on identical causal core associations."""
import sys
sys.dont_write_bytecode = True
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from collections import Counter
from dataclasses import replace
from datetime import datetime
import csv
import argparse
import hashlib
import json
import math

from PIL import Image, ImageDraw, ImageFont
from config import DEFAULT_CONFIG
from src.core_tracking import GEOD, displacement, motion_from_history, trend_geometry
from src.geotiff_reader import read_geotiff
from src.storm_detection import detect_support_areas
from src.products import write_json
from system_tools import distribution, evaluate, rings


def heading_error(before, after, motion):
    if motion is None:
        return None
    minutes = (datetime.fromisoformat(after["history_lonlat"][-1]["timestamp"]) -
               datetime.fromisoformat(before["history_lonlat"][-1]["timestamp"])).total_seconds()/60
    east, north, distance = displacement(before["centroid_lonlat"], after["centroid_lonlat"])
    bearing = math.degrees(math.atan2(east, north)) % 360
    lon, lat, _ = GEOD.fwd(before["centroid_lonlat"]["lon"], before["centroid_lonlat"]["lat"],
                          motion["bearing_deg"], motion["speed_kmh"] * minutes / .06)
    return {"angle_deg": abs((bearing-motion["bearing_deg"]+180) % 360-180) if distance >= 1000 else None,
            "centroid_km": displacement({"lon": lon, "lat": lat}, after["centroid_lonlat"])[2]/1000}


def paired_headings(snapshots, cfg):
    observed, rows = {}, {}
    counts = Counter()
    for index, cores in enumerate(snapshots):
        for core in cores:
            prior = observed.get(core["track_uid"])
            if prior and not (core["lineage"]["split"] or core["lineage"]["merge"]) and len(prior["history_lonlat"]) >= 3:
                start_index = index - 1
                if len(prior["history_lonlat"]) >= 3:
                    key = tuple((p["timestamp"], p["lon"], p["lat"]) for p in prior["history_lonlat"][-3:])
                    key += ((core["history_lonlat"][-1]["timestamp"], core["centroid_lonlat"]["lon"],
                             core["centroid_lonlat"]["lat"]),)
                    row = {"index": start_index, "core_id": prior["core_id"], "track_uid": prior["track_uid"],
                           "cell_id": prior["cell_id"], "motions": {}, "errors": {}}
                    for mode in ("centroid", "support_checked"):
                        config = replace(cfg, direction_mode=mode)
                        motion = motion_from_history(prior["history_lonlat"], config, prior["motion_steps"])
                        visible = trend_geometry(prior, motion, config) is not None
                        counts[mode + "_eligible"] += 1
                        counts[mode + "_published"] += visible
                        if mode == "support_checked":
                            for step in prior["motion_steps"]:
                                counts[step["assessment"]] += 1
                        row["motions"][mode] = motion if visible else None
                        row["errors"][mode] = heading_error(prior, core, motion) if visible else None
                    rows[key] = row
            observed[core["track_uid"]] = core
    return dict(counts), rows


def summarize(rows):
    result = {}
    for mode in ("centroid", "support_checked"):
        errors = [r["errors"][mode] for r in rows if r["errors"][mode] is not None]
        result[mode] = {"next_direction_error_deg": distribution([e["angle_deg"] for e in errors if e["angle_deg"] is not None]),
                        "next_centroid_error_km": distribution([e["centroid_km"] for e in errors])}
    return result


def review_image(summary, snapshots, rows, output):
    paired = [r for r in rows.values() if all(r["errors"].values()) and
              all(r["errors"][m]["angle_deg"] is not None for m in r["errors"]) and
              any(s == "support_translation" for s in r["motions"]["support_checked"]["interval_sources"]) and
              any(c["cell_id"] == r["cell_id"] and -54 < c["centroid_lonlat"]["lon"] < -38 and
                  -34 < c["centroid_lonlat"]["lat"] < -17 for c in snapshots[r["index"]])]
    paired.sort(key=lambda r: r["errors"]["centroid"]["angle_deg"] - r["errors"]["support_checked"]["angle_deg"])
    cases = []
    if paired:
        cases = [("Maior melhora da direcao", paired[-1]["index"], paired[-1]["cell_id"], paired[-1]),
                 ("Maior piora da direcao", paired[0]["index"], paired[0]["cell_id"], paired[0])]
    for label, predicate in (("Divisao do nucleo", lambda c: c["lineage"]["split"]),
                             ("Desaparecimento observado", lambda c: True)):
        found = None
        for i in range(2, len(snapshots)-1):
            following = {c["track_uid"] for c in snapshots[i+1]}
            candidates = [c for c in snapshots[i] if predicate(c) and
                          (label != "Desaparecimento observado" or c["track_uid"] not in following) and
                          -54 < c["centroid_lonlat"]["lon"] < -38 and -34 < c["centroid_lonlat"]["lat"] < -17]
            if candidates:
                c = max(candidates, key=lambda c: c["area_km2"])
                found = (label, i, c["cell_id"], None)
                break
        if found:
            cases.append(found)
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/segoeui.ttf", 16)
    except OSError:
        font = ImageFont.load_default()
    canvas = Image.new("RGB", (1280, 100 + len(cases)*355), "#08111f")
    draw = ImageDraw.Draw(canvas)
    draw.text((18, 12), "Revisao de casos reais | laranja: centroide | ciano: apoio conferido", font=font, fill="white")
    draw.text((18, 40), "t+1 aparece apenas para avaliacao. Contornos brancos: nucleos >=40 dBZ; radar observado.", font=font, fill="#bbcddd")
    records = []
    for case_index, (label, i, cell, row) in enumerate(cases):
        core = next(c for c in snapshots[i] if c["cell_id"] == cell)
        center = core["centroid_lonlat"]
        extent = max(.7, math.sqrt(core["area_km2"])/80)
        west, east, south, north = center["lon"]-extent, center["lon"]+extent, center["lat"]-extent, center["lat"]+extent
        top = 90 + case_index*355
        draw.text((18, top), label + f" | ID {core['core_id']} | " +
                  summary["frames"][i]["frame"]["timestamp"], font=font, fill="white")
        record = {"case": label, "timestamp": summary["frames"][i]["frame"]["timestamp"],
                  "cell_id": cell, "track_uid": core["track_uid"], "core_id": core["core_id"]}
        if row:
            record.update(centroid_angle_deg=row["errors"]["centroid"]["angle_deg"],
                          support_angle_deg=row["errors"]["support_checked"]["angle_deg"])
        records.append(record)
        for col, k in enumerate(range(i-2, i+2)):
            folder = output / datetime.fromisoformat(summary["frames"][k]["frame"]["timestamp"]).strftime("%Y%m%d_%H%M")
            bounds = json.loads((folder/"manifest.json").read_text(encoding="utf-8"))["bounds"]
            (bs, bw), (bn, be) = bounds
            with Image.open(folder/"radar.png") as src:
                crop = src.crop(((west-bw)/(be-bw)*src.width, (bn-north)/(bn-bs)*src.height,
                                 (east-bw)/(be-bw)*src.width, (bn-south)/(bn-bs)*src.height)).resize((300, 300))
                panel = Image.new("RGBA", (300, 300), "#102035")
                panel.alpha_composite(crop.convert("RGBA"))
            pd = ImageDraw.Draw(panel)
            def project(point):
                return ((point[0]-west)/(east-west)*300, (north-point[1])/(north-south)*300)
            for c in snapshots[k]:
                for ring in rings(c["geometry"]):
                    pd.line([project(p) for p in ring], fill="white", width=1)
            if k == i and row:
                for mode, color in (("centroid", "#ffad45"), ("support_checked", "#5df4ff")):
                    motion = row["motions"][mode]
                    lon, lat, _ = GEOD.fwd(center["lon"], center["lat"], motion["bearing_deg"], 20000)
                    start, end = project((center["lon"], center["lat"])), project((lon, lat))
                    pd.line([start, end], fill=color, width=4)
                    pd.ellipse((end[0]-4, end[1]-4, end[0]+4, end[1]+4), fill=color)
            x = 18 + col*315
            canvas.paste(panel.convert("RGB"), (x, top+28))
            draw.text((x+5, top+32), ["t-2", "t-1", "t", "t+1"][col], font=font, fill="white")
    canvas.save(output/"support_review.png")
    with (output/"support_review.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=["case", "timestamp", "cell_id", "track_uid", "core_id",
                                                  "centroid_angle_deg", "support_angle_deg"])
        writer.writeheader()
        writer.writerows(records)
    return records


def main():
    parser = argparse.ArgumentParser(description="Calibra apoio e direção sobre núcleos independentes.")
    parser.add_argument("--review-only", action="store_true", help="Refaz recortes da referência 32 dBZ / 180 km/h usando o cache.")
    parser.add_argument("--record-selection", action="store_true", help="Registra a configuração publicada e validada no relatório.")
    args = parser.parse_args()
    output = ROOT/"outputs"
    summary = json.loads((output/"sequence_summary.json").read_text(encoding="utf-8"))
    if args.record_selection:
        path = output/"support_calibration.json"
        report = json.loads(path.read_text(encoding="utf-8"))
        validation = json.loads((output/"validation_report.json").read_text(encoding="utf-8"))
        if not validation["passed"] or validation["total_frames"] != summary["total_frames"]:
            raise ValueError("Selection needs validated published products")
        cfg = summary["config"]
        selected = next(e for e in report["experiments"] if
                        e["support_threshold_dbz"] == cfg["storm_threshold"] and e["max_speed_kmh"] == cfg["max_centroid_speed_kmh"])
        if selected["associations"]["distinct_track_uids"] != validation["distinct_track_uids"]:
            raise ValueError("Published identities differ from the selected experiment")
        original = json.loads((ROOT/"docs/cleanup_manifest.json").read_text(encoding="utf-8"))["preserved_inputs"]
        mismatches = [item["path"] for item in original if
                      hashlib.sha256((ROOT/item["path"]).read_bytes()).hexdigest() != item["sha256"]]
        if mismatches:
            raise ValueError(f"Original TIFFs changed: {mismatches}")
        report.update(selected_config=cfg, selected_experiment=selected["experiment"],
                      published_validation=validation, original_tiffs_verified=len(original),
                      direction_quality={"min_support_overlap": cfg["support_direction_min_overlap"],
                                         "min_peak_margin": cfg["support_direction_min_peak_margin"],
                                         "min_core_overlap": cfg["overlap_threshold"],
                                         "min_area_ratio": cfg["association_min_area_ratio"],
                                         "min_coherence": cfg["motion_min_coherence"],
                                         "recent_weight": cfg["motion_recent_weight"]})
        write_json(path, report)
        print("Recorded selected configuration, validated products and original TIFF hashes", flush=True)
        return
    cache = [json.loads((ROOT/".cache"/"detections"/(e["frame"]["frame_name"]+".json")).read_text(encoding="utf-8"))
             for e in summary["frames"]]
    if args.review_only:
        if any(item["signature"]["thresholds"] != [40, 32] for item in cache):
            raise ValueError("Review-only expects the 40/32 cache")
        cfg = replace(DEFAULT_CONFIG, storm_threshold=32, max_centroid_speed_kmh=180, direction_mode="centroid")
        if summary["config"]["storm_threshold"] == 32 and summary["config"]["max_centroid_speed_kmh"] == 180:
            snapshots = [json.loads((output/datetime.fromisoformat(e["frame"]["timestamp"]).strftime("%Y%m%d_%H%M")/
                                    "metadata.json").read_text(encoding="utf-8"))["cores"] for e in summary["frames"]]
        else:
            _, snapshots = evaluate(cache, cfg, {})
        _, rows = paired_headings(snapshots, cfg)
        cases = review_image(summary, snapshots, rows, output)
        path = output/"support_calibration.json"
        report = json.loads(path.read_text(encoding="utf-8"))
        report["review_cases"] = cases
        write_json(path, report)
        return
    datasets = {30: [], 32: [], 35: []}
    hashes = []
    for entry, item in zip(summary["frames"], cache):
        source = Path(entry["frame"]["file_path"])
        signature = item["signature"]
        if signature["size"] != source.stat().st_size or signature["mtime_ns"] != source.stat().st_mtime_ns:
            raise ValueError(f"Source changed: {source}")
        if signature["version"] != 3 or signature["thresholds"][0] != 40 or signature["areas"] != [40, 60]:
            raise ValueError("Calibration expects independent cores >=40 dBZ, areas 40/60 km2")
        frame = read_geotiff(source)
        cached_threshold = signature["thresholds"][1]
        rebuilt = detect_support_areas(frame, item["cores"], replace(DEFAULT_CONFIG, storm_threshold=cached_threshold))
        if json.dumps(rebuilt,
                      sort_keys=True) != json.dumps(item["storms"], sort_keys=True):
            raise ValueError("Rebuilt support differs from the signed cache")
        for threshold in (30, 32, 35):
            supports = item["storms"] if threshold == cached_threshold else detect_support_areas(
                frame, item["cores"], replace(DEFAULT_CONFIG, storm_threshold=threshold))
            datasets[threshold].append({**item, "storms": supports})
        hashes.append({"frame": source.name, "sha256": hashlib.sha256(source.read_bytes()).hexdigest()})
        print(f"Support masks 30/32/35: {source.stem}", flush=True)
    registrations, experiments, all_rows = {}, [], {}
    review_snapshots = None
    for threshold in (30, 32, 35):
        for speed in (120, 180):
            cfg = replace(DEFAULT_CONFIG, storm_threshold=threshold, max_centroid_speed_kmh=speed, direction_mode="centroid")
            metrics, snapshots = evaluate(datasets[threshold], cfg, registrations)
            counts, rows = paired_headings(snapshots, cfg)
            name = f"support_{threshold}_speed_{speed}"
            paired = [r for r in rows.values() if all(r["errors"].values())]
            experiments.append({"experiment": name, "support_threshold_dbz": threshold, "max_speed_kmh": speed,
                                "associations": metrics, "heading_counts": counts,
                                "headings_available": summarize(list(rows.values())),
                                "headings_common_within_configuration": summarize(paired)})
            all_rows[name] = rows
            if threshold == 32 and speed == 180:
                review_snapshots = snapshots
            print(f"{name}: {metrics['distinct_track_uids']} tracks; {counts}", flush=True)
    common = set.intersection(*(set(rows) for rows in all_rows.values()))
    common_published = {key for key in common if all(all(rows[key]["errors"].values()) for rows in all_rows.values())}
    for experiment in experiments:
        rows = all_rows[experiment["experiment"]]
        experiment["headings_common_across_all_six"] = summarize([rows[k] for k in common_published])
    cases = review_image(summary, review_snapshots, all_rows["support_32_speed_180"], output)
    report = {"frames": len(cache), "core_threshold_dbz": 40, "core_observations_unchanged": sum(len(i["cores"]) for i in cache),
              "cached_support_rebuild_exact": True, "common_four_observation_sequences": len(common),
              "common_published_sequences_all_modes_all_six": len(common_published),
              "experiments": experiments, "review_cases": cases, "source_hashes": hashes,
              "limits": ["Same 37-frame unlabeled sequence used for tuning and evaluation; no independent validation.",
                         "Next centroid is an evaluation proxy; growth can differ from physical transport.",
                         "Direction modes share identical associations; support/speed variants can change identities.",
                         "Common four-observation sequences match exact timestamps and centroid coordinates.",
                         "Published samples omit uncertain, conflicting and stationary headings; see eligible/published counts.",
                         "Review crops show real observed radar; t+1 is used only after prediction for evaluation."]}
    write_json(output/"support_calibration.json", report)
    print("Saved support_calibration.json and support_review.png/csv", flush=True)


if __name__ == "__main__":
    main()
