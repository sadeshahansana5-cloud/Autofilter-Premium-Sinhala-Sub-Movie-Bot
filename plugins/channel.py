import re
import logging
import asyncio
from datetime import datetime
from collections import defaultdict
from plugins.Dreamxfutures.Imdbposter import get_movie_detailsx, fetch_image, get_movie_details, get_tmdb_details_direct
from database.users_chats_db import db
from pyrogram import Client, filters, enums
from info import CHANNELS, MOVIE_UPDATE_CHANNEL, LOG_CHANNEL, LINK_PREVIEW, ABOVE_PREVIEW, BAD_WORDS, LANDSCAPE_POSTER, TMDB_POSTER
from Script import script
from database.ia_filterdb import save_file
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from utils import temp
import utils as _runtime_utils
from pymongo.errors import PyMongoError, DuplicateKeyError
from pyrogram.errors import MessageIdInvalid, MessageNotModified, FloodWait
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# Precomputed sets for faster lookups
IGNORE_WORDS = {
    "rarbg", "dub", "sub", "sample", "mkv", "aac", "combined",
    "action", "adventure", "animation", "biography", "comedy", "crime", 
    "documentary", "drama", "family", "fantasy", "film-noir", "history", 
    "horror", "music", "musical", "mystery", "romance", "sci-fi", "sport", 
    "thriller", "war", "western", "hdcam", "hdtc", "camrip", "ts", "tc", 
    "telesync", "dvdscr", "dvdrip", "predvd", "webrip", "web-dl", "tvrip", 
    "hdtv", "web dl", "webdl", "bluray", "brrip", "bdrip", "360p", "480p", 
    "720p", "1080p", "2160p", "4k", "1440p", "540p", "240p", "140p", "hevc", 
    "hdrip", "hin", "hindi", "tam", "tamil", "kan", "kannada", "tel", "telugu", 
    "mal", "malayalam", "eng", "english", "pun", "punjabi", "ben", "bengali", 
    "mar", "marathi", "guj", "gujarati", "urd", "urdu", "kor", "korean", "jpn", 
    "japanese", "nf", "netflix", "sonyliv", "sony", "sliv", "amzn", "prime", 
    "primevideo", "hotstar", "zee5", "jio", "jhs", "aha", "hbo", "paramount", 
    "apple", "hoichoi", "sunnxt", "viki"
}|BAD_WORDS

# Constants
CAPTION_LANGUAGES = {
    "hin": "Hindi", "hindi": "Hindi",
    "tam": "Tamil", "tamil": "Tamil",
    "kan": "Kannada", "kannada": "Kannada",
    "tel": "Telugu", "telugu": "Telugu",
    "mal": "Malayalam", "malayalam": "Malayalam",
    "eng": "English", "english": "English",
    "pun": "Punjabi", "punjabi": "Punjabi",
    "ben": "Bengali", "bengali": "Bengali",
    "mar": "Marathi", "marathi": "Marathi",
    "guj": "Gujarati", "gujarati": "Gujarati",
    "urd": "Urdu", "urdu": "Urdu",
    "kor": "Korean", "korean": "Korean",
    "jpn": "Japanese", "japanese": "Japanese",
}

OTT_PLATFORMS = {
    "nf": "Netflix", "netflix": "Netflix",
    "sonyliv": "SonyLiv", "sony": "SonyLiv", "sliv": "SonyLiv",
    "amzn": "Amazon Prime Video", "prime": "Amazon Prime Video", "primevideo": "Amazon Prime Video",
    "hotstar": "Disney+ Hotstar", "zee5": "Zee5",
    "jio": "JioHotstar", "jhs": "JioHotstar",
    "aha": "Aha", "hbo": "HBO Max", "paramount": "Paramount+",
    "apple": "Apple TV+", "hoichoi": "Hoichoi", "sunnxt": "Sun NXT", "viki": "Viki"
}

STANDARD_GENRES = {
    'Action', 'Adventure', 'Animation', 'Biography', 'Comedy', 'Crime', 'Documentary',
    'Drama', 'Family', 'Fantasy', 'Film-Noir', 'History', 'Horror', 'Music',
    'Musical', 'Mystery', 'Romance', 'Sci-Fi', 'Sport', 'Thriller', 'War', 'Western'
}

# Precompiled regex patterns
CLEAN_PATTERN = re.compile(r'@[^ \n\r\t\.,:;!?()\[\]{}<>\\/"\'=_%]+|\bwww\.[^\s\]\)]+|\([\@^]+\)|\[[\@^]+\]')
NORMALIZE_PATTERN = re.compile(r"[._]+|[()\[\]{}:;'–!,.?_]")
QUALITY_PATTERN = re.compile(
    r"\b(?:HDCam|HDTC|CamRip|TS|TC|TeleSync|DVDScr|DVDRip|PreDVD|"
    r"WEBRip|WEB-DL|TVRip|HDTV|WEB DL|WebDl|BluRay|BRRip|BDRip|"
    r"360p|480p|720p|1080p|2160p|4K|1440p|540p|240p|140p|HEVC|HDRip)\b", 
    re.IGNORECASE
)
YEAR_PATTERN = re.compile(r"(?<![A-Za-z0-9])(?:19|20)\d{2}(?![A-Za-z0-9])")
RANGE_REGEX = re.compile(r'\bS(\d{1,2})[^\w\n\r]*E(?:p(?:isode)?)?0*(\d{1,2})\s*(?:to|-)\s*(?:E(?:p(?:isode)?)?)?0*(\d{1,2})',re.IGNORECASE)
SINGLE_REGEX = re.compile(r'\bS(\d{1,2})[^\w\n\r]*E(?:p(?:isode)?)?0*(\d{1,3})', re.IGNORECASE)
NAMED_REGEX = re.compile(r'Season\s*0*(\d{1,2})[\s\-,:]*Ep(?:isode)?\s*0*(\d{1,3})', re.IGNORECASE)
EP_ONLY_RANGE = re.compile(r'\b(?:EP|Episode)0*(\d{1,3})\s*-\s*0*(\d{1,3})\b',re.IGNORECASE)


MEDIA_FILTER = filters.document | filters.video | filters.audio
SUBTITLE_POST_EXTENSIONS = (".srt", ".zip", ".rar", ".7z", ".7zip", ".ass", ".ssa", ".sub", ".vtt")
SUBTITLE_SIDEcar_TOKENS = (" srt", " zip", " rar", " 7z", " 7zip", " ass", " ssa", " sub", " vtt")
def is_subtitle_sidecar(filename):
    value = re.sub(r"[._]+", " ", str(filename or "")).lower().strip()
    return value.endswith(SUBTITLE_POST_EXTENSIONS) or any(value.endswith(token) for token in SUBTITLE_SIDEcar_TOKENS)
locks = defaultdict(asyncio.Lock)
pending_updates = {}



async def resolve_movie_details(title):
    """TMDB-first lookup with cleaned title/year and a safe legacy fallback."""
    candidates = [str(title).strip()]
    without_year = re.sub(r"\s+(?:19|20)\d{2}\s*$", "", str(title).strip()).strip()
    if without_year and without_year not in candidates:
        candidates.append(without_year)
    fallback = {}
    for query in candidates:
        for getter in (get_tmdb_details_direct, get_movie_detailsx, get_movie_details):
            try:
                details = await getter(query) or {}
            except Exception:
                continue
            if details.get("error"):
                continue
            if not fallback:
                fallback = details
            if details.get("poster_url") or details.get("poster"):
                return details
    return fallback

def clean_mentions_links(text: str) -> str:
    return CLEAN_PATTERN.sub("", text or "").strip()

def normalize(s: str) -> str:
    s = NORMALIZE_PATTERN.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()

def remove_ignored_words(text: str) -> str:
    IGNORE_WORDS_LOWER = {w.lower() for w in IGNORE_WORDS}
    return " ".join(word for word in text.split() if word.lower() not in IGNORE_WORDS_LOWER)

def get_qualities(text: str) -> str:
    qualities = QUALITY_PATTERN.findall(text)
    return ", ".join(qualities) if qualities else "N/A"

def extract_ott_platform(text: str) -> str:
    text = text.lower()
    platforms = {plat for key, plat in OTT_PLATFORMS.items() if key in text}
    return " | ".join(platforms) if platforms else "N/A"

def extract_season_episode(filename: str) -> Tuple[Optional[int], Optional[str]]:
    if m := EP_ONLY_RANGE.search(filename):
        return 1, f"{int(m.group(1))}-{int(m.group(2))}"
    for pattern in (RANGE_REGEX, SINGLE_REGEX, NAMED_REGEX):
        if m := pattern.search(filename):
            season = int(m.group(1))
            if pattern == RANGE_REGEX:
                ep = f"{m.group(2)}-{m.group(3)}"
            else:
                ep = m.group(2)
            return season, ep
    return None, None

def schedule_update(bot, base_name, delay=5):
    if handle := pending_updates.get(base_name):
        if not handle.cancelled():
            handle.cancel()
    
    loop = asyncio.get_event_loop()
    pending_updates[base_name] = loop.call_later(
        delay,
        lambda: asyncio.create_task(update_movie_message(bot, base_name))
    )

def extract_media_info(filename: str, caption: str):
    filename = normalize(clean_mentions_links(filename).title())
    caption_clean = clean_mentions_links(caption).lower() if caption else ""
    unified = f"{caption_clean} {filename.lower()}".strip()

    season = episode = year = None
    tag = "#MOVIE"
    processed_raw = base_raw = filename
    quality = get_qualities(caption_clean) or get_qualities(filename.lower()) or "N/A"
    ott_platform = extract_ott_platform(f"{filename} {caption_clean}")

    lang_keys = {k for k in CAPTION_LANGUAGES if k in caption_clean or k in filename.lower()}
    language = ", ".join(sorted({CAPTION_LANGUAGES[k] for k in lang_keys})) if lang_keys else "N/A"

    season, episode = extract_season_episode(filename)
    if season is not None:
        tag = "#SERIES"
        if m := (RANGE_REGEX.search(filename) or SINGLE_REGEX.search(filename) or NAMED_REGEX.search(filename) or EP_ONLY_RANGE.search(filename)):
            match_str = m.group(0)
            start_idx = filename.lower().find(match_str.lower())
            end_idx = start_idx + len(match_str)
            processed_raw = filename[:end_idx]
            base_raw = filename[:start_idx]
            if year_match := YEAR_PATTERN.search(filename.lower()[end_idx:]):
                y = year_match.group(0)
                yi = filename.lower().find(y, end_idx)
                if yi != -1:
                    processed_raw = filename[:yi+4]
                    base_raw += f" {y}"
    else:
        if year_match := YEAR_PATTERN.search(unified):
            year = year_match.group(0)
            year_idx = filename.lower().find(year.lower())
            if year_idx != -1:
                processed_raw = filename[:year_idx + 4]
                base_raw = processed_raw
        else:
            if qual_match := QUALITY_PATTERN.search(unified):
                qual_str = qual_match.group(0)
                qual_idx = filename.lower().find(qual_str.lower())
                if qual_idx != -1:
                    processed_raw = filename[:qual_idx]
                    base_raw = processed_raw

    # Apply the project-wide filename cleaner before the final group name and
    # before TMDB lookup, so branding fragments cannot poison the title.
    base_raw = _runtime_utils.clean_filename(base_raw)
    processed_raw = _runtime_utils.clean_filename(processed_raw)
    base_name = normalize(remove_ignored_words(normalize(base_raw)))
    if year and year not in base_name:
        base_name += f" {year}"

    if base_name.endswith(")"):
        base_name = re.sub(r"\s+\(\d{4}\)$", "", base_name)
        if year:
            base_name += f" ({year})"

    return {
        "processed": normalize(processed_raw),
        "base_name": base_name,
        "tag": tag,
        "season": season,
        "episode": episode,
        "year": year,
        "quality": quality,
        "ott_platform": ott_platform,
        "language": language
    }

@Client.on_message(filters.chat(CHANNELS) & MEDIA_FILTER)
async def media_handler(bot, message):
    media = next(
        (getattr(message, ft) for ft in ("document", "video", "audio")
         if getattr(message, ft, None)),
        None
    )
    if not media:
        return

    media.file_type = next(ft for ft in ("document", "video", "audio") if hasattr(message, ft))
    media.caption = message.caption or ""
    success, info = await save_file(media)
    if not success:
        return

    try:
        # Keep subtitle/archive sidecars in the search index, but never create
        # movie/series announcement posts for them.
        if not is_subtitle_sidecar(media.file_name):
            if await db.movie_update_status(bot.me.id):
                await process_and_send_update(bot, media.file_name, media.caption)
    except Exception:
        logger.exception("Error processing media")

async def process_and_send_update(bot, filename, caption, publish=True):
    try:
        media_info = extract_media_info(filename, caption)
        base_name = media_info["base_name"]
        processed = media_info["processed"]

        lock = locks[base_name]
        async with lock:
            await _process_with_lock(bot, filename, caption, media_info, base_name, processed, publish)
    except PyMongoError as e:
        logger.error("Database error: %s", e)
    except Exception as e:
        logger.exception("Processing failed: %s", e)

async def _process_with_lock(bot, filename, caption, media_info, base_name, processed, publish=True):
    if not hasattr(db, 'movie_updates'):
        db.movie_updates = db.db.movie_updates

    movie_doc = await db.movie_updates.find_one({"_id": base_name})
    global error_tmdb
    error_tmdb=False
    file_data = {
        "filename": filename,
        "processed": processed,
        "quality": media_info["quality"],
        "language": media_info["language"],
        "ott_platform": media_info["ott_platform"],
        "timestamp": datetime.now(),
        "tag": media_info["tag"],
        "season": media_info["season"],
        "episode": media_info["episode"]
    }

    # Always retry TMDB for a previously indexed group that has no poster.
    # This fixes old documents created before poster/details enrichment existed.
    details = {}
    if movie_doc and not movie_doc.get("poster_url"):
        details = await resolve_movie_details(base_name)
        poster = details.get("poster_url") or details.get("poster")
        if poster:
            refresh = {
                "poster_url": poster,
                "genres": details.get("genres") or movie_doc.get("genres", "N/A"),
                "rating": details.get("rating") or movie_doc.get("rating", "N/A"),
                "release_date": details.get("release_date") or movie_doc.get("release_date", ""),
                "countries": details.get("countries") or movie_doc.get("countries", ""),
                "languages": details.get("languages") or movie_doc.get("languages", ""),
                "runtime": details.get("runtime") or movie_doc.get("runtime", ""),
                "tagline": details.get("tagline") or movie_doc.get("tagline", ""),
                "year": movie_doc.get("year") or details.get("year"),
                "imdb_url": details.get("tmdb_url") or details.get("url") or movie_doc.get("imdb_url", ""),
            }
            await db.movie_updates.update_one({"_id": base_name}, {"$set": refresh})
            movie_doc.update(refresh)
    if not movie_doc:
        details = await resolve_movie_details(base_name)

        raw_genres = details.get("genres", "N/A")
        if isinstance(raw_genres, str):
            genre_list = [g.strip() for g in raw_genres.split(",")]
            genres = ", ".join(g for g in genre_list if g in STANDARD_GENRES) or "N/A"
        else:
            genres = ", ".join(g for g in raw_genres if g in STANDARD_GENRES) or "N/A"
        movie_doc = {
            "_id": base_name,
            "files": [file_data],
            "poster_url": details.get("poster_url") or details.get("poster"),
            "genres": genres,
            "rating": details.get("rating", "N/A"),
            "release_date": details.get("release_date") or "",
            "countries": details.get("countries") or "",
            "languages": details.get("languages") or "",
            "runtime": details.get("runtime") or "",
            "tagline": details.get("tagline") or "",
            "imdb_url": details.get("url", "")if not TMDB_POSTER else details.get("tmdb_url"),
            "year": media_info["year"] or details.get("year"),
            "tag": media_info["tag"],
            "ott_platform": media_info["ott_platform"],
            "message_id": None,
            "is_photo": False
        }
        try:
            await db.movie_updates.insert_one(movie_doc)
            if publish:
                await send_movie_update(bot, base_name)
            movie_doc = await db.movie_updates.find_one({"_id": base_name})
        except DuplicateKeyError:
            movie_doc = await db.movie_updates.find_one({"_id": base_name})
            if movie_doc:
                if any(f["filename"] == filename for f in movie_doc["files"]):
                    return
                await db.movie_updates.update_one(
                    {"_id": base_name},
                    {"$push": {"files": file_data}}
                )
                movie_doc["files"].append(file_data)
                if publish:
                    schedule_update(bot, base_name)
    else:
        if any(f["filename"] == filename for f in movie_doc["files"]):
            return
        await db.movie_updates.update_one(
            {"_id": base_name},
            {"$push": {"files": file_data}}
        )
        movie_doc["files"].append(file_data)
        if publish:
            schedule_update(bot, base_name)

async def send_movie_update(bot, base_name):
    max_retries = 3
    base_delay = 5
    for attempt in range(max_retries):
        try:
            movie_doc = await db.movie_updates.find_one({"_id": base_name})
            if not movie_doc:
                return None

            text = generate_movie_message(movie_doc, base_name)
            if not movie_doc.get("poster_url"):
                if not movie_doc.get("missing_image_logged"):
                    try:
                        await bot.send_message(LOG_CHANNEL, f"⚠️ Movie post skipped — TMDB poster not found\n\n<b>Title:</b> {base_name}\n<b>Reason:</b> No image available after cleaned-title lookup.", parse_mode=enums.ParseMode.HTML)
                    except Exception:
                        logger.exception("Failed to log missing poster for %s", base_name)
                    await db.movie_updates.update_one({"_id": base_name}, {"$set": {"missing_image_logged": True}})
                return None
            buttons = InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    'ɢᴇᴛ ғɪʟᴇs',
                    url=f"https://t.me/{temp.U_NAME}?start=getfile-{base_name.replace(' ', '-')}"
                )
            ]])

            if movie_doc.get("poster_url") and not LINK_PREVIEW:
                # Use one consistent poster resolution for every announcement.
                # ImageOps.contain in fetch_image preserves aspect ratio and adds
                # padding instead of cropping the poster.
                resized_poster = await fetch_image(movie_doc["poster_url"], size=(600, 900))
                if not resized_poster:
                    await bot.send_message(LOG_CHANNEL, f"⚠️ Movie post skipped — poster download failed\n\n<b>Title:</b> {base_name}", parse_mode=enums.ParseMode.HTML)
                    return None
                msg = await bot.send_photo(
                    chat_id=MOVIE_UPDATE_CHANNEL,
                    photo=resized_poster,
                    caption=text,
                    reply_markup=buttons,
                    parse_mode=enums.ParseMode.HTML
                )
                is_photo = True
            else:
                send_params = {
                    "chat_id": MOVIE_UPDATE_CHANNEL,
                    "text": text,
                    "reply_markup": buttons,
                    "parse_mode": enums.ParseMode.HTML
                }
                if movie_doc.get("poster_url") and LINK_PREVIEW:
                    send_params["invert_media"] = ABOVE_PREVIEW
                msg = await bot.send_message(**send_params)
                is_photo = False

            await db.movie_updates.update_one(
                {"_id": base_name},
                {"$set": {"message_id": msg.id, "is_photo": is_photo}}
            )
            return msg
        except FloodWait as e:
            wait_time = e.value + 2
            await asyncio.sleep(wait_time)
        except Exception as e:
            logger.error(f"Failed to send movie update: {e}")
            break
    return None

async def update_movie_message(bot, base_name):
    try:
        movie_doc = await db.movie_updates.find_one({"_id": base_name})
        if not movie_doc:
            return

        text = generate_movie_message(movie_doc, base_name)
        buttons = InlineKeyboardMarkup([[
            InlineKeyboardButton(
                'ɢᴇᴛ ғɪʟᴇs',
                url=f"https://t.me/{temp.U_NAME}?start=getfile-{base_name.replace(' ', '-')}"
            )
        ]])

        message_id = movie_doc.get("message_id")
        is_photo = movie_doc.get("is_photo", False)

        if not message_id:
            await send_movie_update(bot, base_name)
            return

        try:
            if is_photo:
                await bot.edit_message_caption(
                    chat_id=MOVIE_UPDATE_CHANNEL,
                    message_id=message_id,
                    caption=text,
                    reply_markup=buttons,
                    parse_mode=enums.ParseMode.HTML
                )
            else:
                await bot.edit_message_text(
                    chat_id=MOVIE_UPDATE_CHANNEL,
                    message_id=message_id,
                    text=text,
                    reply_markup=buttons,
                    parse_mode=enums.ParseMode.HTML,
                    invert_media=ABOVE_PREVIEW,
                    disable_web_page_preview=not LINK_PREVIEW
                )
            return
        except (MessageIdInvalid, MessageNotModified):
            pass
        except Exception:
            try:
                await bot.delete_messages(
                    chat_id=MOVIE_UPDATE_CHANNEL,
                    message_ids=message_id
                )
                await db.movie_updates.update_one(
                    {"_id": base_name},
                    {"$set": {"message_id": None, "is_photo": False}}
                )
            except Exception:
                pass
            await send_movie_update(bot, base_name)
    except Exception as e:
        logger.error(f"Failed to update movie message: {e}")

def generate_movie_message(movie_doc, base_name):
    """Build one compact TMDB-style post for all files in a movie/series group."""
    files = movie_doc.get("files", [])
    all_qualities, all_languages, all_tags = set(), set(), set()
    episodes_by_season = defaultdict(set)
    for file in files:
        if file.get("quality") and file["quality"] != "N/A":
            all_qualities.update(q.strip() for q in file["quality"].split(",") if q.strip())
        if file.get("language") and file["language"] != "N/A":
            all_languages.update(x.strip() for x in file["language"].split(",") if x.strip())
        if file.get("tag"):
            all_tags.add(file["tag"])
        if file.get("season") and file.get("episode"):
            episodes_by_season[file["season"]].add(file["episode"])

    title = base_name
    year = movie_doc.get("year")
    if year and str(year) not in title:
        title = f"{title} ({year})"
    is_series = "#SERIES" in all_tags or bool(episodes_by_season)
    subtitle_label = "Sinhala Subtitles | සිංහල උපසිරැසි සමඟ"
    text = f"☘️  <b>{title} {subtitle_label}</b>\n\n"
    release = movie_doc.get("release_date") or (f"January 1, {year}" if year else "N/A")
    try:
        release = datetime.strptime(str(release), "%Y-%m-%d").strftime("%B %-d, %Y")
    except (TypeError, ValueError):
        pass
    country = movie_doc.get("countries") or "N/A"
    language = movie_doc.get("languages") or (", ".join(sorted(all_languages)) if all_languages else "N/A")
    runtime = movie_doc.get("runtime") or "N/A"
    rating = movie_doc.get("rating") or "N/A"
    tagline = movie_doc.get("tagline") or title
    genres = movie_doc.get("genres") or "N/A"
    genre_tags = " ".join("#" + re.sub(r"\s+", "", g.strip()) for g in str(genres).split(",") if g.strip() and g.strip() != "N/A")
    quality_tags = " ".join("#" + re.sub(r"[^A-Za-z0-9]+", "", q) for q in sorted(all_qualities))
    text += f"📅 𝚁𝚎𝚕𝚎𝚊𝚜𝚎 : {release}\n"
    text += f"🌎 𝙲𝚘𝚞𝚗𝚝𝚛𝚢 : {country}\n"
    text += f"🗣 Language : {language}\n"
    text += f"🕰 𝙳𝚞𝚛𝚊𝚝𝚒𝚘𝚗 : {runtime} min\n"
    text += f"🏆 IMDB Rating : {rating}\n"
    text += f"🎞 Quality : {', '.join(sorted(all_qualities)) if all_qualities else 'N/A'}\n"
    text += f"🔰 TagLine  : {title} | “{tagline}”\n"
    text += "✍️ Subtitles By :  Credit For Original Owner\n"
    text += f"🎭 ꓖⲉⲛⳅⲉ⳽ : #.NEW {genre_tags} {quality_tags}".rstrip() + "\n"
    if episodes_by_season:
        episode_lines = []
        for season, episodes in sorted(episodes_by_season.items(), key=lambda x: int(x[0])):
            episode_lines.append(f"S{int(season)}: {', '.join(sorted(episodes))}")
        text += "📺 Episodes : " + " | ".join(episode_lines) + "\n"
    text += f"\n🔍 <b>Sᴇᴀʀᴄʜ</b> → {temp.B_LINK}"
    return text
