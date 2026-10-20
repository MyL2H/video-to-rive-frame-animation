---
name: video-to-rive-frame-animation
description: Remove video backgrounds with a validated matting model, then export transparent 12 FPS frames and one interruptible Rive multi-action timeline. Generate four-second Seedance 2.5 return videos and sample twelve frames for each one-second return. Use for sprite or character sequence playback, not vector rigging.
---

# Video to Rive Frame Animation

Turn each input video into a transparent 12 FPS action. Combine all actions and their interruptible returns in one self-contained Rive file. An interruption finishes the current action at the next configured cut point, returns to its first frame in one second, then starts the requested action.

## Default request

```text
使用 $video-to-rive-frame-animation 处理 1.mp4和2.mp4
```

This is sufficient to process both videos completely. Use the defaults below without asking for FPS, size, return duration, cut points or output paths. If the user requests a trial or one cut point, honor that scope. Replace an application asset when requested; retain a backup and verify the packaged asset after rebuilding.

## Default profile

- Matte the **complete video at its original frame rate before sampling PNGs**. The verified local default is the official **BiRefNet-matting** checkpoint with foreground-color estimation. Save a lossless video with real alpha (`transparent_master.mkv`, FFV1/BGRA). This is a mature per-frame model applied to every video frame, not a temporal neural model. See [video matting](references/video-matting.md) for setup, backend selection and verification.
- Extract source motion at `12 FPS` from that transparent master. For 24 FPS sources retain every second decoded frame, and always retain the final decoded frame `N-1`. Preserve the original video and matching sampled RGB PNGs for comparison.
- Do not run the old rembg/color-rule matte over imported alpha. Protect light clothing, fine props and genuine background gaps; do not fill holes or delete pale pixels globally. The older helpers are explicit legacy options, not the default. See [matte quality](references/matte-quality.md).
- Resize RGBA frames proportionally to `50%` with premultiplied-alpha Lanczos. Preserve existing transparency through resizing; do not fill holes afterward. Write optimized PNG and RGBA WebP, quality `82`, method `6`.
- Use every whole-second source boundary before the decoded end, plus source frame `N-1`. Map boundaries to retained frame indices through `extraction_manifest.json`.
- For **every cut point**, use **Seedance 2.5**, model `doubao-seedance-2-5-260628`, to generate a **4-second** first/last-frame video: boundary RGB frame as `first_frame`, source RGB frame `0` as `last_frame`.
- Request a smooth return across the entire four seconds with a locked camera, original source framing and margins, consistent subject size/position, and flat `RGB(235,235,235)` background. Save the exact prompt and request.
- Preserve the complete generated video. Uniformly sample **12 frames across all decoded frames**, including both endpoints: `round(i * (N-1) / 11)`, `i=0..11`. Do not trim to the first second or extract at 12 FPS from the four-second video, which would produce about 48 frames.
- Matte each entire generated video at native resolution before selecting the twelve frames. Save native RGB and RGBA samples, then normalize the complete frame to the source aspect ratio without cropping or stretching. Resize RGBA with premultiplied alpha; any one-pixel aspect padding is transparent. See [Seedance returns](references/seedance-return-video.md).
- Replace output frames `0` and `11` with the **exact source RGBA endpoint files**. Do not cross-fade. Downscale and compress afterward.
- Play those twelve ordered frames at **12 FPS for exactly 1 second**. Add a repeated held key for the final image at Rive frame `12`.
- Build one multi-action Rive project, verify it, build locally, and render pointer-driven interruption and routing checks.

Seedream storyboard generation is a retained legacy option only when explicitly requested. Do not silently switch to Seedream or source-reverse sampling if Seedance fails. Its grid geometry and transparent-image request rules do not apply to the default video workflow.

## Acceptance and retries

Complete generation, processing, build and requested integration when outputs are technically usable. Record scale changes, feet drift, imperfect framing, clipping, uneven motion, early arrival/holds, expression changes, background residue and prop differences as **visual warnings**. They do not trigger regeneration, an approval pause or fallback by default. Similar/repeated poses are valid frame files. Endpoint replacement corrects generated endpoints; a visible jump afterward remains a warning.

Keep hard checks for readable video, usable duration and aspect ratio, twelve nonempty ordered RGBA frames, correct dimensions, exact source endpoints, 12 FPS / one-second timing, successful Rive build and working routing. Retry genuine API/transport failures or unusable output at most twice after the original attempt; preserve attempts and continue independent batch items. Fix authentication/model-availability errors before retrying. After an uncertain submission outcome, reconcile or resume the existing task rather than blindly submitting another paid request. Never claim a successful integration if required assets remain technically invalid.

Fix demonstrated processing defects from saved RGB inputs before delivery: accidental removal of visible subject pixels and opaque background-filled gaps are matte bugs, not reasons to regenerate motion or silently accept a known broken mask. Reuse successful Seedance videos. Inspect native sampled frames, RGB/RGBA comparisons, contact sheets on contrasting backgrounds and playback when available. Record what was inspected and any viewing limitation. Optional uniform interior stabilization may improve drift but must preserve exact endpoints and be reported. No adaptive crops, nonuniform stretching or fragment deletion. Apply stricter visual rejection only when the user explicitly asks.

## Tools and credentials

Use an isolated Python environment with PyTorch, torchvision, the official BiRefNet implementation/checkpoint, Pillow, OpenCV, NumPy and FFmpeg; see [video-matting.md](references/video-matting.md) for tested dependencies. The older `rembg`/`onnxruntime` dependencies are only needed for explicit legacy processing. Also use the Rive CLI named `rive` and ModelArk access to the Seedance model above. Read `ARK_API_KEY` or the existing `SEEDANCE_API_KEY_ID` / `SEEDANCE_API_KEY_SECRET` pair from a local environment file or environment variables. Never print or commit credentials. Keep task IDs and signed download URLs in private local task/status files, separate from public manifests.

Endpoint submission to Seedance is part of the requested generation workflow. Do not publish other source assets, use `rive push`, or upload application builds without user authorization.

## Workflow

Read [video-matting.md](references/video-matting.md) before processing backgrounds, [interruptible-multi-action.md](references/interruptible-multi-action.md) before building the state machine, [seedance-return-video.md](references/seedance-return-video.md) before generating returns, and [matte-quality.md](references/matte-quality.md) when reviewing masks. [verified-workflow.md](references/verified-workflow.md) records downstream invariants.

### 1. Extract source frames

Inspect source dimensions, FPS, decoded count and representative frames. The current helpers require constant frame rate (CFR) and check actual frame timestamps; reject variable-rate or discontinuous timing rather than silently changing playback speed. Test the selected model on thin props, pale clothing and a short continuous segment before a full batch. Then process all frames and extract PNGs from the alpha video. `$BIREFNET_REPO` and `$BIREFNET_CHECKPOINT` below are paths to the verified local installation.

```bash
python scripts/matte_video.py \
  --video input.mp4 --output-dir input_matted \
  --model-repo "$BIREFNET_REPO" --checkpoint "$BIREFNET_CHECKPOINT"
python scripts/extract_alpha_video_frames.py \
  --video input_matted/transparent_master.mkv --source-video input.mp4 \
  --mode source --output input_frames_12fps \
  --rgb-output input_frames_12fps_rgb --target-fps 12
python scripts/prepare_frame_assets.py \
  --source input_frames_12fps \
  --png-output input_frames_12fps_50_png \
  --webp-output input_frames_12fps_50_webp \
  --scale 0.5 --webp-quality 82 --webp-method 6
python scripts/validate_matte.py \
  --frames-dir input_frames_12fps \
  --rgb-dir input_frames_12fps_rgb \
  --report input_frames_12fps/matte_validation.json \
  --contact-sheet input_frames_12fps/matte_contact_sheet.jpg
```

Confirm the decoded end frame is present and use `frameMap` to locate exact RGBA endpoints. Enclosed transparent components may be real spaces between limbs or props. They are diagnostic, not a default zero-hole target. Compare saved RGB with RGBA on dark, light and saturated backgrounds; inspect adjacent frames for dropouts. Source-color mismatch metrics flag risks but do not prove semantic correctness.

### 2. Generate and process every return

Example: source frame `72` is the 3-second cut for a 24 FPS source. It maps to retained frame `36`; derive this mapping for each source rather than hardcoding it.

```bash
python scripts/build_seedance_return_videos.py \
  --video input.mp4 --output-root input_seedance_returns \
  --boundary-frame 72 --env-file .env.seedance run
python scripts/matte_video.py \
  --video input_seedance_returns/return_3000ms/generated_full.mp4 \
  --output-dir input_seedance_returns/return_3000ms/matted \
  --model-repo "$BIREFNET_REPO" --checkpoint "$BIREFNET_CHECKPOINT"
python scripts/process_seedance_return_frames.py \
  --video input_seedance_returns/return_3000ms/generated_full.mp4 \
  --alpha-video input_seedance_returns/return_3000ms/matted/transparent_master.mkv \
  --first-frame input_frames_12fps/frame_0036.png \
  --last-frame input_frames_12fps/frame_0000.png \
  --output-dir input_seedance_returns/return_3000ms/processed
```

The generation helper supports `prepare`, `submit`, `poll`, `download` and `run` so saved tasks can be resumed. Use short polls with other work between them. Reuse a successful video instead of making another generation request. Review the actual `prompt.txt` before submission when the user asks to see it; save any customized prompt in the request too.

The processor validates the full alpha video's FPS, dimensions and decoded count against the original, samples both clips, saves RGB audit files, preserves imported alpha, restores exact RGBA endpoints and optimizes all twelve frames. An opaque MP4 with a black background is not a transparent master. Neither the old “complete motion in the first second” prompt nor first-second trimming is part of this workflow.

### 3. Build and verify Rive

Write a multi-action manifest referencing `*_seedance_returns/return_<label>ms/processed/frames_50_webp` and run:

```bash
rive create project
python scripts/build_rive_multi_action.py --manifest multi_action_manifest.json --project-dir project
rive project --verify
rive inspect project --summary
rive project --once
```

Use one shared `Solo` frame pool, 12 FPS source segments, twelve-image one-second returns and one routing state machine. `interruptRequested` is a Boolean latch; `nextAction` is the zero-based target action number. The source exits at the next configured boundary, the return exits unconditionally to a one-frame Router, and the Router routes immediately. Clear the latch on interruption, return exit and routing so a second click during a return cannot leak. Image-reference keys use `hold`, transitions use `duration="0"`, and return keys at frames `0..11` repeat the last image at `12`.

Render actual pointer-driven samples before/within/after returns. Match captures to expected frames. Include uninterrupted playback, a second click during a return, and a target-action switch. `--verify` alone does not prove rendering or interaction works.

### 4. Integrate and report

If replacement was requested, back up the current `.riv`, copy the built file to the application's configured asset path, rebuild/restart as appropriate, and confirm the packaged file matches. If only some cut points were changed, state exactly which use Seedance so a mixed trial cannot be mistaken for complete conversion.

Report source dimensions/FPS/counts, retained counts, cut points and generated return coverage, optimized sizes, combined `.riv` path, tests and any visual warnings. Provide a playable one-second preview when possible. Preserve sources and all raw generated videos.

## Outputs

```text
<stem>_frames_12fps/                   full-resolution source RGBA + frameMap
<stem>_matted/transparent_master.mkv   complete original-FPS lossless alpha video
<stem>_frames_12fps_rgb/               matching sampled original RGB for audit
<stem>_frames_12fps_50_png/
<stem>_frames_12fps_50_webp/
<stem>_seedance_returns/
  return_<label>ms/
    inputs/                          exact source RGB endpoint references
    prompt.txt                       actual submitted text
    request.json                     request without credentials
    metadata.json
    generated_full.mp4               entire four-second generated video
    matted/transparent_master.mkv    complete original-FPS alpha video
    processed/
      frames_native_rgb/             sampled complete provider frames
      generated_frames_native_rgba/  alpha-video samples before normalization
      frames_rgb/                    complete source-size original RGB for audit
      generated_frames_rgba/          generated poses before endpoint restoration
      frames_rgba/                   exact source endpoints restored
      frames_50_png/
      frames_50_webp/
      sampling_manifest.json
      manifest.json
<name>_multi_action_manifest.json
<name>_multi_action_rive/build/<name>_multi_action_rive.riv
```

For an explicitly requested legacy Seedream run, use `scripts/build_seedream_return_frames.py` and read [storyboard-geometry.md](references/storyboard-geometry.md). Keep its outputs separate and label the actual generator. Legacy helpers remain available for existing assets; their presence does not change the Seedance default.
