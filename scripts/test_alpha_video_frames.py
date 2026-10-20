#!/usr/bin/env python3
"""Exercise alpha import against real lossless FFmpeg video files."""

from __future__ import annotations

from fractions import Fraction
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import imageio_ffmpeg
import numpy as np
from PIL import Image

from extract_alpha_video_frames import extract_alpha_video, sample_indices


SCRIPTS = Path(__file__).resolve().parent


def sample_frames(count: int, size=(32, 24), *, opaque: bool = False) -> list[np.ndarray]:
    width, height = size
    result = []
    for index in range(count):
        frame = np.zeros((height, width, 4), dtype=np.uint8)
        frame[:, :, :3] = [index % 256, 59, 173]
        frame[3:-3, 3:-3, 3] = 255
        frame[4, 4:10, 3] = [0, 1, 17, 128, 254, 255]
        if opaque:
            frame[:, :, 3] = 255
        result.append(frame)
    return result


def write_video(path: Path, pixels: list[np.ndarray], fps="24", *, mp4: bool = False) -> None:
    height, width = pixels[0].shape[:2]
    command = [
        imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error",
        "-y", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{width}x{height}",
        "-r", fps, "-i", "-", "-an",
    ]
    command += (["-c:v", "libx264", "-pix_fmt", "yuv420p"] if mp4 else
                ["-c:v", "ffv1", "-pix_fmt", "bgra"])
    command.append(str(path))
    result = subprocess.run(command, input=b"".join(frame.tobytes() for frame in pixels),
                            capture_output=True)
    if result.returncode:
        raise RuntimeError(result.stderr.decode())


class AlphaVideoFrameTests(unittest.TestCase):
    def test_transparent_webm_uses_alpha_capable_vp9_decoder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rgba = sample_frames(8)
            write_video(root / "lossless.mkv", rgba)
            write_video(root / "original.mkv", sample_frames(8, opaque=True))
            result = subprocess.run([
                imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error",
                "-y", "-i", str(root / "lossless.mkv"), "-an", "-c:v", "libvpx-vp9",
                "-lossless", "1", "-pix_fmt", "yuva420p", "-auto-alt-ref", "0",
                str(root / "alpha.webm"),
            ], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            manifest = extract_alpha_video(root / "alpha.webm", root / "original.mkv",
                                           root / "rgba", mode="source")
            self.assertIn("libvpx-vp9", manifest["alphaVideo"]["decoderInputParams"])
            for output_index, source_index in enumerate((0, 2, 4, 6, 7)):
                with Image.open(root / "rgba" / f"frame_{output_index:04d}.png") as image:
                    # RGB -> YUVA encoding can round intermediate alpha by one
                    # level; FFV1 below independently verifies exact RGBA.
                    alpha = np.asarray(image)[:, :, 3]
                    np.testing.assert_allclose(alpha, rgba[source_index][:, :, 3],
                                               rtol=0, atol=1)
                    self.assertEqual(int(alpha[0, 0]), 0)
                    self.assertEqual(int(alpha[5, 5]), 255)

    def test_source_preserves_exact_rgba_values_and_final_odd_frame(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rgba, rgb = sample_frames(8), sample_frames(8, opaque=True)
            write_video(root / "alpha.mkv", rgba)
            write_video(root / "original.mkv", rgb)
            manifest = extract_alpha_video(
                root / "alpha.mkv", root / "original.mkv", root / "rgba",
                mode="source", rgb_output=root / "rgb")
            indices = [0, 2, 4, 6, 7]
            self.assertEqual([frame["sourceFrame"] for frame in manifest["frameMap"]], indices)
            self.assertEqual(manifest["sourceFpsRational"], "24")
            self.assertEqual(manifest["sourceSize"], [32, 24])
            self.assertEqual(manifest["outputFrameCount"], 5)
            for output_index, source_index in enumerate(indices):
                name = f"frame_{output_index:04d}.png"
                with Image.open(root / "rgba" / name) as image:
                    self.assertEqual(image.mode, "RGBA")
                    np.testing.assert_array_equal(np.asarray(image), rgba[source_index])
                with Image.open(root / "rgb" / name) as image:
                    self.assertEqual(image.mode, "RGB")
                    np.testing.assert_array_equal(np.asarray(image), rgb[source_index][:, :, :3])
            saved = json.loads((root / "rgba/extraction_manifest.json").read_text())
            self.assertEqual(saved["alphaVideo"]["decodedFrameCount"], 8)

    def test_return_spans_full_97_frame_video_with_twelve_samples(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rgba = sample_frames(97)
            write_video(root / "alpha.mkv", rgba)
            write_video(root / "original.mkv", sample_frames(97, opaque=True))
            manifest = extract_alpha_video(root / "alpha.mkv", root / "original.mkv",
                                           root / "rgba", mode="return")
            expected = [round(index * 96 / 11) for index in range(12)]
            self.assertEqual([frame["sourceFrame"] for frame in manifest["frameMap"]], expected)
            self.assertEqual(manifest["returnPlaybackDurationSeconds"], 1)
            self.assertEqual(len(list((root / "rgba").glob("frame_*.png"))), 12)
            sampling = json.loads((root / "rgba/sampling_manifest.json").read_text())
            self.assertEqual(sampling["samples"][-1]["videoDecodedFrame"], 96)
            self.assertEqual(sampling["samples"][-1]["videoTimestampSeconds"], 4.0)
            for index in (0, 11):
                with Image.open(root / "rgba" / f"frame_{index:04d}.png") as image:
                    np.testing.assert_array_equal(np.asarray(image), rgba[expected[index]])

    def test_rejects_opaque_mp4_and_does_not_publish_frames(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_video(root / "opaque.mp4", sample_frames(8, opaque=True), mp4=True)
            write_video(root / "original.mkv", sample_frames(8, opaque=True))
            with self.assertRaisesRegex(ValueError, "fully opaque"):
                extract_alpha_video(root / "opaque.mp4", root / "original.mkv",
                                    root / "rgba", mode="source")
            self.assertFalse((root / "rgba").exists())

    def test_rejects_all_transparent_frame(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rgba = sample_frames(8)
            rgba[3][:, :, 3] = 0
            write_video(root / "alpha.mkv", rgba)
            write_video(root / "original.mkv", sample_frames(8, opaque=True))
            with self.assertRaisesRegex(ValueError, "no visible foreground"):
                extract_alpha_video(root / "alpha.mkv", root / "original.mkv",
                                    root / "rgba", mode="source")

    def test_rejects_backend_metadata_changes(self):
        for change, count, size, fps in [
            ("dimensions", 8, (34, 24), "24"),
            ("frame rate", 8, (32, 24), "25"),
            ("decoded frame count", 9, (32, 24), "24"),
        ]:
            with self.subTest(change=change), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                write_video(root / "original.mkv", sample_frames(8, opaque=True))
                write_video(root / "alpha.mkv", sample_frames(count, size), fps)
                with self.assertRaisesRegex(ValueError, change):
                    extract_alpha_video(root / "alpha.mkv", root / "original.mkv",
                                        root / "rgba", mode="source")
                self.assertFalse((root / "rgba").exists())

    def test_rational_fps_is_not_rounded_to_display_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_video(root / "alpha.mkv", sample_frames(7), "24000/1001")
            write_video(root / "original.mkv", sample_frames(7, opaque=True), "24000/1001")
            result = subprocess.run([
                sys.executable, str(SCRIPTS / "extract_alpha_video_frames.py"),
                "--video", str(root / "alpha.mkv"), "--source-video", str(root / "original.mkv"),
                "--mode", "source", "--output", str(root / "frames"), "--target-fps", "12",
            ], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            info = json.loads((root / "frames/extraction_manifest.json").read_text())
            self.assertEqual(info["sourceFpsRational"], "24000/1001")
            self.assertEqual([item["sourceFrame"] for item in info["frameMap"]], [0, 2, 4, 6])

    def test_different_container_time_bases_preserve_cfr_timing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_video(root / "original.mp4", sample_frames(8, opaque=True),
                        "24000/1001", mp4=True)
            write_video(root / "alpha.mkv", sample_frames(8), "24000/1001")
            manifest = extract_alpha_video(
                root / "alpha.mkv", root / "original.mp4", root / "rgba", mode="source")
            source = manifest["originalVideo"]
            alpha = manifest["alphaVideo"]
            self.assertNotEqual(source["timeBaseRational"], alpha["timeBaseRational"])
            self.assertTrue(source["cfrValidation"]["passed"])
            self.assertTrue(alpha["cfrValidation"]["passed"])
            self.assertEqual(len(source["framePresentationTimestamps"]), 8)
            self.assertEqual(len(alpha["framePresentationTimestamps"]), 8)

    def test_rejects_vfr_before_model_processing_or_publication(self):
        from matte_video import produce_video

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_video(root / "regular.mkv", sample_frames(8, opaque=True))
            result = subprocess.run([
                imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error",
                "-y", "-i", str(root / "regular.mkv"),
                "-vf", r"setpts=if(lt(N\,4)\,N\,N+4)/(24*TB)",
                "-fps_mode", "vfr", "-c:v", "ffv1", str(root / "irregular.mkv"),
            ], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            calls = []

            def backend(rgb):
                calls.append(1)
                alpha = np.full(rgb.shape[:2], 255, dtype=np.uint8)
                alpha[0, 0] = 0
                return np.dstack([rgb, alpha])

            with self.assertRaisesRegex(ValueError, "CFR"):
                produce_video(root / "irregular.mkv", root / "master", backend)
            self.assertEqual(calls, [])
            self.assertFalse((root / "master").exists())
            # Importing externally matted videos uses the same guard.
            write_video(root / "alpha.mkv", sample_frames(8))
            with self.assertRaisesRegex(ValueError, "CFR"):
                extract_alpha_video(root / "alpha.mkv", root / "irregular.mkv",
                                    root / "rgba", mode="source")
            self.assertFalse((root / "rgba").exists())

    def test_sampling_rejects_trimmed_return_and_incorrect_playback_fps(self):
        for count in (95, 96, 97):
            indices = sample_indices(count, Fraction(24), "return")
            self.assertEqual(len(indices), 12)
            self.assertEqual(indices[0], 0)
            self.assertEqual(indices[-1], count - 1)
        for count in (94, 98):
            with self.assertRaisesRegex(ValueError, "four-second"):
                sample_indices(count, Fraction(24), "return")
        with self.assertRaisesRegex(ValueError, "12 FPS"):
            sample_indices(96, Fraction(24), "return", Fraction(24))
        with self.assertRaisesRegex(ValueError, "exceed"):
            sample_indices(8, Fraction(24), "source", Fraction(30))


if __name__ == "__main__":
    unittest.main()
