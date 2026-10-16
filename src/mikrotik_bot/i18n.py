from __future__ import annotations

import json
from pathlib import Path


class Translator:
    def __init__(self, locales_dir: str | Path, lang: str = "en") -> None:
        self._messages = self._load(Path(locales_dir), lang)

    @staticmethod
    def _load(locales_dir: Path, lang: str) -> dict[str, str]:
        path = locales_dir / f"{lang}.json"
        if not path.is_file():
            fallback = locales_dir / "en.json"
            if not fallback.is_file():
                raise FileNotFoundError(f"Locale not found: {path}")
            path = fallback

        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)

        if not isinstance(data, dict):
            raise ValueError(f"Locale file must be a JSON object: {path}")
        return {str(k): str(v) for k, v in data.items()}

    def t(self, key: str, **kwargs: object) -> str:
        template = self._messages.get(key, key)
        if not kwargs:
            return template

        class _Safe(dict):
            def __missing__(self, name: str) -> str:
                return "{" + name + "}"

        try:
            return template.format_map(_Safe(**kwargs))
        except (ValueError, TypeError):
            return template
