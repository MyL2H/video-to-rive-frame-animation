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
7. Generate exactly 12 Seedream return images for every whole-second and end-of-media cut point.
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
- runs `rembg` with `isnet-general-use` and CPU provider only to obtain edge alpha;
- removes only the measured background range (`232..238` for the current E03/E06 assets) when it is connected to an image border;
- keeps complete foreground components and fills enclosed transparent holes;
- disables rembg alpha matting for predictable runtime behavior;
- writes RGBA PNGs with Pillow;
- writes `extraction_manifest.json` with output-to-source frame mappings.

Expected counts for the current 24 FPS examples:

| Source | 12 FPS frames | Source cut frames | Timeline cut frames |
|---|---:|---|---|
| E03: 141 frames | 71 | `24,48,72,96,120,140` | `12,24,36,48,60,70` |
| E06: 90 frames | 46 | `24,48,72,89` | `12,24,36,45` |

Do not use a global RGB threshold as the final alpha. The source subject can contain pixels in the same `232..238` range as the background. The border-connected mask plus foreground-component retention is the required protection against interior holes. The complete RGB directory is an auditable intermediate and must remain beside the transparent output.

### 3. Downscale and compress

`scripts/prepare_frame_assets.py` resizes with Lanczos and writes both PNG and WebP. Use:

- scale `0.5`;
- WebP quality `82`;
- WebP compression method `6`;
- RGBA preserved;
- source transparent files unchanged.

Validate dimensions, frame counts, numeric ordering, and alpha channels before proceeding.

### 4. Generate twelve-frame returns

Run `scripts/build_seedream_return_frames.py` once for every cut point. The helper:

- saves exact source endpoints;
- creates a strict fixed-scale Seedream request;
- uses a `1024x1536` canvas for the current `2:3` sources because Seedream rejects image areas below `921600` pixels;
- resizes generated interior images to the source dimensions before endpoint replacement;
- requires 12 returned images;
- stores remote URL-free sanitized response metadata;
- replaces frames `0` and `11` with exact endpoints;
- mattes and optimizes all 12 frames;
- reports 12 FPS and one-second return timing.

Do not use 12 unrelated independent image requests when one multi-image request or a 4x3 storyboard can provide coherent ordering. If a storyboard fallback is used, request `1024x1152` for 12 equal `256x384` cells, validate four columns, three rows, equal cell sizes, row-major reading order, and panel continuity before slicing.

When Seedream's output safety rejects a clearly child-safe animated subject, retry with explicit non-sensitive wording such as fully clothed, child-friendly, and no nudity, violence, injury, weapons, or adult themes. Keep every temporal, identity, endpoint, scale, and framing requirement unchanged.

If the safe wording is still rejected, use `scripts/build_local_return_frames.py` as the final local fallback. It samples 12 evenly spaced poses from the exact boundary source frame to exact source frame `0`, preserves natural source motion, and records `source_reverse_sampling` in `fallback.json`. Do not describe this fallback as generated imagery.

### 5. Validate return scale and framing

Measure each interior frame against both endpoints. Reject large or small subjects, feet movement, camera drift, or changed framing. Retry at most twice. If drift remains, run `scripts/stabilize_return_scale.py` and use only its interior frames. Preserve exact endpoints and report the fallback.

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
- Wrong return image count: reject and use the documented storyboard fallback only when supported.
- Blank first frame: inspect `Solo.activeComponentId` and target child `Image` IDs.
- Frozen animation: verify `propertyKey="296"` and `KeyFrameId` keys.
- Slow or fast playback: verify source and return FPS are both 12 and return duration is 12 frames.
- Missing final return frame visibility: verify the repeated final held key at frame `12`.
- Missing source frames: inspect `extraction_manifest.json` and ensure decoded frame `N-1` was retained.
- Enlarged or shrunken return subject: reject, retry twice, then stabilize only interior frames.
- Interrupt request lost before the boundary: use a Boolean latch and conditioned `100%` exit.
- Return freezes or switching fails: verify unconditional return-to-Router transition and Router routes.
- A second click leaks: clear `interruptRequested` on return and Router exits.

## Privacy and delivery

- Keep source assets local unless the user explicitly requests upload or publishing.
- Do not put credentials, account IDs, provider request IDs, remote image URLs, CDN UUIDs, or personal paths in generated documentation.
- `rive push`, `--publish`, and hosted assets require explicit user authorization.
