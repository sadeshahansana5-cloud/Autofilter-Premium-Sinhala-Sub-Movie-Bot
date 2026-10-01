"""Unified admin dashboard for Sinhala movie/series post management."""
from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from info import ADMINS


_TOOL_TEXT = {
    "rebuild": (
        "<b>♻️ /rebuild_posts</b>\n\n"
        "Database එකේ ඇති සියලු movie/series files scan කරලා grouped posts නැවත create/update කරයි.\n"
        "<code>🗑️ Delete old posts</code> — පරණ stored posts ඉවත් කර සියල්ල නැවත හදයි.\n"
        "<code>♻️ Keep old posts</code> — නැති posts පමණක් හදයි."
    ),
    "sync": (
        "<b>🔄 /sync_movie_posts</b>\n\n"
        "Bot account එකකට Telegram old channel history read කළ නොහැකි නිසා MongoDB සහ file index එකෙන් safe sync කරයි.\n"
        "Stored message IDs ඇති posts වල GET FILES buttons repair කරයි.\n\n"
        "Channel එකේ message-ID range එකක් scan කිරීමට:\n"
        "<code>/sync_movie_posts 1 20000</code>"
    ),
    "merge": (
        "<b>🧩 /merge_series_posts</b>\n\n"
        "Episode-by-episode records එකම series group එකකට merge කරයි.\n\n"
        "භාවිතය:\n<code>/merge_series_posts all</code>\n"
        "<code>/merge_series_posts Series Name 2023</code>"
    ),
    "repair": (
        "<b>🧹 /repair_movie_posts</b>\n\n"
        "Wrong title/year metadata, poster, and channel post details පරීක්ෂා කර repair කරයි.\n"
        "Pause / Resume / Cancel controls සහ final report ලබා දෙයි."
    ),
    "request": (
        "<b>📝 /request</b> — IMDb/TMDB result card එකෙන් movie request කරන්න.\n"
        "<b>/sub</b> — subtitle files පමණක් search කරන්න.\n"
        "<b>/limitations</b> — daily free/premium limits වෙනස් කරන්න."
    ),
}


def _markup():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("♻️ Rebuild posts", callback_data="movie_tool#rebuild"), InlineKeyboardButton("🔄 Sync database", callback_data="movie_tool#sync")],
        [InlineKeyboardButton("🧩 Merge series", callback_data="movie_tool#merge"), InlineKeyboardButton("🧹 Repair metadata", callback_data="movie_tool#repair")],
        [InlineKeyboardButton("📝 Request / Sub / Limits", callback_data="movie_tool#request")],
    ])


@Client.on_message(filters.private & filters.command("movie_tools") & filters.user(ADMINS))
async def movie_tools(client, message):
    await message.reply_text(
        "<b>🎬 Sinhala Movie Post Management</b>\n\n"
        "පහත tools වලින් අවශ්‍ය එක තෝරන්න. මෙය admin-only dashboard එකකි.",
        reply_markup=_markup(), parse_mode=enums.ParseMode.HTML,
    )


@Client.on_callback_query(filters.regex(r"^movie_tool#(rebuild|sync|merge|repair|request)$") & filters.user(ADMINS))
async def movie_tool_details(client, query):
    key = query.data.split("#", 1)[1]
    text = _TOOL_TEXT.get(key)
    if not text:
        return await query.answer("Tool එක හමු වුණේ නැහැ.", show_alert=True)
    await query.answer()
    await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Back to tools", callback_data="movie_tool#home")]
    ]), parse_mode=enums.ParseMode.HTML)


@Client.on_callback_query(filters.regex(r"^movie_tool#home$") & filters.user(ADMINS))
async def movie_tool_home(client, query):
    await query.answer()
    await query.message.edit_text(
        "<b>🎬 Sinhala Movie Post Management</b>\n\nපහත tools වලින් අවශ්‍ය එක තෝරන්න.",
        reply_markup=_markup(), parse_mode=enums.ParseMode.HTML,
    )
