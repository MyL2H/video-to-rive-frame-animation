#!/usr/bin/env python3
"""Generate and process twelve ordered Seedream return frames for one cut point."""

from __future__ import annotations

import argparse
import base64
import io
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
from storyboard_geometry import layout, prompt_contract, validate_image, grid_overlay

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
RGB_FALLBACK_COLOR = 235
DEFAULT_TRANSPARENT_STORYBOARD_PROMPT = (
    "The input is one transparent PNG containing exactly two side-by-side endpoint "
    "references: the left panel is the first pose and the right panel is the final pose. "
    "Return exactly one high-resolution transparent PNG animation storyboard with exactly "
    "4 columns and 3 rows, for 12 equal source-aspect-ratio panels and no gutters, borders, "
    "captions, labels, or watermark. Read panels left to right and top to bottom. Panel 1 "
    "must match the left input pose and panel 12 must match the right input pose. Each panel "
    "is the complete source-frame canvas at the same normalized coordinates: keep the subject "
    "bounding box, head and feet anchors, horizontal position, vertical position, scale, and "
    "the same amount of top and bottom space as the input reference. Do not add padding, "
    "recenter, shrink, enlarge, or lay out the panels as thumbnails. Build one smooth, natural "
    "return motion with evenly spaced poses while preserving the same fully clothed, "
    "child-friendly cartoon character identity, face, clothing, body proportions, art style, "
    "lighting, camera, framing, and scale. The alpha matte must be clean: outside pixels are "
    "fully alpha 0, with no gray or white halo, fringe, spill, grid line, or semi-transparent "
    "background edge; keep only antialiased pixels that belong to the character. Use crisp "
    "high-resolution 2D animation art, clean detailed line work, sharp edges, fine facial and "
    "clothing detail, and no blur or compression artifacts. Do not zoom, crop, reframe, drift, "
    "morph, add objects, remove objects, or change the background."
)
DEFAULT_RGB_STORYBOARD_PROMPT = (
    "Return exactly one high-resolution animation storyboard containing 4 columns and 3 "
    "rows, for 12 equal source-aspect-ratio panels. Read panels left to right and top to bottom. "
    "Panel 1 must match the first input pose and panel 12 must match the second input pose. "
    "Build one smooth, natural return motion with evenly spaced poses. Preserve the same "
    "fully clothed, child-friendly cartoon character identity, face, clothing, body "
    "proportions, subject height, torso width, feet anchor, screen position, art style, "
    "lighting, camera, framing, and scale. Every panel is the complete source-frame canvas at "
    "the same normalized coordinates; keep the subject bounding box, head and feet anchors, "
    "horizontal and vertical position, scale, and top and bottom space exactly like the input "
    "references. Do not add padding, recenter, shrink, enlarge, or lay out the panels as "
    "thumbnails. The entire background of every panel must be one "
    "exact flat RGB(235,235,235) color with no gradient, texture, noise, vignette, floor, "
    "shadow, backdrop object, grid line, text, or watermark. Keep the character edge matte "
    "clean, with no gray or white halo, fringe, spill, or residual background pixels. Use "
    "crisp high-resolution 2D animation art, clean detailed line work, sharp edges, fine facial "
    "and clothing detail, and no blur or compression artifacts. Do not zoom, crop, reframe, "
    "drift, morph, add objects, or remove objects."
)
DEFAULT_RGB_SEPARATE_PROMPT = (
    "Return exactly 12 separate high-resolution images in chronological order. Build one "
    "smooth, natural return motion with evenly spaced poses. The first image must match the "
    "first input pose and the last image must match the second input pose. Preserve the same "
    "fully clothed, child-friendly cartoon character identity, face, clothing, body "
    "proportions, subject height, torso width, feet anchor, screen position, art style, "
    "lighting, camera, framing, and scale. The entire background of every image must be one "
    "exact flat RGB(235,235,235) color with no gradient, texture, noise, vignette, floor, "
    "shadow, backdrop object, text, or watermark. Use crisp high-resolution 2D animation art, "
    "clean detailed line work, sharp edges, fine facial and clothing detail, and no blur or "
    "compression artifacts. Do not zoom, crop, reframe, drift, morph, add objects, or remove "
    "objects."
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
    parser.add_argument("--size", default="1536x2304")
    parser.add_argument(
        "--generation-mode",
        choices=("auto", "transparent-storyboard", "rgb-storyboard", "separate"),
        default="auto",
    )
    parser.add_argument("--storyboard", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--storyboard-size", help="Exact canvas size; default derives equal cells from the source aspect ratio")
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

    if args.storyboard and args.generation_mode == "auto":
        args.generation_mode = "transparent-storyboard"
    if args.generation_mode == "auto":
        args.generation_mode = "transparent-storyboard"

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
    source_size = (int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)), int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)))
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
    args.storyboard_geometry = layout(source_size, args.storyboard_size)
    args.storyboard_size = '{}x{}'.format(*args.storyboard_geometry['storyboardSize'])
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


def image_data_url(path: Path, preserve_alpha: bool = False) -> str:
    with Image.open(path) as image:
        output = io.BytesIO()
        image.convert("RGBA" if preserve_alpha else "RGB").save(
            output, format="PNG", optimize=True
        )
    encoded = base64.b64encode(output.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def source_rgba_endpoints(args: argparse.Namespace) -> tuple[Path, Path]:
    if args.source_rgba_dir is None:
        raise ValueError("--source-rgba-dir is required for transparent generation")
    frames = sorted(args.source_rgba_dir.glob("frame_*.png"))
    if not frames:
        raise ValueError(f"No source RGBA frames found in {args.source_rgba_dir}")
    manifest_path = args.source_rgba_dir / "extraction_manifest.json"
    if manifest_path.is_file():
        mapped = {
            item["sourceFrame"]: item["outputFrame"]
            for item in json.loads(manifest_path.read_text())["frameMap"]
        }
        boundary_index = mapped[args.boundary_frame]
    else:
        boundary_index = int(
            args.boundary_frame * args.target_fps / args.source_fps + 0.5
        )
    if boundary_index >= len(frames):
        raise ValueError("Source RGBA directory does not contain the boundary frame")
    return frames[boundary_index], frames[0]


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

    prompt = args.prompt or {
        "transparent-storyboard": DEFAULT_TRANSPARENT_STORYBOARD_PROMPT,
        "rgb-storyboard": DEFAULT_RGB_STORYBOARD_PROMPT,
        "separate": DEFAULT_RGB_SEPARATE_PROMPT,
    }[args.generation_mode]
    if args.generation_mode != "separate":
        # Custom prompts cannot accidentally drop the source-ratio/grid contract.
        prompt += prompt_contract(args.storyboard_geometry)
    if args.generation_mode == "transparent-storyboard":
        boundary_rgba_path, first_rgba_path = source_rgba_endpoints(args)
        with Image.open(boundary_rgba_path) as boundary_rgba, Image.open(
            first_rgba_path
        ) as first_rgba:
            if boundary_rgba.size != first_rgba.size:
                raise ValueError("Source RGBA endpoint dimensions must match")
            reference = Image.new(
                "RGBA",
                (boundary_rgba.width * 2, boundary_rgba.height),
                (0, 0, 0, 0),
            )
            reference.alpha_composite(boundary_rgba.convert("RGBA"), (0, 0))
            reference.alpha_composite(
                first_rgba.convert("RGBA"), (boundary_rgba.width, 0)
            )
        reference_path = inputs / "reference_rgba.png"
        reference.save(reference_path, format="PNG", optimize=True)
        body = {
            "model": args.model,
            "prompt": prompt,
            "image": [image_data_url(reference_path, preserve_alpha=True)],
            "size": args.storyboard_size,
            "n": 1,
            "response_format": "url",
            "watermark": False,
            "background": "transparent",
            "output_format": "png",
        }
    elif args.generation_mode == "rgb-storyboard":
        body = {
            "model": args.model,
            "prompt": prompt,
            "image": [image_data_url(first_path), image_data_url(last_path)],
            "size": args.storyboard_size,
            "n": 1,
            "response_format": "url",
            "watermark": False,
            "output_format": "png",
        }
    else:
        body = {
            "model": args.model,
            "prompt": prompt,
            "image": [image_data_url(first_path), image_data_url(last_path)],
            "size": args.size,
            "n": RETURN_FRAME_COUNT,
            "response_format": "url",
            "watermark": False,
        }
    request_path = boundary_dir / "request.json"
    write_json(request_path, body)
    if args.generation_mode == "transparent-storyboard":
        fallback_body = {
            **body,
            "prompt": DEFAULT_RGB_STORYBOARD_PROMPT
            + (" Additional motion constraints: " + args.prompt if args.prompt else "")
            + prompt_contract(args.storyboard_geometry),
            "image": [image_data_url(first_path), image_data_url(last_path)],
        }
        fallback_body.pop("background", None)
        write_json(boundary_dir / "request_rgb_fallback.json", fallback_body)
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
            "generationMode": args.generation_mode,
            "storyboardGeometry": args.storyboard_geometry,
            "generationLayout": (
                f"single {STORYBOARD_COLUMNS}x{STORYBOARD_ROWS} transparent storyboard"
                if args.generation_mode == "transparent-storyboard"
                else f"single {STORYBOARD_COLUMNS}x{STORYBOARD_ROWS} RGB(235,235,235) storyboard"
                if args.generation_mode == "rgb-storyboard"
                else f"{RETURN_FRAME_COUNT} separate RGB(235,235,235) images"
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


def post_image_request(
    request_path: Path,
    base: str,
    token: str,
    secrets: tuple[str, ...],
    timeout: float,
) -> dict:
    request = urllib.request.Request(
        base + "/images/generations",
        data=request_path.read_bytes(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"HTTP {error.code}: {redact(detail, secrets)}"
        ) from error


def image_alpha_info(payload: bytes) -> dict[str, object]:
    with Image.open(io.BytesIO(payload)) as image:
        rgba = image.convert("RGBA")
        alpha = rgba.getchannel("A")
        extrema = alpha.getextrema()
        transparent_pixels = sum(value < 8 for value in alpha.getdata())
        return {
            "format": image.format,
            "mode": image.mode,
            "size": list(image.size),
            "alphaExtrema": list(extrema),
            "transparentPixelCount": transparent_pixels,
            "hasTransparency": extrema[0] < 255 and transparent_pixels > 0,
        }


def file_alpha_info(path: Path) -> dict[str, object]:
    with Image.open(path) as image:
        rgba = image.convert("RGBA")
        alpha = rgba.getchannel("A")
        extrema = alpha.getextrema()
        transparent_pixels = sum(value < 8 for value in alpha.getdata())
        return {
            "format": image.format,
            "mode": image.mode,
            "size": list(image.size),
            "alphaExtrema": list(extrema),
            "transparentPixelCount": transparent_pixels,
            "hasTransparency": extrema[0] < 255 and transparent_pixels > 0,
        }


def validate_saved_storyboard(args: argparse.Namespace, path: Path, spec: dict) -> None:
    endpoints = None
    if args.source_rgba_dir:
        endpoints = [Image.open(p).convert('RGBA') for p in source_rgba_endpoints(args)]
    with Image.open(path) as image:
        report = validate_image(image, spec, endpoints)
        grid_overlay(image, spec).save(path.with_name('storyboard_grid_overlay.png'))
    write_json(path.with_name('layout_validation.json'), {'geometry': spec, **report})
    if not report['automaticPassed']:
        raise ValueError('Storyboard geometry rejected; do not slice, stretch or stabilize it: '
                         + '; '.join(report['errors']))


def generate(args: argparse.Namespace) -> Path:
    boundary_dir = args.boundary_dir(args)
    request_path = boundary_dir / "request.json"
    if not request_path.is_file():
        raise ValueError(f"Request does not exist: {request_path}")
    load_env(args.env_file)
    token, secrets = credentials()
    base = os.getenv("SEEDANCE_ARK_BASE", DEFAULT_BASE).rstrip("/")
    fallback_path = boundary_dir / "request_rgb_fallback.json"
    request_paths = [request_path]
    if fallback_path.is_file():
        request_paths.append(fallback_path)

    attempts = []
    value = None
    request_body = None
    request_mode = None
    provider_info = None
    for candidate_path in request_paths:
        candidate_body = json.loads(candidate_path.read_text(encoding="utf-8"))
        try:
            candidate_value = post_image_request(
                candidate_path, base, token, secrets, args.timeout
            )
            candidate_data = candidate_value.get("data", [])
            expected_count = int(candidate_body.get("n", RETURN_FRAME_COUNT))
            if len(candidate_data) != expected_count:
                raise ValueError(
                    f"Expected {expected_count} generated images, received {len(candidate_data)}"
                )
            if expected_count == 1:
                payload = image_payload(candidate_data[0])
                if payload is None:
                    raise ValueError("Unrecognized storyboard image payload")
                info = image_alpha_info(payload)
                if candidate_body.get("background") == "transparent" and not info["hasTransparency"]:
                    raise ValueError("Transparent request returned no alpha transparency")
            else:
                payload = None
                info = None
            value = candidate_value
            request_body = candidate_body
            request_mode = (
                "transparent-storyboard"
                if candidate_body.get("background") == "transparent"
                else "rgb-storyboard"
                if int(candidate_body.get("n", RETURN_FRAME_COUNT)) == 1
                else "separate"
            )
            provider_info = info
            break
        except (RuntimeError, ValueError) as error:
            attempts.append(
                {
                    "request": candidate_path.name,
                    "error": str(error),
                }
            )
    if value is None or request_body is None:
        raise RuntimeError(
            "All Seedream generation attempts failed: "
            + json.dumps(attempts, ensure_ascii=False)
        )

    data = value.get("data", [])
    expected_count = int(request_body.get("n", RETURN_FRAME_COUNT))

    generated = boundary_dir / "generated"
    if generated.exists():
        if not args.replace:
            raise FileExistsError(f"Output already exists: {generated}")
        shutil.rmtree(generated)
    generated.mkdir(parents=True)
    if expected_count == 1:
        payload = image_payload(data[0])
        if payload is None:
            raise ValueError("Unrecognized storyboard image payload")
        storyboard_path = generated / "storyboard.png"
        storyboard_path.write_bytes(payload)
        spec = layout(tuple(args.storyboard_geometry['sourceSize']), request_body['size'])
        validate_saved_storyboard(args, storyboard_path, spec)
        with Image.open(storyboard_path) as storyboard:
            has_alpha = provider_info is not None and provider_info["hasTransparency"]
            image = storyboard.convert("RGBA" if has_alpha else "RGB")
            cell_width, cell_height = spec['cellSize']
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
    safe_response["generationMode"] = request_mode
    safe_response["providerImage"] = provider_info
    safe_response["generationLayout"] = (
        f"single {STORYBOARD_COLUMNS}x{STORYBOARD_ROWS} storyboard sliced row-major"
        if expected_count == 1
        else f"{RETURN_FRAME_COUNT} separate images"
    )
    safe_response["attempts"] = attempts
    (boundary_dir / "response.json").write_text(
        redact(safe_response, secrets) + "\n", encoding="utf-8"
    )
    return generated


def process(args: argparse.Namespace) -> Path:
    boundary_dir = args.boundary_dir(args)
    generated = boundary_dir / "generated"
    if (generated / 'storyboard.png').is_file():
        request_body = json.loads((boundary_dir / 'request.json').read_text())
        spec = layout(tuple(args.storyboard_geometry['sourceSize']), request_body['size'])
        validate_saved_storyboard(args, generated / 'storyboard.png', spec)
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
    provider_reports = []
    source_rgb_frames = []
    source_rgba_frames = []
    for index, path in enumerate(generated_paths):
        with Image.open(path) as image:
            if image.width * first.height != image.height * first.width:
                raise ValueError(f'Panel {index} does not match source aspect ratio; refusing anisotropic resize')
            rgba = image.convert("RGBA").resize(first.size, Image.Resampling.LANCZOS)
            info = file_alpha_info(path)
        rgb_frame = rgba.convert("RGB")
        if index == 0:
            rgb_frame = first.copy()
        elif index == len(generated_paths) - 1:
            rgb_frame = last.copy()
        source_rgb_frames.append(rgb_frame)
        source_rgba_frames.append(rgba)
        provider_reports.append({"frame": index, **info})

    # Keep every complete RGB PNG on disk before any RGB fallback matting.
    for index, frame in enumerate(source_rgb_frames):
        frame.save(rgb_dir / f"frame_{index:04d}.png", format="PNG", optimize=True)

    matte_reports = []
    provider_alpha_frames = []
    session = None
    for index, rgba in enumerate(source_rgba_frames):
        provider_info = provider_reports[index]
        if provider_info["hasTransparency"]:
            provider_alpha_frames.append(index)
        if args.source_rgba_dir and index in {0, len(source_rgba_frames) - 1}:
            continue
        if provider_info["hasTransparency"]:
            repaired = np.asarray(rgba).copy()
            repaired[:, :, 3] = fill_enclosed_transparent(repaired[:, :, 3])
            cutout = repaired
        else:
            if session is None:
                session = new_session(args.matting_model, providers=[args.provider])
            rgb_path = rgb_dir / f"frame_{index:04d}.png"
            with Image.open(rgb_path) as saved_rgb:
                rgb = np.asarray(saved_rgb.convert("RGB"))
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
            matte_reports.append({"frame": index, **matte_report})
        Image.fromarray(np.asarray(cutout, dtype=np.uint8)).save(
            rgba_dir / f"frame_{index:04d}.png", format="PNG", optimize=True
        )

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
            "frameCount": len(source_rgb_frames),
            "sourceSize": list(first.size),
            "optimizedSize": list(size),
            "returnFps": RETURN_FPS,
            "returnDurationFrames": RETURN_FRAME_COUNT,
            "returnDurationSeconds": 1,
            "endpointPolicy": endpoint_policy,
            "generationMode": (
                "source-reverse-sampling"
                if fallback
                else "transparent-provider-alpha"
                if all(
                    provider_reports[index]["hasTransparency"]
                    for index in range(1, RETURN_FRAME_COUNT - 1)
                )
                else "rgb-storyboard"
            ),
            "providerAlpha": {
                "frameIndices": provider_alpha_frames,
                "frameCount": len(provider_alpha_frames),
                "frames": provider_reports,
            },
            "mattingPolicy": {
                "transparent": (
                    "preserve provider alpha; fill enclosed transparent components; no rembg"
                ),
                "rgbFallback": (
                    "save complete RGB PNG first; border-connected rembg background and "
                    "RGB(235,235,235) range 230..241; rembg edge feather"
                ),
                "rgbMattedFrameIndices": [
                    item["frame"] for item in matte_reports
                ],
            },
            "fallbackBackgroundColor": [
                RGB_FALLBACK_COLOR,
                RGB_FALLBACK_COLOR,
                RGB_FALLBACK_COLOR,
            ],
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
