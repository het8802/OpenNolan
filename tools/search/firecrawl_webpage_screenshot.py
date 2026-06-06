"""Webpage screenshot via Firecrawl /scrape with screenshot format.

Navigates to any URL with a full headless browser (JavaScript rendered),
takes a screenshot of the full page or viewport, and returns the image.
Ideal for capturing article proof cards, company press kit pages, product
landing pages, and any web content that needs to appear as a visual asset.

Cost: 1 Firecrawl credit per screenshot.
Screenshot URLs expire after 24 hours — the tool downloads the image
immediately and returns a stable local file path.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

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


class FirecrawlWebpageScreenshot(BaseTool):
    name = "webpage_screenshot"
    version = "0.1.0"
    tier = ToolTier.SOURCE
    capability = "webpage_screenshot"
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
        "Cost: 1 credit per screenshot."
    )
    agent_skills = []

    capabilities = [
        "screenshot_url",
        "capture_article",
        "capture_article_card",
        "capture_press_page",
        "capture_product_page",
        "full_page_screenshot",
        "viewport_screenshot",
    ]
    supports = {
        "javascript_rendering": True,   # Full headless browser — no blank JS pages
        "full_page_capture": True,
        "viewport_capture": True,
        "dynamic_content": True,
        "pdf_pages": True,
        "also_returns_markdown": True,  # Optional: get article text alongside screenshot
    }
    best_for = [
        "article screenshots for visual proof receipts in explainer videos",
        "company press kit and announcement pages",
        "product landing pages and feature announcements",
        "any URL that needs to appear as a visual asset in the composition",
        "capturing JavaScript-rendered pages that basic screenshotters miss",
    ]
    not_good_for = [
        "pages that require login or authentication",
        "very long pages where full_page=False is needed for performance",
        "high-volume batch screenshotting (1 credit each — use sparingly)",
    ]
    fallback_tools = []

    input_schema = {
        "type": "object",
        "required": ["url", "output_dir"],
        "properties": {
            "url": {
                "type": "string",
                "description": "URL of the webpage to screenshot.",
            },
            "output_dir": {
                "type": "string",
                "description": "Local directory to save the screenshot PNG.",
            },
            "full_page": {
                "type": "boolean",
                "default": False,
                "description": (
                    "Capture the full scrollable page height. "
                    "False (default) captures the viewport — better for article headers "
                    "and hero sections. True for complete page capture."
                ),
            },
            "filename": {
                "type": "string",
                "description": "Override the output filename (without extension). Defaults to domain-based name.",
            },
            "also_return_markdown": {
                "type": "boolean",
                "default": False,
                "description": "Also return the page text as markdown alongside the screenshot.",
            },
            "wait_for_selector": {
                "type": "string",
                "description": "CSS selector to wait for before screenshotting (for lazy-loaded content).",
            },
            "viewport_width": {
                "type": "integer",
                "default": 1280,
                "description": "Browser viewport width in pixels.",
            },
            "viewport_height": {
                "type": "integer",
                "default": 800,
                "description": "Browser viewport height in pixels.",
            },
        },
    }

    output_schema = {
        "type": "object",
        "properties": {
            "local_path": {"type": "string", "description": "Local path to the downloaded PNG."},
            "url": {"type": "string"},
            "title": {"type": "string"},
            "description": {"type": "string"},
            "markdown": {"type": ["string", "null"], "description": "Page text if also_return_markdown=True."},
            "width": {"type": "integer"},
            "height": {"type": "integer"},
            "credits_used": {"type": "integer"},
        },
    }

    resource_profile = ResourceProfile(cpu_cores=1, ram_mb=128, vram_mb=0, disk_mb=20, network_required=True)
    retry_policy = RetryPolicy(max_retries=2, retryable_errors=["timeout", "rate_limit"])
    idempotency_key_fields = ["url", "full_page", "viewport_width"]
    side_effects = ["calls Firecrawl API (costs 1 credit)", "writes PNG to output_dir"]
    user_visible_verification = ["View the screenshot PNG to confirm the page captured correctly"]

    def get_status(self) -> ToolStatus:
        try:
            from firecrawl import Firecrawl  # noqa: F401
        except ImportError:
            return ToolStatus.UNAVAILABLE
        if os.environ.get("FIRECRAWL_API_KEY"):
            return ToolStatus.AVAILABLE
        return ToolStatus.UNAVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.001  # 1 credit ≈ $0.001

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        try:
            from firecrawl import Firecrawl
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
        url = inputs["url"]
        output_dir = Path(inputs["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)

        full_page = inputs.get("full_page", False)
        also_markdown = inputs.get("also_return_markdown", False)

        viewport_w = inputs.get("viewport_width", 1280)
        viewport_h = inputs.get("viewport_height", 800)

        # Firecrawl v2 SDK (firecrawl-py >= 2.x, incl. 4.x): per-format options live
        # inside the format object, and scrape() takes keyword args — NOT a legacy
        # `params=` dict (that raised "scrape() got an unexpected keyword argument 'params'").
        screenshot_format: dict[str, Any] = {
            "type": "screenshot",
            "full_page": full_page,
            "viewport": {"width": viewport_w, "height": viewport_h},
        }
        formats: list[Any] = [screenshot_format]
        if also_markdown:
            formats.append("markdown")

        scrape_kwargs: dict[str, Any] = {"formats": formats, "mobile": False}
        if inputs.get("wait_for_selector"):
            scrape_kwargs["actions"] = [
                {"type": "wait", "selector": inputs["wait_for_selector"]}
            ]

        try:
            app = Firecrawl(api_key=api_key)
            result = app.scrape(url, **scrape_kwargs)
        except Exception as e:
            return ToolResult(success=False, error=f"Firecrawl scrape failed: {e}")

        # Extract from the returned Document (pydantic object in v2; dict-tolerant).
        def _get(obj: Any, key: str, default: Any = None) -> Any:
            if isinstance(obj, dict):
                return obj.get(key, default)
            return getattr(obj, key, default)

        screenshot_url = _get(result, "screenshot")
        markdown_text = _get(result, "markdown") if also_markdown else None
        raw_meta = _get(result, "metadata") or {}
        if isinstance(raw_meta, dict):
            metadata = raw_meta
        else:
            metadata = {
                "title": _get(raw_meta, "title", "") or "",
                "description": _get(raw_meta, "description", "") or "",
            }

        if not screenshot_url:
            return ToolResult(
                success=False,
                error=f"Firecrawl did not return a screenshot for URL: {url}. "
                      "The page may require login, be too large, or be unsupported.",
            )

        # Download the screenshot immediately (URLs expire in 24h)
        import requests as req
        try:
            img_resp = req.get(screenshot_url, timeout=30)
            img_resp.raise_for_status()
        except Exception as e:
            return ToolResult(
                success=False,
                error=f"Failed to download screenshot from Firecrawl URL: {e}",
            )

        # Generate filename from URL domain + timestamp if not overridden
        if inputs.get("filename"):
            fname_stem = inputs["filename"]
        else:
            domain = urlparse(url).netloc.replace("www.", "").replace(".", "_")
            fname_stem = f"screenshot_{domain}_{int(start)}"

        local_path = output_dir / f"{fname_stem}.png"
        local_path.write_bytes(img_resp.content)

        # Try to get image dimensions from the PNG header
        width, height = 0, 0
        try:
            import struct
            raw = img_resp.content
            if raw[:8] == b"\x89PNG\r\n\x1a\n":
                width, height = struct.unpack(">II", raw[16:24])
        except Exception:
            pass

        return ToolResult(
            success=True,
            data={
                "local_path": str(local_path),
                "url": url,
                "title": metadata.get("title", ""),
                "description": metadata.get("description", ""),
                "markdown": markdown_text,
                "width": width,
                "height": height,
                "credits_used": 1,
                "provider": "firecrawl",
                "cost_usd": 0.001,
            },
            artifacts=[str(local_path)],
            cost_usd=0.001,
            duration_seconds=round(time.time() - start, 2),
        )
