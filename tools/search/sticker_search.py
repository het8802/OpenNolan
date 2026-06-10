"""Sticker & GIF search via GIPHY / Tenor (Edits parity: stickers & GIFs).

One tool, two providers — uses whichever API key is configured:
  - GIPHY  (env:GIPHY_API_KEY)  api.giphy.com/v1/gifs/search + /v1/stickers/search
    (the stickers endpoint returns transparent assets — preferred for kind=sticker)
  - Tenor  (env:TENOR_API_KEY)  tenor.googleapis.com/v2/search
    (kind=sticker adds searchfilter=sticker; transparent renditions come back as
    gif_transparent / webp_transparent media_formats)
When both keys are set, GIPHY wins by default (its stickers endpoint is the best
source of transparent assets); `provider` forces a specific one.

Capability is "sticker_search", NOT "web_image_search": web_image_search_selector
auto-discovers that capability and would (a) route generic photo queries here via
the scoring engine and (b) drop this tool's kind/download_dir params in
_adapt_inputs. GIF/sticker results are a different contract, so this stays out of
that pool. duckduckgo_image_search (type_image="gif") remains the free fallback.

Rendition preference (_pick_rendition, pure):
  - kind=gif:     mp4 > webm > gif > webp.  .gif as a video_compose overlay has
    path quirks: palette transparency is 1-bit (hard fringed edges), looping needs
    explicit -ignore_loop/-stream_loop handling, and GIF's variable frame delays
    drift against a CFR timeline — the same class of problem as the still-image
    overlay loop fix (keyframe opacity fades don't render without looping). The
    MP4/WebM renditions both providers ship avoid all of this, so they win.
  - kind=sticker: gif > webp > webm > mp4.  Transparency is the whole point of a
    sticker and H.264 MP4 cannot carry alpha — GIPHY/Tenor mp4 renditions of
    stickers are flattened onto a solid background. Only the gif (and *_transparent
    webp) renditions are guaranteed-alpha from these APIs (their webm is usually
    flattened VP8/VP9 too), so the guaranteed-alpha GIF wins; convert gif->alpha
    WebM/MOV locally if edge quality matters for the composite.
The original gif URL is always recorded per result (data.results[].gif_url) so the
caller can grab it regardless of which rendition was downloaded.

Attribution (API terms, not optional): GIPHY requires "Powered By GIPHY"
attribution marks anywhere its content is shown; Tenor requires "Via Tenor" /
"Powered by Tenor". Each result carries an `attribution` string + source page URL.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Optional

from tools.base_tool import (
    BaseTool,
    Determinism,
    ExecutionMode,
    ResourceProfile,
    RetryPolicy,
    ToolResult,
    ToolRuntime,
    ToolStability,
    ToolStatus,
    ToolTier,
)

GIPHY_GIF_ENDPOINT = "https://api.giphy.com/v1/gifs/search"
GIPHY_STICKER_ENDPOINT = "https://api.giphy.com/v1/stickers/search"
TENOR_ENDPOINT = "https://tenor.googleapis.com/v2/search"

# GIPHY rating -> Tenor contentfilter
_TENOR_CONTENTFILTER = {"g": "high", "pg": "medium", "pg-13": "low", "r": "off"}


class StickerSearch(BaseTool):
    name = "sticker_search"
    version = "0.1.0"
    tier = ToolTier.SOURCE
    capability = "sticker_search"
    provider = "giphy_tenor"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.STOCHASTIC
    runtime = ToolRuntime.API

    # Either key is enough — get_status() checks them as an OR, so neither is a
    # hard "env:" dependency (check_dependencies would require both).
    dependencies = ["python:requests"]
    install_instructions = (
        "Set GIPHY_API_KEY (free key: https://developers.giphy.com) "
        "or TENOR_API_KEY (free key: https://developers.google.com/tenor) in .env"
    )
    agent_skills = []

    KINDS = ("sticker", "gif")
    RATINGS = ("g", "pg", "pg-13", "r")
    PROVIDERS = ("auto", "giphy", "tenor")
    LIMIT_MAX = 25
    RENDITION_ORDER = {
        "gif": ("mp4", "webm", "gif", "webp"),
        "sticker": ("gif", "webp", "webm", "mp4"),
    }

    capabilities = ["search_stickers", "search_gifs", "find_reaction_gifs", "find_transparent_stickers"]
    supports = {
        "transparent_stickers": True,
        "reaction_gifs": True,
        "mp4_renditions": True,
        "download_results": True,
        "asset_manifest_registration": True,
    }
    best_for = [
        "reaction GIFs and meme inserts for short-form edits (Edits/CapCut-style sticker layer)",
        "transparent sticker overlays (GIPHY stickers endpoint / Tenor searchfilter=sticker)",
        "MP4/WebM GIF renditions that composite cleanly as video_compose overlays[]",
    ]
    not_good_for = [
        "publishing without attribution — GIPHY API terms require 'Powered By GIPHY' "
        "marks and Tenor requires 'Via Tenor' wherever results are shown",
        "alpha video renditions — H.264 MP4 cannot carry alpha; kind=sticker downloads "
        "the guaranteed-alpha GIF/WebP rendition instead (convert locally if needed)",
        "HDR sources — output is 8-bit SDR; detect with is_hdr_source() and handle HDR "
        "per AGENT_GUIDE before using this tool",
        "high-volume automated use on default keys (GIPHY beta keys and Tenor free keys "
        "are tightly rate-limited)",
    ]
    fallback_tools = ["duckduckgo_image_search"]
    provider_matrix = {
        "giphy": {
            "env": "GIPHY_API_KEY",
            "endpoints": [GIPHY_GIF_ENDPOINT, GIPHY_STICKER_ENDPOINT],
            "transparent_stickers": "stickers endpoint (gif/webp renditions carry alpha)",
        },
        "tenor": {
            "env": "TENOR_API_KEY",
            "endpoints": [TENOR_ENDPOINT],
            "transparent_stickers": "searchfilter=sticker -> gif_transparent/webp_transparent",
        },
    }

    input_schema = {
        "type": "object",
        "required": ["query"],
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query. Be specific: 'thumbs up sticker', 'mind blown reaction'.",
            },
            "kind": {
                "type": "string",
                "enum": list(KINDS),
                "default": "sticker",
                "description": "sticker = transparent overlay assets; gif = full-frame reaction GIFs.",
            },
            "limit": {
                "type": "integer",
                "default": 5,
                "minimum": 1,
                "maximum": LIMIT_MAX,
                "description": "Number of results to return (and download when download_dir is set).",
            },
            "download_dir": {
                "type": "string",
                "description": "If set, download every returned result here. Omit for URL-only search.",
            },
            "provider": {
                "type": "string",
                "enum": list(PROVIDERS),
                "default": "auto",
                "description": "auto = GIPHY if GIPHY_API_KEY is set, else Tenor.",
            },
            "rating": {
                "type": "string",
                "enum": list(RATINGS),
                "default": "pg-13",
                "description": "Content rating (GIPHY rating param; mapped to Tenor contentfilter).",
            },
            "asset_manifest_path": {
                "type": "string",
                "description": "Optional: append downloaded assets to this asset_manifest (validated, written).",
            },
            "scene_id": {
                "type": "string",
                "default": "overlay",
                "description": "scene_id for registered assets.",
            },
        },
    }

    output_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "kind": {"type": "string"},
            "provider": {"type": "string"},
            "attribution_required": {"type": "string"},
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "title": {"type": "string"},
                        "path": {"type": ["string", "null"]},
                        "url": {"type": "string", "description": "Chosen rendition URL"},
                        "rendition": {"type": "string", "enum": ["mp4", "webm", "gif", "webp"]},
                        "gif_url": {"type": ["string", "null"], "description": "Always recorded"},
                        "width": {"type": "integer"},
                        "height": {"type": "integer"},
                        "preview_url": {"type": ["string", "null"]},
                        "source": {"type": "string", "description": "GIPHY/Tenor page URL"},
                        "attribution": {"type": "string"},
                    },
                },
            },
            "manifest_entries": {
                "type": "array",
                "description": "asset_manifest-ready entries for the downloaded files",
            },
            "total_returned": {"type": "integer"},
            "downloaded_paths": {"type": "array", "items": {"type": "string"}},
        },
    }

    resource_profile = ResourceProfile(cpu_cores=1, ram_mb=128, vram_mb=0, disk_mb=200, network_required=True)
    retry_policy = RetryPolicy(max_retries=2, retryable_errors=["rate_limit", "timeout"])
    idempotency_key_fields = ["query", "kind", "limit", "provider"]
    side_effects = [
        "calls GIPHY/Tenor search API",
        "optionally downloads sticker/GIF files to download_dir",
        "may append to an asset_manifest",
    ]
    user_visible_verification = [
        "Open downloaded stickers on a colored background; confirm transparency survived",
        "Confirm 'Powered By GIPHY' / 'Via Tenor' attribution is included before publishing",
    ]

    def get_status(self) -> ToolStatus:
        try:
            import requests  # noqa: F401
        except ImportError:
            return ToolStatus.UNAVAILABLE
        if os.environ.get("GIPHY_API_KEY") or os.environ.get("TENOR_API_KEY"):
            return ToolStatus.AVAILABLE
        return ToolStatus.UNAVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0  # both APIs are free-tier

    # ---- execution ----

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        start = time.time()
        try:
            query, kind, limit, rating = self._validate(inputs)
            provider, api_key = self._resolve_provider(inputs.get("provider", "auto"))
        except _SearchInputError as e:
            return ToolResult(success=False, error=str(e))

        try:
            if provider == "giphy":
                raw = self._search_giphy(query, kind, limit, rating, api_key)
            else:
                raw = self._search_tenor(query, kind, limit, rating, api_key)
        except Exception as e:
            return ToolResult(success=False, error=f"{provider} {kind} search failed: {e}")

        results = []
        for item in raw:
            normalized = (
                self._normalize_giphy(item, kind) if provider == "giphy"
                else self._normalize_tenor(item, kind)
            )
            picked = self._pick_rendition(normalized.pop("renditions"), kind)
            if picked is None:
                continue  # no usable rendition on this result
            normalized["url"], normalized["rendition"] = picked
            normalized["path"] = None
            results.append(normalized)

        if not results:
            return ToolResult(
                success=False,
                error=f"No {kind}s found on {provider} for query: '{query}'. Try a broader query.",
            )

        downloaded_paths: list[str] = []
        manifest_entries: list[dict[str, Any]] = []
        download_dir = inputs.get("download_dir")
        if download_dir:
            out = Path(download_dir)
            out.mkdir(parents=True, exist_ok=True)
            safe_query = "".join(c if c.isalnum() else "_" for c in query[:30])
            for i, result in enumerate(results):
                dest = out / f"{provider}_{kind}_{safe_query}_{i:02d}.{result['rendition']}"
                err = self._download(result["url"], dest)
                if err:
                    continue  # download failure is non-fatal; URL is still returned
                result["path"] = str(dest)
                downloaded_paths.append(str(dest))
                manifest_entries.append(self._manifest_entry(result, provider, kind, query, inputs))

        data: dict[str, Any] = {
            "query": query,
            "kind": kind,
            "provider": provider,
            "attribution_required": (
                "Powered By GIPHY — attribution marks required by GIPHY API terms"
                if provider == "giphy"
                else "Via Tenor — attribution required by Tenor API terms"
            ),
            "results": results,
            "manifest_entries": manifest_entries,
            "total_returned": len(results),
            "downloaded_paths": downloaded_paths,
        }
        artifacts = list(downloaded_paths)

        am_path = inputs.get("asset_manifest_path")
        if am_path and manifest_entries:
            reg_err = self._register_assets(Path(am_path), manifest_entries)
            if reg_err:
                # the downloads exist and are valid; only the registration failed
                data["asset_manifest_warning"] = reg_err
            else:
                data["asset_manifest_path"] = str(am_path)
                artifacts.append(str(am_path))

        return ToolResult(
            success=True,
            data=data,
            artifacts=artifacts,
            cost_usd=0.0,
            duration_seconds=round(time.time() - start, 2),
        )

    # ---- validation (pure, runs before any HTTP) ----

    def _validate(self, inputs: dict[str, Any]) -> tuple[str, str, int, str]:
        query = inputs.get("query")
        if not isinstance(query, str) or not query.strip():
            raise _SearchInputError("query is required (non-empty string).")
        kind = inputs.get("kind", "sticker")
        if kind not in self.KINDS:
            raise _SearchInputError(f"kind must be one of {self.KINDS}; got {kind!r}.")
        limit = inputs.get("limit", 5)
        if not isinstance(limit, int) or isinstance(limit, bool) or not (1 <= limit <= self.LIMIT_MAX):
            raise _SearchInputError(f"limit must be an integer in [1, {self.LIMIT_MAX}]; got {limit!r}.")
        rating = inputs.get("rating", "pg-13")
        if rating not in self.RATINGS:
            raise _SearchInputError(f"rating must be one of {self.RATINGS}; got {rating!r}.")
        return query.strip(), kind, limit, rating

    def _resolve_provider(self, requested: str) -> tuple[str, str]:
        if requested not in self.PROVIDERS:
            raise _SearchInputError(f"provider must be one of {self.PROVIDERS}; got {requested!r}.")
        giphy_key = (os.environ.get("GIPHY_API_KEY") or "").strip()
        tenor_key = (os.environ.get("TENOR_API_KEY") or "").strip()
        if requested == "giphy":
            if not giphy_key:
                raise _SearchInputError("provider=giphy requested but GIPHY_API_KEY is not set.")
            return "giphy", giphy_key
        if requested == "tenor":
            if not tenor_key:
                raise _SearchInputError("provider=tenor requested but TENOR_API_KEY is not set.")
            return "tenor", tenor_key
        # auto: GIPHY first (its stickers endpoint is the best transparent-asset source)
        if giphy_key:
            return "giphy", giphy_key
        if tenor_key:
            return "tenor", tenor_key
        raise _SearchInputError(
            "No sticker/GIF provider configured: set GIPHY_API_KEY "
            "(free key: https://developers.giphy.com) or TENOR_API_KEY "
            "(free key: https://developers.google.com/tenor) in .env."
        )

    # ---- provider search ----

    def _search_giphy(self, query: str, kind: str, limit: int, rating: str, key: str) -> list[dict]:
        import requests

        endpoint = GIPHY_STICKER_ENDPOINT if kind == "sticker" else GIPHY_GIF_ENDPOINT
        resp = requests.get(
            endpoint,
            params={"api_key": key, "q": query, "limit": limit, "rating": rating},
            timeout=20,
        )
        resp.raise_for_status()
        return (resp.json() or {}).get("data") or []

    def _search_tenor(self, query: str, kind: str, limit: int, rating: str, key: str) -> list[dict]:
        import requests

        params: dict[str, Any] = {
            "key": key,
            "q": query,
            "limit": limit,
            "client_key": "opennolan",
            "contentfilter": _TENOR_CONTENTFILTER[rating],
        }
        if kind == "sticker":
            params["searchfilter"] = "sticker"
        resp = requests.get(TENOR_ENDPOINT, params=params, timeout=20)
        resp.raise_for_status()
        return (resp.json() or {}).get("results") or []

    # ---- normalization (pure) ----

    @classmethod
    def _normalize_giphy(cls, item: dict, kind: str) -> dict[str, Any]:
        images = item.get("images") or {}
        original = images.get("original") or {}
        user = item.get("user") or {}
        by = user.get("display_name") or user.get("username") or ""
        return {
            "id": str(item.get("id", "")),
            "title": item.get("title", ""),
            "gif_url": original.get("url") or None,
            "width": cls._as_int(original.get("width")),
            "height": cls._as_int(original.get("height")),
            "preview_url": (images.get("preview_gif") or {}).get("url")
            or (images.get("fixed_width_small") or {}).get("url"),
            "source": item.get("url", ""),
            "attribution": f"Powered By GIPHY{f' — {by}' if by else ''}",
            "renditions": {
                "gif": original.get("url"),
                "mp4": original.get("mp4") or (images.get("original_mp4") or {}).get("mp4"),
                "webm": None,  # GIPHY does not ship webm renditions
                "webp": original.get("webp"),
            },
        }

    @classmethod
    def _normalize_tenor(cls, item: dict, kind: str) -> dict[str, Any]:
        media = item.get("media_formats") or {}
        # only the *_transparent formats are guaranteed-alpha on Tenor
        gif = (media.get("gif_transparent") if kind == "sticker" else None) or media.get("gif") or {}
        webp = (media.get("webp_transparent") if kind == "sticker" else None) or media.get("webp") or {}
        dims = gif.get("dims") or (media.get("mp4") or {}).get("dims") or [0, 0]
        return {
            "id": str(item.get("id", "")),
            "title": item.get("title") or item.get("content_description", ""),
            "gif_url": gif.get("url") or None,
            "width": cls._as_int(dims[0] if len(dims) > 0 else 0),
            "height": cls._as_int(dims[1] if len(dims) > 1 else 0),
            "preview_url": (media.get("tinygif") or {}).get("url") or gif.get("url"),
            "source": item.get("itemurl") or item.get("url", ""),
            "attribution": "Via Tenor",
            "renditions": {
                "gif": gif.get("url"),
                "mp4": (media.get("mp4") or {}).get("url"),
                "webm": (media.get("webm") or {}).get("url"),
                "webp": webp.get("url"),
            },
        }

    @classmethod
    def _pick_rendition(cls, renditions: dict[str, Optional[str]], kind: str) -> Optional[tuple[str, str]]:
        """First available rendition in the kind's preference order (see module docstring).

        kind=gif favors mp4/webm (composites cleaner than .gif in video_compose);
        kind=sticker favors gif/webp (the only guaranteed-alpha renditions).
        Returns (url, label) or None when the result has no usable rendition.
        """
        for label in cls.RENDITION_ORDER[kind]:
            url = renditions.get(label)
            if url:
                return url, label
        return None

    @staticmethod
    def _as_int(value: Any) -> int:
        try:
            return int(value)  # GIPHY returns dims as strings ("480")
        except (TypeError, ValueError):
            return 0

    # ---- download + asset_manifest ----

    def _download(self, url: str, dest: Path) -> Optional[str]:
        import requests

        try:
            resp = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            dest.write_bytes(resp.content)
            return None
        except Exception as e:
            return str(e)

    def _manifest_entry(
        self, result: dict[str, Any], provider: str, kind: str, query: str, inputs: dict[str, Any]
    ) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "id": f"sticker-{provider}-{result['id'] or Path(result['path']).stem}",
            "type": "video" if result["rendition"] in ("mp4", "webm") else "animation",
            "path": result["path"],
            "source_tool": self.name,
            "scene_id": str(inputs.get("scene_id", "overlay")),
            "subtype": kind,
            "format": result["rendition"],
            "provider": provider,
            "license": (
                "GIPHY API terms — 'Powered By GIPHY' attribution required"
                if provider == "giphy"
                else "Tenor API terms — 'Via Tenor' attribution required"
            ),
            "generation_summary": f"sticker_search '{query}' ({kind}) via {provider}",
        }
        if result.get("source"):
            entry["original_url"] = result["source"]
        if result.get("width") and result.get("height"):
            entry["resolution"] = f"{result['width']}x{result['height']}"
        return entry

    def _register_assets(self, path: Path, entries: list[dict[str, Any]]) -> Optional[str]:
        """Append downloaded assets to an asset_manifest, validate, write back.
        Returns an error string on failure (manifest left untouched), else None."""
        if not path.exists():
            return f"asset_manifest_path not found: {path}"
        try:
            doc = json.loads(path.read_text())
        except Exception as e:
            return f"could not read asset_manifest: {e}"
        if not isinstance(doc, dict) or not isinstance(doc.get("assets"), list):
            return "asset_manifest is not a valid manifest object with an assets[] list."

        doc["assets"].extend(entries)
        try:
            from schemas.artifacts import validate_artifact

            validate_artifact("asset_manifest", doc)
        except Exception as e:
            return f"sticker entries did not validate against asset_manifest schema: {e}"
        self._write_json(path, doc)
        return None

    @staticmethod
    def _write_json(path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        os.replace(tmp, path)


class _SearchInputError(Exception):
    """Bad parameters or missing API keys (validated before any HTTP call)."""
