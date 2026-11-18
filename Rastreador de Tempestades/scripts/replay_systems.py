"""Rebuild vector products from detections, preserving existing observed radar PNGs."""
import argparse
from dataclasses import replace
from datetime import datetime
import json
import hashlib
from pathlib import Path
import sys
from types import SimpleNamespace

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
    parser.add_argument("--skip-calibration", action="store_true",
                        help="Atualiza e valida produtos sem repetir a comparação de parâmetros.")
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
        if signature["thresholds"][1] != config.storm_threshold:
            frame = read_geotiff(source_path, config.nodata_value)
            cached["storms"] = detect_support_areas(frame, cached["cores"], config)
            signature["thresholds"][1] = config.storm_threshold
            cached["fingerprint"] = hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()
            write_json(path, cached)
        result = tracker.update(StormDetection(cached["cores"], cached["storms"]),
                                datetime.fromisoformat(cached["timestamp"]))
        frame = SimpleNamespace(frame_name=cached["frame_name"], timestamp=datetime.fromisoformat(cached["timestamp"]))
        entry["products"] = export_products(frame, result, config, args.output_root, render_png=False)
        entry["tracking"] = result.to_summary()
        entry["segmentation"] = {"total_cores": len(cached["cores"]), "total_storms": len(cached["storms"])}
        print(f"{cached['frame_name']}: {len(result.cores)} núcleos", flush=True)
    summary["config"] = config.to_dict()
    summary["processing_mode"] = "cores"
    summary["id_scope"] = {"core_id": "active_lifetime", "track_uid": "sequence_unique", "cell_id": "frame"}
    write_json(summary_path, summary)
    validate_outputs(args.output_root)
    if not args.skip_calibration:
        compare_overlaps(args.cache_dir, args.output_root, config)
    generate_gif(args.output_root)


if __name__ == "__main__":
    main()
