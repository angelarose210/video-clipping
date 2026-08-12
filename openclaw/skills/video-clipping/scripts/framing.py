"""Generate stable crop rectangles from per-frame subject tracks."""

from __future__ import annotations

import math
from typing import Any, Iterable

import numpy as np


def edge_safe_smooth(values: Iterable[float], window: int) -> np.ndarray:
    array = np.asarray(list(values), dtype=np.float64)
    if len(array) == 0 or window <= 1:
        return array
    window = max(1, min(int(window), len(array)))
    if window % 2 == 0:
        window += 1
    if window > len(array):
        window = len(array) if len(array) % 2 else max(1, len(array) - 1)
    if window <= 1:
        return array
    radius = window // 2
    padded = np.pad(array, (radius, radius), mode="edge")
    kernel = np.ones(window, dtype=np.float64) / window
    return np.convolve(padded, kernel, mode="valid")


def apply_dead_zone(values: Iterable[float], radius: float) -> np.ndarray:
    values = np.asarray(list(values), dtype=np.float64)
    if len(values) == 0 or radius <= 0:
        return values
    output = np.empty_like(values)
    output[0] = values[0]
    for index in range(1, len(values)):
        delta = values[index] - output[index - 1]
        if abs(delta) <= radius:
            output[index] = output[index - 1]
        else:
            output[index] = values[index] - math.copysign(radius, delta)
    return output


def limit_motion(values: Iterable[float], max_velocity: float, max_acceleration: float) -> np.ndarray:
    values = np.asarray(list(values), dtype=np.float64)
    if len(values) <= 1:
        return values
    output = np.empty_like(values)
    output[0] = values[0]
    velocity = 0.0
    for index in range(1, len(values)):
        desired_velocity = float(np.clip(values[index] - output[index - 1], -max_velocity, max_velocity))
        velocity += float(np.clip(desired_velocity - velocity, -max_acceleration, max_acceleration))
        velocity = float(np.clip(velocity, -max_velocity, max_velocity))
        output[index] = output[index - 1] + velocity
    return output


def interpolate_centers(records: list[dict[str, Any]], fallback: tuple[float, float]) -> tuple[np.ndarray, np.ndarray]:
    x = np.array([record.get("subjectCenter", [np.nan, np.nan])[0] for record in records], dtype=np.float64)
    y = np.array([record.get("subjectCenter", [np.nan, np.nan])[1] for record in records], dtype=np.float64)
    for values, default in ((x, fallback[0]), (y, fallback[1])):
        valid = np.flatnonzero(np.isfinite(values))
        if not len(valid):
            values[:] = default
        else:
            values[:] = np.interp(np.arange(len(values)), valid, values[valid])
    return x, y


def generate_shot_frames(
    records: list[dict[str, Any]],
    source_width: int,
    source_height: int,
    fps: float,
    output_width: int = 2160,
    smooth_seconds: float = 0.35,
    dead_zone_pixels: float = 45.0,
    max_pan_pixels_per_second: float = 1300.0,
    max_pan_acceleration_per_second2: float = 5200.0,
    zoom_enabled: bool = False,
    min_zoom: float = 1.0,
    max_zoom: float = 1.25,
    target_subject_height: float | None = None,
    output_height: int | None = None,
) -> list[dict[str, Any]]:
    if not records:
        return []
    output_height = output_width if output_height is None else output_height
    if output_width <= 0 or output_height <= 0:
        raise ValueError("output dimensions must be positive")
    target_aspect = output_width / output_height
    source_aspect = source_width / source_height
    if source_aspect >= target_aspect:
        native_crop_height = float(source_height)
        native_crop_width = native_crop_height * target_aspect
    else:
        native_crop_width = float(source_width)
        native_crop_height = native_crop_width / target_aspect
    center_x, center_y = interpolate_centers(records, (source_width / 2, source_height / 2))
    window = max(1, round(fps * smooth_seconds))
    center_x = edge_safe_smooth(center_x, window)
    center_y = edge_safe_smooth(center_y, window)
    center_x = apply_dead_zone(center_x, dead_zone_pixels)
    center_y = apply_dead_zone(center_y, dead_zone_pixels * 0.5)
    center_x = limit_motion(
        center_x,
        max_velocity=max_pan_pixels_per_second / fps,
        max_acceleration=max_pan_acceleration_per_second2 / (fps * fps),
    )
    center_y = limit_motion(
        center_y,
        max_velocity=max_pan_pixels_per_second / fps,
        max_acceleration=max_pan_acceleration_per_second2 / (fps * fps),
    )

    if min_zoom < 1.0:
        raise ValueError("min_zoom must be at least 1.0")
    if max_zoom < min_zoom:
        raise ValueError("max_zoom must be greater than or equal to min_zoom")

    zooms = np.full(len(records), min_zoom if zoom_enabled else 1.0, dtype=np.float64)
    if zoom_enabled:
        heights = np.array([
            max(1.0, float(box[3] - box[1])) if (box := record.get("subjectBox")) else np.nan
            for record in records
        ])
        if target_subject_height is not None:
            if target_subject_height <= 0:
                raise ValueError("target_subject_height must be positive")
            desired = np.where(
                np.isfinite(heights),
                np.clip(
                    target_subject_height / np.maximum(heights, 1.0),
                    min_zoom,
                    max_zoom,
                ),
                min_zoom,
            )
        else:
            valid = heights[np.isfinite(heights)]
            reference = float(np.median(valid)) if len(valid) else source_height * 0.45
            desired = np.where(
                np.isfinite(heights),
                np.clip(min_zoom * reference / np.maximum(heights, 1.0), min_zoom, max_zoom),
                min_zoom,
            )
        zooms = edge_safe_smooth(desired, max(3, round(fps * 1.0)))
        zooms = limit_motion(zooms, max_velocity=0.12 / fps, max_acceleration=0.5 / (fps * fps))
        zooms = np.clip(zooms, min_zoom, max_zoom)

    output: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        zoom = float(zooms[index])
        crop_width = native_crop_width / zoom
        crop_height = native_crop_height / zoom
        # Slight upward bias gives useful headroom while retaining feet/action.
        aim_y = center_y[index] - crop_height * 0.04
        x = float(np.clip(center_x[index] - crop_width / 2, 0, source_width - crop_width))
        y = float(np.clip(aim_y - crop_height / 2, 0, source_height - crop_height))
        output.append({
            "frame": int(record["frame"]),
            "shotId": record["shotId"],
            "crop": {
                "x": round(x, 3),
                "y": round(y, 3),
                "width": round(crop_width, 3),
                "height": round(crop_height, 3),
            },
            "zoom": round(zoom, 5),
            "subjectBox": record.get("subjectBox"),
            "confidence": round(float(record.get("confidence", 0.0)), 4),
            "flags": list(record.get("flags") or []),
        })
    return output
