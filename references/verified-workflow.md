# Verified 12 FPS Workflow and Constraints

This document records frame-processing and Rive invariants for the 12 FPS Seedream profile. Keep source FPS metadata, but play retained frame sequences at 12 FPS.

## Default request handling

Apply this profile without asking for more parameters:

1. Process each video independently.
2. Temporally sample to 12 FPS while preserving motion speed.
3. Always retain decoded source frame `N-1`.
4. Produce transparent full-resolution PNG frames.
5. Resize to 50% with Lanczos.
6. Produce optimized PNG and RGBA WebP at quality 82 and method 6.
7. Generate one high-resolution Seedream return storyboard for every whole-second and end-of-media cut point and slice exactly 12 ordered frames from it.
8. Replace generated return endpoints with exact source frames.
9. Build one combined Rive project sized to the reduced frames.
10. Verify frame continuity, Rive structure, local build, and sampled renders.

If a batch item fails, continue independent items and summarize failures at the end.

## Input specification

### Video

- Readable by OpenCV.
- One video stream; audio is ignored.
- Known FPS and decoded frame count.
- Subject separated from the background well enough for general segmentation.

### Rive project

- Rive CLI executable is `rive`.
- Project directory contains `rive.yaml`, generated `scene.rml`, helper scripts, and `build/`.
- Frame files may live beside the project. `file=` can reference them relatively and embed their bytes in the built `.riv`.

## Execution procedure

### 1. Inspect before transforming

Read source metadata and sample the first, middle, and last frames. Verify that the background is suitable for automatic matte extraction. Record source FPS, decoded frame count, selected 12 FPS indices, and the exact end-frame mapping.

### 2. Temporal sampling and two-stage background removal

Run `scripts/extract_remove_background.py --target-fps 12 --rgb-output <stem>_frames_12fps_rgb`. It:

- selects source frames at 12 FPS;
- appends decoded source frame `N-1` when needed;
- saves every retained source frame as a complete RGB PNG first;
- runs `rembg` with `isnet-general-use` and CPU provider to identify outside background and edge alpha;
- removes the union of border-connected `rembg` background and border-connected pixels in the inclusive per-channel range `230..241`;
- closes tiny segmentation gaps, keeps complete foreground components, and fills enclosed transparent holes;
- disables rembg alpha matting for predictable runtime behavior;
- writes RGBA PNGs with Pillow;
- writes `extraction_manifest.json` with output-to-source frame mappings.

Expected counts for the current 24 FPS examples:

| Source | 12 FPS frames | Source cut frames | Timeline cut frames |
|---|---:|---|---|
| E03: 141 frames | 71 | `24,48,72,96,120,140` | `12,24,36,48,60,70` |
| E06: 90 frames | 46 | `24,48,72,89` | `12,24,36,45` |

Do not use a global RGB threshold as the final alpha. The source subject can contain pixels in the same `230..241` range as the background, and generated return interiors can shift away from that range. The union of border-connected `rembg` and color evidence plus foreground-component retention is the required protection against interior holes. The complete RGB directory is an auditable intermediate and must remain beside the transparent output.

Run `scripts/validate_matte.py` on each final RGBA sequence. Its zero-hole and transparent-edge metrics do not detect opaque background residue, so also inspect its magenta contact sheet. A valid frame can still have transparent background between limbs; treat that as background, not as a missing-content defect.

### 3. Downscale and compress

`scripts/prepare_frame_assets.py` resizes with Lanczos and writes both PNG and WebP. Use:

- scale `0.5`;
- WebP quality `82`;
- WebP compression method `6`;
- RGBA preserved;
- source transparent files unchanged.

Validate dimensions, frame counts, numeric ordering, and alpha channels before proceeding.

### 4. Generate twelve-frame returns

Read [storyboard-geometry.md](storyboard-geometry.md) for the source-ratio equations, exact pixel prompt contract, raw-board overlay review and rejection rules. Canvas divisibility alone does not establish equal semantic panels; never conceal an invalid board with per-panel resizing.

Run `scripts/build_seedream_return_frames.py` once for every cut point. The helper:

- saves exact source endpoints;
- creates one transparent RGBA reference containing the boundary pose and source first pose side by side;
- requests one source-ratio-derived transparent PNG storyboard, producing twelve equal cells with no gutters or labels (`2016x2268` canvas and `504x756` cells for `512x768` sources);
- resizes generated interior images to the source dimensions before endpoint replacement;
- slices the 4x3 storyboard row-major and requires exactly 12 frames;
- stores remote URL-free sanitized response metadata;
- replaces frames `0` and `11` with exact endpoints;
- preserves verified provider Alpha, or mattes only the RGB fallback frames, then optimizes all 12 frames;
- reports 12 FPS and one-second return timing.

Seedream transparent output has a strict input contract: exactly one input image, the input must be a PNG containing real Alpha, and the output must be PNG. `background: transparent` is rejected for RGB input, multiple input images, or non-PNG output. Use `n: 1` and request one transparent 4x3 storyboard. Seedream rejects generation areas above `4624220` pixels; `2016x2268` is below this limit and provides much more detail than the earlier `1024x1152` storyboard.

Both the transparent and RGB prompts must request crisp high-resolution 2D art, sharp edges, fine facial, hair, and clothing detail, and no blur or compression artifacts. Validate four columns, three rows, equal cells with exactly the original single-frame proportions (`504x756`, 2:3 for the current sources), row-major reading order, and panel continuity before slicing. Do not use 12 unrelated independent requests.

If transparent generation fails or the returned PNG lacks real Alpha, automatically use the generated `request_rgb_fallback.json`. That request uses two RGB endpoint inputs and requires a flat `RGB(235,235,235)` background with no gradient, texture, noise, vignette, floor, shadow, backdrop object, text, or watermark. Save every fallback frame as a complete RGB PNG in `processed/frames_rgb` before matting. Run `rembg` and the inclusive `230..241` border-connected background rule only on those saved RGB fallback files. When provider Alpha is present, preserve it and do not run `rembg`; only fill enclosed transparent holes and keep the RGB files as an audit view.

When Seedream's output safety rejects a clearly child-safe animated subject, retry with explicit non-sensitive wording such as fully clothed, child-friendly, and no nudity, violence, injury, weapons, or adult themes. Keep every temporal, identity, endpoint, scale, and framing requirement unchanged.

If the safe wording is still rejected, use `scripts/build_local_return_frames.py` as the final local fallback. It samples 12 evenly spaced poses from the exact boundary source frame to exact source frame `0`, preserves natural source motion, and records `source_reverse_sampling` in `fallback.json`. Do not describe this fallback as generated imagery.

### 5. Validate return scale and framing

Measure each interior frame against both endpoints. Reject large or small subjects, feet movement, camera drift, or changed framing. Retry at most twice. If the grid, subjects and proportions are valid, small residual uniform drift may use `scripts/stabilize_return_scale.py` on interior frames. Reject unequal grids or clipped/distorted subjects; stabilization cannot rescue them. Preserve exact endpoints and report the fallback.

### 6. Generate the combined Rive project

Write a multi-action manifest and run `scripts/build_rive_multi_action.py`. The generator validates numbering and dimensions and emits:

- root-level `ImageAsset` elements;
- one shared `Solo`;
- one `Image` child per unique frame;
- `KeyFrameId` keys for `Solo.activeComponentId` at property key `296`;
- 12 FPS action segments;
- 12-image, one-second return animations;
- one-frame Router animations;
- `interruptRequested` and `nextAction` view-model properties.

Source animation segments use `duration=N-1` at 12 FPS. Return animations use `duration=12` at 12 FPS, image keys at `0..11`, and a repeated last-image hold key at `12`.

### 7. Verify and build

Run:

```bash
rive project --verify
rive inspect project --summary
rive project --once
```

Expected invariants:

- `Image` and `ImageAsset` counts match unique embedded source and return frames;
- one shared `Solo` and one `StateMachine`;
- one `LinearAnimation` per source segment, return, and Router;
- zero errors and zero warnings;
- `.riv` size is plausible for embedded WebP images.

### 8. Render interaction samples

Render pointer-driven samples at:

- just before and after a whole-second interruption boundary;
- return time `0s`, `0.5s`, and `1s`;
- restarted target action after routing;
- a second interruption during a return;
- an action switch using `nextAction=1`.

Composite transparent frames over the screenshot background and image-match against complete candidate sequences. Do not trust `--advance` alone.

## Important Rive details

### Active component IDs

Each `KeyFrameId` must point to the corresponding child `Image` component ID. Using `ImageAsset` IDs can produce a blank first frame.

### Frame timing

Reference keyframes use `hold`. Preserve the temporal sample order and target 12 FPS. A 12-image return needs one additional held key at frame `12` to occupy the full one-second interval.

### Image assets

Assets are root elements before the `Artboard`. External frame changes are not watched; rebuild after changing shared frame assets.

## Visual validation notes

- Headless screenshot startup can consume roughly one animation frame. Align comparisons by image matching.
- Screenshots of transparent artboards may use an opaque matte. Composite source frames over the same RGB color before pixel comparison.
- Contact sheets must show no blank frame, wrong-size asset, enlarged or shrunken subject, framing drift, or discontinuous pose.

## Common failure modes

- `ModelNotOpen`: the model is not activated for the API account or activation has not propagated.
- Invalid storyboard grid or missing panel: reject the output; the default is exactly four columns, three rows, and 12 equal panels at the original single-frame aspect ratio.
- Transparent request returns opaque RGB: reject it and use the exact `RGB(235,235,235)` fallback request before matting saved RGB PNGs.
- Blank first frame: inspect `Solo.activeComponentId` and target child `Image` IDs.
- Frozen animation: verify `propertyKey="296"` and `KeyFrameId` keys.
- Slow or fast playback: verify source and return FPS are both 12 and return duration is 12 frames.
- Missing final return frame visibility: verify the repeated final held key at frame `12`.
- Missing source frames: inspect `extraction_manifest.json` and ensure decoded frame `N-1` was retained.
- Enlarged or shrunken return subject: reject and retry at most twice; stabilize only residual uniform drift on a valid grid, never invalid layout or body proportions.
- Interrupt request lost before the boundary: use a Boolean latch and conditioned `100%` exit.
- Return freezes or switching fails: verify unconditional return-to-Router transition and Router routes.
- A second click leaks: clear `interruptRequested` on return and Router exits.

## Privacy and delivery

- Keep source assets local unless the user explicitly requests upload or publishing.
- Do not put credentials, account IDs, provider request IDs, remote image URLs, CDN UUIDs, or personal paths in generated documentation.
- `rive push`, `--publish`, and hosted assets require explicit user authorization.
