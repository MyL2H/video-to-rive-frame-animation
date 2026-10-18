#!/usr/bin/env python3
"""Generate and process twelve ordered Seedream return frames for one cut point."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path

import cv2
import numpy as np
from PIL import Image
from rembg import new_session, remove

from rgb_matte import (
    DEFAULT_BACKGROUND_MAX,
    DEFAULT_BACKGROUND_MIN,
    fill_enclosed_transparent,
    matte_rgb_frame,
)


DEFAULT_BASE = "https://ark.cn-beijing.volces.com/api/v3"
DEFAULT_MODEL = "doubao-seedream-5-0-pro-260628"
RETURN_FPS = 12
RETURN_FRAME_COUNT = 12
STORYBOARD_COLUMNS = 4
STORYBOARD_ROWS = 3
STORYBOARD_SIZE = "1024x1152"
DEFAULT_RETURN_PROMPT = (
    "Create exactly 12 ordered animation frames as 12 separate images. Frame 1 must "
    "match the first reference pose and frame 12 must match the second reference pose. "
    "Build one smooth, natural return motion from the first reference pose to the second "
    "reference pose with even motion spacing. Preserve the same character identity, "
    "facial features, clothing, body proportions, subject height, torso width, feet "
    "anchor, screen position, artistic style, lighting, and background in every frame. "
    "Use a fixed camera and fixed framing. Do not zoom, resize, crop, reframe, drift, "
    "morph, add objects, remove objects, change text, add text, add a watermark, or cut. "
    "The 12 images must read as consecutive animation frames at 12 frames per second."
)
DEFAULT_STORYBOARD_PROMPT = (
    "Create one ordered animation storyboard containing exactly 4 columns and 3 rows, "
    "for 12 equal rectangular panels in total. Each panel is a 2:3 portrait animation "
    "of a fully clothed, child-friendly cartoon character with no nudity, sexual "
    "content, violence, injury, weapons, or adult themes. "
    "frame with no gutters, borders, captions, labels, or watermark. Read panels from "
    "left to right and top to bottom. Panel 1 must match the first reference pose and "
    "panel 12 must match the second reference pose. Build one smooth, natural return "
    "motion from the first reference pose to the second reference pose with even motion "
    "spacing. Preserve the same character identity, facial features, clothing, body "
    "proportions, subject height, torso width, feet anchor, screen position, artistic "
    "style, lighting, camera, framing, and background in every panel. Do not zoom, "
    "resize, crop, reframe, drift, morph, add objects, remove objects, change text, add "
    "text, add a watermark, or cut. The 12 panels must read as consecutive animation "
    "frames at 12 frames per second."
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--boundary-frame", type=int, required=True)
    parser.add_argument("--target-fps", type=float, default=RETURN_FPS)
    parser.add_argument("--env-file", type=Path, default=Path(".env.seedance"))
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--prompt")
    parser.add_argument("--size", default="1024x1536")
    parser.add_argument("--storyboard", action="store_true")
    parser.add_argument("--storyboard-size", default=STORYBOARD_SIZE)
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--scale", type=float, default=0.5)
    parser.add_argument("--webp-quality", type=int, default=82)
    parser.add_argument("--webp-method", type=int, default=6)
    parser.add_argument("--matting-model", default="isnet-general-use")
    parser.add_argument("--provider", default="CPUExecutionProvider")
    parser.add_argument("--background-min", type=int, default=DEFAULT_BACKGROUND_MIN)
    parser.add_argument("--background-max", type=int, default=DEFAULT_BACKGROUND_MAX)
    parser.add_argument("--edge-pixels", type=int, default=2)
    parser.add_argument("--source-rgba-dir", type=Path)
    parser.add_argument("--replace", action="store_true")
    parser.add_argument(
        "command", choices=("prepare", "generate", "process", "run")
    )
    args = parser.parse_args()

    if args.target_fps != RETURN_FPS:
        raise ValueError(f"Return sequences must use exactly {RETURN_FPS} FPS")
    if not 0 < args.scale <= 1:
        raise ValueError("--scale must be in (0, 1]")
    if args.background_min < 0 or args.background_max > 255:
        raise ValueError("background range must stay within 0..255")
    if args.background_min > args.background_max:
        raise ValueError("background-min cannot exceed background-max")

    capture = cv2.VideoCapture(str(args.video))
    if not capture.isOpened():
        raise ValueError(f"OpenCV could not open video: {args.video}")
    fps = float(capture.get(cv2.CAP_PROP_FPS))
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    capture.release()
    if fps <= 0 or frame_count <= 0:
        raise ValueError(f"Invalid video metadata: fps={fps}, frames={frame_count}")

    def boundary_dir(_: argparse.Namespace) -> Path:
        if args.boundary_frame == frame_count - 1:
            label = round(frame_count * 1000 / fps)
        else:
            label = round(args.boundary_frame * 1000 / fps)
        return args.output_root / f"return_{label}ms"

    args.boundary_dir = boundary_dir
    args.source_fps = fps
    args.source_frame_count = frame_count
    return args


def load_env(path: Path) -> None:
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        os.environ.setdefault(name.strip(), value.strip())


def credentials() -> tuple[str, tuple[str, ...]]:
    ark_key = os.getenv("ARK_API_KEY", "")
    if ark_key:
        return ark_key, (ark_key,)
    key_id = os.getenv("SEEDANCE_API_KEY_ID", "")
    key_secret = os.getenv("SEEDANCE_API_KEY_SECRET", "")
    if not key_id or not key_secret:
        raise ValueError(
            "ARK_API_KEY or SEEDANCE_API_KEY_ID and SEEDANCE_API_KEY_SECRET are required"
        )
    mode = os.getenv("SEEDANCE_ARK_AUTH_MODE", "bearer-secret")
    values = {
        "bearer-secret": key_secret,
        "bearer-pair": f"{key_id}.{key_secret}",
        "bearer-id": key_id,
    }
    if mode not in values:
        raise ValueError(f"Unsupported SEEDANCE_ARK_AUTH_MODE: {mode}")
    return values[mode], (key_id, key_secret)


def redact(value: object, secrets: tuple[str, ...]) -> str:
    text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    for secret in filter(None, secrets):
        text = text.replace(secret, "<redacted>")
    return re.sub(r"https?://[^\"\\s]+", "<redacted-url>", text)


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def read_rgb(path: Path) -> Image.Image:
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Could not read image: {path}")
    return Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB)).convert("RGB")


def image_data_url(path: Path) -> str:
    with Image.open(path) as image:
        image.convert("RGB").save(path, format="PNG", optimize=True)
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def boundary_label(frame_index: int, fps: float, frame_count: int) -> int:
    if frame_index == frame_count - 1:
        return round(frame_count * 1000 / fps)
    return round(frame_index * 1000 / fps)


def prepare(args: argparse.Namespace) -> Path:
    boundary_dir = args.boundary_dir(args)
    inputs = boundary_dir / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    capture = cv2.VideoCapture(str(args.video))
    if not capture.set(cv2.CAP_PROP_POS_FRAMES, args.boundary_frame):
        raise ValueError(f"Could not seek to boundary frame {args.boundary_frame}")
    ok, boundary_bgr = capture.read()
    if not ok:
        raise ValueError(f"Could not decode boundary frame {args.boundary_frame}")
    capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
    ok, first_bgr = capture.read()
    capture.release()
    if not ok:
        raise ValueError("Could not decode source frame 0")

    first_path = inputs / "first_frame.png"
    last_path = inputs / "last_frame.png"
    Image.fromarray(cv2.cvtColor(boundary_bgr, cv2.COLOR_BGR2RGB)).save(
        first_path, format="PNG", optimize=True
    )
    Image.fromarray(cv2.cvtColor(first_bgr, cv2.COLOR_BGR2RGB)).save(
        last_path, format="PNG", optimize=True
    )

    prompt = args.prompt or (
        DEFAULT_STORYBOARD_PROMPT if args.storyboard else DEFAULT_RETURN_PROMPT
    )
    body = {
        "model": args.model,
        "prompt": prompt,
        "image": [image_data_url(first_path), image_data_url(last_path)],
        "size": args.storyboard_size if args.storyboard else args.size,
        "n": 1 if args.storyboard else RETURN_FRAME_COUNT,
        "response_format": "url",
        "watermark": False,
    }
    request_path = boundary_dir / "request.json"
    write_json(request_path, body)
    timeline_boundary = int(
        args.boundary_frame * args.target_fps / args.source_fps + 0.5
    )
    write_json(
        boundary_dir / "metadata.json",
        {
            "sourceVideo": str(args.video),
            "sourceFps": args.source_fps,
            "sourceFrameCount": args.source_frame_count,
            "sourceBoundaryFrame": args.boundary_frame,
            "timelineFps": args.target_fps,
            "timelineBoundaryFrame": timeline_boundary,
            "boundaryLabelMs": boundary_label(
                args.boundary_frame, args.source_fps, args.source_frame_count
            ),
            "returnFrameCount": RETURN_FRAME_COUNT,
            "returnFps": RETURN_FPS,
            "returnDurationSeconds": 1,
            "endpointPolicy": "replace generated frames 0 and 11 with exact source frames",
            "generationLayout": (
                f"single {STORYBOARD_COLUMNS}x{STORYBOARD_ROWS} storyboard"
                if args.storyboard
                else f"{RETURN_FRAME_COUNT} separate images"
            ),
        },
    )
    return request_path


def image_payload(item: object) -> bytes | None:
    if not isinstance(item, dict):
        return None
    for key in ("b64_json", "b64"):
        if isinstance(item.get(key), str):
            return base64.b64decode(item[key])
    if isinstance(item.get("url"), str):
        with urllib.request.urlopen(item["url"], timeout=180) as response:
            return response.read()
    value = item.get("image")
    if isinstance(value, str) and value.startswith("data:"):
        return base64.b64decode(value.split(",", 1)[1])
    return None


def generate(args: argparse.Namespace) -> Path:
    boundary_dir = args.boundary_dir(args)
    request_path = boundary_dir / "request.json"
    if not request_path.is_file():
        raise ValueError(f"Request does not exist: {request_path}")
    load_env(args.env_file)
    token, secrets = credentials()
    base = os.getenv("SEEDANCE_ARK_BASE", DEFAULT_BASE).rstrip("/")
    body = request_path.read_bytes()
    request = urllib.request.Request(
        base + "/images/generations",
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=args.timeout) as response:
            value = json.loads(response.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"HTTP {error.code}: {redact(detail, secrets)}"
        ) from error

    data = value.get("data", [])
    expected_count = 1 if args.storyboard else RETURN_FRAME_COUNT
    if len(data) != expected_count:
        raise ValueError(
            f"Expected {expected_count} generated images, received {len(data)}"
        )

    generated = boundary_dir / "generated"
    if generated.exists():
        if not args.replace:
            raise FileExistsError(f"Output already exists: {generated}")
        shutil.rmtree(generated)
    generated.mkdir(parents=True)
    if args.storyboard:
        payload = image_payload(data[0])
        if payload is None:
            raise ValueError("Unrecognized storyboard image payload")
        storyboard_path = generated / "storyboard.png"
        storyboard_path.write_bytes(payload)
        with Image.open(storyboard_path) as storyboard:
            image = storyboard.convert("RGB")
            if image.width % STORYBOARD_COLUMNS or image.height % STORYBOARD_ROWS:
                raise ValueError(
                    "Storyboard dimensions must divide evenly into "
                    f"{STORYBOARD_COLUMNS}x{STORYBOARD_ROWS} cells"
                )
            cell_width = image.width // STORYBOARD_COLUMNS
            cell_height = image.height // STORYBOARD_ROWS
            for index in range(RETURN_FRAME_COUNT):
                row, column = divmod(index, STORYBOARD_COLUMNS)
                cell = image.crop(
                    (
                        column * cell_width,
                        row * cell_height,
                        (column + 1) * cell_width,
                        (row + 1) * cell_height,
                    )
                )
                cell.save(generated / f"frame_{index:04d}.png", format="PNG", optimize=True)
    else:
        for index, item in enumerate(data):
            payload = image_payload(item)
            if payload is None:
                raise ValueError(f"Unrecognized image payload at index {index}")
            raw_path = generated / f"raw_{index:04d}.bin"
            raw_path.write_bytes(payload)
            with Image.open(raw_path) as image:
                image.convert("RGB").save(
                    generated / f"frame_{index:04d}.png", format="PNG", optimize=True
                )
            raw_path.unlink()

    safe_response = {
        key: value for key, value in value.items() if key not in {"data", "usage"}
    }
    safe_response["data_count"] = len(data)
    safe_response["image_payloads"] = "downloaded locally; remote URLs redacted"
    safe_response["generationLayout"] = (
        f"single {STORYBOARD_COLUMNS}x{STORYBOARD_ROWS} storyboard sliced row-major"
        if args.storyboard
        else f"{RETURN_FRAME_COUNT} separate images"
    )
    (boundary_dir / "response.json").write_text(
        redact(safe_response, secrets) + "\n", encoding="utf-8"
    )
    return generated


def process(args: argparse.Namespace) -> Path:
    boundary_dir = args.boundary_dir(args)
    generated = boundary_dir / "generated"
    generated_paths = sorted(generated.glob("frame_*.png"))
    if len(generated_paths) != RETURN_FRAME_COUNT:
        raise ValueError(
            f"Expected {RETURN_FRAME_COUNT} generated frames, found {len(generated_paths)}"
        )

    output = boundary_dir / "processed"
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

    first = read_rgb(boundary_dir / "inputs/first_frame.png")
    last = read_rgb(boundary_dir / "inputs/last_frame.png")
    if first.size != last.size:
        raise ValueError("Endpoint frames must have identical dimensions")
    frames = []
    matte_reports = []
    for path in generated_paths:
        with Image.open(path) as image:
            frames.append(
                image.convert("RGB").resize(first.size, Image.Resampling.LANCZOS)
            )
    frames[0] = first.copy()
    frames[-1] = last.copy()

    for index, frame in enumerate(frames):
        frame.save(rgb_dir / f"frame_{index:04d}.png", format="PNG", optimize=True)

    session = new_session(args.matting_model, providers=[args.provider])
    for index, frame in enumerate(frames):
        rgb = np.asarray(frame)
        rembg_rgba = np.asarray(
            remove(rgb, session=session, alpha_matting=False)
        )
        cutout, matte_report = matte_rgb_frame(
            rgb,
            rembg_rgba,
            background_min=args.background_min,
            background_max=args.background_max,
            edge_pixels=args.edge_pixels,
        )
        Image.fromarray(cutout).save(
            rgba_dir / f"frame_{index:04d}.png", format="PNG", optimize=True
        )
        matte_reports.append({"frame": index, **matte_report})

    endpoint_policy = "frames 0 and 11 are exact source RGB endpoints and independently matted"
    if args.source_rgba_dir:
        source_frames = sorted(args.source_rgba_dir.glob("frame_*.png"))
        extraction_manifest = args.source_rgba_dir / "extraction_manifest.json"
        if extraction_manifest.is_file():
            frame_map = json.loads(extraction_manifest.read_text())["frameMap"]
            mapped = {
                item["sourceFrame"]: item["outputFrame"] for item in frame_map
            }
            timeline_boundary = mapped[args.boundary_frame]
        else:
            timeline_boundary = int(
                args.boundary_frame * args.target_fps / args.source_fps + 0.5
            )
        if not source_frames or timeline_boundary >= len(source_frames):
            raise ValueError("Source RGBA directory does not contain the boundary frame")
        shutil.copy2(source_frames[timeline_boundary], rgba_dir / "frame_0000.png")
        shutil.copy2(source_frames[0], rgba_dir / "frame_0011.png")
        endpoint_policy = "frames 0 and 11 reuse exact source RGBA frames"

    size = (
        max(1, round(first.width * args.scale)),
        max(1, round(first.height * args.scale)),
    )
    resampling = getattr(Image, "Resampling", Image)
    for source_path in sorted(rgba_dir.glob("*.png")):
        with Image.open(source_path) as source:
            frame = source.convert("RGBA").resize(size, resampling.LANCZOS)
        rgba = np.asarray(frame).copy()
        rgba[:, :, 3] = fill_enclosed_transparent(rgba[:, :, 3])
        frame = Image.fromarray(rgba)
        frame.save(png_dir / source_path.name, format="PNG", optimize=True)
        frame.save(
            webp_dir / f"{source_path.stem}.webp",
            format="WEBP",
            quality=args.webp_quality,
            method=args.webp_method,
        )

    fallback_path = boundary_dir / "fallback.json"
    fallback = json.loads(fallback_path.read_text(encoding="utf-8")) if fallback_path.is_file() else None
    write_json(
        output / "manifest.json",
        {
            "generationModel": "source_reverse_sampling" if fallback else args.model,
            "generatedFrameCount": len(generated_paths),
            "frameCount": len(frames),
            "sourceSize": list(first.size),
            "optimizedSize": list(size),
            "returnFps": RETURN_FPS,
            "returnDurationFrames": RETURN_FRAME_COUNT,
            "returnDurationSeconds": 1,
            "endpointPolicy": endpoint_policy,
            "mattingPolicy": (
                "complete RGB PNG first; border-connected rembg background and RGB range; "
                "rembg edge feather"
            ),
            "backgroundRange": [args.background_min, args.background_max],
            "edgePixels": args.edge_pixels,
            "holePolicy": "fill enclosed transparent components",
            "matteFrames": matte_reports,
            "interiorPolicy": (
                "10 exact reverse-sampled source frames"
                if fallback
                else "10 Seedream frames; stabilize only when measured drift requires it"
            ),
            "generationException": fallback,
            "webpQuality": args.webp_quality,
            "webpMethod": args.webp_method,
        },
    )
    return output


def main() -> None:
    args = parse_args()
    if not args.video.is_file():
        raise ValueError(f"Video does not exist: {args.video}")
    if not 0 <= args.boundary_frame < args.source_frame_count:
        raise ValueError(
            f"Boundary frame must be in 0..{args.source_frame_count - 1}"
        )

    if args.command == "prepare":
        print(f"request={prepare(args)}")
    elif args.command == "generate":
        print(f"generated={generate(args)}")
    elif args.command == "process":
        print(f"processed={process(args)}")
    else:
        prepare(args)
        generate(args)
        print(f"processed={process(args)}")


if __name__ == "__main__":
    try:
        main()
    except (
        ValueError,
        OSError,
        FileExistsError,
        json.JSONDecodeError,
        RuntimeError,
    ) as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(1)
