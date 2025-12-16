from fastapi import APIRouter
from typing import Optional
from urllib.parse import unquote
from Backend.config import Telegram
from Backend import db, __version__
import PTN

# --- Configuration ---
BASE_URL = Telegram.BASE_URL
ADDON_NAME = "Arşivim"
ADDON_VERSION = __version__
PAGE_SIZE = 15

router = APIRouter(prefix="/stremio", tags=["Stremio Addon"])

# --- Genres / Platforms ---
GENRES = [
    "Aile", "Aksiyon", "Animasyon", "Belgesel", "Bilim Kurgu",
    "Biyografi", "Çocuklar", "Dram", "Fantastik", "Gerilim",
    "Gizem", "Komedi", "Korku", "Macera", "Müzik", "Romantik",
    "Savaş", "Spor", "Suç", "Tarih"
]

PLATFORMS = ["Netflix", "Disney", "Amazon", "Tv+", "Exxen"]

# --- Helpers ---
def convert_to_stremio_meta(item: dict) -> dict:
    media_type = "series" if item.get("media_type") == "tv" else "movie"
    stremio_id = f"{item.get('tmdb_id')}-{item.get('db_index')}"

    name = item.get("title", "")
    if item.get("cevrildi"):
        name = "🇹🇷 " + name

    return {
        "id": stremio_id,
        "type": media_type,
        "name": name,
        "poster": item.get("poster") or "",
        "logo": item.get("logo") or "",
        "year": item.get("release_year"),
        "background": item.get("backdrop") or "",
        "genres": item.get("genres") or [],
        "imdbRating": item.get("rating") or "",
        "description": item.get("description") or "",
        "cast": item.get("cast") or [],
        "runtime": item.get("runtime") or "",
    }


def get_resolution_priority(name: str) -> int:
    for r in (2160, 1080, 720, 480):
        if str(r) in name:
            return r
    return 1


def parse_size(size: str) -> float:
    if not size:
        return 0
    size = size.lower().replace(" ", "")
    if "gb" in size:
        return float(size.replace("gb", "")) * 1024
    if "mb" in size:
        return float(size.replace("mb", ""))
    return 0


def format_stream(filename, quality, size, file_id):
    source = "Link" if file_id.startswith("http") else "Telegram"
    parsed = {}
    try:
        parsed = PTN.parse(filename)
    except:
        pass

    resolution = parsed.get("resolution", quality)
    codec = parsed.get("codec", "")
    audio = parsed.get("audio", "")

    name = f"{source} {resolution}"
    title = f"📁 {filename}\n💾 {size}"

    if codec or audio:
        title += f"\n🎥 {codec} 🔊 {audio}"

    return name.strip(), title


# --- Manifest ---
@router.get("/manifest.json")
async def manifest():
    catalogs = [
        {
            "type": "movie",
            "id": "latest_movies",
            "name": "Son Eklenen Filmler",
            "extra": [{"name": "genre", "options": GENRES}, {"name": "skip"}],
            "extraSupported": ["genre", "skip"]
        },
        {
            "type": "series",
            "id": "latest_series",
            "name": "Son Eklenen Diziler",
            "extra": [{"name": "genre", "options": GENRES}, {"name": "skip"}],
            "extraSupported": ["genre", "skip"]
        }
    ]

    for p in PLATFORMS:
        catalogs += [
            {
                "type": "movie",
                "id": f"platform_movie_{p.lower()}",
                "name": f"{p} Filmleri",
                "extra": [{"name": "skip"}],
                "extraSupported": ["skip"]
            },
            {
                "type": "series",
                "id": f"platform_series_{p.lower()}",
                "name": f"{p} Dizileri",
                "extra": [{"name": "skip"}],
                "extraSupported": ["skip"]
            }
        ]

    return {
        "id": "telegram.media",
        "version": ADDON_VERSION,
        "name": ADDON_NAME,
        "description": "Film & Dizi Arşivi",
        "types": ["movie", "series"],
        "resources": ["catalog", "meta", "stream"],
        "catalogs": catalogs
    }


# --- Catalog ---
@router.get("/catalog/{media_type}/{id}/{extra:path}.json")
@router.get("/catalog/{media_type}/{id}.json")
async def catalog(media_type: str, id: str, extra: Optional[str] = None):
    skip = 0
    genre = None
    platform = None

    if extra:
        for p in extra.replace("&", "/").split("/"):
            if p.startswith("genre="):
                genre = unquote(p[6:])
            elif p.startswith("skip="):
                skip = int(p[5:])

    page = (skip // PAGE_SIZE) + 1

    if id.startswith("platform_"):
        platform = id.split("_")[2].capitalize()
        genre = platform  # PLATFORM = GENRE

    if media_type == "movie":
        data = await db.sort_movies(
            [("updated_on", "desc")],
            page,
            PAGE_SIZE,
            genre
        )
        items = data.get("movies", [])
    else:
        data = await db.sort_tv_shows(
            [("updated_on", "desc")],
            page,
            PAGE_SIZE,
            genre
        )
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
        videos = []
        for s in media.get("seasons", []):
            for e in s.get("episodes", []):
                videos.append({
                    "id": f"{id}:{s['season_number']}:{e['episode_number']}",
                    "title": e.get("title"),
                    "season": s["season_number"],
                    "episode": e["episode_number"],
                    "released": e.get("released"),
                    "overview": e.get("overview"),
                    "thumbnail": e.get("episode_backdrop")
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
        name, title = format_stream(q["name"], q["quality"], q["size"], q["id"])
        url = q["id"] if q["id"].startswith("http") else f"{BASE_URL}/dl/{q['id']}/video.mkv"

        streams.append({
            "name": name,
            "title": title,
            "url": url,
            "_size": parse_size(q["size"])
        })

    streams.sort(
        key=lambda s: (get_resolution_priority(s["name"]), s["_size"]),
        reverse=True
    )

    for i, s in enumerate(streams):
        if i == 0:
            s["name"] = "⭐ " + s["name"]
        s.pop("_size", None)

    return {"streams": streams}
