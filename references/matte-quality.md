# Matte quality: preserve the subject and remove the background

Use this guide for flat or nearly flat gray-background character videos. Preserve the complete saved RGB inputs; repair masks locally rather than generating new motion. This profile does not establish a general solution for textured/moving backgrounds, translucent materials or strongly blurred edges.

## Failure modes that the old mask missed

Two opposite mistakes can both pass a zero-hole check:

- A segmentation model removes light clothing or a feather together with the outside background. The damaged area is connected to the border, so it is not counted as an enclosed hole. Color differences visible in the original RGB can prove that subject detail was erased.
- A true background space between the character, hand and prop is enclosed. Filling every transparent component turns it into an opaque gray patch. Zero holes is not a correctness target.

A pale outline on black may be actual artwork or background mixed into antialiased edge RGB. Compare it with the saved RGB before eroding pixels. Do not indiscriminately delete pale pixels or shrink the entire silhouette.

## Processing requirements

- Estimate the flat background from border pixels. Use color proximity and chroma along with the segmentation model. The legacy `230..241` range is calibration guidance, not permission to erase every pixel in that range.
- Never remove a clearly non-background interior solely because rembg returned low alpha. Preserve pale colored clothing, fine fur, feather tips and held objects.
- Distinguish actual background pockets from subject-colored holes using original RGB and segmentation context. Preserve real gaps between limbs and props. Do not fill all enclosed alpha components.
- Avoid component-area-only deletion: a small disconnected feature can be part of the drawing. Report suspicious speckles rather than deleting all small components indiscriminately.
- Confine alpha refinement and background-color decontamination to the boundary. Keep opaque subject interiors unchanged. A weak segmentation confidence is not by itself an instruction to fade away a real feature.
- Resize with premultiplied alpha and Lanczos. Preserve alpha after resize; do not run unconditional hole filling over the optimized result. Check both PNG and the final WebP used by Rive.
- Apply the same matte policy to source actions and generated returns. After repairing source RGBA frames, restore return endpoints from those repaired source files, verify the exact matches, then optimize.

## Evidence and review

Use saved RGB/RGBA pairs and inspect selected frames on black/dark, white/light and saturated backgrounds. Include thin props, pale clothing and enclosed gaps; inspect adjacent frames or a preview for single-frame dropouts. Check the delivered half-resolution WebP as well as the full-resolution matte.

The Seedance processor writes `generated_matte_validation.json` from generated RGB and RGBA before replacing endpoints. Review the restored endpoints against the source action's RGB and RGBA instead: generated RGB is not an exact reference for a restored source frame. The final `matte_validation.json` checks the delivered twelve-frame sequence; its technical failures stop processing.

Record technical checks separately from visual diagnostics. A nonempty RGBA file, correct dimensions and valid timing are technical requirements. Counts of enclosed transparency, near-background opaque pixels and pixels removed despite RGB differences are diagnostic risks, not semantic ground truth. Canvas-edge alpha can indicate clipping; it is not automatically an unreadable image. Explicit validator thresholds remain available for a user-requested strict workflow.

For these example assets, the regression cases include DELIGHT return 1000/2000 ms frame 3 light clothing, return 3000 ms frame 5 abdomen, return 1000 ms frame 4 feather, and source frames 32/33 enclosed background gaps. These identify observed defects; do not encode character-specific coordinates as universal mask rules.

When a demonstrated defect comes from the processing algorithm, fix or tune that local processing and compare again. Keep prior outputs for comparison. Generation appearance warnings remain advisory, and fixing a matte never authorizes a paid regeneration loop.
