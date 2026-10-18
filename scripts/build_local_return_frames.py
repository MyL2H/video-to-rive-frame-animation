#!/usr/bin/env python3
"""Build a twelve-frame return by sampling source motion in reverse."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import cv2
from PIL import Image


RETURN_FPS = 12
RETURN_FRAME_COUNT = 12


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--boundary-frame", type=int, required=True)
    parser.add_argument("--target-fps", type=float, default=RETURN_FPS)
    parser.add_argument("--replace", action="store_true")
    return parser.parse_args()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    if args.target_fps != RETURN_FPS:
        raise ValueError(f"Return sequences must use exactly {RETURN_FPS} FPS")

    capture = cv2.VideoCapture(str(args.video))
    if not capture.isOpened():
        raise ValueError(f"OpenCV could not open video: {args.video}")
    fps = float(capture.get(cv2.CAP_PROP_FPS))
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    if fps <= 0 or frame_count <= 0:
        raise ValueError(f"Invalid video metadata: fps={fps}, frames={frame_count}")
    if not 0 <= args.boundary_frame < frame_count:
        raise ValueError(
            f"Boundary frame must be in 0..{frame_count - 1}, got {args.boundary_frame}"
        )

    label = (
        round(frame_count * 1000 / fps)
        if args.boundary_frame == frame_count - 1
        else round(args.boundary_frame * 1000 / fps)
    )
    boundary_dir = args.output_root / f"return_{label}ms"
    inputs = boundary_dir / "inputs"
    generated = boundary_dir / "generated"
    inputs.mkdir(parents=True, exist_ok=True)
    if generated.exists():
        if not args.replace:
            raise FileExistsError(f"Output already exists: {generated}")
        shutil.rmtree(generated)
    generated.mkdir(parents=True)

    source_indices = [
        round(args.boundary_frame * index / (RETURN_FRAME_COUNT - 1))
        for index in range(RETURN_FRAME_COUNT)
    ]
    for output_index, source_index in enumerate(source_indices):
        if not capture.set(cv2.CAP_PROP_POS_FRAMES, source_index):
            raise ValueError(f"Could not seek to source frame {source_index}")
        ok, frame = capture.read()
        if not ok:
            raise ValueError(f"Could not decode source frame {source_index}")
        path = generated / f"frame_{output_index:04d}.png"
        Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)).save(
            path, format="PNG", optimize=True
        )
        if output_index == 0:
            shutil.copy2(path, inputs / "first_frame.png")
        if output_index == RETURN_FRAME_COUNT - 1:
            shutil.copy2(path, inputs / "last_frame.png")
    capture.release()

    write_json(
        boundary_dir / "fallback.json",
        {
            "method": "source_reverse_sampling",
            "reason": "Seedream output safety rejected the storyboard after safe-prompt retries",
            "sourceVideo": str(args.video),
            "sourceFps": fps,
            "sourceFrameCount": frame_count,
            "sourceBoundaryFrame": args.boundary_frame,
            "returnFps": RETURN_FPS,
            "returnFrameCount": RETURN_FRAME_COUNT,
            "sourceFrameSequence": source_indices,
            "endpointPolicy": "exact source boundary and source frame 0",
        },
    )
    print(f"generated={generated}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, FileExistsError) as error:
        print(f"Error: {error}")
        raise SystemExit(1)
