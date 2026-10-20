# Interruptible 12 FPS Multi-action Workflow

This reference defines temporal sampling, cut points, Seedance return generation, manifests, and the state-machine contract for the 12 FPS profile.

## Temporal sampling

The Rive timeline target is exactly 12 FPS. First matte the complete original video at native resolution and FPS, using official BiRefNet-matting with 1024×1024 model input and official foreground-color estimation. Preserve a lossless FFV1/BGRA master containing real alpha. Then preserve source motion speed by sampling that master at 12 frames per second, saving matching original RGB audit PNGs. This backend processes frames independently; it is not a temporal neural model. Read [video-matting.md](video-matting.md) before processing.

For source FPS `S`, target FPS `T=12`, and `N` decoded frames:

1. Select source indices `round(j * S / T)` for target frames `j=0,1,...` while the mapped index is below `N-1`.
2. Always append source frame `N-1` when it is not already selected.
3. Write output frames as continuous `0..M-1` and retain the source mapping in `extraction_manifest.json`.

For a 24 FPS source this normally selects `0,2,4,...`; an odd final source frame is appended exactly.

Examples:

| Source | Retained source frames | 12 FPS count |
|---|---|---:|
| 141 frames at 24 FPS | `0,2,...,140` | 71 |
| 90 frames at 24 FPS | `0,2,...,88,89` | 46 |

## Cut points

Use all whole-second source boundaries strictly before the decoded end, then append source frame `N-1`.

For each source boundary, use the matching output index from `extraction_manifest.json`. Internal labels use `round(sourceFrame * 1000 / sourceFps)`. The final label uses `round(N * 1000 / sourceFps)` to represent decoded media duration.

Examples:

| Source | Source cut frames | 12 FPS cut frames | Labels |
|---|---|---|---|
| 141 frames at 24 FPS | `24,48,72,96,120,140` | `12,24,36,48,60,70` | `1000ms,2000ms,3000ms,4000ms,5000ms,5875ms` |
| 90 frames at 24 FPS | `24,48,72,89` | `12,24,36,45` | `1000ms,2000ms,3000ms,3750ms` |

An interruption at `t` is handled at the first boundary greater than or equal to `t`. If `t` is exactly a boundary, that boundary is used.

## Seedance return generation

Read [seedance-return-video.md](seedance-return-video.md) before generating returns. Use Seedance 2.5 (`doubao-seedance-2-5-260628`) for each source boundary. Send the complete exact RGB boundary frame and source frame `0` as first/last references, and request a four-second continuous return across the entire clip.

Keep the full generated video and matte every frame at native resolution and FPS before sampling. Save a lossless FFV1/BGRA master and verify its dimensions, FPS and decoded count against the original clip. Sample twelve distinct ordered matching RGB/RGBA frames with `round(i*(N-1)/11)`, including the first and last; this is different from sampling source motion at 12 FPS. Preserve native RGB audit and RGBA samples. Reconcile only one-pixel provider aspect rounding with at most one padded row or column if needed; RGBA padding must be transparent. Resize the full frame uniformly to source dimensions using premultiplied alpha for RGBA, and retain normalized RGB audit PNGs.

Use the same complete-video matting backend as source actions. Preserve imported alpha without rembg, gray-threshold cleanup or unconditional hole filling; those older processing helpers are explicit legacy options only. Protect visible subject pixels and preserve true background gaps. Read [matte-quality.md](matte-quality.md). Replace output `0`/`11` with exact source RGBA endpoints found through `extraction_manifest.json`, without cross-fades. Only afterward downscale to 50% with premultiplied-alpha Lanczos and export PNG plus quality-82/method-6 RGBA WebP.

Record visual defects as warnings and continue with usable frames. Optional uniform stabilization must preserve endpoints and be reported. Do not regenerate merely for appearance, and do not silently substitute Seedream or source-reverse sampling. Retry actual technical failures at most twice; reconcile uncertain submissions before another paid POST.

## Return timing

A return has 12 ordered frame files; similar or repeated poses are allowed:

- `fps: 12`
- `durationFrames: 12`
- `durationSeconds: 1`
- Image keys at Rive frames `0..11` with `hold` interpolation.
- Repeat the last image's held key at Rive frame `12` so the final pose remains visible through the end of the second.

This differs from the source-segment rule. Source segments use `duration=N-1` at their playback FPS; the one-second return must include the final-frame hold interval.

## Multi-action manifest

All action frame directories must contain continuous numeric suffixes from `0`, use the same `*.webp` pattern, and have identical RGBA dimensions.

```json
{
  "name": "Character Multi-action Playback",
  "artboardSize": [256, 384],
  "framePattern": "*.webp",
  "returnFps": 12,
  "returnDurationFrames": 12,
  "returnDurationSeconds": 1,
  "defaultAction": 0,
  "inputs": ["a.mp4", "b.mp4"],
  "actions": [
    {
      "name": "Action A",
      "fps": 12,
      "framesDir": "a_frames_12fps_50_webp",
      "returns": [
        {
          "label": "1000ms",
          "boundaryFrame": 12,
          "fps": 12,
          "durationFrames": 12,
          "framesDir": "a_seedance_returns/return_1000ms/processed/frames_50_webp"
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
               | interrupted   | interrupted
               v               v
          A return 0       A return 1
               |               |
               +--> Router <--+
                       |
                       +-- nextAction=0 --> A segment 0
                       +-- nextAction=1 --> B segment 0
```

Each action is one logical timeline split at return boundaries. The segment order preserves every retained 12 FPS source frame.

### Inputs

- `interruptRequested`: `ViewModelPropertyBoolean`. Set it to `true` to request the delayed interruption.
- `nextAction`: `ViewModelPropertyNumber`. Set it to the zero-based target action before setting `interruptRequested`.

### Transitions

- Each source segment has a conditioned `100%` exit to its matching return and an unconditional `100%` exit to the next source segment.
- The conditioned transition reads `interruptRequested == true` and writes `false` as a transition action.
- Each return has one unconditional `100%` exit to its Router and writes `interruptRequested=false`.
- Each Router has immediate, conditioned routes for every `nextAction` value. Router transitions also write `interruptRequested=false`.
- Entry goes to action `defaultAction`, segment `0`.

Do not use `ViewModelPropertyTrigger` for the delayed request.

## RML invariants

- Put every `ImageAsset` at RML root level before the `Artboard`.
- Put every frame `Image` inside one shared `Solo`.
- Key `Solo.activeComponentId` (`propertyKey="296"`) with `KeyFrameId` values pointing to child `Image` component IDs.
- Use `interpolationType="hold"` for every image-reference key.
- Source animation duration is `N-1` at 12 FPS.
- Return animation duration is `12` at 12 FPS, with 12 ordered frame files and a repeated final held key at frame `12`.
- Keep transitions between frame animations at `duration="0"`.
- Match artboard and image dimensions unless scaling is intentional.

## Verification

Run all structural checks:

```bash
rive project --verify
rive inspect project --summary
rive project --once
```

Then verify behavior with real pointer events. Sample interruption cut points, return frames at 0, 0.5, and 1 second, and restarted source frames. Image-match screenshots against complete candidate sequences because startup and capture time are not exact.

Review continuous matte playback and RGB/RGBA comparisons on dark, light and saturated backgrounds. Check thin feathers, pale clothing and actual background gaps for single-frame dropouts. Repair demonstrated local processing defects from saved video; this does not change the generation-warning policy.

Also verify:

1. No-click captures continue through every forward segment and do not enter a return.
2. A second click during a return does not cause another return after routing.
3. With multiple actions, setting `nextAction=1` routes to action `1` after the return.
4. Every return first and last frame matches the exact source endpoint.
5. No missing/empty frames or wrong-size assets. Record scale drift, framing drift and discontinuous poses at the source-to-return boundary as warnings; these do not block delivery by default.

## Common failures

- `ModelNotOpen`: activate `doubao-seedance-2-5-260628` for the API account and retry after activation propagates.
- Unreadable or wrong-duration video, incompatible source aspect ratio, or empty/missing usable frames: technical failures. Correct the cause or retry generation at most twice; do not stretch a wrong ratio or invent frames.
- About 48 return frames or four-second Rive playback: sample twelve frames across the complete generated video and set Rive FPS/duration to 12/12. Do not truncate the generated video to one second.
- Subject enlarges or shrinks during a return: record a warning and use the generated frames. Uniform interior stabilization is optional; keep exact endpoints and report any use.
- Return starts early after a click: keep the original segment active and use a Boolean condition at its `100%` exit.
- Return freezes on its endpoint: use the unconditional return-to-Router transition.
- A second click leaks into the next action: clear `interruptRequested` on return and Router exits.
- Blank first frame: confirm `KeyFrameId` values target child `Image` IDs, not `ImageAsset` IDs.
