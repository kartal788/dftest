from fastapi import APIRouter, HTTPException
from typing import Optional
from urllib.parse import unquote
from Backend.config import Telegram
from Backend import db, __version__
import PTN
from datetime import datetime, timezone, timedelta
from dateutil.parser import parse as parse_date


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


# --- Helper Functions ---
def convert_to_stremio_meta(item: dict) -> dict:
    media_type = "series" if item.get("media_type") == "tv" else "movie"
    stremio_id = f"{item.get('tmdb_id')}-{item.get('db_index')}"

    meta = {
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
    return meta


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
        "2160p": 2160, "4k": 2160, "uhd": 2160,
        "1080p": 1080, "fhd": 1080,
        "720p": 720, "hd": 720,
        "480p": 480, "sd": 480,
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


# --- Manifest ---
@router.get("/manifest.json")
async def get_manifest():
    return {
        "id": "telegram.media",
        "version": ADDON_VERSION,
        "name": ADDON_NAME,
        "logo": "https://i.postimg.cc/XqWnmDXr/Picsart-25-10-09-08-09-45-867.png",
        "description": "Dizi ve film arşivim.",
        "types": ["movie", "series"],
        "resources": ["catalog", "meta", "stream"],
        "catalogs": [
            {
                "type": "movie",
                "id": "latest_movies",
                "name": "Yeni eklenenler",
                "extra": [
                    {"name": "genre", "isRequired": False, "options": GENRES},
                    {"name": "skip"}
                ],
                "extraSupported": ["genre", "skip"]
            },
            {
                "type": "movie",
                "id": "top_movies",
                "name": "Popüler",
                "extra": [
                    {"name": "genre", "isRequired": False, "options": GENRES},
                    {"name": "skip"},
                    {"name": "search", "isRequired": False}
                ],
                "extraSupported": ["genre", "skip", "search"]
            },
            {
                "type": "series",
                "id": "latest_series",
                "name": "Yeni eklenenler",
                "extra": [
                    {"name": "genre", "isRequired": False, "options": GENRES},
                    {"name": "skip"}
                ],
                "extraSupported": ["genre", "skip"]
            },
            {
                "type": "series",
                "id": "top_series",
                "name": "Popüler",
                "extra": [
                    {"name": "genre", "isRequired": False, "options": GENRES},
                    {"name": "skip"},
                    {"name": "search", "isRequired": False}
                ],
                "extraSupported": ["genre", "skip", "search"]
            }
        ],
        "idPrefixes": [""],
        "behaviorHints": {
            "configurable": False,
            "configurationRequired": False
        }
    }


# --- Catalog ---
@router.get("/catalog/{media_type}/{id}/{extra:path}.json")
@router.get("/catalog/{media_type}/{id}.json")
async def get_catalog(media_type: str, id: str, extra: Optional[str] = None):
    if media_type not in ["movie", "series"]:
        raise HTTPException(status_code=404, detail="Invalid catalog type")

    genre_filter = None
    search_query = None
    stremio_skip = 0

    if extra:
        params = extra.replace("&", "/").split("/")
        for param in params:
            if param.startswith("genre="):
                genre_filter = unquote(param.removeprefix("genre="))
            elif param.startswith("search="):
                search_query = unquote(param.removeprefix("search="))
            elif param.startswith("skip="):
                try:
                    stremio_skip = int(param.removeprefix("skip="))
                except ValueError:
                    stremio_skip = 0

    page = (stremio_skip // PAGE_SIZE) + 1

    try:
        if search_query:
            search_results = await db.search_documents(query=search_query, page=page, page_size=PAGE_SIZE)
            all_items = search_results.get("results", [])
            db_media_type = "tv" if media_type == "series" else "movie"
            items = [item for item in all_items if item.get("media_type") == db_media_type]
        else:
            if "latest" in id:
                sort_params = [("updated_on", "desc")]
            elif "top" in id:
                sort_params = [("rating", "desc")]
            else:
                sort_params = [("updated_on", "desc")]

            if media_type == "movie":
                data = await db.sort_movies(sort_params, page, PAGE_SIZE, genre_filter=genre_filter)
                items = data.get("movies", [])
            else:
                data = await db.sort_tv_shows(sort_params, page, PAGE_SIZE, genre_filter=genre_filter)
                items = data.get("tv_shows", [])
    except Exception:
        return {"metas": []}

    metas = [convert_to_stremio_meta(item) for item in items]
    return {"metas": metas}


# --- Meta ---
@router.get("/meta/{media_type}/{id}.json")
async def get_meta(media_type: str, id: str):
    try:
        tmdb_id_str, db_index_str = id.split("-")
        tmdb_id, db_index = int(tmdb_id_str), int(db_index_str)
    except (ValueError, IndexError):
        raise HTTPException(status_code=400, detail="Invalid Stremio ID format")

    media = await db.get_media_details(tmdb_id=tmdb_id, db_index=db_index)
    if not media:
        return {"meta": {}}

    meta_obj = convert_to_stremio_meta(media)

    if media_type == "series" and "seasons" in media:
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        videos = []
        for season in sorted(media.get("seasons", []), key=lambda s: s.get("season_number")):
            for episode in sorted(season.get("episodes", []), key=lambda e: e.get("episode_number")):
                episode_id = f"{id}:{season['season_number']}:{episode['episode_number']}"
                videos.append({
                    "id": episode_id,
                    "title": episode.get("title", f"Episode {episode['episode_number']}"),
                    "season": season.get("season_number"),
                    "episode": episode.get("episode_number"),
                    "overview": episode.get("overview") or "No description available for this episode yet.",
                    "released": episode.get("released") or yesterday,
                    "thumbnail": episode.get("episode_backdrop") or "https://raw.githubusercontent.com/weebzone/Colab-Tools/refs/heads/main/no_episode_backdrop.png",
                    "imdb_id": episode.get("imdb_id") or media.get("imdb_id"),
                })
        meta_obj["videos"] = videos

    return {"meta": meta_obj}


# --- Stream ---
@router.get("/stream/{media_type}/{id}.json")
async def get_streams(media_type: str, id: str):
    try:
        parts = id.split(":")
        tmdb_id, db_index = map(int, parts[0].split("-"))
        season_num = int(parts[1]) if len(parts) > 1 else None
        episode_num = int(parts[2]) if len(parts) > 2 else None
    except (ValueError, IndexError):
        raise HTTPException(status_code=400, detail="Invalid Stremio ID format")

    media_details = await db.get_media_details(
        tmdb_id=tmdb_id,
        db_index=db_index,
        season_number=season_num,
        episode_number=episode_num
    )

    if not media_details or "telegram" not in media_details:
        return {"streams": []}

    streams = []
    for quality in media_details.get("telegram", []):
        file_id = quality.get("id")
        filename = quality.get("name", "")
        quality_str = quality.get("quality", "HD")
        size = quality.get("size", "")

        stream_name, stream_title = format_stream_details(filename, quality_str, size, file_id)
        url = (
            file_id
            if file_id.startswith(("http://", "https://"))
            else f"{BASE_URL}/dl/{file_id}/video.mkv"
        )

        streams.append({
            "name": stream_name,
            "title": stream_title,
            "url": url,
            "_size": parse_size(size)
        })

    streams.sort(key=lambda s: (get_resolution_priority(s["name"]), s["_size"]), reverse=True)
    for s in streams:
        s.pop("_size", None)

    return {"streams": streams}
