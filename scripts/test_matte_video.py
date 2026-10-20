"""Verify complete-video publication and exact alpha transport with a tiny backend."""
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from extract_alpha_video_frames import frames
from matte_video import produce_video
from test_alpha_video_frames import sample_frames, write_video


class FixtureBackend:
    provenance = {"name": "fixture", "temporalModel": False}

    def __init__(self, fail_at=None, invalid=None):
        self.count = 0
        self.fail_at = fail_at
        self.invalid = invalid
        self.outputs = []

    def __call__(self, rgb):
        if self.count == self.fail_at:
            if self.invalid == "size":
                return np.zeros((2, 2, 4), dtype=np.uint8)
            if self.invalid == "opaque":
                return np.full((*rgb.shape[:2], 4), 255, dtype=np.uint8)
            if self.invalid == "empty":
                return np.zeros((*rgb.shape[:2], 4), dtype=np.uint8)
            raise RuntimeError("backend failed midstream")
        rgba = sample_frames(1, (rgb.shape[1], rgb.shape[0]))[0]
        rgba[:, :, :3] = rgb
        self.outputs.append(rgba)
        self.count += 1
        return rgba


class CompleteVideoTests(unittest.TestCase):
    def test_every_frame_is_processed_and_preserved_losslessly_before_sampling(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "original.mkv"
            write_video(source, sample_frames(9, opaque=True), "24000/1001")
            backend = FixtureBackend()
            progress = []
            report = produce_video(source, root / "result", backend,
                                   save_preview=True, progress_callback=progress.append)
            self.assertEqual(backend.count, 9)
            self.assertEqual([p["processedFrames"] for p in progress], list(range(1, 10)))
            self.assertEqual(report["sourceFpsRational"], "24000/1001")
            self.assertEqual(report["sourceSize"], [32, 24])
            self.assertTrue(report["losslessRGBAValidation"]["passed"])
            self.assertEqual(report["preview"]["decodedFrameCount"], 9)
            stream = frames(root / "result/transparent_master.mkv",
                            report["alphaVideo"]["decoderInputParams"])
            next(stream)
            actual = list(stream)
            self.assertEqual(len(actual), 9)
            for expected, result in zip(backend.outputs, actual):
                np.testing.assert_array_equal(expected, result)
            saved = json.loads((root / "result/matte_manifest.json").read_text())
            self.assertEqual(saved["processedFrameCount"], 9)
            self.assertFalse(saved["backend"]["temporalModel"])

    def test_midstream_failures_never_publish_partial_video_or_manifest(self):
        for invalid in (None, "size", "opaque", "empty"):
            with self.subTest(invalid=invalid), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                write_video(root / "source.mkv", sample_frames(7, opaque=True))
                with self.assertRaises((RuntimeError, ValueError)):
                    produce_video(root / "source.mkv", root / "result",
                                  FixtureBackend(fail_at=3, invalid=invalid), save_preview=True)
                self.assertFalse((root / "result").exists())
                self.assertEqual(list(root.glob(".matte-video-*")), [])

    def test_existing_success_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = root / "result"
            result.mkdir()
            marker = result / "matte_manifest.json"
            marker.write_text('{"previous":"valid"}\n')
            with self.assertRaisesRegex(ValueError, "absent or empty"):
                produce_video(root / "missing-source.mp4", result, FixtureBackend())
            self.assertEqual(marker.read_text(), '{"previous":"valid"}\n')


if __name__ == "__main__":
    unittest.main()
