"""Admin tool for merging separate episode posts into one series post."""
import asyncio
import logging
import re
import uuid

from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from database.users_chats_db import db
from info import ADMINS, MOVIE_UPDATE_CHANNEL
from utils import temp

logger = logging.getLogger(__name__)
_BATCHES = {}
_TASK = None


def _key(value):
    value = str(value or "")
    value = re.sub(r"(?i)\b(?:copy\s+of|sanju|com|sinhalasub|sinhalenmovie|mp4|mkv|web[- ]?dl|webrip|bluray|720p|1080p|x264|x265|sinhala|subtitles?)\b", " ", value)
    value = re.sub(r"(?i)\b(?:S\d{1,2}\s*(?:E|EP)\d{1,3}|SE\d{1,2}\s*(?:E|EP)?\d{1,3}|S\d{1,2}S\d{1,2}|EP\d{1,3})\b", " ", value)
    value = re.sub(r"(?<!\d)(?:19|20)\d{2}(?!\d)", " ", value)
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _display(value):
    value = re.sub(r"(?i)\b(?:S\d{1,2}\s*(?:E|EP)\d{1,3}|SE\d{1,2}\s*(?:E|EP)?\d{1,3}|S\d{1,2}S\d{1,2}|EP\d{1,3})\b", " ", str(value or ""))
    value = re.sub(r"(?i)\b(?:19|20)\d{2}\b", " ", value)
    value = re.sub(r"(?i)\b(?:copy\s+of|sanju|com|sinhalasub|sinhalenmovie|mp4|mkv|sinhala|subtitles?)\b", " ", value)
    return re.sub(r"\s+", " ", value).strip(" -_[]()").title() or "Unknown Series"


def _is_episode(doc):
    hay = str(doc.get("_id", "")) + " " + " ".join(str(f.get("filename", "")) for f in doc.get("files", []) or [])
    return bool(re.search(r"(?i)\b(?:S\d{1,2}\s*(?:E|EP)\d{1,3}|SE\d{1,2}\s*(?:E|EP)?\d{1,3}|S\d{1,2}S\d{1,2}|EP\d{1,3})\b", hay)) or any(f.get("season") and f.get("episode") for f in doc.get("files", []) or [])


async def _groups(target=""):
    grouped = {}
    wanted = _key(target)
    async for doc in db.movie_updates.find({}):
        if not target and not _is_episode(doc):
            continue
        source = str(doc.get("title") or doc.get("_id") or "")
        key = _key(source)
        if key and (not wanted or wanted in key or key in wanted):
            grouped.setdefault(key, {"source": source, "docs": []})["docs"].append(doc)
    return [x for x in grouped.values() if target or len(x["docs"]) > 1]


async def _resolve(group):
    from plugins.channel import resolve_movie_details
    candidate = _display(group["source"])
    years = []
    for doc in group["docs"]:
        for value in (doc.get("year"), doc.get("release_date")):
            years += re.findall(r"(?<!\d)(?:19|20)\d{2}(?!\d)", str(value or ""))
    expected = max(set(years), key=years.count) if years else None
    details = await resolve_movie_details(candidate, expected_year=expected, file_hint=candidate)
    return {"title": details.get("title") or candidate, "year": str(details.get("year") or expected or ""), "details": details}


def _buttons(batch_id):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ MERGE / REBUILD", callback_data=f"seriesmerge_apply#{batch_id}"),
        InlineKeyboardButton("✖️ CANCEL", callback_data=f"seriesmerge_cancel#{batch_id}"),
    ]])


@Client.on_message(filters.private & filters.command("merge_series_posts") & filters.user(ADMINS))
async def merge_series_posts(client, message):
    global _TASK
    if _TASK and not _TASK.done():
        return await message.reply_text("🛠️ Series merge එක දැනටමත් ධාවනය වෙමින් පවතී.")
    target = message.text.split(maxsplit=1)[1].strip() if len(message.command) > 1 else ""
    if target.lower() == "all":
        target = ""
    if target.lower() in {"help", "usage", "?"}:
        return await message.reply_text(
            "<b>🧩 Series Post Merge භාවිතය</b>\n\n"
            "<code>/merge_series_posts all</code> — සියලු episode groups preview\n"
            "<code>/merge_series_posts Destined With You 2023</code> — එක් series එකක් පමණක් preview\n\n"
            "Preview එක පරීක්ෂා කර <b>MERGE / REBUILD</b> ඔබන්න. Individual episode posts delete කර files එකම TMDB post එකකට එකතු කරයි.\n"
            "වැරදි හෝ ambiguous නමක් නම් Apply නොකරන්න.", parse_mode=enums.ParseMode.HTML)
    groups = await _groups(target)
    prepared = []
    for group in groups:
        try:
            group["resolved"] = await _resolve(group)
            prepared.append(group)
        except Exception as exc:
            logger.warning("Series lookup failed: %s", exc)
    if not prepared:
        return await message.reply_text("❌ Safe series group එකක් හමු වුණේ නැහැ.")
    batch_id = uuid.uuid4().hex[:10]
    _BATCHES[batch_id] = {"groups": prepared}
    lines = ["<b>🧩 Series episode-post merge preview</b>", f"Groups: <code>{len(prepared)}</code>", ""]
    for group in prepared[:30]:
        result = group["resolved"]
        suffix = f" ({result['year']})" if result["year"] else ""
        lines.append(f"• <b>{result['title']}{suffix}</b> — {len(group['docs'])} old post(s)")
    if len(prepared) > 30:
        lines.append(f"\n…and {len(prepared) - 30} more groups")
    await message.reply_text("\n".join(lines) + "\n\nApply කිරීමට පෙර preview එක බලන්න.", reply_markup=_buttons(batch_id), parse_mode=enums.ParseMode.HTML)


@Client.on_callback_query(filters.regex(r"^seriesmerge_(apply|cancel)#"))
async def series_merge_callback(client, query):
    global _TASK
    if query.from_user.id not in ADMINS:
        return await query.answer("Admin only", show_alert=True)
    action, batch_id = query.data.split("#", 1)
    batch = _BATCHES.get(batch_id)
    if not batch:
        return await query.answer("Series merge preview expired.", show_alert=True)
    if action == "seriesmerge_cancel":
        _BATCHES.pop(batch_id, None)
        return await query.message.edit_text("✖️ Series post merge cancelled.")
    await query.answer("Series merge started")
    await query.message.edit_text("🛠️ Series posts merge කරමින්…\nකරුණාකර රැඳී සිටින්න.")
    _TASK = asyncio.create_task(_apply(client, query.message, batch_id, batch))


async def _apply(client, status, batch_id, batch):
    from plugins.channel import send_movie_update
    temp.MAINTENANCE = True
    merged = skipped = 0
    try:
        for group in batch["groups"]:
            result = group["resolved"]
            title, year, details = result["title"], result["year"], result["details"]
            canonical = f"{title} {year}".strip()
            files, seen, old_ids, old_message_ids = [], set(), [], set()
            for doc in group["docs"]:
                old_ids.append(str(doc.get("_id")))
                if doc.get("message_id"):
                    old_message_ids.add(int(doc["message_id"]))
                for item in doc.get("files", []) or []:
                    name = item.get("filename")
                    if name and name not in seen:
                        seen.add(name)
                        files.append(item)
            if not files:
                skipped += 1
                continue
            base = dict(group["docs"][0])
            base.update({
                "_id": canonical, "title": title, "year": year or base.get("year"),
                "files": files, "message_id": None, "is_photo": False,
                "poster_url": details.get("poster_url") or details.get("poster") or base.get("poster_url"),
                "release_date": details.get("release_date") or base.get("release_date", ""),
                "rating": details.get("rating") or base.get("rating", "N/A"),
                "genres": details.get("genres") or base.get("genres", "N/A"),
                "countries": details.get("countries") or base.get("countries", ""),
                "languages": details.get("languages") or base.get("languages", ""),
                "runtime": details.get("runtime") or base.get("runtime", ""),
                "tagline": details.get("tagline") or base.get("tagline", ""),
                "tmdb_url": details.get("tmdb_url") or details.get("url") or base.get("tmdb_url", ""),
            })
            await db.movie_updates.replace_one({"_id": canonical}, base, upsert=True)
            new_msg = await asyncio.wait_for(send_movie_update(client, canonical), timeout=40)
            if not new_msg:
                skipped += 1
                continue
            for message_id in old_message_ids:
                if message_id != int(new_msg.id):
                    try:
                        await client.delete_messages(MOVIE_UPDATE_CHANNEL, message_id)
                    except Exception:
                        pass
            await db.movie_updates.delete_many({"_id": {"$in": [x for x in old_ids if x != canonical]}})
            merged += 1
        await status.edit_text(f"✅ Series merge complete\n\nMerged groups: <code>{merged}</code>\nSkipped: <code>{skipped}</code>\n\nEpisode files දැන් එක post එකක පෙන්වයි.", parse_mode=enums.ParseMode.HTML)
    except Exception as exc:
        logger.exception("Series merge failed")
        await status.edit_text(f"❌ Series merge failed: <code>{type(exc).__name__}: {exc}</code>", parse_mode=enums.ParseMode.HTML)
    finally:
        temp.MAINTENANCE = False
        _BATCHES.pop(batch_id, None)
