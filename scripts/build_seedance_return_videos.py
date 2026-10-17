#!/usr/bin/env python3
"""Generate first-frame to last-frame return clips with Volcengine Ark."""

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import cv2
from PIL import Image


DEFAULT_BASE = "https://ark.cn-beijing.volces.com/api/v3"
DEFAULT_MODEL = "doubao-seedance-2-5-260628"
DEFAULT_RETURN_PROMPT = (
    "Create one continuous four-second transition from the supplied first frame "
    "to the supplied last frame. Keep the same subject, identity, clothing, art or "
    "photographic style, scene, lighting, camera angle, framing, and background "
    "exactly consistent. Begin at the first image and perform a natural, smooth, "
    "physically coherent return motion toward the final pose. End at the supplied "
    "last image. Use one continuous shot with no cuts, zoom, camera movement, "
    "morphing, extra objects, text, watermark, or background change. Preserve "
    "detail, line work, and colors throughout."
)


def load_env(path: Path) -> None:
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        os.environ.setdefault(name.strip(), value.strip())


def auth_headers() -> tuple[dict[str, str], tuple[str, ...]]:
    ark_key = os.getenv("ARK_API_KEY", "")
    if ark_key:
        return (
            {
                "Authorization": f"Bearer {ark_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            (ark_key,),
        )
    key_id = os.getenv("SEEDANCE_API_KEY_ID", "")
    key_secret = os.getenv("SEEDANCE_API_KEY_SECRET", "")
    if not key_id or not key_secret:
        raise ValueError(
            "SEEDANCE_API_KEY_ID and SEEDANCE_API_KEY_SECRET are required"
        )
    mode = os.getenv("SEEDANCE_ARK_AUTH_MODE", "bearer-secret")
    if mode == "bearer-secret":
        authorization = f"Bearer {key_secret}"
    elif mode == "bearer-pair":
        authorization = f"Bearer {key_id}.{key_secret}"
    elif mode == "bearer-id":
        authorization = f"Bearer {key_id}"
    else:
        raise ValueError(f"Unsupported SEEDANCE_ARK_AUTH_MODE: {mode}")
    return (
        {
            "Authorization": authorization,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        (key_id, key_secret),
    )


def redact(text: str, credentials: tuple[str, ...]) -> str:
    for value in credentials:
        text = text.replace(value, "<redacted>")
    return text


def request_json(
    path: str,
    *,
    env_file: Path,
    method: str = "GET",
    body: dict | None = None,
    timeout: float = 120,
) -> dict:
    load_env(env_file)
    headers, credentials = auth_headers()
    base = os.getenv("SEEDANCE_ARK_BASE", DEFAULT_BASE).rstrip("/")
    payload = (
        json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if body is not None
        else None
    )
    request = urllib.request.Request(
        base + path, data=payload, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:4000]
        raise RuntimeError(
            f"HTTP {error.code}: {redact(detail, credentials)}"
        ) from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"Network error: {error.reason}") from error
    try:
        return json.loads(raw)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"Invalid JSON response: {redact(raw[:1000], credentials)}") from error


def boundary_frame(
    video: Path, frame_index: int
) -> tuple[object, int, int, int, float]:
    capture = cv2.VideoCapture(str(video))
    if not capture.isOpened():
        raise ValueError(f"OpenCV could not open video: {video}")
    fps = float(capture.get(cv2.CAP_PROP_FPS))
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if fps <= 0 or frame_count <= 0:
        raise ValueError(f"Invalid video metadata: fps={fps}, frames={frame_count}")
    if not 0 <= frame_index < frame_count:
        raise ValueError(f"Boundary frame {frame_index} is outside 0..{frame_count - 1}")
    if not capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index):
        raise ValueError(f"Could not seek to frame {frame_index}")
    ok, frame_bgr = capture.read()
    capture.release()
    if not ok:
        raise ValueError(f"Could not decode frame {frame_index}")
    frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
    return frame_rgb, width, height, frame_count, fps


def image_data_url(path: Path) -> str:
    with Image.open(path) as image:
        image.convert("RGB").save(path, format="PNG", optimize=True)
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def boundary_label(frame_index: int, fps: float, frame_count: int) -> int:
    if frame_index == frame_count - 1:
        return round(frame_count * 1000 / fps)
    return round(frame_index * 1000 / fps)


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def prepare(args: argparse.Namespace) -> Path:
    boundary_dir = args.boundary_dir(args.output_root)
    inputs = boundary_dir / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)

    first_rgb, width, height, frame_count, fps = boundary_frame(
        args.video, args.boundary_frame
    )
    first_path = inputs / "first_frame.png"
    last_path = inputs / "last_frame.png"
    Image.fromarray(first_rgb).save(first_path, format="PNG", optimize=True)

    if args.boundary_frame == 0:
        shutil.copy2(first_path, last_path)
    else:
        last_rgb, _, _, _, _ = boundary_frame(args.video, 0)
        Image.fromarray(last_rgb).save(last_path, format="PNG", optimize=True)

    body = {
        "model": args.model,
        "content": [
            {
                "type": "text",
                "text": args.prompt or os.getenv("SEEDANCE_RETURN_PROMPT", DEFAULT_RETURN_PROMPT),
            },
            {
                "type": "image_url",
                "role": "first_frame",
                "image_url": {"url": image_data_url(first_path)},
            },
            {
                "type": "image_url",
                "role": "last_frame",
                "image_url": {"url": image_data_url(last_path)},
            },
        ],
        "duration": 4,
        "ratio": "adaptive",
        "generate_audio": False,
        "watermark": False,
        "output_format": "mp4",
    }
    request_path = boundary_dir / "request.json"
    write_json(request_path, body)
    write_json(
        boundary_dir / "metadata.json",
        {
            "sourceVideo": str(args.video),
            "sourceSize": [width, height],
            "sourceFps": fps,
            "sourceFrameCount": frame_count,
            "boundaryFrame": args.boundary_frame,
            "boundaryFrameMs": round(args.boundary_frame * 1000 / fps),
            "boundaryLabelMs": boundary_label(args.boundary_frame, fps, frame_count),
            "returnDurationSeconds": 1,
            "seedanceDurationSeconds": 4,
        },
    )
    return request_path


def task_id(response: dict) -> str:
    for key in ("id", "task_id", "taskId"):
        value = response.get(key)
        if isinstance(value, str) and value:
            return value
    raise RuntimeError(f"Task response has no task ID: {response}")


def submit(args: argparse.Namespace) -> None:
    boundary_dir = args.boundary_dir(args.output_root)
    body = json.loads((boundary_dir / "request.json").read_text(encoding="utf-8"))
    response = request_json(
        "/contents/generations/tasks",
        env_file=args.env_file,
        method="POST",
        body=body,
        timeout=args.timeout,
    )
    response["_task_id"] = task_id(response)
    write_json(boundary_dir / "task.json", response)
    print(f"task_id={response['_task_id']}")


def load_task_id(args: argparse.Namespace) -> str:
    task = json.loads(
        (args.boundary_dir(args.output_root) / "task.json").read_text(encoding="utf-8")
    )
    return task["_task_id"]


def status(args: argparse.Namespace) -> dict:
    boundary_dir = args.boundary_dir(args.output_root)
    response = request_json(
        "/contents/generations/tasks/" + urllib.parse.quote(load_task_id(args), safe=""),
        env_file=args.env_file,
        timeout=args.timeout,
    )
    write_json(boundary_dir / "status.json", response)
    print(json.dumps({"id": response.get("id"), "status": response.get("status")}))
    return response


def find_video_url(value: object) -> str | None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key.lower() in {"video_url", "video", "url"} and isinstance(child, str):
                if child.startswith(("http://", "https://")):
                    return child
            found = find_video_url(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = find_video_url(child)
            if found:
                return found
    return None


def download(args: argparse.Namespace) -> None:
    boundary_dir = args.boundary_dir(args.output_root)
    response = json.loads((boundary_dir / "status.json").read_text(encoding="utf-8"))
    video_url = find_video_url(response)
    if not video_url:
        raise RuntimeError(f"Completed task response has no video URL: {response}")
    output = boundary_dir / "return_4s.mp4"
    with urllib.request.urlopen(video_url, timeout=args.timeout) as remote:
        output.write_bytes(remote.read())
    print(f"video={output}")


def wait_and_download(args: argparse.Namespace) -> None:
    deadline = time.monotonic() + args.wait_timeout
    while True:
        response = status(args)
        state = str(response.get("status", "")).lower()
        if state in {"succeeded", "success", "completed", "complete"}:
            download(args)
            return
        if state in {"failed", "cancelled", "canceled", "error"}:
            raise RuntimeError(f"Generation failed: {response}")
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Generation did not finish within {args.wait_timeout}s")
        time.sleep(args.poll_interval)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--boundary-frame", type=int, required=True)
    parser.add_argument("--env-file", type=Path, default=Path(".env.seedance"))
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--prompt")
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--wait-timeout", type=float, default=600)
    parser.add_argument("--poll-interval", type=float, default=5)
    parser.add_argument("command", choices=("prepare", "submit", "status", "download", "run"))
    args = parser.parse_args()

    def boundary_dir(output_root: Path) -> Path:
        fps = 24.0
        frame_count = 0
        capture = cv2.VideoCapture(str(args.video))
        if capture.isOpened():
            fps = float(capture.get(cv2.CAP_PROP_FPS))
            frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        capture.release()
        label = boundary_label(args.boundary_frame, fps, frame_count)
        return output_root / f"return_{label}ms"

    args.boundary_dir = boundary_dir
    return args


def main() -> None:
    args = parse_args()
    if not args.video.is_file():
        raise ValueError(f"Video does not exist: {args.video}")
    if args.boundary_frame < 0:
        raise ValueError("--boundary-frame must be non-negative")

    if args.command == "prepare":
        request_path = prepare(args)
        print(f"request={request_path}")
    elif args.command == "submit":
        submit(args)
    elif args.command == "status":
        status(args)
    elif args.command == "download":
        download(args)
    elif args.command == "run":
        prepare(args)
        submit(args)
        wait_and_download(args)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, json.JSONDecodeError, RuntimeError, TimeoutError) as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(1)
