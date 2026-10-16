# video-to-rive-frame-animation

A Codex Skill that converts one or more videos into optimized, transparent Rive frame animations.

## Usage

```text
使用 $video-to-rive-frame-animation 处理 1.mp4和2.mp4
```

That single request is sufficient. Each video is processed independently.

## Default workflow

- Preserve source FPS and extract every frame.
- Remove the background and write transparent PNG frames.
- Resize to 50% with Lanczos.
- Write optimized PNG and RGBA WebP frames at quality 82.
- Build a separate looping Rive CLI timeline for each video.
- Verify frame continuity, Rive structure, the built `.riv`, and sampled renders.

## Outputs

```text
<stem>_frames/             full-resolution transparent PNG frames
<stem>_frames_50_png/      half-resolution optimized PNG frames
<stem>_frames_50_webp/     half-resolution transparent WebP frames
<stem>_rive/               Rive CLI project and built .riv
```

## Requirements

- Python 3 and Pillow
- OpenCV, `rembg`, and `onnxruntime` for video extraction and background removal
- Rive CLI available as `rive`

See [SKILL.md](SKILL.md) for the operating instructions and [references/verified-workflow.md](references/verified-workflow.md) for the input/output contract, Rive invariants, validation procedure, and limitations.
