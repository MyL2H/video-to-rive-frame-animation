#!/usr/bin/env python3
"""Offline regressions for transparent gaps, edge colors and matte diagnostics."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image

from prepare_frame_assets import resize_rgba_premultiplied
from validate_matte import frame_metrics, make_contact_sheet


SCRIPTS = Path(__file__).resolve().parent


class MatteAssetPreparationTests(unittest.TestCase):
    def test_premultiplied_resize_preserves_real_hole_and_soft_edge_color(self):
        # Deliberately poisonous RGB under zero alpha; it must never tint the
        # orange ring. The soft ring edge must retain its color at low alpha.
        yy, xx = np.mgrid[:768, :512]
        radius = np.hypot(xx - 256, yy - 384)
        alpha = (
            np.clip((radius - 55) / 4, 0, 1)
            * np.clip((120 - radius) / 4, 0, 1)
        )
        rgba = np.empty((768, 512, 4), dtype=np.uint8)
        rgba[:, :, :3] = [13, 245, 180]
        rgba[alpha > 0, :3] = [239, 99, 39]
        rgba[:, :, 3] = np.rint(alpha * 255).astype(np.uint8)
        result = np.asarray(
            resize_rgba_premultiplied(Image.fromarray(rgba), (256, 384))
        )
        self.assertEqual(result.shape, (384, 256, 4))
        self.assertEqual(int(result[192, 128, 3]), 0)
        self.assertTrue(np.all(result[180:204, 116:140, 3] == 0))
        soft = (result[:, :, 3] >= 8) & (result[:, :, 3] <= 247)
        self.assertGreater(int(soft.sum()), 100)
        error = np.abs(result[soft, :3].astype(int) - [239, 99, 39])
        self.assertLessEqual(int(error.max()), 1)
        self.assertTrue(np.all(result[result[:, :, 3] == 0, :3] == 0))

    def test_cli_outputs_expected_png_and_webp_without_filling_gap(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            source.mkdir()
            rgba = np.zeros((768, 512, 4), dtype=np.uint8)
            rgba[100:660, 80:430] = [220, 90, 20, 255]
            rgba[250:500, 200:310, 3] = 0
            Image.fromarray(rgba).save(source / "frame_0000.png")
            result = subprocess.run(
                [
                    sys.executable, str(SCRIPTS / "prepare_frame_assets.py"),
                    "--source", str(source), "--png-output", str(root / "png"),
                    "--webp-output", str(root / "webp"), "--scale", "0.5",
                ],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            for path in [root / "png/frame_0000.png", root / "webp/frame_0000.webp"]:
                with Image.open(path) as frame:
                    self.assertEqual(frame.size, (256, 384))
                    self.assertEqual(frame.mode, "RGBA")
                    self.assertEqual(frame.getpixel((125, 175))[3], 0)
                    self.assertEqual(frame.getpixel((60, 100))[3], 255)

    def test_validator_holes_and_gray_foreground_are_advisory_until_opt_in(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frames = root / "frames"
            frames.mkdir()
            rgba = np.zeros((96, 64, 4), dtype=np.uint8)
            rgba[12:84, 12:52] = [235, 235, 235, 255]
            rgba[32:64, 28:36, 3] = 0
            Image.fromarray(rgba).save(frames / "frame_0000.png")
            command = [
                sys.executable, str(SCRIPTS / "validate_matte.py"),
                "--frames-dir", str(frames), "--report", str(root / "report.json"),
                "--contact-sheet", str(root / "contact.jpg"),
            ]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads((root / "report.json").read_text())
            self.assertTrue(report["passed"])
            self.assertTrue(report["technicalChecksPassed"])
            self.assertEqual(report["maximumEnclosedHolePixels"], 256)
            self.assertGreater(report["frames"][0]["opaqueBackgroundRangePixels"], 0)
            self.assertIn("not background residue", report["diagnosticPolicy"])
            strict = subprocess.run(
                command + ["--max-hole-pixels", "0"], capture_output=True, text=True
            )
            self.assertEqual(strict.returncode, 1)
            report = json.loads((root / "report.json").read_text())
            self.assertTrue(report["technicalChecksPassed"])
            self.assertFalse(report["explicitThresholdChecksPassed"])

    def test_rgb_comparison_detects_open_missing_body_region(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rgb = np.full((96, 64, 3), 235, dtype=np.uint8)
            rgb[12:84, 12:52] = [140, 45, 30]
            rgba = np.dstack((rgb, np.zeros((96, 64), dtype=np.uint8)))
            rgba[12:84, 12:52, 3] = 255
            # Open notch is connected to outside, so enclosed-hole analysis
            # alone misses this clear removal of source body pixels.
            rgba[42:54, 12:32, 3] = 0
            Image.fromarray(rgba).save(root / "frame_0000.png")
            Image.fromarray(rgb).save(root / "rgb.png")
            metrics = frame_metrics(
                root / "frame_0000.png", 230, 241, root / "rgb.png"
            )
            self.assertEqual(metrics["enclosedHolePixels"], 0)
            self.assertEqual(metrics["removedNonBackgroundPixels"], 240)
            self.assertIn("Risk only", metrics["interpretation"])

    def test_contact_sheet_is_bounded_and_checks_three_backgrounds(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            files = []
            for index in range(30):
                path = root / f"frame_{index:04d}.png"
                Image.new("RGBA", (512, 768), (239, 99, 39, 128)).save(path)
                files.append(path)
            report = make_contact_sheet(files, root / "sheet.jpg", columns=3)
            self.assertEqual(len(report["sampledFrames"]), 12)
            self.assertEqual(report["sampledFrames"][0], "frame_0000.png")
            self.assertEqual(report["sampledFrames"][-1], "frame_0029.png")
            self.assertEqual(len(report["backgrounds"]), 3)
            self.assertEqual(report["size"], [1152, 848])

    def test_validator_rejects_empty_foreground_as_technical_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            Image.new("RGBA", (64, 96), (235, 235, 235, 0)).save(root / "frame_0000.png")
            result = subprocess.run(
                [
                    sys.executable, str(SCRIPTS / "validate_matte.py"),
                    "--frames-dir", str(root), "--report", str(root / "report.json"),
                    "--contact-sheet", str(root / "contact.jpg"),
                ],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 1)
            report = json.loads((root / "report.json").read_text())
            self.assertFalse(report["technicalChecksPassed"])
            self.assertIn("empty foreground", report["technicalErrors"][0])

    def test_validator_rejects_missing_numbered_frames_and_manifest_tail(self):
        cases = [
            ("middle", [0, 2], None, "consecutive"),
            ("first", [1, 2], None, "start at 0"),
            ("tail", [0, 1], {"outputFrameCount": 3}, "expects 3 frames"),
            ("mapped_tail", [0, 1], {"frameMap": [
                {"outputFrame": index} for index in range(3)
            ]}, "expects 3 frames"),
        ]
        for label, indices, manifest, message in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                rgba = np.zeros((96, 64, 4), dtype=np.uint8)
                rgba[12:84, 12:52] = [200, 90, 50, 255]
                for index in indices:
                    Image.fromarray(rgba).save(root / f"frame_{index:04d}.png")
                if manifest is not None:
                    (root / "extraction_manifest.json").write_text(json.dumps(manifest))
                result = subprocess.run(
                    [
                        sys.executable, str(SCRIPTS / "validate_matte.py"),
                        "--frames-dir", str(root), "--report", str(root / "report.json"),
                        "--contact-sheet", str(root / "contact.jpg"),
                    ],
                    capture_output=True, text=True,
                )
                self.assertEqual(result.returncode, 1, result.stderr)
                report = json.loads((root / "report.json").read_text())
                self.assertFalse(report["technicalChecksPassed"])
                self.assertTrue(any(message in item for item in report["technicalErrors"]))

    def test_validator_accepts_ordinary_nonnumbered_png_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rgba = np.zeros((96, 64, 4), dtype=np.uint8)
            rgba[12:84, 12:52] = [200, 90, 50, 255]
            for name in ["idle.png", "wave.png"]:
                Image.fromarray(rgba).save(root / name)
            result = subprocess.run(
                [
                    sys.executable, str(SCRIPTS / "validate_matte.py"),
                    "--frames-dir", str(root), "--report", str(root / "report.json"),
                    "--contact-sheet", str(root / "contact.jpg"),
                ],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads((root / "report.json").read_text())
            self.assertTrue(report["technicalChecksPassed"])
            self.assertEqual(report["frameCount"], 2)


if __name__ == "__main__":
    unittest.main()
