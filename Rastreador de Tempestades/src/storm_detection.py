"""Independent, nested reflectivity masks; no temporal or morphological bridges."""
from dataclasses import dataclass

import numpy as np
from pyproj import Transformer
from rasterio.features import shapes
from rasterio.transform import rowcol
from scipy import ndimage
from shapely.geometry import mapping, shape
from shapely import transform, set_precision
from shapely.ops import unary_union

from config import TrackingConfig
from .geotiff_reader import GeoTiffFrame


AREA_TRANSFORM = Transformer.from_crs(4326, 6933, always_xy=True).transform


@dataclass
class StormDetection:
    cores: list[dict]
    storms: list[dict]

    def to_summary(self):
        return {"total_cores": len(self.cores), "total_storms": len(self.storms)}


def polygon_area(geometry):
    return transform(shape(geometry), AREA_TRANSFORM, interleaved=False).area / 1e6


def canonical_component(component):
    """Sub-millimetre shared precision removes arithmetic seams at local crop edges."""
    geometry = set_precision(shape(component["geometry"]), grid_size=1e-9)
    component["geometry"] = mapping(geometry)
    component["area_km2"] = polygon_area(component["geometry"])
    component["centroid_lonlat"] = {"lon": geometry.centroid.x, "lat": geometry.centroid.y}
    return component


def _component(frame, labels, component_slice, label_id):
    local = labels[component_slice] == label_id
    row0, col0 = component_slice[0].start, component_slice[1].start
    local_transform = frame.transform * frame.transform.translation(col0, row0)
    polygons = [shape(geometry) for geometry, value in shapes(
        local.astype(np.uint8), mask=local, transform=local_transform) if value]
    geometry = mapping(unary_union(polygons))
    area = polygon_area(geometry)
    center = shape(geometry).centroid
    values = frame.data[component_slice][local]
    return canonical_component({
        "geometry": geometry, "area_km2": area, "area_px": int(local.sum()),
        "centroid_lonlat": {"lon": center.x, "lat": center.y},
        "intensity_max": float(values.max()), "intensity_mean": float(values.mean()),
    })


def detect_storms(frame: GeoTiffFrame, config: TrackingConfig) -> StormDetection:
    if frame.crs is None or frame.crs.to_epsg() != 4326:
        raise ValueError("Inputs must use EPSG:4326; reproject other rasters before processing")
    outer_labels, _ = ndimage.label(frame.valid_mask & (frame.data >= config.storm_threshold))
    core_labels, _ = ndimage.label(frame.valid_mask & (frame.data >= config.core_threshold))
    cores = []
    grouped = {}
    # Equatorial upper bound for a geographic pixel; final area uses EPSG:6933.
    counts = np.bincount(core_labels.ravel())
    angular_area = abs(frame.transform.a * frame.transform.e - frame.transform.b * frame.transform.d)
    max_pixel_km2 = angular_area * 112.0 ** 2
    pixel_min = max(1, int(config.core_min_area_km2 / max_pixel_km2))
    for label_id, section in enumerate(ndimage.find_objects(core_labels), 1):
        if section is None or counts[label_id] < pixel_min:
            continue
        core = _component(frame, core_labels, section, label_id)
        if core["area_km2"] < config.core_min_area_km2:
            continue
        local = core_labels[section] == label_id
        outer_id = int(outer_labels[section][local][0])
        core["cell_id"] = len(cores) + 1
        core["id_scope"] = "frame"
        cores.append(core)
        grouped.setdefault(outer_id, []).append(core["cell_id"])
    storms = []
    sections = ndimage.find_objects(outer_labels)
    for outer_id, cell_ids in grouped.items():
        storm = _component(frame, outer_labels, sections[outer_id - 1], outer_id)
        if storm["area_km2"] < config.storm_min_area_km2:
            continue
        storm["detection_id"] = len(storms) + 1
        storm["cell_ids"] = cell_ids
        storms.append(storm)
    # Intense-core acceptance is independent of the optional displacement support.
    return StormDetection(cores, storms)


def detect_support_areas(frame, cores, config):
    """Recompute only support; core footprints and frame IDs remain exactly unchanged."""
    if frame.crs is None or frame.crs.to_epsg() != 4326:
        raise ValueError("Inputs must use EPSG:4326")
    labels, _ = ndimage.label(frame.valid_mask & (frame.data >= config.storm_threshold))
    grouped = {}
    for core in cores:
        point = shape(core["geometry"]).representative_point()
        row, col = rowcol(frame.transform, point.x, point.y)
        label = int(labels[row, col])
        if not label:
            raise ValueError("Cached core does not belong to the requested support mask")
        grouped.setdefault(label, []).append(core["cell_id"])
    sections = ndimage.find_objects(labels)
    supports = []
    for label, cell_ids in grouped.items():
        item = _component(frame, labels, sections[label - 1], label)
        if item["area_km2"] >= config.storm_min_area_km2:
            item.update(detection_id=len(supports) + 1, cell_ids=cell_ids)
            supports.append(item)
    return supports
