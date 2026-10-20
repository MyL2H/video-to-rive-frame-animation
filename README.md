# video-to-rive-frame-animation

A Codex skill that converts videos into transparent 12 FPS frame assets and one interruptible Rive multi-action timeline.

```text
使用 $video-to-rive-frame-animation 处理 1.mp4和2.mp4
```

Each source video becomes one action. At every whole-second and end-of-media cut point, **Seedance 2.5** (`doubao-seedance-2-5-260628`) generates a **4-second** first/last-frame return video. The workflow uniformly samples **12 frames from the complete video**, restores exact source endpoints, and plays them at **12 FPS for 1 second** in Rive.

Complete RGB PNGs are saved before border-connected background removal. Frames are resized to 50% with Lanczos and exported as PNG and RGBA WebP at quality 82/method 6. The complete generated videos and native sampled frames remain available for review. No storyboard slicing, first-second trimming or endpoint cross-fading is used in the default workflow.

Appearance defects are advisory: record them and complete technically valid generation, build and requested integration. Retry only actual API/transport or unusable-output failures, at most twice. Do not silently switch to Seedream or source-reverse sampling. Legacy Seedream helpers remain for explicit requests and existing assets.

`interruptRequested` is a Boolean latch and `nextAction` selects the zero-based next action. An interruption waits for the next configured source boundary, plays its one-second return and routes to the selected action. Verify both structure and actual pointer-driven rendering before delivery.

Requirements: Python 3, Pillow, NumPy, OpenCV, rembg/onnxruntime, Rive CLI (`rive`), and ModelArk access to Seedance 2.5. Credentials stay in a local environment file or environment variables and are never committed. Seedance generation requires sending the two endpoint references; publishing assets or using `rive push` requires separate authorization.

See [SKILL.md](SKILL.md), [Seedance video generation](references/seedance-return-video.md), [cut points and routing](references/interruptible-multi-action.md), and [verification](references/verified-workflow.md).
