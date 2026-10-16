#!/usr/bin/env python3
"""Generate a Rive RML timeline from a numbered image sequence."""

from __future__ import annotations

import argparse
import os
import re
from pathlib import Path
from xml.sax.saxutils import quoteattr

from PIL import Image


NUMERIC_SUFFIX = re.compile(r"(\d+)(?=\.[^.]+$)")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frames", type=Path, required=True)
    parser.add_argument("--project-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--pattern", default="*.webp")
    parser.add_argument("--fps", type=int, default=24)
    parser.add_argument("--loop", choices=("oneShot", "loop", "pingPong"), default="loop")
    return parser.parse_args()


def frame_number(path: Path) -> int:
    match = NUMERIC_SUFFIX.search(path.name)
    if not match:
        raise ValueError(f"Frame filename has no numeric suffix: {path.name}")
    return int(match.group(1))


def ordered_frames(directory: Path, pattern: str) -> list[Path]:
    frames = sorted(directory.glob(pattern), key=frame_number)
    if not frames:
        raise ValueError(f"No frames matched {pattern!r} in {directory}")
    numbers = [frame_number(path) for path in frames]
    if numbers != list(range(len(frames))):
        raise ValueError(f"Frame numbering must be continuous from 0: {numbers}")
    return frames


def frame_dimensions(frames: list[Path]) -> tuple[int, int]:
    with Image.open(frames[0]) as first:
        size = first.size
        mode = first.mode
    if mode != "RGBA":
        raise ValueError(f"Frames must be RGBA for transparent output, found {mode}")
    for path in frames[1:]:
        with Image.open(path) as frame:
            if frame.size != size:
                raise ValueError(f"Frame size mismatch: {path}")
            if frame.mode != "RGBA":
                raise ValueError(f"Frame must be RGBA: {path}")
    return size


def number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


def build_rml(frames: list[Path], project_dir: Path, fps: int, loop: str) -> str:
    width, height = frame_dimensions(frames)
    lines = ['<Rive version="1" kind="fragment">', ""]

    for index, frame_path in enumerate(frames):
        relative_path = os.path.relpath(frame_path.resolve(), project_dir.resolve()).replace(os.sep, "/")
        lines.append(
            "  <ImageAsset file={} name={} id=\"0:{}\"/>".format(
                quoteattr(relative_path), quoteattr(f"Frame {index:04d}"), 1000 + index
            )
        )

    lines.extend(
        [
            "",
            "  <Artboard defaultStateMachineId=\"0:7\" styleId=\"0:5\" "
            "width={} height={} name=\"Frame Sequence\" id=\"0:2\">".format(
                quoteattr(number(width)), quoteattr(number(height))
            ),
            '    <LayoutComponentStyle name="Artboard Style" id="0:5"/>',
            "",
            "    <Solo x={} y={} activeComponentId=\"0:2000\" "
            "name=\"Timeline Frames\" id=\"0:200\">".format(
                quoteattr(number(width / 2)), quoteattr(number(height / 2))
            ),
        ]
    )

    for index in range(len(frames)):
        lines.append(
            "      <Image x=\"0\" y=\"0\" assetId=\"0:{}\" name={} id=\"0:{}\"/>".format(
                1000 + index, quoteattr(f"Frame {index:04d}"), 2000 + index
            )
        )

    lines.extend(
        [
            "    </Solo>",
            "",
            "    <LinearAnimation fps={} duration={} loopValue={} "
            "name=\"Frame Timeline\" id=\"0:6\">".format(
                quoteattr(number(fps)), quoteattr(number(len(frames) - 1)), quoteattr(loop)
            ),
            '      <KeyedObject objectId="0:200">',
            '        <KeyedProperty propertyKey="296">',
        ]
    )

    for index in range(len(frames)):
        lines.append(
            "          <KeyFrameId value=\"0:{}\" frame={} interpolationType=\"hold\"/>".format(
                2000 + index, quoteattr(number(index))
            )
        )

    lines.extend(
        [
            "        </KeyedProperty>",
            "      </KeyedObject>",
            "    </LinearAnimation>",
            "",
            '    <StateMachine name="Frame Playback" id="0:7">',
            '      <StateMachineLayer name="Playback Layer" id="0:8">',
            '        <AnyState x="200" y="-120"/>',
            '        <ExitState x="400" y="-120"/>',
            "        <EntryState>",
            '          <StateTransition stateToId="0:12"/>',
            "        </EntryState>",
            '        <AnimationState x="200" animationId="0:6" reset="true" id="0:12"/>',
            "      </StateMachineLayer>",
            "    </StateMachine>",
            "  </Artboard>",
            "</Rive>",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    args = parse_args()
    if args.fps <= 0:
        raise ValueError("--fps must be positive")
    project_dir = args.project_dir.resolve()
    if not (project_dir / "rive.yaml").is_file():
        raise ValueError(f"Not a Rive CLI project: {project_dir}")

    frames = ordered_frames(args.frames.resolve(), args.pattern)
    output = args.output.resolve() if args.output else project_dir / "scene.rml"
    output.write_text(build_rml(frames, project_dir, args.fps, args.loop), encoding="utf-8")

    width, height = frame_dimensions(frames)
    print(f"output={output}")
    print(f"frames={len(frames)}")
    print(f"size={width}x{height}")
    print(f"fps={args.fps}")


if __name__ == "__main__":
    main()
