"""Additive features only. Original plugins, messages and buttons are intentionally untouched."""
import datetime
import asyncio
import urllib.parse
import re
import utils as _original_utils
from pyrogram import Client, filters, StopPropagation, ContinuePropagation
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from database.ia_filterdb import get_search_results
from database.users_chats_db import db
from info import ADMINS, REQST_CHANNEL, MOVIE_UPDATE_CHANNEL
from utils import get_poster, get_posterx, get_seconds, temp

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

_SUBTITLE_SEARCHES = {}

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
    return await message.reply_text("Subtitle files හමු වුණා. Download කිරීමට file එක තෝරන්න:", reply_markup=markup)

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
