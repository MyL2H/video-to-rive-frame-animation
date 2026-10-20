# Matte quality: preserve the subject and remove the background

Use this guide when reviewing video mattes and their PNG/WebP derivatives. The verified examples are illustrated characters with flat or nearly flat gray backgrounds; do not assume that this establishes quality for every textured background, translucent material or strongly blurred edge. Preserve original videos, lossless alpha masters and complete sampled RGB inputs. Repair matting locally rather than generating new motion. Read [video-matting.md](video-matting.md) for backend setup and validation.

## Failure modes that the old mask missed

Two opposite mistakes can both pass a zero-hole check:

- A segmentation model removes light clothing or a feather together with the outside background. The damaged area is connected to the border, so it is not counted as an enclosed hole. Color differences visible in the original RGB can prove that subject detail was erased.
- A true background space between the character, hand and prop is enclosed. Filling every transparent component turns it into an opaque gray patch. Zero holes is not a correctness target.

A pale outline on black may be actual artwork or background mixed into antialiased edge RGB. Compare it with the saved RGB before eroding pixels. Do not indiscriminately delete pale pixels or shrink the entire silhouette.

## Processing requirements

- Default to the official BiRefNet-matting checkpoint with 1024×1024 model input and the official foreground-color estimation helper. Process every native-FPS frame of the complete video, preserving native output dimensions, before selecting PNGs. Save a lossless FFV1/BGRA `transparent_master.mkv` with a real alpha channel. An opaque black-background MP4 is not an alpha master.
- BiRefNet is a per-frame matting model applied to a video. It does not provide temporal neural inference; inspect continuous playback for flicker and transient detail loss.
- Extract source motion at 12 FPS from the alpha master, retaining decoded frame `N-1`. Matte each complete generated return before selecting its twelve uniformly spaced native RGB/RGBA samples. Preserve frame mappings and original RGB audit inputs.
- Preserve imported alpha. Do not chain rembg, gray thresholds or the older combined color-mask rules after the model. The previous `230..241` range and color-supported rembg helpers are explicit legacy options only.
- Compare pale clothing, fine fur, feather tips and held objects against original RGB. A model's low alpha does not establish that a visibly present feature should disappear. Repair demonstrated omissions through the local matting stage instead of globally erasing pale pixels or filling holes.
- Distinguish actual background pockets from subject-colored holes using original RGB and neighboring frames. Preserve real gaps between limbs and props. Do not fill all enclosed alpha components.
- Avoid component-area-only deletion: a small disconnected feature can be part of the drawing. Report suspicious speckles rather than deleting all small components indiscriminately.
- Use the official foreground-color estimation with the predicted alpha to reduce background contamination. Keep it separate from alpha estimation; do not use RGB decontamination as permission to delete subject geometry.
- Normalize whole generated frames only after native matting. If provider aspect rounding requires a correction, permit at most one row or column and record it. RGBA padding is transparent; RGB audit padding may match the original background. Resize uniformly, never crop or stretch axes independently.
- Resize with premultiplied alpha and Lanczos. Preserve alpha after resize; do not run unconditional hole filling over the optimized result. Check both PNG and the final WebP used by Rive.
- Apply the same complete-video matte policy to source actions and generated returns. After repairing source mattes and re-extracting source RGBA, restore return endpoints from those exact source files, verify the matches, then optimize.

## Evidence and review

Use saved native RGB/RGBA pairs and inspect selected frames on black/dark, white/light and saturated backgrounds. Include thin props, pale clothing and enclosed gaps. Inspect continuous native-FPS matte playback, adjacent frames and the final 12 FPS preview for single-frame dropouts or edge flicker. Check the delivered half-resolution WebP as well as the native matte and normalized full-resolution PNGs.

The Seedance processor writes `generated_matte_validation.json` from generated RGB and RGBA before replacing endpoints. Review the restored endpoints against the source action's RGB and RGBA instead: generated RGB is not an exact reference for a restored source frame. The final `matte_validation.json` checks the delivered twelve-frame sequence; its technical failures stop processing.

Record technical checks separately from visual diagnostics. A real alpha master with the original dimensions/FPS/decoded count, nonempty RGBA files, correct frame dimensions, exact endpoints and valid timing are technical requirements. Counts of enclosed transparency, near-background opaque pixels and pixels removed despite RGB differences are diagnostic risks, not semantic ground truth. Canvas-edge alpha can indicate clipping; it is not automatically an unreadable image. Explicit validator thresholds remain available for a user-requested strict workflow.

For these example assets, the regression cases include DELIGHT return 1000/2000 ms frame 3 light clothing, return 3000 ms frame 5 abdomen, return 1000 ms frame 4 feather, and source frames 32/33 enclosed background gaps. These identify observed defects; do not encode character-specific coordinates as universal mask rules.

When a demonstrated defect comes from the processing algorithm, fix or tune that local matting stage from the preserved video and compare again, keeping prior outputs. Do not append the legacy color-key cleanup as a hidden fallback. Generation appearance warnings remain advisory: scale drift, motion jumps, clipping or other imperfect generated details do not trigger regeneration or an approval pause by default. Fixing a matte never authorizes a paid regeneration loop.
