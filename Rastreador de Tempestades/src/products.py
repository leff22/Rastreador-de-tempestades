import copy
import json
from pathlib import Path

import numpy as np
from PIL import Image
from pyproj import Transformer
from rasterio.features import geometry_mask
from rasterio.transform import rowcol
from shapely.geometry import GeometryCollection, LineString, MultiLineString, MultiPolygon, Polygon, mapping, shape
from shapely.ops import unary_union


AREA_TRANSFORM = Transformer.from_crs(4326, 6933, always_xy=True).transform


def write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def coverage_geometry(config):
    polygon = Polygon(config.coverage_polygon)
    if not polygon.is_valid or polygon.is_empty or polygon.area <= 0:
        raise ValueError("Configured coverage polygon must be valid and non-empty")
    return polygon


def _polygonal(geometry):
    if geometry.is_empty:
        return GeometryCollection()
    if geometry.geom_type in ("Polygon", "MultiPolygon"):
        return geometry
    parts = []
    if hasattr(geometry, "geoms"):
        for part in geometry.geoms:
            clipped = _polygonal(part)
            if not clipped.is_empty:
                parts.append(clipped)
    return unary_union(parts) if parts else GeometryCollection()


def _linear(geometry):
    if geometry.is_empty:
        return GeometryCollection()
    if geometry.geom_type in ("LineString", "MultiLineString"):
        return geometry
    parts = []
    if hasattr(geometry, "geoms"):
        for part in geometry.geoms:
            clipped = _linear(part)
            if not clipped.is_empty:
                parts.extend(list(clipped.geoms) if clipped.geom_type == "MultiLineString" else [clipped])
    return MultiLineString(parts) if parts else GeometryCollection()


def _area_km2(geometry):
    return float(__import__("shapely").transform(geometry, AREA_TRANSFORM, interleaved=False).area / 1e6)


def _clip_feature(item, region, kind):
    clipped = _polygonal(shape(item["geometry"]).intersection(region))
    if clipped.is_empty or clipped.area <= 0:
        return None
    output = copy.deepcopy(item)
    output["geometry"] = mapping(clipped)
    output["area_km2"] = _area_km2(clipped)
    output.pop("area_px", None)
    if kind == "core":
        center = item.get("centroid_lonlat")
        if not center or not region.covers(__import__("shapely").Point(center["lon"], center["lat"])):
            centroid = clipped.centroid
            output["centroid_lonlat"] = {"lon": float(centroid.x), "lat": float(centroid.y)}
            output["centroid_source"] = "clipped_geometry"
        else:
            output["centroid_source"] = "observed"
        output["history_lonlat"] = [
            point for point in item.get("history_lonlat", [])
            if region.covers(__import__("shapely").Point(point["lon"], point["lat"]))
        ]
        trend = item.get("trend")
        if trend:
            trend = copy.deepcopy(trend)
            trend_geometry = _polygonal(shape(trend["geometry"]).intersection(region))
            axis = _linear(shape(trend["axis"]).intersection(region))
            if trend_geometry.is_empty:
                output["trend"] = None
            else:
                trend["geometry"] = mapping(trend_geometry)
                trend["axis"] = mapping(axis) if not axis.is_empty else None
                output["trend"] = trend
    return output


def _clip_track(history, region):
    coordinates = [[point["lon"], point["lat"]] for point in history]
    deduplicated = []
    for coordinate in coordinates:
        if not deduplicated or coordinate != deduplicated[-1]:
            deduplicated.append(coordinate)
    if len(deduplicated) < 2:
        return None
    clipped = _linear(LineString(deduplicated).intersection(region))
    return mapping(clipped) if not clipped.is_empty and clipped.length > 0 else None


def render_radar(frame, config, path):
    region = coverage_geometry(config)
    minx, miny, maxx, maxy = region.bounds
    left = max(minx, frame.bounds.left)
    right = min(maxx, frame.bounds.right)
    top = min(maxy, frame.bounds.top)
    bottom = max(miny, frame.bounds.bottom)
    if left >= right or bottom >= top:
        raise ValueError("Coverage polygon does not intersect the input raster")
    row0, col0 = rowcol(frame.transform, left, top)
    row1, col1 = rowcol(frame.transform, right, bottom, op=np.ceil)
    row0, col0 = max(0, int(row0)), max(0, int(col0))
    row1, col1 = min(frame.height, int(row1)), min(frame.width, int(col1))
    if row0 >= row1 or col0 >= col1:
        raise ValueError("Coverage polygon produces an empty raster window")
    data = frame.data[row0:row1, col0:col1]
    crop_transform = frame.transform * frame.transform.translation(col0, row0)
    inside = geometry_mask([mapping(region)], out_shape=data.shape, transform=crop_transform,
                           invert=True, all_touched=False)
    valid = frame.valid_mask[row0:row1, col0:col1] & (data > 5) & inside
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
    west, north = crop_transform * (0, 0)
    east, south = crop_transform * (col1 - col0, row1 - row0)
    values = data[valid]
    return {
        "bounds": [[float(south), float(west)], [float(north), float(east)]],
        "width": int(data.shape[1]), "height": int(data.shape[0]),
        "valid_pixels": int(valid.sum()),
        "min_value": float(values.min()) if values.size else None,
        "max_value": float(values.max()) if values.size else None,
    }


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
    region = coverage_geometry(config)
    if render_png:
        raster = render_radar(frame, config, folder / "radar.png")
    else:
        old_manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        if old_manifest.get("coverage_polygon") != mapping(region):
            raise ValueError("Existing radar image does not match the configured coverage polygon")
        raster = old_manifest["raster"]
    published_cores = []
    for core in result.cores:
        clipped = _clip_feature(core, region, "core")
        if clipped is not None:
            published_cores.append(clipped)
    published_cell_ids = {core["cell_id"] for core in published_cores}
    published_support = []
    for support in result.support_areas:
        clipped = _clip_feature(support, region, "support")
        if clipped is not None:
            clipped["cell_ids"] = [cell_id for cell_id in support["cell_ids"] if cell_id in published_cell_ids]
            if clipped["cell_ids"]:
                published_support.append(clipped)
    support_ids = {item["detection_id"] for item in published_support}
    for core in published_cores:
        if core.get("support_detection_id") not in support_ids:
            core["support_detection_id"] = None
    trends = []
    tracks = []
    for original in result.cores:
        if original["cell_id"] not in published_cell_ids:
            # A track may still cross the coverage even when its current core does not.
            track_geometry = _clip_track(original.get("history_lonlat", []), region)
            if track_geometry:
                tracks.append({"core_id": original["core_id"], "track_uid": original["track_uid"],
                               "geometry": track_geometry})
            continue
        core = next(item for item in published_cores if item["cell_id"] == original["cell_id"])
        ids = {"core_id": core["core_id"], "track_uid": core["track_uid"]}
        if core.get("trend"):
            trends.append({**core["trend"], **ids, "motion": core["motion"]})
        track_geometry = _clip_track(original.get("history_lonlat", []), region)
        if track_geometry:
            tracks.append({**ids, "geometry": track_geometry})
    cells_payload = collection(published_cores, frame, "cores")
    support_payload = collection(published_support, frame, "motion_support")
    trends_payload = collection(trends, frame, "trends")
    tracks_payload = collection(tracks, frame, "history")
    write_json(folder / "cells.geojson", cells_payload)
    write_json(folder / "support.geojson", support_payload)
    write_json(folder / "trends.geojson", trends_payload)
    write_json(folder / "tracks.geojson", tracks_payload)
    summary = result.to_summary()
    summary["total_cores"] = len(published_cores)
    summary["total_support_areas"] = len(published_support)
    summary["published_trends"] = len(trends)
    summary["published_tracks"] = len(tracks)
    write_json(folder / "metadata.json", {
        "processing_mode": "cores", "frame_name": frame.frame_name,
        "timestamp": frame.timestamp.isoformat(), "source": config.source_name,
        "crs": "EPSG:4326", "coverage_polygon": mapping(region),
        "coverage_type": "polygon", "coverage_bounds": raster["bounds"],
        "id_scope": {"core_id": "active_lifetime", "track_uid": "sequence_unique", "cell_id": "frame"},
        "thresholds_dbz": {"core": config.core_threshold, "storm": config.storm_threshold},
        "summary": summary, "support_areas": published_support, "cores": published_cores,
    })
    files = {key: str(folder / name) for key, name in {
        "radar": "radar.png", "cells": "cells.geojson", "support": "support.geojson",
        "trends": "trends.geojson", "tracks": "tracks.geojson", "metadata": "metadata.json",
    }.items()}
    counts = {"cells": len(cells_payload["features"]), "support": len(support_payload["features"]),
              "trends": len(trends_payload["features"]), "tracks": len(tracks_payload["features"])}
    write_json(folder / "manifest.json", {
        "product": "intense_core_tracks", "processing_mode": "cores", "source": config.source_name,
        "frame_name": frame.frame_name, "timestamp": frame.timestamp.isoformat(),
        "crs": "EPSG:4326", "coverage_type": "polygon", "coverage_polygon": mapping(region),
        "bounds": raster["bounds"], "raster": raster,
        "layers": {key: {"type": "raster" if key == "radar" else "json" if key == "metadata" else "geojson",
                         "path": Path(value).name,
                         **({"feature_count": counts[key]} if key in counts else {})}
                   for key, value in files.items()},
    })
    files["manifest"] = str(folder / "manifest.json")
    return {key: str(Path(folder.name) / Path(value).name) for key, value in files.items()}
