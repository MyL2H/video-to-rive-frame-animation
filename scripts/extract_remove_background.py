#!/usr/bin/env python3
"""Extract complete RGB PNG frames, then write transparent RGBA PNG files."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image
from rembg import new_session, remove

from rgb_matte import (
    DEFAULT_BACKGROUND_MAX,
    DEFAULT_BACKGROUND_MIN,
    matte_rgb_frame,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rgb-output", type=Path)
    parser.add_argument("--target-fps", type=float)
    parser.add_argument("--model", default="isnet-general-use")
    parser.add_argument("--provider", default="CPUExecutionProvider")
    parser.add_argument("--background-min", type=int, default=DEFAULT_BACKGROUND_MIN)
    parser.add_argument("--background-max", type=int, default=DEFAULT_BACKGROUND_MAX)
    parser.add_argument("--edge-pixels", type=int, default=2)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.video.is_file():
        raise ValueError(f"Video does not exist: {args.video}")
    args.output.mkdir(parents=True, exist_ok=True)

    capture = cv2.VideoCapture(str(args.video))
    if not capture.isOpened():
        raise ValueError(f"OpenCV could not open video: {args.video}")

    fps = float(capture.get(cv2.CAP_PROP_FPS))
    expected_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    if fps <= 0 or expected_count <= 0:
        raise ValueError(f"Invalid video metadata: fps={fps}, frames={expected_count}")
    if args.target_fps is not None and args.target_fps <= 0:
        raise ValueError("--target-fps must be positive")
    if args.background_min < 0 or args.background_max > 255:
        raise ValueError("background range must stay within 0..255")
    if args.background_min > args.background_max:
        raise ValueError("background-min cannot exceed background-max")

    rgb_output = args.rgb_output or args.output.parent / f"{args.output.name}_rgb"
    rgb_output.mkdir(parents=True, exist_ok=True)

    if args.target_fps is None:
        selected_indices = set(range(expected_count))
    else:
        step = fps / args.target_fps
        selected_indices = {round(index * step) for index in range(expected_count)}
        selected_indices = {
            min(index, expected_count - 1) for index in selected_indices
        }
        selected_indices.add(expected_count - 1)
    selected_indices = sorted(selected_indices)
    session = new_session(args.model, providers=[args.provider])

    source_index = 0
    output_index = 0
    frame_map = []
    while True:
        ok, frame_bgr = capture.read()
        if not ok:
            break
        if source_index in selected_indices:
            frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
            rgb_path = rgb_output / f"frame_{output_index:04d}.png"
            Image.fromarray(frame_rgb).save(rgb_path, format="PNG", optimize=True)
            rembg_rgba = np.asarray(
                remove(frame_rgb, session=session, alpha_matting=False)
            )
            cutout_rgba, matte_report = matte_rgb_frame(
                frame_rgb,
                rembg_rgba,
                background_min=args.background_min,
                background_max=args.background_max,
                edge_pixels=args.edge_pixels,
            )
            output_path = args.output / f"frame_{output_index:04d}.png"
            Image.fromarray(cutout_rgba).save(
                output_path, format="PNG", optimize=True
            )
            frame_map.append(
                {
                    "outputFrame": output_index,
                    "sourceFrame": source_index,
                    "sourceTimeMs": round(source_index * 1000 / fps),
                    "rgbFrame": rgb_path.name,
                    "matte": matte_report,
                }
            )
            output_index += 1
        source_index += 1

    capture.release()
    if output_index == 0:
        raise ValueError("No frames were decoded")

    extraction_manifest = {
        "sourceVideo": str(args.video),
        "sourceFps": fps,
        "sourceFrameCount": source_index,
        "targetFps": args.target_fps or fps,
        "outputFrameCount": output_index,
        "rgbOutput": str(rgb_output),
        "rgbOutputFrameCount": output_index,
        "mattingPolicy": "save complete RGB PNG first; strict border-connected RGB range plus rembg edge feather",
        "backgroundRange": [args.background_min, args.background_max],
        "edgePixels": args.edge_pixels,
        "holePolicy": "fill enclosed transparent components",
        "frameMap": frame_map,
        "endFramePolicy": "always retain decoded source frame N-1",
    }
    (args.output / "extraction_manifest.json").write_text(
        json.dumps(extraction_manifest, indent=2) + "\n", encoding="utf-8"
    )

    print(f"video={args.video}")
    print(f"output={args.output}")
    print(f"fps={fps}")
    print(f"target_fps={args.target_fps or fps}")
    print(f"frames={output_index}")
    print(f"rgb_output={rgb_output}")
    print(f"container_frame_count={expected_count}")


if __name__ == "__main__":
    main()
