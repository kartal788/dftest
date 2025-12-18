from fastapi import APIRouter, HTTPException
from typing import Optional, List, Dict, Any
from urllib.parse import unquote
from datetime import datetime, timezone, timedelta
import PTN

# Backend bileşenleri
from Backend.config import Telegram
from Backend import db, __version__

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
        "releaseInfo": str(item.get("release_year")),
        "imdb_id": item.get("imdb_id", ""),
        "moviedb_id": item.get("tmdb_id", ""),
        "background": item.get("backdrop") or "",
        "genres": item.get("genres") or [],
        "imdbRating": str(item.get("rating") or ""),
        "description": item.get("description") or "",
        "cast": item.get("cast") or [],
        "runtime": item.get("runtime") or "",
    }

def format_stream_details(filename: str, quality: str, size: str, file_id: str) -> tuple[str, str]:
    if file_id.startswith("http://") or file_id.startswith("https://"):
        source_prefix = "Link"
    else:
        source_prefix = "Telegram"

    try:
        parsed = PTN.parse(filename)
    except Exception:
        return (
            f"{source_prefix} {quality}",
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
    stream_name = f"{source_prefix} {resolution} {quality_type}".strip()

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

# --- "Yeni Bölüm" (Released) Katalog Sıralama Motoru ---
async def get_released_series_logic(page: int, page_size: int, genre: Optional[str] = None):
    """
    Dizileri bölümlerinin 'released' tarihine göre MongoDB pipeline ile sıralar.
    Python tarafındaki limitli sıralama sorununu kökten çözer.
    """
    skip = (page - 1) * page_size
    match_query = {"genres": {"$in": [genre]}} if genre else {}
    
    pipeline = [
        {"$match": match_query},
        {
            "$addFields": {
                "max_released": {
                    "$max": {
                        "$map": {
                            "input": "$seasons",
                            "as": "s",
                            "in": {"$max": "$$s.episodes.released"}
                        }
                    }
                }
            }
        },
        {"$sort": {"max_released": -1}},
        {"$skip": skip},
        {"$limit": page_size}
    ]

    results = []
    # Mevcut db yapısına göre tüm storage'ları tarar
    for i in range(1, db.current_db_index + 1):
        db_key = f"storage_{i}"
        cursor = db.dbs[db_key]["tv"].aggregate(pipeline)
        docs = await cursor.to_list(None)
        results.extend(docs)

    # Farklı DB'lerden gelen veriyi ortak paydada tekrar sıralar
    results.sort(key=lambda x: x.get("max_released", "") or "", reverse=True)
    from Backend.database import convert_objectid_to_str
    return [convert_objectid_to_str(r) for r in results[:page_size]]

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
        "logo": "https://i.postimg.cc/XqWnmDXr/Picsart-25-10-09-08-09-45-867.png",
        "catalogs": [
            {"type": "series", "id": "released", "name": "Yeni Bölüm", "extra": [{"name": "skip"}], "extraSupported": ["skip"]},
            {"type": "movie", "id": "latest_movies", "name": "Yeni Eklenen", "extra": [{"name": "genre", "options": GENRES}, {"name": "skip"}], "extraSupported": ["genre", "skip"]},
            {"type": "movie", "id": "top_movies", "name": "Popüler", "extra": [{"name": "genre", "options": GENRES}, {"name": "skip"}], "extraSupported": ["genre", "skip"]},
            {"type": "series", "id": "latest_series", "name": "Yeni Eklenen", "extra": [{"name": "genre", "options": GENRES}, {"name": "skip"}], "extraSupported": ["genre", "skip"]},
            {"type": "series", "id": "top_series", "name": "Popüler", "extra": [{"name": "genre", "options": GENRES}, {"name": "skip"}], "extraSupported": ["genre", "skip"]},
            {"type": "movie", "id": "movies_2025", "name": "2025 Filmleri", "extra": [{"name": "genre", "options": GENRES}, {"name": "skip"}], "extraSupported": ["genre", "skip"]},
            {"type": "movie", "id": "movies_2024", "name": "2024 Filmleri", "extra": [{"name": "genre", "options": GENRES}, {"name": "skip"}], "extraSupported": ["genre", "skip"]}
        ],
    }

# --- Catalog ---
@router.get("/catalog/{media_type}/{id}/{extra:path}.json")
@router.get("/catalog/{media_type}/{id}.json")
async def catalog(media_type: str, id: str, extra: Optional[str] = None):
    stremio_skip = 0
    genre = None

    if extra:
        for p in extra.replace("&", "/").split("/"):
            if p.startswith("genre="):
                genre = unquote(p[6:])
            elif p.startswith("skip="):
                stremio_skip = int(p[5:] or 0)

    page = (stremio_skip // PAGE_SIZE) + 1

    if media_type == "movie":
        if id == "movies_2025":
            sort = [("updated_on", "desc")]
            all_movies = await db.sort_movies(sort, page, PAGE_SIZE, genre)
            items = [m for m in all_movies.get("movies", []) if m.get("release_year") == 2025]
        elif id == "movies_2024":
            sort = [("updated_on", "desc")]
            all_movies = await db.sort_movies(sort, page, PAGE_SIZE, genre)
            items = [m for m in all_movies.get("movies", []) if m.get("release_year") == 2024]
        elif "top" in id:
            sort = [("rating", "desc")]
            items = (await db.sort_movies(sort, page, PAGE_SIZE, genre)).get("movies", [])
        else:
            sort = [("updated_on", "desc")]
            items = (await db.sort_movies(sort, page, PAGE_SIZE, genre)).get("movies", [])
    else:  # series
        if id == "released":
            # DÜZELTİLEN KISIM: Özel sıralama mantığı çağrılıyor
            items = await get_released_series_logic(page, PAGE_SIZE, genre)
        elif "top" in id:
            sort = [("rating", "desc")]
            data = await db.sort_tv_shows(sort, page, PAGE_SIZE, genre)
            items = data.get("tv_shows", [])
        else:
            sort = [("updated_on", "desc")]
            data = await db.sort_tv_shows(sort, page, PAGE_SIZE, genre)
            items = data.get("tv_shows", [])

    return {"metas": [convert_to_stremio_meta(i) for i in items]}

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
                    "thumbnail": e.get("episode_backdrop") or "https://raw.githubusercontent.com/weebzone/Colab-Tools/refs/heads/main/no_episode_backdrop.png",
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

    streams_out = []
    for q in media["telegram"]:
        file_id = q["id"]
        filename = q.get("name", "")
        quality = q.get("quality", "HD")
        size = q.get("size", "")

        name, title = format_stream_details(filename, quality, size, file_id)

        url = (
            file_id
            if file_id.startswith(("http://", "https://"))
            else f"{BASE_URL}/dl/{file_id}/video.mkv"
        )

        streams_out.append({
            "name": name,
            "title": title,
            "url": url,
            "_size": parse_size(size)
        })

    # Sıralama: Çözünürlük ve Boyut
    streams_out.sort(
        key=lambda s: (
            get_resolution_priority(s["name"]),
            s["_size"]
        ),
        reverse=True
    )

    for s in streams_out:
        s.pop("_size", None)

    return {"streams": streams_out}
