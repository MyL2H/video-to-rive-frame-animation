#!/usr/bin/env python3
"""Convert a complete four-second Seedance video into a twelve-frame return.

This offline helper makes no generation calls. It preserves native RGB samples,
applies this skill's matte pipeline, restores exact source RGBA endpoints, and
writes 50% PNG/WebP assets for a one-second return at 12 FPS.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

import cv2
import numpy as np
from PIL import Image, ImageDraw


SCRIPTS = Path(__file__).resolve().parent
FRAME_COUNT = 12
FPS = 12
BACKGROUND_RGB = (235, 235, 235)


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_endpoints(paths: list[Path]) -> tuple[int, int]:
    size = None
    for path in paths:
        with Image.open(path) as image:
            if image.format != "PNG" or image.mode != "RGBA":
                raise ValueError(f"Exact source endpoint must be an RGBA PNG: {path}")
            if size is not None and image.size != size:
                raise ValueError("Source endpoints have different dimensions")
            size = image.size
            if not image.getchannel("A").getbbox():
                raise ValueError(f"Empty source endpoint: {path}")
    assert size is not None
    return size


def run_skill(script: str, *args: object, advisory: bool = False) -> int:
    result = subprocess.run([sys.executable, str(SCRIPTS / script), *map(str, args)])
    if result.returncode and not (advisory and result.returncode == 1):
        raise RuntimeError(f"Skill helper {script} failed with exit {result.returncode}")
    return result.returncode


def normalization_size(native: tuple[int, int], source: tuple[int, int]) -> tuple[int, int]:
    """Permit only an exact ratio or one added background row/column."""
    width, height = native
    source_width, source_height = source
    for padded in ((width, height), (width, height + 1), (width + 1, height)):
        if padded[0] * source_height == padded[1] * source_width:
            return padded
    raise ValueError(
        f"Video size {width}x{height} differs from source aspect ratio "
        f"{source_width}:{source_height} by more than one pixel of provider rounding; "
        "refusing cropping or nonuniform resizing"
    )


def read_video(video: Path, source_size: tuple[int, int]) -> tuple[list[np.ndarray], dict]:
    capture = cv2.VideoCapture(str(video))
    if not capture.isOpened():
        raise ValueError(f"Unable to open generated video: {video}")
    fps = float(capture.get(cv2.CAP_PROP_FPS))
    reported = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    decoded = []
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            decoded.append(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    finally:
        capture.release()
    if len(decoded) < FRAME_COUNT or not np.isfinite(fps) or fps <= 0:
        raise ValueError("Video needs at least twelve decoded frames and a valid FPS")
    if any(frame.shape != decoded[0].shape for frame in decoded):
        raise ValueError("Generated video changes dimensions")
    # A provider may include both t=0 and t=4, giving 97 rather than 96 frames
    # at 24 FPS. Accept that single-frame discrepancy; reject trimmed clips.
    if abs(len(decoded) / fps - 4.0) > 1 / fps + 1e-6:
        raise ValueError(
            f"Expected the complete four-second video, got {len(decoded)} frames "
            f"at {fps:g} FPS ({len(decoded) / fps:.6f}s)"
        )
    height, width = decoded[0].shape[:2]
    normalized = normalization_size((width, height), source_size)
    return decoded, {
        "path": str(video), "sha256": sha256(video), "fps": fps,
        "reportedFrameCount": reported, "decodedFrameCount": len(decoded),
        "size": [width, height], "durationSeconds": len(decoded) / fps,
        "lastDecodedFrameTimestampSeconds": (len(decoded) - 1) / fps,
        "requestedGenerationDurationSeconds": 4,
        "sourceAspectNormalization": {
            "nativeSize": [width, height], "normalizedSize": list(normalized),
            "paddingPixels": {"left": 0, "top": 0, "right": normalized[0] - width,
                              "bottom": normalized[1] - height},
            "paddingRGB": list(BACKGROUND_RGB),
            "policy": "At most one background row or column for provider pixel rounding, then uniform scaling; no crop",
        },
    }


def composite(path: Path) -> Image.Image:
    with Image.open(path) as source:
        rgba = source.convert("RGBA")
        background = Image.new("RGBA", rgba.size, (*BACKGROUND_RGB, 255))
        background.alpha_composite(rgba)
    return background.convert("RGB")


def contact_sheet(files: list[Path], labels: list[str], output: Path,
                  size: tuple[int, int]) -> None:
    width, height = size
    columns, header = 4, 28
    rows = (len(files) + columns - 1) // columns
    canvas = Image.new("RGB", (columns * width, rows * (height + header)), (245, 245, 245))
    draw = ImageDraw.Draw(canvas)
    for index, (path, label) in enumerate(zip(files, labels)):
        image = composite(path)
        # Keep the native audit's actual aspect ratio even if it differs by a
        # single provider-rounded pixel. Letterboxing here is only a preview.
        image.thumbnail(size, Image.Resampling.LANCZOS)
        x = (index % columns) * width
        y = (index // columns) * (height + header)
        canvas.paste(image, (x + (width - image.width) // 2, y + header))
        draw.text((x + 5, y + 6), label, fill=(20, 20, 20))
    canvas.save(output, quality=94)


def frame_checks(files: list[Path], size: tuple[int, int], *,
                 replaceable_endpoints: bool = False) -> list[dict]:
    if len(files) != FRAME_COUNT:
        raise ValueError(f"Expected twelve frames, got {len(files)}")
    reports = []
    for index, path in enumerate(files):
        with Image.open(path) as image:
            if image.mode != "RGBA" or image.size != size:
                raise ValueError(f"Frame has wrong mode or dimensions: {path}")
            alpha = np.asarray(image.getchannel("A"))
            will_replace = replaceable_endpoints and index in (0, FRAME_COUNT - 1)
            if not will_replace and (not (alpha > 0).any() or not (alpha < 255).any()):
                raise ValueError(f"Frame is empty or lacks transparency: {path}")
            mask = alpha >= 128 if (alpha >= 128).any() else alpha > 0
            ys, xs = np.where(mask)
            has_foreground = bool(xs.size)
            reports.append({
                "frame": path.name, "size": list(size), "mode": image.mode,
                "alphaRange": [int(alpha.min()), int(alpha.max())],
                "replacedByExactSourceEndpoint": will_replace,
                "foregroundBounds": ([int(xs.min()), int(ys.min()), int(xs.max() + 1), int(ys.max() + 1)]
                                     if has_foreground else None),
                "foregroundBottomPx": int(ys.max() + 1) if has_foreground else None,
                "foregroundHeightPx": int(ys.max() - ys.min() + 1) if has_foreground else 0,
            })
    return reports


def encode_preview(files: list[Path], output: Path) -> dict:
    frames = [composite(path) for path in files]
    size = frames[0].size
    if any(dimension % 2 for dimension in size):
        return {"available": False, "reason": "H.264 preview requires even dimensions; PNG/WebP assets remain valid"}
    writer = cv2.VideoWriter(str(output), cv2.VideoWriter_fourcc(*"avc1"), FPS, size)
    if not writer.isOpened():
        writer.release()
        return {"available": False, "reason": "OpenCV H.264 avc1 encoder unavailable; PNG/WebP assets remain valid"}
    try:
        for frame in frames:
            writer.write(cv2.cvtColor(np.asarray(frame), cv2.COLOR_RGB2BGR))
    finally:
        writer.release()
    capture = cv2.VideoCapture(str(output))
    count = 0
    try:
        while True:
            ok, _ = capture.read()
            if not ok:
                break
            count += 1
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        actual_size = (int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)), int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)))
    finally:
        capture.release()
    if count != FRAME_COUNT or abs(fps - FPS) > 1e-6 or actual_size != size:
        raise ValueError(f"Incorrect encoded preview: {count} frames, {fps} FPS, {actual_size}")
    return {"available": True, "path": str(output), "decodedFrameCount": count,
            "fps": fps, "durationSeconds": count / fps, "size": list(size), "codec": "H264"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, required=True, help="Complete four-second generated video")
    parser.add_argument("--first-frame", type=Path, required=True, help="Exact transparent source RGBA PNG at the cut point")
    parser.add_argument("--last-frame", type=Path, required=True, help="Exact transparent first source RGBA PNG")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    video, output = args.video.resolve(), args.output_dir.resolve()
    endpoints = [args.first_frame.resolve(), args.last_frame.resolve()]
    source_size = check_endpoints(endpoints)
    optimized_size = tuple(max(1, round(dimension * 0.5)) for dimension in source_size)
    for helper in ("remove_background.py", "prepare_frame_assets.py", "validate_matte.py"):
        if not (SCRIPTS / helper).is_file():
            raise FileNotFoundError(SCRIPTS / helper)
    decoded, video_info = read_video(video, source_size)
    indices = [round(i * (len(decoded) - 1) / (FRAME_COUNT - 1)) for i in range(FRAME_COUNT)]
    if indices[0] != 0 or indices[-1] != len(decoded) - 1 or len(set(indices)) != FRAME_COUNT:
        raise ValueError("Sampling must span the complete video in twelve distinct frames")

    directories = {key: output / key for key in (
        "frames_native_rgb", "frames_rgb", "generated_frames_rgba", "frames_rgba",
        "frames_50_png", "frames_50_webp",
    )}
    expected_names = {f"frame_{i:04d}" for i in range(FRAME_COUNT)}
    for directory in directories.values():
        if any(directory == source.parent for source in [video, *endpoints]):
            raise ValueError("Input files must be outside generated frame directories")
        directory.mkdir(parents=True, exist_ok=True)
        extras = [path for path in directory.glob("frame_*") if path.stem not in expected_names]
        if extras:
            raise ValueError(f"Unexpected frames in output directory: {extras}")

    samples = []
    normalized_size = tuple(video_info["sourceAspectNormalization"]["normalizedSize"])
    for index, source_index in enumerate(indices):
        name = f"frame_{index:04d}.png"
        image = Image.fromarray(decoded[source_index])
        image.save(directories["frames_native_rgb"] / name, optimize=True)
        if image.size != normalized_size:
            padded = Image.new("RGB", normalized_size, BACKGROUND_RGB)
            padded.paste(image, (0, 0))
            image = padded
        if image.size != source_size:
            image = image.resize(source_size, Image.Resampling.LANCZOS)
        image.save(directories["frames_rgb"] / name, optimize=True)
        samples.append({"outputFrame": index, "videoDecodedFrame": source_index,
                        "videoTimestampSeconds": source_index / video_info["fps"],
                        "returnTimestampSeconds": index / FPS})
    write_json(output / "sampling_manifest.json", {
        "video": video_info, "samplingFormula": "round(i * (N - 1) / 11), i=0..11",
        "coverage": "Entire generated video, including first and final decoded frames",
        "sourceSize": list(source_size), "samples": samples,
    })

    run_skill("remove_background.py", "--source", directories["frames_rgb"],
              "--output", directories["generated_frames_rgba"],
              "--background-min", 230, "--background-max", 241)
    generated = sorted(directories["generated_frames_rgba"].glob("frame_*.png"))
    generated_checks = frame_checks(generated, source_size, replaceable_endpoints=True)
    # Compare generated masks with their own RGB before source endpoints replace
    # frames 0/11. Comparing restored endpoints against generated RGB is invalid.
    generated_matte_exit = run_skill(
        "validate_matte.py", "--frames-dir", directories["generated_frames_rgba"],
        "--rgb-dir", directories["frames_rgb"],
        "--report", output / "generated_matte_validation.json",
        "--contact-sheet", output / "generated_matte_contact_sheet.jpg",
        advisory=True,
    )
    for path in generated:
        shutil.copy2(path, directories["frames_rgba"] / path.name)
    targets = [directories["frames_rgba"] / "frame_0000.png", directories["frames_rgba"] / "frame_0011.png"]
    for source, target in zip(endpoints, targets):
        shutil.copy2(source, target)
    endpoint_checks = [
        {"frame": target.name, "source": str(source), "sourceSHA256": sha256(source),
         "outputSHA256": sha256(target), "exactFileMatch": sha256(source) == sha256(target)}
        for source, target in zip(endpoints, targets)
    ]
    if not all(item["exactFileMatch"] for item in endpoint_checks):
        raise ValueError("Source endpoint files were not preserved exactly")
    run_skill("prepare_frame_assets.py", "--source", directories["frames_rgba"],
              "--png-output", directories["frames_50_png"], "--webp-output", directories["frames_50_webp"],
              "--scale", 0.5, "--webp-quality", 82, "--webp-method", 6)
    rgba = sorted(directories["frames_rgba"].glob("frame_*.png"))
    png = sorted(directories["frames_50_png"].glob("frame_*.png"))
    webp = sorted(directories["frames_50_webp"].glob("frame_*.webp"))
    checks = {"generatedRGBA": generated_checks, "rgba": frame_checks(rgba, source_size),
              "png50": frame_checks(png, optimized_size), "webp50": frame_checks(webp, optimized_size)}
    matte_exit = run_skill("validate_matte.py", "--frames-dir", directories["frames_rgba"],
                           "--report", output / "matte_validation.json",
                           "--contact-sheet", output / "matte_contact_sheet.jpg")

    labels = [f"{i:02d} video {sample['videoTimestampSeconds']:.2f}s" for i, sample in enumerate(samples)]
    endpoint_labels = list(labels)
    endpoint_labels[0] += " EXACT START"
    endpoint_labels[-1] += " EXACT END"
    contact_sheet(png, endpoint_labels, output / "preview_contact_sheet.jpg", optimized_size)
    native = sorted(directories["frames_native_rgb"].glob("frame_*.png"))
    contact_sheet(native, labels, output / "generated_sample_contact_sheet.jpg", optimized_size)
    preview = encode_preview(png, output / "preview_12fps_1s.mp4")
    bottoms = [item["foregroundBottomPx"] for item in checks["rgba"]]
    heights = [item["foregroundHeightPx"] for item in checks["rgba"]]
    manifest = {
        "generationMethod": "Seedance 2.5 four-second video, uniformly sampled over all decoded frames",
        "generationVideo": video_info, "frameCount": FRAME_COUNT, "fps": FPS,
        "durationFrames": FRAME_COUNT, "durationSeconds": FRAME_COUNT / FPS,
        "sourceSize": list(source_size), "optimizedSize": list(optimized_size), "sampling": samples,
        "endpointPolicy": "Exact source RGBA file replacement at frames 0 and 11; no cross-fade",
        "endpointChecks": endpoint_checks, "frameChecks": checks,
        "mattingPolicy": "Save complete RGB PNG first; calibrated RGB-supported segmentation; preserve real gaps and refine only edges",
        "optimization": {"scale": 0.5, "webpQuality": 82, "webpMethod": 6},
        "matteValidatorExitCode": matte_exit, "visualChecksAreAdvisory": True,
        "generatedMatteValidatorExitCode": generated_matte_exit,
        "rgbMatteDiagnostic": {
            "report": str(output / "generated_matte_validation.json"),
            "scope": "Generated RGBA paired with generated RGB before exact source endpoint replacement",
            "restoredEndpoints": "Review source RGB/RGBA diagnostics separately",
        },
        "frameBoundsDiagnostic": {"foregroundBottomPxRange": [min(bottoms), max(bottoms)],
                                  "foregroundHeightPxRange": [min(heights), max(heights)],
                                  "note": "Alpha bounds include props; these are not body registration or scale estimates"},
        "stabilizationUsed": False, "croppingUsed": False, "preview": preview,
        "bytes": {"png50": sum(path.stat().st_size for path in png),
                  "webp50": sum(path.stat().st_size for path in webp)},
        "technicalChecksPassed": True,
    }
    write_json(output / "manifest.json", manifest)
    print(json.dumps({"technicalChecksPassed": True, "frameCount": FRAME_COUNT,
                      "fps": FPS, "durationSeconds": 1, "preview": preview,
                      "manifest": str(output / "manifest.json")}))


if __name__ == "__main__":
    main()
