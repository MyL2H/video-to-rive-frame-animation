"""Exercise alpha-video import, provider rounding, endpoints and return timing."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image

from prepare_frame_assets import resize_rgba_premultiplied
from test_alpha_video_frames import sample_frames, write_video


class SeedanceAlphaPipelineTests(unittest.TestCase):
    def test_complete_video_alpha_survives_normalization_and_exact_endpoints(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Provider rounded height down by one; source is exactly 2:3.
            original = sample_frames(97, (32, 47), opaque=True)
            alpha = sample_frames(97, (32, 47))
            write_video(root / "original.mkv", original)
            write_video(root / "alpha.mkv", alpha)
            endpoints = []
            for name, color in (("first", (60, 30, 80, 255)),
                                ("last", (30, 80, 50, 255))):
                image = Image.new("RGBA", (32, 48), (0, 0, 0, 0))
                image.paste(color, (8, 8, 24, 40))
                path = root / f"{name}.png"
                image.save(path)
                endpoints.append(path)
            output = root / "processed"
            result = subprocess.run([
                sys.executable, str(Path(__file__).parent / "process_seedance_return_frames.py"),
                "--video", str(root / "original.mkv"),
                "--alpha-video", str(root / "alpha.mkv"),
                "--first-frame", str(endpoints[0]), "--last-frame", str(endpoints[1]),
                "--output-dir", str(output),
            ], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            report = json.loads((output / "manifest.json").read_text())
            self.assertTrue(report["technicalChecksPassed"])
            self.assertFalse(report["legacyRGBMatteUsed"])
            self.assertEqual((report["frameCount"], report["fps"], report["durationSeconds"]), (12, 12, 1))
            self.assertEqual([x["videoDecodedFrame"] for x in report["sampling"]],
                             [round(i * 96 / 11) for i in range(12)])
            for index in (1, 5, 10):
                native = Image.open(output / "generated_frames_native_rgba" / f"frame_{index:04d}.png")
                expected_native = alpha[round(index * 96 / 11)]
                np.testing.assert_array_equal(np.asarray(native), expected_native)
                padded = Image.new("RGBA", (32, 48), (0, 0, 0, 0))
                padded.paste(native, (0, 0))
                final = Image.open(output / "frames_rgba" / f"frame_{index:04d}.png")
                np.testing.assert_array_equal(np.asarray(final), np.asarray(padded))
                half = Image.open(output / "frames_50_png" / f"frame_{index:04d}.png")
                np.testing.assert_array_equal(np.asarray(half),
                                             np.asarray(resize_rgba_premultiplied(padded, (16, 24))))
            for source, index in zip(endpoints, (0, 11)):
                target = output / "frames_rgba" / f"frame_{index:04d}.png"
                self.assertEqual(hashlib.sha256(source.read_bytes()).digest(),
                                 hashlib.sha256(target.read_bytes()).digest())
            self.assertEqual(len(list((output / "frames_50_webp").glob("*.webp"))), 12)


if __name__ == "__main__":
    unittest.main()
