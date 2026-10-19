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
- Generate one high-resolution transparent Seedream storyboard for every whole-second cut point and the decoded end. Derive twelve equal 4x3 cells from the original single-frame aspect ratio. For 512x768 sources, use a 2016x2268 canvas with exactly 504x756 cells; never stretch other source ratios into 2:3. Include exact pixel boundaries and one uniform coordinate scale in both transparent and RGB prompts.
- Seedream transparent mode requires exactly one PNG input with real Alpha and PNG output. Use one side-by-side RGBA endpoint reference with `background: transparent`, `output_format: png`, and `n: 1`.
- If transparent generation fails or returns no real Alpha, automatically request the same storyboard with two RGB inputs and a flat `RGB(235,235,235)` background. Save complete RGB PNGs first, then use border-connected `rembg` and the inclusive `230..241` range to remove background.
- Prompt for crisp high-resolution artwork with sharp edges and fine facial, hair, and clothing detail, explicitly excluding blur and compression artifacts.
- Replace return frames `0` and `11` with the exact source RGBA boundary and first frames.
- Preserve verified provider Alpha without rerunning background removal; matte only saved RGB fallback frames.
- Prompt Seedream to preserve subject identity, scale, body proportions, feet anchor, and framing throughout the return.
- Complete generation, processing, build and requested integration when frames are technically usable. Record appearance and motion imperfections as warnings; do not regenerate, pause for approval or use source-reverse sampling because of them. Retain raw boards and overlays, use fixed 4x3 slicing and preserve exact endpoints. Uniform interior stabilization is optional and must be reported.
- Keep exact source-ratio canvas dimensions, twelve nonempty ordered frames, RGBA assets, 12 FPS, one-second returns and working Rive routing as hard checks. Similar or repeated poses are allowed. Retry actual API/transport or unusable-output failures at most twice; apply stricter visual rejection only when explicitly requested.
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

See [SKILL.md](SKILL.md) for the operating instructions, [references/storyboard-geometry.md](references/storyboard-geometry.md) for fixed source-ratio geometry, technical checks and advisory visual review, [references/interruptible-multi-action.md](references/interruptible-multi-action.md) for the cut-point and state-machine contract, and [references/verified-workflow.md](references/verified-workflow.md) for frame-processing and validation constraints.
