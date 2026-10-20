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

    async def set_my_commands(
        self, token: str, commands: list[dict[str, str]]
    ) -> None:
        url = f"https://api.telegram.org/bot{token}/setMyCommands"
        response = await self._client.post(url, json={"commands": commands})
        if response.status_code >= 400:
            log.error(
                "setMyCommands failed (%s): %s",
                response.status_code,
                response.text[:300],
            )
            response.raise_for_status()

    async def send_message(
        self,
        token: str,
        chat_id: str,
        text: str,
        *,
        reply_markup: dict[str, Any] | None = None,
        parse_mode: str | None = None,
    ) -> None:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        if parse_mode:
            payload["parse_mode"] = parse_mode
        response = await self._client.post(url, json=payload)
        if response.status_code >= 400:
            log.error(
                "Telegram send failed (%s): %s",
                response.status_code,
                response.text[:300],
            )
            response.raise_for_status()

    async def notify_chats(
        self,
        token: str,
        chat_ids: list[str],
        text: str,
        *,
        parse_mode: str | None = None,
    ) -> None:
        if not chat_ids:
            log.warning("No subscribers to notify for this bot push")
            return

        errors: list[str] = []
        for chat_id in chat_ids:
            try:
                await self.send_message(
                    token, chat_id, text, parse_mode=parse_mode
                )
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
