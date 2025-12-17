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
# PLATFORM MAP
# -------------------------------------------------
PLATFORM_ALIASES = {
    "MAX": "Max",
    "HBOMAX": "Max",
    "HBO": "Max",
    "BLUTV": "Max",

    "TABII": "Tabii",
    "TABİİ": "Tabii",

    "NF": "Netflix",
    "NETFLIX": "Netflix",

    "DSNP": "Disney",
    "DISNEY": "Disney",
    "DISNEY+": "Disney",

    "TOD": "Tod",
    "TV+": "Tv+",
    "EXXEN": "Exxen",
    "GAIN": "Gain",

    "AMZN": "Amazon",
    "AMAZON": "Amazon",
}

def extract_platform(filename: str) -> str:
    if not filename:
        return ""
    upper = filename.upper()
    for key, value in PLATFORM_ALIASES.items():
        if re.search(rf"\b{re.escape(key)}\b", upper):
            return value
    return ""

# -------------------------------------------------
# GENRE NORMALIZATION
# -------------------------------------------------
GENRE_TUR_ALIASES = {
    "action": "Aksiyon",
    "adventure": "Macera",
    "animation": "Animasyon",
    "comedy": "Komedi",
    "crime": "Suç",
    "documentary": "Belgesel",
    "drama": "Dram",
    "family": "Aile",
    "fantasy": "Fantastik",
    "history": "Tarih",
    "horror": "Korku",
    "music": "Müzik",
    "mystery": "Gizem",
    "romance": "Romantik",
    "science fiction": "Bilim Kurgu",
    "thriller": "Gerilim",
    "war": "Savaş",
    "western": "Vahşi Batı",
}

def tur_genre_normalize(genres):
    if not genres:
        return []
    return [GENRE_TUR_ALIASES.get(g.lower(), g) for g in genres]

# -------------------------------------------------
# HELPERS
# -------------------------------------------------
def format_tmdb_image(path, size="w500"):
    return f"https://image.tmdb.org/t/p/{size}{path}" if path else ""

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
        dt = dt.replace(hour=11, minute=0, second=0, microsecond=0, tzinfo=timezone.utc)
        return dt.isoformat().replace("+00:00", "Z")
    except Exception:
        return ""

def translate_text_safe(text):
    if not text:
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
# MAIN
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

    platform = extract_platform(filename)
    encoded = await encode_string({"chat_id": channel, "msg_id": msg_id})
    default_id = extract_default_id(Backend.USE_DEFAULT_ID) or extract_default_id(filename)

    if season:
        return await fetch_tv_metadata(
            title, season, episode, encoded, year, quality, platform, default_id
        )

    return await fetch_movie_metadata(
        title, encoded, year, quality, platform, default_id
    )

# -------------------------------------------------
# TV
# -------------------------------------------------
async def fetch_tv_metadata(title, season, episode, encoded, year, quality, platform, default_id):
    res = await tmdb.search().tv(title)
    if not res:
        return None

    tv = await tmdb.tv(res[0].id).details(append_to_response="external_ids,credits")
    ep = await tmdb.episode(tv.id, season, episode).details()

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
        "logo": "",
        "genres": tur_genre_normalize([g.name for g in tv.genres]),
        "cast": [c.name for c in tv.credits.cast[:10]],
        "runtime": "",
        "media_type": "tv",
        "season_number": season,
        "episode_number": episode,
        "episode_title": ep.name,
        "episode_backdrop": format_tmdb_image(ep.still_path, "original"),
        "episode_overview": translate_text_safe(ep.overview),
        "episode_released": to_iso_datetime(ep.air_date),
        "platform": platform,
        "quality": quality,
        "encoded_string": encoded,
    }

# -------------------------------------------------
# MOVIE
# -------------------------------------------------
async def fetch_movie_metadata(title, encoded, year, quality, platform, default_id):
    res = await tmdb.search().movies(title, year=year)
    if not res:
        return None

    movie = await tmdb.movie(res[0].id).details(append_to_response="external_ids,credits")

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
        "logo": "",
        "genres": tur_genre_normalize([g.name for g in movie.genres]),
        "cast": [c.name for c in movie.credits.cast[:10]],
        "runtime": f"{movie.runtime} min" if movie.runtime else "",
        "media_type": "movie",
        "platform": platform,
        "quality": quality,
        "encoded_string": encoded,
    }
