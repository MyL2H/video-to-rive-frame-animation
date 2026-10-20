# Equal cells at the original image aspect ratio

Legacy Seedream only: read this when the user explicitly requests storyboard generation. The default uses Seedance videos and does not slice a grid.

Read this before generating or processing a return storyboard. The numerical canvas/cell contract controls deterministic slicing. Subject placement and appearance inside those cells are advisory by default; canvas dimensions alone do not prove semantic layout quality.

## Geometry contract

Let one complete source frame be `W × H`, with reduced ratio `p:q`. Every panel must have exactly that ratio. Do not use the aspect ratio of the two-endpoint reference strip as the panel ratio. Use four columns and three rows, row-major:

```text
panelWidth  = k * p
panelHeight = k * q
canvasWidth = 4 * panelWidth
canvasHeight = 3 * panelHeight
12 * panelWidth * panelHeight <= 4,624,220
```

The helper chooses the largest even `k` within the pixel budget; a custom size must satisfy the same exact cross-product ratio and divisibility checks. It fails when an exact ratio cannot fit. Never round each axis independently or stretch a panel to make it fit.

For the current `512×768` sources:

- Original single-frame ratio: **2:3**.
- Equal panel size: **504×756**, one uniform scale `63/64` on both axes.
- Full storyboard: **2016×2268**, whose ratio is **8:9**, not 2:3.
- Column boundaries: `0, 504, 1008, 1512, 2016`.
- Row boundaries: `0, 756, 1512, 2268`.
- Panel `(c,r)` occupies `[504c, 756r, 504(c+1), 756(r+1))`, zero-based.

Preserve source-frame coordinates inside each cell: `(x,y)` becomes `(c*panelWidth+s*x, r*panelHeight+s*y)`. This applies to the subject's scale, body proportions, feet anchor and surrounding empty space. A panel is a uniformly scaled full frame, not a tightly cropped character thumbnail. Never scale each pose to its own bounding box. Keep the same props throughout the sequence.

`storyboard_geometry.py` appends this numerical contract to both transparent and RGB requests, including custom prompts. Custom prompts add motion guidance; they must not replace the geometry contract. The prompt asks for no grid lines, panel labels, gutters, borders or outer padding. If the provider nevertheless adds them, record the defect and continue normal slicing/matting rather than automatically regenerating.

## Technical validation and advisory review

1. Run `prepare`, then `generate`, or use `run` for the full pipeline. Retain the unmodified `generated/storyboard.png`.
2. The helper requires exact requested canvas dimensions, the configured source aspect ratio per fixed cell and twelve nonempty Alpha cells. Wrong dimensions or empty cells are technical errors. RGB content is checked for nonempty usable frames after matting. These failures stop processing; do not stretch a wrong-sized board to hide them.
3. When source Alpha is available, the helper measures visible pixels in the outer half of margins that are clear in both endpoints. This is a conservative warning heuristic, not proof of invalid layout: valid pose changes can enter these areas. Margin findings go into `warnings` rather than `errors` and do not stop generation or processing.
4. `generated/layout_validation.json` reports `automaticPassed` for technical checks, separate `errors` and `warnings`, `visualReviewRecommended: true` and `requiresVisualReview: false`. The local `storyboard_grid_overlay.png` remains available for review. A technical pass does not claim perfect appearance.
5. View the raw board/overlay when available. Record scale/feet drift, uneven or early-finished motion, expression reversals, gutters, semantic cell placement, fragments, clipping, proportions or prop differences as visual warnings. Continue the default workflow; do not ask the user to approve these already-permitted imperfections. If visual inspection is unavailable, state that limitation and continue.
6. Slice the twelve fixed rectangles row-major. Resize interiors uniformly to `W×H`, replace frames 0/11 with the exact source endpoints, preserve provider Alpha or matte saved RGB fallback files, then write the half-resolution assets. Generated endpoint pixel differences alone are not a failure: exact endpoint restoration is part of normal processing.
7. Measurements and uniform scale/anchor stabilization are optional. Preserve exact endpoints and record any stabilization. Do not introduce adaptive panel crops, per-row stretching or fragment deletion to conceal defects.

## Retry boundary

Do not retry or declare generation failure solely because of visual warnings. Use the Seedream frames directly and complete the build, interaction tests and any requested asset replacement. Do not substitute source-reverse sampling for cosmetic reasons.

Retry only genuine generation/transport failures, unreadable payloads, incorrect canvas dimensions or missing/empty usable frames, with at most two additional attempts for the same technical failure. Stricter visual acceptance applies only when explicitly requested by the user; record that requested policy separately from the default technical result.
