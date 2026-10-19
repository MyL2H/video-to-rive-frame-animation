# Interruptible 12 FPS Multi-action Workflow

This reference defines temporal sampling, cut points, Seedream return generation, manifests, and the state-machine contract for the 12 FPS profile.

## Temporal sampling

The Rive timeline target is exactly 12 FPS. Preserve source motion speed by sampling the source at 12 frames per second.

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

## Seedream return generation

Read [storyboard-geometry.md](storyboard-geometry.md) for the source-ratio equations, exact pixel prompt contract, raw-board overlay review and rejection rules. Canvas divisibility alone does not establish equal semantic panels; never conceal an invalid board with per-panel resizing.

For every boundary:

1. Save the exact RGB source boundary frame as `inputs/first_frame.png`.
2. Save exact source frame `0` as `inputs/last_frame.png`.
3. Build one transparent RGBA reference containing the two endpoints side by side, then send one `doubao-seedream-5-0-pro-260628` request for a source-ratio-derived transparent PNG 4x3 storyboard (`2016x2268` for `512x768` sources). Use `background: transparent`, `output_format: png`, and `n: 1`. Slice twelve equal source-ratio cells (`504x756`, 2:3 only for `512x768` sources) row-major and resize generated interiors back to the source dimensions.
4. Require the complete natural return motion from the boundary pose to the first-frame pose across those 12 images.
5. Preserve the subject's identity, face, clothing, body proportions, overall size, feet anchor, screen position, style, lighting, camera, framing, and background.
6. Reject zoom, shrink, enlarge, crop, reframe, drift, morphing, object changes, text, watermark, and cuts.
7. Replace generated frame `0` and frame `11` with the exact source RGBA endpoints mapped by `extraction_manifest.json`.
8. When generated frames contain real provider Alpha, preserve that Alpha and fill enclosed transparent holes without running `rembg`. For the RGB fallback only, keep complete RGB PNG frames in `processed/frames_rgb` first, then run the same border-connected RGB matte and transparent-hole repair as source frames. Downscale and write PNG/WebP derivatives afterward.
9. Measure interior scale and framing against both exact endpoints. Retry at most twice. Reject invalid grids and distorted body proportions. Only residual uniform drift on a visually valid equal grid may use `scripts/stabilize_return_scale.py` on interior frames; report the fallback.

Seedream transparent generation accepts exactly one input PNG containing real Alpha and rejects RGB inputs, multiple inputs, and non-PNG output. The default request therefore uses one left/right RGBA endpoint reference and returns one transparent 4x3 storyboard. Seedream also rejects generation areas above `4624220` pixels. For `512x768` sources use `2016x2268`; derive other layouts from the original single-frame ratio and require sharp edges, fine detail, and no blur or compression artifacts.

Independent per-frame calls can drift and must be avoided. If transparent generation fails or the output lacks real Alpha, automatically retry with `request_rgb_fallback.json`: two RGB endpoint inputs, one source-ratio-derived 4x3 storyboard (`2016x2268` for `512x768` sources), and an exact flat `RGB(235,235,235)` background with no gradient, texture, noise, vignette, floor, shadow, backdrop object, text, or watermark. Save complete RGB PNGs before using border-connected `rembg` and the inclusive `230..241` color rule. Report the mode actually used.

If Seedream output safety rejects the storyboard after non-sensitive prompt mitigation, use `scripts/build_local_return_frames.py` to sample the exact source motion in reverse at 12 evenly spaced poses. Record `source_reverse_sampling` as an exception and preserve the same return timing and exact endpoints.

## Return timing

A return has 12 unique images:

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
          "framesDir": "a_seedream_returns/return_1000ms/processed/frames_50_webp"
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
- Return animation duration is `12` at 12 FPS, with 12 unique images and a repeated final held key at frame `12`.
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

Also verify:

1. No-click captures continue through every forward segment and do not enter a return.
2. A second click during a return does not cause another return after routing.
3. With multiple actions, setting `nextAction=1` routes to action `1` after the return.
4. Every return first and last frame matches the exact source endpoint.
5. The contact sheet shows no blank frame, wrong-size asset, enlarged or shrunken subject, framing drift, or discontinuous pose at the source-to-return boundary.

## Common failures

- `ModelNotOpen`: activate `doubao-seedream-5-0-pro-260628` for the API account and retry after activation propagates.
- Invalid storyboard grid, cell ratio, or panel count: reject the response and retry the same strict request. The required result is one 4x3 storyboard with 12 equal panels at the original single-frame aspect ratio.
- Transparent request returns opaque RGB: reject it and use the exact `RGB(235,235,235)` fallback request, then matte only saved RGB PNG frames.
- Subject enlarges or shrinks during a return: reject and regenerate with the strict scale and framing prompt. After at most two retries, reject invalid geometry; stabilize only small residual uniform drift on an otherwise valid grid and report it.
- Return starts early after a click: keep the original segment active and use a Boolean condition at its `100%` exit.
- Return freezes on its endpoint: use the unconditional return-to-Router transition.
- A second click leaks into the next action: clear `interruptRequested` on return and Router exits.
- Blank first frame: confirm `KeyFrameId` values target child `Image` IDs, not `ImageAsset` IDs.
