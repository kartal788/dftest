from fastapi import APIRouter, HTTPException
from typing import Optional
from urllib.parse import unquote
from Backend.config import Telegram
from Backend import db, __version__
import PTN
from datetime import datetime, timezone, timedelta

# -------------------------------------------------
# Configuration
# -------------------------------------------------

BASE_URL = Telegram.BASE_URL
ADDON_NAME = "Arşivim"
ADDON_VERSION = __version__
PAGE_SIZE = 15

router = APIRouter(prefix="/stremio", tags=["Stremio Addon"])

# -------------------------------------------------
# Genres
# -------------------------------------------------

GENRES = [
    "Aile", "Aksiyon", "Aksiyon ve Macera", "Animasyon", "Belgesel",
    "Bilim Kurgu", "Bilim Kurgu ve Fantazi", "Biyografi", "Çocuklar",
    "Dram", "Fantastik", "Gerilim", "Gerçeklik", "Gizem", "Haberler",
    "Kara Film", "Komedi", "Korku", "Kısa", "Macera", "Müzik",
    "Müzikal", "Oyun Gösterisi", "Pembe Dizi", "Romantik", "Savaş",
    "Savaş ve Politika", "Spor", "Suç", "TV Filmi", "Talk-Show",
    "Tarih", "Vahşi Batı"
]

# -------------------------------------------------
# Helpers
# -------------------------------------------------

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


def get_resolution_priority(name: str) -> int:
    mapping = {
        "2160p": 2160, "4k": 2160,
        "1080p": 1080,
        "720p": 720,
        "480p": 480,
        "360p": 360,
    }
    for k, v in mapping.items():
        if k in name.lower():
            return v
    return 1


def parse_size(size_str: str) -> float:
    if not size_str:
        return 0.0
    size_str = size_str.lower().replace(" ", "")
    try:
        if "gb" in size_str:
            return float(size_str.replace("gb", "")) * 1024
        if "mb" in size_str:
            return float(size_str.replace("mb", ""))
    except ValueError:
        pass
    return 0.0


def parse_dt(v: str) -> datetime:
    try:
        return datetime.fromisoformat(v.split(".")[0])
    except Exception:
        return datetime.min

# -------------------------------------------------
# PLATFORM LOGIC
# -------------------------------------------------

PLATFORM_KEYWORDS = {
    "Netflix": ["nf", "netflix"],
    "Disney": ["dsnp", "disney"],
    "Amazon": ["amzn", "amazon"],
    "Hbo": ["blutv", "hbo", "hbomax", "max"],
}


def detect_platform(filename: str) -> Optional[str]:
    name = filename.lower()
    for platform, keys in PLATFORM_KEYWORDS.items():
        if any(k in name for k in keys):
            return platform
    return None


def platform_section_name(item: dict, platform: str) -> str:
    return f"{platform} Filmleri" if item.get("media_type") == "movie" else f"{platform} Dizileri"


def build_platform_catalog(items: list[dict]) -> dict:
    sections = {}

    for item in items:
        for tg in item.get("telegram", []):
            platform = detect_platform(tg.get("name", ""))
            if not platform:
                continue

            section = platform_section_name(item, platform)

            sections.setdefault(section, {
                "updated_on": [],
                "rating": [],
                "released": []
            })

            sections[section]["updated_on"].append(item)
            sections[section]["rating"].append(item)
            sections[section]["released"].append(item)
            break

    for sec in sections.values():
        sec["updated_on"].sort(key=lambda x: parse_dt(x.get("updated_on", "")), reverse=True)
        sec["rating"].sort(key=lambda x: x.get("rating", 0), reverse=True)
        sec["released"].sort(key=lambda x: x.get("release_year", 0), reverse=True)

    return sections

# -------------------------------------------------
# Manifest
# -------------------------------------------------

@router.get("/manifest.json")
async def manifest():
    catalogs = [
        {
            "type": "movie",
            "id": "latest_movies",
            "name": "Latest",
            "extra": [{"name": "genre", "options": GENRES}, {"name": "skip"}],
            "extraSupported": ["genre", "skip"]
        },
        {
            "type": "movie",
            "id": "top_movies",
            "name": "Popular",
            "extra": [{"name": "genre", "options": GENRES}, {"name": "skip"}],
            "extraSupported": ["genre", "skip"]
        },
        {
            "type": "series",
            "id": "latest_series",
            "name": "Latest",
            "extra": [{"name": "genre", "options": GENRES}, {"name": "skip"}],
            "extraSupported": ["genre", "skip"]
        },
        {
            "type": "series",
            "id": "top_series",
            "name": "Popular",
            "extra": [{"name": "genre", "options": GENRES}, {"name": "skip"}],
            "extraSupported": ["genre", "skip"]
        },
    ]

    for p in ["netflix", "disney", "amazon", "hbo"]:
        catalogs.append({
            "type": "movie",
            "id": f"platform_{p}",
            "name": f"{p.capitalize()} Filmleri",
            "extra": [{"name": "skip"}],
            "extraSupported": ["skip"]
        })
        catalogs.append({
            "type": "series",
            "id": f"platform_{p}",
            "name": f"{p.capitalize()} Dizileri",
            "extra": [{"name": "skip"}],
            "extraSupported": ["skip"]
        })

    return {
        "id": "telegram.media",
        "version": ADDON_VERSION,
        "name": ADDON_NAME,
        "description": "Dizi ve film arşivim.",
        "types": ["movie", "series"],
        "resources": ["catalog", "meta", "stream"],
        "catalogs": catalogs,
    }

# -------------------------------------------------
# Platform Catalog
# -------------------------------------------------

@router.get("/catalog/{media_type}/platform_{platform}/{extra:path}.json")
@router.get("/catalog/{media_type}/platform_{platform}.json")
async def platform_catalog(media_type: str, platform: str, extra: Optional[str] = None):
    stremio_skip = 0
    if extra:
        for p in extra.replace("&", "/").split("/"):
            if p.startswith("skip="):
                stremio_skip = int(p[5:] or 0)

    if media_type == "movie":
        data = await db.sort_movies([], 1, 500)
        items = data.get("movies", [])
    else:
        data = await db.sort_tv_shows([], 1, 500)
        items = data.get("tv_shows", [])

    sections = build_platform_catalog(items)
    platform_name = platform.capitalize()
    section = f"{platform_name} Filmleri" if media_type == "movie" else f"{platform_name} Dizileri"

    selected = sections.get(section, [])
    metas = selected["updated_on"][stremio_skip: stremio_skip + PAGE_SIZE] if selected else []

    return {"metas": [convert_to_stremio_meta(i) for i in metas]}
