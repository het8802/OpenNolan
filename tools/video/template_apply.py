"""Template Apply — fill a reel template's slots with assets and emit edit_decisions.

Instagram Edits' Templates: a reusable reel structure (clip slots + timing + transitions +
music/subtitle config) that you drop your own clips into. This tool loads a template
(lib/template_loader), maps the agent's/user's assets onto the slots, and emits a VALIDATED
edit_decisions artifact ready for compose.

Design (Edits-parity Wave 6, /plan-eng-review):
  - Templates are YAML in templates/, validated by schemas/templates/template.schema.json
    (mirrors the styles/playbook_loader pattern).
  - The emitted edit_decisions is validated against its schema before writing — a template
    can never produce a corrupt artifact.
  - Slot-count mismatch (you gave N assets, the template has M slots) is rejected with the
    list of missing/extra slots.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

from tools.base_tool import (
    BaseTool,
    Determinism,
    ExecutionMode,
    ResourceProfile,
    ToolResult,
    ToolRuntime,
    ToolStability,
    ToolTier,
)


class TemplateApply(BaseTool):
    name = "template_apply"
    version = "0.1.0"
    tier = ToolTier.CORE
    capability = "video_post"
    provider = "opennolan"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.DETERMINISTIC
    runtime = ToolRuntime.LOCAL

    dependencies: list[str] = []  # pure: loads a template + emits an artifact
    install_instructions = ""
    agent_skills: list[str] = []

    capabilities = ["apply_template", "emit_edit_decisions"]
    supports = {"slot_filling": True, "list_templates": True}
    best_for = ["turning a reusable reel template + your clips into a ready-to-compose edit"]
    not_good_for = ["rendering — emits edit_decisions; compose renders it"]
    fallback_tools: list[str] = []

    input_schema = {
        "type": "object",
        "required": ["template", "slot_assets"],
        "properties": {
            "template": {"type": "string", "description": "Template name (see templates/) or use template_path."},
            "template_path": {"type": "string", "description": "Path to a template YAML (overrides template)."},
            "slot_assets": {
                "type": "object",
                "description": "Map each template slot_id -> asset path (video/image).",
            },
            "music_path": {"type": "string", "description": "Music track (used if the template enables music)."},
            "subtitle_source": {"type": "string", "description": "Subtitle file/asset (used if the template enables subtitles)."},
            "output_path": {"type": "string", "description": "Where to write edit_decisions. Defaults to projects/<id>/artifacts or ./edit_decisions.json."},
            "project_id": {"type": "string"},
        },
    }

    resource_profile = ResourceProfile(cpu_cores=1, ram_mb=128, vram_mb=0, disk_mb=10)
    idempotency_key_fields = ["template", "slot_assets"]
    side_effects = ["writes an edit_decisions artifact"]
    user_visible_verification = ["Review the emitted cuts/timing/music match the template's intent"]

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        # --- load + validate template ---
        try:
            from lib.template_loader import load_template, validate_template
        except Exception as e:
            return ToolResult(success=False, error=f"template loader unavailable: {e}")

        tpl_path = inputs.get("template_path")
        try:
            if tpl_path:
                import yaml

                with open(tpl_path) as f:
                    template = yaml.safe_load(f)
                validate_template(template)
            else:
                name = inputs.get("template")
                if not name:
                    return ToolResult(success=False, error="Provide template (name) or template_path.")
                template = load_template(name)
        except FileNotFoundError as e:
            return ToolResult(success=False, error=str(e))
        except Exception as e:
            return ToolResult(success=False, error=f"template failed to load/validate: {e}")

        slots = template.get("slots", [])
        slot_assets = inputs.get("slot_assets") or {}
        if not isinstance(slot_assets, dict):
            return ToolResult(success=False, error="slot_assets must be an object {slot_id: asset_path}.")

        # --- slot-count / coverage check ---
        slot_ids = [s["slot_id"] for s in slots]
        missing = [sid for sid in slot_ids if sid not in slot_assets]
        extra = [sid for sid in slot_assets if sid not in slot_ids]
        if missing:
            return ToolResult(
                success=False,
                error=(
                    f"Template {template['name']!r} has {len(slots)} slot(s); missing assets for: "
                    f"{missing}. Provide a path for each slot_id."
                    + (f" (unknown slot ids given: {extra})" if extra else "")
                ),
            )
        # verify the asset files exist
        for sid in slot_ids:
            ap = Path(slot_assets[sid])
            if not ap.exists():
                return ToolResult(success=False, error=f"asset for slot {sid!r} not found: {ap}")

        # --- build cuts from slots ---
        cuts: list[dict[str, Any]] = []
        for s in slots:
            sid = s["slot_id"]
            seconds = float(s["seconds"])
            cut: dict[str, Any] = {
                "id": sid,
                "source": str(slot_assets[sid]),
                "in_seconds": 0,
                "out_seconds": round(seconds, 4),
            }
            if s.get("kind") == "image" and s.get("animation"):
                cut["transform"] = {"animation": s["animation"]}
            if s.get("transition_in"):
                cut["transition_in"] = s["transition_in"]
                if s.get("transition_duration") is not None:
                    cut["transition_duration"] = float(s["transition_duration"])
            cut["reason"] = f"template:{template['name']}"
            cuts.append(cut)

        edit_decisions: dict[str, Any] = {
            "version": "1.0",
            "cuts": cuts,
            "render_runtime": template.get("render_runtime", "ffmpeg"),
        }
        if template.get("renderer_family"):
            edit_decisions["renderer_family"] = template["renderer_family"]

        warnings: list[str] = []

        # --- music ---
        music_cfg = template.get("music") or {}
        if music_cfg.get("enabled"):
            music_path = inputs.get("music_path")
            if music_path:
                if not Path(music_path).exists():
                    return ToolResult(success=False, error=f"music_path not found: {music_path}")
                edit_decisions.setdefault("audio", {})["music"] = {
                    "asset_id": str(music_path),
                    "volume": float(music_cfg.get("volume", 0.7)),
                    "ducking": bool(music_cfg.get("ducking", True)),
                }
            else:
                warnings.append("template enables music but no music_path was given — emitted without music.")

        # --- subtitles ---
        sub_cfg = template.get("subtitles") or {}
        if sub_cfg.get("enabled"):
            subs: dict[str, Any] = {"enabled": True}
            if sub_cfg.get("style"):
                subs["style"] = sub_cfg["style"]
            if sub_cfg.get("position"):
                subs["position"] = sub_cfg["position"]
            src = inputs.get("subtitle_source")
            if src:
                subs["source"] = str(src)
            else:
                warnings.append("template enables subtitles but no subtitle_source was given.")
            edit_decisions["subtitles"] = subs

        edit_decisions["metadata"] = {
            "template": template["name"],
            "aspect_ratio": template.get("aspect_ratio"),
        }

        # --- validate BEFORE writing (never emit a corrupt artifact) ---
        try:
            from schemas.artifacts import validate_artifact

            validate_artifact("edit_decisions", edit_decisions)
        except Exception as e:
            return ToolResult(
                success=False,
                error=f"emitted edit_decisions did not validate against its schema: {e}",
            )

        out_path = self._resolve_output_path(inputs)
        self._write_json(out_path, edit_decisions)

        data: dict[str, Any] = {
            "template": template["name"],
            "n_cuts": len(cuts),
            "output_path": str(out_path),
            "edit_decisions": edit_decisions,
        }
        if warnings:
            data["warnings"] = warnings
        return ToolResult(success=True, data=data, artifacts=[str(out_path)])

    def _resolve_output_path(self, inputs: dict[str, Any]) -> Path:
        op = inputs.get("output_path")
        if op:
            return Path(op)
        pid = inputs.get("project_id")
        if pid:
            return Path("projects") / pid / "artifacts" / "edit_decisions.json"
        return Path("edit_decisions.json")

    @staticmethod
    def _write_json(path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        os.replace(tmp, path)
