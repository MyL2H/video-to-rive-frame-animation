#!/usr/bin/env python3
"""Validate transparent frames and build a contact sheet for visual review."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frames-dir", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--contact-sheet", type=Path, required=True)
    parser.add_argument("--background-min", type=int, default=232)
    parser.add_argument("--background-max", type=int, default=238)
    parser.add_argument("--max-edge-opaque-pixels", type=int, default=0)
    parser.add_argument("--max-hole-pixels", type=int, default=0)
    parser.add_argument("--columns", type=int, default=6)
    return parser.parse_args()


def frame_metrics(path: Path, background_min: int, background_max: int) -> dict:
    with Image.open(path) as image:
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
    return {
        "frame": path.name,
        "size": [width, height],
        "transparentPercent": round(float(transparent.mean() * 100), 2),
        "edgeOpaquePixels": int((edge >= 8).sum()),
        "enclosedHolePixels": hole_pixels,
        "opaqueBackgroundRangePixels": int((background_color & ~transparent).sum()),
    }


def make_contact_sheet(files: list[Path], output: Path, columns: int) -> None:
    if columns <= 0:
        raise ValueError("columns must be positive")
    with Image.open(files[0]) as first:
        cell_size = first.size
    rows = (len(files) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * cell_size[0], rows * cell_size[1]))
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()
    for index, path in enumerate(files):
        with Image.open(path) as source:
            rgba = source.convert("RGBA")
            tile = Image.new("RGBA", rgba.size, (255, 0, 255, 255))
            tile.alpha_composite(rgba)
        x = (index % columns) * cell_size[0]
        y = (index // columns) * cell_size[1]
        sheet.paste(tile.convert("RGB"), (x, y))
        draw.rectangle((x, y, x + 46, y + 14), fill=(0, 0, 0))
        draw.text((x + 3, y + 2), path.stem, fill=(255, 255, 255), font=font)
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output, quality=94)


def main() -> None:
    args = parse_args()
    files = sorted(args.frames_dir.glob("frame_*.png"))
    if not files:
        raise ValueError(f"No frame_*.png files found in {args.frames_dir}")
    frames = [
        frame_metrics(path, args.background_min, args.background_max)
        for path in files
    ]
    result = {
        "framesDir": str(args.frames_dir),
        "frameCount": len(frames),
        "backgroundRange": [args.background_min, args.background_max],
        "maximumEdgeOpaquePixels": max(item["edgeOpaquePixels"] for item in frames),
        "maximumEnclosedHolePixels": max(
            item["enclosedHolePixels"] for item in frames
        ),
        "transparentPercentRange": [
            min(item["transparentPercent"] for item in frames),
            max(item["transparentPercent"] for item in frames),
        ],
        "frames": frames,
    }
    result["passed"] = (
        result["maximumEdgeOpaquePixels"] <= args.max_edge_opaque_pixels
        and result["maximumEnclosedHolePixels"] <= args.max_hole_pixels
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    make_contact_sheet(files, args.contact_sheet, args.columns)
    print(json.dumps({key: value for key, value in result.items() if key != "frames"}))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
