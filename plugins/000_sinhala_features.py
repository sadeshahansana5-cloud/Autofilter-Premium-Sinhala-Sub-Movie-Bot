"""Additive features only. Original plugins, messages and buttons are intentionally untouched."""
import datetime
import asyncio
import urllib.parse
import re
import utils as _original_utils
import logging
import uuid
logger = logging.getLogger(__name__)
from pyrogram import Client, filters, enums, StopPropagation, ContinuePropagation
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from database.ia_filterdb import get_search_results
from database.users_chats_db import db
from info import ADMINS, REQST_CHANNEL, MOVIE_UPDATE_CHANNEL, LOG_CHANNEL, DELETE_TIME
from utils import get_poster, get_posterx, get_seconds, temp
from plugins.Dreamxfutures.Imdbposter import get_tmdb_details_direct

_PENDING_REQUESTS = {}

# Runtime-only filename cleanup wrapper. The original utils.py implementation remains untouched.
_original_clean_filename = _original_utils.clean_filename
def _addon_clean_filename(file_name):
    if not file_name:
        return file_name
    text = str(file_name)
    for pattern in (
        r"(?i)(?:https?://)?(?:www\.)?cinesubz\.com",
        r"(?i)cinesubz_com_?",
        r"(?i)(?:https?://)?(?:www\.)?sinhalasub\.net",
        r"(?i)@sanju_films",
        r"(?i)telegram\s*channel",
        r"(?i)sadesha\s*xbots?",
        r"(?i)\bfilms?\b",
        r"(?i)\[?cinesubz(?:\.co|\.com)?\]?",
    ):
        text = re.sub(pattern, " ", text)
    text = re.sub(r"(?i)(?:https?://|www\.)\S+|t\.me/\S+|@[A-Za-z0-9_]+", " ", text)
    text = ''.join(ch for ch in text if not (ord(ch) >= 0x1F000 or 0x2600 <= ord(ch) <= 0x27BF))
    return _original_clean_filename(re.sub(r"\s{2,}", " ", text).strip())
_original_utils.clean_filename = _addon_clean_filename

@Client.on_message(filters.private & filters.command("start"), group=-1)
async def request_start_deeplink_addon(client, message):
    if len(message.command) > 1 and message.command[1].startswith("request_"):
        message.text = "/request " + urllib.parse.unquote_plus(message.command[1][8:])
        await private_movie_request_addon(client, message)
        raise StopPropagation
    raise ContinuePropagation

@Client.on_message(filters.private & filters.command("request"), group=-1)
async def private_movie_request_addon(client, message):
    title = message.text.split(" ", 1)[1].strip() if len(message.command) > 1 else ""
    if not title:
        await message.reply_text("භාවිතය: /request Movie Name 2024")
        raise StopPropagation
    # First show a compact IMDb result list so the user can select the exact title.
    try:
        results = await get_poster(title, bulk=True)
    except Exception:
        results = None
    if results:
        _PENDING_REQUESTS[message.from_user.id] = {str(item.movieID): item for item in results[:7]}
        buttons = [[InlineKeyboardButton(str(item.get('title')), callback_data=f"sinhala_pick#tt{item.movieID}#{message.from_user.id}")] for item in results[:7]]
        buttons.append([InlineKeyboardButton("🚫 ᴄʟᴏsᴇ 🚫", callback_data="close_data")])
        await message.reply_text("IMDb results — select a movie:", reply_markup=InlineKeyboardMarkup(buttons))
        raise StopPropagation
    try:
        card = await get_posterx(title)
    except Exception:
        card = None
    if not card:
        await message.reply_text(f"{title} සඳහා IMDb result එකක් හමු වුණේ නැහැ. නැවත උත්සාහ කරන්න.")
        raise StopPropagation
    _PENDING_REQUESTS[message.from_user.id] = card
    caption = (f"<b>{card.get('title') or title}</b>\n\n"
               f"⭐ Rating: {card.get('rating') or 'N/A'}\n"
               f"📅 Year: {card.get('year') or 'N/A'}\n"
               f"📝 {card.get('plot') or 'N/A'}\n\n"
               "මෙම movie එක request කිරීමට පහත button එක ඔබන්න.")
    buttons = [[InlineKeyboardButton("Request Movie", callback_data=f"sinhala_request#{message.from_user.id}")]]
    poster = card.get("poster_url") or card.get("poster")
    if poster:
        await message.reply_photo(poster, caption=caption, reply_markup=InlineKeyboardMarkup(buttons))
    else:
        await message.reply_text(caption, reply_markup=InlineKeyboardMarkup(buttons))
    raise StopPropagation

@Client.on_callback_query(filters.regex(r"^sinhala_pick#"), group=-1)
async def pick_movie_request_addon(client, query):
    _, movie_id, owner_id = query.data.split("#")
    if int(owner_id) != query.from_user.id:
        return await query.answer("මෙය ඔබගේ request එක නොවේ.", show_alert=True)
    try:
        card = await get_posterx(movie_id, id=True)
    except Exception:
        card = None
    if not card:
        return await query.answer("Movie details හමු වුණේ නැහැ.", show_alert=True)
    _PENDING_REQUESTS[query.from_user.id] = card
    caption = (f"<b>{card.get('title') or movie_id}</b>\n\n"
               f"⭐ Rating: {card.get('rating') or 'N/A'}\n"
               f"📅 Year: {card.get('year') or 'N/A'}\n"
               f"📝 {card.get('plot') or 'N/A'}")
    buttons = [[InlineKeyboardButton("Request Movie", callback_data=f"sinhala_request#{query.from_user.id}")]]
    poster = card.get("poster_url") or card.get("poster")
    if poster:
        return await query.message.edit_media(__import__('pyrogram').types.InputMediaPhoto(poster, caption=caption), reply_markup=InlineKeyboardMarkup(buttons))
    return await query.message.edit_text(caption, reply_markup=InlineKeyboardMarkup(buttons))

@Client.on_callback_query(filters.regex(r"^sinhala_request#"), group=-1)
async def submit_movie_request_addon(client, query):
    user_id = int(query.data.split("#", 1)[1])
    if user_id != query.from_user.id:
        return await query.answer("මෙය ඔබගේ request එක නොවේ.", show_alert=True)
    card = _PENDING_REQUESTS.get(user_id)
    if not card:
        return await query.answer("Request එක කල් ඉකුත් වී ඇත. නැවත /request භාවිතා කරන්න.", show_alert=True)
    title = card.get("title") or "Unknown title"
    year = card.get("year") or ""
    poster = card.get("poster_url") or card.get("poster")
    if year and str(year) not in str(title):
        title = f"{title} ({year})"
    # Do not create duplicate requests when the indexed movie already exists.
    try:
        existing, _, total = await get_search_results(user_id, title, offset=0, filter=True)
        if total and existing:
            return await query.answer("මෙම චිත්‍රපටය දැනටමත් bot එකේ තිබේ.", show_alert=True)
    except Exception:
        pass
    # Use the original request-system message shape so the existing
    # show_option -> status-button callbacks continue to work.
    text = ("<b>📝 ʀᴇǫᴜᴇsᴛ : <u>" + str(title) + "</u>\n\n"
            f"📚 ʀᴇᴘᴏʀᴛᴇᴅ ʙʏ : {query.from_user.mention}\n"
            f"📖 ʀᴇᴘᴏʀᴛᴇʀ ɪᴅ : {user_id}\n\n</b>")
    buttons = [[
        InlineKeyboardButton("ᴠɪᴇᴡ ᴜsᴇʀ", url=f"tg://user?id={user_id}"),
        InlineKeyboardButton("ꜱʜᴏᴡ ᴏᴘᴛɪᴏɴs", callback_data=f"show_option#{user_id}")
    ]]
    markup = InlineKeyboardMarkup(buttons)
    delivered = False
    # Prefer the original request channel when configured.
    if REQST_CHANNEL:
        try:
            if poster:
                await client.send_photo(REQST_CHANNEL, photo=poster, caption=text, reply_markup=markup)
            else:
                await client.send_message(REQST_CHANNEL, text, reply_markup=markup)
            delivered = True
        except Exception:
            delivered = False
    # Keep the original direct-admin fallback if the request channel is unavailable.
    if not delivered:
        for admin in ADMINS:
            try:
                if poster:
                    await client.send_photo(admin, photo=poster, caption=text, reply_markup=markup)
                else:
                    await client.send_message(admin, text, reply_markup=markup)
                delivered = True
            except Exception:
                pass
    if delivered:
        await query.answer("Request එක admin වෙත යැව්වා.", show_alert=True)
    else:
        await query.answer("Request යැවීමට නොහැකි වුණා. Admin configuration බලන්න.", show_alert=True)

_REBUILD = {"task": None, "paused": False, "cancelled": False, "status": None, "done": 0, "total": 0}

def _rebuild_markup():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("⏸ Pause", callback_data="rebuild_pause"),
        InlineKeyboardButton("▶️ Resume", callback_data="rebuild_resume"),
        InlineKeyboardButton("✖️ Cancel", callback_data="rebuild_cancel"),
    ]])

async def _broadcast_maintenance(client, text):
    """Notify every known user and group; failures are ignored per recipient."""
    try:
        async for user in await db.get_all_users():
            try: await client.send_message(user["id"], text)
            except Exception: pass
        async for chat in await db.get_all_chats():
            try: await client.send_message(chat["id"], text)
            except Exception: pass
    except Exception:
        pass

async def _delete_old_posts(client):
    from database.users_chats_db import db as main_db
    async for doc in main_db.movie_updates.find({}):
        mid = doc.get("message_id")
        if mid:
            try: await client.delete_messages(MOVIE_UPDATE_CHANNEL, mid)
            except Exception: pass
    await main_db.movie_updates.delete_many({})

async def _begin_rebuild(client, status, mode):
    from database.ia_filterdb import Media, Media2
    from plugins.channel import extract_media_info, is_subtitle_sidecar
    if mode == "delete_all":
        await _delete_old_posts(client)
    temp.MAINTENANCE = True
    await _broadcast_maintenance(client, "🛠️ Bot maintenance ආරම්භ කළා. Search සහ file send තාවකාලිකව නවතා ඇත. කරුණාකර ටික වේලාවක් රැඳී සිටින්න.")
    try:
        models = (Media, Media2) if getattr(__import__('info'), 'MULTIPLE_DB', False) else (Media,)
        groups = {}
        for model in models:
            async for item in model.find({}):
                name = getattr(item, "file_name", "") or ""
                if is_subtitle_sidecar(name): continue
                info = extract_media_info(name, getattr(item, "caption", "") or "")
                groups.setdefault(info["base_name"], []).append((name, getattr(item, "caption", "") or ""))
        _REBUILD.update({"paused": False, "cancelled": False, "status": status})
        _REBUILD["task"] = asyncio.create_task(_rebuild_worker(client, status, groups))
    except Exception as exc:
        temp.MAINTENANCE = False
        await status.edit_text(f"❌ Rebuild error: <code>{exc}</code>")

async def _rebuild_worker(client, status, groups):
    from plugins.channel import process_and_send_update, update_movie_message, is_subtitle_sidecar
    total_files = sum(len(v) for v in groups.values())
    total_groups = len(groups); done_files = 0; done_groups = 0
    _REBUILD.update({"total": total_files, "done": 0, "cancelled": False})
    for group_name, items in groups.items():
        if _REBUILD["cancelled"]:
            break
        while _REBUILD["paused"] and not _REBUILD["cancelled"]:
            await asyncio.sleep(1)
        if _REBUILD["cancelled"]:
            break
        # Index every file in this title group without sending partial posts.
        for name, caption in items:
            if _REBUILD["cancelled"]:
                break
            await process_and_send_update(client, name, caption or "", publish=False)
            done_files += 1
            _REBUILD["done"] = done_files
        if _REBUILD["cancelled"]:
            break
        # One final post/update contains all files in this movie/series group.
        await update_movie_message(client, group_name)
        done_groups += 1
        filled = int((done_groups / total_groups) * 20) if total_groups else 20
        bar = "█" * filled + "░" * (20 - filled)
        remaining_files = total_files - done_files
        state = "⏸ Paused" if _REBUILD["paused"] else "🔄 Running"
        try:
            await status.edit_text(
                f"<b>Movie/Series Post Rebuild</b>\n\n{bar}\n\n"
                f"<b>Status:</b> {state}\n"
                f"<b>Groups posted:</b> {done_groups}/{total_groups}\n"
                f"<b>Files scanned:</b> {done_files}/{total_files}\n"
                f"<b>Files remaining:</b> {remaining_files}\n"
                f"<b>Current group:</b> {group_name}",
                reply_markup=_rebuild_markup()
            )
        except Exception:
            pass
        # Wait only after a complete post, not between every scanned file.
        await asyncio.sleep(5)
    final = (f"⚠️ Rebuild cancelled. Groups posted: {done_groups}/{total_groups}; "
             f"files scanned: {done_files}/{total_files}" if _REBUILD["cancelled"] else
             f"✅ Rebuild completed. Groups posted: {done_groups}/{total_groups}; files scanned: {done_files}/{total_files}")
    try:
        await status.edit_text(final)
    except Exception:
        pass
    temp.MAINTENANCE = False
    await _broadcast_maintenance(client, "✅ Bot maintenance අවසන්. දැන් bot එක නැවත භාවිතා කළ හැකියි.")
    _REBUILD["task"] = None

@Client.on_message(filters.private & filters.command("rebuild_posts") & filters.user(ADMINS))
async def rebuild_movie_posts(client, message):
    if _REBUILD.get("task") and not _REBUILD["task"].done():
        return await message.reply_text("Rebuild එක දැනටමත් ධාවනය වෙමින් පවතී.")
    buttons = InlineKeyboardMarkup([[
        InlineKeyboardButton("🗑️ පරණ posts අයින් කරලා සියල්ල නැවත දාන්න", callback_data="rebuild_start#delete_all")
    ],[
        InlineKeyboardButton("♻️ පරණ posts තියාගෙන නැති ඒවා පමණක් දාන්න", callback_data="rebuild_start#keep")
    ]])
    await message.reply_text("Rebuild ආරම්භ කිරීමට option එකක් තෝරන්න:", reply_markup=buttons)

@Client.on_callback_query(filters.regex(r"^rebuild_start#(delete_all|keep)$"))
async def rebuild_start_choice(client, query):
    if query.from_user.id not in ADMINS:
        return await query.answer("Admin only", show_alert=True)
    if _REBUILD.get("task") and not _REBUILD["task"].done():
        return await query.answer("Rebuild එක දැනටමත් ධාවනය වෙමින් පවතී.", show_alert=True)
    await query.answer("Maintenance started")
    await query.message.edit_text("🛠️ Maintenance ආරම්භ කළා. Database scan කරමින්...")
    await _begin_rebuild(client, query.message, query.data.split("#", 1)[1])

@Client.on_callback_query(filters.regex(r"^rebuild_(pause|resume|cancel)$"))
async def rebuild_controls(client, query):
    if query.from_user.id not in ADMINS:
        return await query.answer("Admin only", show_alert=True)
    action = query.data.split("_", 1)[1]
    if not _REBUILD.get("task") or _REBUILD["task"].done():
        return await query.answer("No rebuild is running.", show_alert=True)
    if action == "pause":
        _REBUILD["paused"] = True
    elif action == "resume":
        _REBUILD["paused"] = False
    else:
        _REBUILD["cancelled"] = True
    await query.answer(action.capitalize())
    if action == "pause":
        await query.message.edit_reply_markup(_rebuild_markup())

_REPAIR_TASK = None
_REPAIR = {"paused": False, "cancelled": False, "status": None}

def _repair_markup():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("⏸ Pause", callback_data="repair_pause"),
        InlineKeyboardButton("▶️ Resume", callback_data="repair_resume"),
        InlineKeyboardButton("✖️ Cancel", callback_data="repair_cancel"),
    ]])

def _post_link(chat, message_id, username=None):
    if not message_id: return "N/A"
    if username: return f"https://t.me/{username.lstrip('@')}/{message_id}"
    return f"tg://openmessage?chat_id={chat}&message_id={message_id}"

async def _repair_metadata_worker(client, status):
    from plugins.channel import send_movie_update, resolve_movie_details
    total = await db.movie_updates.count_documents({})
    checked = fixed = skipped = 0
    results = []
    skip_reasons = {}
    problem_rows = []
    username = None
    progress = {"state": "🔄 Starting", "current": "Preparing database scan…"}
    progress_done = False
    last_progress = 0.0

    async def show_progress(state, current="—", force=False):
        nonlocal last_progress
        progress.update(state=state, current=current)
        now = asyncio.get_running_loop().time()
        if not force and now - last_progress < 5:
            return
        last_progress = now
        remaining = max(total - checked, 0)
        filled = int((checked / total) * 20) if total else 20
        bar = "█" * filled + "░" * (20 - filled)
        text = (
            f"<b>🧹 Movie Metadata Repair</b>\n\n{bar}\n\n"
            f"<b>Status:</b> {state}\n"
            f"<b>Checked:</b> {checked}/{total}\n"
            f"<b>Remaining:</b> {remaining}\n"
            f"<b>Fixed:</b> {fixed}\n"
            f"<b>Skipped/Errors:</b> {skipped}\n"
            f"<b>Top skip reason:</b> {(max(skip_reasons, key=skip_reasons.get) if skip_reasons else '—')}\n"
            f"<b>Current:</b> <code>{current}</code>"
        )
        try:
            await status.edit_text(text, reply_markup=_repair_markup())
        except Exception:
            pass

    async def mark_skip(reason, current, state):
        nonlocal skipped
        skipped += 1
        skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
        problem_rows.append((current, str(doc.get("title") or ""), reason, "Review TMDB title/year/language and choose the correct candidate."))
        progress.update(state=state, current=current)

    await show_progress("🔄 Starting", "Preparing database scan…")
    try:
        chat = await client.get_chat(MOVIE_UPDATE_CHANNEL)
        username = getattr(chat, "username", None)
    except Exception: pass
    async def heartbeat():
        while not progress_done:
            await show_progress(progress["state"], progress["current"], force=True)
            await asyncio.sleep(5)

    heartbeat_task = asyncio.create_task(heartbeat())
    try:
        async for doc in db.movie_updates.find({}):
            while _REPAIR["paused"] and not _REPAIR["cancelled"]:
                progress.update(state="⏸ Paused", current="Waiting for Resume…")
                await asyncio.sleep(1)
            if _REPAIR["cancelled"]:
                break
            checked += 1
            base_name = str(doc.get("_id", "")).strip()
            progress.update(state="🔄 Checking", current=base_name)
            years = re.findall(r"(?<!\d)(?:19|20)\d{2}(?!\d)", base_name)
            expected_year = years[-1] if years else None
            if not expected_year:
                await mark_skip("NO_YEAR_IN_FILENAME", base_name, "⏭️ Skipped — no year")
                continue

            # A stale/missing message_id must not be treated as a valid channel post.
            old_id = doc.get("message_id")
            post_exists = False
            if old_id:
                try:
                    channel_msg = await asyncio.wait_for(
                        client.get_messages(MOVIE_UPDATE_CHANNEL, int(old_id)), timeout=10
                    )
                    post_exists = bool(channel_msg and getattr(channel_msg, "id", None))
                except Exception:
                    post_exists = False

            current_year = str(doc.get("year") or "")
            release_year = str(doc.get("release_date") or "")[:4]
            metadata_ok = current_year == expected_year and release_year in ("", expected_year)
            if metadata_ok and post_exists:
                progress.update(state="✅ Already correct", current=base_name)
                continue

            old_link = _post_link(MOVIE_UPDATE_CHANNEL, old_id, username)
            # Missing post: use the existing indexed file group and saved poster/details.
            # Only resolve TMDB when the stored poster is absent.
            details = {}
            if not metadata_ok or not doc.get("poster_url"):
                progress.update(state="🔍 Finding matching TMDB details", current=base_name)
                details = await resolve_movie_details(base_name, expected_year, file_hint=base_name)
                if not details or not (details.get("poster_url") or details.get("poster")):
                    await mark_skip("NO_MATCHING_TMDB_OR_POSTER", base_name, "⚠️ Skipped — no matching details/poster")
                    continue

            if details:
                genres = details.get("genres", doc.get("genres", "N/A"))
                if isinstance(genres, (list, tuple)): genres = ", ".join(str(x) for x in genres)
                updates = {
                    "poster_url": details.get("poster_url") or details.get("poster"),
                    "title": details.get("title") or base_name, "year": expected_year,
                    "release_date": details.get("release_date") or f"{expected_year}",
                    "rating": details.get("rating") or "N/A", "genres": genres,
                    "countries": details.get("countries") or "", "languages": details.get("languages") or "",
                    "runtime": details.get("runtime") or "", "tagline": details.get("tagline") or "",
                    "tmdb_url": details.get("tmdb_url") or details.get("url") or "",
                    "message_id": None, "is_photo": False, "missing_image_logged": False
                }
                await db.movie_updates.update_one({"_id": base_name}, {"$set": updates})
            elif not post_exists:
                await db.movie_updates.update_one({"_id": base_name}, {"$set": {"message_id": None, "is_photo": False}})

            try:
                if old_id and post_exists:
                    await client.delete_messages(MOVIE_UPDATE_CHANNEL, old_id)
                    await client.send_message(LOG_CHANNEL, f"🗑️ Wrong/missing movie post replaced\n<b>Title:</b> {base_name}\n<b>Old post:</b> {old_link}", parse_mode=enums.ParseMode.HTML)
                progress.update(state="📤 Creating channel post", current=base_name)
                new_msg = await asyncio.wait_for(send_movie_update(client, base_name), timeout=30)
                if not new_msg:
                    await mark_skip("POST_CREATION_RETURNED_NONE", base_name, "⚠️ Skipped — post creation failed")
                    continue
                new_link = _post_link(MOVIE_UPDATE_CHANNEL, getattr(new_msg, "id", None), username)
                results.append((base_name, old_link, new_link))
                fixed += 1
                await client.send_message(LOG_CHANNEL, f"✅ Movie post created/repaired\n<b>Title:</b> {base_name}\n<b>Old:</b> {old_link}\n<b>New:</b> {new_link}", parse_mode=enums.ParseMode.HTML)
                progress.update(state="✅ Post created", current=base_name)
            except Exception as exc:
                await mark_skip(f"POST_ERROR_{type(exc).__name__}", base_name, "⚠️ Skipped — post error")
                try:
                    await client.send_message(LOG_CHANNEL, f"❌ Repair skipped\n<b>Title:</b> {base_name}\n<code>{exc}</code>", parse_mode=enums.ParseMode.HTML)
                except Exception:
                    pass
    finally:
        progress_done = True
        heartbeat_task.cancel()
        try: await heartbeat_task
        except asyncio.CancelledError: pass

    remaining = max(total - checked, 0)
    final_state = "⚠️ Metadata repair cancelled" if _REPAIR["cancelled"] else "✅ Metadata repair complete"
    try:
        lines = [f"{final_state}\nChecked: {checked}/{total}\nRemaining: {remaining}\nFixed: {fixed}\nSkipped/Errors: {skipped}", "\n<b>Skip reason breakdown:</b>"]
        for reason, count in sorted(skip_reasons.items(), key=lambda item: (-item[1], item[0])):
            lines.append(f"• <code>{reason}</code>: {count}")
        for title, old, new in results[-30:]: lines.append(f"\n<b>{title}</b>\nOld: {old}\nNew: {new}")
        await status.edit_text("\n".join(lines))
        report_path = "/tmp/metadata-repair-problems.txt"
        with open(report_path, "w", encoding="utf-8") as report:
            report.write("Metadata Repair Problems\n")
            report.write("Original database name\tCurrent stored/post title\tReason\tSuggested action\n")
            for original, current_title, reason, suggestion in problem_rows:
                report.write(f"{original}\t{current_title or '[none]'}\t{reason}\t{suggestion}\n")
        try:
            await client.send_document(status.chat.id, report_path, caption=f"🧾 Repair report: {len(problem_rows)} problematic entries")
        except Exception as report_exc:
            logger.warning("Could not send repair report: %s", report_exc)
    except Exception: pass
    temp.MAINTENANCE = False
    _REPAIR["status"] = None
    await _broadcast_maintenance(client, "✅ Metadata repair අවසන්. දැන් bot එක නැවත භාවිතා කළ හැකියි." if not _REPAIR["cancelled"] else "⚠️ Metadata repair නවතා ඇත. දැන් bot එක නැවත භාවිතා කළ හැකියි.")
    _REPAIR["cancelled"] = False
    _REPAIR["paused"] = False

@Client.on_message(filters.private & filters.command("repair_movie_posts") & filters.user(ADMINS))
async def repair_movie_posts(client, message):
    global _REPAIR_TASK
    if _REPAIR_TASK and not _REPAIR_TASK.done():
        return await message.reply_text("Metadata repair එක දැනටමත් ධාවනය වෙමින් පවතී.")
    temp.MAINTENANCE = True
    _REPAIR.update({"paused": False, "cancelled": False})
    await _broadcast_maintenance(client, "🛠️ Movie metadata repair ආරම්භ කළා. Search සහ file send තාවකාලිකව නවතා ඇත.")
    status = await message.reply_text("🧹 Wrong title/year metadata posts පරීක්ෂා කරමින්...\nකරුණාකර රැඳී සිටින්න.", reply_markup=_repair_markup())
    _REPAIR["status"] = status
    _REPAIR_TASK = asyncio.create_task(_repair_metadata_worker(client, status))

@Client.on_callback_query(filters.regex(r"^repair_(pause|resume|cancel)$"))
async def repair_controls(client, query):
    if query.from_user.id not in ADMINS:
        return await query.answer("Admin only", show_alert=True)
    if not _REPAIR_TASK or _REPAIR_TASK.done():
        return await query.answer("No metadata repair is running.", show_alert=True)
    action = query.data.split("_", 1)[1]
    if action == "pause":
        _REPAIR["paused"] = True
    elif action == "resume":
        _REPAIR["paused"] = False
    else:
        _REPAIR["cancelled"] = True
        _REPAIR["paused"] = False
    await query.answer(action.capitalize())

_SUBTITLE_SEARCHES = {}
_CORRECTION_BATCHES = {}


def _parse_correction_file(text):
    rows, invalid = [], []
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#") or line.lower().startswith("original database name"):
            continue
        parts = [part.strip() for part in line.split("\t")]
        if len(parts) < 3:
            invalid.append((number, "Expected: original_name<TAB>correct_title<TAB>correct_year<TAB>language(optional)"))
            continue
        original, title, year = parts[:3]
        language = parts[3] if len(parts) > 3 else ""
        if not original or not title or not re.fullmatch(r"(?:19|20)\d{2}", year):
            invalid.append((number, "Missing original/title or invalid four-digit year"))
            continue
        rows.append({"original": original, "title": title, "year": year, "language": language})
    return rows, invalid


def _correction_keyboard(batch_id):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ APPLY CORRECTIONS", callback_data=f"corr_apply#{batch_id}"),
        InlineKeyboardButton("✖️ CANCEL", callback_data=f"corr_cancel#{batch_id}"),
    ]])


@Client.on_message(filters.document & filters.user(ADMINS))
async def receive_correction_file(client, message):
    caption = (message.caption or "").strip().lower()
    if "/apply_repair_file" not in caption:
        return
    path = await message.download(file_name=f"/tmp/repair-corrections-{message.id}.txt")
    try:
        text = open(path, "r", encoding="utf-8", errors="replace").read()
        rows, invalid = _parse_correction_file(text)
    except Exception as exc:
        return await message.reply_text(f"❌ Correction file read failed: <code>{exc}</code>", parse_mode=enums.ParseMode.HTML)
    if not rows:
        return await message.reply_text("❌ No valid correction rows found. Use the required TSV format.", parse_mode=enums.ParseMode.HTML)
    batch_id = uuid.uuid4().hex[:10]
    _CORRECTION_BATCHES[batch_id] = {"rows": rows, "invalid": invalid, "owner": message.from_user.id}
    invalid_text = f"\n⚠️ Invalid rows skipped: {len(invalid)}" if invalid else ""
    await message.reply_text(
        f"<b>🧾 Correction file preview</b>\n\n"
        f"Valid corrections: <code>{len(rows)}</code>{invalid_text}\n\n"
        f"The bot will use the corrected title/year/language to fetch TMDB details, replace the wrong post, and save the new message ID.\n\n"
        f"Choose <b>APPLY</b> only after checking the corrected file.",
        reply_markup=_correction_keyboard(batch_id), parse_mode=enums.ParseMode.HTML
    )


@Client.on_callback_query(filters.regex(r"^corr_(apply|cancel)#"))
async def correction_file_controls(client, query):
    if query.from_user.id not in ADMINS:
        return await query.answer("Admin only", show_alert=True)
    action, batch_id = query.data.split("#", 1)
    batch = _CORRECTION_BATCHES.get(batch_id)
    if not batch:
        return await query.answer("This correction preview has expired.", show_alert=True)
    if action == "corr_cancel":
        _CORRECTION_BATCHES.pop(batch_id, None)
        return await query.message.edit_text("✖️ Correction apply cancelled.")
    await query.answer("Applying corrections…")
    await query.message.edit_text(f"🛠️ Applying <code>{len(batch['rows'])}</code> corrections…", parse_mode=enums.ParseMode.HTML)
    from plugins.channel import send_movie_update
    fixed = skipped = 0
    failures = []
    temp.MAINTENANCE = True
    try:
        for row in batch["rows"]:
            original, title, year, language = row["original"], row["title"], row["year"], row["language"]
            doc = await db.movie_updates.find_one({"_id": original})
            if not doc:
                skipped += 1; failures.append(f"{original}\tNOT_FOUND"); continue
            details = await get_tmdb_details_direct(title, expected_year=year, file_hint=f"{title} {year} {language}")
            if not details or not (details.get("poster_url") or details.get("poster")):
                skipped += 1; failures.append(f"{original}\tTMDB_NOT_FOUND"); continue
            old_id = doc.get("message_id")
            updates = {
                "title": details.get("title") or title, "year": year,
                "release_date": details.get("release_date") or year,
                "poster_url": details.get("poster_url") or details.get("poster"),
                "rating": details.get("rating") or "N/A", "genres": details.get("genres") or doc.get("genres", "N/A"),
                "countries": details.get("countries") or doc.get("countries", ""),
                "languages": details.get("languages") or doc.get("languages", ""),
                "runtime": details.get("runtime") or doc.get("runtime", ""),
                "tagline": details.get("tagline") or doc.get("tagline", ""),
                "tmdb_url": details.get("tmdb_url") or details.get("url") or "",
                "message_id": None, "is_photo": False, "missing_image_logged": False
            }
            if old_id:
                try: await client.delete_messages(MOVIE_UPDATE_CHANNEL, old_id)
                except Exception: pass
            await db.movie_updates.update_one({"_id": original}, {"$set": updates})
            new_msg = await asyncio.wait_for(send_movie_update(client, original), timeout=30)
            if new_msg: fixed += 1
            else: skipped += 1; failures.append(f"{original}\tPOST_CREATE_FAILED")
    except Exception as exc:
        failures.append(f"WORKER_ERROR\t{type(exc).__name__}: {exc}")
    finally:
        temp.MAINTENANCE = False
        _CORRECTION_BATCHES.pop(batch_id, None)
    report = f"✅ Applied: {fixed}\n⚠️ Skipped: {skipped}"
    if failures:
        report += "\n\n<b>Failures:</b>\n" + "\n".join(f"• <code>{x}</code>" for x in failures[:30])
    await query.message.edit_text(report, parse_mode=enums.ParseMode.HTML)


async def _subtitle_page_keyboard(files, owner_id, search_key, offset, next_offset, total_results):
    buttons = [[InlineKeyboardButton(
        f"{_original_utils.clean_filename(getattr(f, 'file_name', 'subtitle'))[:55]}",
        callback_data=f"subfile#{f.file_id}",
    )] for f in files]
    if await db.has_premium_access(owner_id) and next_offset not in ("", None, 0):
        buttons.append([
            *(([InlineKeyboardButton("⋞ ʙᴀᴄᴋ", callback_data=f"subpage#{owner_id}#{search_key}#{max(0, int(offset) - 7)}")] if int(offset) > 0 else [])),
            InlineKeyboardButton(f"{int(offset) // 7 + 1}/{(int(total_results) + 6) // 7}", callback_data="pages"),
            InlineKeyboardButton("ɴᴇxᴛ ⋟", callback_data=f"subpage#{owner_id}#{search_key}#{next_offset}"),
        ])
    elif await db.has_premium_access(owner_id) and int(offset) > 0:
        buttons.append([
            InlineKeyboardButton("⋞ ʙᴀᴄᴋ", callback_data=f"subpage#{owner_id}#{search_key}#{max(0, int(offset) - 7)}"),
            InlineKeyboardButton(f"{int(offset) // 7 + 1}/{(int(total_results) + 6) // 7}", callback_data="pages"),
        ])
    return InlineKeyboardMarkup(buttons)

async def _expire_subtitle_result(result_message, key):
    try:
        await asyncio.sleep(DELETE_TIME)
        try: await result_message.delete()
        except Exception: pass
        _SUBTITLE_SEARCHES.pop(key, None)
    except Exception: pass

@Client.on_message(filters.command("sub"))
async def subtitle_search_addon(client, message):
    title = message.text.split(" ", 1)[1].strip() if len(message.command) > 1 else ""
    if not title:
        return await message.reply_text("භාවිතය: /sub Movie Name 2024")
    files, next_offset, total_results = await get_search_results(
        message.chat.id, title, max_results=7, offset=0, filter=True,
        include_subtitle_files=True, subtitle_only=True,
    )
    if not files:
        return await message.reply_text(f"{title} සඳහා subtitle file එකක් හමු වුණේ නැහැ.")
    key = str(__import__('time').time_ns())
    _SUBTITLE_SEARCHES[key] = {"chat_id": message.chat.id, "title": title}
    markup = await _subtitle_page_keyboard(files, message.from_user.id, key, 0, next_offset, total_results)
    result_message = await message.reply_text("Subtitle files හමු වුණා. Download කිරීමට file එක තෝරන්න:", reply_markup=markup)
    asyncio.create_task(_expire_subtitle_result(result_message, key))
    return result_message

@Client.on_callback_query(filters.regex(r"^subpage#"))
async def subtitle_page_addon(client, query):
    _, owner_id, key, offset = query.data.split("#", 3)
    if int(owner_id) != query.from_user.id:
        return await query.answer("මෙය ඔබගේ subtitle search එක නොවේ.", show_alert=True)
    if not await db.has_premium_access(query.from_user.id):
        return await query.answer("Subtitle pagination premium users සඳහා පමණයි.", show_alert=True)
    state = _SUBTITLE_SEARCHES.get(key)
    if not state:
        return await query.answer("Search එක කල් ඉකුත් වී ඇත. නැවත /sub භාවිතා කරන්න.", show_alert=True)
    files, next_offset, total_results = await get_search_results(
        state["chat_id"], state["title"], max_results=7, offset=int(offset),
        filter=True, include_subtitle_files=True, subtitle_only=True,
    )
    if not files:
        return await query.answer("තවත් subtitle files හමු වුණේ නැහැ.", show_alert=True)
    markup = await _subtitle_page_keyboard(files, query.from_user.id, key, int(offset), next_offset, total_results)
    await query.message.edit_reply_markup(reply_markup=markup)
    await query.answer()

@Client.on_callback_query(filters.regex(r"^subfile#"))
async def subtitle_file_download_addon(client, query):
    ok, used, limit = await _addon_consume_daily_quota(query.from_user.id, 1, bucket="subtitle")
    if not ok:
        return await query.answer(f"Subtitle daily limit reached: {used}/{limit}", show_alert=True)
    file_id = query.data.split("#", 1)[1]
    return await query.answer(url=f"https://t.me/{temp.U_NAME}?start=file_0_{file_id}")

@Client.on_message(filters.private & filters.command("limitations") & filters.user(ADMINS))
async def addon_limitations(client, message):
    args = message.command[1:]
    if not args:
        free = await db.get_bot_setting(0, "FREE_DAILY_LIMIT", 5)
        premium = await db.get_bot_setting(0, "PREMIUM_DAILY_LIMIT", 50)
        subtitle = min(int(await db.get_bot_setting(0, "SUBTITLE_DAILY_LIMIT", 20)), 20)
        return await message.reply_text(f"Daily limits\nFree: {free}\nPremium: {premium}\nSubtitle: {subtitle}\n\nභාවිතය: /limitations 5 50 20")
    if len(args) not in (2, 3) or not all(x.isdigit() for x in args):
        return await message.reply_text("භාවිතය: /limitations free_limit premium_limit [subtitle_limit]")
    await db.update_bot_setting(0, "FREE_DAILY_LIMIT", int(args[0]))
    await db.update_bot_setting(0, "PREMIUM_DAILY_LIMIT", int(args[1]))
    if len(args) == 3:
        await db.update_bot_setting(0, "SUBTITLE_DAILY_LIMIT", min(int(args[2]), 20))
    await message.reply_text(f"Daily limits update කළා. Free: {args[0]}, Premium: {args[1]}, Subtitle: {min(int(args[2]), 20) if len(args) == 3 else 20}")

async def _addon_consume_daily_quota(user_id, amount=1, bucket="download"):
    try:
        today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
        user = await db.users.find_one({"id": int(user_id)}) or {}
        usage = user.get("addon_daily_" + bucket, {})
        used = int(usage.get("count", 0)) if usage.get("date") == today else 0
        premium = await db.has_premium_access(int(user_id))
        limit_key = "SUBTITLE_DAILY_LIMIT" if bucket == "subtitle" else ("PREMIUM_DAILY_LIMIT" if premium else "FREE_DAILY_LIMIT")
        default_limit = 20 if bucket == "subtitle" else (50 if premium else 5)
        limit = min(int(await db.get_bot_setting(0, limit_key, default_limit)), 20) if bucket == "subtitle" else int(await db.get_bot_setting(0, limit_key, default_limit))
        if used + int(amount) > limit:
            return False, used, limit
        await db.users.update_one({"id": int(user_id)}, {"$set": {"addon_daily_" + bucket: {"date": today, "count": used + int(amount)}}}, upsert=True)
        return True, used + int(amount), limit
    except Exception:
        # Never break the original file delivery path because the optional quota store is unavailable.
        return True, 0, 0

_original_utils.consume_daily_quota = _addon_consume_daily_quota
