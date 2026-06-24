from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


DEFAULT_CONFIG: dict[str, Any] = {
    "applicant": {
        "name": "",
        "email": "",
    },
    "paths": {
        "input": "input",
        "jobs": "jobs",
        "reports": "reports",
        "profile": "profile",
    },
    "llm": {
        "enabled": True,
        "provider": "ollama",
        "base_url": "http://localhost:11434/v1",
        "model": "gemma4:12b-it-q4_K_M",
        "api_key": "local-not-needed",
    },
    "export": {
        "create_docx": True,
        "create_pdf": True,
    },
}


def deep_merge(default: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(default)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(config_path: Path = Path("config.yml")) -> dict[str, Any]:
    if not config_path.exists():
        return DEFAULT_CONFIG

    with config_path.open("r", encoding="utf-8") as f:
        loaded = yaml.safe_load(f) or {}

    return deep_merge(DEFAULT_CONFIG, loaded)


def ensure_project_dirs(config: dict[str, Any]) -> None:
    for key in ["input", "jobs", "reports", "profile"]:
        Path(config["paths"][key]).mkdir(parents=True, exist_ok=True)
