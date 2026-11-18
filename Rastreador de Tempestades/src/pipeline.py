from pathlib import Path
import hashlib
import json

from config import TrackingConfig
from .geotiff_reader import read_geotiff
from .products import export_products, write_json
from .storm_detection import StormDetection, detect_storms
from .core_tracking import CoreTracker


class RadarTrackingPipeline:
    def __init__(self, config: TrackingConfig):
        self.config = config
        self.tracker = CoreTracker(config)

    def process_file(self, file_path, output_root=None, cache_dir=None):
        path = Path(file_path)
        frame = read_geotiff(path, self.config.nodata_value)
        config = self.config
        signature = {"version": 3, "size": path.stat().st_size, "mtime_ns": path.stat().st_mtime_ns,
                     "thresholds": [config.core_threshold, config.storm_threshold],
                     "areas": [config.core_min_area_km2, config.storm_min_area_km2],
                     "nodata": config.nodata_value}
        fingerprint = hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()
        cache_path = Path(cache_dir) / (path.stem + ".json") if cache_dir else None
        cached = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path and cache_path.exists() else None
        if cached and cached.get("fingerprint") == fingerprint:
            detection = StormDetection(cached["cores"], cached["storms"])
        else:
            detection = detect_storms(frame, config)
            if cache_path:
                write_json(cache_path, {"fingerprint": fingerprint, "signature": signature,
                           "frame_name": frame.frame_name, "timestamp": frame.timestamp.isoformat(),
                           "cores": detection.cores, "storms": detection.storms})
        result = self.tracker.update(detection, frame.timestamp)
        products = export_products(frame, result, config, output_root or config.output_root)
        return {"frame": frame.to_summary(), "segmentation": detection.to_summary(),
                "tracking": result.to_summary(), "products": products}

    def process_directory(self, input_dir, output_root=None, cache_dir=None):
        paths = sorted(Path(input_dir).glob("*.tif"))
        if not paths:
            raise ValueError(f"No TIFFs found in {input_dir}")
        results = []
        for index, path in enumerate(paths, 1):
            results.append(self.process_file(path, output_root, cache_dir))
            print(f"[{index}/{len(paths)}] {path.stem}: "
                  f"{results[-1]['tracking']['total_cores']} cores", flush=True)
        summary = {"processing_mode": "cores", "input_dir": str(Path(input_dir).resolve()),
                   "id_scope": {"core_id": "active_lifetime", "track_uid": "sequence_unique", "cell_id": "frame"},
                   "config": self.config.to_dict(), "total_frames": len(results), "frames": results,
                   "latest_frame": results[-1]["frame"]["frame_name"]}
        output = Path(output_root or self.config.output_root)
        write_json(output / "sequence_summary.json", summary)
        return summary
