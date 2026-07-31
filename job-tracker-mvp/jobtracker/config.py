from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .paths import AppPaths


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
        "remote_disclosure_consent": False,
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


def _default_config(paths: AppPaths, base_dir: Path | None = None) -> dict[str, Any]:
    config = deep_merge(DEFAULT_CONFIG, {})
    if base_dir is None:
        config["paths"] = {
            "input": str(paths.input_dir),
            "jobs": str(paths.jobs_dir),
            "reports": str(paths.reports_dir),
            "profile": str(paths.profile_dir),
        }
    else:
        config["paths"] = {key: str(base_dir / key) for key in ("input", "jobs", "reports", "profile")}
    return config


def _resolve_config_paths(config: dict[str, Any], base_dir: Path) -> dict[str, Any]:
    for key in ("input", "jobs", "reports", "profile"):
        value = config["paths"].get(key)
        if isinstance(value, (str, Path)) and str(value):
            path = Path(value).expanduser()
            config["paths"][key] = str(path if path.is_absolute() else (base_dir / path).resolve())
    return config


def load_config(
    config_path: str | Path | None = None,
    *,
    app_paths: AppPaths | None = None,
) -> dict[str, Any]:
    paths = app_paths or AppPaths.resolve()
    explicit_path = config_path is not None
    path = paths.config_file if config_path is None else Path(config_path)
    path = path.expanduser().resolve()
    defaults = _default_config(paths, path.parent if explicit_path else None)

    if not path.exists():
        return defaults

    with path.open("r", encoding="utf-8") as f:
        loaded = yaml.safe_load(f) or {}

    return _resolve_config_paths(deep_merge(defaults, loaded), path.parent)


def ensure_project_dirs(config: dict[str, Any]) -> None:
    for key in ["input", "jobs", "reports", "profile"]:
        Path(config["paths"][key]).mkdir(parents=True, exist_ok=True)
