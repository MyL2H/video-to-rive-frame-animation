#!/usr/bin/env python3
"""Generate complete four-second Seedance returns for later twelve-frame sampling.

prepare is offline. submit/poll/download/run use the configured Ark API. A POST
with an uncertain outcome is never retried automatically; preserve its private
pending marker and reconcile it with the provider before any new submission.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import cv2
from PIL import Image


DEFAULT_BASE = "https://ark.cn-beijing.volces.com/api/v3"
DEFAULT_MODEL = "doubao-seedance-2-5-260628"
REQUEST_SECONDS = 4
SUCCESS = {"succeeded", "success", "completed", "complete"}
FAILED = {"failed", "cancelled", "canceled", "error", "expired"}
KNOWN_STATES = SUCCESS | FAILED | {"queued", "running", "pending", "processing"}
DEFINITE_REJECTION_CODES = {400, 401, 403, 404, 405, 413, 415, 422, 429}
DEFAULT_PROMPT = (
    "Create one continuous 4-second animation using the supplied first_frame and "
    "last_frame images. Start in the exact first-image pose and return naturally "
    "to the exact last-image pose. Distribute the movement smoothly and evenly "
    "across the entire four seconds, reaching the final pose at the end of the "
    "fourth second. Do not finish early and hold the last pose. Preserve the same "
    "subject identity, anatomy, body proportions, face, clothing, colors, materials, "
    "and visual style. Keep all held and attached props continuously present and "
    "consistent; only the pose and return motion may change. Use a locked camera "
    "and preserve the full source frame, its aspect ratio, and its surrounding "
    "empty space. Match the subject's visual scale, head and torso size, screen "
    "position, and feet contact position to the supplied reference images. Preserve "
    "their top, bottom, and side margins. Keep the entire subject and every prop "
    "inside the frame throughout. Do not zoom, crop, reframe, rescale the subject, "
    "move the camera, cut, change scenes, or add or remove objects. Use a plain, "
    "flat RGB(235,235,235) background without shadows, gradients, texture, text, "
    "captions, or watermarks. Preserve clean subject edges and coherent motion. "
    "Output one ordinary animation video, never a sprite sheet, collage, or grid."
)


class ApiRejectedError(RuntimeError):
    """The server explicitly rejected the request before accepting a generation."""

    def __init__(self, http_status: int):
        self.http_status = http_status
        super().__init__(f"Seedance API HTTP {http_status}; request rejected, no automatic retry performed.")


def load_env(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(name.strip(), value)


def auth_headers() -> dict[str, str]:
    ark_key = os.getenv("ARK_API_KEY", "")
    if ark_key:
        authorization = f"Bearer {ark_key}"
    else:
        key_id = os.getenv("SEEDANCE_API_KEY_ID", "")
        secret = os.getenv("SEEDANCE_API_KEY_SECRET", "")
        if not key_id or not secret:
            raise ValueError("Configure ARK_API_KEY or the Seedance API key pair.")
        mode = os.getenv("SEEDANCE_ARK_AUTH_MODE", "bearer-secret")
        values = {"bearer-secret": secret, "bearer-pair": f"{key_id}.{secret}",
                  "bearer-id": key_id}
        if mode not in values:
            raise ValueError("Unsupported SEEDANCE_ARK_AUTH_MODE.")
        authorization = f"Bearer {values[mode]}"
    return {"Authorization": authorization, "Content-Type": "application/json",
            "Accept": "application/json"}


def request_json(path: str, *, env_file: Path, method: str = "GET",
                 body: dict | None = None, timeout: float = 120) -> dict:
    load_env(env_file)
    headers = auth_headers()
    base = os.getenv("SEEDANCE_ARK_BASE", DEFAULT_BASE).rstrip("/")
    payload = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    request = urllib.request.Request(base + path, data=payload, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as error:
        # Never print response bodies: they can echo credentials or signed URLs.
        if error.code in DEFINITE_REJECTION_CODES:
            raise ApiRejectedError(error.code) from None
        raise RuntimeError(f"Seedance API HTTP {error.code}; no automatic retry performed.") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise RuntimeError("Seedance network request failed; no automatic retry performed.") from None
    try:
        result = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise RuntimeError("Seedance API returned invalid JSON.") from None
    if not isinstance(result, dict):
        raise RuntimeError("Seedance API returned an unexpected response shape.")
    return result


def write_json(path: Path, value: dict, *, private: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".json-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.chmod(temporary, 0o600 if private else 0o644)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            value.update(chunk)
    return value.hexdigest()


def inspect_source(video: Path, boundary: int) -> tuple[dict, object, object]:
    capture = cv2.VideoCapture(str(video))
    if not capture.isOpened():
        raise ValueError("OpenCV could not open the source video.")
    fps = float(capture.get(cv2.CAP_PROP_FPS))
    first = cut = None
    count = 0
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            if count == 0:
                first = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            if count == boundary:
                cut = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            count += 1
    finally:
        capture.release()
    if fps <= 0 or first is None:
        raise ValueError("The source has invalid FPS or no decoded frames.")
    if cut is None:
        raise ValueError(f"Boundary frame must be within 0..{count - 1}.")
    label = round((count if boundary == count - 1 else boundary) * 1000 / fps)
    info = {"sourceVideo": str(video.resolve()), "sourceSha256": digest(video),
            "sourceSize": [int(first.shape[1]), int(first.shape[0])],
            "sourceFps": fps, "sourceFrameCount": count, "boundaryFrame": boundary,
            "boundaryFrameMs": round(boundary * 1000 / fps), "boundaryLabelMs": label}
    return info, cut, first


def initialize(args: argparse.Namespace) -> None:
    if args.boundary_frame < 0 or not args.video.is_file():
        raise ValueError("Provide an existing --video and a non-negative --boundary-frame.")
    args.source_info, args.first_rgb, args.last_rgb = inspect_source(args.video, args.boundary_frame)
    args.boundary_dir = args.output_root / f"return_{args.source_info['boundaryLabelMs']}ms"


def image_data_url(path: Path) -> str:
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def prepare(args: argparse.Namespace) -> Path:
    directory = args.boundary_dir
    metadata = {**args.source_info, "model": args.model,
                "seedanceDurationSeconds": REQUEST_SECONDS, "sampleFrameCount": 12,
                "returnFps": 12, "returnDurationSeconds": 1,
                "samplingPolicy": "round(i*(decodedFrameCount-1)/11), i=0..11; full clip inclusive endpoints",
                "retentionPolicy": "keep the complete generated video; never trim to the first second",
                "endpointPolicy": "replace sampled endpoints with exact source RGBA frames during processing"}
    prompt = args.prompt or DEFAULT_PROMPT
    metadata["promptSha256"] = hashlib.sha256(prompt.encode()).hexdigest()
    saved = directory / "metadata.json"
    if saved.exists():
        old = json.loads(saved.read_text())
        for key in ("sourceSha256", "boundaryFrame", "model", "promptSha256"):
            if old.get(key) != metadata[key]:
                raise ValueError("Output already belongs to different inputs or settings; use a new --output-root.")
        if (directory / "request.json").is_file() and (directory / "prompt.txt").is_file():
            return directory / "request.json"
    if (directory / ".task.json").exists() or (directory / ".submit_pending.json").exists():
        raise RuntimeError("Cannot recreate an incomplete request after submission; inspect the existing private task files.")
    inputs = directory / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    images = []
    for name, pixels in (("first_frame", args.first_rgb), ("last_frame", args.last_rgb)):
        path = inputs / f"{name}.png"
        Image.fromarray(pixels).convert("RGB").save(path, format="PNG", optimize=True)
        images.append({"type": "image_url", "role": name,
                       "image_url": {"url": image_data_url(path)}})
    body = {"model": args.model, "content": [{"type": "text", "text": prompt}, *images],
            "duration": REQUEST_SECONDS, "ratio": "adaptive", "generate_audio": False,
            "watermark": False, "output_format": "mp4"}
    write_json(directory / "request.json", body)
    (directory / "prompt.txt").write_text(prompt + "\n", encoding="utf-8")
    write_json(saved, metadata)
    return directory / "request.json"


def task_id(response: dict) -> str:
    for key in ("_task_id", "id", "task_id", "taskId"):
        value = response.get(key)
        if isinstance(value, str) and value:
            return value
    raise RuntimeError("Seedance task response has no task ID; inspect the private submission response.")


def submit(args: argparse.Namespace) -> None:
    request_path = prepare(args)
    private = args.boundary_dir / ".task.json"
    if private.exists():
        task_id(json.loads(private.read_text()))
        print("Reusing the existing Seedance task; no new submission.")
        return
    pending = args.boundary_dir / ".submit_pending.json"
    if pending.exists():
        raise RuntimeError("A previous submission has an uncertain outcome. Reconcile its private pending marker with the provider before resubmitting.")
    # Local failures cannot have created a remote task. Validate before recording
    # a possible submission, so fixing credentials does not require manual repair.
    load_env(args.env_file)
    auth_headers()
    body = json.loads(request_path.read_text())
    request_hash = digest(request_path)
    try:
        fd = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise RuntimeError("A previous submission has an uncertain outcome. Reconcile its private pending marker with the provider before resubmitting.") from None
    with os.fdopen(fd, "w") as stream:
        json.dump({"requestSha256": request_hash, "state": "submission_started"}, stream)
    try:
        response = request_json("/contents/generations/tasks", env_file=args.env_file,
                                method="POST", body=body, timeout=args.timeout)
    except ApiRejectedError as error:
        # Persist only safe fields. An explicit later invocation may resubmit
        # after correcting the rejection; uncertain transport outcomes stay locked.
        history_path = args.boundary_dir / "submission_rejections.json"
        history = json.loads(history_path.read_text()) if history_path.exists() else {"attempts": []}
        history["attempts"].append({"state": "rejected", "httpStatus": error.http_status,
                                    "requestSha256": request_hash, "recordedAtUnix": time.time()})
        write_json(history_path, history)
        pending.unlink()
        raise
    # Save even malformed responses privately; never lose evidence of a submitted request.
    write_json(private, response, private=True)
    response["_task_id"] = task_id(response)
    write_json(private, response, private=True)
    pending.unlink()
    print("Seedance task submitted; private task record saved.")


def poll(args: argparse.Namespace) -> dict:
    task = json.loads((args.boundary_dir / ".task.json").read_text())
    response = request_json("/contents/generations/tasks/" + urllib.parse.quote(task_id(task), safe=""),
                            env_file=args.env_file, timeout=args.timeout)
    write_json(args.boundary_dir / ".status_private.json", response, private=True)
    state = str(response.get("status", "")).lower()
    # Public status is an allowlist, not a redacted copy of the private response.
    public = {"status": state if state in KNOWN_STATES else "unknown",
              "requestedDurationSeconds": REQUEST_SECONDS}
    write_json(args.boundary_dir / "generation_status.json", public)
    print(json.dumps(public))
    if state in FAILED:
        raise RuntimeError("Seedance task failed; inspect .status_private.json for provider details. No automatic regeneration performed.")
    return response


def find_video_url(value: object) -> str | None:
    if isinstance(value, dict):
        # Do not accidentally select thumbnails or source-image URLs.
        for key in ("video_url", "videoUrl"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.startswith("https://"):
                return candidate
        for child in value.values():
            result = find_video_url(child)
            if result:
                return result
    elif isinstance(value, list):
        for child in value:
            result = find_video_url(child)
            if result:
                return result
    return None


def download(args: argparse.Namespace) -> Path:
    output = args.boundary_dir / "generated_full.mp4"
    if output.is_file() and output.stat().st_size > 0:
        print("Reusing the complete downloaded video; no trimming.")
        return output
    response = json.loads((args.boundary_dir / ".status_private.json").read_text())
    if str(response.get("status", "")).lower() not in SUCCESS:
        raise RuntimeError("Only a succeeded task can be downloaded; poll the existing task first.")
    url = find_video_url(response)
    if not url:
        raise RuntimeError("Succeeded task has no HTTPS video URL; inspect its private status.")
    temporary = output.with_suffix(".download")
    try:
        with urllib.request.urlopen(url, timeout=args.timeout) as remote, temporary.open("wb") as stream:
            while chunk := remote.read(1024 * 1024):
                stream.write(chunk)
        if temporary.stat().st_size == 0:
            raise RuntimeError("Generated video download was empty.")
        temporary.replace(output)
    except (urllib.error.URLError, TimeoutError, OSError):
        raise RuntimeError("Video download failed; rerun download for the existing task. No new generation was submitted.") from None
    finally:
        temporary.unlink(missing_ok=True)
    print(f"Saved complete generated_full.mp4 ({output.stat().st_size} bytes); no trimming.")
    return output


def run(args: argparse.Namespace) -> None:
    prepare(args)
    if (args.boundary_dir / "generated_full.mp4").is_file():
        download(args)
        return
    submit(args)
    deadline = time.monotonic() + args.wait_timeout
    while True:
        response = poll(args)
        if str(response.get("status", "")).lower() in SUCCESS:
            download(args)
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Waiting timed out; run or poll again to reuse the existing task.")
        time.sleep(min(args.poll_interval, remaining))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--boundary-frame", type=int, required=True)
    parser.add_argument("--env-file", type=Path, default=Path(".env.seedance"))
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--prompt", help="Override the saved prompt; requires a new output directory after preparation.")
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--wait-timeout", type=float, default=600)
    parser.add_argument("--poll-interval", type=float, default=10)
    parser.add_argument("command", choices=("prepare", "submit", "poll", "download", "run"))
    args = parser.parse_args()
    if min(args.timeout, args.wait_timeout, args.poll_interval) <= 0:
        parser.error("Timeouts and poll interval must be positive.")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", args.model):
        parser.error("Model must be an API model or endpoint identifier.")
    initialize(args)
    if args.command == "prepare":
        prepare(args)
        print("Prepared exact RGB endpoints and the complete four-second request; no API call.")
    else:
        {"submit": submit, "poll": poll, "download": download, "run": run}[args.command](args)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, RuntimeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(1)
