# Verified Workflow and Constraints

This document records the complete workflow demonstrated on a 24 FPS character video and the checks that caught real errors during implementation. Numbers in the observed example are evidence from one run, not performance guarantees.

## Default request handling

A request that names only videos is complete. For example:

```text
使用 $video-to-rive-frame-animation 处理 1.mp4和2.mp4
```

Apply this profile without asking for more parameters:

1. Process each video independently from the others.
2. Preserve source FPS and extract all frames in source order.
3. Produce transparent full-resolution PNG frames.
4. Resize to 50% with Lanczos.
5. Produce optimized PNG and RGBA WebP at quality 82 and method 6.
6. Build a separate looping Rive timeline sized to the reduced frames.
7. Verify frame continuity, Rive structure, a local build, and sampled renders.
8. Keep source files unchanged and return paths and measured results for every video.

If a batch item fails, continue with independent items and summarize failures at the end. Ask the user only for conditions listed in `SKILL.md`; otherwise use these defaults.

## Applicable scenarios

- A short video of one subject on a simple, mostly uniform background.
- An existing numbered sequence of transparent PNG frames.
- A Rive timeline that should play the frames in order and loop.
- Deliverables that must reduce raster and `.riv` size while retaining transparency.

Use a different matte method for footage with moving backgrounds, translucent objects, heavy motion blur, fine hair, or semitransparent effects. Use vector rigging or mesh deformation instead of frame playback when the animation must be edited as articulated parts.

## Input specification

### Video

- Readable by OpenCV.
- One video stream; audio is ignored.
- Known or discoverable FPS and frame count.
- Subject separated from the background well enough for general segmentation.

### Existing frames

- Ordered by a numeric suffix.
- Numbering starts at `0000` and has no gaps.
- All frames have identical dimensions.
- RGBA is required for transparent input/output. RGB input is accepted by the optimization script but the result will remain opaque.
- The optimization script accepts PNG and writes PNG plus WebP. Rive can also reference PNG, JPEG, or WebP image assets.

### Rive project

- Rive CLI is installed and the executable is `rive`.
- Project directory contains `rive.yaml`, generated `scene.rml`, helper scripts, and `build/`.
- Frame files may live beside the project. `file=` can reference them relatively and embeds their bytes in the built `.riv`.

## Output specification

| Artifact | Contract |
|---|---|
| RGBA PNG frames | Gap-free numbering, original aspect ratio, transparent background |
| Resized PNG frames | Same count and ordering; dimensions are an integer scale or explicit size |
| WebP frames | Same count, order, dimensions, and alpha range as resized PNG frames |
| `scene.rml` | Root-level image assets, one Solo, one image child per frame, one linear animation |
| Timeline | FPS requested by the user; frame `i` is visible at animation frame `i` |
| `.riv` | Self-contained build with all referenced images embedded |

## Execution procedure

### 1. Inspect before transforming

Read source metadata and sample the first, middle, and last frames. Verify that the background is suitable for automatic matte extraction. Record the source FPS and frame count; do not infer them from filename count alone.

### 2. Extract and remove the background

For video input, use `scripts/extract_remove_background.py`. It:

- reads frames through OpenCV;
- runs `rembg` with a general model and the CPU provider;
- disables alpha matting by default for predictable runtime behavior;
- writes RGBA PNGs with Pillow to preserve the channel order.

When running inside a restricted sandbox, temporary cache location can matter. A verified invocation pattern is:

```bash
TMPDIR=/private/tmp \
NUMBA_CACHE_DIR=/private/tmp/numba-cache \
python scripts/extract_remove_background.py \
  --video input.mp4 --output frames
```

Do not assume the model is installed. Check model availability first and ask before downloading when network access is restricted.

### 3. Downscale and compress

`scripts/prepare_frame_assets.py` resizes with Lanczos and writes both PNG and WebP. Defaults used in the verified run:

- scale: `0.5`;
- WebP quality: `82`;
- WebP compression method: `6`;
- RGBA preserved;
- source files unchanged.

Use `--width` and `--height` when an exact output size matters. Use `--scale` when proportional reduction is sufficient. Validate dimensions and alpha before proceeding.

### 4. Generate the Rive timeline

Create a normal Rive CLI project:

```bash
rive create project
```

Run `scripts/build_rive_timeline.py`. The generator derives width and height from the first frame, validates that every frame matches, and emits:

- root-level `ImageAsset` elements;
- one `Solo` centered in the artboard;
- one `Image` child per frame at local `(0, 0)` with centered origin;
- `KeyFrameId` keys for `Solo.activeComponentId` at property key `296`;
- a state machine that starts the linear animation.

For `N` numbered frames `0..N-1`, the animation uses `duration="N-1"`. At frame `i`, the active child is the `Image` with component ID `0:{2000+i}`.

### 5. Verify and build

Run all three structural checks:

```bash
rive project --verify
rive inspect project --summary
rive project --once
```

Expected invariants for 141 frames:

- `Image`: 141;
- `ImageAsset`: 141;
- `KeyFrameId`: 141;
- `Solo`, `LinearAnimation`, and `StateMachine`: 1 each;
- zero errors and zero warnings;
- `.riv` size is plausible for the compressed embedded images.

Then render representative frames. Compare each screenshot with the matching source frame after accounting for renderer behavior described below.

## Important Rive details

### Active component IDs

`Solo.activeComponentId` selects a child component. A correct key looks like:

```xml
<KeyFrameId value="0:2000" frame="0" interpolationType="hold"/>
<KeyFrameId value="0:2001" frame="1" interpolationType="hold"/>
```

The values above are the IDs of `Image` children. Do not use the IDs from their `ImageAsset` elements. During the verified implementation, using asset IDs caused the initial headless render to be blank even though later frames appeared through renderer tolerance. Correcting the references to child component IDs fixed the initial frame and made the structure semantically valid.

### Image assets

Assets are root elements:

```xml
<Rive version="1" kind="fragment">
  <ImageAsset file="../frames/frame_0000.webp" name="Frame 0000" id="0:1000"/>
  <Artboard ...>
    ...
  </Artboard>
</Rive>
```

The CLI docs allow `file=` outside the project directory, but external file changes are not watched. Rebuild manually after changing shared frame assets.

### Frame timing

Reference keyframes must use `hold`. The generated animation is one frame per keyframe and loops by default. Preserve all frames unless the user explicitly requests temporal sampling; dropping frames changes motion timing and is not equivalent to reducing pixel size.

## Visual validation notes

These behaviors were observed with the local CLI used for the verified run and should be measured rather than generalized:

- Headless screenshot startup consumed roughly one source-animation frame. Screenshots intended for exact frame comparison landed one frame early until the requested advance time was increased by one frame.
- Screenshots of a transparent artboard were written with an opaque dark matte in that environment. Composite transparent WebP/PNG frames over the same RGB color before pixel comparison.
- After aligning the timing and matte, five sampled frames across the timeline matched their sources with a maximum RGB channel delta of `1`.

Use image matching to identify which source frame a screenshot actually shows when timing is uncertain. Do not rely only on the requested `--advance` value.

## Observed optimization example

One verified character sequence had 141 frames at `512x768`, played at 24 FPS:

| Stage | Observed size |
|---|---:|
| Original RGBA PNG frames | about 26 MB |
| `256x384` optimized PNG frames | about 9.0 MB |
| `256x384` WebP frames, quality 82 | about 2.2 MB |
| Built `.riv` after WebP migration | about 2.1 MB |

The final `.riv` was about 92% smaller than the earlier PNG-based build. Actual size depends on artwork, alpha complexity, dimensions, frame count, and compression quality.

## Common failure modes

- `rive-cli: command not found`: use the installed `rive` executable.
- Blank first frame: inspect `Solo.activeComponentId` and confirm its values are child `Image` IDs.
- No animation or frozen appearance: verify `propertyKey="296"` and `KeyFrameId`, not numeric keyframes.
- Wrong colors after export: do not save RGBA arrays as BGR/BGRA through OpenCV; use Pillow or explicit channel conversion.
- Missing frames: validate continuous numeric suffixes before building.
- Sudden motion or shortened duration: frame sampling changed the sequence; pixel reduction alone should not change frame count.
- Large `.riv`: confirm the project references compressed WebP rather than oversized PNG files.
- Clean verify but wrong appearance: verify is structural. Render and compare frames.

## Privacy and delivery

- Keep source assets local unless the user explicitly requests upload or publishing.
- Do not put user paths, account IDs, model credentials, CDN UUIDs, or personal file names in the Skill or generated documentation.
- `rive push`, `--publish`, and hosted assets can involve external services or redistribution. Require explicit user authorization for those operations.
