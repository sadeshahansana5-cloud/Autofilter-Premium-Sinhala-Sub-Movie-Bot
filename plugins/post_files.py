"""Delivery flow for movie/series channel-post buttons."""
import asyncio
import base64
import logging
import re
import urllib.parse

from pyrogram import Client, filters, enums, StopPropagation
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from database.users_chats_db import db
from database.ia_filterdb import Media, Media2, get_search_results, get_file_details
from info import ADMINS, DELETE_TIME, CUSTOM_FILE_CAPTION, PROTECT_CONTENT, UPDATE_CHNL_LNK, MULTIPLE_DB
from utils import temp, get_size, clean_filename, get_time
from Script import script

_POST_FILES = {}
_PAGE_SIZE = 7
_SIDEcar = re.compile(r"(?:^|[.\s])(srt|zip|rar|7z|7zip|ass|ssa|sub|vtt)(?:$|[.\s])", re.I)
logger = logging.getLogger(__name__)


def _decode(value):
    return urllib.parse.unquote(str(value or ""))


def _encode(value):
    return base64.urlsafe_b64encode(str(value).encode()).decode().rstrip("=")


def post_token(title):
    """Return a compact opaque token for channel buttons."""
    return _encode(title)


def decode_post_token(value):
    try:
        padded = str(value).replace("-", "+").replace("_", "/")
        padded += "=" * (-len(padded) % 4)
        return base64.b64decode(padded).decode("utf-8")
    except Exception:
        return ""


def _tokens(value):
    return [x.lower() for x in re.findall(r"[a-z0-9]+", str(value or ""), re.I)]


async def _lookup_files(names):
    found, seen_ids = [], set()
    models = (Media, Media2) if MULTIPLE_DB else (Media,)
    for name in names:
        if not name:
            continue
        tokens = _tokens(name)
        if not tokens:
            continue
        # The index normally stores cleaned names. Match all tokens while
        # accepting punctuation/underscore/dot differences.
        pattern = re.compile(r"^\s*" + r"\s+".join(map(re.escape, tokens)) + r"\s*$", re.I)
        for model in models:
            doc = await model.find_one({"file_name": pattern})
            if doc and doc.file_id not in seen_ids:
                found.append(doc)
                seen_ids.add(doc.file_id)
                break
    # Legacy movie_updates records can contain names that do not exactly match
    # the cleaned index. Fall back to the normal index search by each name.
    if not found:
        for name in names:
            if not name:
                continue
            try:
                candidates, _, _ = await get_search_results(
                    None, str(name), max_results=1000, filter=True,
                    include_subtitle_files=False, subtitle_only=False,
                )
            except Exception:
                candidates = []
            for doc in candidates:
                if doc.file_id not in seen_ids and not _SIDEcar.search(doc.file_name or ""):
                    found.append(doc)
                    seen_ids.add(doc.file_id)
    return found


async def _files_for_post(doc, base_name):
    names = []
    for item in doc.get("files", []) or []:
        if isinstance(item, dict):
            names.extend((item.get("filename"), item.get("file_name"), item.get("name")))
        else:
            names.append(item)
    files = await _lookup_files(names)
    return files or await _lookup_files([base_name])


def _file_button(file):
    return [InlineKeyboardButton(
        f"🔗 {get_size(file.file_size)} ≽ {clean_filename(file.file_name)[:55]}",
        url=f"https://t.me/{temp.U_NAME}?start=file_0_{file.file_id}",
    )]


def _page_markup(files, key, offset):
    buttons = [_file_button(file) for file in files[offset:offset + _PAGE_SIZE]]
    total = len(files)
    pages = max(1, (total + _PAGE_SIZE - 1) // _PAGE_SIZE)
    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton("⋞ ʙᴀᴄᴋ", callback_data=f"postpage#{key}#{max(0, offset - _PAGE_SIZE)}"))
    if offset + _PAGE_SIZE < total:
        nav.append(InlineKeyboardButton("ɴᴇxᴛ ⋟", callback_data=f"postpage#{key}#{offset + _PAGE_SIZE}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(f"{offset // _PAGE_SIZE + 1}/{pages}", callback_data="pages")])
    return InlineKeyboardMarkup(buttons)


async def _expiry_warning(message):
    return await message.reply_text(script.DEL_MSG.format(get_time(DELETE_TIME)), parse_mode=enums.ParseMode.HTML)


async def _send_one(client, message, file):
    # Resolve through the same helper used by the original /start file route.
    # This is important for old records where the stored media reference is
    # not identical to the filename stored in movie_updates.
    resolved = await get_file_details(file.file_id)
    if resolved:
        file = resolved[0]
    file_id = file.file_id
    caption = clean_filename(file.file_name)
    try:
        caption = CUSTOM_FILE_CAPTION.format(file_name=caption, file_size=get_size(file.file_size), file_caption="")
    except Exception:
        pass
    buttons = [[InlineKeyboardButton("📌 ᴊᴏɪɴ ᴜᴘᴅᴀᴛᴇꜱ ᴄʜᴀɴɴᴇʟ 📌", url=UPDATE_CHNL_LNK)]]
    # Send to the user who opened the postfiles deep link, not to the channel.
    sent = await client.send_cached_media(
        chat_id=message.from_user.id, file_id=file_id, caption=caption,
        protect_content=PROTECT_CONTENT, reply_markup=InlineKeyboardMarkup(buttons),
    )
    warning = await _expiry_warning(sent)

    async def remove_later():
        await asyncio.sleep(DELETE_TIME)
        try:
            await sent.delete()
        except Exception:
            pass
        try:
            await warning.edit_text(
                "<b>ʏᴏᴜʀ ᴠɪᴅᴇᴏ / ғɪʟᴇ ɪs sᴜᴄᴄᴇssғᴜʟʟʏ ᴅᴇʟᴇᴛᴇᴅ !!</b>\n\n"
                "<b>ඔබගේ file/video එක සාර්ථකව මකා ඇත.</b>", parse_mode=enums.ParseMode.HTML,
            )
        except Exception:
            pass
    asyncio.create_task(remove_later())


async def _expire_post_list(key, result_message):
    await asyncio.sleep(DELETE_TIME)
    _POST_FILES.pop(key, None)
    try:
        await result_message.delete()
    except Exception:
        pass


@Client.on_message(filters.private & filters.command("start"), group=-2)
async def post_files_start(client, message):
    if len(message.command) < 2 or not message.command[1].startswith(("postfiles-", "postfiles64-")):
        return
    argument = message.command[1].split("-", 1)[1]
    if message.command[1].startswith("postfiles64-"):
        base_name = decode_post_token(argument).strip()
    else:
        # Backward compatibility for already-published title links.
        base_name = _decode(argument).strip()
    if not base_name:
        await message.reply_text("❌ Post title එක හඳුනාගත නොහැක.")
        raise StopPropagation
    doc = await db.movie_updates.find_one({"_id": base_name})
    if not doc:
        doc = await db.movie_updates.find_one({"title": base_name})
    if not doc:
        await message.reply_text("❌ මෙම post එකට database record එක හමු වුණේ නැහැ.")
        raise StopPropagation
    try:
        files = await _files_for_post(doc, base_name)
    except Exception as exc:
        logger.exception("Post file lookup failed for %s", base_name)
        await message.reply_text("❌ File database lookup එක අසාර්ථක වුණා. Adminට logs බලන්න කියන්න.")
        raise StopPropagation
    if not files:
        await message.reply_text("❌ මෙම post එකට indexed files හමු වුණේ නැහැ. Adminට post sync/rebuild කරන්න කියන්න.")
        raise StopPropagation
    if len(files) == 1:
        try:
            await _send_one(client, message, files[0])
        except Exception as exc:
            logger.exception("Single file delivery failed for %s", base_name)
            await message.reply_text("❌ File එක send කිරීමට නොහැකි වුණා. File database ID එක නැවත sync කරන්න.")
        raise StopPropagation
    key = _encode(base_name)
    _POST_FILES[key] = files
    result_message = await message.reply_text(
        f"<b>{clean_filename(base_name)}</b>\n\nFiles: <code>{len(files)}</code>\nFile එක තෝරන්න:",
        reply_markup=_page_markup(files, key, 0), parse_mode=enums.ParseMode.HTML,
    )
    asyncio.create_task(_expire_post_list(key, result_message))
    raise StopPropagation


@Client.on_callback_query(filters.regex(r"^postpage#"), group=-1)
async def post_files_page(client, query):
    try:
        _, key, raw_offset = query.data.split("#", 2)
        offset = max(0, int(raw_offset))
    except (ValueError, TypeError):
        return await query.answer("Invalid page.", show_alert=True)
    files = _POST_FILES.get(key)
    if not files:
        try:
            padded = key + "=" * (-len(key) % 4)
            base_name = base64.urlsafe_b64decode(padded).decode("utf-8")
            doc = await db.movie_updates.find_one({"_id": base_name}) or await db.movie_updates.find_one({"title": base_name})
            files = await _files_for_post(doc, base_name) if doc else []
            if files:
                _POST_FILES[key] = files
        except Exception:
            files = []
        if not files:
            return await query.answer("මෙම file list එක කල් ඉකුත් වී ඇත. Post එකෙන් නැවත උත්සාහ කරන්න.", show_alert=True)
    await query.message.edit_reply_markup(_page_markup(files, key, offset))
    await query.answer()
