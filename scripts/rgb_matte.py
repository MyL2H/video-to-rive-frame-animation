#!/usr/bin/env python3
"""Matte flat neutral backdrops using color evidence and a model-assisted trimap.

This is a constrained cartoon/studio-background method, not a general matte for
complex scenes. Complete source RGB must be retained separately for audit.
"""

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


def estimate_background(rgb: np.ndarray) -> tuple[np.ndarray, dict]:
    """Fit a robust RGB plane to a thin border, allowing mild lighting gradients.

    A foreground touching part of the border is excluded by robust inlier fitting.
    Require a mostly flat, neutral border instead of silently applying gray rules
    to complex backgrounds. Never infer background from a foreground model alone.
    """
    h, w = rgb.shape[:2]
    yy, xx = np.mgrid[:h, :w].astype(np.float32)
    xn, yn = xx / max(w - 1, 1), yy / max(h - 1, 1)
    border_width = max(1, min(5, round(min(h, w) * .01)))
    border = (xx < border_width) | (xx >= w - border_width)
    border |= (yy < border_width) | (yy >= h - border_width)
    colors = rgb[border].astype(np.float32)
    design = np.stack([np.ones_like(xn[border]), xn[border], yn[border]], axis=1)
    center = np.median(colors, axis=0)
    inlier = np.max(np.abs(colors - center), axis=1) <= 14
    if float(inlier.mean()) < .7 or float(center.max() - center.min()) > 12:
        raise ValueError("Matte requires a mostly flat neutral border; choose a scene-specific matte for complex backgrounds")
    for _ in range(3):
        coefficients = np.linalg.lstsq(design[inlier], colors[inlier], rcond=None)[0]
        error = np.max(np.abs(colors - design @ coefficients), axis=1)
        inlier = error <= 10
    field = (coefficients[0] + xn[..., None] * coefficients[1]
             + yn[..., None] * coefficients[2]).astype(np.float32)
    residual = colors[inlier] - design[inlier] @ coefficients
    noise = float(np.percentile(np.max(np.abs(residual), axis=1), 99.5))
    chroma_residual = residual[:, [0, 2]] - residual[:, 1:2]
    chroma_noise = float(np.percentile(np.max(np.abs(chroma_residual), axis=1), 99.5))
    # The cap matters: noise is never a license to delete pale clothing.
    tolerance = float(np.clip(noise + 2, 3, 8))
    chroma_tolerance = float(np.clip(chroma_noise + 1.5, 2.5, 5))
    return field, {
        "backgroundEstimateRGB": [round(float(v), 3) for v in center],
        "backgroundBorderInlierFraction": round(float(inlier.mean()), 6),
        "backgroundNoiseP995": round(noise, 3),
        "backgroundColorTolerance": round(tolerance, 3),
        "backgroundChromaTolerance": round(chroma_tolerance, 3),
    }


def matte_rgb_frame(
    rgb: np.ndarray,
    rembg_rgba: np.ndarray,
    background_min: int = DEFAULT_BACKGROUND_MIN,
    background_max: int = DEFAULT_BACKGROUND_MAX,
    edge_pixels: int = 2,
) -> tuple[np.ndarray, dict]:
    """Preserve color-supported foreground; remove confirmed gray background.

    The model may expand background only within a bounded neutral-color band.
    Enclosed gaps need color AND model evidence; they are not blindly filled.
    No connected foreground component is discarded based on its size. Narrow
    silhouette edges are unmixed against the estimated backdrop to avoid a gray
    fringe when composited over dark colors. Thin features without a nearby
    reliable interior retain their RGB and opacity instead of being eroded.

    background_min/max remain accepted for API compatibility and are recorded as
    legacy hints. Color thresholds now adapt to measured border noise.
    """
    if rgb.ndim != 3 or rgb.shape[2] != 3 or rgb.dtype != np.uint8:
        raise ValueError("rgb must be uint8 with shape (height, width, 3)")
    if rembg_rgba.ndim != 3 or rembg_rgba.shape != (*rgb.shape[:2], 4):
        raise ValueError("rembg RGBA dimensions must match rgb")
    if edge_pixels < 0:
        raise ValueError("edge_pixels must be non-negative")
    background, background_info = estimate_background(rgb)
    pixels = rgb.astype(np.float32)
    delta = pixels - background
    distance = np.max(np.abs(delta), axis=2)
    chroma = np.max(np.abs(delta[:, :, [0, 2]] - delta[:, :, 1:2]), axis=2)
    tolerance = background_info["backgroundColorTolerance"]
    chroma_tolerance = background_info["backgroundChromaTolerance"]
    model_alpha = rembg_rgba[:, :, 3]
    color_match = (distance <= tolerance) & (chroma <= chroma_tolerance)
    relaxed_match = ((distance <= 24) & (chroma <= 16)
                     & ((pixels.max(axis=2) - pixels.min(axis=2)) <= 16))
    # Rembg cannot independently erase non-background-colored subject pixels.
    eligible_outer = color_match | (relaxed_match & (model_alpha < 96))
    outer = border_connected(eligible_outer)
    # A real enclosed hand/arm gap may be background. An uncertain pale patch
    # remains opaque: there is no universal fill-hole or remove-hole operation.
    enclosed_match = ((distance <= max(12, tolerance + 6))
                      & (chroma <= max(8, chroma_tolerance + 2))
                      & ((pixels.max(axis=2) - pixels.min(axis=2)) <= 12))
    enclosed = enclosed_match & ~outer & (model_alpha < 224)
    background_mask = outer | enclosed
    foreground = ~background_mask
    if not foreground.any():
        raise ValueError("No foreground subject was found")

    alpha = foreground.astype(np.float32)
    output_rgb = pixels.copy()
    edge_corrected = np.zeros(foreground.shape, bool)
    ringing_corrected = np.zeros(foreground.shape, bool)
    if edge_pixels:
        kernel = np.ones((3, 3), np.uint8)
        interior = cv2.erode(foreground.astype(np.uint8), kernel,
                             iterations=edge_pixels).astype(bool)
        if interior.any():
            distances, labels = cv2.distanceTransformWithLabels(
                (~interior).astype(np.uint8), cv2.DIST_L2, 5,
                labelType=cv2.DIST_LABEL_PIXEL)
            ref_colors = np.zeros((int(labels.max()) + 1, 3), np.float32)
            ref_colors[labels[interior]] = pixels[interior]
            reference = ref_colors[labels]
            vector = reference - background
            denom = np.sum(vector * vector, axis=2)
            estimate = np.clip(np.sum(delta * vector, axis=2)
                               / np.maximum(denom, 1), 0, 1)
            residual = np.max(np.abs(delta - estimate[..., None] * vector), axis=2)
            edge_zone = foreground & ~interior & (distances <= edge_pixels + 1.5)
            # A nearby interior with incompatible color must not erase a thin
            # feather, jewelry, or a different overlapping body part.
            edge_corrected = (edge_zone & (residual <= max(8, tolerance * 1.5))
                              & (denom > 100))
            alpha[edge_corrected] = estimate[edge_corrected]
            # Video chroma ringing may overshoot the background in one channel
            # (e.g. a white/red rim outside a red scarf). A two-channel agreement
            # avoids treating that impossible RGB blend as opaque foreground.
            # Only high-contrast references qualify, protecting pale thin props.
            ratios = delta / np.where(np.abs(vector) > 1, vector, 1)
            strong = np.abs(vector) >= 32
            pair_error = np.full(foreground.shape, np.inf, np.float32)
            pair_alpha = np.zeros(foreground.shape, np.float32)
            for first, second in ((0, 1), (0, 2), (1, 2)):
                error = np.abs(ratios[:, :, first] - ratios[:, :, second])
                eligible = strong[:, :, first] & strong[:, :, second]
                better = eligible & (error < pair_error)
                pair_error[better] = error[better]
                pair_alpha[better] = (ratios[:, :, first][better]
                                     + ratios[:, :, second][better]) * .5
            ringing_corrected = (edge_zone & ~edge_corrected
                                 & (model_alpha < 192) & (pair_error < .08)
                                 & (pair_alpha >= -.05) & (pair_alpha < .95))
            alpha[ringing_corrected] = np.clip(pair_alpha[ringing_corrected], 0, 1)
            output_rgb[ringing_corrected] = reference[ringing_corrected]
            partial = edge_corrected & (alpha < .995)
            output_rgb[partial] = np.clip(
                (pixels[partial] - (1 - alpha[partial, None]) * background[partial])
                / np.maximum(alpha[partial, None], .035), 0, 255)
    output_rgb[~foreground] = 0
    byte_alpha = np.rint(alpha * 255).astype(np.uint8)
    output_rgb[byte_alpha == 0] = 0
    rgba = np.dstack([np.rint(output_rgb).astype(np.uint8), byte_alpha])
    count, _, stats, _ = cv2.connectedComponentsWithStats(foreground.astype(np.uint8), 8)
    transparent = byte_alpha < 8
    holes = transparent & ~border_connected(transparent)
    protected = (model_alpha < 32) & ~relaxed_match & foreground
    return rgba, {
        **background_info,
        "mattingPolicy": "flat-neutral-color-model-trimap-v2",
        "backgroundMin": background_min,
        "backgroundMax": background_max,
        "candidatePixels": int(color_match.sum()),
        "outerBackgroundPixels": int(outer.sum()),
        "outerCombinedBackgroundPixels": int(background_mask.sum()),
        "enclosedBackgroundPixels": int(enclosed.sum()),
        "colorProtectedModelRejectPixels": int(protected.sum()),
        "keptForegroundComponents": int(count - 1),
        "largestForegroundPixels": int(stats[1:, cv2.CC_STAT_AREA].max()),
        "minimumKeptForegroundPixels": 0,
        "edgePixels": edge_pixels,
        "edgeUnmixedPixels": int(edge_corrected.sum()),
        "edgeChromaRingingCorrectedPixels": int(ringing_corrected.sum()),
        "holePixelsAfterMatte": int(holes.sum()),
        "holePolicy": "preserve color/model-confirmed background gaps; no unconditional filling",
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
            "complete RGB PNG first; robust flat-neutral border estimate; "
            "color-constrained model evidence; narrow edge unmixing"
        ),
        "backgroundRange": [background_min, background_max],
        "edgePixels": edge_pixels,
        "holePolicy": "color/model-confirmed background gaps remain transparent",
        "frames": reports,
    }
    (output / "matte_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return output / "matte_manifest.json"
