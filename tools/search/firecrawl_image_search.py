"""Web image search via Firecrawl /search?sources=images.

Firecrawl's search endpoint with sources=["images"] returns real-world web
image results including HD images. Costs 2 credits per 10 results.
Supports HD size filtering via query operators (e.g. "imagesize:1920x1080").
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


class FirecrawlImageSearch(BaseTool):
    name = "firecrawl_image_search"
    version = "0.1.0"
    tier = ToolTier.SOURCE
    capability = "web_image_search"
    provider = "firecrawl"
    stability = ToolStability.BETA
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.STOCHASTIC
    runtime = ToolRuntime.API

    dependencies = ["pip:firecrawl-py"]
    install_instructions = (
        "pip install firecrawl-py\n"
        "Set FIRECRAWL_API_KEY environment variable.\n"
        "Get a key at https://firecrawl.dev (1,000 free credits/month).\n"
        "Cost: 2 credits per 10 results."
    )
    agent_skills = []

    capabilities = [
        "search_web_images", "hd_image_search", "size_filtered_search",
        "find_event_photos", "find_logos", "find_news_images",
    ]
    supports = {
        "hd_filtering": True,
        "size_operators": True,      # imagesize:WxH query operator
        "high_resolution": True,
        "download_results": True,
        "metadata_rich": True,       # returns width, height, title, source URL
    }
    best_for = [
        "HD and high-resolution image search (imagesize:1920x1080 operator)",
        "when DuckDuckGo rate-limits or returns insufficient quality results",
        "event photos and news images with richer metadata",
        "fallback when free DDG search is unavailable",
    ]
    not_good_for = [
        "high-volume searches (2 credits per 10 results — use DDG for volume)",
        "when FIRECRAWL_API_KEY is not configured",
    ]
    fallback_tools = ["duckduckgo_image_search"]

    input_schema = {
        "type": "object",
        "required": ["query"],
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Image search query. Supports size operators: "
                    "'Google I/O 2024 event photo imagesize:1920x1080'. "
                    "Append imagesize:WxH for HD-only results."
                ),
            },
            "max_results": {
                "type": "integer",
                "default": 5,
                "minimum": 1,
                "maximum": 20,
                "description": "Number of results. Rounds up to next multiple of 10 for credit billing.",
            },
            "hd_only": {
                "type": "boolean",
                "default": False,
                "description": "Auto-append 'imagesize:1920x1080' to query for HD results.",
            },
            "output_dir": {
                "type": "string",
                "description": "If set, download the top N images to this directory.",
            },
            "download_top_n": {
                "type": "integer",
                "default": 1,
                "description": "How many top results to download when output_dir is set.",
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
                        "source_url": {"type": "string"},
                        "width": {"type": "integer"},
                        "height": {"type": "integer"},
                        "position": {"type": "integer"},
                        "local_path": {"type": ["string", "null"]},
                    },
                },
            },
            "query": {"type": "string"},
            "total_returned": {"type": "integer"},
            "credits_used": {"type": "number"},
            "downloaded_paths": {"type": "array", "items": {"type": "string"}},
        },
    }

    resource_profile = ResourceProfile(cpu_cores=1, ram_mb=128, vram_mb=0, disk_mb=50, network_required=True)
    retry_policy = RetryPolicy(max_retries=2, retryable_errors=["rate_limit", "timeout"])
    idempotency_key_fields = ["query", "max_results"]
    side_effects = ["calls Firecrawl API (costs credits)", "optionally writes image files to output_dir"]
    user_visible_verification = ["Check downloaded images match the intended subject"]

    def get_status(self) -> ToolStatus:
        try:
            from firecrawl import FirecrawlApp  # noqa: F401
        except ImportError:
            return ToolStatus.UNAVAILABLE
        if os.environ.get("FIRECRAWL_API_KEY"):
            return ToolStatus.AVAILABLE
        return ToolStatus.UNAVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        # 2 credits per 10 results; credits are not USD but approximate $0.001 each
        max_results = inputs.get("max_results", 5)
        credits = 2 * (max(max_results, 10) // 10)
        return credits * 0.001

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        try:
            from firecrawl import FirecrawlApp
        except ImportError:
            return ToolResult(
                success=False,
                error="firecrawl-py not installed. Run: pip install firecrawl-py",
            )

        api_key = os.environ.get("FIRECRAWL_API_KEY")
        if not api_key:
            return ToolResult(
                success=False,
                error="FIRECRAWL_API_KEY not set. " + self.install_instructions,
            )

        start = time.time()
        query = inputs["query"]
        max_results = inputs.get("max_results", 5)

        # Auto-append HD size operator if requested
        if inputs.get("hd_only") and "imagesize:" not in query:
            query = f"{query} imagesize:1920x1080"

        try:
            app = FirecrawlApp(api_key=api_key)
            response = app.search(
                query,
                params={
                    "sources": ["images"],
                    "limit": max_results,
                },
            )
        except Exception as e:
            return ToolResult(success=False, error=f"Firecrawl image search failed: {e}")

        # Normalize: Firecrawl returns result.images as a list
        raw_images = []
        if hasattr(response, "images") and response.images:
            raw_images = response.images
        elif isinstance(response, dict):
            raw_images = response.get("images", [])

        results = []
        for item in raw_images[:max_results]:
            if isinstance(item, dict):
                results.append({
                    "title": item.get("title", ""),
                    "image_url": item.get("imageUrl", item.get("image_url", "")),
                    "source_url": item.get("url", ""),
                    "width": item.get("imageWidth", item.get("width", 0)),
                    "height": item.get("imageHeight", item.get("height", 0)),
                    "position": item.get("position", len(results) + 1),
                    "local_path": None,
                })
            else:
                # SDK model object
                results.append({
                    "title": getattr(item, "title", ""),
                    "image_url": getattr(item, "imageUrl", getattr(item, "image_url", "")),
                    "source_url": getattr(item, "url", ""),
                    "width": getattr(item, "imageWidth", getattr(item, "width", 0)),
                    "height": getattr(item, "imageHeight", getattr(item, "height", 0)),
                    "position": getattr(item, "position", len(results) + 1),
                    "local_path": None,
                })

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
                    ct = resp.headers.get("content-type", "image/jpeg")
                    ext = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp",
                           "image/gif": "gif"}.get(ct.split(";")[0].strip(), "jpg")
                    safe_query = "".join(c if c.isalnum() else "_" for c in query[:30])
                    fname = out / f"fc_{safe_query}_{i:02d}.{ext}"
                    fname.write_bytes(resp.content)
                    result["local_path"] = str(fname)
                    downloaded_paths.append(str(fname))
                except Exception:
                    pass

        credits_used = 2 * (max(len(results), 10) // 10)
        cost_usd = credits_used * 0.001

        if not results:
            return ToolResult(
                success=False,
                error=f"No images found for query: '{query}'. Try removing size operators or broadening the query.",
                data={"credits_used": credits_used},
            )

        return ToolResult(
            success=True,
            data={
                "query": query,
                "results": results,
                "total_returned": len(results),
                "credits_used": credits_used,
                "downloaded_paths": downloaded_paths,
                "provider": "firecrawl",
                "cost_usd": cost_usd,
            },
            artifacts=downloaded_paths,
            cost_usd=cost_usd,
            duration_seconds=round(time.time() - start, 2),
        )
