from __future__ import annotations

from dataclasses import dataclass


def normalize_username(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip().lstrip("@").lower()
    return cleaned or None


@dataclass(frozen=True)
class WhitelistACL:
    """ACL entry: match by Telegram user id and/or username (@optional)."""

    user_ids: frozenset[int]
    usernames: frozenset[str]  # lowercase, without @

    def allows(self, user_id: int | None, username: str | None) -> bool:
        if user_id is not None and user_id in self.user_ids:
            return True
        norm = normalize_username(username)
        if norm and norm in self.usernames:
            return True
        return False

    def summary(self) -> str:
        parts: list[str] = []
        if self.user_ids:
            parts.extend(str(i) for i in sorted(self.user_ids))
        if self.usernames:
            parts.extend(f"@{u}" for u in sorted(self.usernames))
        return ", ".join(parts) if parts else "(empty)"


def parse_whitelist_entries(raw: list[object]) -> WhitelistACL:
    user_ids: set[int] = set()
    usernames: set[str] = set()

    for item in raw:
        if isinstance(item, bool):
            raise ValueError(f"invalid whitelist entry: {item!r}")
        if isinstance(item, int):
            user_ids.add(item)
            continue
        if isinstance(item, float) and item.is_integer():
            user_ids.add(int(item))
            continue
        if isinstance(item, str):
            text = item.strip()
            if not text:
                raise ValueError("empty whitelist entry")
            if text.isdigit() or (text.startswith("-") and text[1:].isdigit()):
                user_ids.add(int(text))
                continue
            norm = normalize_username(text)
            if not norm:
                raise ValueError(f"invalid whitelist username: {item!r}")
            usernames.add(norm)
            continue
        raise ValueError(f"invalid whitelist entry (want id or @username): {item!r}")

    return WhitelistACL(
        user_ids=frozenset(user_ids),
        usernames=frozenset(usernames),
    )
