#!/usr/bin/env python3
"""Resize PNG frames and write optimized PNG and WebP derivatives."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from PIL import Image

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


def resize_rgba_premultiplied(
    source: Image.Image, size: tuple[int, int]
) -> Image.Image:
    """Resize color weighted by alpha; preserve intentional transparent gaps.

    RGB values under zero alpha must not bleed into a feathered outline. Use
    float channels to avoid quantizing very low coverage before resampling.
    This step changes resolution only: it must not repair or fill the matte.
    """
    rgba = np.asarray(source.convert("RGBA"), dtype=np.float32)
    alpha = rgba[:, :, 3] / 255.0
    premultiplied = rgba[:, :, :3] * alpha[:, :, None]

    def resize_channel(channel: np.ndarray) -> np.ndarray:
        return np.asarray(
            Image.fromarray(channel).resize(size, Image.Resampling.LANCZOS),
            dtype=np.float32,
        )

    resized_alpha = np.clip(resize_channel(alpha), 0.0, 1.0)
    resized_color = np.stack(
        [resize_channel(premultiplied[:, :, channel]) for channel in range(3)],
        axis=2,
    )
    # Lanczos can overshoot near an edge. Bound premultiplied channels by
    # coverage before unpremultiplication, without expanding alpha support.
    resized_color = np.clip(
        resized_color, 0.0, resized_alpha[:, :, None] * 255.0
    )
    straight_color = np.divide(
        resized_color,
        resized_alpha[:, :, None],
        out=np.zeros_like(resized_color),
        where=resized_alpha[:, :, None] > 1e-8,
    )
    result = np.empty((*resized_alpha.shape, 4), dtype=np.uint8)
    result[:, :, 3] = np.rint(resized_alpha * 255.0).astype(np.uint8)
    result[:, :, :3] = np.rint(straight_color).astype(np.uint8)
    result[result[:, :, 3] == 0, :3] = 0
    return Image.fromarray(result)


def main() -> None:
    args = parse_args()
    files = sorted(args.source.glob(args.pattern))
    if not files:
        raise ValueError(f"No files matched {args.pattern!r} in {args.source}")

    args.png_output.mkdir(parents=True, exist_ok=True)
    args.webp_output.mkdir(parents=True, exist_ok=True)

    first_size = Image.open(files[0]).size
    size = output_size(first_size, args)
    for source_path in files:
        with Image.open(source_path) as source_image:
            if source_image.size != first_size:
                raise ValueError(f"Frame size mismatch: {source_path}")
            frame = resize_rgba_premultiplied(source_image, size)

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
