---
name: video-to-rive-frame-animation
description: Convert video or transparent image sequences into an optimized Rive CLI timeline animation, including frame extraction, background removal, PNG downscaling, WebP compression, RML generation, and render verification. Use for sprite or character sequence playback; do not use it for vector rigging, skeletal animation, or generative image editing.
---

# Video to Rive Frame Animation

Produce a self-contained Rive animation from a frame sequence while preserving the source motion, frame rate, color, and transparency. Keep original assets unchanged and write derived assets to new directories.

## Default invocation

When the user supplies only one or more video files, treat this as the complete request:

```text
使用 $video-to-rive-frame-animation 处理 1.mp4和2.mp4
```

Do not ask for dimensions, quality, FPS, frame count, loop behavior, or output paths. Apply the default profile below independently to every listed video and report the results when all videos finish.

### Default profile

- Extract every frame at the source video FPS; do not sample or drop frames.
- Remove the background and write full-resolution transparent PNG frames.
- Resize proportionally to `50%`, using the nearest integer pixel dimensions.
- Write optimized transparent PNG frames and WebP frames with quality `82` and compression method `6`.
- Build a separate Rive CLI project for each input video and make its timeline loop.
- Match the Rive artboard and image assets to the resized frame dimensions.
- Run Rive verify, inspect, build, and sampled visual checks before reporting completion.
- Preserve every source file and write all derived files under new directories.

Use these sibling output directories, where `<stem>` is the input filename without its extension:

```text
<stem>_frames/             full-resolution transparent PNG frames
<stem>_frames_50_png/      half-resolution optimized PNG frames
<stem>_frames_50_webp/     half-resolution transparent WebP frames
<stem>_rive/               Rive CLI project and built .riv
```

Avoid collisions when input stems repeat by adding a short parent-directory prefix or numeric suffix.

Only ask a question when the source cannot be decoded, automatic matting produces unusable edges, a required dependency or model is missing, output names collide irreconcilably, or the requested operation needs external publishing. The default processing settings are already authorized by a request that invokes this Skill.

## Choose the entry path

- **Video input:** extract every video frame, remove a simple background, and save RGBA PNG frames.
- **Existing RGBA PNG frames:** skip extraction and background removal.
- **Existing RGB frames with a uniform background:** remove the background before optimization.
- **Complex scenes, motion blur, hair-like detail, or moving backgrounds:** inspect representative frames first. The bundled remover is a general segmentation model, not a universal matte solver; pause and choose a stronger matte workflow when edges fail.

Read [references/verified-workflow.md](references/verified-workflow.md) before the first run. It contains the full input/output contract, Rive RML invariants, validation procedure, and observed limitations.

## Required tools

- Python 3 with Pillow; video/background stages also require OpenCV, `rembg`, and `onnxruntime`.
- Rive CLI exposed as `rive`. The command is not normally named `rive-cli`.
- A local segmentation model used by `rembg` when offline background removal is required.

Do not install, upload, or sign assets unless the user asks. Rive build, inspect, and screenshot operations work without a Rive account; publishing and `rive push` do not.

## Workflow

1. Inspect the source resolution, frame rate, frame count, color mode, and representative frames. Unless the user explicitly overrides the defaults, preserve the source FPS and retain every frame.
2. For video input, extract and matte the frames:

   ```bash
   python scripts/extract_remove_background.py \
     --video input.mp4 --output frames
   ```

3. Create half-resolution PNG and WebP derivatives without overwriting the source frames. The shown values are the default profile:

   ```bash
   python scripts/prepare_frame_assets.py \
     --source frames --png-output frames_png --webp-output frames_webp \
     --scale 0.5 --webp-quality 82
   ```

4. Scaffold a separate Rive CLI project for each video, then generate its RML timeline. Pass the source FPS to `--fps`:

   ```bash
   rive create project
   python scripts/build_rive_timeline.py \
     --frames frames_webp --project-dir project --fps <source_fps>
   ```

5. Build and validate the result:

   ```bash
   rive project --verify
   rive inspect project --summary
   rive project --once
   ```

6. Render representative frames with `--screenshot` and compare them with the corresponding WebP frames. See the reference for how to account for screenshot timing and transparent screenshot matting in some local CLI environments.

## Multiple videos

Process all requested videos through the same default profile. Keep their assets and Rive projects separate so filenames, IDs, and output sizes cannot cross-contaminate. Continue with the remaining videos when one fails, then report each result as completed or failed with a concrete reason. Do not stop after the first successful video.

## Non-negotiable Rive invariants

- Put every `ImageAsset` at RML root level, before the `Artboard`.
- Use one `Solo` containing one `Image` child per source frame.
- Key `Solo.activeComponentId` (`propertyKey="296"`) with `KeyFrameId`; never use `KeyFrameDouble`.
- Point each `KeyFrameId` at the child `Image` component ID, not its `ImageAsset` ID.
- Use `interpolationType="hold"` so references do not blend.
- For frame indices `0..N-1`, use `duration="N-1"`. Keep `fps` equal to the intended playback rate.
- Keep the artboard and all frame assets the same pixel size unless scaling is intentional.

## Output contract

The finished result contains:

- an ordered, gap-free frame sequence;
- resized transparent PNG frames for archival or later re-encoding;
- WebP frames used by Rive;
- `scene.rml`, a rebuild script, and project configuration;
- a self-contained `.riv` with embedded image assets;
- verification evidence for frame continuity, Rive structure, and sampled rendering.

For a default invocation, the final report should list each input video, its source and output dimensions, source FPS, frame count, optimized PNG/WebP sizes, `.riv` path, and any visual-check exception. Do not ask the user to choose settings that are already defined above.

Do not claim completion from `--verify` alone. Verify the RML structure and visually inspect the rendered timeline before reporting success.
