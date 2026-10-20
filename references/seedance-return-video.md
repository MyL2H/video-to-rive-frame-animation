# Seedance 2.5 return video

Use this reference for the default generation route. One source boundary produces one complete four-second video; twelve sampled poses become a one-second Rive return.

## Request contract

- Model: `doubao-seedance-2-5-260628`.
- API: `POST /contents/generations/tasks`, then query that task to completion.
- `duration: 4`, `ratio: adaptive`, `generate_audio: false`, `watermark: false`, `output_format: mp4`.
- Two RGB PNG image inputs: `role: first_frame` is the exact source cut frame; `role: last_frame` is source frame `0`. Keep the complete source frame and margins.
- Persist actual text as `prompt.txt` and the request without authorization headers as `request.json`. Keep task IDs and signed result URLs private; never paste these in reports or source control.

Use `scripts/build_seedance_return_videos.py`. Prepare/submit/poll/download are separable; `run` reuses saved work. A successful POST followed by a timeout is not evidence that the task was never created. Resolve an uncertain submission before retrying. Local credential/configuration errors and definitive API rejections are different from an uncertain network outcome: fix the cause and retry without treating an unaccepted request as a running task. Keep rejected-attempt metadata. A changed model or prompt needs a new output directory so it cannot overwrite the original request.

Retry actual technical failures at most twice, not appearance warnings. A task that reached terminal failure remains recorded; prepare a retry under a new attempt output root instead of overwriting its task/status files. Polling or re-downloading an already submitted task is not a new generation attempt. Do not fall back to another generator or source-reverse sampling without the user's instruction.

## Prompt content

Ask for a continuous, coherent return from the first reference to the last reference, with motion distributed over all four seconds and arrival at the end. Do not instruct the model to finish in the first second and hold for three seconds.

Preserve identity, face, clothing, body proportions, head/torso size, held objects, colors and illustration style. Lock the camera, complete source frame, feet contact location and clear margins. No zoom, crop, reframing, cuts or scene changes. Keep ears, feet, tail and held props inside the image. Request crisp artwork and the same flat `RGB(235,235,235)` background without gradients, shadows, texture, text or watermark. Request one ordinary video, not a sprite sheet or grid.

Derive any numerical aspect ratio, feet coordinate or scale guidance from that input's dimensions and endpoints. Do not copy one character's name, 2:3 ratio or 87% feet position into unrelated requests. Add motion-specific wording only when supported by the source poses. Prompt constraints improve consistency but do not guarantee exact feature registration; record remaining drift without a visual retry loop.

## Complete-video matting, sampling and geometry

Decode the entire video and record actual dimensions, FPS and decoded frame count `N`. Process every frame at native resolution and FPS with `scripts/matte_video.py` before selecting PNGs: the default is official BiRefNet-matting with 1024×1024 model input and official foreground-color estimation. Preserve the original clip and a lossless FFV1/BGRA `transparent_master.mkv` containing real alpha. BiRefNet processes frames independently; it is not a temporal neural model. See [video-matting.md](video-matting.md) for setup and verification.

Supply that master through `--alpha-video` to `scripts/process_seedance_return_frames.py`. Verify matching dimensions, FPS and decoded counts against the original clip. Require enough frames to select twelve distinct indices. Sample matching RGB and RGBA with:

```text
indices[i] = round(i * (N - 1) / 11), i = 0..11
```

This includes both the first and final decoded frames. For a 97-frame 24 FPS result the indices are `0,9,17,26,35,44,52,61,70,79,87,96`. Its first-to-last timestamp spans four seconds; `N/FPS` includes the last frame's display interval and is about 4.042 seconds. That normal final-frame interval is not a duration failure.

Save each sampled native RGB and native RGBA frame before resizing. Preserve the full-frame aspect ratio. If the provider rounds one dimension down by one pixel, add at most one row or column at the bottom or right to restore the exact source ratio, record it, then resize uniformly. RGB audit padding may match the background; RGBA padding must be transparent. Use premultiplied alpha when resizing RGBA. Example: `786x1178` becomes `786x1179` before uniform resizing to `512x768`. If the ratio cannot be reconciled this way, report a technical geometry failure; never stretch width/height independently, crop the subject or normalize each pose to its own bounding box.

## Matte, endpoints and output timing

Keep source-size `frames_rgb` as audit references and normalized generated RGBA separate from the exact-endpoint sequence. Preserve the alpha imported from the complete-video matting stage. Do not run `remove_background.py`, rembg, gray thresholds or the old combined-mask cleanup over these samples; those are explicit legacy options only. Protect light clothing and fine props, and preserve actual background spaces even when enclosed. Read [matte-quality.md](matte-quality.md); do not use a zero-hole target or unconditional fill.

Restore frame `0` from the source cut RGBA file and frame `11` from source frame `0` RGBA, using the source extraction `frameMap`. Verify exact file hashes before optimizing. Do not blend/cross-fade the restored endpoints into generated poses.

After restoring exact endpoints, write twelve full-resolution RGBA frames, half-resolution PNG and quality-82/method-6 RGBA WebP. Downscale RGBA with premultiplied-alpha Lanczos. Play at 12 FPS for one second; the Rive return repeats the held last-image key at timeline frame `12`. The generated video's FPS and four-second duration do not become the Rive playback timing.

Review native RGB/RGBA differences on dark, light and saturated backgrounds, continuous alpha-video playback and a one-second preview. Include feather tips, pale clothing, true background gaps and adjacent frames to detect dropouts or flicker. Fix confirmed mask errors from the preserved video without another generation request. Uniform generated interior size can still differ from restored source endpoints; keep that observation as an advisory visual warning. Any optional stabilization must preserve exact endpoints and be disclosed.

## Tested behavior

The initial THINK 3-second trial used one successful four-second generation, all twelve full-span sampled frames and exact source endpoints. It avoided storyboard slicing and kept the feet inside the image. Interior feet placement was stable, but restoring source endpoints exposed a scale/position jump. This is evidence for the video workflow, not a guarantee of visually seamless results for every cut point.
