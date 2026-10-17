# video-to-rive-frame-animation

A Codex Skill that converts one or more videos into optimized transparent frame assets and one interruptible Rive CLI multi-action timeline.

## Usage

```text
使用 $video-to-rive-frame-animation 处理 1.mp4和2.mp4
```

That single request is sufficient. Each video becomes one logical action in one combined Rive project.

## Default workflow

- Preserve source FPS and extract every frame.
- Remove backgrounds and write transparent PNG frames.
- Resize to 50% and create optimized PNG and RGBA WebP frames.
- Generate return motion for every whole-second cut point and the decoded end.
- Use exact source boundary and first frames as the fixed one-second return endpoints.
- Build a Rive state machine with delayed interruption and action routing.
- Verify the project structure, local build, frame matching, and pointer-driven behavior.

## Interaction

- `interruptRequested`: Boolean input that requests an interruption.
- `nextAction`: Number input containing the zero-based action index to start after the return.

An interruption continues the current action to the next configured cut point, plays a one-second return to frame 0, and then starts `nextAction`.

## Outputs

```text
<stem>_frames/
<stem>_frames_50_png/
<stem>_frames_50_webp/
<stem>_seedance_returns/
<name>_multi_action_manifest.json
<name>_multi_action_rive/
```

## Requirements

- Python 3, Pillow, OpenCV, `rembg`, and `onnxruntime`
- Rive CLI available as `rive`
- Volcengine ModelArk access to the configured Seedance model

Credentials stay in local environment variables or a protected local environment file and are never committed.

See [SKILL.md](SKILL.md) for the operating instructions, [references/interruptible-multi-action.md](references/interruptible-multi-action.md) for the cut-point and state-machine contract, and [references/verified-workflow.md](references/verified-workflow.md) for frame-processing and validation constraints.
