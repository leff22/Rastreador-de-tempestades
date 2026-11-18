"""Rebuild and validate polygon-clipped products from original cached detections."""
import argparse
from dataclasses import replace
from datetime import datetime
import json
import hashlib
from pathlib import Path
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from config import DEFAULT_CONFIG
from src.products import export_products, write_json
from src.storm_detection import StormDetection, detect_support_areas
from src.geotiff_reader import read_geotiff
from src.core_tracking import CoreTracker
from system_tools import validate_outputs, compare_overlaps, generate_gif


def main():
    parser = argparse.ArgumentParser(description="Reassocia detecções em cache e atualiza os vetores.")
    parser.add_argument("--output-root", type=Path, default=ROOT / "outputs")
    parser.add_argument("--cache-dir", type=Path, default=ROOT / ".cache" / "detections")
    parser.add_argument("--overlap", type=float, default=DEFAULT_CONFIG.overlap_threshold)
    parser.add_argument("--storm-threshold", type=float, default=DEFAULT_CONFIG.storm_threshold)
    parser.add_argument("--max-speed", type=float, default=DEFAULT_CONFIG.max_centroid_speed_kmh)
    parser.add_argument("--direction-mode", choices=("centroid", "support_checked"), default=DEFAULT_CONFIG.direction_mode)
    parser.add_argument("--calibrate", action="store_true",
                        help="Executa a comparação experimental de parâmetros após o replay.")
    args = parser.parse_args()
    summary_path = args.output_root / "sequence_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    config = replace(DEFAULT_CONFIG, output_root=args.output_root, overlap_threshold=args.overlap,
                     storm_threshold=args.storm_threshold, max_centroid_speed_kmh=args.max_speed,
                     direction_mode=args.direction_mode)
    tracker = CoreTracker(config)
    for entry in summary["frames"]:
        path = args.cache_dir / (entry["frame"]["frame_name"] + ".json")
        cached = json.loads(path.read_text(encoding="utf-8"))
        signature = cached["signature"]
        source_path = Path(entry["frame"]["file_path"])
        if signature["size"] != source_path.stat().st_size or signature["mtime_ns"] != source_path.stat().st_mtime_ns:
            raise ValueError(f"Source changed: {source_path}")
        if (signature["thresholds"][0] != config.core_threshold or
                signature["areas"] != [config.core_min_area_km2, config.storm_min_area_km2]):
            raise ValueError(f"Segmentation configuration changed: {path}")
        if signature["version"] != 3:
            raise ValueError(f"Cache must use independent core detection (version 3); run process: {path}")
        frame = read_geotiff(source_path, config.nodata_value)
        if signature["thresholds"][1] != config.storm_threshold:
            cached["storms"] = detect_support_areas(frame, cached["cores"], config)
            signature["thresholds"][1] = config.storm_threshold
            cached["fingerprint"] = hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()
            write_json(path, cached)
        result = tracker.update(StormDetection(cached["cores"], cached["storms"]),
                                datetime.fromisoformat(cached["timestamp"]))
        entry["products"] = export_products(frame, result, config, args.output_root, render_png=True)
        folder = args.output_root / frame.timestamp.strftime("%Y%m%d_%H%M")
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        metadata = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
        raster = manifest["raster"]
        frame_summary = frame.to_summary()
        frame_summary.update(bounds={
            "left": raster["bounds"][0][1], "bottom": raster["bounds"][0][0],
            "right": raster["bounds"][1][1], "top": raster["bounds"][1][0]},
            width=raster["width"], height=raster["height"], valid_pixels=raster["valid_pixels"],
            min_value=raster["min_value"] if raster["min_value"] is not None else frame.nodata_value,
            max_value=raster["max_value"] if raster["max_value"] is not None else frame.nodata_value)
        entry["frame"] = frame_summary
        entry["tracking"] = result.to_summary()
        entry["tracking"]["total_cores"] = len(metadata["cores"])
        entry["tracking"]["total_support_areas"] = len(metadata["support_areas"])
        entry["tracking"]["new_cores"] = sum(core["association"]["method"] == "birth"
                                               for core in metadata["cores"])
        entry["segmentation"] = {"total_cores": len(metadata["cores"]),
                                 "total_storms": len(metadata["support_areas"])}
        print(f"{cached['frame_name']}: {len(result.cores)} núcleos", flush=True)
    summary["config"] = config.to_dict()
    summary["processing_mode"] = "cores"
    summary["id_scope"] = {"core_id": "active_lifetime", "track_uid": "sequence_unique", "cell_id": "frame"}
    write_json(summary_path, summary)
    validate_outputs(args.output_root)
    if args.calibrate:
        compare_overlaps(args.cache_dir, args.output_root, config)
    generate_gif(args.output_root)


if __name__ == "__main__":
    main()
