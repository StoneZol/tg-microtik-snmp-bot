from __future__ import annotations

import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)


class TelegramClient:
    def __init__(self, timeout: float = 20.0) -> None:
        self._client = httpx.AsyncClient(timeout=timeout)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def send_message(self, token: str, chat_id: str, text: str) -> None:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        response = await self._client.post(
            url,
            json={
                "chat_id": chat_id,
                "text": text,
                "disable_web_page_preview": True,
            },
        )
        if response.status_code >= 400:
            log.error(
                "Telegram send failed (%s): %s",
                response.status_code,
                response.text[:300],
            )
            response.raise_for_status()

    async def notify_chats(
        self, token: str, chat_ids: list[str], text: str
    ) -> None:
        if not chat_ids:
            log.warning("No subscribers to notify for this bot push")
            return

        errors: list[str] = []
        for chat_id in chat_ids:
            try:
                await self.send_message(token, chat_id, text)
            except Exception as exc:  # noqa: BLE001 — fan-out must continue
                errors.append(f"{chat_id}: {exc}")
                log.exception("Failed to notify chat %s", chat_id)
        if errors and len(errors) == len(chat_ids):
            raise RuntimeError("; ".join(errors))

    async def get_updates(
        self, token: str, offset: int | None = None, timeout: int = 25
    ) -> list[dict[str, Any]]:
        url = f"https://api.telegram.org/bot{token}/getUpdates"
        params: dict[str, Any] = {"timeout": timeout}
        if offset is not None:
            params["offset"] = offset
        response = await self._client.get(url, params=params, timeout=timeout + 10)
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            raise RuntimeError(f"getUpdates failed: {payload}")
        return list(payload.get("result") or [])
