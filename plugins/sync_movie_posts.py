"""Reconcile channel movie posts with movie_updates using message IDs.

Bots cannot enumerate channel history with messages.GetHistory, but they can fetch
known channel messages by ID. This command uses stored IDs and optionally scans an
explicit numeric message-ID range supplied by an admin.
"""
import asyncio
import re
import urllib.parse
from datetime import datetime

from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from info import ADMINS, MOVIE_UPDATE_CHANNEL
from database.users_chats_db import db
from database.ia_filterdb import get_search_results
from utils import temp
from plugins.post_files import post_token, decode_post_token

_SIDEcar = re.compile(r"(?:^|[.\s])(srt|zip|rar|7z|7zip|ass|ssa|sub|vtt)(?:$|[.\s])", re.I)


def _html_text(value):
    value = re.sub(r"<[^>]+>", " ", str(value or ""))
    return re.sub(r"\s+", " ", value).strip()


def _title_from_message(message):
    markup = getattr(message, "reply_markup", None)
    for row in getattr(markup, "inline_keyboard", []) or []:
        for button in row:
            url = getattr(button, "url", None) or ""
            if "start=postfiles64-" in url:
                raw = url.split("start=postfiles64-", 1)[1].split("&", 1)[0]
                return decode_post_token(raw).strip()
            if "start=postfiles-" in url:
                raw = url.split("start=postfiles-", 1)[1].split("&", 1)[0]
                return urllib.parse.unquote(raw).strip()
    text = _html_text(getattr(message, "caption", None) or getattr(message, "text", None))
    first = next((x.strip() for x in text.splitlines() if x.strip()), "")
    first = re.sub(r"^[^A-Za-z0-9]+", "", first)
    first = re.sub(r"(?i)\s*(?:Sinhala Subtitles|සිංහල උපසිරැසි(?: සමඟ)?).*?$", "", first)
    first = re.sub(r"\s*\|.*$", "", first)
    return re.sub(r"\s+", " ", first).strip(" -|:")


def _post_markup(title):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "ɢᴇᴛ ғɪʟᴇs",
            url=f"https://t.me/{temp.U_NAME}?start=postfiles64-{post_token(title)}",
        )
    ]])


async def _indexed_files(title):
    try:
        files, _, _ = await get_search_results(
            None, title, max_results=1000, filter=True,
            include_subtitle_files=False, subtitle_only=False,
        )
    except Exception:
        return []
    output, seen = [], set()
    for file in files:
        name = getattr(file, "file_name", "") or ""
        if not name or name in seen or _SIDEcar.search(name):
            continue
        seen.add(name)
        output.append({
            "filename": name, "processed": name, "quality": "N/A",
            "language": "N/A", "ott_platform": "N/A",
            "timestamp": datetime.utcnow(),
            "tag": "#SERIES" if re.search(r"S\d{1,2}.*E\d{1,3}", name, re.I) else "#MOVIE",
            "season": None, "episode": None,
        })
    return output


def _status(done, total, fetched, created, updated, repaired, skipped, current):
    pct = (done / total * 100) if total else 100
    filled = min(20, int(pct // 5))
    return (
        "<b>🔄 Channel Message Reconciliation</b>\n\n"
        + "█" * filled + "░" * (20 - filled) + "\n\n"
        f"<b>Checked:</b> {done}/{total}\n"
        f"<b>Messages found:</b> {fetched}\n"
        f"<b>New database posts:</b> {created}\n"
        f"<b>Existing records updated:</b> {updated}\n"
        f"<b>Buttons repaired:</b> {repaired}\n"
        f"<b>Skipped/errors:</b> {skipped}\n"
        f"<b>Current:</b> {current or '—'}"
    )


async def _upsert_channel_message(client, item):
    """Fetch one known message, derive title, and upsert its database record."""
    title = _title_from_message(item)
    if len(title) < 2:
        return "skip"
    existing = await db.movie_updates.find_one({"message_id": int(item.id)})
    if not existing:
        existing = await db.movie_updates.find_one({"_id": title})
    files = (existing or {}).get("files") or []
    if not files:
        files = await _indexed_files(title)
    update = {
        "title": title, "message_id": int(item.id), "is_photo": bool(getattr(item, "photo", None)),
        "channel_synced": True, "channel_synced_at": datetime.utcnow(), "files": files,
    }
    if existing:
        await db.movie_updates.update_one({"_id": existing["_id"]}, {"$set": update})
        result = "updated"
    else:
        await db.movie_updates.update_one(
            {"_id": title},
            {"$setOnInsert": {
                "_id": title, "files": files, "poster_url": None, "genres": "N/A",
                "rating": "N/A", "release_date": "", "countries": "N/A",
                "languages": "N/A", "runtime": "N/A", "tagline": title,
                "year": None, "tag": "#MOVIE",
            }, "$set": update}, upsert=True,
        )
        result = "created"
    try:
        from plugins.channel import _channel_post_files, _legacy_channel_buttons
        channel_files = await _channel_post_files(update, title)
        markup = _legacy_channel_buttons(channel_files, title) or _post_markup(title)
        await client.edit_message_reply_markup(MOVIE_UPDATE_CHANNEL, int(item.id), markup)
        return result + ":button"
    except Exception:
        # Some legacy posts cannot be edited through reply-markup updates. Use
        # the normal post renderer as a safe fallback; it creates the same
        # poster/details post with the opaque postfiles64 button.
        try:
            from plugins.channel import update_movie_message
            await db.movie_updates.update_one(
                {"_id": (existing or {}).get("_id", title)},
                {"$set": {"message_id": int(item.id)}},
            )
            await update_movie_message(client, title)
            return result + ":recreated"
        except Exception:
            return result


async def _known_items(client):
    ids = set()
    async for doc in db.movie_updates.find({"message_id": {"$exists": True}}):
        try:
            ids.add(int(doc["message_id"]))
        except (TypeError, ValueError):
            pass
    if not ids:
        return []
    items = await client.get_messages(MOVIE_UPDATE_CHANNEL, list(ids))
    return [item for item in items if item and (getattr(item, "caption", None) or getattr(item, "text", None))]


@Client.on_message(filters.private & filters.command("sync_movie_posts") & filters.user(ADMINS))
async def sync_movie_posts(client, message):
    args = message.command[1:]
    if args and args[0].lower() in {"help", "usage"}:
        return await message.reply_text(
            "<b>/sync_movie_posts</b>\n\n"
            "Stored message IDs වලින් channel posts fetch කරලා title, files, database record සහ GET FILES button repair කරයි.\n\n"
            "Message ID range එකක් scan කිරීමට:\n"
            "<code>/sync_movie_posts 1 20000</code>\n\n"
            "Telegram bot එකට channel history enumerate කරන්න බැහැ; නමුත් message IDs වලින් posts fetch කළ හැක. "
            "ඒ නිසා channel එකේ first/last post IDs දීලා range scan කරන්න.\n\n"
            "ඊට පසු missing/new posts create කිරීමට <code>/rebuild_posts</code> භාවිතා කරන්න.",
            parse_mode=enums.ParseMode.HTML,
        )
    status = await message.reply_text("Channel message IDs verify කරමින්…", parse_mode=enums.ParseMode.HTML)
    items = []
    try:
        if len(args) >= 2 and args[0].isdigit() and args[1].isdigit():
            start, end = int(args[0]), int(args[1])
            if end < start or end - start > 50000:
                return await status.edit_text("❌ Range එක 1–50000 අතර විය යුතුයි.")
            for pos in range(start, end + 1, 100):
                batch = await client.get_messages(MOVIE_UPDATE_CHANNEL, list(range(pos, min(end + 1, pos + 100))))
                items.extend(x for x in batch if x and (getattr(x, "caption", None) or getattr(x, "text", None)))
        else:
            items = await _known_items(client)
    except Exception as exc:
        return await status.edit_text(f"❌ Message fetch failed: <code>{type(exc).__name__}: {exc}</code>", parse_mode=enums.ParseMode.HTML)

    total = len(items)
    fetched = created = updated = repaired = skipped = 0
    last = 0.0
    for index, item in enumerate(items, 1):
        fetched += 1
        title = _title_from_message(item)
        try:
            result = await _upsert_channel_message(client, item)
            if result == "created": created += 1
            elif result == "updated": updated += 1
            elif result == "created:button": created += 1; repaired += 1
            elif result == "updated:button": updated += 1; repaired += 1
            elif result == "created:recreated": created += 1; repaired += 1
            elif result == "updated:recreated": updated += 1; repaired += 1
            elif result == "skip": skipped += 1
            else: skipped += 1
        except Exception:
            skipped += 1
        now = asyncio.get_running_loop().time()
        if now - last >= 5 or index == total:
            try:
                await status.edit_text(_status(index, total, fetched, created, updated, repaired, skipped, title), parse_mode=enums.ParseMode.HTML)
            except Exception:
                pass
            last = now
    await status.edit_text(
        "<b>✅ Channel message reconciliation complete</b>\n\n"
        f"Messages found: <code>{fetched}</code>\n"
        f"New database posts: <code>{created}</code>\n"
        f"Existing records updated: <code>{updated}</code>\n"
        f"Buttons repaired: <code>{repaired}</code>\n"
        f"Skipped/errors: <code>{skipped}</code>\n\n"
        "Newly discovered posts databaseට වැටුණා. දැන් `/rebuild_posts` හෝ `/merge_series_posts all` අවශ්‍ය නම් run කරන්න.",
        parse_mode=enums.ParseMode.HTML,
    )
