"""Reads the JSON files in retopo_tool/config. Config is read, never copied."""
import json
from pathlib import Path

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
SUPPORTED_VERSION = 1  # schema version this code reads


def load_config(name):
    """Load config/<name>.json. Raises ValueError with the file name on bad JSON."""
    path = CONFIG_DIR / f"{name}.json"
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        raise ValueError(f"Config file missing: {path}")
    except json.JSONDecodeError as e:
        raise ValueError(f"Config file {path} is not valid JSON: {e}")
    if data.get("version") != SUPPORTED_VERSION:
        raise ValueError(f"Config file {path} has version {data.get('version')!r}; "
                         f"this tool reads version {SUPPORTED_VERSION}.")
    return data
