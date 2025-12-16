from fastapi import APIRouter
from typing import Optional, Set
from urllib.parse import unquote
from datetime import datetime, timezone, timedelta
import PTN

from Backend import db, __version__
from Backend.config import Telegram


# ================= CONFIG =================
router = APIRouter(prefix="/stremio", tags=["Stremio Addon"])

BASE_URL = Telegram.BASE_URL
ADDON_NAME = "Arşivim"
ADDON_VERSION = __version__
PAGE_SIZE = 15


# ================= GENRES =================
GENRES = [
    "Aile", "Aksiyon", "Aksiyon ve Macera", "Animasyon", "Belgesel",
    "Bilim Kurgu", "Biyografi", "Çocuklar", "Dram", "Fantastik",
    "Gerilim", "Gizem", "Komedi", "Korku", "Macera", "Müzik",
    "Romantik", "Savaş", "Suç", "Tarih",
    "Netflix", "Disney", "Amazon", "HBO", "BluTV", "Tv+"
]


# ================= PLATFORMS =================
PLATFORM_MAP = {
    "nf": "Netflix",
    "dsnp": "Disney",
    "amzn": "Amazon",
    "blutv": "HBO",
    "hbo": "HBO",
    "hbomax": "HBO"
}

PLATFORMS = ["Netflix", "Amazon", "Disney", "HBO"]


# ================= HELPERS =================
def detect_platforms_from_name(filename: str) -> Set[str]:
    platforms = set()
    try:
        parsed = PTN.parse(filename)
        text = " ".join(str(v).lower() for v in parsed.values())
    except Exception:
        text = filename.lower()

    for key, platform in PLATFORM_MAP.items():
        if key in text:
            platforms.add(platform)

    return platforms


def extract_platforms_from_media(item: dict) -> Set[str]:
    platforms = set()

    for t in item.get("telegram", []):
        platforms |= detect_platforms_from_name(t.get("name", ""))

    for s in item.get("seasons", []):
        for e in s.get("episodes", []):
            for t in e.get("telegram", []):
                platforms |= detect_platforms_from_name(t.get("name", ""))

    return platforms


def convert_to_stremio_meta(item: dict) -> dict:
    media_type = "series" if item.get("media_type") == "tv" else "movie"
    stremio_id = f"{item['tmdb_id']}-{item['db_index']}"

    return {
        "id": stremio_id,
        "type": media_type,
        "name": item.get("title"),
        "poster": item.get("poster", ""),
        "logo": item.get("logo", ""),
        "background": item.get("backdrop", ""),
        "year": item.get("release_year"),
        "imdbRating": item.get("rating"),
        "genres": item.get("genres", []),
        "description": item.get("description", ""),
        "cast": item.get("cast", []),
        "runtime": item.get("runtime", "")
    }


# ================= MANIFEST =================
@router.get("/manifest.json")
async def manifest():
    catalogs = []

    # Platform catalogs
    for p in PLATFORMS:
        pid = p.lower()
        catalogs.extend([
            {"type": "movie", "id": f"{pid}_latest_movies", "name": f"{p} Filmleri", "extraSupported": ["skip"]},
            {"type": "movie", "id": f"{pid}_top_movies", "name": f"{p} Popüler Filmler", "extraSupported": ["skip"]},
            {"type": "series", "id": f"{pid}_latest_series", "name": f"{p} Dizileri", "extraSupported": ["skip"]},
            {"type": "series", "id": f"{pid}_top_series", "name": f"{p} Popüler Diziler", "extraSupported": ["skip"]},
        ])

    # Genre catalogs
    for g in GENRES:
        gid = g.lower().replace(" ", "_")
        catalogs.extend([
            {
                "type": "movie",
                "id": f"genre_{gid}_movies",
                "name": f"{g} Filmleri",
                "extra": [{"name": "skip"}],
                "extraSupported": ["skip"]
            },
            {
                "type": "series",
                "id": f"genre_{gid}_series",
                "name": f"{g} Dizileri",
                "extra": [{"name": "skip"}],
                "extraSupported": ["skip"]
            }
        ])

    return {
        "id": "telegram.media",
        "version": ADDON_VERSION,
        "name": ADDON_NAME,
        "description": "Platform & tür bazlı film-dizi arşivi",
        "types": ["movie", "series"],
        "resources": ["catalog", "meta", "stream"],
        "catalogs": catalogs
    }


# ================= CATALOG =================
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
    genre = None

    for p in PLATFORMS:
        if p.lower() in id:
            platform = p
            break

    if id.startswith("genre_"):
        genre = id.replace("genre_", "").replace("_movies", "").replace("_series", "")
        genre = genre.replace("_", " ").title()

    sort = [("updated_on", "desc")]
    if "top" in id:
        sort = [("rating", "desc")]

    if media_type == "movie":
        data = await db.sort_movies(sort, page, PAGE_SIZE, genre)
        items = data.get("movies", [])
    else:
        data = await db.sort_tv_shows(sort, page, PAGE_SIZE, genre)
        items = data.get("tv_shows", [])

    result = []
    for item in items:
        if platform:
            platforms = extract_platforms_from_media(item)
            if platform not in platforms:
                continue
        result.append(item)

    return {"metas": [convert_to_stremio_meta(i) for i in result]}


# ================= META =================
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
                    "overview": e.get("overview", "")
                })

        meta_obj["videos"] = videos

    return {"meta": meta_obj}


# ================= STREAM =================
@router.get("/stream/{media_type}/{id}.json")
async def stream(media_type: str, id: str):
    parts = id.split(":")
    tmdb_id, db_index = map(int, parts[0].split("-"))
    season = int(parts[1]) if len(parts) > 1 else None
    episode = int(parts[2]) if len(parts) > 2 else None

    media = await db.get_media_details(tmdb_id, db_index, season, episode)
    if not media or "telegram" not in media:
        return {"streams": []}

    streams = []
    for t in media["telegram"]:
        file_id = t["id"]
        name = t.get("name", "")
        size = t.get("size", "")
        quality = t.get("quality", "")

        url = file_id if file_id.startswith("http") else f"{BASE_URL}/dl/{file_id}/video.mkv"
        platforms = ", ".join(detect_platforms_from_name(name)) or "Telegram"

        streams.append({
            "name": f"{platforms} {quality}",
            "title": f"📁 {name}\n💾 {size}",
            "url": url
        })

    return {"streams": streams}
