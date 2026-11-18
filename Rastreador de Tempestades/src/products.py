import json
from pathlib import Path

import numpy as np
from PIL import Image
from rasterio.transform import rowcol


def write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def render_radar(frame, config, path):
    left = max(config.render_extent_left, frame.bounds.left)
    right = min(config.render_extent_right, frame.bounds.right)
    top = min(config.render_extent_top, frame.bounds.top)
    bottom = max(config.render_extent_bottom, frame.bounds.bottom)
    if left >= right or bottom >= top:
        raise ValueError("Render extent does not intersect the input raster")
    row0, col0 = rowcol(frame.transform, left, top)
    row1, col1 = rowcol(frame.transform, right, bottom, op=np.ceil)
    row0, col0, row1, col1 = map(int, (row0, col0, row1, col1))
    data = frame.data[row0:row1, col0:col1]
    valid = frame.valid_mask[row0:row1, col0:col1] & (data > 5)
    palette = np.array([[30, 85, 190], [30, 180, 240], [50, 200, 95],
                        [225, 235, 40], [255, 135, 25], [235, 35, 55],
                        [180, 30, 165]], dtype=np.uint8)
    stops = np.linspace(5, 65, len(palette))
    rgba = np.zeros((*data.shape, 4), dtype=np.uint8)
    safe = np.where(valid, data, 0)
    for channel in range(3):
        rgba[..., channel] = np.interp(safe, stops, palette[:, channel]).astype(np.uint8)
    rgba[..., 3] = valid.astype(np.uint8) * 255
    Image.fromarray(rgba).save(path)
    # Bounds follow the actual crop edges so PNG and vectors align.
    west, north = frame.transform * (col0, row0)
    east, south = frame.transform * (col1, row1)
    return [[south, west], [north, east]]


def collection(items, frame, kind):
    features = []
    for item in items:
        properties = {key: value for key, value in item.items()
                      if key not in ("geometry", "trend", "history_lonlat")}
        properties.update(frame_name=frame.frame_name, timestamp=frame.timestamp.isoformat(), layer=kind)
        features.append({"type": "Feature", "geometry": item["geometry"], "properties": properties})
    return {"type": "FeatureCollection", "features": features}


def export_products(frame, result, config, output_root, render_png=True):
    folder = Path(output_root) / frame.timestamp.strftime("%Y%m%d_%H%M")
    folder.mkdir(parents=True, exist_ok=True)
    bounds = (render_radar(frame, config, folder / "radar.png") if render_png else
              json.loads((folder / "manifest.json").read_text(encoding="utf-8"))["bounds"])
    write_json(folder / "cells.geojson", collection(result.cores, frame, "cores"))
    write_json(folder / "support.geojson", collection(result.support_areas, frame, "motion_support"))
    trends = []
    tracks = []
    for core in result.cores:
        ids = {"core_id": core["core_id"], "track_uid": core["track_uid"]}
        if core["trend"]:
            trend = core["trend"]
            trends.append({**trend, **ids, "motion": core["motion"]})
        history = core["history_lonlat"]
        coordinates = []
        for p in history:
            coordinate = [p["lon"], p["lat"]]
            if not coordinates or coordinates[-1] != coordinate:
                coordinates.append(coordinate)
        if len(coordinates) >= 2:
            tracks.append({**ids, "geometry": {"type": "LineString",
                           "coordinates": coordinates}})
    write_json(folder / "trends.geojson", collection(trends, frame, "trends"))
    write_json(folder / "tracks.geojson", collection(tracks, frame, "history"))
    write_json(folder / "metadata.json", {
        "processing_mode": "cores", "frame_name": frame.frame_name,
        "timestamp": frame.timestamp.isoformat(), "source": config.source_name,
        "id_scope": {"core_id": "active_lifetime", "track_uid": "sequence_unique", "cell_id": "frame"},
        "thresholds_dbz": {"core": config.core_threshold, "storm": config.storm_threshold},
        "summary": result.to_summary(), "support_areas": result.support_areas, "cores": result.cores,
    })
    files = {key: str(folder / name) for key, name in {
        "radar": "radar.png", "cells": "cells.geojson", "support": "support.geojson",
        "trends": "trends.geojson", "tracks": "tracks.geojson", "metadata": "metadata.json",
    }.items()}
    write_json(folder / "manifest.json", {
        "product": "intense_core_tracks", "processing_mode": "cores", "source": config.source_name,
        "frame_name": frame.frame_name, "timestamp": frame.timestamp.isoformat(),
        "crs": "EPSG:4326", "bounds": bounds,
        "layers": {key: {"type": "raster" if key == "radar" else "json" if key == "metadata" else "geojson",
                         "path": Path(value).name} for key, value in files.items()},
    })
    files["manifest"] = str(folder / "manifest.json")
    return {key: str(Path(folder.name) / Path(value).name) for key, value in files.items()}
