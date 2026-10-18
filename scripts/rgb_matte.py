#!/usr/bin/env python3
"""Create a hole-free RGBA matte from an RGB frame and rembg alpha."""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image


DEFAULT_BACKGROUND_MIN = 230
DEFAULT_BACKGROUND_MAX = 241


def outer_background_mask(
    rgb: np.ndarray,
    background_min: int = DEFAULT_BACKGROUND_MIN,
    background_max: int = DEFAULT_BACKGROUND_MAX,
) -> tuple[np.ndarray, dict[str, int | float]]:
    """Return only background-colored components connected to an image border."""
    candidate = np.all(
        (rgb >= background_min) & (rgb <= background_max), axis=2
    ).astype(np.uint8)
    count, labels = cv2.connectedComponents(candidate, connectivity=8)
    border_labels = set(labels[0, :]) | set(labels[-1, :])
    border_labels |= set(labels[:, 0]) | set(labels[:, -1])
    border_labels.discard(0)
    outer = np.isin(labels, list(border_labels))
    return outer, {
        "candidatePixels": int(candidate.sum()),
        "outerBackgroundPixels": int(outer.sum()),
        "backgroundMin": background_min,
        "backgroundMax": background_max,
        "connectedComponents": int(count - 1),
    }


def fill_enclosed_transparent(alpha: np.ndarray, threshold: int = 8) -> np.ndarray:
    """Fill transparent components that do not touch the image border."""
    result = alpha.copy()
    transparent = (result < threshold).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(transparent, 8)
    height, width = result.shape
    for label in range(1, count):
        x, y, w, h, _ = stats[label]
        touches_border = x == 0 or y == 0 or x + w == width or y + h == height
        if not touches_border:
            result[labels == label] = 255
    return result


def border_connected(mask: np.ndarray) -> np.ndarray:
    """Return only true components that touch an image border."""
    values = mask.astype(np.uint8)
    count, labels = cv2.connectedComponents(values, connectivity=8)
    border_labels = set(labels[0, :]) | set(labels[-1, :])
    border_labels |= set(labels[:, 0]) | set(labels[:, -1])
    border_labels.discard(0)
    return np.isin(labels, list(border_labels))


def fill_enclosed_mask(mask: np.ndarray) -> np.ndarray:
    """Fill false components that do not touch an image border."""
    result = mask.astype(bool).copy()
    count, labels, stats, _ = cv2.connectedComponentsWithStats(
        (~result).astype(np.uint8), 8
    )
    height, width = result.shape
    for label in range(1, count):
        x, y, w, h, _ = stats[label]
        touches_border = x == 0 or y == 0 or x + w == width or y + h == height
        if not touches_border:
            result[labels == label] = True
    return result


def matte_rgb_frame(
    rgb: np.ndarray,
    rembg_rgba: np.ndarray,
    background_min: int = DEFAULT_BACKGROUND_MIN,
    background_max: int = DEFAULT_BACKGROUND_MAX,
    edge_pixels: int = 2,
) -> tuple[np.ndarray, dict[str, int | float]]:
    """Combine outer background evidence with rembg edge feathering.

    The saved RGB PNG remains complete. Only border-connected rembg background
    and border-connected pixels in the configured RGB range become transparent.
    This protects light or dark subject pixels that overlap either background
    signal and fills rembg holes enclosed by complete subject components.
    """
    if rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError("rgb must have shape (height, width, 3)")
    if rembg_rgba.shape[:2] != rgb.shape[:2]:
        raise ValueError("rembg alpha dimensions must match rgb")
    if edge_pixels < 0:
        raise ValueError("edge_pixels must be non-negative")

    outer_color, background_info = outer_background_mask(
        rgb, background_min, background_max
    )

    # rembg can make enclosed holes inside a light subject. Close tiny gaps and
    # fill those holes before using its mask as evidence for outside background.
    rembg_subject = rembg_rgba[:, :, 3] >= 128
    rembg_subject = cv2.morphologyEx(
        rembg_subject.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8)
    ).astype(bool)
    rembg_subject = fill_enclosed_mask(rembg_subject)
    outer_rembg = border_connected(~rembg_subject)

    outer = outer_color | outer_rembg
    foreground = (~outer).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(foreground, 8)
    if count <= 1:
        raise ValueError("No foreground subject was found")

    areas = stats[1:, cv2.CC_STAT_AREA]
    largest_area = int(areas.max())
    min_keep_area = max(64, round(largest_area * 0.002))
    keep_labels = [
        label
        for label, area in zip(range(1, count), areas)
        if int(area) >= min_keep_area
    ]
    if not keep_labels:
        raise ValueError("No stable foreground component was found")
    keep = np.isin(labels, keep_labels)

    alpha = np.zeros(rgb.shape[:2], dtype=np.uint8)
    alpha[keep] = 255
    if edge_pixels:
        kernel = np.ones((3, 3), dtype=np.uint8)
        near_outer = cv2.dilate(
            outer.astype(np.uint8), kernel, iterations=edge_pixels
        ).astype(bool)
        edge_zone = near_outer & keep
        alpha[edge_zone] = rembg_rgba[:, :, 3][edge_zone]
    alpha = fill_enclosed_transparent(alpha)
    rgba = np.dstack([rgb, alpha]).astype(np.uint8)

    transparent = (alpha < 8).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(transparent, 8)
    height, width = alpha.shape
    hole_pixels = 0
    for label in range(1, count):
        x, y, w, h, area = stats[label]
        touches_border = x == 0 or y == 0 or x + w == width or y + h == height
        if not touches_border:
            hole_pixels += int(area)

    return rgba, {
        **background_info,
        "outerRembgBackgroundPixels": int(outer_rembg.sum()),
        "outerCombinedBackgroundPixels": int(outer.sum()),
        "keptForegroundComponents": len(keep_labels),
        "largestForegroundPixels": largest_area,
        "minimumKeptForegroundPixels": min_keep_area,
        "edgePixels": edge_pixels,
        "holePixelsAfterMatte": hole_pixels,
    }


def matte_directory(
    source: Path,
    output: Path,
    background_min: int = DEFAULT_BACKGROUND_MIN,
    background_max: int = DEFAULT_BACKGROUND_MAX,
    edge_pixels: int = 2,
) -> Path:
    from rembg import new_session, remove

    files = sorted(source.glob("*.png"))
    if not files:
        raise ValueError(f"No PNG frames found in {source}")
    output.mkdir(parents=True, exist_ok=True)
    session = new_session("isnet-general-use", providers=["CPUExecutionProvider"])
    reports = []
    for path in files:
        with Image.open(path) as image:
            rgb = np.asarray(image.convert("RGB"))
        rembg_rgba = np.asarray(
            remove(rgb, session=session, alpha_matting=False)
        )
        rgba, report = matte_rgb_frame(
            rgb,
            rembg_rgba,
            background_min=background_min,
            background_max=background_max,
            edge_pixels=edge_pixels,
        )
        Image.fromarray(rgba).save(output / path.name, format="PNG", optimize=True)
        reports.append({"frame": path.name, **report})

    manifest = {
        "source": str(source),
        "output": str(output),
        "frameCount": len(files),
        "mattingPolicy": (
            "complete RGB PNG first; border-connected rembg background and RGB range; "
            "rembg edge feather"
        ),
        "backgroundRange": [background_min, background_max],
        "edgePixels": edge_pixels,
        "holePolicy": "fill enclosed transparent components",
        "frames": reports,
    }
    (output / "matte_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return output / "matte_manifest.json"
