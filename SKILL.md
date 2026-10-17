---
name: video-to-rive-frame-animation
description: Convert one or more videos into optimized transparent frame assets and one interruptible Rive CLI multi-action timeline, including whole-second return clips, background removal, PNG downscaling, WebP compression, frame extraction, and render verification. Use for sprite or character sequence playback; do not use it for vector rigging, skeletal animation, or generative image editing.
---

# Video to Rive Frame Animation

Turn each input video into a complete frame sequence and combine the resulting actions into one Rive CLI project. Preserve source motion and transparency while supporting an interruption that finishes the current action to the next configured cut point, returns to its first frame over exactly one second, and then starts a requested action.

## Default invocation

When the user supplies only one or more video files, treat this as the complete request:

```text
使用 $video-to-rive-frame-animation 处理 1.mp4和2.mp4
```

Do not ask for dimensions, quality, FPS, frame count, return duration, cut points, state-machine names, or output paths. Apply the profile below to every input and combine the actions into one Rive project. A single input produces one action in the same combined project.

## Default profile

- Decode every source frame at the source FPS; do not sample or drop frames.
- Remove the background and write full-resolution transparent PNG frames.
- Resize proportionally to `50%`, using Lanczos and nearest integer pixel dimensions.
- Write optimized transparent PNG and RGBA WebP frames with quality `82` and compression method `6`.
- Define cut points at every whole second before the end, plus one final cut point at the decoded end frame.
- For every action and cut point, generate a return clip from that exact boundary frame to source frame `0`.
- ModelArk `flf2v` rejects `duration: 1` and `duration: 3`. Request Seedance with the verified `duration: 4`, require the complete return motion to finish during the first second and the last pose to hold afterward, then retain only the first second as `return_1s.mp4`. Do not speed up the full generated clip.
- Use the strict scale and framing prompt built into `scripts/build_seedance_return_videos.py`.
- Require the subject's overall size, body proportions, vertical anchor, and screen position to remain consistent with both endpoint frames. Reject and regenerate results that zoom, shrink, enlarge, crop, reframe, or drift.
- Treat the strict Seedance prompt as necessary but not sufficient. Measure scale drift in every retained return. After two rejected retries, normalize only the interior frames with `scripts/stabilize_return_scale.py`; keep both endpoints exact and report that fallback.
- Replace the generated endpoints with the exact source frames. Do not cross-fade the endpoints for a one-second return.
- Retain every generated return frame and play the generated one-second sequence as a fixed `1s` timeline.
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
- Volcengine ModelArk access to `doubao-seedance-2-5-260628` for return generation.

Read credentials from a local environment file or process environment. Accept `ARK_API_KEY`, or the existing `SEEDANCE_API_KEY_ID` and `SEEDANCE_API_KEY_SECRET` pair. Never print, copy into generated artifacts, commit, or include credentials in this Skill. Do not upload source assets or use `rive push` unless the user explicitly asks.

## Workflow

1. Inspect every video's resolution, FPS, decoded frame count, color mode, and representative frames. Confirm that automatic matting will produce usable edges.
2. Extract and matte every frame:

   ```bash
   python scripts/extract_remove_background.py \
     --video input.mp4 --output <stem>_frames
   ```

3. Create half-resolution PNG and WebP derivatives without changing the source frames:

   ```bash
   python scripts/prepare_frame_assets.py \
     --source <stem>_frames \
     --png-output <stem>_frames_50_png \
     --webp-output <stem>_frames_50_webp \
     --scale 0.5 --webp-quality 82
   ```

4. Calculate the cut-point frames as specified in [references/interruptible-multi-action.md](references/interruptible-multi-action.md). Generate and process one return clip for every cut point:

   ```bash
   python scripts/build_seedance_return_videos.py \
     --video input.mp4 --output-root <stem>_seedance_returns \
     --boundary-frame <frame> run

   python scripts/process_seedance_return_frames.py \
     --video <stem>_seedance_returns/return_<label>ms/return_1s.mp4 \
     --first-frame <stem>_seedance_returns/return_<label>ms/inputs/first_frame.png \
     --last-frame <stem>_seedance_returns/return_<label>ms/inputs/last_frame.png \
     --output-dir <stem>_seedance_returns/return_<label>ms/processed \
     --blend-frames 0

   python scripts/stabilize_return_scale.py \
     --return-root <stem>_seedance_returns --replace
   ```

   Before processing, run the same command with `--validate-only`. It rejects the retained clip unless its decoded duration is within `0.25s` of `1s`. Measure the retained first second against both endpoint frames and reject visible subject scale or framing drift. Retry at most twice with the same `duration: 4` request and strict first-second prompt. If drift remains, use the scale stabilization script for interior frames and report the fallback; never build from visibly enlarged frames and never alter the exact endpoints.

5. Create one Rive CLI project, write a multi-action manifest, and build its RML:

   ```bash
   rive create project
   python scripts/build_rive_multi_action.py \
     --manifest multi_action_manifest.json --project-dir project
   ```

6. Validate the project and its behavior:

   ```bash
   rive project --verify
   rive inspect project --summary
   rive project --once
   ```

7. Render pointer-driven samples at interruption cut points, return midpoints, and return endpoints. Match the screenshots against the expected source or return frames. Also test uninterrupted playback, a second interruption during a return, and a target-action switch when multiple actions are present.

Read [references/interruptible-multi-action.md](references/interruptible-multi-action.md) before implementing the state machine and [references/verified-workflow.md](references/verified-workflow.md) before the first frame-processing run.

## Interruption contract

- Let the source action's decoded duration define its end. Use all whole-second boundaries strictly before the end, then always append the end frame as the final boundary.
- If an interruption is requested at time `t`, continue the current action to the smallest configured boundary at or after `t`. For example, a request at `3.2s` continues to `4.0s`.
- Seedance must complete each return during the generated clip's first second. Retain that second and exclude the held remainder from processing.
- Each return runs for exactly `1s`, regardless of the source FPS.
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

## Output contract

For each input stem:

```text
<stem>_frames/                         full-resolution transparent PNG frames
<stem>_frames_50_png/                  half-resolution optimized PNG frames
<stem>_frames_50_webp/                 half-resolution RGBA WebP frames
<stem>_seedance_returns/
  return_<label>ms/
    inputs/                            exact source endpoint frames
    request.json                       sanitized generation request
    task.json, status.json             API task metadata with credentials redacted
    return_seedance_raw.mp4            complete provider response, retained for audit
    return_1s.mp4                      first-second return motion used by Rive
    processed/
      frames_rgba/                     transparent full-resolution return frames
      frames_50_png/                   optimized return PNG frames
      frames_50_webp/                  Rive return WebP frames
      manifest.json                    counts, size, timing, and endpoint policy
      processed_stabilized/            measured scale-normalized derivatives
        frames_50_png/                 stabilized PNG frames used when needed
        frames_50_webp/                stabilized WebP frames referenced by Rive
        manifest.json                  scale measurements and endpoint policy
```

For all inputs together:

```text
<name>_multi_action_manifest.json      source actions and every return mapping
<name>_multi_action_rive/
  scene.rml                            generated images, animations, and state machine
  multi_action_manifest.json           resolved project summary
  build/<name>_multi_action_rive.riv   self-contained built Rive file
```

The final report lists every source video and its dimensions, FPS, decoded frame count, cut points, generated return durations, return counts, PNG/WebP sizes, combined `.riv` path, and any scale, framing, or visual-check exception.

## Example

```text
使用 $video-to-rive-frame-animation 处理 E07_SMUG_00001_.mp4
```

For a `512x768`, `24 FPS`, `107`-frame input, the verified profile creates cut points at frames `24`, `48`, `72`, `96`, and `106`; produces five one-second return sequences; resizes frames to `256x384`; and builds one interruptible action. The internal boundaries are `1s`, `2s`, `3s`, and `4s`, followed by the decoded end label `4458ms`.

Do not claim completion from `--verify` alone. Structural verification and rendered interaction checks are both required.
