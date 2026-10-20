# video-to-rive-frame-animation

A Codex skill that converts videos into transparent 12 FPS frame assets and one interruptible Rive multi-action timeline.

```text
使用 $video-to-rive-frame-animation 处理 1.mp4和2.mp4
```

Each source video becomes one action. First process its **complete video at native resolution and FPS** with the official **BiRefNet-matting** checkpoint, using 1024×1024 model input and the official foreground-color estimation helper. Save a lossless **FFV1/BGRA video containing real alpha**, then extract the 12 FPS source PNGs. BiRefNet is a per-frame model applied to every video frame; it is not a temporal neural model.

At every whole-second and end-of-media cut point, **Seedance 2.5** (`doubao-seedance-2-5-260628`) generates a **4-second** first/last-frame return video. Matte that entire video at native resolution and FPS before uniformly selecting **12 frames across the complete clip**. Preserve native RGB audit samples and RGBA samples, normalize the complete frame uniformly, restore exact source endpoints, and play the twelve frames at **12 FPS for 1 second** in Rive.

Imported alpha is preserved without the old rembg or gray-threshold cleanup. Protect light clothing, fine props and genuine background gaps. Any provider aspect-rounding correction uses at most one row or column, transparent in RGBA; no crop or nonuniform stretch is allowed. Normalize and resize RGBA with premultiplied alpha, then export 50% PNG and RGBA WebP at quality 82/method 6. Preserve original videos, alpha masters and audit samples. No storyboard slicing, first-second trimming or endpoint cross-fading is used in the default workflow.

Appearance defects are advisory: record them and complete technically valid generation, build and requested integration. Retry only actual API/transport or unusable-output failures, at most twice. Do not silently switch to Seedream or source-reverse sampling. Legacy Seedream helpers remain for explicit requests and existing assets.

`interruptRequested` is a Boolean latch and `nextAction` selects the zero-based next action. An interruption waits for the next configured source boundary, plays its one-second return and routes to the selected action. Verify both structure and actual pointer-driven rendering before delivery.

Check continuous playback as well as RGB/RGBA comparisons on dark, light and saturated backgrounds, especially feather tips, pale clothing and real spaces between limbs and props. Fix demonstrated processing defects from the preserved video without regenerating successful motion.

Requirements: an isolated Python environment with PyTorch, torchvision, the official BiRefNet implementation/checkpoint, Pillow, NumPy, OpenCV, FFmpeg, Rive CLI (`rive`), and ModelArk access to Seedance 2.5. The old rembg/onnxruntime helpers are explicit legacy options only. Credentials stay in a local environment file or environment variables and are never committed. Seedance generation requires sending the two endpoint references; publishing assets or using `rive push` requires separate authorization.

See [SKILL.md](SKILL.md), [video matting](references/video-matting.md), [Seedance video generation](references/seedance-return-video.md), [cut points and routing](references/interruptible-multi-action.md), [matte quality](references/matte-quality.md), and [verification](references/verified-workflow.md).
