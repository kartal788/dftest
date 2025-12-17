import asyncio
import traceback
import PTN
import re
from re import compile, IGNORECASE
from Backend.helper.imdb import get_detail, get_season, search_title
from themoviedb import aioTMDb
from Backend.config import Telegram
import Backend
from Backend.logger import LOGGER
from Backend.helper.encrypt import encode_string

# ----------------- Configuration -----------------
DELAY = 0
tmdb = aioTMDb(key=Telegram.TMDB_API, language="en-US", region="US")

IMDB_CACHE = {}
TMDB_SEARCH_CACHE = {}
TMDB_DETAILS_CACHE = {}
EPISODE_CACHE = {}

API_SEMAPHORE = asyncio.Semaphore(12)

# ----------------- TMDb Genre & Platform -----------------

TMDB_GENRE_TR_MAP = {
    "Action": "Aksiyon",
    "Adventure": "Macera",
    "Animation": "Animasyon",
    "Comedy": "Komedi",
    "Crime": "Suç",
    "Documentary": "Belgesel",
    "Drama": "Dram",
    "Family": "Aile",
    "Fantasy": "Fantastik",
    "History": "Tarih",
    "Horror": "Korku",
    "Music": "Müzik",
    "Mystery": "Gizem",
    "Romance": "Romantik",
    "Science Fiction": "Bilim Kurgu",
    "TV Movie": "TV Filmi",
    "Thriller": "Gerilim",
    "War": "Savaş",
    "Western": "Western"
}

def translate_tmdb_genres(genres):
    if not genres:
        return []
    return [
        TMDB_GENRE_TR_MAP.get(g.name, g.name)
        for g in genres
        if getattr(g, "name", None)
    ]

def extract_platform(platform_map):
    if not platform_map or not isinstance(platform_map, dict):
        return ""
    return next(iter(platform_map.keys()), "")

# ----------------- Helpers -----------------

def format_tmdb_image(path: str, size="w500") -> str:
    if not path:
        return ""
    return f"https://image.tmdb.org/t/p/{size}{path}"

def get_tmdb_logo(images) -> str:
    if not images:
        return ""
    logos = getattr(images, "logos", None)
    if not logos:
        return ""
    for logo in logos:
        if logo.iso_639_1 == "en" and logo.file_path:
            return format_tmdb_image(logo.file_path, "w300")
    for logo in logos:
        if logo.file_path:
            return format_tmdb_image(logo.file_path, "w300")
    return ""

def format_imdb_images(imdb_id: str) -> dict:
    if not imdb_id:
        return {"poster": "", "backdrop": "", "logo": ""}
    return {
        "poster": f"https://images.metahub.space/poster/small/{imdb_id}/img",
        "backdrop": f"https://images.metahub.space/background/medium/{imdb_id}/img",
        "logo": f"https://images.metahub.space/logo/medium/{imdb_id}/img",
    }

def extract_default_id(url: str):
    imdb_match = re.search(r'/title/(tt\d+)', url)
    if imdb_match:
        return imdb_match.group(1)
    tmdb_match = re.search(r'/(movie|tv)/(\d+)', url)
    if tmdb_match:
        return tmdb_match.group(2)
    return None

# ----------------- Safe Search -----------------

async def safe_imdb_search(title, type_):
    key = f"{type_}:{title}"
    if key in IMDB_CACHE:
        return IMDB_CACHE[key]
    async with API_SEMAPHORE:
        result = await search_title(query=title, type=type_)
    imdb_id = result["id"] if result else None
    IMDB_CACHE[key] = imdb_id
    return imdb_id

async def safe_tmdb_search(title, type_, year=None):
    key = f"{type_}:{title}:{year}"
    if key in TMDB_SEARCH_CACHE:
        return TMDB_SEARCH_CACHE[key]
    async with API_SEMAPHORE:
        if type_ == "movie":
            results = await tmdb.search().movies(query=title, year=year)
        else:
            results = await tmdb.search().tv(query=title)
    res = results[0] if results else None
    TMDB_SEARCH_CACHE[key] = res
    return res

async def _tmdb_movie_details(movie_id):
    if movie_id in TMDB_DETAILS_CACHE:
        return TMDB_DETAILS_CACHE[movie_id]
    async with API_SEMAPHORE:
        details = await tmdb.movie(movie_id).details(
            append_to_response="external_ids,credits,watch/providers"
        )
        images = await tmdb.movie(movie_id).images()
        details.images = images
    TMDB_DETAILS_CACHE[movie_id] = details
    return details

async def _tmdb_tv_details(tv_id):
    if tv_id in TMDB_DETAILS_CACHE:
        return TMDB_DETAILS_CACHE[tv_id]
    async with API_SEMAPHORE:
        details = await tmdb.tv(tv_id).details(
            append_to_response="external_ids,credits,watch/providers"
        )
        images = await tmdb.tv(tv_id).images()
        details.images = images
    TMDB_DETAILS_CACHE[tv_id] = details
    return details

async def _tmdb_episode_details(tv_id, season, episode):
    key = (tv_id, season, episode)
    if key in EPISODE_CACHE:
        return EPISODE_CACHE[key]
    async with API_SEMAPHORE:
        details = await tmdb.episode(tv_id, season, episode).details()
    EPISODE_CACHE[key] = details
    return details

# ----------------- MAIN -----------------

async def metadata(filename: str, channel: int, msg_id):
    parsed = PTN.parse(filename)

    if "excess" in parsed and any("combined" in x.lower() for x in parsed["excess"]):
        return None

    multipart_pattern = compile(r'(?:part|cd|disc)[s._-]*\d+', IGNORECASE)
    if multipart_pattern.search(filename):
        return None

    title = parsed.get("title")
    season = parsed.get("season")
    episode = parsed.get("episode")
    year = parsed.get("year")
    quality = parsed.get("resolution")

    if not title or not quality:
        return None

    default_id = extract_default_id(Backend.USE_DEFAULT_ID) or extract_default_id(filename)

    encoded_string = await encode_string({"chat_id": channel, "msg_id": msg_id})

    if season and episode:
        return await fetch_tv_metadata(title, season, episode, encoded_string, year, quality, default_id)
    return await fetch_movie_metadata(title, encoded_string, year, quality, default_id)

# ----------------- MOVIE -----------------

async def fetch_movie_metadata(title, encoded_string, year, quality, default_id):
    tmdb_id = int(default_id) if default_id and default_id.isdigit() else None

    if not tmdb_id:
        result = await safe_tmdb_search(title, "movie", year)
        if not result:
            return None
        tmdb_id = result.id

    movie = await _tmdb_movie_details(tmdb_id)

    cast = [c.name for c in movie.credits.cast] if movie.credits else []

    providers = getattr(movie, "watch_providers", {})
    platform = extract_platform(
        getattr(getattr(providers, "results", {}), "US", {}).get("flatrate")
        if providers else None
    )

    return {
        "tmdb_id": movie.id,
        "imdb_id": movie.external_ids.imdb_id,
        "title": movie.title,
        "year": movie.release_date.year if movie.release_date else 0,
        "rate": movie.vote_average,
        "description": movie.overview,
        "poster": format_tmdb_image(movie.poster_path),
        "backdrop": format_tmdb_image(movie.backdrop_path, "original"),
        "logo": get_tmdb_logo(movie.images),
        "cast": cast,
        "runtime": f"{movie.runtime} min" if movie.runtime else "",
        "media_type": "movie",
        "genres": translate_tmdb_genres(movie.genres),
        "platform": platform,
        "quality": quality,
        "encoded_string": encoded_string,
    }

# ----------------- TV -----------------

async def fetch_tv_metadata(title, season, episode, encoded_string, year, quality, default_id):
    tmdb_id = int(default_id) if default_id and default_id.isdigit() else None

    if not tmdb_id:
        result = await safe_tmdb_search(title, "tv", year)
        if not result:
            return None
        tmdb_id = result.id

    tv = await _tmdb_tv_details(tmdb_id)
    ep = await _tmdb_episode_details(tmdb_id, season, episode)

    cast = [c.name for c in tv.credits.cast] if tv.credits else []

    providers = getattr(tv, "watch_providers", {})
    platform = extract_platform(
        getattr(getattr(providers, "results", {}), "US", {}).get("flatrate")
        if providers else None
    )

    return {
        "tmdb_id": tv.id,
        "imdb_id": tv.external_ids.imdb_id,
        "title": tv.name,
        "year": tv.first_air_date.year if tv.first_air_date else 0,
        "rate": tv.vote_average,
        "description": tv.overview,
        "poster": format_tmdb_image(tv.poster_path),
        "backdrop": format_tmdb_image(tv.backdrop_path, "original"),
        "logo": get_tmdb_logo(tv.images),
        "cast": cast,
        "runtime": "",
        "media_type": "tv",
        "genres": translate_tmdb_genres(tv.genres),
        "platform": platform,

        "season_number": season,
        "episode_number": episode,
        "episode_title": ep.name if ep else "",
        "episode_backdrop": format_tmdb_image(ep.still_path, "original") if ep else "",
        "episode_overview": ep.overview if ep else "",
        "episode_released": ep.air_date.isoformat() if ep and ep.air_date else "",

        "quality": quality,
        "encoded_string": encoded_string,
    }
