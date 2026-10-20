# Complete-video matting before PNG sampling

Use the original source videos and the saved complete Seedance return videos. Process every decoded frame at its original frame rate into a lossless alpha master, then select PNG frames. Reuse existing generated motion; a matte repair does not require another Seedance request.

The current helpers support **constant-frame-rate (CFR) sources only**. Actual decoded presentation timestamps must follow the declared frame interval; variable-frame-rate (VFR) input or timestamp jumps are technical failures. Do not silently transcode, retime or sample such input into CFR, since that can change motion timing or duration. The verified 24 FPS source and Seedance clips fall within this CFR scope.

The verified local backend is **BiRefNet-matting with Photoroom foreground estimation**. BiRefNet is an image model applied independently to every frame. Processing a complete video does **not** make it a temporal model: it provides no tracking, temporal propagation or flicker guarantee. Review continuous playback as well as individual frames.

## Verified model and runtime

- Official code: [ZhengPeng7/BiRefNet](https://github.com/ZhengPeng7/BiRefNet), commit `97e4f35d74043427b0e9ba935ca10991853a36bc`.
- Official weights: [ZhengPeng7/BiRefNet-matting](https://huggingface.co/ZhengPeng7/BiRefNet-matting), revision `eccde0a8cbdce7ac5fecfeb06340fe7b949e85d9`, file `model.safetensors`.
- Verified runtime: isolated Python 3.12, PyTorch `2.7.1`, torchvision `0.22.1`; Apple Silicon MPS and CPU inference. Keep model files outside the skill repository.
- Standard inference input: **1024 × 1024**. This is the neural network's internal input, not a new output canvas. Restore the alpha to the original video dimensions and estimate foreground colors against the original RGB.
- Use the official `image_proc.refine_foreground` implementation, derived from [Photoroom fast foreground estimation](https://github.com/Photoroom/fast-foreground-estimation). It estimates clean foreground RGB from the original image and alpha to reduce background-colored fringes. It does not redraw or generate the character.

Do not reduce inference size merely to accelerate processing. In the verified samples, a smaller native-aspect model input changed thin feather details. Any different inference size, model revision or backend needs a fresh visual comparison.

### Create an isolated environment

The following commands are for a new environment in the current project. Reuse an already verified environment and model checkout when available. Set `SKILL_ROOT` to the actual installed skill directory; the example is a placeholder.

```bash
export MATTE_ROOT="$PWD/.video-matte"
SKILL_ROOT="/path/to/video-to-rive-frame-animation"
python3.12 -m venv "$MATTE_ROOT/venv"
MATTE_PY="$MATTE_ROOT/venv/bin/python"

"$MATTE_PY" -m pip install \
  torch==2.7.1 torchvision==0.22.1 \
  timm==1.0.15 kornia==0.8.3 einops==0.8.2 \
  huggingface_hub==0.36.2 safetensors==0.8.0 \
  numpy==2.5.4 opencv-python-headless==5.0.0.93 \
  pillow==13.0.0 imageio-ffmpeg==0.6.0 tqdm==4.70.1

git clone https://github.com/ZhengPeng7/BiRefNet.git "$MATTE_ROOT/BiRefNet"
git -C "$MATTE_ROOT/BiRefNet" checkout --detach \
  97e4f35d74043427b0e9ba935ca10991853a36bc

"$MATTE_PY" - <<'PY'
import hashlib
import os
from pathlib import Path
from huggingface_hub import hf_hub_download

root = Path(os.environ["MATTE_ROOT"])
checkpoint = Path(hf_hub_download(
    repo_id="ZhengPeng7/BiRefNet-matting",
    filename="model.safetensors",
    revision="eccde0a8cbdce7ac5fecfeb06340fe7b949e85d9",
    local_dir=root / "models" / "BiRefNet-matting",
))
print("checkpoint:", checkpoint)
print("sha256:", hashlib.sha256(checkpoint.read_bytes()).hexdigest())
PY
```

These are the validated inference dependencies, not the upstream training environment. The inference helper constructs the architecture with `bb_pretrained=False` and loads the supplied local checkpoint strictly; it does not download another backbone. The helper itself never installs packages or fetches model files.

### MPS execution

`matte_video.py` defaults to `--device auto` and `--threads 2`. On MPS it enables `birefnet_mps.py`, a separately checked equivalent implementation of the model's deformable convolutions. The hook patches only the supplied model instances and records its source hash and validation metadata in the manifest.

Random operator tests and three real 1024-input samples were compared with the official operator. The measured maximum full-model alpha difference was below `4e-6`; no sample pixel differed by more than `1/255` before quantization. A few values near rounding boundaries can still differ by one 8-bit level. This is numerical equivalence evidence, not a guarantee for arbitrary future PyTorch versions.

Use `--disable-mps-deform` for an official-operator reference run. CPU and CUDA do not receive this MPS hook. On a changed runtime, run `test_birefnet_mps.py` and repeat representative full-model comparisons before relying on the acceleration.

## Produce a complete alpha master

Choose fresh output directories; the producer refuses to overwrite a nonempty directory.

```bash
SOURCE_VIDEO="/path/to/source.mp4"
SOURCE_RUN="$MATTE_ROOT/source_run"

"$MATTE_PY" "$SKILL_ROOT/scripts/matte_video.py" \
  --video "$SOURCE_VIDEO" \
  --output-dir "$SOURCE_RUN" \
  --model-repo "$MATTE_ROOT/BiRefNet" \
  --checkpoint "$MATTE_ROOT/models/BiRefNet-matting/model.safetensors" \
  --checkpoint-revision eccde0a8cbdce7ac5fecfeb06340fe7b949e85d9 \
  --device auto --inference-size 1024 --threads 2 \
  --save-preview
```

The output contains:

- `transparent_master.mkv`: every original decoded frame, original rational FPS and original dimensions, lossless FFV1/BGRA.
- `matte_manifest.json`: source/checkpoint hashes, code revision, device, model hook, frame counts, alpha checks, per-frame timings and the explicit non-temporal backend policy.
- `preview_dark.mp4`, when requested: an opaque review video composited over a dark background. It is not an alpha asset.

The producer streams frames without holding a whole video in memory. It verifies that processing reaches the complete decoded source count, then decodes the master to check dimensions, FPS, count and meaningful alpha on every frame. A SHA-256 over the complete decoded RGBA pixel stream must match the pixels sent to the lossless encoder. Only then is the completed output directory published.

To batch clips, repeat `--video`; the CLI loads one model and creates numbered per-clip subdirectories. For programmatic batches, instantiate `BiRefNetMattingBackend` once and reuse it with `produce_video`. Do not load a new model for each frame.

**Do not run the old gray thresholds, rembg mask union, blanket hole filling, silhouette shrinking or component-area deletion over these model outputs.** Preserve the model alpha and its provenance. When a visible defect remains, compare the source RGB, model alpha and estimated foreground RGB before changing the processing.

## Source actions: sample at 12 FPS

```bash
"$MATTE_PY" "$SKILL_ROOT/scripts/extract_alpha_video_frames.py" \
  --video "$SOURCE_RUN/transparent_master.mkv" \
  --source-video "$SOURCE_VIDEO" \
  --mode source --target-fps 12 \
  --output "$SOURCE_RUN/frames_rgba" \
  --rgb-output "$SOURCE_RUN/frames_rgb"

"$MATTE_PY" "$SKILL_ROOT/scripts/prepare_frame_assets.py" \
  --source "$SOURCE_RUN/frames_rgba" \
  --png-output "$SOURCE_RUN/frames_50_png" \
  --webp-output "$SOURCE_RUN/frames_50_webp" \
  --scale 0.5
```

At 24 FPS, source sampling retains decoded indices `0, 2, 4, ...` and always includes `N-1`. The extraction manifest preserves the original decoded index and timestamp for every output frame. The optional RGB audit samples come from the original video at the same indices.

Sampling does not crop, scale, pad or autorotate the image. Size reduction happens only in the subsequent premultiplied-alpha asset preparation step.

## Seedance returns: sample twelve poses over the full four seconds

First run `matte_video.py` on the **complete saved four-second generated video**, using the same model, checkpoint and inference settings as the source action. Do not feed it a trimmed one-second segment.

The standalone extraction command is:

```bash
RETURN_VIDEO="/path/to/return/generated_full.mp4"
RETURN_RUN="$MATTE_ROOT/return_run"

"$MATTE_PY" "$SKILL_ROOT/scripts/matte_video.py" \
  --video "$RETURN_VIDEO" \
  --output-dir "$RETURN_RUN" \
  --model-repo "$MATTE_ROOT/BiRefNet" \
  --checkpoint "$MATTE_ROOT/models/BiRefNet-matting/model.safetensors" \
  --checkpoint-revision eccde0a8cbdce7ac5fecfeb06340fe7b949e85d9 \
  --device auto --inference-size 1024 --threads 2 --save-preview

"$MATTE_PY" "$SKILL_ROOT/scripts/extract_alpha_video_frames.py" \
  --video "$RETURN_RUN/transparent_master.mkv" \
  --source-video "$RETURN_VIDEO" \
  --mode return --target-fps 12 \
  --output "$RETURN_RUN/sampled_native_rgba" \
  --rgb-output "$RETURN_RUN/sampled_native_rgb"
```

Return mode requires four seconds within one decoded-frame tolerance, at least twelve decoded frames, and exactly twelve distinct samples:

```text
index(i) = round(i × (N − 1) / 11), i = 0...11
```

This includes the first and final decoded frame. The twelve output frames play at **12 FPS for one second**. The alpha master retains the generated video's original dimensions and FPS.

For the complete Rive return asset workflow, pass the alpha master to the return processor:

```bash
CUT_RGBA="/path/to/repaired/source/frames_rgba/frame_0012.png"
ZERO_RGBA="/path/to/repaired/source/frames_rgba/frame_0000.png"

"$MATTE_PY" "$SKILL_ROOT/scripts/process_seedance_return_frames.py" \
  --video "$RETURN_VIDEO" \
  --alpha-video "$RETURN_RUN/transparent_master.mkv" \
  --first-frame "$CUT_RGBA" --last-frame "$ZERO_RGBA" \
  --output-dir "$RETURN_RUN/processed"
```

That processor imports model-generated alpha without another matte pass. It retains native audit frames, applies the existing permitted source-aspect normalization only to derived frames, restores exact repaired source RGBA endpoints, and prepares the half-size PNG/WebP assets. Preserve the native master even when a provider's one-pixel dimension rounding requires padding during that later normalization.

## Validation and review

Run the technical validator on source frames and their matching RGB:

```bash
"$MATTE_PY" "$SKILL_ROOT/scripts/validate_matte.py" \
  --frames-dir "$SOURCE_RUN/frames_rgba" \
  --rgb-dir "$SOURCE_RUN/frames_rgb" \
  --report "$SOURCE_RUN/matte_validation.json" \
  --contact-sheet "$SOURCE_RUN/matte_contact_sheet.jpg"
```

Both alpha import and the producer reject fully opaque frames, empty foreground, changed dimensions, changed rational FPS, or a changed decoded count. A black-background MP4 does not pass as transparent. Transparent WebM is decoded with `libvpx-vp9` because a native VP9 decoder can silently discard its alpha stream.

Technical validity does not establish semantic mask quality. Inspect the complete preview and adjacent frames around difficult poses. Compare matching RGB/RGBA on black or dark, white or light, and saturated backgrounds. Include light clothing, fur and feather tips, held objects, gaps between limbs, motion blur and frames that previously lost subject detail. Inspect the final half-size WebP used by Rive as well as the full-size PNG.

Review generated return RGB against generated RGBA before endpoint restoration. Restored endpoints must instead be compared with their source action RGB/RGBA. Keep exact endpoint hash checks, sample-index mapping and the complete model/alpha audit. Residual edge artifacts or frame-to-frame changes should be recorded honestly; neither a modern model nor a passing alpha check proves a perfect matte.

## Optional external video matting

[Bria's video background-removal API](https://docs.bria.ai/video-editing/editing/remove-background) is an optional provider of complete alpha videos. Use an already connected account or valid token; without credentials, continue the local workflow rather than calling the service or creating an account. Check the current provider terms and actual account quote before a paid run; do not hard-code prices in this skill.

Request `background_color=Transparent`, `auto_zoom=false`, and an alpha-capable output such as `webm_vp9` or `mov_proresks`. Import that result through `extract_alpha_video_frames.py` with the original pre-matting video. Reject any output that trims, changes FPS/count, crops, resizes or falls back to an opaque background. Provider acceptance of a clip does not prove quality for cartoon characters; test the actual difficult poses and continuous playback before adopting that backend.
