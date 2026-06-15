"""Template loader — load, validate, and list reel templates from templates/.

Mirrors styles/playbook_loader.py. A template is a reusable, parametrized reel structure
(clip slots + timing + transitions + music/subtitle config) that template_apply fills with
real assets to emit a valid edit_decisions artifact.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import jsonschema
import yaml

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
_SCHEMA_PATH = (
    Path(__file__).resolve().parent.parent / "schemas" / "templates" / "template.schema.json"
)


def _load_template_schema() -> dict:
    with open(_SCHEMA_PATH) as f:
        return json.load(f)


def validate_template(template: dict) -> None:
    """Validate a template dict against the template schema. Raises on failure."""
    jsonschema.validate(instance=template, schema=_load_template_schema())


def load_template(name: str, templates_dir: Optional[Path] = None) -> dict[str, Any]:
    """Load and validate a template by name (without the .yaml extension)."""
    templates_dir = templates_dir or TEMPLATES_DIR
    path = templates_dir / f"{name}.yaml"
    if not path.exists():
        available = ", ".join(list_templates(templates_dir)) or "(none)"
        raise FileNotFoundError(f"Template {name!r} not found in {templates_dir}. Available: {available}")
    with open(path) as f:
        template = yaml.safe_load(f)
    validate_template(template)
    return template


def list_templates(templates_dir: Optional[Path] = None) -> list[str]:
    """List available template names (filenames without .yaml)."""
    templates_dir = templates_dir or TEMPLATES_DIR
    if not templates_dir.exists():
        return []
    return sorted(p.stem for p in templates_dir.glob("*.yaml"))
