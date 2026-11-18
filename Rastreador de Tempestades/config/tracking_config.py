from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class TrackingConfig:
    source_name: str = "rainviewer"
    nodata_value: int = -1
    input_interval_minutes: int = 20
    core_threshold: float = 40.0
    storm_threshold: float = 32.0
    core_min_area_km2: float = 40.0
    storm_min_area_km2: float = 60.0
    overlap_threshold: float = 0.10
    fallback_ambiguity_margin: float = 0.10
    max_centroid_speed_kmh: float = 180.0
    max_gap_minutes: float = 40.0
    motion_history_intervals: int = 2
    motion_recent_weight: float = 2.0
    motion_min_coherence: float = 0.85
    motion_min_displacement_km: float = 1.0
    use_support_motion: bool = True
    use_history_fallback: bool = True
    support_min_overlap: float = 0.50
    support_resolution_km: float = 2.0
    direction_mode: str = "centroid"
    support_direction_min_overlap: float = 0.65
    support_direction_min_peak_margin: float = 0.02
    association_min_area_ratio: float = 0.25
    association_max_intensity_delta: float = 15.0
    corridor_length_km: float = 20.0
    corridor_near_width_km: float = 10.0
    corridor_far_width_km: float = 20.0
    output_root: Path = Path("outputs")
    coverage_polygon: tuple = ((-63.0, -14.6), (-35.5, -14.6), (-35.5, -34.3),
                               (-63.0, -32.2), (-63.0, -14.6))
    gif_frame_duration_ms: int = 650

    def to_dict(self):
        return {key: str(value) if isinstance(value, Path) else value
                for key, value in asdict(self).items()}

    def __post_init__(self):
        points = self.coverage_polygon
        if len(points) < 4 or tuple(points[0]) != tuple(points[-1]):
            raise ValueError("coverage_polygon must be a closed ring with at least four points")
        if any(len(point) != 2 for point in points):
            raise ValueError("coverage_polygon points must be [longitude, latitude]")
        if self.direction_mode not in ("centroid", "support_checked"):
            raise ValueError("direction_mode must be centroid or support_checked")
        if not 0 < self.support_direction_min_overlap <= 1 or not 0 <= self.support_direction_min_peak_margin <= 1:
            raise ValueError("Invalid support direction quality gates")
        if not 0 < self.storm_threshold < self.core_threshold:
            raise ValueError("storm_threshold must be below core_threshold")
        if not 0 < self.overlap_threshold <= 1:
            raise ValueError("overlap_threshold must be in (0, 1]")
        if min(self.core_min_area_km2, self.storm_min_area_km2,
               self.max_gap_minutes, self.max_centroid_speed_kmh) <= 0:
            raise ValueError("Areas, gap and speed must be positive")
        if self.motion_history_intervals != 2:
            raise ValueError("Direction uses exactly t, t-1 and t-2 (two intervals)")
        if min(self.motion_recent_weight, self.support_resolution_km, self.corridor_length_km,
               self.corridor_near_width_km, self.corridor_far_width_km) <= 0:
            raise ValueError("Motion weight, resolution and corridor sizes must be positive")
        if not 0 <= self.motion_min_coherence <= 1 or not 0 < self.support_min_overlap <= 1:
            raise ValueError("Coherence and support overlap must be fractions")
        if not 0 < self.association_min_area_ratio <= 1 or self.association_max_intensity_delta < 0:
            raise ValueError("Invalid association gates")
        if self.motion_min_displacement_km < 0 or not 0 <= self.fallback_ambiguity_margin <= 1:
            raise ValueError("Invalid displacement or ambiguity margin")


DEFAULT_CONFIG = TrackingConfig()
