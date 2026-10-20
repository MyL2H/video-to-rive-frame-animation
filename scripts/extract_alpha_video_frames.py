#!/usr/bin/env python3
"""Sample an already-matted full video without changing its alpha or framing.

The original, unmatted video is required so a backend cannot silently trim,
crop, resize or retime its output. This helper does no matting or resizing.
"""

from __future__ import annotations

import argparse
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

import imageio_ffmpeg
import numpy as np
from PIL import Image


RETURN_FRAMES = 12
RETURN_SECONDS = 4


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def exact_fps(path: Path) -> Fraction:
    """Read FFmpeg's rational filter input rate, not its rounded '23.98 fps'."""
    result = subprocess.run(
        [
            imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-nostdin",
            "-noautorotate", "-i", str(path), "-map", "0:v:0", "-an",
            "-vf", "showinfo", "-frames:v", "1", "-f", "null", "-",
        ],
        capture_output=True, text=True,
    )
    match = re.search(r"config in time_base:\s*\d+/\d+,\s*frame_rate:\s*(\d+/\d+)", result.stderr)
    if result.returncode or match is None:
        raise ValueError(f"Cannot read an exact frame rate from video: {path}")
    fps = Fraction(match.group(1))
    if fps <= 0:
        raise ValueError(f"Video has an invalid frame rate: {path}")
    return fps


def inspect_timestamps(path: Path) -> dict:
    """Accept only CFR presentation timing, allowing container tick rounding.

    Absolute start offsets are not animation timing; compare timestamps relative
    to the first decoded frame. A whole-frame gap must never be accepted merely
    because a coarse container time base is also one frame long.
    """
    result = subprocess.run([
        imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-nostdin",
        "-noautorotate", "-i", str(path), "-map", "0:v:0", "-an",
        "-vf", "showinfo", "-vsync", "0", "-f", "null", "-",
    ], capture_output=True, text=True)
    configurations = re.findall(
        r"config in time_base:\s*(\d+/\d+),\s*frame_rate:\s*(\d+/\d+)",
        result.stderr)
    if result.returncode or not configurations or len(set(configurations)) != 1:
        raise ValueError(f"Cannot read consistent presentation timing: {path}")
    try:
        time_base, fps = map(Fraction, configurations[0])
    except (ValueError, ZeroDivisionError) as error:
        raise ValueError(f"Invalid presentation timing: {path}") from error
    if time_base <= 0 or fps <= 0:
        raise ValueError(f"Invalid presentation timing: {path}")
    records = re.findall(r"\bn:\s*(\d+)\s+pts:\s*([+-]?\d+)\s+pts_time:", result.stderr)
    if not records or [int(index) for index, _ in records] != list(range(len(records))):
        raise ValueError(f"Missing or inconsistent frame presentation timestamps: {path}")
    pts = [int(timestamp) for _, timestamp in records]
    if any(right <= left for left, right in zip(pts, pts[1:])):
        raise ValueError(f"Video must have strictly increasing CFR presentation timestamps: {path}")
    # Millisecond Matroska timestamps, for example, alternate 41/42 ms at
    # 24 FPS. Bound errors by one tick, capped below any full-frame omission.
    tolerance = min(time_base, Fraction(1, 4) / fps)
    errors = [abs(Fraction(timestamp - pts[0]) * time_base - Fraction(index) / fps)
              for index, timestamp in enumerate(pts)]
    maximum_error = max(errors)
    if maximum_error > tolerance:
        index = max(range(len(errors)), key=errors.__getitem__)
        raise ValueError(
            f"Only constant-frame-rate (CFR) video is supported; variable timing or "
            f"a timestamp jump at frame {index}: error={float(errors[index]):.6f}s, "
            f"allowed container rounding={float(tolerance):.6f}s: {path}")
    return {
        "fpsRational": str(fps), "timeBaseRational": str(time_base),
        "framePresentationTimestamps": pts,
        "timestampOriginSeconds": float(pts[0] * time_base),
        "cfrValidation": {
            "passed": True, "toleranceSeconds": float(tolerance),
            "maximumTimestampErrorSeconds": float(maximum_error),
            "policy": "CFR relative to first frame; one container tick rounding, capped at one quarter frame",
        },
    }


def decoder_params(path: Path) -> list[str]:
    # FFmpeg's native VP9 decoder can discard WebM's auxiliary alpha stream.
    # libvpx-vp9 exposes that stream, which is essential for honest validation.
    reader = imageio_ffmpeg.read_frames(str(path), pix_fmt="rgba", bits_per_pixel=32,
                                       input_params=["-noautorotate"])
    try:
        metadata = next(reader)
    finally:
        reader.close()
    params = ["-noautorotate"]
    if metadata.get("codec", "").rstrip(",") == "vp9":
        params += ["-c:v", "libvpx-vp9"]
    return params


def frames(path: Path, input_params: list[str]):
    """Yield native-size RGBA arrays in decoded order without FPS conversion."""
    reader = imageio_ffmpeg.read_frames(
        str(path), pix_fmt="rgba", bits_per_pixel=32,
        input_params=input_params, output_params=["-vsync", "0"],
    )
    try:
        metadata = next(reader)
        size = tuple(metadata["size"])
        if size != tuple(metadata["source_size"]):
            raise ValueError(f"Decoder changed video dimensions: {path}")
        if len(size) != 2 or min(size) <= 0:
            raise ValueError(f"Invalid video dimensions: {path}")
        yield metadata
        width, height = size
        for raw in reader:
            if len(raw) != width * height * 4:
                raise ValueError(f"Inconsistent decoded frame dimensions: {path}")
            yield np.frombuffer(raw, dtype=np.uint8).reshape(height, width, 4)
    finally:
        reader.close()


def inspect_video(path: Path, *, require_alpha: bool) -> dict:
    if not path.is_file():
        raise ValueError(f"Video does not exist: {path}")
    timing = inspect_timestamps(path)
    fps = Fraction(timing["fpsRational"])
    params = decoder_params(path)
    reader = frames(path, params)
    metadata = next(reader)
    count = 0
    minimum, maximum = 255, 0
    try:
        for count, rgba in enumerate(reader, 1):
            if require_alpha:
                alpha = rgba[:, :, 3]
                low, high = int(alpha.min()), int(alpha.max())
                if low == 255:
                    raise ValueError(
                        f"Decoded frame {count - 1} is fully opaque: {path}. "
                        "Use a codec with real alpha; a black-background MP4 is not transparent."
                    )
                if high == 0:
                    raise ValueError(f"Decoded frame {count - 1} has no visible foreground: {path}")
                minimum, maximum = min(minimum, low), max(maximum, high)
    finally:
        reader.close()
    if count == 0:
        raise ValueError(f"No video frames decoded: {path}")
    if count != len(timing["framePresentationTimestamps"]):
        raise ValueError(f"Timestamp scan and pixel decoding disagree on frame count: {path}")
    pts = timing["framePresentationTimestamps"]
    last_time = Fraction(pts[-1] - pts[0]) * Fraction(timing["timeBaseRational"])
    report = {
        "path": str(path), "sha256": sha256(path),
        "fps": float(fps), "fpsRational": str(fps),
        "decodedFrameCount": count, "size": list(metadata["size"]),
        "durationSeconds": float(last_time + 1 / fps),
        "lastDecodedFrameTimestampSeconds": float(last_time),
        "codec": metadata.get("codec"), "inputPixelFormat": metadata.get("pix_fmt"),
        "decoderInputParams": params,
        **timing,
    }
    if require_alpha:
        report["alphaRange"] = [minimum, maximum]
        report["alphaValidation"] = "Every decoded frame has visible foreground and nonopaque pixels"
    return report


def sample_indices(count: int, fps: Fraction, mode: str,
                   target_fps: Fraction = Fraction(12)) -> list[int]:
    if target_fps <= 0:
        raise ValueError("--target-fps must be positive")
    if mode == "return":
        if target_fps != 12:
            raise ValueError("Return playback must be twelve frames at 12 FPS")
        if count < RETURN_FRAMES:
            raise ValueError("Return video must contain at least twelve decoded frames")
        duration = Fraction(count) / fps
        if abs(duration - RETURN_SECONDS) > 1 / fps:
            raise ValueError(
                f"Expected a complete four-second return video, got {count} frames "
                f"at {float(fps):g} FPS ({float(duration):.6f}s); tolerance is one frame"
            )
        return [round(Fraction(index * (count - 1), RETURN_FRAMES - 1))
                for index in range(RETURN_FRAMES)]
    if mode != "source":
        raise ValueError(f"Unsupported sampling mode: {mode}")
    if target_fps > fps:
        raise ValueError("--target-fps cannot exceed the source FPS")
    selected = {round(index * fps / target_fps) for index in range(count)}
    return sorted({index for index in selected if index < count} | {count - 1})


def validate_pair(original: dict, matted: dict) -> None:
    for key, label in (("size", "dimensions"), ("fpsRational", "frame rate"),
                       ("decodedFrameCount", "decoded frame count")):
        if original[key] != matted[key]:
            raise ValueError(
                f"Matting backend changed {label}: source={original[key]}, "
                f"matted={matted[key]}; trimming, cropping, resizing and retiming are not allowed"
            )
    fps = Fraction(original["fpsRational"])
    original_base = Fraction(original["timeBaseRational"])
    matted_base = Fraction(matted["timeBaseRational"])
    # Different containers commonly have different time bases. Compare their
    # actual relative PTS values, allowing the sum of their rounding errors.
    tolerance = min(original_base + matted_base, Fraction(1, 4) / fps)
    original_pts, matted_pts = (item["framePresentationTimestamps"]
                               for item in (original, matted))
    for index, (left, right) in enumerate(zip(original_pts, matted_pts)):
        original_time = (left - original_pts[0]) * original_base
        matted_time = (right - matted_pts[0]) * matted_base
        if abs(original_time - matted_time) > tolerance:
            raise ValueError(
                f"Matting backend changed presentation timing at frame {index}; "
                f"source={float(original_time):.6f}s, matted={float(matted_time):.6f}s")


def export_samples(video: Path, info: dict, selected: list[int], output: Path,
                   *, rgb: bool = False) -> list[dict]:
    wanted = {source_index: output_index for output_index, source_index in enumerate(selected)}
    reports = []
    reader = frames(video, info["decoderInputParams"])
    next(reader)
    count = 0
    try:
        for count, rgba in enumerate(reader, 1):
            source_index = count - 1
            if source_index not in wanted:
                continue
            output_index = wanted[source_index]
            name = f"frame_{output_index:04d}.png"
            pixels = rgba[:, :, :3] if rgb else rgba
            Image.fromarray(pixels).save(output / name, format="PNG", optimize=True)
            reports.append({
                "frame": name, "outputFrame": output_index, "sourceFrame": source_index,
                "alphaRange": [int(rgba[:, :, 3].min()), int(rgba[:, :, 3].max())],
            })
    finally:
        reader.close()
    if count != info["decodedFrameCount"] or len(reports) != len(selected):
        raise ValueError(f"Video changed or decoding ended early while exporting: {video}")
    return reports


def empty_destination(path: Path) -> None:
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        raise ValueError(f"Output directory must be absent or empty: {path}")


def extract_alpha_video(video: Path, source_video: Path, output: Path, *, mode: str,
                        target_fps: Fraction = Fraction(12),
                        rgb_output: Path | None = None) -> dict:
    video, source_video, output = video.resolve(), source_video.resolve(), output.resolve()
    rgb_output = rgb_output.resolve() if rgb_output is not None else None
    if rgb_output is not None and (
        rgb_output == output or rgb_output in output.parents or output in rgb_output.parents
    ):
        raise ValueError("RGBA and RGB output directories must be separate and non-nested")
    empty_destination(output)
    if rgb_output is not None:
        empty_destination(rgb_output)
    original = inspect_video(source_video, require_alpha=False)
    matted = inspect_video(video, require_alpha=True)
    validate_pair(original, matted)
    fps = Fraction(original["fpsRational"])
    indices = sample_indices(original["decodedFrameCount"], fps, mode, target_fps)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Stage files until validation and both complete decodes succeed. A failed
    # backend result must not leave plausible-looking partial frame assets.
    with tempfile.TemporaryDirectory(prefix=".alpha-extract-", dir=output.parent) as tmp:
        stage = Path(tmp)
        rgba_stage, rgb_stage = stage / "rgba", stage / "rgb"
        rgba_stage.mkdir()
        checks = export_samples(video, matted, indices, rgba_stage)
        if rgb_output is not None:
            rgb_stage.mkdir()
            export_samples(source_video, original, indices, rgb_stage, rgb=True)
        frame_map = []
        for output_index, source_index in enumerate(indices):
            entry = {
                "outputFrame": output_index, "sourceFrame": source_index,
                "sourceTimeMs": round(Fraction(source_index * 1000) / fps),
                "videoDecodedFrame": source_index,
                "videoTimestampSeconds": float(Fraction(source_index) / fps),
                "rgbaFrame": f"frame_{output_index:04d}.png",
            }
            if rgb_output is not None:
                entry["rgbFrame"] = f"frame_{output_index:04d}.png"
            if mode == "return":
                entry["returnTimestampSeconds"] = output_index / 12
            frame_map.append(entry)
        manifest = {
            "sourceVideo": str(source_video), "sourceFps": float(fps),
            "sourceFpsRational": str(fps),
            "sourceFrameCount": original["decodedFrameCount"],
            "targetFps": float(target_fps), "outputFrameCount": len(indices),
            "mode": mode, "sourceSize": original["size"],
            "originalVideo": original, "alphaVideo": matted,
            "mattingPolicy": "Import complete video alpha from an external matting backend; no framewise re-matting",
            "geometryPolicy": "Original full resolution; no resize, padding, crop or autorotation",
            "endFramePolicy": "always retain decoded source frame N-1",
            "samplingFormula": ("round(i * (N - 1) / 11), i=0..11" if mode == "return"
                                else "round(i * sourceFps / targetFps), plus decoded frame N-1"),
            "frameMap": frame_map, "frameChecks": checks,
        }
        if rgb_output is not None:
            manifest.update(rgbOutput=str(rgb_output), rgbOutputFrameCount=len(indices))
        if mode == "return":
            manifest.update(returnPlaybackDurationSeconds=1, requestedGenerationDurationSeconds=4)
            sampling = {
                "video": matted, "samplingFormula": manifest["samplingFormula"],
                "coverage": "Entire generated video, including first and final decoded frames",
                "sourceSize": original["size"], "samples": frame_map,
            }
            (rgba_stage / "sampling_manifest.json").write_text(
                json.dumps(sampling, indent=2) + "\n", encoding="utf-8")
        (rgba_stage / "extraction_manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        if rgb_output is not None:
            rgb_output.parent.mkdir(parents=True, exist_ok=True)
            if rgb_output.exists():
                rgb_output.rmdir()
            shutil.move(str(rgb_stage), str(rgb_output))
        if output.exists():
            output.rmdir()
        shutil.move(str(rgba_stage), str(output))
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, required=True, help="Complete video with real alpha")
    parser.add_argument("--source-video", type=Path, required=True, help="Original video before matting")
    parser.add_argument("--output", "--output-dir", dest="output", type=Path, required=True)
    parser.add_argument("--rgb-output", type=Path, help="Optional original RGB samples for audit")
    parser.add_argument("--mode", choices=("source", "return"), required=True)
    parser.add_argument("--target-fps", type=Fraction, default=Fraction(12))
    args = parser.parse_args()
    try:
        manifest = extract_alpha_video(
            args.video, args.source_video, args.output, mode=args.mode,
            target_fps=args.target_fps, rgb_output=args.rgb_output)
    except (ValueError, OSError, RuntimeError) as error:
        parser.exit(1, f"error: {error}\n")
    print(json.dumps({
        "output": str(args.output.resolve()), "mode": args.mode,
        "frames": manifest["outputFrameCount"], "size": manifest["sourceSize"],
        "sourceFpsRational": manifest["sourceFpsRational"],
    }))


if __name__ == "__main__":
    main()
