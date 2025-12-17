import asyncio
import PTN
import re
from datetime import datetime, timezone

from deep_translator import GoogleTranslator
from Backend.helper.imdb import get_detail, get_season, search_title
from themoviedb import aioTMDb
from Backend.config import Telegram
import Backend
from Backend.logger import LOGGER
from Backend.helper.encrypt import encode_string

# -------------------------------------------------
# CONFIG
# -------------------------------------------------
tmdb = aioTMDb(key=Telegram.TMDB_API, language="en-US", region="US")

IMDB_CACHE = {}
TMDB_SEARCH_CACHE = {}
TMDB_DETAILS_CACHE = {}
EPISODE_CACHE = {}
TRANSLATE_CACHE = {}

API_SEMAPHORE = asyncio.Semaphore(12)

# -------------------------------------------------
# GENRE NORMALIZATION
# -------------------------------------------------
GENRE_TUR_ALIASES = {
  "action": "Aksiyon",
  "film-noir": "Kara Film",
  "game-show": "Oyun Gösterisi",
  "short": "Kısa",
  "sci-fi": "Bilim Kurgu",
  "sport": "Spor",
  "adventure": "Macera",
  "animation": "Animasyon",
  "biography": "Biyografi",
  "comedy": "Komedi",
  "crime": "Suç",
  "documentary": "Belgesel",
  "drama": "Dram",
  "family": "Aile",
  "news": "Haberler",
  "fantasy": "Fantastik",
  "history": "Tarih",
  "horror": "Korku",
  "music": "Müzik",
  "musical": "Müzikal",
  "mystery": "Gizem",
  "romance": "Romantik",
  "science fiction": "Bilim Kurgu",
  "tv movie": "TV Filmi",
  "thriller": "Gerilim",
  "war": "Savaş",
  "western": "Vahşi Batı",
  "action & adventure": "Aksiyon ve Macera",
  "kids": "Çocuklar",
  "reality": "Gerçeklik",
  "reality-tv": "Gerçeklik",
  "sci-fi & fantasy": "Bilim Kurgu ve Fantazi",
  "soap": "Pembe Dizi",
  "war & politics": "Savaş ve Politika",
  "bilim-kurgu": "Bilim Kurgu",
  "aksiyon & macera": "Aksiyon ve Macera",
  "savaş & politik": "Savaş ve Politika",
  "bilim kurgu & fantazi": "Bilim Kurgu ve Fantazi",
  "talk": "Talk-Show",
}
  
def tur_genre_normalize(genres):
    if not genres:
        return []
    out = []
    for g in genres:
        key = g.lower().replace("-", " ").replace("_", " ").strip()
        out.append(GENRE_TUR_ALIASES.get(key, g))
    return out

# -------------------------------------------------
# HELPERS
# -------------------------------------------------
def format_tmdb_image(path, size="w500"):
    return f"https://image.tmdb.org/t/p/{size}{path}" if path else ""

def get_tmdb_logo(images):
    if not images or not getattr(images, "logos", None):
        return ""
    for l in images.logos:
        if l.iso_639_1 == "en" and l.file_path:
            return format_tmdb_image(l.file_path, "w300")
    for l in images.logos:
        if l.file_path:
            return format_tmdb_image(l.file_path, "w300")
    return ""

def format_imdb_images(imdb_id):
    if not imdb_id:
        return {"poster": "", "backdrop": "", "logo": ""}
    return {
        "poster": f"https://images.metahub.space/poster/small/{imdb_id}/img",
        "backdrop": f"https://images.metahub.space/background/medium/{imdb_id}/img",
        "logo": f"https://images.metahub.space/logo/medium/{imdb_id}/img",
    }

def extract_default_id(text):
    if not text:
        return None
    imdb = re.search(r"(tt\d+)", str(text))
    if imdb:
        return imdb.group(1)
    tmdb = re.search(r"/(movie|tv)/(\d+)", str(text))
    if tmdb:
        return tmdb.group(2)
    return None

def to_iso_datetime(date_value):
    if not date_value:
        return ""
    try:
        if isinstance(date_value, str):
            dt = datetime.fromisoformat(date_value)
        else:
            dt = date_value
        dt = dt.replace(
            hour=11, minute=0, second=0, microsecond=0,
            tzinfo=timezone.utc
        )
        return dt.isoformat().replace("+00:00", "Z")
    except Exception:
        return ""

def translate_text_safe(text):
    if not text or not str(text).strip():
        return ""
    if text in TRANSLATE_CACHE:
        return TRANSLATE_CACHE[text]
    try:
        tr = GoogleTranslator(source="en", target="tr").translate(text)
    except Exception:
        tr = text
    TRANSLATE_CACHE[text] = tr
    return tr

# -------------------------------------------------
# SAFE SEARCH
# -------------------------------------------------
async def safe_imdb_search(title, type_):
    key = f"{type_}:{title}"
    if key in IMDB_CACHE:
        return IMDB_CACHE[key]
    try:
        async with API_SEMAPHORE:
            res = await search_title(title, type_)
        imdb_id = res["id"] if res else None
        IMDB_CACHE[key] = imdb_id
        return imdb_id
    except Exception:
        return None

async def safe_tmdb_search(title, type_, year=None):
    key = f"{type_}:{title}:{year}"
    if key in TMDB_SEARCH_CACHE:
        return TMDB_SEARCH_CACHE[key]
    try:
        async with API_SEMAPHORE:
            res = (
                await tmdb.search().movies(title, year=year)
                if type_ == "movie"
                else await tmdb.search().tv(title)
            )
        TMDB_SEARCH_CACHE[key] = res[0] if res else None
        return TMDB_SEARCH_CACHE[key]
    except Exception:
        return None

# -------------------------------------------------
# TMDB FETCHERS
# -------------------------------------------------
async def _tmdb_tv_details(tid):
    if tid in TMDB_DETAILS_CACHE:
        return TMDB_DETAILS_CACHE[tid]
    async with API_SEMAPHORE:
        d = await tmdb.tv(tid).details(
            append_to_response="external_ids,credits"
        )
        d.images = await tmdb.tv(tid).images()
    TMDB_DETAILS_CACHE[tid] = d
    return d

async def _tmdb_episode_details(tid, s, e):
    key = (tid, s, e)
    if key in EPISODE_CACHE:
        return EPISODE_CACHE[key]
    async with API_SEMAPHORE:
        d = await tmdb.episode(tid, s, e).details(
            append_to_response="images"
        )
    EPISODE_CACHE[key] = d
    return d

async def _tmdb_movie_details(mid):
    if mid in TMDB_DETAILS_CACHE:
        return TMDB_DETAILS_CACHE[mid]
    async with API_SEMAPHORE:
        d = await tmdb.movie(mid).details(
            append_to_response="external_ids,credits"
        )
        d.images = await tmdb.movie(mid).images()
    TMDB_DETAILS_CACHE[mid] = d
    return d

# -------------------------------------------------
# MAIN ENTRY
# -------------------------------------------------
async def metadata(filename, channel, msg_id):
    try:
        parsed = PTN.parse(filename)
    except Exception:
        return None

    title = parsed.get("title")
    season = parsed.get("season")
    episode = parsed.get("episode")
    year = parsed.get("year")
    quality = parsed.get("resolution")

    if not title or not quality:
        return None
    if season and not episode:
        return None

    encoded = await encode_string({"chat_id": channel, "msg_id": msg_id})

    default_id = extract_default_id(Backend.USE_DEFAULT_ID) or extract_default_id(filename)

    if season:
        return await fetch_tv_metadata(
            title, season, episode, encoded, year, quality, default_id
        )

    return await fetch_movie_metadata(
        title, encoded, year, quality, default_id
    )

# -------------------------------------------------
# TV METADATA
# -------------------------------------------------
async def fetch_tv_metadata(title, season, episode, encoded, year, quality, default_id):
    imdb_id = default_id if default_id and str(default_id).startswith("tt") else None
    tmdb_id = int(default_id) if default_id and str(default_id).isdigit() else None

    if not imdb_id and not tmdb_id:
        imdb_id = await safe_imdb_search(title, "tvSeries")

    if imdb_id:
        try:
            imdb = await get_detail(imdb_id, "tvSeries")
            ep = await get_season(imdb_id, season, episode)
            images = format_imdb_images(imdb_id)

            return {
                "tmdb_id": imdb.get("moviedb_id"),
                "imdb_id": imdb_id,
                "title": imdb.get("title"),
                "year": imdb.get("releaseDetailed", {}).get("year", 0),
                "released": to_iso_datetime(imdb.get("releaseDetailed", {}).get("date")),
                "rate": imdb.get("rating", {}).get("star", 0),
                "description": translate_text_safe(imdb.get("plot", "")),
                "poster": images["poster"],
                "backdrop": images["backdrop"],
                "logo": images["logo"],
                "genres": tur_genre_normalize(imdb.get("genre", [])),
                "cast": imdb.get("cast", []),
                "runtime": str(imdb.get("runtime") or ""),
                "media_type": "tv",
                "season_number": season,
                "episode_number": episode,
                "episode_title": ep.get("title", ""),
                "episode_backdrop": ep.get("image", ""),
                "episode_overview": translate_text_safe(ep.get("plot", "")),
                "episode_released": to_iso_datetime(ep.get("released")),
                "quality": quality,
                "encoded_string": encoded,
            }
        except Exception:
            pass

    if not tmdb_id:
        res = await safe_tmdb_search(title, "tv", year)
        if not res:
            return None
        tmdb_id = res.id

    tv = await _tmdb_tv_details(tmdb_id)
    ep = await _tmdb_episode_details(tmdb_id, season, episode)

    still = ep.still_path if ep else None

    return {
        "tmdb_id": tv.id,
        "imdb_id": getattr(tv.external_ids, "imdb_id", None),
        "title": tv.name,
        "year": tv.first_air_date.year if tv.first_air_date else 0,
        "released": to_iso_datetime(tv.first_air_date),
        "rate": tv.vote_average or 0,
        "description": translate_text_safe(tv.overview),
        "poster": format_tmdb_image(tv.poster_path),
        "backdrop": format_tmdb_image(tv.backdrop_path, "original"),
        "logo": get_tmdb_logo(tv.images),
        "genres": tur_genre_normalize([g.name for g in tv.genres]),
        "cast": [c.name for c in tv.credits.cast],
        "runtime": "",
        "media_type": "tv",
        "season_number": season,
        "episode_number": episode,
        "episode_title": ep.name if ep else "",
        "episode_backdrop": format_tmdb_image(still, "original") if still else "",
        "episode_overview": translate_text_safe(ep.overview) if ep else "",
        "episode_released": to_iso_datetime(ep.air_date) if ep else "",
        "quality": quality,
        "encoded_string": encoded,
    }

# -------------------------------------------------
# MOVIE METADATA
# -------------------------------------------------
async def fetch_movie_metadata(title, encoded, year, quality, default_id):
    imdb_id = default_id if default_id and str(default_id).startswith("tt") else None
    tmdb_id = int(default_id) if default_id and str(default_id).isdigit() else None

    if not imdb_id and not tmdb_id:
        imdb_id = await safe_imdb_search(title, "movie")

    if imdb_id:
        try:
            imdb = await get_detail(imdb_id, "movie")
            images = format_imdb_images(imdb_id)

            return {
                "tmdb_id": imdb.get("moviedb_id"),
                "imdb_id": imdb_id,
                "title": imdb.get("title", title),
                "year": imdb.get("releaseDetailed", {}).get("year", 0),
                "released": to_iso_datetime(imdb.get("releaseDetailed", {}).get("date")),
                "rate": imdb.get("rating", {}).get("star", 0),
                "description": translate_text_safe(imdb.get("plot", "")),
                "poster": images["poster"],
                "backdrop": images["backdrop"],
                "logo": images["logo"],
                "genres": tur_genre_normalize(imdb.get("genre", [])),
                "cast": imdb.get("cast", []),
                "runtime": str(imdb.get("runtime") or ""),
                "media_type": "movie",
                "quality": quality,
                "encoded_string": encoded,
            }
        except Exception:
            pass

    if not tmdb_id:
        res = await safe_tmdb_search(title, "movie", year)
        if not res:
            return None
        tmdb_id = res.id

    movie = await _tmdb_movie_details(tmdb_id)

    return {
        "tmdb_id": movie.id,
        "imdb_id": getattr(movie.external_ids, "imdb_id", None),
        "title": movie.title,
        "year": movie.release_date.year if movie.release_date else 0,
        "released": to_iso_datetime(movie.release_date),
        "rate": movie.vote_average or 0,
        "description": translate_text_safe(movie.overview),
        "poster": format_tmdb_image(movie.poster_path),
        "backdrop": format_tmdb_image(movie.backdrop_path, "original"),
        "logo": get_tmdb_logo(movie.images),
        "genres": tur_genre_normalize([g.name for g in movie.genres]),
        "cast": [c.name for c in movie.credits.cast],
        "runtime": f"{movie.runtime} min" if movie.runtime else "",
        "media_type": "movie",
        "quality": quality,
        "encoded_string": encoded,
    }
