#!/usr/bin/env python3
"""Small offline regressions for false erasure, real gaps and edge compositing."""
import unittest
import numpy as np
from rgb_matte import matte_rgb_frame


class MatteRegressionTests(unittest.TestCase):
    def frame(self):
        rgb = np.full((72, 72, 3), 235, dtype=np.uint8)
        model = np.dstack([rgb.copy(), np.zeros((72, 72), np.uint8)])
        return rgb, model

    def test_model_cannot_erase_cream_clothing(self):
        rgb, model = self.frame()
        rgb[15:58, 15:58] = (242, 226, 202)
        result, _ = matte_rgb_frame(rgb, model)
        self.assertEqual(int(result[30, 30, 3]), 255)
        np.testing.assert_array_equal(result[30, 30, :3], rgb[30, 30])
        self.assertEqual(int(result[0, 0, 3]), 0)

    def test_pale_feather_not_deleted_by_uncertain_model(self):
        rgb, model = self.frame()
        rgb[15:50, 28:34] = (248, 238, 226)
        model[15:50, 28:34, 3] = 70
        result, _ = matte_rgb_frame(rgb, model)
        self.assertTrue(np.all(result[18:47, 28:34, 3] == 255))

    def test_small_disconnected_prop_is_not_filtered_by_area(self):
        rgb, model = self.frame()
        rgb[15:58, 15:50] = (90, 80, 100)
        rgb[12, 60] = (180, 120, 40)
        result, info = matte_rgb_frame(rgb, model)
        self.assertEqual(int(result[12, 60, 3]), 255)
        self.assertGreaterEqual(info['keptForegroundComponents'], 2)

    def test_real_enclosed_background_gap_remains_transparent(self):
        rgb, model = self.frame()
        rgb[12:60, 12:60] = (100, 100, 110)
        rgb[25:45, 25:45] = (236, 237, 235)
        model[25:45, 25:45, 3] = 145
        result, info = matte_rgb_frame(rgb, model)
        self.assertEqual(int(result[35, 35, 3]), 0)
        self.assertGreater(info['holePixelsAfterMatte'], 0)

    def test_model_supported_gray_detail_is_not_blindly_punched_out(self):
        rgb, model = self.frame()
        rgb[12:60, 12:60] = (100, 100, 110)
        rgb[25:45, 25:45] = (235, 235, 235)
        model[25:45, 25:45, 3] = 255
        result, _ = matte_rgb_frame(rgb, model)
        self.assertEqual(int(result[35, 35, 3]), 255)

    def test_gray_codec_ringing_is_color_and_model_constrained(self):
        rgb, model = self.frame()
        rgb[15:58, 15:58] = (90, 80, 100)
        rgb[15:58, 14] = (248, 246, 250)
        result, _ = matte_rgb_frame(rgb, model)
        self.assertTrue(np.all(result[15:58, 14, 3] == 0))

    def test_narrow_antialias_unmix_preserves_rgb_composite(self):
        rgb, model = self.frame()
        rgb[15:58, 15:58] = (65, 65, 65)
        rgb[15:58, 14] = (150, 150, 150)  # half foreground plus backdrop
        result, _ = matte_rgb_frame(rgb, model)
        p = result[35, 14].astype(float)
        self.assertLess(abs(p[3] - 128), 2)
        self.assertLess(abs(p[0] - 65), 2)
        reconstructed = p[0] * p[3] / 255 + 235 * (1 - p[3] / 255)
        self.assertLess(abs(reconstructed - 150), 1)

    def test_coherent_nearly_zero_coverage_does_not_fall_back_to_opaque(self):
        rgb, model = self.frame()
        rgb[15:58, 15:58] = (65, 65, 65)
        rgb[15:58, 14] = (235, 232, 235)
        model[15:58, 14, 3] = 255
        result, _ = matte_rgb_frame(rgb, model)
        self.assertLess(int(result[35, 14, 3]), 8)

    def test_chroma_overshoot_does_not_leave_white_scarf_rim(self):
        rgb, model = self.frame()
        rgb[15:58, 15:58] = (160, 70, 70)
        rgb[15:58, 14] = (251, 227, 225)
        model[15:58, 14, 3] = 150
        result, info = matte_rgb_frame(rgb, model)
        self.assertLess(int(result[35, 14, 3]), 32)
        self.assertLess(int(result[35, 14, 1]), 100)
        self.assertGreater(info['edgeChromaRingingCorrectedPixels'], 0)

    def test_complex_border_is_rejected_instead_of_gray_keyed(self):
        rgb, model = self.frame()
        rgb[:, :36] = (10, 130, 190)
        with self.assertRaisesRegex(ValueError, 'flat neutral border'):
            matte_rgb_frame(rgb, model)


if __name__ == '__main__':
    unittest.main()
