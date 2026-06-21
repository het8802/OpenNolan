"""BYOK env-file config — the curated variable menu + safe read/write of the repo `.env`.

The desktop app is bring-your-own-key: keys live in a plain `.env` at the repo root (the same
file `lib.env_loader` loads). The BYOK panel reads this file, shows every known variable (plus any
extra ones already present), and writes the user's edits back — preserving comments/structure via
python-dotenv's `set_key`. Values are returned as-is (this is a local, single-user app); the UI
masks secrets by default. All write paths validate variable names and reject newline injection.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from dotenv import dotenv_values, load_dotenv, set_key

# The repo-root .env — the exact file lib.env_loader.load_env() reads at startup.
ENV_PATH = Path(__file__).resolve().parent.parent / ".env"

_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# Values made only of these chars are safe to write UNQUOTED (matches the existing .env style and
# covers every API key/token/URL). Anything else (spaces, #, quotes, $, …) gets quoted so it can't
# leak into a comment or break parsing on read. Empty matches too → written as `KEY=`.
_UNQUOTED_RE = re.compile(r"^[A-Za-z0-9_.:/+=~@-]*$")

# Curated BYOK menu — the variables the app knows how to use. Display order = list order;
# `group` buckets them in the panel and `secret` drives the masked (password) input. Anything in
# the user's .env that isn't listed here is still shown (appended under "Other").
KNOWN_ENV_VARS: list[dict] = [
    {"key": "CLAUDE_CODE_OAUTH_TOKEN", "label": "Claude Code token", "group": "Agent (Claude)", "secret": True,
     "description": "OAuth token from `claude setup-token` — powers the editing agent."},
    {"key": "ANTHROPIC_API_KEY", "label": "Anthropic API key", "group": "Agent (Claude)", "secret": True,
     "description": "Alternative to the Claude Code token for BYOK agent auth."},
    {"key": "OPENAI_API_KEY", "label": "OpenAI", "group": "AI generation", "secret": True,
     "description": "OpenAI API key."},
    {"key": "GOOGLE_API_KEY", "label": "Google (Gemini / Veo)", "group": "AI generation", "secret": True,
     "description": "Google AI / Gemini key."},
    {"key": "XAI_API_KEY", "label": "xAI (Grok)", "group": "AI generation", "secret": True,
     "description": "xAI API key."},
    {"key": "FAL_KEY", "label": "fal.ai", "group": "AI generation", "secret": True,
     "description": "fal.ai key for image/video models."},
    {"key": "REPLICATE_API_TOKEN", "label": "Replicate", "group": "AI generation", "secret": True,
     "description": "Replicate API token (community models, Content Signal scorer)."},
    {"key": "RUNWAY_API_KEY", "label": "Runway", "group": "AI generation", "secret": True,
     "description": "Runway video generation."},
    {"key": "HF_TOKEN", "label": "Hugging Face", "group": "AI generation", "secret": True,
     "description": "Hugging Face token (Spaces, hosted models)."},
    {"key": "ELEVENLABS_API_KEY", "label": "ElevenLabs", "group": "Audio / voice", "secret": True,
     "description": "ElevenLabs TTS, sound effects, and music."},
    {"key": "HEYGEN_API_KEY", "label": "HeyGen", "group": "Audio / voice", "secret": True,
     "description": "HeyGen avatar video + TTS."},
    {"key": "SUNO_API_KEY", "label": "Suno", "group": "Audio / voice", "secret": True,
     "description": "Suno music generation."},
    {"key": "DOUBAO_SPEECH_API_KEY", "label": "Doubao speech", "group": "Audio / voice", "secret": True,
     "description": "Doubao (Volcengine) speech key."},
    {"key": "DOUBAO_SPEECH_VOICE_TYPE", "label": "Doubao voice type", "group": "Audio / voice", "secret": False,
     "description": "Default Doubao voice id (not a secret)."},
    {"key": "PEXELS_API_KEY", "label": "Pexels", "group": "Stock media", "secret": True,
     "description": "Pexels stock photos and video."},
    {"key": "PIXABAY_API_KEY", "label": "Pixabay", "group": "Stock media", "secret": True,
     "description": "Pixabay stock media."},
    {"key": "UNSPLASH_ACCESS_KEY", "label": "Unsplash", "group": "Stock media", "secret": True,
     "description": "Unsplash photos."},
    {"key": "YOUTUBE_API_KEY", "label": "YouTube Data API", "group": "Stock media", "secret": True,
     "description": "YouTube Data API key (reference clips)."},
    {"key": "FIRECRAWL_API_KEY", "label": "Firecrawl", "group": "Web", "secret": True,
     "description": "Firecrawl web scrape / screenshots."},
    {"key": "VIDEO_GEN_LOCAL_ENABLED", "label": "Local video gen", "group": "Local / advanced", "secret": False,
     "description": "Enable local video generation (true / false)."},
    {"key": "VIDEO_GEN_LOCAL_MODEL", "label": "Local video model", "group": "Local / advanced", "secret": False,
     "description": "Local video model name."},
    {"key": "MODAL_LTX2_ENDPOINT_URL", "label": "Modal LTX2 endpoint", "group": "Local / advanced", "secret": False,
     "description": "Modal LTX2 endpoint URL."},
]

_KNOWN_BY_KEY = {v["key"]: v for v in KNOWN_ENV_VARS}


def _looks_secret(key: str) -> bool:
    k = key.upper()
    return any(tok in k for tok in ("KEY", "TOKEN", "SECRET", "PASSWORD"))


def read_env_file(path: Path = ENV_PATH) -> dict[str, str]:
    """Parsed {KEY: value} from the .env (handles quotes/comments via python-dotenv). {} if absent."""
    if not path.exists():
        return {}
    return {k: (v or "") for k, v in dotenv_values(path).items() if k}


def list_env_vars(path: Path = ENV_PATH) -> list[dict]:
    """The curated menu with each var's current value, plus any extra keys present in the file."""
    current = read_env_file(path)
    out: list[dict] = [{**spec, "value": current.get(spec["key"], "")} for spec in KNOWN_ENV_VARS]
    for key, val in current.items():
        if key in _KNOWN_BY_KEY:
            continue
        out.append({"key": key, "label": key, "group": "Other", "secret": _looks_secret(key),
                    "description": "", "value": val})
    return out


def write_env_vars(updates: dict[str, Optional[str]], path: Path = ENV_PATH) -> list[str]:
    """Persist edits to the .env (preserving the rest). Returns the keys that actually changed.

    Skips no-ops and never adds a blank line for a key the user left empty + never had. Validates
    names and rejects newline injection so the file can't be corrupted.
    """
    for key, val in updates.items():
        if not _KEY_RE.match(key):
            raise ValueError(f"Invalid variable name: {key!r}")
        if val is not None and ("\n" in val or "\r" in val):
            raise ValueError(f"Value for {key} may not contain newlines")

    current = read_env_file(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.touch()

    changed: list[str] = []
    for key, raw in updates.items():
        val = "" if raw is None else raw
        if key not in current and val == "":
            continue  # don't clutter the file with blanks for keys the user never set
        if current.get(key, None) == val:
            continue  # unchanged
        # Keep simple values unquoted (clean, matches the file); quote only when needed.
        quote_mode = "never" if _UNQUOTED_RE.match(val) else "always"
        set_key(str(path), key, val, quote_mode=quote_mode)
        changed.append(key)
    return changed


def reload_env(path: Path = ENV_PATH) -> None:
    """Re-load the .env into os.environ WITH override, so saved keys take effect this session
    (the next agent turn / tool subprocess inherits them) without a restart."""
    if path.exists():
        load_dotenv(path, override=True)
