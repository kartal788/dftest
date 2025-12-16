from fastapi import APIRouter, HTTPException
from typing import Optional
from urllib.parse import unquote
from Backend.config import Telegram
from Backend import db, __version__
import PTN
from datetime import datetime, timezone, timedelta


# --- Configuration ---
BASE_URL = Telegram.BASE_URL
ADDON_NAME = "Arşivim"
ADDON_VERSION = __version__
PAGE_SIZE = 15

router = APIRouter(prefix="/stremio", tags=["Stremio Addon"])


# --- Genres ---
GENRES = [
    "Aile", "Aksiyon", "Animasyon", "Belgesel", "Bilim Kurgu",
    "Biyografi", "Çocuklar", "Dram", "Fantastik", "Gerilim",
    "Gizem", "Komedi", "Korku", "Macera", "Müzik",
    "Romantik", "Savaş", "Spor", "Suç", "Tarih"
]

# --- Platform Mapping ---
PLATFORM_TAGS = {
    "netflix": ["nf"],
    "disney": ["dsnp"],
    "amazon": ["amzn"],
    "hbo": ["blutv", "hbo", "hbomax"],
}


# --- Helpers ---
def detect_platform_from_item(item: dict) -> Optional[str]:
    """
    Telegram filename'larından PTN ile platform algılar
    """
    for q in item.get("telegram", []):
        name = q.get("name", "")
        try:
            parsed = PTN.parse(name)
        except Exception:
            continue

        release = (parsed.get("releaseGroup") or "").lower()
        extra = name.lower()

        for platform, tags in PLATFORM_TAGS.items():
            for t in tags:
                if t in release or f".{t}." in extra or f" {t} " in extra:
                    return platform
    return None


def convert_to_stremio_meta(item: dict) -> dict:
    media_type = "series" if item.get("media_type") == "tv" else "movie"
    stremio_id = f"{item.get('tmdb_id')}-{item.get('db_index')}"

    return {
        "id": stremio_id,
        "type": media_type,
        "name": item.get("title"),
        "poster": item.get("poster") or "",
        "logo": item.get("logo") or "",
        "year": item.get("release_year"),
        "releaseInfo": item.get("release_year"),
        "imdb_id": item.get("imdb_id", ""),
        "moviedb_id": item.get("tmdb_id", ""),
        "background": item.get("backdrop") or "",
        "genres": item.get("genres") or [],
        "imdbRating": item.get("rating") or "",
        "description": item.get("description") or "",
        "cast": item.get("cast") or [],
        "runtime": item.get("runtime") or "",
    }


# --- Manifest ---
@router.get("/manifest.json")
async def manifest():
    catalogs = []

    for platform in ["netflix", "amazon", "disney", "hbo"]:
        for media_type in ["movie", "series"]:
            label = f"{platform.capitalize()} {'Filmleri' if media_type == 'movie' else 'Dizileri'}"

            catalogs.append({
                "type": media_type,
                "id": f"{platform}_{media_type}_latest",
                "name": f"{label} • Son Eklenenler",
                "extraSupported": ["skip"]
            })
            catalogs.append({
                "type": media_type,
                "id": f"{platform}_{media_type}_top",
                "name": f"{label} • Popüler",
                "extraSupported": ["skip"]
            })

    return {
        "id": "telegram.media",
        "version": ADDON_VERSION,
        "name": ADDON_NAME,
        "description": "Platform bazlı dizi ve film arşivi",
        "types": ["movie", "series"],
        "resources": ["catalog", "meta", "stream"],
        "catalogs": catalogs,
    }


# --- Catalog ---
@router.get("/catalog/{media_type}/{id}/{extra:path}.json")
@router.get("/catalog/{media_type}/{id}.json")
async def catalog(media_type: str, id: str, extra: Optional[str] = None):
    skip = 0
    if extra:
        for p in extra.replace("&", "/").split("/"):
            if p.startswith("skip="):
                skip = int(p[5:] or 0)

    page = (skip // PAGE_SIZE) + 1

    platform = None
    for p in PLATFORM_TAGS:
        if id.startswith(p):
            platform = p
            break

    is_top = id.endswith("_top")
    sort = [("rating", "desc")] if is_top else [("updated_on", "desc")]

    if media_type == "movie":
        data = await db.sort_movies(sort, page, PAGE_SIZE, None)
        items = data.get("movies", [])
    else:
        data = await db.sort_tv_shows(sort, page, PAGE_SIZE, None)
        items = data.get("tv_shows", [])

    if platform:
        items = [
            i for i in items
            if detect_platform_from_item(i) == platform
        ]

    return {
        "metas": [convert_to_stremio_meta(i) for i in items]
    }


# --- Meta ---
@router.get("/meta/{media_type}/{id}.json")
async def meta(media_type: str, id: str):
    tmdb_id, db_index = map(int, id.split("-"))
    media = await db.get_media_details(tmdb_id, db_index)

    if not media:
        return {"meta": {}}

    meta_obj = convert_to_stremio_meta(media)

    if media_type == "series":
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        videos = []

        for s in media.get("seasons", []):
            for e in s.get("episodes", []):
                videos.append({
                    "id": f"{id}:{s['season_number']}:{e['episode_number']}",
                    "title": e.get("title"),
                    "season": s["season_number"],
                    "episode": e["episode_number"],
                    "released": e.get("released") or yesterday,
                    "overview": e.get("overview"),
                })

        meta_obj["videos"] = videos

    return {"meta": meta_obj}


# --- Streams ---
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
        file_id = q["id"]
        filename = q.get("name", "")
        quality = q.get("quality", "HD")
        size = q.get("size", "")

        url = (
            file_id
            if file_id.startswith(("http://", "https://"))
            else f"{BASE_URL}/dl/{file_id}/video.mkv"
        )

        streams.append({
            "name": quality,
            "title": f"📁 {filename}\n💾 {size}",
            "url": url
        })

    return {"streams": streams}
