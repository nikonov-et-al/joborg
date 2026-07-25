from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib


project_file = Path(__file__).parents[1] / "pyproject.toml"
try:
    __version__ = version("jobtracker")
except PackageNotFoundError:
    __version__ = "0+unknown"

if project_file.exists():
    with project_file.open("rb") as source:
        __version__ = tomllib.load(source)["project"]["version"]
