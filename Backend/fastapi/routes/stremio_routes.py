from fastapi import APIRouter, HTTPException
from typing import Optional
from urllib.parse import unquote
from Backend.config import Telegram
from Backend import db, __version__
import PTN
from datetime import datetime, timezone, timedelta
import re


# --- Configuration ---
BASE_URL = Telegram.BASE_URL
ADDON_NAME = "Arşivim"
ADDON_VERSION = __version__
PAGE_SIZE = 15

router = APIRouter(prefix="/stremio", tags=["Stremio Addon"])


# --- Genres ---
GENRES = [
    "Aile", "Aksiyon", "Aksiyon ve Macera", "Animasyon", "Belgesel",
    "Bilim Kurgu", "Bilim Kurgu ve Fantazi", "Biyografi", "Çocuklar",
    "Dram", "Fantastik", "Gerilim", "Gerçeklik", "Gizem", "Haberler",
    "Kara Film", "Komedi", "Korku", "Kısa", "Macera", "Müzik",
    "Müzikal", "Oyun Gösterisi", "Pembe Dizi", "Romantik", "Savaş",
    "Savaş ve Politika", "Spor", "Suç", "TV Filmi", "Talk-Show",
    "Tarih", "Vahşi Batı"
]


# --- Helpers ---
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


# ✅ PLATFORM TESPİTİ
def detect_platform(filename: str) -> Optional[str]:
    name = filename.lower()

    if re.search(r"\bnf\b", name):
        return "Netflix"
    if re.search(r"\bdsnp\b", name):
        return "Disney"
    if re.search(r"\bamzn\b", name):
        return "Amazon"
    if re.search(r"\b(blutv|hbo|max|hbomax)\b", name):
        return "HBO"

    return None


def format_stream_details(filename: str, quality: str, size: str, file_id: str, media_type: str) -> tuple[str, str]:
    if file_id.startswith(("http://", "https://")):
        source_prefix = "Link"
    else:
        source_prefix = "Telegram"

    platform = detect_platform(filename)
    media_label = "Filmleri" if media_type == "movie" else "Dizileri"

    try:
        parsed = PTN.parse(filename)
    except Exception:
        name_parts = [source_prefix]
        if platform:
            name_parts.append(f"{platform} {media_label}")
        name_parts.append(quality)

        return (
            " ".join(name_parts),
            f"📁 {filename}\n💾 {size}"
        )

    codec_parts = []
    if parsed.get("codec"):
        codec_parts.append(f"🎥 {parsed['codec']}")
    if parsed.get("bitDepth"):
        codec_parts.append(f"🔟 {parsed['bitDepth']}bit")
    if parsed.get("audio"):
        codec_parts.append(f"🔊 {parsed['audio']}")
    if parsed.get("encoder"):
        codec_parts.append(f"👤 {parsed['encoder']}")

    codec_info = " ".join(codec_parts)

    resolution = parsed.get("resolution", quality)
    quality_type = parsed.get("quality", "")

    name_parts = [source_prefix]
    if platform:
        name_parts.append(f"{platform} {media_label}")
    name_parts.append(resolution)
    if quality_type:
        name_parts.append(quality_type)

    stream_name = " ".join(name_parts).strip()

    stream_title = "\n".join(
        filter(None, [
            f"📁 {filename}",
            f"💾 {size}",
            codec_info
        ])
    )

    return stream_name, stream_title


def get_resolution_priority(name: str) -> int:
    mapping = {
        "2160p": 2160, "4k": 2160, "uhd": 2160,
        "1080p": 1080, "fhd": 1080,
        "720p": 720, "hd": 720,
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

    match = re.search(r"([\d.]+)\s*(gb|gib|mb|mib)", size_str.lower())
    if not match:
        return 0.0

    value, unit = match.groups()
    value = float(value)

    if unit in ("gb", "gib"):
        return value * 1024
    return value


# --- Manifest ---
@router.get("/manifest.json")
async def manifest():
    return {
        "id": "telegram.media",
        "version": ADDON_VERSION,
        "name": ADDON_NAME,
        "description": "Dizi ve film arşivim.",
        "types": ["movie", "series"],
        "resources": ["catalog", "meta", "stream"],
        "catalogs": [
            {"type": "movie", "id": "latest_movies", "name": "Latest", "extra": [{"name": "genre", "options": GENRES}, {"name": "skip"}], "extraSupported": ["genre", "skip"]},
            {"type": "movie", "id": "top_movies", "name": "Popular", "extra": [{"name": "genre", "options": GENRES}, {"name": "skip"}], "extraSupported": ["genre", "skip"]},
            {"type": "series", "id": "latest_series", "name": "Latest", "extra": [{"name": "genre", "options": GENRES}, {"name": "skip"}], "extraSupported": ["genre", "skip"]},
            {"type": "series", "id": "top_series", "name": "Popular", "extra": [{"name": "genre", "options": GENRES}, {"name": "skip"}], "extraSupported": ["genre", "skip"]},
        ],
    }


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

        name, title = format_stream_details(filename, quality, size, file_id, media_type)

        url = (
            file_id
            if file_id.startswith(("http://", "https://"))
            else f"{BASE_URL}/dl/{file_id}/video.mkv"
        )

        streams.append({
            "name": name,
            "title": title,
            "url": url,
            "_size": parse_size(size)
        })

    streams.sort(
        key=lambda s: (
            get_resolution_priority(s["name"]),
            s["_size"]
        ),
        reverse=True
    )

    for s in streams:
        s.pop("_size", None)

    return {"streams": streams}
