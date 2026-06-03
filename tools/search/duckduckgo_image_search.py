"""Web image search via DuckDuckGo (free, no API key required).

Uses the ddgs library which wraps the unofficial DDG images API.
Returns image URLs with metadata — the caller downloads whichever are needed.
Free tier: ~100-200 requests/hour per IP before soft rate-limiting.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

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


class DuckDuckGoImageSearch(BaseTool):
    name = "duckduckgo_image_search"
    version = "0.1.0"
    tier = ToolTier.SOURCE
    capability = "web_image_search"
    provider = "duckduckgo"
    stability = ToolStability.BETA
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.STOCHASTIC
    runtime = ToolRuntime.API

    dependencies = ["pip:ddgs"]
    install_instructions = "pip install ddgs  (no API key needed)"
    agent_skills = []

    capabilities = ["search_web_images", "find_event_photos", "find_logos", "find_news_images"]
    supports = {
        "free": True,
        "no_api_key": True,
        "size_filter": True,
        "type_filter": True,
        "license_filter": True,
        "region_filter": True,
        "download_results": True,
    }
    best_for = [
        "real-world event photos (conferences, product launches, keynotes)",
        "company logos and brand assets",
        "news images and press photos",
        "finding specific real-world visuals quickly at zero cost",
        "general web image search with no API key or credits",
    ]
    not_good_for = [
        "HD/4K guaranteed resolution (results vary by source)",
        "licensed/royalty-free guaranteed stock photography",
        "high-volume automated searches (soft rate-limit ~100-200/hr per IP)",
    ]
    fallback_tools = ["firecrawl_image_search"]

    input_schema = {
        "type": "object",
        "required": ["query"],
        "properties": {
            "query": {
                "type": "string",
                "description": "Image search query. Be specific: 'Google I/O 2024 keynote stage photo', 'OpenAI logo transparent PNG'.",
            },
            "max_results": {
                "type": "integer",
                "default": 5,
                "minimum": 1,
                "maximum": 20,
                "description": "Number of image results to return.",
            },
            "size": {
                "type": "string",
                "enum": ["Small", "Medium", "Large", "Wallpaper"],
                "description": "Filter by image size. 'Large' or 'Wallpaper' for high-res.",
            },
            "type_image": {
                "type": "string",
                "enum": ["photo", "clipart", "gif", "transparent", "line"],
                "description": "Filter by image type. Use 'transparent' for logos with transparent backgrounds.",
            },
            "layout": {
                "type": "string",
                "enum": ["Square", "Tall", "Wide"],
            },
            "license_image": {
                "type": "string",
                "enum": ["any", "Public", "Share", "ShareCommercially", "Modify", "ModifyCommercially"],
                "default": "any",
                "description": "License filter. Use 'ShareCommercially' for content you can use commercially.",
            },
            "output_dir": {
                "type": "string",
                "description": "If set, download the top N images to this directory. Returns local paths.",
            },
            "download_top_n": {
                "type": "integer",
                "default": 1,
                "description": "How many of the top results to download when output_dir is set.",
            },
            "min_width": {
                "type": "integer",
                "description": "Minimum image width in pixels. Filters results after fetching.",
            },
            "min_height": {
                "type": "integer",
                "description": "Minimum image height in pixels. Filters results after fetching.",
            },
        },
    }

    output_schema = {
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "image_url": {"type": "string"},
                        "thumbnail_url": {"type": "string"},
                        "source_url": {"type": "string"},
                        "width": {"type": "integer"},
                        "height": {"type": "integer"},
                        "local_path": {"type": ["string", "null"]},
                    },
                },
            },
            "query": {"type": "string"},
            "total_returned": {"type": "integer"},
            "downloaded_paths": {"type": "array", "items": {"type": "string"}},
        },
    }

    resource_profile = ResourceProfile(cpu_cores=1, ram_mb=128, vram_mb=0, disk_mb=50, network_required=True)
    retry_policy = RetryPolicy(max_retries=2, retryable_errors=["rate_limit", "timeout"])
    idempotency_key_fields = ["query", "max_results", "size", "type_image"]
    side_effects = ["calls DuckDuckGo images API", "optionally writes image files to output_dir"]
    user_visible_verification = ["Check downloaded images match the intended subject"]

    def get_status(self) -> ToolStatus:
        try:
            from ddgs import DDGS  # noqa: F401
            return ToolStatus.AVAILABLE
        except ImportError:
            return ToolStatus.UNAVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0  # Completely free

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        try:
            from ddgs import DDGS
        except ImportError:
            return ToolResult(
                success=False,
                error="ddgs not installed. Run: pip install ddgs",
            )

        start = time.time()
        query = inputs["query"]
        max_results = inputs.get("max_results", 5)

        # Build DDG kwargs — only pass filters that were explicitly requested
        ddg_kwargs: dict[str, Any] = {}
        if inputs.get("size"):
            ddg_kwargs["size"] = inputs["size"]
        if inputs.get("type_image"):
            ddg_kwargs["type_image"] = inputs["type_image"]
        if inputs.get("layout"):
            ddg_kwargs["layout"] = inputs["layout"]
        if inputs.get("license_image") and inputs["license_image"] != "any":
            ddg_kwargs["license_image"] = inputs["license_image"]

        try:
            with DDGS() as ddgs:
                raw = list(ddgs.images(query, max_results=max_results * 2, **ddg_kwargs))
        except Exception as e:
            return ToolResult(success=False, error=f"DuckDuckGo image search failed: {e}")

        # Normalize results
        results = []
        for item in raw:
            results.append({
                "title": item.get("title", ""),
                "image_url": item.get("image", ""),
                "thumbnail_url": item.get("thumbnail", ""),
                "source_url": item.get("url", ""),
                "width": item.get("width", 0),
                "height": item.get("height", 0),
                "local_path": None,
            })

        # Apply min dimension filters
        min_w = inputs.get("min_width", 0)
        min_h = inputs.get("min_height", 0)
        if min_w or min_h:
            results = [r for r in results if r["width"] >= min_w and r["height"] >= min_h]

        results = results[:max_results]

        # Optionally download top N
        downloaded_paths: list[str] = []
        output_dir = inputs.get("output_dir")
        download_n = inputs.get("download_top_n", 1)

        if output_dir and results:
            import requests as req
            out = Path(output_dir)
            out.mkdir(parents=True, exist_ok=True)

            for i, result in enumerate(results[:download_n]):
                img_url = result["image_url"]
                if not img_url:
                    continue
                try:
                    resp = req.get(img_url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
                    resp.raise_for_status()
                    # Infer extension from content-type or URL
                    ct = resp.headers.get("content-type", "image/jpeg")
                    ext = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp",
                           "image/gif": "gif"}.get(ct.split(";")[0].strip(), "jpg")
                    safe_query = "".join(c if c.isalnum() else "_" for c in query[:30])
                    fname = out / f"ddg_{safe_query}_{i:02d}.{ext}"
                    fname.write_bytes(resp.content)
                    result["local_path"] = str(fname)
                    downloaded_paths.append(str(fname))
                except Exception:
                    pass  # Download failure is non-fatal; URL is still returned

        if not results:
            return ToolResult(
                success=False,
                error=f"No images found for query: '{query}'. Try a more specific query or remove size/type filters.",
            )

        return ToolResult(
            success=True,
            data={
                "query": query,
                "results": results,
                "total_returned": len(results),
                "downloaded_paths": downloaded_paths,
                "provider": "duckduckgo",
                "cost_usd": 0.0,
            },
            artifacts=downloaded_paths,
            cost_usd=0.0,
            duration_seconds=round(time.time() - start, 2),
        )
