from fastapi import APIRouter
from typing import Optional
from urllib.parse import unquote
from datetime import datetime, timezone, timedelta
import PTN

from Backend.config import Telegram
from Backend import db, __version__

router = APIRouter(prefix="/stremio", tags=["Stremio Addon"])

BASE_URL = Telegram.BASE_URL
ADDON_NAME = "Arşivim"
ADDON_VERSION = __version__
PAGE_SIZE = 15

# -------------------------------------------------
# PLATFORM KURALLARI
# -------------------------------------------------
PLATFORM_RULES = {
    "Netflix": ["nf"],
    "Disney": ["dsnp"],
    "Amazon": ["amzn"],
    "HBO": ["blutv", "hbo", "hbomax"]
}

GENRES = [
    "Aksiyon", "Komedi", "Dram", "Bilim Kurgu",
    "Korku", "Romantik", "Animasyon",
    "Belgesel", "Macera"
]

# -------------------------------------------------
# PLATFORM ALGILAMA
# -------------------------------------------------
def detect_platforms(filename: str) -> list[str]:
    if not filename:
        return []
    name = filename.lower()
    return [
        platform
        for platform, keys in PLATFORM_RULES.items()
        if any(k in name for k in keys)
    ]

# -------------------------------------------------
# STREMIO META FORMAT
# -------------------------------------------------
def convert_to_stremio_meta(item: dict) -> dict:
    media_type = "series" if item.get("media_type") == "tv" else "movie"
    stremio_id = f"{item['tmdb_id']}-{item['db_index']}"

    return {
        "id": stremio_id,
        "type": media_type,
        "name": item.get("title"),
        "poster": item.get("poster"),
        "background": item.get("backdrop"),
        "logo": item.get("logo"),
        "description": item.get("description"),
        "genres": item.get("genres", []),
        "imdbRating": item.get("rating"),
        "year": item.get("release_year"),
        "runtime": item.get("runtime"),
        "cast": item.get("cast", [])
    }

# -------------------------------------------------
# MANIFEST (TEK SATIR PLATFORM)
# -------------------------------------------------
@router.get("/manifest.json")
async def manifest():
    catalogs = []

    for platform in PLATFORM_RULES.keys():
        for media_type, label in [("movie", "Filmleri"), ("series", "Dizileri")]:
            catalogs.append({
                "type": media_type,
                "id": f"{platform.lower()}_{media_type}",
                "name": f"{platform} {label}",
                "extra": [
                    {
                        "name": "sort",
                        "options": ["popular", "released"]
                    },
                    {
                        "name": "genre",
                        "options": GENRES
                    }
                ],
                "extraSupported": ["sort", "genre", "skip"]
            })

    return {
        "id": "telegram.media",
        "version": ADDON_VERSION,
        "name": ADDON_NAME,
        "description": "Platform bazlı arşiv",
        "types": ["movie", "series"],
        "resources": ["catalog", "meta", "stream"],
        "catalogs": catalogs
    }

# -------------------------------------------------
# CATALOG
# -------------------------------------------------
@router.get("/catalog/{media_type}/{id}/{extra:path}.json")
@router.get("/catalog/{media_type}/{id}.json")
async def catalog(media_type: str, id: str, extra: Optional[str] = None):
    skip = 0
    genre = None
    sort_mode = None

    if extra:
        for p in extra.replace("&", "/").split("/"):
            if p.startswith("skip="):
                skip = int(p.replace("skip=", ""))
            elif p.startswith("genre="):
                genre = unquote(p.replace("genre=", ""))
            elif p.startswith("sort="):
                sort_mode = p.replace("sort=", "")

    page = (skip // PAGE_SIZE) + 1

    # Default sort
    sort = [("updated_on", "desc")]

    if sort_mode == "popular":
        sort = [("rating", "desc")]
    elif sort_mode == "released":
        sort = [("released" if media_type == "series" else "updated_on", "desc")]

    parts = id.split("_")
    platform = parts[0].capitalize() if parts[0].capitalize() in PLATFORM_RULES else None

    # DB fetch
    if media_type == "movie":
        data = await db.sort_movies(sort, page, PAGE_SIZE, genre)
        items = data.get("movies", [])
    else:
        data = await db.sort_tv_shows(sort, page, PAGE_SIZE, genre)
        items = data.get("tv_shows", [])

    # Platform filtre
    if platform:
        filtered = []
        for item in items:
            for t in item.get("telegram", []):
                if platform in detect_platforms(t.get("name", "")):
                    filtered.append(item)
                    break
        items = filtered

    return {"metas": [convert_to_stremio_meta(i) for i in items]}

# -------------------------------------------------
# META
# -------------------------------------------------
@router.get("/meta/{media_type}/{id}.json")
async def meta(media_type: str, id: str):
    tmdb_id, db_index = map(int, id.split("-"))
    media = await db.get_media_details(tmdb_id, db_index)

    meta_obj = convert_to_stremio_meta(media)

    if media_type == "series":
        videos = []
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()

        for s in media.get("seasons", []):
            for e in s.get("episodes", []):
                videos.append({
                    "id": f"{id}:{s['season_number']}:{e['episode_number']}",
                    "title": e.get("title"),
                    "season": s["season_number"],
                    "episode": e["episode_number"],
                    "released": e.get("released") or yesterday,
                    "overview": e.get("overview")
                })

        meta_obj["videos"] = videos

    return {"meta": meta_obj}

# -------------------------------------------------
# STREAM
# -------------------------------------------------
@router.get("/stream/{media_type}/{id}.json")
async def streams(media_type: str, id: str):
    parts = id.split(":")
    tmdb_id, db_index = map(int, parts[0].split("-"))

    season = int(parts[1]) if len(parts) > 1 else None
    episode = int(parts[2]) if len(parts) > 2 else None

    media = await db.get_media_details(tmdb_id, db_index, season, episode)
    if not media or "telegram" not in media:
        return {"streams": []}

    streams = []

    for q in media["telegram"]:
        filename = q.get("name", "")
        quality = q.get("quality", "HD")
        size = q.get("size", "")
        file_id = q["id"]

        try:
            parsed = PTN.parse(filename)
            resolution = parsed.get("resolution", quality)
            codec = parsed.get("codec", "")
        except Exception:
            resolution = quality
            codec = ""

        url = (
            file_id
            if file_id.startswith(("http://", "https://"))
            else f"{BASE_URL}/dl/{file_id}/video.mkv"
        )

        streams.append({
            "name": f"Telegram {resolution}",
            "title": f"📁 {filename}\n💾 {size}\n🎥 {codec}",
            "url": url
        })

    return {"streams": streams}
