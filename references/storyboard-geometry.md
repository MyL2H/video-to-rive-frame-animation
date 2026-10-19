# Equal cells at the original image aspect ratio

Read this before generating or accepting any return storyboard. Canvas dimensions alone do not prove that the model placed twelve poses in twelve equal cells.

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

`storyboard_geometry.py` appends this numerical contract to both transparent and RGB requests, including custom prompts. Custom prompts add motion guidance; they must not replace the geometry contract. No grid lines, panel labels, gutters, borders or outer padding may appear in provider output.

## Validate before acceptance

1. Run `prepare`, then `generate`. Retain the unmodified provider `generated/storyboard.png`.
2. The helper requires the exact requested canvas size, exact source aspect ratio per cell, and twelve nonempty Alpha cells. When source Alpha is available, it rejects visible pixels in the outer half of margins that are clear in both source endpoints. This conservatively catches neighboring feet, clipping, grid artifacts and gross shifts. It writes `generated/layout_validation.json` and a local-only `storyboard_grid_overlay.png` before slicing. A failed check stops generation and standalone `process` also refuses the board.
3. View the original and the overlay. Verify **all twelve** subjects are wholly inside their numbered cell, with equal row/column spacing, no adjacent-cell fragments, no stretched faces/bodies, no independent row scaling and no missing props. Compare the generated first/last panels against the exact source endpoints **before** endpoint replacement. Review identity and natural pose continuity; Alpha/size checks cannot establish these.
4. Only after visual acceptance, run `process`. Interior panels may be resized to `W×H` with the same scale on both axes. The script refuses mismatched aspect ratios. RGB output requires inspection of the saved RGB slices and subsequent matte contact sheet because opaque backgrounds cannot establish subject occupancy automatically.
5. Measure stable head/torso scale and feet anchors against both endpoints. Changing arm span during natural motion is not itself a scale change. A positive automatic report still has `requiresVisualReview: true`; never present it as proof of correct semantic layout or body proportions.

Reject an unequal grid, wrong ratio, neighboring-cell fragments, clipped subject, distorted body proportions, or changed objects. Retry with the same numerical geometry contract at most twice, preserving rejected attempts for diagnosis. If no valid board is obtained, report that return as a generation failure. Do not silently accept it or substitute another action's board.

Do not repair an invalid grid using adaptive crops, independent row/panel resizing, deleting crossing fragments, stretching, or scale stabilization. `stabilize_return_scale.py` remains an explicitly reported fallback only for small residual **uniform** scale/anchor drift on a visually valid equal grid with complete subjects and correct body proportions. It cannot turn a rejected layout into an accepted one. Preserve exact endpoints and revalidate after any allowed stabilization.
