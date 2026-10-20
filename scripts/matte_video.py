#!/usr/bin/env python3
"""Matte every original video frame, then save a verified lossless alpha master.

BiRefNet-matting is an image model applied independently to every video frame.
It does not provide temporal propagation or tracking. The complete master can
subsequently be sampled by extract_alpha_video_frames.py.

Dependencies and official model files must already be installed locally. This
helper never installs packages, downloads weights, or calls a hosted service.
"""

from __future__ import annotations

import argparse
from fractions import Fraction
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Callable

import imageio_ffmpeg
import numpy as np
from PIL import Image

from extract_alpha_video_frames import frames, inspect_video, sha256, validate_pair


def repository_info(path: Path) -> dict:
    def git(*args: str) -> str | None:
        result = subprocess.run(["git", "-C", str(path), *args],
                                capture_output=True, text=True)
        return result.stdout.strip() if result.returncode == 0 else None
    status = git("status", "--porcelain", "--untracked-files=no")
    return {
        "path": str(path),
        "commit": git("rev-parse", "HEAD"),
        "trackedFilesModified": bool(status) if status is not None else None,
    }


def load_model_hook(specification: str) -> tuple[Callable, dict]:
    """Explicit opt-in for a separately verified local model implementation hook."""
    try:
        filename, function_name = specification.rsplit(":", 1)
    except ValueError as error:
        raise ValueError("--model-hook must be /absolute/path.py:function") from error
    path = Path(filename).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"Model hook does not exist: {path}")
    spec = importlib.util.spec_from_file_location("_video_matte_model_hook", path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Cannot load model hook: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    hook = getattr(module, function_name, None)
    if not callable(hook):
        raise ValueError(f"Model hook function is not callable: {specification}")
    return hook, {"path": str(path), "sha256": sha256(path), "function": function_name}


class BiRefNetMattingBackend:
    """Reusable official BiRefNet model plus Photoroom foreground estimation."""

    def __init__(self, model_repo: Path, checkpoint: Path, *, device: str = "auto",
                 inference_size: tuple[int, int] = (1024, 1024), threads: int = 2,
                 model_hook: Callable | None = None, hook_provenance: dict | None = None,
                 checkpoint_revision: str | None = None, use_mps_deform: bool = True):
        started = time.perf_counter()
        model_repo, checkpoint = model_repo.resolve(), checkpoint.resolve()
        if not (model_repo / "models/birefnet.py").is_file():
            raise ValueError(f"Not an official BiRefNet checkout: {model_repo}")
        if not checkpoint.is_file():
            raise ValueError(f"Checkpoint does not exist: {checkpoint}")
        if threads < 1 or any(value < 32 or value % 32 for value in inference_size):
            raise ValueError("Use positive threads and inference dimensions divisible by 32")
        # This must be set before importing torch. The separately verified MPS
        # deform-convolution implementation can be disabled for reference runs.
        os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
        import torch
        import torch.nn.functional as functional
        from torchvision import transforms
        from safetensors.torch import load_file

        if device == "auto":
            device = ("cuda" if torch.cuda.is_available() else
                      "mps" if torch.backends.mps.is_available() else "cpu")
        if device == "cuda" and not torch.cuda.is_available():
            raise ValueError("CUDA was requested but is unavailable")
        if device == "mps" and not torch.backends.mps.is_available():
            raise ValueError("MPS was requested but is unavailable")
        if device not in {"cpu", "mps", "cuda"}:
            raise ValueError(f"Unknown device: {device}")
        torch.set_num_threads(threads)
        import cv2
        cv2.setNumThreads(threads)
        existing = sys.modules.get("models.birefnet")
        if existing is not None and Path(existing.__file__).resolve() != model_repo / "models/birefnet.py":
            raise ValueError("Another BiRefNet checkout is already imported; start a fresh process")
        sys.path.insert(0, str(model_repo))
        from models.birefnet import BiRefNet
        from image_proc import refine_foreground

        self.torch, self.functional = torch, functional
        self.device, self.threads = device, threads
        self.refine_foreground = refine_foreground
        self.model = BiRefNet(bb_pretrained=False)
        if checkpoint.suffix == ".safetensors":
            state = load_file(str(checkpoint), device="cpu")
        else:
            state = torch.load(str(checkpoint), map_location="cpu", weights_only=True)
            if isinstance(state, dict) and "state_dict" in state:
                state = state["state_dict"]
        self.model.load_state_dict(state, strict=True)
        del state
        self.model.eval().to(device)
        hook_report = None
        if model_hook is None and device == "mps" and use_mps_deform:
            from birefnet_mps import install
            model_hook = install
            hook_file = Path(__file__).with_name("birefnet_mps.py")
            hook_provenance = {
                "path": str(hook_file), "sha256": sha256(hook_file), "function": "install",
                "selection": "Verified equivalent MPS deform-convolution; disable with --disable-mps-deform",
            }
        if model_hook is not None:
            hook_report = model_hook(self.model, device)
            # Reject opaque objects in audit metadata before a long batch starts.
            json.dumps(hook_report)
        self.transform = transforms.Compose([
            transforms.Resize(inference_size), transforms.ToTensor(),
            transforms.Normalize([.485, .456, .406], [.229, .224, .225]),
        ])
        self.provenance = {
            "name": "BiRefNet-matting",
            "officialModel": "https://huggingface.co/ZhengPeng7/BiRefNet-matting",
            "officialRepository": "https://github.com/ZhengPeng7/BiRefNet",
            "repository": repository_info(model_repo),
            "checkpoint": {"path": str(checkpoint), "sha256": sha256(checkpoint),
                           "revision": checkpoint_revision},
            "device": device, "inferenceSizeHW": list(inference_size), "threads": threads,
            "torchVersion": torch.__version__,
            "foregroundEstimator": "Official image_proc.refine_foreground; Photoroom fast-foreground-estimation; CPU",
            "alphaProcessing": "Final model sigmoid; bilinear restoration to original size; rounded to uint8",
            "temporalModel": False,
            "temporalPolicy": "Independent image-model inference on every original frame; no tracking, temporal propagation or smoothing",
            "legacyColorKeyApplied": False,
            "geometryPolicy": "Model input resize only; output alpha restored to original canvas; no crop or output resize",
            "modelHook": {"source": hook_provenance, "report": hook_report} if model_hook is not None else None,
            "loadSeconds": round(time.perf_counter() - started, 6),
        }
        self.last_timings: dict = {}

    def _synchronize(self) -> None:
        if self.device == "mps":
            self.torch.mps.synchronize()
        elif self.device == "cuda":
            self.torch.cuda.synchronize()

    def __call__(self, rgb: np.ndarray) -> np.ndarray:
        if rgb.dtype != np.uint8 or rgb.ndim != 3 or rgb.shape[2] != 3:
            raise ValueError("Backend input must be a uint8 RGB frame")
        image = Image.fromarray(rgb)
        self._synchronize()
        started = time.perf_counter()
        with self.torch.inference_mode():
            tensor = self.transform(image).unsqueeze(0).to(self.device)
            prediction = self.model(tensor)[-1].sigmoid()
            alpha = self.functional.interpolate(
                prediction, size=(image.height, image.width), mode="bilinear",
                align_corners=False)[0, 0].cpu().numpy()
        self._synchronize()
        inference_seconds = time.perf_counter() - started
        if not np.isfinite(alpha).all():
            raise ValueError("Model returned nonfinite alpha")
        alpha_image = Image.fromarray(np.rint(np.clip(alpha, 0, 1) * 255).astype(np.uint8))
        foreground_started = time.perf_counter()
        foreground = self.refine_foreground(image, alpha_image, device="cpu").convert("RGBA")
        foreground.putalpha(alpha_image)
        self.last_timings = {
            "inferenceSeconds": inference_seconds,
            "foregroundEstimationSeconds": time.perf_counter() - foreground_started,
        }
        return np.asarray(foreground)


class RawVideoWriter:
    """Stream native pixels to FFmpeg, retaining encoder diagnostics on failure."""

    def __init__(self, path: Path, size: tuple[int, int], fps: Fraction, *,
                 preview: bool = False, threads: int = 2):
        self.path, self.preview = path, preview
        self.log = tempfile.TemporaryFile()
        width, height = size
        command = [
            imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error",
            "-nostdin", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24" if preview else "rgba",
            "-s", f"{width}x{height}", "-framerate", str(fps), "-i", "pipe:0", "-an",
        ]
        if preview:
            command += ["-c:v", "libx264", "-crf", "18", "-pix_fmt",
                        "yuv420p" if width % 2 == height % 2 == 0 else "yuv444p",
                        "-movflags", "+faststart"]
        else:
            command += ["-c:v", "ffv1", "-level", "3", "-pix_fmt", "bgra"]
        command += ["-threads", str(threads), str(path)]
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE,
                                        stdout=subprocess.DEVNULL, stderr=self.log)
        self.closed = False

    def diagnostics(self) -> str:
        self.log.seek(0)
        return self.log.read().decode("utf-8", errors="replace")[-3000:]

    def write(self, pixels: np.ndarray) -> None:
        try:
            assert self.process.stdin is not None
            self.process.stdin.write(np.ascontiguousarray(pixels).tobytes())
        except (BrokenPipeError, OSError) as error:
            raise RuntimeError(f"FFmpeg encoder failed for {self.path}: {self.diagnostics()}") from error

    def close(self) -> None:
        if self.closed:
            return
        try:
            assert self.process.stdin is not None
            self.process.stdin.close()
            status = self.process.wait(timeout=60)
            if status:
                raise RuntimeError(f"FFmpeg exited {status}: {self.diagnostics()}")
        finally:
            if self.process.poll() is None:
                self.process.kill()
                self.process.wait()
            self.log.close()
            self.closed = True

    def abort(self) -> None:
        if self.closed:
            return
        self.process.kill()
        if self.process.stdin is not None:
            try:
                self.process.stdin.close()
            except BrokenPipeError:
                pass
        self.process.wait()
        self.log.close()
        self.closed = True


def verify_pixel_roundtrip(video: Path, info: dict, expected_digest: str) -> str:
    digest = hashlib.sha256()
    reader = frames(video, info["decoderInputParams"])
    next(reader)
    try:
        for rgba in reader:
            digest.update(rgba.tobytes())
    finally:
        reader.close()
    actual = digest.hexdigest()
    if actual != expected_digest:
        raise ValueError("FFV1 roundtrip changed RGBA pixels; refusing to publish the master")
    return actual


def produce_video(video: Path, output_dir: Path, backend: Callable, *,
                  save_preview: bool = False, threads: int = 2,
                  progress_callback: Callable[[dict], None] | None = None) -> dict:
    """Run a reusable callable RGB->RGBA backend over one complete source video."""
    started = time.perf_counter()
    video, output_dir = video.resolve(), output_dir.resolve()
    if threads < 1:
        raise ValueError("--threads must be positive")
    if output_dir.exists() and (not output_dir.is_dir() or any(output_dir.iterdir())):
        raise ValueError(f"Output directory must be absent or empty: {output_dir}")
    original = inspect_video(video, require_alpha=False)
    source_inspection_seconds = time.perf_counter() - started
    size = tuple(original["size"])
    fps = Fraction(original["fpsRational"])
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".matte-video-", dir=output_dir.parent) as tmp:
        stage = Path(tmp)
        master = stage / "transparent_master.mkv"
        writer = RawVideoWriter(master, size, fps, threads=threads)
        preview_writer = None
        reader = None
        reports = []
        digest = hashlib.sha256()
        processing_started = time.perf_counter()
        try:
            if save_preview:
                preview_writer = RawVideoWriter(stage / "preview_dark.mp4", size, fps,
                                                preview=True, threads=threads)
            reader = frames(video, original["decoderInputParams"])
            metadata = next(reader)
            if tuple(metadata["size"]) != size:
                raise ValueError("Source dimensions changed between inspection and processing")
            for index, decoded in enumerate(reader):
                frame_started = time.perf_counter()
                rgba = np.asarray(backend(decoded[:, :, :3]))
                if rgba.shape != decoded.shape or rgba.dtype != np.uint8:
                    raise ValueError(f"Backend changed frame {index} dimensions or RGBA uint8 format")
                alpha = rgba[:, :, 3]
                low, high = int(alpha.min()), int(alpha.max())
                if low == 255 or high == 0:
                    raise ValueError(f"Backend frame {index} is fully opaque or has no visible foreground")
                digest.update(np.ascontiguousarray(rgba).tobytes())
                writer.write(rgba)
                if preview_writer is not None:
                    coverage = alpha[:, :, None].astype(np.float32) / 255
                    composite = np.rint(rgba[:, :, :3] * coverage +
                                        np.array([26, 28, 34]) * (1 - coverage)).astype(np.uint8)
                    preview_writer.write(composite)
                frame_report = {
                    "sourceFrame": index, "alphaRange": [low, high],
                    "transparentPixels": int((alpha == 0).sum()),
                    "nonopaquePixels": int((alpha < 255).sum()),
                    "visiblePixels": int((alpha > 0).sum()),
                    "seconds": round(time.perf_counter() - frame_started, 6),
                }
                timings = getattr(backend, "last_timings", {})
                frame_report.update({key: round(float(value), 6) for key, value in timings.items()})
                reports.append(frame_report)
                if progress_callback is not None:
                    progress_callback({
                        "video": str(video), "processedFrames": len(reports),
                        "totalFrames": original["decodedFrameCount"],
                        "elapsedSeconds": round(time.perf_counter() - processing_started, 3),
                    })
            if len(reports) != original["decodedFrameCount"]:
                raise ValueError("Source decoding ended early or changed its frame count")
            if sha256(video) != original["sha256"]:
                raise ValueError("Source video changed while processing")
            writer.close()
            if preview_writer is not None:
                preview_writer.close()
        finally:
            if reader is not None:
                reader.close()
            writer.abort()
            if preview_writer is not None:
                preview_writer.abort()
        processing_seconds = time.perf_counter() - processing_started
        verification_started = time.perf_counter()
        matted = inspect_video(master, require_alpha=True)
        validate_pair(original, matted)
        pixel_digest = verify_pixel_roundtrip(master, matted, digest.hexdigest())
        matted["path"] = str(output_dir / master.name)
        preview_info = None
        if save_preview:
            preview_info = inspect_video(stage / "preview_dark.mp4", require_alpha=False)
            validate_pair(original, preview_info)
            preview_info["path"] = str(output_dir / "preview_dark.mp4")
            preview_info["backgroundRGB"] = [26, 28, 34]
            preview_info["purpose"] = "Opaque visual review only; not an alpha asset"
        provenance = getattr(backend, "provenance", {
            "name": type(backend).__name__,
            "temporalPolicy": "Caller-supplied backend; temporal behavior not declared",
        })
        manifest = {
            "schemaVersion": 1, "sourceVideo": original, "alphaVideo": matted,
            "backend": provenance, "processedFrameCount": len(reports),
            "sourceFps": original["fps"], "sourceFpsRational": str(fps),
            "sourceSize": list(size),
            "processingPolicy": "Every original decoded frame processed before any sampling",
            "geometryPolicy": "Original full-resolution canvas; no crop, pad, output resize or autorotation",
            "alphaValidation": "Every full-resolution decoded frame retains visible foreground and nonopaque pixels",
            "losslessRGBAValidation": {"passed": True, "streamSHA256": pixel_digest,
                                       "format": "FFV1 level 3 / BGRA in Matroska"},
            "preview": preview_info,
            "timings": {
                "sourceInspectionSeconds": round(source_inspection_seconds, 6),
                "processingSeconds": round(processing_seconds, 6),
                "verificationSeconds": round(time.perf_counter() - verification_started, 6),
                "totalSeconds": round(time.perf_counter() - started, 6),
                "meanFrameSeconds": round(processing_seconds / len(reports), 6),
            },
            "frames": reports,
        }
        (stage / "matte_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if output_dir.exists():
            output_dir.rmdir()
        # The completed master, manifest and optional preview become visible
        # together only after every frame and the lossless roundtrip pass.
        shutil.move(str(stage), str(output_dir))
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, action="append", required=True,
                        help="Repeat for a batch; the model loads once")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model-repo", type=Path, required=True,
                        help="Existing local official BiRefNet checkout")
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--checkpoint-revision", help="Optional upstream weight revision for audit")
    parser.add_argument("--device", choices=("auto", "mps", "cpu", "cuda"), default="auto")
    parser.add_argument("--inference-size", type=int, nargs="+", default=[1024],
                        help="One square dimension or HEIGHT WIDTH; output stays original size")
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--save-preview", action="store_true")
    parser.add_argument("--model-hook", help="Optional verified local /path.py:function(model, device)")
    parser.add_argument("--disable-mps-deform", action="store_true",
                        help="Use the official MPS CPU-fallback operator for reference comparison")
    args = parser.parse_args()
    if len(args.inference_size) not in (1, 2):
        parser.error("--inference-size expects one square dimension or HEIGHT WIDTH")
    size = tuple(args.inference_size * 2 if len(args.inference_size) == 1 else args.inference_size)
    try:
        hook, hook_info = load_model_hook(args.model_hook) if args.model_hook else (None, None)
        backend = BiRefNetMattingBackend(
            args.model_repo, args.checkpoint, device=args.device, inference_size=size,
            threads=args.threads, model_hook=hook, hook_provenance=hook_info,
            checkpoint_revision=args.checkpoint_revision,
            use_mps_deform=not args.disable_mps_deform)
        previous_progress = 0.0

        def progress(value: dict) -> None:
            nonlocal previous_progress
            now = time.monotonic()
            if now - previous_progress >= 10 or value["processedFrames"] == value["totalFrames"]:
                print(json.dumps({"progress": value}), flush=True)
                previous_progress = now

        results = []
        for index, video in enumerate(args.video):
            output = (args.output_dir if len(args.video) == 1 else
                      args.output_dir / f"{index:04d}_{video.stem}")
            report = produce_video(video, output, backend, save_preview=args.save_preview,
                                   threads=args.threads, progress_callback=progress)
            result = {"source": str(video.resolve()), "output": str(output.resolve()),
                      "frames": report["processedFrameCount"], "timings": report["timings"]}
            results.append(result)
            print(json.dumps({"completed": result}), flush=True)
        if len(results) > 1:
            (args.output_dir / "batch_manifest.json").write_text(
                json.dumps({"backend": backend.provenance, "videos": results},
                           ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (ValueError, OSError, RuntimeError) as error:
        parser.exit(1, f"error: {error}\n")


if __name__ == "__main__":
    main()
