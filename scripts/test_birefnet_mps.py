"""Numerical checks against torchvision's official CPU deform-convolution."""
import unittest

import torch
from torch import nn
from torchvision.ops import deform_conv2d

from birefnet_mps import grid_deform, install


class DeformableConv2d(nn.Module):
    """Minimal structural fixture; the production model comes from BiRefNet."""

    def __init__(self, groups=1):
        super().__init__()
        self.stride = (1, 1)
        self.padding = 1
        self.regular_conv = nn.Conv2d(4, 4, 3, padding=1, groups=groups)
        self.offset_conv = nn.Conv2d(4, 18, 3, padding=1)
        self.modulator_conv = nn.Conv2d(4, 9, 3, padding=1)


class DeformConvolutionEquivalenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        torch.set_num_threads(4)

    def check_operator(self, device):
        torch.manual_seed(730)
        for kernel, stride, dilation, height, width, use_mask, use_bias in [
            (1, 1, 1, 7, 9, True, True),
            (3, 1, 1, 17, 12, True, True),
            (7, 1, 1, 13, 19, True, True),
            (3, 2, 2, 18, 21, True, True),
            (1, 1, 1, 1, 5, True, True),
            (3, 1, 1, 11, 8, False, False),
        ]:
            with self.subTest(device=device, kernel=kernel, stride=stride,
                              dilation=dilation, mask=use_mask):
                padding = kernel // 2
                oh = (height + 2 * padding - dilation * (kernel - 1) - 1) // stride + 1
                ow = (width + 2 * padding - dilation * (kernel - 1) - 1) // stride + 1
                args = {
                    "input": torch.randn(2, 5, height, width),
                    "offset": torch.randn(2, 2 * kernel * kernel, oh, ow) * 2,
                    "weight": torch.randn(4, 5, kernel, kernel),
                    "bias": torch.randn(4) if use_bias else None,
                    "mask": torch.rand(2, kernel * kernel, oh, ow) * 2 if use_mask else None,
                    "stride": stride, "padding": padding, "dilation": dilation,
                }
                with torch.inference_mode():
                    expected = deform_conv2d(**args)
                    actual = grid_deform(**{
                        key: value.to(device) if isinstance(value, torch.Tensor) else value
                        for key, value in args.items()
                    }).cpu()
                torch.testing.assert_close(actual, expected, atol=2e-4, rtol=2e-4)

    def test_cpu_matches_official_operator(self):
        self.check_operator("cpu")

    @unittest.skipUnless(torch.backends.mps.is_available(), "MPS device unavailable")
    def test_mps_matches_official_cpu_operator(self):
        self.check_operator("mps")

    def test_cpu_install_leaves_model_unchanged(self):
        model = nn.Sequential(DeformableConv2d())
        before = model[0].forward
        metadata = install(model, "cpu")
        self.assertFalse(metadata["enabled"])
        self.assertEqual(model[0].forward, before)

    def test_install_preserves_parameter_tensors(self):
        model = nn.Sequential(DeformableConv2d())
        before = {name: param.detach().clone() for name, param in model.named_parameters()}
        identities = {name: id(param) for name, param in model.named_parameters()}
        metadata = install(model, "mps")
        self.assertTrue(metadata["enabled"])
        self.assertEqual(metadata["moduleCount"], 1)
        for name, parameter in model.named_parameters():
            self.assertEqual(id(parameter), identities[name])
            torch.testing.assert_close(parameter, before[name], atol=0, rtol=0)

    def test_rejects_convolution_groups(self):
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            install(nn.Sequential(DeformableConv2d(groups=2)), "mps")

    def test_rejects_unrecognized_model(self):
        with self.assertRaisesRegex(ValueError, "No compatible"):
            install(nn.Sequential(nn.Linear(2, 2)), "mps")

    def test_rejects_unvalidated_precision(self):
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            install(nn.Sequential(DeformableConv2d().half()), "mps")


if __name__ == "__main__":
    unittest.main()
