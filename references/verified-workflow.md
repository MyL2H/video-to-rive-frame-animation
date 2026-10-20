# Verified 12 FPS Workflow and Constraints

This document records frame-processing and Rive invariants for the 12 FPS Seedance profile. Keep source FPS metadata, but play retained frame sequences at 12 FPS.

## Default request handling

Apply this profile without asking for more parameters:

1. Process each video independently.
2. Temporally sample to 12 FPS while preserving motion speed.
3. Always retain decoded source frame `N-1`.
4. Produce transparent full-resolution PNG frames.
5. Resize to 50% with Lanczos.
6. Produce optimized PNG and RGBA WebP at quality 82 and method 6.
7. Generate one four-second Seedance 2.5 return video for every whole-second and end-of-media cut point; uniformly sample exactly 12 frames over the complete video.
8. Replace generated return endpoints with exact source frames.
9. Build one combined Rive project sized to the reduced frames.
10. Verify frame continuity, Rive structure, local build, and sampled renders.

Appearance and motion checks are advisory: record visual warnings and complete the full workflow and requested integration. Do not retry or request approval solely for visual defects. If a batch item has a technical failure, continue independent items and summarize failures at the end.

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

Run `scripts/validate_matte.py` on each final RGBA sequence. Its zero-hole and transparent-edge metrics do not detect opaque background residue, so inspect its magenta contact sheet when available. Appearance findings, including this tool's nonzero status for holes or opaque edges, are advisory under the default policy. Missing, unreadable or fully empty frames remain technical failures. Transparent background between limbs is valid.

### 3. Downscale and compress

`scripts/prepare_frame_assets.py` resizes with Lanczos and writes both PNG and WebP. Use:

- scale `0.5`;
- WebP quality `82`;
- WebP compression method `6`;
- RGBA preserved;
- source transparent files unchanged.

Validate dimensions, frame counts, numeric ordering, and alpha channels before proceeding.

### 4. Generate twelve-frame returns

Read [seedance-return-video.md](seedance-return-video.md) for the request, prompt and full-video sampling contract. Run `scripts/build_seedance_return_videos.py` for every cut point, followed by `scripts/process_seedance_return_frames.py` using the exact RGBA source endpoints.

The default helper uses `doubao-seedance-2-5-260628`, requests four seconds, and saves the complete generated video. It sends exact RGB source boundary/first frames, requests a fixed camera and full-frame motion throughout all four seconds, and retains the actual prompt. It does not trim to the first second.

The processor saves twelve native sampled RGB PNGs selected across all decoded frames, including both endpoints. It preserves the source aspect ratio with at most a one-pixel background pad for provider rounding, uniformly resizes to source dimensions, saves complete RGB PNGs and applies the same matte rules as the source. It keeps the generated RGBA poses, restores exact source RGBA files at indices 0 and 11 without blending, and produces half-resolution PNG/WebP. Source `frameMap` determines the boundary endpoint; never mistake decoded source index 72 for retained index 72 in a 24-to-12 FPS conversion.

Check both the actual generated-video timing and the output timing. A four-second 24 FPS provider result may contain 97 frames including the endpoint at t=4 seconds; that final display interval is valid. The Rive return must still contain exactly twelve ordered image files and run at 12 FPS for one second. Similar/repeated poses do not count as missing frames.

The old Seedream helpers and storyboard geometry are retained only for explicitly requested legacy runs. Do not use them or source-reverse sampling as an automatic Seedance failure fallback.

### 5. Record return appearance and motion

Inspect native sampled frames, contact sheets and the one-second playback preview when available. Record scale/feet drift, uneven motion, early arrival or repeated poses, expression reversals, clipping, background residue and detail changes as warnings. Continue with the generated frames without regeneration or an approval gate. If viewing is unavailable, record that limitation and continue the technical workflow.

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
- Enlarged/shrunken subjects, feet drift or uneven return motion: record warnings and use the generated frames. Uniform stabilization is optional, keeps exact endpoints and must be reported.
- Interrupt request lost before the boundary: use a Boolean latch and conditioned `100%` exit.
- Return freezes or switching fails: verify unconditional return-to-Router transition and Router routes.
- A second click leaks: clear `interruptRequested` on return and Router exits.

## Privacy and delivery

- Send the two reference endpoints to Seedance as part of requested generation; keep other source assets local unless upload or publishing is requested.
- Do not put credentials, account IDs, provider request IDs, remote image URLs, CDN UUIDs, or personal paths in generated documentation.
- `rive push`, `--publish`, and hosted assets require explicit user authorization.
