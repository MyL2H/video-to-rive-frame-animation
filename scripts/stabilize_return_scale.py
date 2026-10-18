#!/usr/bin/env python3
"""Stabilize subject scale and vertical position in generated return frames."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from rgb_matte import fill_enclosed_transparent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--return-root", type=Path, required=True)
    parser.add_argument("--label", action="append", dest="labels")
    parser.add_argument("--replace", action="store_true")
    parser.add_argument("--webp-quality", type=int, default=82)
    parser.add_argument("--webp-method", type=int, default=6)
    return parser.parse_args()


def load_rgba(path: Path) -> Image.Image:
    return Image.open(path).convert("RGBA")


def alpha_bbox(image: Image.Image) -> tuple[int, int, int, int]:
    alpha = np.asarray(image.getchannel("A"))
    ys, xs = np.where(alpha > 8)
    if len(xs) == 0:
        raise ValueError("Frame has no visible alpha pixels")
    return int(xs.min()), int(ys.min()), int(xs.max() + 1), int(ys.max() + 1)


def matching_gray(image: Image.Image) -> np.ndarray:
    rgba = np.asarray(image)
    alpha = rgba[:, :, 3:4].astype(np.float32) / 255.0
    background = np.full(rgba.shape[:2] + (3,), 32, dtype=np.float32)
    rgb = rgba[:, :, :3].astype(np.float32) * alpha + background * (1 - alpha)
    return cv2.cvtColor(rgb.astype(np.uint8), cv2.COLOR_RGB2GRAY)


def similarity_scale(
    reference: np.ndarray,
    image: np.ndarray,
    detector: cv2.Feature2D,
) -> tuple[float | None, int]:
    ref_points, ref_descriptors = detector.detectAndCompute(reference, None)
    points, descriptors = detector.detectAndCompute(image, None)
    if ref_descriptors is None or descriptors is None:
        return None, 0

    matches = cv2.BFMatcher().knnMatch(ref_descriptors, descriptors, k=2)
    good = [first for first, second in matches if first.distance < 0.7 * second.distance]
    if len(good) < 8:
        return None, len(good)

    source = np.float32(
        [ref_points[match.queryIdx].pt for match in good]
    ).reshape(-1, 1, 2)
    target = np.float32(
        [points[match.trainIdx].pt for match in good]
    ).reshape(-1, 1, 2)
    matrix, inliers = cv2.estimateAffinePartial2D(
        source,
        target,
        method=cv2.RANSAC,
        ransacReprojThreshold=2.0,
    )
    if matrix is None or inliers is None:
        return None, len(good)

    inlier_count = int(inliers.sum())
    if inlier_count < 12:
        return None, inlier_count
    scale = float(np.sqrt(abs(np.linalg.det(matrix[:2, :2]))))
    if not 0.8 <= scale <= 1.3:
        return None, inlier_count
    return scale, inlier_count


def estimate_scales(images: list[Image.Image]) -> tuple[list[float], list[dict[str, object]]]:
    detector = cv2.SIFT_create(nfeatures=2000)
    sample_indices = sorted(
        {0, 1, len(images) - 2, len(images) - 1}
        | {round(position * (len(images) - 1) / 8) for position in range(9)}
    )
    gray = {index: matching_gray(images[index]) for index in sample_indices}
    sampled_logs: dict[int, float] = {0: 0.0, len(images) - 1: 0.0}
    report: list[dict[str, object]] = []

    for index in sample_indices:
        if index in (0, len(images) - 1):
            report.append(
                {"frame": index, "scale": 1.0, "method": "exact endpoint"}
            )
            continue

        estimates: list[tuple[float, int]] = []
        for reference_index in (0, len(images) - 1):
            scale, inliers = similarity_scale(
                gray[reference_index], gray[index], detector
            )
            if scale is not None:
                estimates.append((scale, inliers))

        if not estimates:
            report.append(
                {"frame": index, "scale": None, "method": "interpolated"}
            )
            continue

        weights = np.asarray([inliers for _, inliers in estimates], dtype=np.float64)
        values = np.asarray([np.log(scale) for scale, _ in estimates])
        estimate = float(np.exp(np.average(values, weights=weights)))
        sampled_logs[index] = float(np.log(estimate))
        report.append(
            {
                "frame": index,
                "scale": estimate,
                "inliers": [inliers for _, inliers in estimates],
                "method": "weighted endpoint feature matching",
            }
        )

    known = sorted(sampled_logs)
    interpolated = np.interp(
        np.arange(len(images)),
        known,
        [sampled_logs[index] for index in known],
    )
    return [float(np.exp(value)) for value in interpolated], report


def stabilize_frame(
    image: Image.Image,
    scale: float,
    target_bottom: float,
) -> Image.Image:
    width, height = image.size
    resized_size = (
        max(1, round(width * scale)),
        max(1, round(height * scale)),
    )
    resampling = getattr(Image, "Resampling", Image)
    scaled = image.resize(resized_size, resampling.LANCZOS)
    left = (width - resized_size[0]) // 2
    _, _, _, bottom = alpha_bbox(scaled)
    top = round(target_bottom - bottom)

    output = Image.new("RGBA", image.size, (0, 0, 0, 0))
    output.alpha_composite(scaled, (left, top))
    return output


def process_clip(
    source_dir: Path,
    output_dir: Path,
    replace: bool,
    webp_quality: int,
    webp_method: int,
) -> None:
    if output_dir.exists():
        if not replace:
            raise FileExistsError(f"Output already exists: {output_dir}")
        shutil.rmtree(output_dir)

    png_dir = output_dir / "frames_50_png"
    webp_dir = output_dir / "frames_50_webp"
    for directory in (png_dir, webp_dir):
        directory.mkdir(parents=True)

    paths = sorted(source_dir.glob("frame_*.png"))
    if len(paths) < 4:
        raise ValueError(f"Expected at least four frames in {source_dir}")
    images = [load_rgba(path) for path in paths]
    if len({image.size for image in images}) != 1:
        raise ValueError(f"Frame size mismatch in {source_dir}")

    scales, scale_report = estimate_scales(images)
    endpoint_bottoms = [alpha_bbox(images[0])[3], alpha_bbox(images[-1])[3]]
    width, height = images[0].size

    for index, (path, image, scale) in enumerate(zip(paths, images, scales)):
        if index in (0, len(images) - 1):
            stabilized = image.copy()
        else:
            target_bottom = float(
                np.interp(index, [0, len(images) - 1], endpoint_bottoms)
            )
            stabilized = stabilize_frame(image, 1.0 / scale, target_bottom)

        resized = stabilized.resize(
            (round(width * 0.5), round(height * 0.5)),
            getattr(Image, "Resampling", Image).LANCZOS,
        )
        resized_rgba = np.asarray(resized).copy()
        resized_rgba[:, :, 3] = fill_enclosed_transparent(resized_rgba[:, :, 3])
        resized = Image.fromarray(resized_rgba)
        resized.save(png_dir / path.name, format="PNG")
        resized.save(
            webp_dir / f"{path.stem}.webp",
            format="WEBP",
            quality=webp_quality,
            method=webp_method,
        )

    manifest = {
        "sourceFrames": str(source_dir),
        "frameCount": len(images),
        "sourceSize": [width, height],
        "optimizedSize": [round(width * 0.5), round(height * 0.5)],
        "endpointPolicy": "exact source endpoint frames",
        "scaleStabilization": {
            "method": "SIFT similarity estimates with endpoint anchors",
            "minimumObservedScale": min(item["scale"] for item in scale_report if item["scale"] is not None),
            "maximumObservedScale": max(item["scale"] for item in scale_report if item["scale"] is not None),
        },
        "frames": scale_report,
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )


def main() -> None:
    args = parse_args()
    return_root = args.return_root.resolve()
    clips = sorted(return_root.glob("return_*/processed/frames_rgba"))
    if args.labels:
        clips = [
            path
            for path in clips
            if path.parents[1].name.removeprefix("return_") in args.labels
        ]
    if not clips:
        raise ValueError(f"No processed return clips found in {return_root}")

    for source_dir in clips:
        output_dir = source_dir.parent / "processed_stabilized"
        process_clip(
            source_dir,
            output_dir,
            args.replace,
            args.webp_quality,
            args.webp_method,
        )
        print(f"stabilized={output_dir}")


if __name__ == "__main__":
    main()
