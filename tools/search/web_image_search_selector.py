"""Capability-level selector for web image search.

Auto-discovers all tools with capability="web_image_search" from the registry.
Currently routes between duckduckgo_image_search (free, default) and
firecrawl_image_search (credits, HD filtering).

Scoring behaviour:
  - DDG wins by default: free → cost_efficiency=1.0, good reliability
  - Firecrawl wins when: hd_only=True, DDG unavailable, or task context
    signals high-quality evidence requirement
  - User can force a provider via preferred_provider
"""

from __future__ import annotations

import os
from typing import Any

from tools.base_tool import BaseTool, ToolResult, ToolRuntime, ToolStability, ToolStatus, ToolTier


class WebImageSearchSelector(BaseTool):
    name = "web_image_search"
    version = "0.1.0"
    tier = ToolTier.SOURCE
    capability = "web_image_search"
    provider = "selector"
    stability = ToolStability.BETA
    runtime = ToolRuntime.HYBRID
    agent_skills = []

    capabilities = [
        "search_web_images",
        "find_event_photos",
        "find_logos",
        "find_news_images",
        "hd_image_search",
        "provider_selection",
    ]
    supports = {
        "user_preference_routing": True,
        "free_fallback": True,
        "hd_filtering": True,
    }
    best_for = [
        "real-world event photos, company logos, news images, press assets",
        "any visual proof that stock libraries (Pexels/Pixabay) won't have",
        "preflight routing between free DDG and credit-based Firecrawl",
    ]
    not_good_for = [
        "generic stock photography — use image_selector (pexels/pixabay) instead",
        "AI-generated or stylized imagery — use image_selector (flux/grok) instead",
    ]

    input_schema = {
        "type": "object",
        "required": ["query"],
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Image search query. Be specific: 'Google I/O 2024 keynote stage', "
                    "'OpenAI logo PNG transparent', 'Anthropic Claude announcement press photo'."
                ),
            },
            "max_results": {
                "type": "integer",
                "default": 5,
                "minimum": 1,
                "maximum": 20,
            },
            "hd_only": {
                "type": "boolean",
                "default": False,
                "description": (
                    "Request only HD/high-resolution images. "
                    "Automatically routes to Firecrawl (imagesize operator support) "
                    "over DDG when enabled."
                ),
            },
            "type_image": {
                "type": "string",
                "enum": ["photo", "clipart", "gif", "transparent", "line"],
                "description": "DDG image type filter. 'transparent' is ideal for logos.",
            },
            "size": {
                "type": "string",
                "enum": ["Small", "Medium", "Large", "Wallpaper"],
                "description": "DDG size filter.",
            },
            "license_image": {
                "type": "string",
                "enum": ["any", "Public", "Share", "ShareCommercially", "Modify", "ModifyCommercially"],
                "default": "any",
            },
            "output_dir": {
                "type": "string",
                "description": "Directory to download images into. Passed to selected provider.",
            },
            "download_top_n": {
                "type": "integer",
                "default": 1,
                "description": "Number of top results to download.",
            },
            "min_width": {
                "type": "integer",
                "description": "Minimum image width in pixels (DDG provider only).",
            },
            "min_height": {
                "type": "integer",
                "description": "Minimum image height in pixels (DDG provider only).",
            },
            "preferred_provider": {
                "type": "string",
                "enum": ["auto", "duckduckgo", "firecrawl"],
                "default": "auto",
                "description": (
                    "Force a specific provider. 'auto' uses scoring engine: "
                    "DDG by default (free), Firecrawl for HD or DDG unavailable."
                ),
            },
            "task_context": {
                "type": "object",
                "description": "Pipeline task context for scoring (intent, style_keywords, budget_remaining_usd).",
            },
        },
    }

    def _providers(self) -> list[BaseTool]:
        from tools.tool_registry import registry
        registry.ensure_discovered()
        return [t for t in registry.get_by_capability("web_image_search") if t.name != self.name]

    def get_status(self) -> ToolStatus:
        if any(t.get_status() == ToolStatus.AVAILABLE for t in self._providers()):
            return ToolStatus.AVAILABLE
        return ToolStatus.UNAVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        tool = self._select(inputs)
        return tool.estimate_cost(inputs) if tool else 0.0

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        tool = self._select(inputs)
        if tool is None:
            return ToolResult(
                success=False,
                error=(
                    "No web image search provider available. "
                    "Install duckduckgo-search (free) or set FIRECRAWL_API_KEY."
                ),
            )

        # Pass only the params the selected provider accepts
        adapted = self._adapt_inputs(inputs, tool)
        result = tool.execute(adapted)

        if result.success:
            result.data.setdefault("selected_provider", tool.provider)
            result.data["selected_tool"] = tool.name
            result.data["alternatives_considered"] = [
                t.name for t in self._providers()
                if t.name != tool.name and t.get_status() == ToolStatus.AVAILABLE
            ]
        return result

    def _select(self, inputs: dict[str, Any]) -> BaseTool | None:
        from lib.scoring import rank_providers, normalize_task_context

        providers = self._providers()
        available = [t for t in providers if t.get_status() == ToolStatus.AVAILABLE]
        if not available:
            return None

        preferred = inputs.get("preferred_provider", "auto")

        # Explicit override
        if preferred != "auto":
            for t in available:
                if t.provider == preferred:
                    return t
            # Requested provider not available — fall through to scoring

        # hd_only forces Firecrawl (it has imagesize: operator support)
        if inputs.get("hd_only"):
            for t in available:
                if t.provider == "firecrawl":
                    return t
            # Firecrawl not available — fall through to scoring

        # Score remaining candidates
        task_context = normalize_task_context(
            inputs.get("task_context", {}),
            prompt=inputs.get("query", ""),
            capability=self.capability,
        )
        rankings = rank_providers(available, task_context)
        for score in rankings:
            for t in available:
                if t.provider == score.provider:
                    return t

        return available[0] if available else None

    def _adapt_inputs(self, inputs: dict[str, Any], tool: BaseTool) -> dict[str, Any]:
        """Pass only params the selected provider accepts. Both share the same core schema."""
        adapted = {
            "query": inputs["query"],
            "max_results": inputs.get("max_results", 5),
            "output_dir": inputs.get("output_dir"),
            "download_top_n": inputs.get("download_top_n", 1),
        }
        # DDG-specific
        if tool.provider == "duckduckgo":
            for k in ("size", "type_image", "layout", "license_image", "min_width", "min_height"):
                if inputs.get(k) is not None:
                    adapted[k] = inputs[k]

        # Firecrawl-specific
        if tool.provider == "firecrawl":
            if inputs.get("hd_only"):
                adapted["hd_only"] = True

        # Remove None values
        return {k: v for k, v in adapted.items() if v is not None}
