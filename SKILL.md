---
name: video-to-rive-frame-animation
description: Convert videos into optimized transparent 12 FPS frame assets and one interruptible Rive CLI multi-action timeline. Generate twelve-frame Seedream returns from whole-second cut points to the first source frame, then build, render-test, and deliver a self-contained Rive file. Use for sprite or character sequence playback; do not use it for vector rigging, skeletal animation, or generative image editing.
---

# Video to Rive Frame Animation

Turn each input video into a 12 FPS transparent frame sequence and combine the actions into one Rive CLI project. Preserve the source motion speed while supporting an interruption that finishes the current action at the next configured cut point, returns to its first frame over exactly one second, and starts a requested action.

## Default invocation

When the user supplies only one or more video files, treat this as the complete request:

```text
使用 $video-to-rive-frame-animation 处理 1.mp4和2.mp4
```

Do not ask for dimensions, quality, FPS, frame count, return duration, cut points, state-machine names, or output paths. Apply the profile below to every input and combine the actions into one Rive project.

## Default profile

- Play every source and return timeline at exactly `12 FPS`.
- For a 24 FPS source, retain every second decoded frame so the original motion speed is preserved. Always retain decoded source frame `N-1`, even when it is not on the regular sample grid.
- Save every retained source frame as a complete RGB PNG before matting, then write full-resolution transparent PNG frames. For generated return frames, preserve verified provider alpha directly; only save and matte an RGB fallback. Never derive an RGB matte directly from an in-memory image when a complete PNG can be audited first.
- For a uniform source background, use the inclusive per-channel RGB range `230..241` (`230 <= R/G/B <= 241`) as `--background-min 230 --background-max 241`. Save every decoded or generated RGB PNG before matting. Remove the union of border-connected `rembg` background and border-connected pixels in this RGB range. Keep complete subject components, use `rembg` only for a small edge feather, close tiny mask gaps, and fill enclosed transparent holes. Do not delete every pixel in the RGB range globally; matching colors can occur inside the subject.
- Resize proportionally to `50%`, using Lanczos and nearest integer pixel dimensions.
- Write optimized transparent PNG and RGBA WebP frames with quality `82` and compression method `6`.
- Define cut points at every whole second before the end, plus the decoded end frame. Map each source cut to its corresponding 12 FPS timeline frame.
- For every action and cut point, use `doubao-seedream-5-0-pro-260628` to generate exactly 12 ordered return poses from the exact boundary pose to source frame `0`.
- Seedream supports transparent output only when the request has exactly one input image, that input is a PNG with real Alpha, and the requested output is PNG. Build one transparent RGBA reference containing the boundary and first-frame poses side by side. Use `background: "transparent"`, `output_format: "png"`, and `n: 1`.
- Generate one high-resolution `2016x2268` transparent 4x3 storyboard, read it row-major, and slice 12 equal `504x756` 2:3 panels. Seedream rejects generation areas above `4624220` pixels. Resize only the generated interiors to source dimensions, then replace frames `0` and `11` with the exact source endpoints.
- When transparent generation fails or its output has no real Alpha, automatically retry with two RGB endpoint inputs and require the entire background to be exactly flat `RGB(235,235,235)` with no gradient, texture, shadow, floor, text, or watermark. Save the complete RGB PNG frames first, then matte saved files with border-connected `rembg` evidence and the inclusive `230..241` background range.
- Both prompt paths must request crisp high-resolution 2D art with sharp edges and fine facial, hair, and clothing detail, explicitly excluding blur and compression artifacts. Do not generate 12 unrelated images independently; use the single 4x3 storyboard as the default coherent layout.
- Require natural, evenly spaced motion and consistent identity, face, clothing, body proportions, subject size, feet anchor, screen position, style, lighting, camera, framing, and background. Reject zoom, shrink, enlarge, crop, reframe, drift, morphing, object changes, text, watermark, or cuts.
- If Seedream's output safety rejects a child-safe animated subject, retry with explicit non-sensitive constraints such as fully clothed, child-friendly, and no violence or adult themes. Do not loosen the identity, timing, endpoint, scale, or framing constraints.
- If output safety still rejects a storyboard after safe-prompt retries, build the return with `scripts/build_local_return_frames.py`, sampling source motion in reverse at 12 evenly spaced poses including both exact endpoints. Mark `source_reverse_sampling` as a generation exception; never present it as Seedream output.
- Replace generated frames `0` and `11` with the exact source endpoints. Do not cross-fade the endpoints.
- Play the 12 unique return images at `12 FPS`. Hold the final image through the end of the one-second timeline by repeating its held key at Rive frame `12`.
- Measure scale and framing drift in every return. Retry rejected results at most twice. If drift remains, normalize only interior frames with `scripts/stabilize_return_scale.py`; keep both endpoints exact and report the fallback.
- Build one Rive CLI project containing all logical actions, their forward segments, all return timelines, and the routing state machine.
- Run structural checks, a local build, and sampled pointer-driven renders before reporting completion.
- Preserve source files and put derived files in new directories.

## Applicable scenarios

- One or more short videos of one subject on a simple, mostly uniform background.
- Sprite or character actions that must coexist in one Rive scene.
- An interruption that must not cut mid-motion: the current action advances to the next configured boundary before returning.
- A fixed one-second return from any interruption boundary to the action's first frame.
- A host-controlled action switch where the target action is selected before the interruption is requested.

Use another matte method for moving backgrounds, translucent objects, heavy motion blur, fine hair, or semitransparent effects. Use vector rigging or mesh deformation when the animation must be edited as articulated parts.

## Required tools and credentials

- Python 3 with Pillow.
- OpenCV, `rembg`, and `onnxruntime` for video extraction and background removal.
- Rive CLI exposed as `rive`; the command is not normally named `rive-cli`.
- A local segmentation model for `rembg` when offline background removal is required.
- Volcengine ModelArk access to `doubao-seedream-5-0-pro-260628` for return image generation.

Read credentials from a local environment file or process environment. Accept `ARK_API_KEY`, or the existing `SEEDANCE_API_KEY_ID` and `SEEDANCE_API_KEY_SECRET` pair. Never print, copy into generated artifacts, commit, or include credentials in this Skill. Do not upload source assets or use `rive push` unless the user explicitly asks.

## Workflow

1. Inspect every video's resolution, FPS, decoded frame count, color mode, and representative frames. Confirm that automatic matting will produce usable edges.

2. Extract and temporal-resample every frame to complete RGB PNG files, then matte the saved PNGs:

   ```bash
   python scripts/extract_remove_background.py \
     --video input.mp4 --output <stem>_frames_12fps \
     --rgb-output <stem>_frames_12fps_rgb --target-fps 12 \
     --background-min 230 --background-max 241
   ```

   The extraction manifest records every retained RGB frame, its exact source frame, the configured background range, and matte diagnostics. Verify the end frame is present. Inspect the RGB and RGBA results as contact sheets; a zero-hole metric alone does not detect opaque background residue. To re-matte an existing RGB PNG directory without decoding the video again, use `scripts/remove_background.py --source <rgb-dir> --output <rgba-dir> --background-min 230 --background-max 241`.

3. Create half-resolution PNG and WebP derivatives without changing the transparent source frames:

   ```bash
   python scripts/prepare_frame_assets.py \
     --source <stem>_frames_12fps \
     --png-output <stem>_frames_12fps_50_png \
     --webp-output <stem>_frames_12fps_50_webp \
     --scale 0.5 --webp-quality 82 --webp-method 6
   ```

   Validate the full-resolution RGBA sequence and inspect its contact sheet:

   ```bash
   python scripts/validate_matte.py \
     --frames-dir <stem>_frames_12fps \
     --report <stem>_frames_12fps/matte_validation.json \
     --contact-sheet <stem>_frames_12fps/matte_contact_sheet.jpg
   ```

   `validate_matte.py` fails on opaque frame edges or enclosed transparent holes. Its magenta contact sheet is still required because those metrics cannot identify every opaque background residue. Transparent background visible between separate limbs is valid.

4. Calculate cut-point frames as specified in [references/interruptible-multi-action.md](references/interruptible-multi-action.md). Generate and process 12 return images for every cut point:

   ```bash
   python scripts/build_seedream_return_frames.py \
     --video input.mp4 --output-root <stem>_seedream_returns \
     --boundary-frame <source-frame> --target-fps 12 \
     --source-rgba-dir <stem>_frames_12fps run
   ```

   The default `auto` mode first requests a single high-resolution transparent storyboard. Preserve verified provider Alpha without running `rembg`. If the request fails or the returned PNG has no real Alpha, the same run retries with the exact `RGB(235,235,235)` fallback prompt. For that fallback only, write complete `frames_rgb` PNGs first and matte the saved RGB files using border-connected `rembg` background, the `230..241` range, and hole repair. Replace endpoints with exact source RGBA frames and write 50% PNG/WebP derivatives. Measure interior scale and framing against both endpoints. Retry at most twice with the same strict prompt. If drift remains, run:

   ```bash
   python scripts/stabilize_return_scale.py \
     --return-root <stem>_seedream_returns --replace
   ```

   Use `processed_stabilized/frames_50_webp` only when stabilization was required and report the fallback. Never alter the exact endpoints.

5. Create one Rive CLI project, write a multi-action manifest, and build its RML:

   ```bash
   rive create project
   python scripts/build_rive_multi_action.py \
     --manifest multi_action_manifest.json --project-dir project
   ```

   Source actions use `fps: 12`. Each return has 12 unique frames, `fps: 12`, and `durationFrames: 12`; the builder adds one held key for the final frame at frame `12`.

6. Validate the project and its behavior:

   ```bash
   rive project --verify
   rive inspect project --summary
   rive project --once
   ```

7. Render pointer-driven samples at interruption cut points, return midpoints, and return endpoints. Match the screenshots against expected source or return frames. Also test uninterrupted playback, a second interruption during a return, and a target-action switch when multiple actions are present.

Read [references/interruptible-multi-action.md](references/interruptible-multi-action.md) before implementing the state machine and [references/verified-workflow.md](references/verified-workflow.md) before the first frame-processing run. The verified workflow also records the RGB-PNG-first matte fix for generated return interiors.

## Interruption contract

- Use all whole-second source boundaries strictly before the decoded end, then always append source frame `N-1`.
- Map every source boundary to its retained 12 FPS timeline frame.
- If an interruption is requested at time `t`, continue the current action to the smallest configured boundary at or after `t`.
- Each return contains exactly 12 unique images and runs for exactly `1s` at `12 FPS`.
- The return's first frame is the exact source boundary frame and its last frame is exact source frame `0`.
- After the return, enter the action selected by `nextAction`; use action `0` by default.
- When switching from action A to action B, set `nextAction` to B's zero-based index before requesting the interruption.

## Rive state-machine invariants

- Use one shared `Solo` image pool for all source and return frames.
- Split each logical action timeline at its return boundaries. Join the segments in the state machine without dropping source frames.
- Use `interruptRequested` as a `ViewModelPropertyBoolean`. A Trigger is spent at the end of its firing frame and cannot carry a request to a later whole-second exit.
- Use `nextAction` as a `ViewModelPropertyNumber` containing the zero-based action index.
- On interruption, use a conditioned `100%` exit to the matching return and write `interruptRequested=false` in that transition action.
- Clear `interruptRequested` again on return and router exits so a second click during a return cannot leak into the next action.
- Give each return a single unconditional `100%` exit to a one-frame Router state.
- Give the Router no exit time. Route it to each action's first segment with immediate `nextAction` conditions.
- Keep transitions between image-reference animations at `duration="0"`; frame references use `hold` keyframes.
- A 12-frame one-second return uses `fps="12"`, `duration="12"`, image keys at frames `0..11`, and a repeated held key for the last image at frame `12`.

## Output contract

For each input stem:

```text
<stem>_frames_12fps/                    full-resolution transparent 12 FPS PNG frames
<stem>_frames_12fps_rgb/                complete RGB PNG frames before matting
<stem>_frames_12fps_50_png/             half-resolution optimized PNG frames
<stem>_frames_12fps_50_webp/            half-resolution RGBA WebP frames
<stem>_seedream_returns/
  return_<label>ms/
    inputs/                              exact source endpoint frames
    request.json                         sanitized generation request
    response.json                        sanitized provider response
    generated/                           one provider storyboard and 12 sliced frames
    processed/
      frames_rgb/                        complete RGB audit PNGs, including RGB fallbacks
      frames_rgba/                       provider Alpha or matted full-resolution frames
      frames_50_png/                     optimized return PNG frames
      frames_50_webp/                    Rive return WebP frames
      manifest.json                      counts, size, timing, and endpoint policy
      processed_stabilized/              measured scale-normalized derivatives
        frames_50_png/                   stabilized PNG frames used when needed
        frames_50_webp/                  stabilized WebP frames referenced by Rive
        manifest.json                    scale measurements and endpoint policy
```

For all inputs together:

```text
<name>_multi_action_manifest.json        source actions and every return mapping
<name>_multi_action_rive/
  scene.rml                              generated images, animations, and state machine
  multi_action_manifest.json             resolved project summary
  build/<name>_multi_action_rive.riv      self-contained built Rive file
```

The final report lists every source video and its dimensions, source FPS, decoded frame count, 12 FPS retained frame count, cut points, generated return counts, PNG/WebP sizes, combined `.riv` path, and any scale, framing, generation, or visual-check exception.

Do not claim completion from `--verify` alone. Structural verification and rendered interaction checks are both required.
