#!/usr/bin/env python3
"""Regression checks for advisory quality warnings and hard usability failures.

Run with: python scripts/test_storyboard_validation.py
Uses only synthetic local images; no provider calls or segmentation downloads.
"""
from __future__ import annotations

import argparse
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image, ImageDraw

import build_seedream_return_frames as builder
from storyboard_geometry import layout, validate_image


SOURCE_SIZE = (32, 48)


def endpoint() -> Image.Image:
    image = Image.new('RGBA', SOURCE_SIZE)
    ImageDraw.Draw(image).rectangle((8, 8, 23, 43), fill=(80, 40, 180, 255))
    return image


def storyboard() -> tuple[Image.Image, dict]:
    spec = layout(SOURCE_SIZE, '128x144')
    board = Image.new('RGBA', spec['storyboardSize'])
    for index, box in enumerate(spec['panels']):
        panel = endpoint()
        if index == 4:
            # Technically usable pose with an anchor/scale shift into a clear margin.
            ImageDraw.Draw(panel).rectangle((1, 8, 23, 43), fill=(80, 40, 180, 255))
        board.paste(panel, box[:2])
    return board, spec


class StoryboardValidationTests(unittest.TestCase):
    def test_margin_drift_warns_without_blocking(self):
        board, spec = storyboard()
        result = validate_image(board, spec, [endpoint(), endpoint()])
        self.assertTrue(result['automaticPassed'])
        self.assertEqual(result['errors'], [])
        self.assertEqual(len(result['warnings']), 1)
        self.assertIn('Panel 5', result['warnings'][0])
        self.assertFalse(result['requiresVisualReview'])
        self.assertTrue(result['visualReviewRecommended'])

    def test_wrong_actual_canvas_is_still_rejected(self):
        board, spec = storyboard()
        result = validate_image(board.crop((0, 0, 127, 144)), spec)
        self.assertFalse(result['automaticPassed'])
        self.assertIn('Expected exact canvas', result['errors'][0])

    def test_invalid_requested_geometry_is_still_rejected(self):
        for requested in ('127x144', '128x96'):
            with self.subTest(requested=requested), self.assertRaises(ValueError):
                layout(SOURCE_SIZE, requested)

    def test_empty_panel_is_still_rejected(self):
        board, spec = storyboard()
        board.paste((0, 0, 0, 0), spec['panels'][4])
        result = validate_image(board, spec)
        self.assertFalse(result['automaticPassed'])
        self.assertIn('Panel 5 is empty', result['errors'])


class ProcessingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.generated = root / 'generated'
        inputs = root / 'inputs'
        source = root / 'source'
        for directory in (self.generated, inputs, source):
            directory.mkdir()
        board, spec = storyboard()
        board.save(self.generated / 'storyboard.png')
        for index, box in enumerate(spec['panels']):
            board.crop(box).save(self.generated / f'frame_{index:04d}.png')
        for name in ('first_frame', 'last_frame'):
            endpoint().convert('RGB').save(inputs / f'{name}.png')
        for index in range(2):
            endpoint().save(source / f'frame_{index:04d}.png')
        (source / 'extraction_manifest.json').write_text(json.dumps({'frameMap': [
            {'sourceFrame': 0, 'outputFrame': 0},
            {'sourceFrame': 12, 'outputFrame': 1},
        ]}))
        (root / 'request.json').write_text(json.dumps({'size': '128x144'}))
        self.args = argparse.Namespace(
            boundary_dir=lambda args: root, source_rgba_dir=source,
            storyboard_geometry=spec, boundary_frame=12, target_fps=12,
            source_fps=24, replace=False, scale=0.5,
            webp_quality=82, webp_method=6, model=builder.DEFAULT_MODEL,
            background_min=230, background_max=241, edge_pixels=2,
            matting_model='unused', provider='CPUExecutionProvider',
        )

    def test_warning_storyboard_processes_all_frames_and_preserves_endpoints(self):
        with patch.object(builder, 'new_session', side_effect=AssertionError('Alpha must not use rembg')):
            output = builder.process(self.args)
        manifest = json.loads((output / 'manifest.json').read_text())
        self.assertEqual(manifest['frameCount'], 12)
        self.assertEqual(manifest['returnDurationSeconds'], 1)
        self.assertTrue(manifest['layoutValidation']['automaticPassed'])
        self.assertEqual(len(manifest['layoutValidation']['warnings']), 1)
        for directory, extension in (('frames_rgba', 'png'), ('frames_50_png', 'png'), ('frames_50_webp', 'webp')):
            self.assertEqual(len(list((output / directory).glob(f'frame_*.{extension}'))), 12)
        for index in (0, 11):
            with Image.open(output / 'frames_rgba' / f'frame_{index:04d}.png') as actual:
                self.assertTrue(np.array_equal(np.asarray(actual), np.asarray(endpoint())))

    def test_generation_keeps_warning_board_without_a_retry(self):
        board, _ = storyboard()
        payload = io.BytesIO()
        board.save(payload, format='PNG')
        root = self.generated.parent
        (root / 'request.json').write_text(json.dumps({
            'size': '128x144', 'n': 1, 'background': 'transparent',
        }))
        self.args.env_file = root / 'no-credentials-file'
        self.args.timeout = 1
        self.args.replace = True
        with patch.object(builder, 'credentials', return_value=('unused', ())), \
             patch.object(builder, 'post_image_request', return_value={'data': [{}]}) as request, \
             patch.object(builder, 'image_payload', return_value=payload.getvalue()):
            generated = builder.generate(self.args)
        self.assertEqual(request.call_count, 1)
        report = json.loads((generated / 'layout_validation.json').read_text())
        self.assertTrue(report['automaticPassed'])
        self.assertEqual(len(report['warnings']), 1)
        self.assertEqual(len(list(generated.glob('frame_*.png'))), 12)
        output = builder.process(self.args)
        self.assertTrue((output / 'manifest.json').is_file())

    def test_missing_generated_frame_is_still_rejected(self):
        (self.generated / 'frame_0005.png').unlink()
        with self.assertRaisesRegex(ValueError, 'Expected 12 generated frames'):
            builder.process(self.args)

    def test_unreadable_storyboard_is_still_rejected(self):
        (self.generated / 'storyboard.png').write_bytes(b'not a PNG')
        with self.assertRaises(OSError):
            builder.process(self.args)

    def test_rgb_matting_with_no_visible_subject_is_still_rejected(self):
        # No raw board also exercises the separate-image processing path.
        (self.generated / 'storyboard.png').unlink()
        for path in self.generated.glob('frame_*.png'):
            Image.new('RGB', SOURCE_SIZE, (235, 235, 235)).save(path)
        empty = np.zeros((SOURCE_SIZE[1], SOURCE_SIZE[0], 4), dtype=np.uint8)
        with patch.object(builder, 'new_session', return_value=object()), \
             patch.object(builder, 'remove', return_value=empty), \
             patch.object(builder, 'matte_rgb_frame', return_value=(empty, {})):
            with self.assertRaisesRegex(ValueError, 'Processed frame has no visible subject'):
                builder.process(self.args)


if __name__ == '__main__':
    unittest.main()
