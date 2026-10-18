#!/usr/bin/env python3
"""Resize PNG frames and write optimized PNG and WebP derivatives."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from PIL import Image

from rgb_matte import fill_enclosed_transparent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--png-output", type=Path, required=True)
    parser.add_argument("--webp-output", type=Path, required=True)
    parser.add_argument("--pattern", default="*.png")
    parser.add_argument("--scale", type=float, default=0.5)
    parser.add_argument("--width", type=int)
    parser.add_argument("--height", type=int)
    parser.add_argument("--webp-quality", type=int, default=82)
    parser.add_argument("--webp-method", type=int, default=6)
    return parser.parse_args()


def output_size(source_size: tuple[int, int], args: argparse.Namespace) -> tuple[int, int]:
    if (args.width is None) != (args.height is None):
        raise ValueError("--width and --height must be provided together")
    if args.width is not None:
        if args.width <= 0 or args.height <= 0:
            raise ValueError("Output dimensions must be positive")
        return args.width, args.height
    if args.scale <= 0:
        raise ValueError("--scale must be positive")
    width = round(source_size[0] * args.scale)
    height = round(source_size[1] * args.scale)
    return max(width, 1), max(height, 1)


def main() -> None:
    args = parse_args()
    files = sorted(args.source.glob(args.pattern))
    if not files:
        raise ValueError(f"No files matched {args.pattern!r} in {args.source}")

    args.png_output.mkdir(parents=True, exist_ok=True)
    args.webp_output.mkdir(parents=True, exist_ok=True)

    first_size = Image.open(files[0]).size
    size = output_size(first_size, args)
    resampling = getattr(Image, "Resampling", Image)

    for source_path in files:
        with Image.open(source_path) as source_image:
            if source_image.size != first_size:
                raise ValueError(f"Frame size mismatch: {source_path}")
            frame = source_image.convert("RGBA").resize(size, resampling.LANCZOS)
        rgba = np.asarray(frame).copy()
        rgba[:, :, 3] = fill_enclosed_transparent(rgba[:, :, 3])
        frame = Image.fromarray(rgba)

        frame.save(args.png_output / source_path.name, format="PNG", optimize=True)
        webp_path = args.webp_output / f"{source_path.stem}.webp"
        frame.save(
            webp_path,
            format="WEBP",
            quality=args.webp_quality,
            method=args.webp_method,
        )

    png_bytes = sum(path.stat().st_size for path in args.png_output.glob("*.png"))
    webp_bytes = sum(path.stat().st_size for path in args.webp_output.glob("*.webp"))
    print(f"frames={len(files)}")
    print(f"source_size={first_size[0]}x{first_size[1]}")
    print(f"output_size={size[0]}x{size[1]}")
    print(f"png_bytes={png_bytes}")
    print(f"webp_bytes={webp_bytes}")


if __name__ == "__main__":
    main()
