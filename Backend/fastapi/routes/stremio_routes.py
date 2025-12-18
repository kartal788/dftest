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


# ✅ SADECE BOYUT İÇİN EKLENDİ
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
             {
                "type": "series",
                "id": "released",
                "name": "Yeni Bölüm",
                "extra": [{"name": "skip"}],
                "extraSupported": ["skip"]
            },
            {
                "type": "movie",
                "id": "latest_movies",
                "name": "Yeni Eklenen",
                "extra": [{"name": "genre", "options": GENRES}, {"name": "skip"}],
                "extraSupported": ["genre", "skip"]
            },
            {
                "type": "movie",
                "id": "top_movies",
                "name": "Popüler",
                "extra": [{"name": "genre", "options": GENRES}, {"name": "skip"}],
                "extraSupported": ["genre", "skip"]
            },
            {
                "type": "series",
                "id": "latest_series",
                "name": "Yeni Eklenen",
                "extra": [{"name": "genre", "options": GENRES}, {"name": "skip"}],
                "extraSupported": ["genre", "skip"]
            },
            {
                "type": "series",
                "id": "top_series",
                "name": "Popüler",
                "extra": [{"name": "genre", "options": GENRES}, {"name": "skip"}],
                "extraSupported": ["genre", "skip"]
            },
            {
                "type": "movie",
                "id": "movies_2025",
                "name": "2025 Filmleri",
                "extra": [{"name": "genre", "options": GENRES}, {"name": "skip"}],
                "extraSupported": ["genre", "skip"]
            }
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
        elif "top" in id:
            sort = [("rating", "desc")]
            items = (await db.sort_movies(sort, page, PAGE_SIZE, genre)).get("movies", [])
        else:
            sort = [("updated_on", "desc")]
            items = (await db.sort_movies(sort, page, PAGE_SIZE, genre)).get("movies", [])

    else:  # series
        if "top" in id:
            sort = [("rating", "desc")]
            data = await db.sort_tv_shows(sort, page, PAGE_SIZE, genre)
            items = data.get("tv_shows", [])
        else:
            sort = [("updated_on", "desc")]
            data = await db.sort_tv_shows(sort, page, PAGE_SIZE, genre)
            items = data.get("tv_shows", [])

        # --- Dizi released sıralaması ---
        if "released" in id:
            def get_latest_episode_release(series):
                latest_date = datetime.min.replace(tzinfo=timezone.utc)
                for season in series.get("seasons", []):
                    for ep in season.get("episodes", []):
                        try:
                            ep_date = datetime.fromisoformat(ep["released"].replace("Z", "+00:00"))
                            if ep_date > latest_date:
                                latest_date = ep_date
                        except Exception:
                            continue
                return latest_date

            items.sort(key=get_latest_episode_release, reverse=True)

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

    streams = []

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

        streams.append({
            "name": name,
            "title": title,
            "url": url,
            "_size": parse_size(size)   # ← sadece sıralama için
        })

    # ✅ AYNI ÇÖZÜNÜRLÜKTE BOYUTU BÜYÜK OLAN ÜSTE
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
