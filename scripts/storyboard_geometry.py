"""Source-aspect-ratio storyboard geometry and conservative seam checks."""
from __future__ import annotations

import math
from PIL import Image, ImageDraw
import numpy as np

COLUMNS, ROWS = 4, 3
MAX_PIXELS = 4_624_220


def dimensions(value: str) -> tuple[int, int]:
    try:
        width, height = map(int, value.lower().split('x'))
    except (ValueError, AttributeError):
        raise ValueError('Size must be WIDTHxHEIGHT') from None
    if width <= 0 or height <= 0:
        raise ValueError('Size must have positive dimensions')
    return width, height


def layout(source_size: tuple[int, int], requested: str | None = None) -> dict:
    width, height = source_size
    if min(width, height) <= 0:
        raise ValueError('Source dimensions must be positive')
    divisor = math.gcd(width, height)
    p, q = width // divisor, height // divisor
    if requested is None:
        # Even panel dimensions, one identical isotropic scale for both axes.
        factor = math.isqrt(MAX_PIXELS // (COLUMNS * ROWS * p * q))
        factor -= factor % 2
        if factor < 2:
            raise ValueError('Cannot fit this exact source ratio into the storyboard pixel budget')
        board_width, board_height = COLUMNS * p * factor, ROWS * q * factor
    else:
        board_width, board_height = dimensions(requested)
    if board_width * board_height > MAX_PIXELS:
        raise ValueError(f'Storyboard exceeds {MAX_PIXELS} pixels')
    if board_width % COLUMNS or board_height % ROWS:
        raise ValueError('Storyboard must divide exactly into four columns and three rows')
    cell_width, cell_height = board_width // COLUMNS, board_height // ROWS
    if cell_width * height != cell_height * width:
        raise ValueError(f'Each panel must have the SOURCE ratio {p}:{q}; got {cell_width}x{cell_height}')
    return {
        'sourceSize': [width, height], 'sourceAspectRatio': [p, q],
        'storyboardSize': [board_width, board_height],
        'cellSize': [cell_width, cell_height], 'columns': COLUMNS, 'rows': ROWS,
        'uniformScale': cell_width / width,
        'xBoundaries': [i * cell_width for i in range(COLUMNS + 1)],
        'yBoundaries': [i * cell_height for i in range(ROWS + 1)],
        'panels': [[c * cell_width, r * cell_height, (c + 1) * cell_width,
                    (r + 1) * cell_height] for r in range(ROWS) for c in range(COLUMNS)],
    }


def prompt_contract(spec: dict) -> str:
    w, h = spec['sourceSize']
    bw, bh = spec['storyboardSize']
    cw, ch = spec['cellSize']
    p, q = spec['sourceAspectRatio']
    rectangles = '; '.join(f'{i + 1}:({x0},{y0})-({x1},{y1})'
                            for i, (x0, y0, x1, y1) in enumerate(spec['panels']))
    return (
        f' MANDATORY PIXEL GEOMETRY: each original endpoint canvas is {w}x{h}, '
        f'aspect ratio {p}:{q}. This refers to ONE endpoint, not the combined two-pose reference. '
        f'Output exactly {bw}x{bh} pixels. Use exactly four equally wide columns and three '
        f'equally high rows. EVERY panel is exactly {cw}x{ch}, aspect ratio {p}:{q}. '
        f'Invisible column boundaries: {spec["xBoundaries"]}; invisible row boundaries: '
        f'{spec["yBoundaries"]}. Row-major panel rectangles (right/bottom exclusive): {rectangles}. '
        f'Transfer source coordinates using the SAME scale on both axes, s={cw}/{w}={ch}/{h}: '
        'a source point (x,y) in panel (column,row), zero-based, maps to '
        f'(column*{cw}+s*x, row*{ch}+s*y). Preserve the reference body proportions, subject '
        'occupancy, feet anchor and normalized empty margins within every panel. Animate the pose '
        'only; never scale to fit an individual pose or independently reposition a row or column. '
        'All twelve full subjects and held props must fit entirely inside their own rectangles. '
        'No unequal cells, merged cells, collage layout, staggered rows, shared limbs, cross-cell '
        'pixels, gutters, outer padding, drawn grid lines, labels, crop, stretch, squash, or zoom. '
        'Keep every held prop continuously present and recognizable. These geometry constraints '
        'also apply to custom motion instructions and to RGB fallback output.'
    )


def validate_image(image: Image.Image, spec: dict, endpoints: list[Image.Image] | None = None) -> dict:
    """Detect wrong dimensions, empty cells, and bleed into source-clear margins.

    This is not a semantic grid/identity validator; a visual overlay review is mandatory.
    RGB output defers content checks until after matting.
    """
    errors = []
    if list(image.size) != spec['storyboardSize']:
        errors.append(f'Expected exact canvas {spec["storyboardSize"]}, got {list(image.size)}')
        return {'automaticPassed': False, 'errors': errors, 'requiresVisualReview': True}
    rgba = np.asarray(image.convert('RGBA'))
    has_alpha = bool((rgba[:, :, 3] < 8).any())
    margins = None
    if endpoints:
        boxes = [im.convert('RGBA').getchannel('A').point(lambda x: 255 if x > 8 else 0).getbbox() for im in endpoints]
        if any(box is None for box in boxes):
            errors.append('An endpoint has no visible subject')
        else:
            w, h = spec['sourceSize']
            if any(im.size != (w, h) for im in endpoints):
                errors.append('Endpoint canvas size differs from source video')
            margins = [min(b[0] for b in boxes) / w, min(b[1] for b in boxes) / h,
                       min(w-b[2] for b in boxes) / w, min(h-b[3] for b in boxes) / h]
    cells = []
    cw, ch = spec['cellSize']
    for i, (x0, y0, x1, y1) in enumerate(spec['panels']):
        if not has_alpha:
            break
        a = rgba[y0:y1, x0:x1, 3] > 8
        visible = int(a.sum())
        item = {'panel': i + 1, 'visiblePixels': visible}
        if not visible:
            errors.append(f'Panel {i+1} is empty')
        if margins:
            # The outer half of a margin clear in BOTH endpoints must stay clear.
            # Allow pose motion within the inner half; never silently erase detected bleed.
            bands = [int(margins[0]*cw/2), int(margins[1]*ch/2),
                     int(margins[2]*cw/2), int(margins[3]*ch/2)]
            counts = [int(a[:,:bands[0]].sum()) if bands[0] else 0,
                      int(a[:bands[1],:].sum()) if bands[1] else 0,
                      int(a[:,cw-bands[2]:].sum()) if bands[2] else 0,
                      int(a[ch-bands[3]:,:].sum()) if bands[3] else 0]
            item['outerMarginVisiblePixels'] = counts
            if any(counts):
                errors.append(f'Panel {i+1} has pixels in source-clear outer margins: {counts}')
        cells.append(item)
    return {'automaticPassed': not errors, 'errors': errors, 'cells': cells,
            'contentCheck': 'source-clear margins' if has_alpha and margins else 'visual review required',
            'requiresVisualReview': True}


def grid_overlay(image: Image.Image, spec: dict) -> Image.Image:
    rgba = image.convert('RGBA')
    out = Image.new('RGBA', rgba.size, (255, 0, 255, 255))
    out.alpha_composite(rgba)
    draw = ImageDraw.Draw(out)
    for i, box in enumerate(spec['panels']):
        x0, y0, x1, y1 = box
        draw.rectangle((x0, y0, x1-1, y1-1), outline=(0,255,255,255), width=3)
        draw.text((x0+8,y0+8), str(i+1), fill=(0,0,0,255), stroke_width=2, stroke_fill=(255,255,255,255))
    return out.convert('RGB')
