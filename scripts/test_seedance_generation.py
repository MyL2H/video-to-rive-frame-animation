#!/usr/bin/env python3
"""Offline transport regression tests; never call or upload to a generation API."""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

import build_seedance_return_videos as generation


class GenerationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.args = argparse.Namespace(
            output_root=self.root, boundary_dir=self.root / "return_3000ms",
            source_info={"sourceVideo": "source.mp4", "sourceSha256": "source-hash",
                         "sourceSize": [8, 12], "sourceFps": 24,
                         "sourceFrameCount": 141, "boundaryFrame": 72,
                         "boundaryFrameMs": 3000, "boundaryLabelMs": 3000},
            first_rgb=np.full((12, 8, 3), 123, dtype=np.uint8),
            last_rgb=np.full((12, 8, 3), 235, dtype=np.uint8),
            model=generation.DEFAULT_MODEL, prompt=None, env_file=self.root / "absent.env",
            timeout=1, wait_timeout=1, poll_interval=0.01)

    def test_exact_endpoints_and_full_duration_payload(self):
        path = generation.prepare(self.args)
        request = json.loads(path.read_text())
        self.assertEqual(request["duration"], 4)
        self.assertEqual(request["model"], "doubao-seedance-2-5-260628")
        self.assertEqual(request["ratio"], "adaptive")
        self.assertIs(request["generate_audio"], False)
        self.assertIs(request["watermark"], False)
        self.assertEqual(request["output_format"], "mp4")
        self.assertEqual([item["role"] for item in request["content"][1:]], ["first_frame", "last_frame"])
        for name, expected in (("first_frame", self.args.first_rgb), ("last_frame", self.args.last_rgb)):
            with Image.open(path.parent / "inputs" / f"{name}.png") as image:
                self.assertEqual(image.mode, "RGB")
                np.testing.assert_array_equal(np.asarray(image), expected)
        self.assertEqual((path.parent / "prompt.txt").read_text().strip(), request["content"][0]["text"])
        self.assertEqual(json.loads((path.parent / "metadata.json").read_text())["sampleFrameCount"], 12)

    def test_resume_reuses_task_and_preserves_entire_download(self):
        # Includes bytes beyond a hypothetical first-second segment; output must be identical.
        video = b"first-second" + bytes(range(256)) * 10000 + b"fourth-second"
        responses = [{"id": "private-task-id"},
                     {"status": "succeeded", "id": "private-task-id",
                      "content": {"video_url": "https://example.test/video?secret=private-token"}}]
        output = io.StringIO()
        with patch.object(generation, "request_json", side_effect=responses) as transport, \
             patch.object(generation, "auth_headers", return_value={}), \
             patch.object(generation.urllib.request, "urlopen", return_value=io.BytesIO(video)) as remote, \
             contextlib.redirect_stdout(output):
            generation.submit(self.args)
            generation.run(self.args)
            generation.run(self.args)
            generation.submit(self.args)
        self.assertEqual(transport.call_count, 2)
        self.assertEqual(transport.call_args_list[0].kwargs["method"], "POST")
        self.assertEqual(remote.call_count, 1)
        self.assertEqual((self.args.boundary_dir / "generated_full.mp4").read_bytes(), video)
        for name in (".task.json", ".status_private.json"):
            self.assertEqual((self.args.boundary_dir / name).stat().st_mode & 0o777, 0o600)
        public = (self.args.boundary_dir / "generation_status.json").read_text() + output.getvalue()
        for private in ("private-task-id", "private-token", "https://"):
            self.assertNotIn(private, public)

    def test_uncertain_post_blocks_duplicate_submission(self):
        with patch.object(generation, "request_json", side_effect=RuntimeError("mock timeout")) as transport, \
             patch.object(generation, "auth_headers", return_value={}):
            with self.assertRaisesRegex(RuntimeError, "mock timeout"):
                generation.submit(self.args)
            with self.assertRaisesRegex(RuntimeError, "uncertain outcome"):
                generation.submit(self.args)
            self.assertEqual(transport.call_count, 1)
        marker = self.args.boundary_dir / ".submit_pending.json"
        self.assertEqual(marker.stat().st_mode & 0o777, 0o600)

    def test_local_auth_failures_leave_no_pending_marker(self):
        environments = [{}, {"SEEDANCE_API_KEY_ID": "offline-id",
                             "SEEDANCE_API_KEY_SECRET": "offline-secret",
                             "SEEDANCE_ARK_AUTH_MODE": "invalid-mode"}]
        for environment in environments:
            with self.subTest(environment=list(environment)), \
                 patch.dict(os.environ, environment, clear=True), \
                 patch.object(generation.urllib.request, "urlopen") as remote:
                with self.assertRaises(ValueError):
                    generation.submit(self.args)
                remote.assert_not_called()
                self.assertFalse((self.args.boundary_dir / ".submit_pending.json").exists())

    def test_explicit_401_rejection_is_recorded_and_can_be_resubmitted(self):
        import urllib.error
        failure = urllib.error.HTTPError("https://example.test/private", 401, "bad", {},
                                         io.BytesIO(b"token-secret https://private.test/result"))
        with patch.dict(os.environ, {"ARK_API_KEY": "offline-fake-key"}, clear=True), \
             patch.object(generation.urllib.request, "urlopen",
                          side_effect=[failure, io.BytesIO(b'{"id":"private-task-id"}')]) as remote, \
             contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(generation.ApiRejectedError):
                generation.submit(self.args)
            self.assertFalse((self.args.boundary_dir / ".submit_pending.json").exists())
            self.assertFalse((self.args.boundary_dir / ".task.json").exists())
            history = (self.args.boundary_dir / "submission_rejections.json").read_text()
            self.assertEqual(json.loads(history)["attempts"][0]["httpStatus"], 401)
            self.assertNotIn("token-secret", history)
            self.assertNotIn("https://", history)
            generation.submit(self.args)
            self.assertEqual(remote.call_count, 2)
            self.assertTrue((self.args.boundary_dir / ".task.json").exists())
        # A known task can be reused without any credentials or network access.
        with patch.dict(os.environ, {}, clear=True), \
             patch.object(generation.urllib.request, "urlopen") as remote, \
             contextlib.redirect_stdout(io.StringIO()):
            generation.submit(self.args)
            remote.assert_not_called()

    def test_ambiguous_http_and_invalid_json_keep_pending_marker(self):
        import urllib.error
        failures = [urllib.error.HTTPError("https://example.test/private", code, "bad", {},
                                           io.BytesIO(b"private")) for code in (408, 500)]
        failures.append(io.BytesIO(b"invalid JSON response"))
        for index, result in enumerate(failures):
            self.args.boundary_dir = self.root / f"ambiguous-{index}"
            with self.subTest(case=index), \
                 patch.dict(os.environ, {"ARK_API_KEY": "offline-fake-key"}, clear=True), \
                 patch.object(generation.urllib.request, "urlopen", side_effect=[result]) as remote:
                with self.assertRaises(RuntimeError):
                    generation.submit(self.args)
                self.assertTrue((self.args.boundary_dir / ".submit_pending.json").exists())
                with self.assertRaisesRegex(RuntimeError, "uncertain outcome"):
                    generation.submit(self.args)
                self.assertEqual(remote.call_count, 1)

    def test_prepared_task_rejects_changed_inputs(self):
        generation.prepare(self.args)
        self.args.prompt = "Different requested motion"
        with self.assertRaisesRegex(ValueError, "different inputs"):
            generation.prepare(self.args)

    def test_api_error_does_not_echo_credentials_or_urls(self):
        import urllib.error
        failure = urllib.error.HTTPError("https://example.test/private", 401, "bad", {},
                                         io.BytesIO(b"token-secret https://private.test/result"))
        with patch.object(generation, "auth_headers", return_value={}), \
             patch.object(generation.urllib.request, "urlopen", side_effect=failure):
            with self.assertRaisesRegex(RuntimeError, "HTTP 401") as caught:
                generation.request_json("/test", env_file=self.args.env_file)
        self.assertNotIn("token-secret", str(caught.exception))
        self.assertNotIn("https://", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
