# FFmpeg Tools — What the Agent Has and How It Finds Them

This document answers four things about the FFmpeg-based tools in OpenNolan:

1. **What FFmpeg tools the agent has**
2. **What each one can do**
3. **Which file each is stored in**
4. **How the agent discovers them** (the registry mechanism, in the codebase)

---

## 1. How the agent discovers FFmpeg tools

There is **no hardcoded list** of FFmpeg tools anywhere. The agent learns about them at runtime through the tool registry. Here is the exact chain in the codebase.

### Step 1 — The agent runs `registry.discover()`

The registry is a singleton defined in [`tools/tool_registry.py`](tools/tool_registry.py) (`registry = ToolRegistry()` at the bottom of the file). The agent runs, at preflight:

```python
from tools.tool_registry import registry
registry.discover()
```

### Step 2 — `discover()` walks the whole `tools/` package

`ToolRegistry.discover()` ([tools/tool_registry.py:105](tools/tool_registry.py)) does:

1. Loads `.env` (so API-key-based tools can report availability).
2. `importlib.import_module("tools")`.
3. Uses `pkgutil.walk_packages` to import **every module under `tools/`** (skipping `base_tool` and `tool_registry`).
4. For each module, calls `register_module()`.

### Step 3 — `register_module()` auto-registers any `BaseTool` subclass

`register_module()` ([tools/tool_registry.py:73](tools/tool_registry.py)) uses `inspect.getmembers` to find every **concrete** (non-abstract) subclass of `BaseTool` *defined in that module*, instantiates it (`cls()`), and stores it keyed by `tool.name`.

> **This is the key insight:** dropping a new `BaseTool` subclass into any file under `tools/` makes it discoverable automatically. No registration call, no central list. An "FFmpeg tool" is simply a `BaseTool` subclass that declares `provider = "ffmpeg"` and depends on the `ffmpeg` binary.

### Step 4 — The agent queries the registry by provider / capability

Every tool subclasses `BaseTool` ([tools/base_tool.py](tools/base_tool.py)), which exposes identity fields the registry groups on. The agent then calls one of:

| Query | Method | What it returns |
|-------|--------|-----------------|
| All tools grouped **by provider** (`ffmpeg`, `elevenlabs`, …) | `registry.provider_catalog()` | FFmpeg tools all land under the `"ffmpeg"` key |
| All tools grouped **by capability** (`video_post`, `audio_processing`, …) | `registry.capability_catalog()` | FFmpeg tools spread across their capability families |
| Preflight capability menu | `registry.provider_menu()` / `registry.provider_menu_summary()` | "N of M configured" rollup |
| Full per-tool contract dump | `registry.support_envelope()` | Everything, including file paths |
| Tools for one capability | `registry.get_by_capability("video_post")` | Used by the selector tools |

The commands the agent actually runs (from [`AGENT_GUIDE.md`](AGENT_GUIDE.md) → Mandatory Preflight):

```bash
# Group by provider — ffmpeg tools cluster under the "ffmpeg" key
python -c "from tools.tool_registry import registry; import json; registry.discover(); print(json.dumps(registry.provider_catalog(), indent=2))"

# Group by capability
python -c "from tools.tool_registry import registry; import json; registry.discover(); print(json.dumps(registry.capability_catalog(), indent=2))"
```

### Step 5 — How the agent learns the **file** and the **availability**

For each tool, `BaseTool.get_info()` ([tools/base_tool.py:226](tools/base_tool.py)) reports:

- `usage_location` → the source file, computed live via `inspect.getfile(self.__class__)`. **This is how the agent knows which file a tool lives in.**
- `status` → `available` / `unavailable`, computed by `get_status()` → `check_dependencies()`.

`check_dependencies()` ([tools/base_tool.py:202](tools/base_tool.py)) reads each tool's `dependencies` list. For an FFmpeg tool the dependency is `"cmd:ffmpeg"`, which is checked with `shutil.which("ffmpeg")`. **So an FFmpeg tool reports `available` only when the `ffmpeg` binary is on `PATH`.** Since ffmpeg is treated as always-present locally, these tools are the always-available local backbone of composition.

```
registry.discover()
   └─ pkgutil.walk_packages("tools")        # import every module
        └─ register_module()                 # find concrete BaseTool subclasses
             └─ registry._tools[tool.name] = tool
   ↓
provider_catalog()  →  group by tool.provider  →  "ffmpeg": [ ...20 tools... ]
   ↓ per tool
get_info() → usage_location (file)  +  status (shutil.which("ffmpeg"))
```

---

## 2. The FFmpeg tools (`provider = "ffmpeg"`)

These **20 tools** declare `provider = "ffmpeg"` and run FFmpeg directly on-device (`runtime = LOCAL`, free, no API key). Grouped by capability, exactly as `capability_catalog()` returns them.

### `video_post` — composition, cutting, layout (10 tools)

| Tool (`name`) | File | What it can do |
|---------------|------|----------------|
| `video_compose` | [tools/video/video_compose.py](tools/video/video_compose.py) | **Main composition orchestrator.** Takes `edit_decisions` + `asset_manifest` + audio and renders. Routes between **FFmpeg / Remotion / HyperFrames** by `edit_decisions.render_runtime`. FFmpeg path: cuts, concat, per-cut xfade transitions, keyframed/text overlays, PiP, crop, custom canvas, subtitle burn. |
| `video_trimmer` | [tools/video/video_trimmer.py](tools/video/video_trimmer.py) | Cut, trim, speed adjustment, and concatenation of segments. Deterministic, near-lossless by default. |
| `video_stitch` | [tools/video/video_stitch.py](tools/video/video_stitch.py) | Multi-clip assembly with validation, crossfade/fade transitions, and spatial layouts (side-by-side, vertical stack, PiP) for duet/stitch-style content. |
| `auto_reframe` | [tools/video/auto_reframe.py](tools/video/auto_reframe.py) | Aspect-ratio conversion (e.g. 16:9 → 9:16) that keeps the speaker's face centered, using `face_tracker` data → smoothed crop. No GPU. |
| `motion_ops` | [tools/video/motion_ops.py](tools/video/motion_ops.py) | Timeline tricks baked into new clips: `freeze`, `reverse`, `speed` (0.5×–4×), `segment_volume`, plus pan-zoom / fx / flip. Re-probes duration after the transform. |
| `mask_ops` | [tools/video/mask_ops.py](tools/video/mask_ops.py) | Region masks baked into new clips: `blur_region`, `dim_outside` (spotlight), image-mask, and masked reveal. |
| `silence_cutter` | [tools/video/silence_cutter.py](tools/video/silence_cutter.py) | Auto jump-cuts via FFmpeg `silencedetect`. Modes: `remove`, `speed_up`, `mark`. |
| `showcase_card` | [tools/video/showcase_card.py](tools/video/showcase_card.py) | Builds a 9:16 presentation card: letterboxed content + bold title + subtitle + dark background. For Reels/TikTok showcase segments. |
| `green_screen_composite` | [tools/video/green_screen_composite.py](tools/video/green_screen_composite.py) | Composites a keyed speaker over a background video with layout presets (news anchor, full behind, PiP, split). PIL/numpy alpha + FFmpeg mux. |
| `green_screen_processor` | [tools/video/green_screen_processor.py](tools/video/green_screen_processor.py) | Removes green/blue screen via FFmpeg `chromakey` or rembg AI segmentation. `auto` mode picks the method by histogram analysis. |

### `enhancement` — look / face polish (2 tools)

| Tool | File | What it can do |
|------|------|----------------|
| `color_grade` | [tools/enhancement/color_grade.py](tools/enhancement/color_grade.py) | LUT + filter-chain grading (Edits parity: Adjustments, Curves, saved Looks). Preset profiles, external `.cube` LUTs, parametric brightness/contrast/saturation/gamma/temperature/tint/sharpness/vignette. |
| `face_enhance` | [tools/enhancement/face_enhance.py](tools/enhancement/face_enhance.py) | Skin smoothing, sharpening, lighting-correction presets for talking-head footage. Pure FFmpeg filter chains, no GPU/models. |

### `analysis` — inspection / measurement (4 tools)

| Tool | File | What it can do |
|------|------|----------------|
| `frame_sampler` | [tools/analysis/frame_sampler.py](tools/analysis/frame_sampler.py) | Extracts representative frames (interval / count / timestamp strategies) for AI analysis, thumbnails, QA. |
| `audio_energy` | [tools/analysis/audio_energy.py](tools/analysis/audio_energy.py) | Uses FFmpeg `ebur128` loudness to profile energy at 100 ms intervals; recommends a playback offset (skip quiet intros, find peak section, detect looping need). |
| `visual_qa` | [tools/analysis/visual_qa.py](tools/analysis/visual_qa.py) | Automated quality checks: resolution/duration/codec validation, frame extraction, caption-occlusion check, transition verification. |
| `scene_detect` | [tools/analysis/scene_detect.py](tools/analysis/scene_detect.py) | Scene/shot boundary detection via PySceneDetect, with an FFmpeg-based fallback if PySceneDetect isn't installed. |

### `audio_processing` — mix / clean / voice (3 tools)

| Tool | File | What it can do |
|------|------|----------------|
| `audio_mixer` | [tools/audio/audio_mixer.py](tools/audio/audio_mixer.py) | Mixes speech/music/SFX with ducking, fades, volume normalization, loudness auto-balancing, audio extraction. FFmpeg + optional pydub. |
| `audio_enhance` | [tools/audio/audio_enhance.py](tools/audio/audio_enhance.py) | Noise reduction, normalization, EQ presets, standalone de-esser. **HYBRID**: local FFmpeg modes *or* the ElevenLabs `ai_isolate` API for ML voice isolation. |
| `voice_ops` | [tools/audio/voice_ops.py](tools/audio/voice_ops.py) | Voiceover workflow: `list_devices`, `record` (mic capture), `effect` (helium/deep/robot/alien/echo/telephone/whisper or custom pitch shift), `insert` (place a take onto the timeline over ducked music). All local. |

### `screen_capture` (1 tool)

| Tool | File | What it can do |
|------|------|----------------|
| `screen_recorder` | [tools/capture/screen_recorder.py](tools/capture/screen_recorder.py) | Cross-platform screen + optional audio capture via FFmpeg native devices (gdigrab/dshow on Windows, avfoundation on macOS, x11grab/pulse on Linux). |

---

## 3. Tools that *depend on* FFmpeg but report a different provider

These are **not** FFmpeg tools by `provider`, but they shell out to FFmpeg (declare `cmd:ffmpeg`) for frame extraction/encoding/muxing while routing their core work to another engine or model. Listed for completeness — they will **not** show up under the `"ffmpeg"` key in `provider_catalog()`.

| Tool | File | Provider | FFmpeg's role here |
|------|------|----------|--------------------|
| `remotion_caption_burn` | [tools/video/remotion_caption_burn.py](tools/video/remotion_caption_burn.py) | `remotion` | FFmpeg subtitle-burn fallback if Remotion path fails |
| `hyperframes_compose` | [tools/video/hyperframes_compose.py](tools/video/hyperframes_compose.py) | `hyperframes` | FFmpeg used inside the HTML/CSS/GSAP render path |
| `fuse_transition` | [tools/video/fuse_transition.py](tools/video/fuse_transition.py) | `seedance` | FFmpeg extracts first/last frames; Seedance 2.0 makes the morph |
| `object_cutout` | [tools/enhancement/object_cutout.py](tools/enhancement/object_cutout.py) | `meta-sam2` | FFmpeg frame I/O around SAM 2 video segmentation |
| `upscale` | [tools/enhancement/upscale.py](tools/enhancement/upscale.py) | `realesrgan` | FFmpeg splits/reassembles frames around Real-ESRGAN |
| `eye_enhance` | [tools/enhancement/eye_enhance.py](tools/enhancement/eye_enhance.py) | `mediapipe` | FFmpeg frame I/O around MediaPipe eye work |
| `face_tracker` | [tools/analysis/face_tracker.py](tools/analysis/face_tracker.py) | `mediapipe` | FFmpeg frame extraction for face tracking |
| `video_analyzer` | [tools/analysis/video_analyzer.py](tools/analysis/video_analyzer.py) | `multi` | FFmpeg + yt-dlp for local reference-video analysis |
| `lip_sync` | [tools/avatar/lip_sync.py](tools/avatar/lip_sync.py) | `wav2lip` | FFmpeg frame I/O around Wav2Lip/MuseTalk |

---

## 4. Quick reference — see it yourself

```bash
# Every tool whose provider is ffmpeg (the 20 above):
python -c "from tools.tool_registry import registry; registry.discover(); print('\n'.join(sorted(t.name for t in registry.get_by_provider('ffmpeg'))))"

# Full contract (file path = usage_location, plus status) for one tool:
python -c "from tools.tool_registry import registry; import json; registry.discover(); print(json.dumps(registry.get('video_compose').get_info(), indent=2, default=str))"
```

---

## Notes / gotchas

- **Dependency-string inconsistency.** Most FFmpeg tools declare `dependencies = ["cmd:ffmpeg"]`, which `check_dependencies()` verifies with `shutil.which`. But `screen_recorder` and `audio_energy` declare `dependencies = ["binary:ffmpeg"]`. The `binary:` prefix is **not** handled by `check_dependencies()` ([tools/base_tool.py:202](tools/base_tool.py) only handles `cmd:`, `env:`, `python:`), so those two will report `available` even on a machine with no ffmpeg installed. The canonical form is `cmd:ffmpeg`.
- **FFmpeg is the always-on local backbone.** Because the binary is treated as universally present, these tools form the floor of capability — `video_compose`'s FFmpeg path is the fallback render runtime when Remotion/HyperFrames aren't installed (see the runtime table in [`AGENT_GUIDE.md`](AGENT_GUIDE.md)).
- **The registry is the source of truth.** Per `AGENT_GUIDE.md`, the agent must not maintain hardcoded tool lists — always query the registry at runtime. This document is a snapshot for human reference, generated from the code on the `feat/edits-parity-tools` branch.
