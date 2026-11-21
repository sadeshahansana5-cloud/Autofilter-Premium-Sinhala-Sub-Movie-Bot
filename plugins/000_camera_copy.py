# ---------------------------------------------------------------------------
# Camera-copy (CAM) system  --  additive plugin, no existing file is edited.
#
#  * Admin PM search works even when /pm_search is OFF.
#  * Every file the bot sends to an admin carries admin buttons
#    (Mark as Camera Copy / Update Original File / Unmark).
#  * A file marked as CAM is NOT sent straight to normal users. They get a
#    notice with  [Get Camera Copy]  and  [Notification On]  instead.
#  * "Update Original File": admin sends the original file to the bot, or
#    searches one that is already in the database. From then on
#      - the CAM file disappears from search results,
#      - every old link / old channel post button that pointed at the CAM
#        file delivers the original (links are NOT changed, they are resolved
#        at delivery time),
#      - users get the original with an extra  [Old Camera Copy]  button,
#      - users who pressed "Notification On" are told the original is ready.
#
# It hooks the shared delivery / search functions at runtime (same technique
# 000_sinhala_features.py already uses for clean_filename) and keeps its own
# state in a new Mongo collection:  cam_files
# ---------------------------------------------------------------------------
import asyncio
import contextvars
import datetime
import html
import logging
import time

import utils as _original_utils
import database.ia_filterdb as _ia
from pyrogram import Client, filters, StopPropagation, ContinuePropagation
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from database.users_chats_db import db
from info import ADMINS, DELETE_TIME, PROTECT_CONTENT, UPDATE_CHNL_LNK, CUSTOM_FILE_CAPTION
from utils import temp

log = logging.getLogger(__name__)

_cam_col = db.db["cam_files"]

_SESSION_TTL = 900          # seconds an "Update Original File" session stays open
_PENDING_TTL = 7200         # seconds the remembered delivery options of a CAM notice live
_HIDDEN = {"ids": set(), "ts": None}   # ts None = must refresh
_CAM_SESSIONS = {}          # admin_id -> {"cam_id", "ts", "candidates"}
_CAM_PENDING = {}           # (user_id, cam_file_id) -> {"kwargs", "ts"}
_PM_ADMIN_BYPASS = contextvars.ContextVar("cam_pm_admin_bypass", default=False)
_INDEX_READY = False


# ----------------------------- small helpers ------------------------------
def _norm(data):
    if isinstance(data, bytes):
        return data.decode("utf-8", "ignore")
    return str(data or "")


def _strip_cam_rows(markup):
    """Existing buttons of a message minus any row that holds a cam_ button."""
    rows = []
    if markup is not None and getattr(markup, "inline_keyboard", None):
        for row in markup.inline_keyboard:
            if any(_norm(getattr(b, "callback_data", None)).startswith("cam_") for b in row):
                continue
            rows.append(list(row))
    return rows


def _with_rows(markup, extra_rows):
    rows = _strip_cam_rows(markup) + [list(r) for r in extra_rows]
    return InlineKeyboardMarkup(rows) if rows else None


def _title(name):
    try:
        return _original_utils.clean_filename(name) or str(name or "")
    except Exception:
        return str(name or "")


def _size(num):
    try:
        return _original_utils.get_size(num)
    except Exception:
        return str(num)


async def _ensure_indexes():
    global _INDEX_READY
    if _INDEX_READY:
        return
    try:
        await _cam_col.create_index("original_file_id")
        await _cam_col.create_index("status")
        _INDEX_READY = True
    except Exception as e:
        log.warning("cam_files index not created: %s", e)


async def _hidden_ids(force=False):
    """File ids of CAM copies that were replaced by an original (hidden from search)."""
    now = time.monotonic()
    if force or _HIDDEN["ts"] is None or now - _HIDDEN["ts"] > 20:
        try:
            docs = await _cam_col.find({"status": "replaced"}, {"_id": 1}).to_list(length=None)
            _HIDDEN["ids"] = {d["_id"] for d in docs}
            _HIDDEN["ts"] = now
        except Exception as e:
            log.warning("cam hidden-id refresh failed: %s", e)
    return _HIDDEN["ids"]


def _invalidate_hidden():
    _HIDDEN["ts"] = None


async def _cam_lookup(fid):
    """(doc of this file if it is a CAM record, list of replaced CAMs this file is the original of)"""
    try:
        doc = await _cam_col.find_one({"_id": fid})
        olds = await _cam_col.find({"original_file_id": fid, "status": "replaced"}).to_list(length=5)
        return doc, olds
    except Exception as e:
        log.warning("cam lookup failed: %s", e)
        return None, []


async def _refund_quota(user_id):
    """The notice is shown instead of a file, so give back the download that the
    original /start handler already counted."""
    try:
        today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
        await db.users.update_one(
            {"id": int(user_id), "addon_daily_download.date": today, "addon_daily_download.count": {"$gt": 0}},
            {"$inc": {"addon_daily_download.count": -1}},
        )
    except Exception as e:
        log.warning("quota refund failed: %s", e)


async def _delete_later(msg, seconds):
    try:
        await asyncio.sleep(seconds)
        await msg.delete()
    except Exception:
        pass


def _prune():
    now = time.time()
    for key in [k for k, v in _CAM_PENDING.items() if now - v["ts"] > _PENDING_TTL]:
        _CAM_PENDING.pop(key, None)
    for key in [k for k, v in _CAM_SESSIONS.items() if time.monotonic() - v["ts"] > _SESSION_TTL]:
        _CAM_SESSIONS.pop(key, None)


def _session(admin_id):
    s = _CAM_SESSIONS.get(admin_id)
    if s and time.monotonic() - s["ts"] <= _SESSION_TTL:
        return s
    _CAM_SESSIONS.pop(admin_id, None)
    return None


# ------------------------------- buttons ----------------------------------
def _admin_rows(state, fid):
    if state == "cam":
        return [
            [InlineKeyboardButton("🎬 ᴜᴘᴅᴀᴛᴇ ᴏʀɪɢɪɴᴀʟ ꜰɪʟᴇ", callback_data=f"cam_upd#{fid}")],
            [InlineKeyboardButton("♻️ ᴜɴᴍᴀʀᴋ ᴄᴀᴍᴇʀᴀ ᴄᴏᴘʏ", callback_data=f"cam_unmark#{fid}")],
        ]
    if state == "none":
        return [[InlineKeyboardButton("📼 ᴍᴀʀᴋ ᴀs ᴄᴀᴍᴇʀᴀ ᴄᴏᴘʏ", callback_data=f"cam_mark#{fid}")]]
    return []


def _old_cam_rows(olds):
    if not olds:
        return []
    if len(olds) == 1:
        return [[InlineKeyboardButton("📼 ᴏʟᴅ ᴄᴀᴍᴇʀᴀ ᴄᴏᴘʏ ɢᴇᴛ ᴋʀᴀɴɴᴀ", callback_data=f"cam_old#{olds[0]['_id']}")]]
    return [[InlineKeyboardButton(f"📼 ᴏʟᴅ ᴄᴀᴍᴇʀᴀ ᴄᴏᴘʏ #{i}", callback_data=f"cam_old#{d['_id']}")]
            for i, d in enumerate(olds, 1)]


def _notice_markup(fid, subscribed):
    notif = ("🔕 ɴᴏᴛɪꜰɪᴄᴀᴛɪᴏɴ ᴏꜰꜰ" if subscribed else "🔔 ɴᴏᴛɪꜰɪᴄᴀᴛɪᴏɴ ᴏɴ")
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📥 ɢᴇᴛ ᴄᴀᴍᴇʀᴀ ᴄᴏᴘʏ", callback_data=f"cam_get#{fid}")],
        [InlineKeyboardButton(notif, callback_data=f"cam_notif#{fid}")],
    ])


def _notice_text(name, size):
    return (
        "<b>📼 ᴄᴀᴍᴇʀᴀ ᴄᴏᴘʏ ᴀʟᴇʀᴛ</b>\n\n"
        f"<b>🎬 {html.escape(str(name))}</b>\n"
        f"<b>📦 Sɪᴢᴇ : {html.escape(str(size))}</b>\n\n"
        "<blockquote><b>මෙම චිත්‍රපටය තවමත් Camera Copy (CAM) එකක් පමණි.</b>\n\n"
        "📽 Theatre එකේදී තිරය පටිගත කළ video එකක් නිසා රූප (video) සහ ශබ්ද (audio) තත්ත්වය අඩු විය හැක.\n"
        "🔊 සමහර අවස්ථාවලදී ශබ්දය පැහැදිලි නොවීම, තිරය කැළඹීම හෝ රූපය අඳුරු වීම දැකිය හැක.\n"
        "✅ HD / Original version එක ලැබුණු විගස අපි upload කරනවා.</blockquote>\n\n"
        "<b>🔔 Original එක upload කළ විගස ඔබට දැනුම් දීමට \"Notification On\" button එක ඔබන්න.\n"
        "📥 මේ Camera Copy එකම ලබා ගැනීමට \"Get Camera Copy\" button එක ඔබන්න.</b>"
    )


# --------------------------- runtime hook installer -----------------------
def _install(owner, name, factory):
    """Wrap owner.name once. A second import of this file (bot.py loads every
    plugin a second time) reuses the existing wrapper instead of stacking."""
    current = getattr(owner, name)
    if getattr(current, "_cam_patched", False):
        return current._cam_original
    original = current
    wrapper = factory(original)
    wrapper._cam_patched = True
    wrapper._cam_original = original
    setattr(owner, name, wrapper)
    return original


# ---- search: hide CAM copies that were replaced by an original ----
def _make_search(orig):
    async def _cam_search(*args, **kwargs):
        result = await orig(*args, **kwargs)
        try:
            files, next_offset, total = result
        except Exception:
            return result
        hidden = await _hidden_ids()
        if not hidden or not files:
            return result
        kept = [f for f in files if getattr(f, "file_id", None) not in hidden]
        removed = len(files) - len(kept)
        if not removed:
            return result
        return kept, next_offset, max(0, (total or 0) - removed)
    return _cam_search


# ---- details: an old CAM id resolves to the original's record ----
def _make_details(orig):
    async def _cam_details(query):
        details = await orig(query)
        try:
            if query in await _hidden_ids():
                doc = await _cam_col.find_one({"_id": query})
                if doc and doc.get("original_file_id"):
                    replaced = await orig(doc["original_file_id"])
                    if replaced:
                        return replaced
        except Exception as e:
            log.warning("cam details resolve failed: %s", e)
        return details
    return _cam_details


async def _send_cam_notice(client, chat_id, fid, kwargs):
    details = await _ORIG_DETAILS(fid)
    name = _title(details[0].file_name) if details else "Unknown"
    size = _size(details[0].file_size) if details else "N/A"
    _prune()
    _CAM_PENDING[(chat_id, fid)] = {"kwargs": dict(kwargs), "ts": time.time()}
    await _refund_quota(chat_id)
    doc = await _cam_col.find_one({"_id": fid}) or {}
    return await client.send_message(
        chat_id, _notice_text(name, size),
        reply_markup=_notice_markup(fid, chat_id in doc.get("notify_users", [])),
    )


# ---- delivery: the single place every file sent to a user passes through ----
def _make_send(orig):
    async def _cam_send(self, chat_id, file_id, *args, **kwargs):
        try:
            if isinstance(chat_id, int) and chat_id > 0:
                is_admin = chat_id in ADMINS
                doc, olds = await _cam_lookup(file_id)
                rows = []
                if doc and doc.get("status") == "replaced":
                    original_id = doc.get("original_file_id")
                    if original_id:
                        file_id = original_id
                        olds = [doc]
                        rows = []
                elif doc and doc.get("status") == "cam":
                    if not is_admin:
                        return await _send_cam_notice(self, chat_id, file_id, kwargs)
                    rows += _admin_rows("cam", file_id)
                elif is_admin and not olds:
                    rows += _admin_rows("none", file_id)
                rows += _old_cam_rows(olds)
                if rows:
                    kwargs["reply_markup"] = _with_rows(kwargs.get("reply_markup"), rows)
            elif isinstance(chat_id, int) and file_id in await _hidden_ids():
                doc = await _cam_col.find_one({"_id": file_id})
                if doc and doc.get("original_file_id"):
                    file_id = doc["original_file_id"]
        except Exception as e:
            log.exception("cam delivery hook failed: %s", e)
        return await orig(self, chat_id, file_id, *args, **kwargs)
    return _cam_send


# ---- admin PM search even when /pm_search is off ----
def _make_pm_status(orig):
    async def _cam_pm_status(bot_id):
        if _PM_ADMIN_BYPASS.get():
            return True
        return await orig(bot_id)
    return _cam_pm_status


_ORIG_SEARCH = _install(_ia, "get_search_results", _make_search)
_ORIG_DETAILS = _install(_ia, "get_file_details", _make_details)
_ORIG_SEND = _install(Client, "send_cached_media", _make_send)
_install(db, "pm_search_status", _make_pm_status)


# ------------------------- sending a CAM file on request -------------------
async def _default_send_kwargs(fid):
    details = await _ORIG_DETAILS(fid)
    name = _title(details[0].file_name) if details else ""
    caption = name
    if details:
        try:
            caption = CUSTOM_FILE_CAPTION.format(
                file_name=name, file_size=_size(details[0].file_size),
                file_caption=details[0].caption or "",
            )
        except Exception:
            caption = name
    return {
        "caption": caption,
        "protect_content": PROTECT_CONTENT,
        "reply_markup": InlineKeyboardMarkup([[InlineKeyboardButton("📌 ᴊᴏɪɴ ᴜᴘᴅᴀᴛᴇꜱ ᴄʜᴀɴɴᴇʟ 📌", url=UPDATE_CHNL_LNK)]]),
    }


async def _deliver_cam_file(client, query, fid, use_pending):
    uid = query.from_user.id
    if not await _cam_col.find_one({"_id": fid}):
        return await query.answer("Camera copy එක සොයාගත නොහැක.", show_alert=True)
    ok, used, limit = await _original_utils.consume_daily_quota(uid)
    if not ok:
        return await query.answer(f"Daily limit reached: {used}/{limit}", show_alert=True)
    pending = _CAM_PENDING.get((uid, fid)) if use_pending else None
    kwargs = dict(pending["kwargs"]) if pending else await _default_send_kwargs(fid)
    kwargs["reply_markup"] = _with_rows(kwargs.get("reply_markup"), [])
    await query.answer("📥 ඔබගේ file එක යවනවා...")
    msg = await _ORIG_SEND(client, uid, fid, **kwargs)
    asyncio.create_task(_delete_later(msg, DELETE_TIME))


# --------------------------- applying an original --------------------------
async def _notify_subscribers(client, cam, orig_id, admin_id):
    users = list(cam.get("notify_users", []))
    if not users:
        return
    name = html.escape(_title(cam.get("file_name")))
    text = (
        "<b>🎉 ɢᴏᴏᴅ ɴᴇᴡs!</b>\n\n"
        f"<b>🎬 {name}</b>\n\n"
        "<blockquote><b>ඔබ බලා සිටි Original (HD) version එක දැන් upload කර ඇත ✅</b>\n"
        "පහත button එක ඔබා ලබා ගන්න.</blockquote>"
    )
    markup = InlineKeyboardMarkup([[InlineKeyboardButton(
        "📥 ɢᴇᴛ ᴏʀɪɢɪɴᴀʟ ꜰɪʟᴇ", url=f"https://t.me/{temp.U_NAME}?start=file_0_{orig_id}")]])
    sent = 0
    for uid in users:
        try:
            await client.send_message(uid, text, reply_markup=markup)
            sent += 1
        except Exception:
            pass
        await asyncio.sleep(0.05)
    try:
        await client.send_message(admin_id, f"<b>🔔 Notification යැව්වා : {sent}/{len(users)}</b>")
    except Exception:
        pass


async def _apply_original(client, admin_id, cam_id, orig_id):
    cam = await _cam_col.find_one({"_id": cam_id})
    if not cam or cam.get("status") != "cam":
        return "❌ මෙම Camera Copy record එක දැන් නැහැ."
    if orig_id == cam_id:
        return "❌ Original එක ලෙස Camera Copy file එකම තෝරා ඇත."
    clash = await _cam_col.find_one({"_id": orig_id})
    if clash:
        return "❌ තෝරාගත් file එක Camera Copy එකක්. Original file එකක් තෝරන්න."
    if not await _ORIG_DETAILS(orig_id):
        return "❌ Original file එක database එකේ හමු වුණේ නැහැ."
    await _cam_col.update_one(
        {"_id": cam_id, "status": "cam"},
        {"$set": {"status": "replaced", "original_file_id": orig_id,
                  "replaced_by": admin_id, "replaced_at": datetime.datetime.utcnow()}},
    )
    _invalidate_hidden()
    subs = len(cam.get("notify_users", []))
    asyncio.create_task(_notify_subscribers(client, cam, orig_id, admin_id))
    return ("✅ <b>Original file එක update කළා.</b>\n\n"
            "• Camera Copy එක search results වලින් ඉවත් වුණා\n"
            "• පරණ links / channel post buttons දැන් Original එක දෙනවා\n"
            "• Users ට Original එක යටින් \"Old Camera Copy\" button එකත් එනවා\n"
            f"• Notification යවන්නේ : {subs} දෙනෙකුට")


# ------------------------------ message handlers ---------------------------
@Client.on_message(filters.private & filters.text & filters.incoming & ~filters.regex(r"^/"), group=-1)
async def cam_private_text(client, message):
    uid = message.from_user.id if message.from_user else 0
    # Fresh value for every private text update (the original pm_text runs next
    # in the same task): only admins bypass the "PM search off" switch.
    _PM_ADMIN_BYPASS.set(uid in ADMINS)
    session = _session(uid) if uid in ADMINS else None
    if not session:
        return
    query = message.text.strip()
    try:
        files, _, _ = await _ia.get_search_results(None, query, max_results=10, filter=True)
    except Exception:
        files = []
    candidates = []
    for f in files:
        doc, olds = await _cam_lookup(f.file_id)
        if doc or olds or f.file_id == session["cam_id"]:
            continue
        candidates.append(f)
    if not candidates:
        await message.reply_text("<b>🔎 Result එකක් හමු වුණේ නැහැ. වෙනත් නමක් type කරන්න, නැත්නම් file එක send කරන්න.</b>")
        raise StopPropagation
    session["candidates"] = [f.file_id for f in candidates]
    session["ts"] = time.monotonic()
    rows = [[InlineKeyboardButton(f"[{_size(f.file_size)}] {_title(f.file_name)}"[:60], callback_data=f"cam_pick#{i}")]
            for i, f in enumerate(candidates)]
    rows.append([InlineKeyboardButton("❌ ᴄᴀɴᴄᴇʟ", callback_data="cam_cancel")])
    await message.reply_text("<b>🎬 Original file එක select කරන්න :</b>", reply_markup=InlineKeyboardMarkup(rows))
    raise StopPropagation


@Client.on_message(filters.private & filters.user(ADMINS) & (filters.document | filters.video | filters.audio), group=-1)
async def cam_session_media(client, message):
    session = _session(message.from_user.id)
    if not session:
        raise ContinuePropagation
    ftype = next((ft for ft in ("document", "video", "audio") if getattr(message, ft, None)), None)
    media = getattr(message, ftype) if ftype else None
    if media is None:
        raise ContinuePropagation
    media.file_type = ftype
    media.caption = message.caption or ""
    try:
        await _ia.save_file(media)           # already-indexed files are simply skipped
        orig_id, _ref = _ia.unpack_new_file_id(media.file_id)
        result = await _apply_original(client, message.from_user.id, session["cam_id"], orig_id)
    except Exception as e:
        log.exception("cam original upload failed: %s", e)
        result = "❌ File එක process කිරීමේදී දෝෂයක් ඇති වුණා. නැවත උත්සාහ කරන්න."
    if result.startswith("✅"):
        _CAM_SESSIONS.pop(message.from_user.id, None)
    await message.reply_text(result)
    raise StopPropagation


@Client.on_message(filters.private & filters.command("cancel") & filters.user(ADMINS), group=-1)
async def cam_cancel_command(client, message):
    if _CAM_SESSIONS.pop(message.from_user.id, None) is None:
        raise ContinuePropagation
    await message.reply_text("<b>❌ Update Original File අවලංගු කළා.</b>")
    raise StopPropagation


# ------------------------------ callback handlers --------------------------
async def _refresh_admin_buttons(query, state, fid):
    try:
        await query.message.edit_reply_markup(_with_rows(query.message.reply_markup, _admin_rows(state, fid)))
    except Exception:
        pass


async def _cb_mark(client, query):
    fid = query.data.split("#", 1)[1]
    if await _cam_col.find_one({"_id": fid}):
        return await query.answer("මෙම file එක දැනටමත් Camera Copy record එකක් ලෙස ඇත.", show_alert=True)
    if await _cam_col.find_one({"original_file_id": fid, "status": "replaced"}):
        return await query.answer("මෙය Original file එකක්. Camera Copy ලෙස mark කළ නොහැක.", show_alert=True)
    details = await _ORIG_DETAILS(fid)
    await _ensure_indexes()
    await _cam_col.insert_one({
        "_id": fid, "file_name": details[0].file_name if details else "",
        "status": "cam", "original_file_id": None, "notify_users": [],
        "marked_by": query.from_user.id, "marked_at": datetime.datetime.utcnow(),
    })
    await query.answer("📼 Camera Copy ලෙස mark කළා. දැන් users ට මෙම file එක වෙනුවට CAM notice එක යයි.", show_alert=True)
    await _refresh_admin_buttons(query, "cam", fid)


async def _cb_unmark(client, query):
    fid = query.data.split("#", 1)[1]
    res = await _cam_col.delete_one({"_id": fid, "status": "cam"})
    if not getattr(res, "deleted_count", 0):
        return await query.answer("Unmark කළ හැක්කේ Camera Copy ලෙස mark කර ඇති (replace නොකළ) file එකක් පමණයි.", show_alert=True)
    await query.answer("♻️ Camera Copy mark එක ඉවත් කළා.", show_alert=True)
    await _refresh_admin_buttons(query, "none", fid)


async def _cb_upd(client, query):
    fid = query.data.split("#", 1)[1]
    cam = await _cam_col.find_one({"_id": fid})
    if not cam or cam.get("status") != "cam":
        return await query.answer("මෙය Camera Copy (replace නොකළ) file එකක් නොවේ.", show_alert=True)
    _prune()
    _CAM_SESSIONS[query.from_user.id] = {"cam_id": fid, "ts": time.monotonic(), "candidates": []}
    await query.answer()
    await client.send_message(
        query.from_user.id,
        "<b>🎬 ᴜᴘᴅᴀᴛᴇ ᴏʀɪɢɪɴᴀʟ ꜰɪʟᴇ</b>\n\n"
        f"<b>📼 CAM :</b> <code>{html.escape(_title(cam.get('file_name')))}</code>\n\n"
        "<b>මෙයින් එකක් කරන්න :</b>\n"
        "<b>1️⃣ Original file එක මෙතනට send කරන්න</b> (video / document)\n"
        "<b>2️⃣ දැනටමත් database එකේ ඇත්නම් චිත්‍රපටයේ නම type කර search කර select කරන්න</b>\n\n"
        "⏳ මිනිත්තු 15ක් ඇතුළත කරන්න.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ ᴄᴀɴᴄᴇʟ", callback_data="cam_cancel")]]),
    )


async def _cb_cancel(client, query):
    _CAM_SESSIONS.pop(query.from_user.id, None)
    await query.answer("අවලංගු කළා.")
    try:
        await query.message.delete()
    except Exception:
        pass


async def _cb_pick(client, query):
    session = _session(query.from_user.id)
    if not session:
        return await query.answer("Session එක කල් ඉකුත් වී ඇත. නැවත Update Original File ඔබන්න.", show_alert=True)
    try:
        orig_id = session["candidates"][int(query.data.split("#", 1)[1])]
    except Exception:
        return await query.answer("තෝරාගැනීම වලංගු නැත.", show_alert=True)
    await query.answer()
    result = await _apply_original(client, query.from_user.id, session["cam_id"], orig_id)
    if result.startswith("✅"):
        _CAM_SESSIONS.pop(query.from_user.id, None)
    try:
        await query.message.edit_text(result)
    except Exception:
        await client.send_message(query.from_user.id, result)


async def _cb_notif(client, query):
    fid = query.data.split("#", 1)[1]
    uid = query.from_user.id
    cam = await _cam_col.find_one({"_id": fid})
    if not cam:
        return await query.answer("Record එක සොයාගත නොහැක.", show_alert=True)
    if cam.get("status") == "replaced":
        return await query.answer("Original file එක දැනටමත් upload කර ඇත. නැවත movie එක search කරන්න.", show_alert=True)
    if uid in cam.get("notify_users", []):
        await _cam_col.update_one({"_id": fid}, {"$pull": {"notify_users": uid}})
        await query.answer("🔕 Notification off කළා.", show_alert=True)
        subscribed = False
    else:
        await _cam_col.update_one({"_id": fid}, {"$addToSet": {"notify_users": uid}})
        await query.answer("🔔 Original එක upload කළ විගස ඔබට දැනුම් දෙන්නම්.", show_alert=True)
        subscribed = True
    try:
        await query.message.edit_reply_markup(_notice_markup(fid, subscribed))
    except Exception:
        pass


async def _cb_get(client, query):
    await _deliver_cam_file(client, query, query.data.split("#", 1)[1], use_pending=True)


async def _cb_old(client, query):
    await _deliver_cam_file(client, query, query.data.split("#", 1)[1], use_pending=False)


_ADMIN_ONLY = {"cam_mark", "cam_unmark", "cam_upd", "cam_cancel", "cam_pick"}
_CALLBACKS = {
    "cam_mark": _cb_mark, "cam_unmark": _cb_unmark, "cam_upd": _cb_upd,
    "cam_cancel": _cb_cancel, "cam_pick": _cb_pick,
    "cam_notif": _cb_notif, "cam_get": _cb_get, "cam_old": _cb_old,
}


@Client.on_callback_query(filters.regex(r"^cam_(mark|unmark|upd|cancel|pick|notif|get|old)(#|$)"), group=-1)
async def camera_copy_callbacks(client, query):
    action = query.data.split("#", 1)[0]
    try:
        if action in _ADMIN_ONLY and query.from_user.id not in ADMINS:
            await query.answer("Admins ට පමණයි.", show_alert=True)
        else:
            await _CALLBACKS[action](client, query)
    except Exception as e:
        log.exception("camera copy callback %s failed: %s", action, e)
        try:
            await query.answer("දෝෂයක් ඇති වුණා. නැවත උත්සාහ කරන්න.", show_alert=True)
        except Exception:
            pass
    raise StopPropagation
