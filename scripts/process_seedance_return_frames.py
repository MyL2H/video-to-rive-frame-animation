#!/usr/bin/env python3
"""Extract, endpoint-align, matte, and optimize a generated return clip."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image
from rembg import new_session, remove


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--first-frame", type=Path, required=True)
    parser.add_argument("--last-frame", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--blend-frames", type=int, default=3)
    parser.add_argument("--scale", type=float, default=0.5)
    parser.add_argument("--webp-quality", type=int, default=82)
    parser.add_argument("--webp-method", type=int, default=6)
    parser.add_argument("--model", default="isnet-general-use")
    parser.add_argument("--provider", default="CPUExecutionProvider")
    parser.add_argument("--replace", action="store_true")
    return parser.parse_args()


def load_rgb(path: Path) -> np.ndarray:
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Could not read image: {path}")
    return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)


def read_video(path: Path) -> tuple[list[np.ndarray], float]:
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise ValueError(f"OpenCV could not open video: {path}")
    fps = float(capture.get(cv2.CAP_PROP_FPS))
    frames = []
    while True:
        ok, frame_bgr = capture.read()
        if not ok:
            break
        frames.append(cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB))
    capture.release()
    if not frames:
        raise ValueError(f"No frames decoded from {path}")
    return frames, fps


def blend(a: np.ndarray, b: np.ndarray, amount: float) -> np.ndarray:
    return cv2.addWeighted(a, 1 - amount, b, amount, 0)


def align_endpoints(
    generated: list[np.ndarray],
    first: np.ndarray,
    last: np.ndarray,
    blend_frames: int,
) -> list[np.ndarray]:
    if len(generated) < 4:
        raise ValueError("Generated clip must contain at least four frames")
    if first.shape != last.shape:
        raise ValueError("Endpoint frames must have identical dimensions")
    if blend_frames < 0:
        raise ValueError("--blend-frames must be non-negative")

    size = (first.shape[1], first.shape[0])
    scaled = [cv2.resize(frame, size, interpolation=cv2.INTER_AREA) for frame in generated]
    result = scaled.copy()
    result[0] = first
    result[-1] = last

    start_blend = min(blend_frames, (len(result) - 1) // 2)
    end_blend = min(blend_frames, (len(result) - 1) // 2)
    for offset in range(1, start_blend + 1):
        amount = offset / (start_blend + 1)
        result[offset] = blend(first, scaled[offset], amount)
    for offset in range(1, end_blend + 1):
        target = len(result) - 1 - offset
        amount = offset / (end_blend + 1)
        result[target] = blend(scaled[target], last, amount)
    return result


def main() -> None:
    args = parse_args()
    for path in (args.video, args.first_frame, args.last_frame):
        if not path.is_file():
            raise ValueError(f"Input does not exist: {path}")
    if not 0 < args.scale <= 1:
        raise ValueError("--scale must be in (0, 1]")

    output = args.output_dir.resolve()
    if output.exists():
        if not args.replace:
            raise FileExistsError(f"Output already exists: {output}")
        shutil.rmtree(output)

    rgb_dir = output / "frames_rgb"
    rgba_dir = output / "frames_rgba"
    png_dir = output / "frames_50_png"
    webp_dir = output / "frames_50_webp"
    for directory in (rgb_dir, rgba_dir, png_dir, webp_dir):
        directory.mkdir(parents=True)

    generated, source_fps = read_video(args.video)
    first = load_rgb(args.first_frame)
    last = load_rgb(args.last_frame)
    aligned = align_endpoints(generated, first, last, args.blend_frames)

    for index, frame in enumerate(aligned):
        Image.fromarray(frame).save(
            rgb_dir / f"frame_{index:04d}.png", format="PNG", optimize=True
        )

    session = new_session(args.model, providers=[args.provider])
    for index, frame in enumerate(aligned):
        cutout = remove(frame, session=session, alpha_matting=False)
        Image.fromarray(cutout).save(
            rgba_dir / f"frame_{index:04d}.png", format="PNG", optimize=True
        )

    width = round(first.shape[1] * args.scale)
    height = round(first.shape[0] * args.scale)
    size = (max(width, 1), max(height, 1))
    resampling = getattr(Image, "Resampling", Image)
    for source_path in sorted(rgba_dir.glob("*.png")):
        with Image.open(source_path) as source:
            frame = source.convert("RGBA").resize(size, resampling.LANCZOS)
        frame.save(png_dir / source_path.name, format="PNG", optimize=True)
        frame.save(
            webp_dir / f"{source_path.stem}.webp",
            format="WEBP",
            quality=args.webp_quality,
            method=args.webp_method,
        )

    manifest = {
        "sourceVideo": str(args.video),
        "sourceGeneratedFps": source_fps,
        "generatedFrameCount": len(generated),
        "frameCount": len(aligned),
        "sourceSize": [first.shape[1], first.shape[0]],
        "optimizedSize": list(size),
        "blendFramesAtEachEnd": min(args.blend_frames, (len(aligned) - 1) // 2),
        "playbackFps": len(aligned) - 1,
        "playbackDurationSeconds": 1,
        "endpointPolicy": (
            "exact source endpoint frames with crossfade into generated motion"
            if args.blend_frames > 0
            else "exact source endpoint frames with generated motion between"
        ),
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )

    print(f"output={output}")
    print(f"frames={len(aligned)}")
    print(f"source_size={first.shape[1]}x{first.shape[0]}")
    print(f"optimized_size={size[0]}x{size[1]}")
    print(f"playback_fps={len(aligned) - 1}")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, FileExistsError) as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(1)
