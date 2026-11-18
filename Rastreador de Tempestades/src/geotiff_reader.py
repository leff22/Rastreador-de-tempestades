from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional
import math
import re

import numpy as np
import rasterio
from rasterio.coords import BoundingBox
from rasterio.crs import CRS
from rasterio.transform import Affine, xy


TIMESTAMP_PATTERN = re.compile(r"(\d{8})_(\d{4})")


@dataclass(frozen=True)
class GeoTiffFrame:
    file_path: Path
    frame_name: str
    timestamp: Optional[datetime]
    data: np.ndarray
    valid_mask: np.ndarray
    nodata_value: float
    width: int
    height: int
    crs: Optional[CRS]
    bounds: BoundingBox
    transform: Affine
    resolution_x: float
    resolution_y: float
    pixel_area_km2: float
    min_value: float
    max_value: float

    def pixel_to_lonlat(self, row: int, col: int) -> tuple[float, float]:
        lon, lat = xy(self.transform, row, col, offset="center")
        return float(lon), float(lat)

    def to_summary(self) -> dict:
        return {
            "file_path": str(self.file_path),
            "frame_name": self.frame_name,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "width": self.width,
            "height": self.height,
            "crs": self.crs.to_string() if self.crs else None,
            "bounds": {
                "left": float(self.bounds.left),
                "bottom": float(self.bounds.bottom),
                "right": float(self.bounds.right),
                "top": float(self.bounds.top),
            },
            "resolution_x": float(self.resolution_x),
            "resolution_y": float(self.resolution_y),
            "pixel_area_km2": float(self.pixel_area_km2),
            "nodata_value": float(self.nodata_value),
            "valid_pixels": int(self.valid_mask.sum()),
            "min_value": float(self.min_value),
            "max_value": float(self.max_value),
        }


def read_geotiff(file_path: str | Path, nodata_value: float = -1) -> GeoTiffFrame:
    path = Path(file_path)

    with rasterio.open(path) as dataset:
        data = dataset.read(1).astype(np.float32)
        valid_mask = (dataset.read_masks(1) > 0) & np.isfinite(data) & (data != nodata_value)
        valid_values = data[valid_mask]

        if valid_values.size == 0:
            min_value = nodata_value
            max_value = nodata_value
        else:
            min_value = float(valid_values.min())
            max_value = float(valid_values.max())

        resolution_x, resolution_y = dataset.res
        pixel_area_km2 = _estimate_pixel_area_km2(dataset.bounds, dataset.width, dataset.height)

        return GeoTiffFrame(
            file_path=path,
            frame_name=path.stem,
            timestamp=_extract_timestamp(path.stem),
            data=data,
            valid_mask=valid_mask,
            nodata_value=float(nodata_value),
            width=dataset.width,
            height=dataset.height,
            crs=dataset.crs,
            bounds=dataset.bounds,
            transform=dataset.transform,
            resolution_x=float(resolution_x),
            resolution_y=float(resolution_y),
            pixel_area_km2=float(pixel_area_km2),
            min_value=min_value,
            max_value=max_value,
        )


def _extract_timestamp(frame_name: str) -> Optional[datetime]:
    match = TIMESTAMP_PATTERN.search(frame_name)
    if not match:
        return None

    return datetime.strptime(f"{match.group(1)}{match.group(2)}", "%Y%m%d%H%M")


def _estimate_pixel_area_km2(bounds: BoundingBox, width: int, height: int) -> float:
    center_lat = (float(bounds.top) + float(bounds.bottom)) / 2.0
    lat_extent = float(bounds.top) - float(bounds.bottom)
    lon_extent = float(bounds.right) - float(bounds.left)

    pixel_height_deg = abs(lat_extent / height)
    pixel_width_deg = abs(lon_extent / width)

    km_per_degree_lat = 111.32
    km_per_degree_lon = 111.32 * math.cos(math.radians(center_lat))

    pixel_height_km = pixel_height_deg * km_per_degree_lat
    pixel_width_km = pixel_width_deg * km_per_degree_lon

    return pixel_height_km * pixel_width_km
