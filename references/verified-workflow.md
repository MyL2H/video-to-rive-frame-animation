# Verified 12 FPS Workflow and Constraints

This document records frame-processing and Rive invariants for the 12 FPS Seedance profile. Keep source FPS metadata, but play retained frame sequences at 12 FPS.

## Default request handling

Apply this profile without asking for more parameters:

1. Process each complete video independently at native resolution and FPS with BiRefNet-matting, using 1024×1024 model input and official foreground-color estimation.
2. Preserve a lossless FFV1/BGRA video containing real alpha, then temporally sample that master to 12 FPS while preserving motion speed.
3. Always retain decoded source frame `N-1`.
4. Produce transparent full-resolution PNG frames and matching original RGB audit samples.
5. Resize to 50% with premultiplied-alpha Lanczos.
6. Produce optimized PNG and RGBA WebP at quality 82 and method 6.
7. Generate one four-second Seedance 2.5 return video for every whole-second and end-of-media cut point; matte the entire native video before uniformly sampling exactly 12 frames over the complete clip.
8. Replace generated return endpoints with exact source frames.
9. Build one combined Rive project sized to the reduced frames.
10. Verify frame continuity, Rive structure, local build, and sampled renders.

Appearance and motion checks are advisory: record visual warnings and complete the full workflow and requested integration. Do not retry or request approval solely for visual defects. If a batch item has a technical failure, continue independent items and summarize failures at the end.

## Input specification

### Video

- Readable by FFmpeg and OpenCV.
- One video stream; audio is ignored.
- Known FPS and decoded frame count.
- Subject separated from the background well enough for the validated matting backend; test thin details and pale regions before the complete batch.

### Rive project

- Rive CLI executable is `rive`.
- Project directory contains `rive.yaml`, generated `scene.rml`, helper scripts, and `build/`.
- Frame files may live beside the project. `file=` can reference them relatively and embed their bytes in the built `.riv`.

## Execution procedure

### 1. Inspect before transforming

Read source metadata and sample the first, middle, and last frames, plus known difficult feather, clothing and background-gap cases. Test the selected backend on a short continuous segment. Record source dimensions/FPS, decoded frame count, selected 12 FPS indices, and the exact end-frame mapping. See [video-matting.md](video-matting.md) for setup and backend validation.

### 2. Complete-video matting, then temporal sampling

Run `scripts/matte_video.py` on the original video with the verified official BiRefNet repository and matting checkpoint. The default uses 1024×1024 model input, returns alpha to native video dimensions and estimates foreground colors with the official helper. Save every native-FPS frame to `transparent_master.mkv` using lossless FFV1/BGRA. This is per-frame neural matting over the complete video, not a temporal neural model; temporal consistency must be checked in playback.

Run `scripts/extract_alpha_video_frames.py --mode source --target-fps 12` with this master and the original `--source-video`. It:

- verifies the alpha master against original dimensions, FPS and decoded count;
- selects matching source and alpha-video frames at 12 FPS;
- appends decoded source frame `N-1` when needed;
- saves every retained original frame as a complete RGB audit PNG;
- exports the matching RGBA frame while preserving the imported alpha;
- writes `extraction_manifest.json` with output-to-source frame mappings.

Expected counts for the current 24 FPS examples:

| Source | 12 FPS frames | Source cut frames | Timeline cut frames |
|---|---:|---|---|
| E03: 141 frames | 71 | `24,48,72,96,120,140` | `12,24,36,48,60,70` |
| E06: 90 frames | 46 | `24,48,72,89` | `12,24,36,45` |

Do not run rembg, gray-color thresholds or the older combined color-mask rules over the imported alpha. The older `extract_remove_background.py` and `remove_background.py` workflows are explicit legacy options only. Never fill all enclosed transparency: real gaps must stay transparent. See [matte-quality.md](matte-quality.md) for the observed failure cases and review procedure. Original videos, alpha masters and matching complete RGB audit samples must remain available beside the transparent output.

Run `scripts/validate_matte.py` on each final RGBA sequence, adding `--rgb-dir` when matching saved RGB is available. Inspect its contrasting-background samples. Enclosed transparency and canvas-edge alpha are diagnostic by default; explicitly requested thresholds can enforce stricter checks. Missing, unreadable or fully empty frames remain technical failures. Compare to RGB to find subject pixels accidentally removed through a gap connected to the image border; counting enclosed holes cannot detect that error.

### 3. Downscale and compress

`scripts/prepare_frame_assets.py` resizes with premultiplied-alpha Lanczos and writes both PNG and WebP. Use:

- scale `0.5`;
- WebP quality `82`;
- WebP compression method `6`;
- RGBA and genuine transparent gaps preserved; no unconditional hole filling after resize;
- source transparent files unchanged.

Validate dimensions, frame counts, numeric ordering, and alpha channels before proceeding.

### 4. Generate twelve-frame returns

Read [seedance-return-video.md](seedance-return-video.md) for the request, prompt and full-video sampling contract. Run `scripts/build_seedance_return_videos.py` for every cut point, then `scripts/matte_video.py` on each complete generated video. Pass its transparent master through `--alpha-video` to `scripts/process_seedance_return_frames.py`, using the exact RGBA source endpoints.

The default helper uses `doubao-seedance-2-5-260628`, requests four seconds, and saves the complete generated video. It sends exact RGB source boundary/first frames, requests a fixed camera and full-frame motion throughout all four seconds, and retains the actual prompt. It does not trim to the first second.

The processor first validates the alpha video's dimensions, FPS and decoded count against the complete generated video. It saves twelve native RGB and RGBA samples selected across all decoded frames, including both endpoints. It preserves the source aspect ratio with at most one row or column for provider rounding; this padding is transparent in RGBA. It uniformly resizes complete frames to source dimensions, using premultiplied alpha for RGBA, and retains source-size RGB audit PNGs. It does not re-matte normalized samples. It keeps the generated RGBA poses, restores exact source RGBA files at indices 0 and 11 without blending, and only then produces half-resolution PNG/WebP. Source `frameMap` determines the boundary endpoint; never mistake decoded source index 72 for retained index 72 in a 24-to-12 FPS conversion.

Check both the actual generated-video timing and the output timing. A four-second 24 FPS provider result may contain 97 frames including the endpoint at t=4 seconds; that final display interval is valid. The Rive return must still contain exactly twelve ordered image files and run at 12 FPS for one second. Similar/repeated poses do not count as missing frames.

The old Seedream helpers and storyboard geometry are retained only for explicitly requested legacy runs. Do not use them or source-reverse sampling as an automatic Seedance failure fallback.

### 5. Record return appearance and motion

Inspect native RGB/RGBA samples, contact sheets on dark, light and saturated backgrounds, continuous alpha-video playback and the one-second return preview when available. Check feather tips, pale clothing, actual background gaps and adjacent frames for dropouts or flicker. Record generated scale/feet drift, uneven motion, early arrival or repeated poses, expression reversals, clipping and detail changes as warnings. Repair demonstrated matte regressions locally from the saved video; do not regenerate a successful video to fix its mask. Continue with the generated frames without regeneration or an approval gate. If viewing is unavailable, record that limitation and continue the technical workflow.

Scale and anchor measurements are optional diagnostics, not rejection thresholds. `scripts/stabilize_return_scale.py` may optionally correct uniform interior drift; preserve exact endpoints and report any use. Do not conceal defects with adaptive crops, nonuniform stretching or fragment deletion. Retry only actual API/transport or unusable-output failures, at most twice for the same failure. Stricter visual rejection requires an explicit user instruction.

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
- Confirm no missing/empty frames or wrong-size assets. Record scale changes, framing drift and discontinuous poses as visual warnings; they do not block delivery by default.

## Common failure modes

- `ModelNotOpen`: the model is not activated for the API account or activation has not propagated.
- Unreadable or wrong-duration video, incompatible source aspect ratio, or empty/missing usable frames: technical failures; correct the cause or retry generation at most twice. Appearance defects remain warnings.
- About 48 frames or four-second returns: uniformly select twelve indices over the whole generated video; set Rive FPS and durationFrames to 12. Never fix timing by trimming the source video to its first second.
- Blank first frame: inspect `Solo.activeComponentId` and target child `Image` IDs.
- Frozen animation: verify `propertyKey="296"` and `KeyFrameId` keys.
- Slow or fast playback: verify source and return FPS are both 12 and return duration is 12 frames.
- Missing final return frame visibility: verify the repeated final held key at frame `12`.
- Missing source frames: inspect `extraction_manifest.json` and ensure decoded frame `N-1` was retained.
- Black-background MP4 mistaken for transparent video: verify a real alpha channel and save a lossless FFV1/BGRA master before PNG extraction.
- Subject holes, opaque background pockets or flickering edges: inspect the model output and native RGB together, repair the local matting stage, and re-extract. Do not apply old gray-threshold cleanup or regenerate motion.
- Enlarged/shrunken subjects, feet drift or uneven return motion: record warnings and use the generated frames. Uniform stabilization is optional, keeps exact endpoints and must be reported.
- Interrupt request lost before the boundary: use a Boolean latch and conditioned `100%` exit.
- Return freezes or switching fails: verify unconditional return-to-Router transition and Router routes.
- A second click leaks: clear `interruptRequested` on return and Router exits.

## Privacy and delivery

- Send the two reference endpoints to Seedance as part of requested generation; keep other source assets local unless upload or publishing is requested.
- Do not put credentials, account IDs, provider request IDs, remote image URLs, CDN UUIDs, or personal paths in generated documentation.
- `rive push`, `--publish`, and hosted assets require explicit user authorization.
