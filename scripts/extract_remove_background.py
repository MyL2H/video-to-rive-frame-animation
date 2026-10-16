#!/usr/bin/env python3
"""Extract video frames and write transparent RGBA PNG files."""

from __future__ import annotations

import argparse
from pathlib import Path

import cv2
from PIL import Image
from rembg import new_session, remove


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default="isnet-general-use")
    parser.add_argument("--provider", default="CPUExecutionProvider")
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
    session = new_session(args.model, providers=[args.provider])

    frame_index = 0
    while True:
        ok, frame_bgr = capture.read()
        if not ok:
            break
        frame_rgba = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGBA)
        cutout_rgba = remove(frame_rgba, session=session, alpha_matting=False)
        output_path = args.output / f"frame_{frame_index:04d}.png"
        Image.fromarray(cutout_rgba).save(output_path, format="PNG", optimize=True)
        frame_index += 1

    capture.release()
    if frame_index == 0:
        raise ValueError("No frames were decoded")

    print(f"video={args.video}")
    print(f"output={args.output}")
    print(f"fps={fps}")
    print(f"frames={frame_index}")
    print(f"container_frame_count={expected_count}")


if __name__ == "__main__":
    main()
