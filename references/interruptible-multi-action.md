# Interruptible Multi-action Workflow

This reference defines the cut-point, return-clip, manifest, and state-machine contract used by the default profile. Read it before generating return clips or writing the Rive RML.

## Cut points

For a decoded source with `N` frames at `F` FPS:

1. The final frame index is `N - 1`.
2. For each whole second `k = 1, 2, ...`, compute `round(k * F)` while the result is less than `N - 1`.
3. Append `N - 1` as the final boundary, even when it is not at a whole second.
4. Remove duplicate frame indices.

Internal labels use `round(frame * 1000 / F)`. The final label uses `round(N * 1000 / F)` to represent the decoded media duration. The implementation must not infer `N` from filenames alone.

Example:

| Decoded source | Boundary frames | Labels |
|---|---|---|
| `107` frames at `24 FPS` | `24, 48, 72, 96, 106` | `1000ms, 2000ms, 3000ms, 4000ms, 4458ms` |
| about `6.6s` at `24 FPS` | `24, 48, 72, 96, 120, 144, <end>` | `1s, 2s, 3s, 4s, 5s, 6s, <decoded end>` |

An interruption at `t` is handled at the first boundary greater than or equal to `t`. If `t` is exactly a boundary, that boundary is used.

## Return clip generation

For every boundary:

1. Save the exact RGB source boundary frame as `inputs/first_frame.png`.
2. Save exact source frame `0` as `inputs/last_frame.png`.
3. Ask Seedance for a continuous four-second first-frame to last-frame transition. Keep the same subject, identity, style, scene, lighting, camera, framing, and background; use no cuts or camera movement.
4. Retain every decoded generated frame.
5. Replace generated frame `0` with `first_frame.png` and generated frame `N-1` with `last_frame.png`.
6. Run the same matte, downscale, PNG, and WebP stages as source frames.
7. Play the processed sequence as exactly `1s` using `playbackFps = processedFrameCount - 1` and animation duration `processedFrameCount - 1`.

Use `--blend-frames 0`. Cross-fades are unsuitable for the verified one-second return because they produce visible double exposure. Seedance supplies only the motion between the endpoints; it is not expected to reproduce those endpoints pixel-for-pixel.

The generation helper accepts `ARK_API_KEY` or `SEEDANCE_API_KEY_ID` plus `SEEDANCE_API_KEY_SECRET`. Keep the local environment file unreadable by group or world, and never place its contents in a manifest, log, screenshot, Skill, or Git commit.

## Multi-action manifest

All action frame directories must contain continuous numeric suffixes from `0`, use the same `*.webp` pattern, and have identical RGBA dimensions.

```json
{
  "name": "Character Multi-action Playback",
  "artboardSize": [256, 384],
  "framePattern": "*.webp",
  "defaultAction": 0,
  "inputs": ["a.mp4", "b.mp4"],
  "actions": [
    {
      "name": "Action A",
      "fps": 24,
      "framesDir": "a_frames_50_webp",
      "returns": [
        {
          "label": "1000ms",
          "boundaryFrame": 24,
          "framesDir": "a_seedance_returns/return_1000ms/processed/frames_50_webp"
        },
        {
          "label": "end",
          "boundaryFrame": 106,
          "framesDir": "a_seedance_returns/return_end/processed/frames_50_webp"
        }
      ]
    },
    {
      "name": "Action B",
      "fps": 30,
      "framesDir": "b_frames_50_webp",
      "returns": [
        {
          "label": "1000ms",
          "boundaryFrame": 30,
          "framesDir": "b_seedance_returns/return_1000ms/processed/frames_50_webp"
        }
      ]
    }
  ]
}
```

Return boundaries must increase and remain within the source action. The action order defines the zero-based `nextAction` values.

## State-machine topology

```text
Entry -> A segment 0 -> A segment 1 -> ... -> A segment 0
               |               |
               | 100% when     | 100% when
               | interrupted   | interrupted
               v               v
          A return 0       A return 1
               |               |
               +--> Router <--+
                       |
                       +-- nextAction=0 --> A segment 0
                       +-- nextAction=1 --> B segment 0
```

Each action is one logical timeline split at return boundaries. The segment order preserves every source frame and its FPS. The split exists only so a delayed conditioned exit can select the correct return at a whole second.

### Inputs

- `interruptRequested`: `ViewModelPropertyBoolean`. Set it to `true` to request the delayed interruption.
- `nextAction`: `ViewModelPropertyNumber`. Set it to the zero-based target action before setting `interruptRequested`.

The click listener writes only `interruptRequested=true`. A host that switches actions must write `nextAction` first, then fire the interruption. For a plain interruption, leave `nextAction` at the current action or the configured default.

### Transitions

- Each source segment has a conditioned `100%` exit to its matching return and an unconditional `100%` exit to the next source segment.
- The conditioned transition reads `interruptRequested == true` and writes `false` as a transition action.
- Each return has one unconditional `100%` exit to its Router and writes `interruptRequested=false` in that transition action.
- Each Router has immediate, conditioned routes for every `nextAction` value. Router transitions also write `interruptRequested=false`.
- Entry goes to action `defaultAction`, segment `0`.

Do not use `ViewModelPropertyTrigger` for the delayed request. A trigger is spent at the end of the frame in which it fires, so it cannot remain true until a later `100%` exit. Do not route directly from a return with `nextAction` conditions: that allows an early route and freezes on the endpoint. The unconditional return-to-Router exit is required.

## RML invariants

- Put every `ImageAsset` at RML root level before the `Artboard`.
- Put every frame `Image` inside one shared `Solo`.
- Key `Solo.activeComponentId` (`propertyKey="296"`) with `KeyFrameId` values pointing to child `Image` component IDs.
- Use `interpolationType="hold"` for every image-reference key.
- For `N` frames in an animation, use `duration="N-1"`; use the intended playback rate as `fps`.
- Keep transitions between these one-shot frame animations at `duration="0"`.
- Match the artboard and image dimensions unless scaling is intentional.

## Verification

Run all structural checks:

```bash
rive project --verify
rive inspect project --summary
rive project --once
```

Then verify behavior with real pointer events. For a `3.2s` interruption on a `256x384` artboard:

```bash
rive project --screenshot=at_3500.png \
  --advance=3200ms --pointer=click@128,192 --advance=300ms

rive project --screenshot=at_4500.png \
  --advance=3200ms --pointer=click@128,192 --advance=1300ms

rive project --screenshot=at_5000.png \
  --advance=3200ms --pointer=click@128,192 --advance=1800ms

rive project --screenshot=at_5200.png \
  --advance=3200ms --pointer=click@128,192 --advance=2000ms
```

Expected sequence:

| Requested capture | Expected frame family |
|---|---|
| `3.5s` | source around frame `84` for `24 FPS` |
| `4.0s` | boundary frame or the first few return frames |
| `4.5s` | matching return midpoint, around half of its frame count |
| `5.0s` | exact source frame `0` or the matching return endpoint |
| `5.2s` | source action restarted near frame `5` |

Headless screenshots may land one to three return frames late because startup and capture time are not exact. Composite transparent frames over the screenshot background and image-match the result against the complete candidate sequence; do not trust `--advance` alone.

Also verify:

1. No-click captures continue through every forward segment and do not enter a return.
2. A second click during a return does not cause another return after routing.
3. With multiple actions, `--data=nextAction=1` routes to action `1` after the return and its screenshots match that action rather than action `0`.
4. Every return first and last frame matches the corresponding exact source endpoint.
5. The contact sheet shows no blank frame, wrong-size asset, or discontinuous pose at the source-to-return boundary.

## Common failures

- `InvalidAccessKey` or `InvalidEndpointOrModel.NotFound`: confirm the credentials are for Volcengine ModelArk and the account can access the configured model. Do not retry by copying credentials into commands or files.
- Generated endpoints differ: expected; replace both endpoints with exact source frames.
- Return starts early after a click: a duplicate animation state restarted segment time. Keep the original segment active and use a Boolean condition at its `100%` exit.
- Return freezes on its endpoint: a conditional route fired from the return state. Insert the unconditional Router state.
- The action unexpectedly returns after every cycle: `interruptRequested` was not cleared by transition actions.
- A second click during a return leaks into the next action: add the clear action to both the return exit and Router exit.
- Blank first frame: confirm `KeyFrameId` values target child `Image` IDs, not `ImageAsset` IDs.
- Clean verify but wrong playback: `--verify` is structural; complete the rendered interaction checks.
