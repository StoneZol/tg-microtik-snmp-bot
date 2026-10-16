from __future__ import annotations

import os
from pathlib import Path

# src/mikrotik_bot/paths.py → repo root is parents[2]
_REPO_ROOT = Path(__file__).resolve().parents[2]


def resolve_dir(
    env_name: str,
    docker_path: str,
    repo_relative: str,
    *,
    create: bool = False,
) -> str:
    override = os.getenv(env_name)
    if override:
        path = Path(override)
        if create:
            path.mkdir(parents=True, exist_ok=True)
        return str(path)

    docker = Path(docker_path)
    if docker.is_dir():
        return str(docker)

    local = _REPO_ROOT / repo_relative
    if create:
        local.mkdir(parents=True, exist_ok=True)
    return str(local)
