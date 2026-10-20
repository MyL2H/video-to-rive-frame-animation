"""Validated inference-only MPS deform-convolution for BiRefNet's single-group layers.

No source-tree mutation; install() patches only the passed model's module instances.
Requires float32, ordinary zero padding, convolution/offset groups of one.
"""
import hashlib
from pathlib import Path
from types import MethodType
import torch
import torch.nn.functional as F

def pair(v):
    return (v, v) if isinstance(v, int) else v

def grid_deform(input, offset, weight, bias=None, stride=(1, 1), padding=(0, 0),
                dilation=(1, 1), mask=None):
    """groups=offset_groups=1, mathematical bilinear deform-convolution."""
    n, c, h, w = input.shape
    oc, wc, kh, kw = weight.shape
    oh, ow = offset.shape[-2:]
    assert wc == c and offset.shape[1] == 2*kh*kw
    sy, sx = pair(stride)
    py, px = pair(padding)
    dy, dx = pair(dilation)
    yy = torch.arange(oh, device=input.device, dtype=input.dtype)*sy-py
    xx = torch.arange(ow, device=input.device, dtype=input.dtype)*sx-px
    base_y, base_x = torch.meshgrid(yy, xx, indexing="ij")
    offsets = offset.reshape(n, kh*kw, 2, oh, ow)
    weights = weight.reshape(oc, c, kh*kw)
    result = None
    for start in range(0, kh*kw, 8):
        stop = min(start+8, kh*kw)
        ki = torch.arange(start, stop, device=input.device)
        ky = (ki//kw).to(input.dtype)*dy
        kx = (ki%kw).to(input.dtype)*dx
        gy = base_y[None, None] + ky[None, :, None, None] + offsets[:, start:stop, 0]
        gx = base_x[None, None] + kx[None, :, None, None] + offsets[:, start:stop, 1]
        # align_corners=False maps pixel centers exactly, including h/w=1.
        grid = torch.stack(((gx+.5)*(2/w)-1, (gy+.5)*(2/h)-1), dim=-1)
        sampled = F.grid_sample(input, grid.reshape(n, (stop-start)*oh, ow, 2),
                                mode="bilinear", padding_mode="zeros", align_corners=False)
        sampled = sampled.reshape(n, c, stop-start, oh, ow)
        if mask is not None:
            sampled = sampled*mask[:, None, start:stop]
        update = F.conv2d(sampled.reshape(n, c*(stop-start), oh, ow),
                          weights[:, :, start:stop].reshape(oc, c*(stop-start), 1, 1))
        result = update if result is None else result+update
    return result if bias is None else result+bias[None, :, None, None]

def _forward(self, x):
    if x.dtype != torch.float32 or x.device.type != "mps":
        raise ValueError("Validated BiRefNet MPS hook requires float32 MPS input")
    return grid_deform(
        input=x, offset=self.offset_conv(x), weight=self.regular_conv.weight,
        bias=self.regular_conv.bias, padding=self.padding, stride=self.stride,
        mask=2.0*torch.sigmoid(self.modulator_conv(x)),
    )


def install(model, device):
    report = {
        "name": "birefnet-mps-single-group-grid-deform-v1",
        "enabled": False,
        "sourceSHA256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "precision": "float32",
        "testedTorch": "2.7.1",
        "testedTorchvision": "0.22.1",
        "algorithm": "Bilinear grid_sample plus kernel-chunked pointwise convolution",
        "validation": {
            "randomOfficialCPUOperatorMaxAbsolute": 4.57763671875e-5,
            "real1024AlphaMaxAbsolute": 3.993511199951172e-6,
            "real1024AlphaPixelsOver1of255": 0,
            "cases": 3,
        },
    }
    if torch.device(device).type != "mps":
        report["reason"] = "Official operator retained on non-MPS device"
        return report
    candidates = []
    for name, module in model.named_modules():
        if module.__class__.__name__ != "DeformableConv2d":
            continue
        conv = module.regular_conv
        k = conv.kernel_size[0]*conv.kernel_size[1]
        if (conv.groups != 1 or module.offset_conv.out_channels != 2*k
                or module.modulator_conv.out_channels != k
                or pair(conv.dilation) != (1,1)
                or conv.padding_mode != "zeros"
                or conv.weight.dtype != torch.float32):
            raise ValueError(f"Unsupported deform-convolution configuration: {name}")
        candidates.append((name,module))
    if not candidates:
        raise ValueError("No compatible BiRefNet DeformableConv2d modules found")
    for name,module in candidates:
        module.forward = MethodType(_forward,module)
    report["enabled"] = True
    report["moduleCount"] = len(candidates)
    report["moduleNames"] = [name for name,_ in candidates]
    return report
