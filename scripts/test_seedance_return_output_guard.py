"""A failed return rerun must not mix new audits with previously valid assets."""

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("process_seedance_return_frames.py")


class ReturnOutputGuardTests(unittest.TestCase):
    def invoke(self, root, output):
        # These missing inputs ensure the destination guard runs before input
        # processing or any writes. An old successful output must stay intact.
        return subprocess.run([
            sys.executable, str(SCRIPT),
            "--video", str(root / "missing.mp4"),
            "--alpha-video", str(root / "missing_alpha.mkv"),
            "--first-frame", str(root / "missing_first.png"),
            "--last-frame", str(root / "missing_last.png"),
            "--output-dir", str(output),
        ], capture_output=True, text=True)

    def test_rejects_nonempty_output_without_changing_old_assets_or_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "processed"
            (output / "frames_rgba").mkdir(parents=True)
            (output / "manifest.json").write_text('{"technicalChecksPassed": true}')
            (output / "frames_rgba/frame_0000.png").write_bytes(b"old asset sentinel")
            before = {p.relative_to(output): p.read_bytes()
                      for p in output.rglob("*") if p.is_file()}
            result = self.invoke(root, output)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Output directory must be absent or empty", result.stderr)
            after = {p.relative_to(output): p.read_bytes()
                     for p in output.rglob("*") if p.is_file()}
            self.assertEqual(after, before)
            self.assertFalse((output / "sampling_manifest.json").exists())

    def test_rejects_file_destination_without_overwriting_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "processed"
            output.write_bytes(b"existing file")
            result = self.invoke(root, output)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Output directory must be absent or empty", result.stderr)
            self.assertEqual(output.read_bytes(), b"existing file")


if __name__ == "__main__":
    unittest.main()
