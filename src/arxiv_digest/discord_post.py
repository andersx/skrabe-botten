"""Post curated digests to Discord via a bot token."""

from __future__ import annotations

import asyncio
import os
from datetime import date
from typing import Any

import discord

from .discord_format import format_daily_discord


def discord_token() -> str:
    return (os.getenv("DISCORD_BOT_TOKEN") or os.getenv("DISCORD_TOKEN") or "").strip()


def _channel_name() -> str:
    return (os.getenv("DISCORD_CHANNEL") or "").strip().lstrip("#")


def _channel_id() -> int | None:
    raw = os.getenv("DISCORD_CHANNEL_ID")
    if raw in (None, ""):
        return None
    return int(raw)


def _embeds_from_payload(payload: dict[str, Any]) -> list[discord.Embed]:
    embeds: list[discord.Embed] = []
    for raw in payload.get("embeds") or []:
        emb = discord.Embed(
            title=raw.get("title"),
            description=raw.get("description"),
            color=raw.get("color"),
        )
        footer = raw.get("footer") or {}
        if footer.get("text"):
            emb.set_footer(text=footer["text"])
        for field in raw.get("fields") or []:
            emb.add_field(
                name=field.get("name", "\u200b"),
                value=field.get("value", "\u200b"),
                inline=bool(field.get("inline", False)),
            )
        embeds.append(emb)
    return embeds


async def _find_channel(client: discord.Client) -> discord.abc.Messageable:
    channel_id = _channel_id()
    if channel_id is not None:
        ch = client.get_channel(channel_id)
        if ch is None:
            ch = await client.fetch_channel(channel_id)
        if ch is None:
            raise RuntimeError(
                f"Discord channel id {channel_id} not found (is the bot in that server?)."
            )
        return ch  # type: ignore[return-value]

    name = _channel_name()
    if not name:
        raise RuntimeError(
            "Set DISCORD_CHANNEL or DISCORD_CHANNEL_ID in .env so the bot knows where to post."
        )
    matches = [
        ch
        for ch in client.get_all_channels()
        if isinstance(ch, discord.TextChannel) and ch.name == name
    ]
    if not matches:
        raise RuntimeError(
            f"No text channel named #{name} visible to the bot. "
            "Invite the bot to the server and ensure it can see the channel, "
            "or set DISCORD_CHANNEL_ID in .env."
        )
    if len(matches) > 1:
        guilds = ", ".join(f"{ch.guild.name} ({ch.id})" for ch in matches)
        raise RuntimeError(
            f"Multiple #{name} channels found: {guilds}. Set DISCORD_CHANNEL_ID in .env."
        )
    return matches[0]


async def _post_async(
    cfg: dict[str, Any],
    day: date | str,
    papers: list[dict[str, Any]],
    curations: list[dict[str, Any]],
    usage: dict[str, Any],
    *,
    format_kwargs: dict[str, Any] | None = None,
) -> list[int]:
    token = discord_token()
    if not token:
        raise RuntimeError("Missing Discord bot token. Set DISCORD_BOT_TOKEN in .env.")

    payloads = format_daily_discord(
        day,
        papers,
        curations,
        usage,
        cfg.get("section_order", []),
        **(format_kwargs or {}),
    )

    intents = discord.Intents.default()
    client = discord.Client(intents=intents)
    message_ids: list[int] = []
    error: list[BaseException] = []

    @client.event
    async def on_ready() -> None:
        try:
            channel = await _find_channel(client)
            for payload in payloads:
                content = (payload.get("content") or "").strip()
                raw_embeds = payload.get("embeds") or []
                embeds = _embeds_from_payload(payload) if raw_embeds else []
                if not content and not embeds:
                    continue
                msg = await channel.send(
                    content=content or None,
                    embeds=embeds or None,  # type: ignore[arg-type]
                )
                message_ids.append(msg.id)
                ch_label = getattr(channel, "name", str(getattr(channel, "id", "?")))
                print(f"Posted Discord digest to #{ch_label} (message id {msg.id})")
        except BaseException as exc:  # noqa: BLE001 — surface to caller after close
            error.append(exc)
        finally:
            await client.close()

    try:
        await client.start(token)
    except discord.LoginFailure as exc:
        raise RuntimeError("Discord login failed — check DISCORD_BOT_TOKEN in .env.") from exc

    if error:
        raise error[0]
    if not message_ids:
        raise RuntimeError("Discord client exited without posting a message.")
    return message_ids


def post_digest(
    cfg: dict[str, Any],
    day: date | str,
    papers: list[dict[str, Any]],
    curations: list[dict[str, Any]],
    usage: dict[str, Any],
    *,
    format_kwargs: dict[str, Any] | None = None,
) -> list[int]:
    """Post the daily digest to Discord. Returns message ids."""
    return asyncio.run(
        _post_async(cfg, day, papers, curations, usage, format_kwargs=format_kwargs)
    )
