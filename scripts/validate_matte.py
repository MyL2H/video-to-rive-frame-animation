#!/usr/bin/env python3
"""Check frame integrity and report matte risks for visual comparison.

Color statistics and closed transparent regions are not visual ground truth:
clothing can match the background color and real limb gaps can be enclosed.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frames-dir", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--contact-sheet", type=Path, required=True)
    parser.add_argument(
        "--rgb-dir", type=Path,
        help="Optional original RGB PNGs with matching filenames for removal-risk diagnostics",
    )
    parser.add_argument("--background-min", type=int, default=230)
    parser.add_argument("--background-max", type=int, default=241)
    parser.add_argument(
        "--max-edge-opaque-pixels", type=int,
        help="Opt-in strict limit; border alpha is advisory by default",
    )
    parser.add_argument(
        "--max-hole-pixels", type=int,
        help="Opt-in strict limit; legitimate enclosed gaps are advisory by default",
    )
    parser.add_argument("--columns", type=int, default=3)
    parser.add_argument("--max-contact-frames", type=int, default=12)
    parser.add_argument("--thumbnail-height", type=int, default=192)
    return parser.parse_args()


def rgb_removal_metrics(rgb_path: Path, alpha: np.ndarray) -> dict:
    """Flag removed source contrast, including gaps connected to the border.

    A robust border color is a diagnostic estimate only. Shadows, compression,
    or nonuniform scenery may also differ from it; this does not classify
    individual pixels as foreground or justify automatic matte edits.
    """
    with Image.open(rgb_path) as source:
        rgb = np.asarray(source.convert("RGB"), dtype=np.float32)
    if rgb.shape[:2] != alpha.shape:
        raise ValueError(f"Original RGB/frame size mismatch: {rgb_path}")
    rim = np.concatenate((rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]), axis=0)
    background = np.median(rim, axis=0)
    rim_distance = np.max(np.abs(rim - background), axis=1)
    tolerance = max(12.0, float(np.percentile(rim_distance, 95)) + 4.0)
    source_distance = np.max(np.abs(rgb - background), axis=2)
    distinct_from_background = source_distance > tolerance
    removed = (alpha < 8) & distinct_from_background
    partially_removed = (alpha >= 8) & (alpha < 247) & distinct_from_background
    return {
        "rgbReference": str(rgb_path),
        "estimatedBorderRGB": np.rint(background).astype(int).tolist(),
        "backgroundDistanceThreshold": round(tolerance, 2),
        "removedNonBackgroundPixels": int(removed.sum()),
        "partiallyRemovedNonBackgroundPixels": int(partially_removed.sum()),
        "removedNonBackgroundPercent": round(float(removed.mean() * 100), 3),
        "interpretation": (
            "Risk only: removed pixels differ from the estimated border color. "
            "Inspect against RGB; shadows, compression and real transparent gaps "
            "can contribute. This is not a foreground label or a failure."
        ),
    }


def frame_metrics(
    path: Path, background_min: int, background_max: int,
    rgb_path: Path | None = None,
) -> dict:
    with Image.open(path) as image:
        mode = image.mode
        rgba = np.asarray(image.convert("RGBA"))
    alpha = rgba[:, :, 3]
    transparent = alpha < 8
    count, labels, stats, _ = cv2.connectedComponentsWithStats(
        transparent.astype(np.uint8), 8
    )
    height, width = alpha.shape
    hole_pixels = 0
    for label in range(1, count):
        x, y, w, h, area = stats[label]
        touches_border = x == 0 or y == 0 or x + w == width or y + h == height
        if not touches_border:
            hole_pixels += int(area)

    edge = np.r_[alpha[0], alpha[-1], alpha[:, 0], alpha[:, -1]]
    background_color = np.all(
        (rgba[:, :, :3] >= background_min)
        & (rgba[:, :, :3] <= background_max),
        axis=2,
    )
    result = {
        "frame": path.name,
        "size": [width, height],
        "mode": mode,
        "alphaRange": [int(alpha.min()), int(alpha.max())],
        "transparentPercent": round(float(transparent.mean() * 100), 2),
        "edgeOpaquePixels": int((edge >= 8).sum()),
        "enclosedHolePixels": hole_pixels,
        "opaqueBackgroundRangePixels": int((background_color & ~transparent).sum()),
    }
    if rgb_path is not None:
        result.update(rgb_removal_metrics(rgb_path, alpha))
    return result


def make_contact_sheet(
    files: list[Path], output: Path, columns: int,
    max_frames: int = 12, thumbnail_height: int = 192,
) -> dict:
    if columns <= 0 or max_frames <= 0 or thumbnail_height <= 0:
        raise ValueError("columns, max_frames and thumbnail_height must be positive")
    indices = sorted(set(
        round(i * (len(files) - 1) / max(min(max_frames, len(files)) - 1, 1))
        for i in range(min(max_frames, len(files)))
    ))
    samples = [files[index] for index in indices]
    with Image.open(files[0]) as first:
        scale = min(1.0, thumbnail_height / first.height)
        thumb_size = (max(1, round(first.width * scale)), max(1, round(first.height * scale)))
    backgrounds = [
        ("dark", (29, 29, 29)),
        ("light", (250, 250, 250)),
        ("color", (24, 150, 180)),
    ]
    label_height = 20
    cell_size = (thumb_size[0] * len(backgrounds), thumb_size[1] + label_height)
    columns = min(columns, len(samples))
    rows = (len(samples) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * cell_size[0], rows * cell_size[1]), (45, 45, 45))
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()
    for index, path in enumerate(samples):
        with Image.open(path) as source:
            rgba = source.convert("RGBA")
        x = (index % columns) * cell_size[0]
        y = (index // columns) * cell_size[1]
        for background_index, (label, color) in enumerate(backgrounds):
            # Composite at full resolution first, then thumbnail. This keeps
            # hidden RGB from polluting the diagnostic display.
            tile = Image.new("RGBA", rgba.size, color + (255,))
            tile.alpha_composite(rgba)
            tile = tile.convert("RGB").resize(thumb_size, Image.Resampling.LANCZOS)
            tile_x = x + background_index * thumb_size[0]
            sheet.paste(tile, (tile_x, y + label_height))
            draw.text(
                (tile_x + 3, y + 3), f"{path.stem} {label}",
                fill=(255, 255, 255), font=font,
            )
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output, quality=94)
    return {
        "path": str(output),
        "sampledFrames": [path.name for path in samples],
        "backgrounds": [dict(name=name, rgb=list(rgb)) for name, rgb in backgrounds],
        "size": list(sheet.size),
        "policy": "Uniformly sampled frames on dark, light and colored backgrounds",
    }


def sequence_errors(files: list[Path], frames_dir: Path) -> list[str]:
    """Check numbered frame continuity and any saved extraction frame count."""
    errors = []
    indices = sorted(
        int(match.group(1))
        for path in files
        if (match := re.fullmatch(r"frame_(\d+)\.png", path.name))
    )
    if indices and indices != list(range(len(indices))):
        errors.append(
            "Numbered frame sequence must start at 0 and be consecutive; "
            f"found indices {indices[:20]}{'...' if len(indices) > 20 else ''}"
        )

    manifest_path = frames_dir / "extraction_manifest.json"
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(manifest, dict):
                raise ValueError("expected a JSON object")
            expected_counts = {}
            if "outputFrameCount" in manifest:
                count = manifest["outputFrameCount"]
                if type(count) is not int or count <= 0:
                    raise ValueError("outputFrameCount must be a positive integer")
                expected_counts["outputFrameCount"] = count
            if "frameMap" in manifest:
                if not isinstance(manifest["frameMap"], list):
                    raise ValueError("frameMap must be a list")
                expected_counts["frameMap"] = len(manifest["frameMap"])
            for field, count in expected_counts.items():
                if len(files) != count:
                    errors.append(
                        f"Extraction manifest {field} expects {count} frames, "
                        f"found {len(files)}"
                    )
        except (OSError, ValueError) as error:
            errors.append(f"extraction_manifest.json: {error}")
    return errors


def main() -> None:
    args = parse_args()
    files = sorted(args.frames_dir.glob("frame_*.png"))
    if not files:
        files = sorted(args.frames_dir.glob("*.png"))
    if not files:
        raise ValueError(f"No PNG frame files found in {args.frames_dir}")
    if not 0 <= args.background_min <= args.background_max <= 255:
        raise ValueError("background range must be ordered within 0..255")
    for value in [args.max_edge_opaque_pixels, args.max_hole_pixels]:
        if value is not None and value < 0:
            raise ValueError("Explicit pixel limits must be nonnegative")
    frames = []
    technical_errors = sequence_errors(files, args.frames_dir)
    readable_files = []
    for path in files:
        try:
            frame = frame_metrics(
                path, args.background_min, args.background_max,
                args.rgb_dir / path.name if args.rgb_dir else None,
            )
        except (OSError, ValueError) as error:
            technical_errors.append(f"{path.name}: {error}")
            continue
        frames.append(frame)
        readable_files.append(path)
        if frame["mode"] != "RGBA":
            technical_errors.append(f"{path.name}: expected RGBA, got {frame['mode']}")
        if frame["alphaRange"][1] == 0:
            technical_errors.append(f"{path.name}: empty foreground")
        if frame["alphaRange"][0] == 255:
            technical_errors.append(f"{path.name}: no transparent background")
        if frame["size"] != frames[0]["size"]:
            technical_errors.append(f"{path.name}: inconsistent frame dimensions")
    result = {
        "framesDir": str(args.frames_dir),
        "frameCount": len(files),
        "inspectedFrameCount": len(frames),
        "backgroundRange": [args.background_min, args.background_max],
        "maximumEdgeOpaquePixels": max((item["edgeOpaquePixels"] for item in frames), default=0),
        "maximumEnclosedHolePixels": max(
            (item["enclosedHolePixels"] for item in frames), default=0
        ),
        "transparentPercentRange": [
            min((item["transparentPercent"] for item in frames), default=0),
            max((item["transparentPercent"] for item in frames), default=0),
        ],
        "technicalChecksPassed": not technical_errors,
        "technicalErrors": technical_errors,
        "diagnosticPolicy": (
            "Visual risks are advisory. Enclosed transparency may be a real limb "
            "gap. Opaque pixels within the configured RGB range may be clothing, "
            "skin or props, not background residue. Color counts and RGB removal "
            "comparisons are risk indicators, never visual ground truth."
        ),
        "frames": frames,
    }
    threshold_errors = []
    for metric, limit in [
        ("maximumEdgeOpaquePixels", args.max_edge_opaque_pixels),
        ("maximumEnclosedHolePixels", args.max_hole_pixels),
    ]:
        if limit is not None and result[metric] > limit:
            threshold_errors.append(f"{metric}={result[metric]} exceeds explicit limit {limit}")
    result["explicitThresholdChecksPassed"] = not threshold_errors
    result["explicitThresholdErrors"] = threshold_errors
    result["passed"] = result["technicalChecksPassed"] and not threshold_errors
    result["advisoryCounts"] = {
        "framesWithEnclosedTransparency": sum(item["enclosedHolePixels"] > 0 for item in frames),
        "framesWithBorderAlpha": sum(item["edgeOpaquePixels"] > 0 for item in frames),
        "framesWithOpaqueBackgroundRangeColor": sum(
            item["opaqueBackgroundRangePixels"] > 0 for item in frames
        ),
    }
    if args.rgb_dir is not None:
        result["rgbDir"] = str(args.rgb_dir)
        result["maximumRemovedNonBackgroundPixels"] = max(
            (item["removedNonBackgroundPixels"] for item in frames), default=0
        )
        result["maximumPartiallyRemovedNonBackgroundPixels"] = max(
            (item["partiallyRemovedNonBackgroundPixels"] for item in frames), default=0
        )
    if readable_files:
        result["contactSheet"] = make_contact_sheet(
            readable_files, args.contact_sheet, args.columns,
            args.max_contact_frames, args.thumbnail_height,
        )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "frames"}))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
