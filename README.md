# video-to-rive-frame-animation

A Codex Skill that converts one or more videos into optimized transparent frame assets and one interruptible Rive CLI multi-action timeline.

## Usage

```text
使用 $video-to-rive-frame-animation 处理 1.mp4和2.mp4
```

That single request is sufficient. Each video becomes one logical action in one combined Rive project.

## Default workflow

- Sample source motion to exactly 12 FPS while preserving playback speed and the decoded end frame.
- Save complete RGB PNG frames first, then create transparent PNG frames. For the uniform background, remove only inclusive per-channel `RGB 230..241` regions connected to an image border, keep complete subject components, and use `rembg` for edge feathering and hole repair.
- Resize to 50% with Lanczos, repair any resampling holes, and create optimized PNG and RGBA WebP frames.
- Generate exactly 12 ordered Seedream return images for every whole-second cut point and the decoded end.
- Replace return frames `0` and `11` with the exact source RGBA boundary and first frames.
- Require Seedream to preserve subject identity, scale, body proportions, feet anchor, framing, and background throughout the return.
- Measure return-frame scale against exact endpoints, retry rejected generations, and normalize only interior frames when the provider still drifts.
- Build a Rive state machine with delayed interruption and action routing.
- Verify the project structure, local build, frame matching, and pointer-driven behavior.

## Interaction

- `interruptRequested`: Boolean input that requests an interruption.
- `nextAction`: Number input containing the zero-based action index to start after the return.

An interruption continues the current action to the next configured cut point, plays a one-second return to frame 0, and then starts `nextAction`.

## Outputs

```text
<stem>_frames_12fps/
<stem>_frames_12fps_rgb/
<stem>_frames_12fps_50_png/
<stem>_frames_12fps_50_webp/
<stem>_seedream_returns/
<name>_multi_action_manifest.json
<name>_multi_action_rive/
```

## Requirements

- Python 3, Pillow, OpenCV, `rembg`, and `onnxruntime`
- Rive CLI available as `rive`
- Volcengine ModelArk access to `doubao-seedream-5-0-pro-260628`

Credentials stay in local environment variables or a protected local environment file and are never committed.

See [SKILL.md](SKILL.md) for the operating instructions, [references/interruptible-multi-action.md](references/interruptible-multi-action.md) for the cut-point and state-machine contract, and [references/verified-workflow.md](references/verified-workflow.md) for frame-processing and validation constraints.
