from fastapi import APIRouter, HTTPException
from typing import Optional, List, Dict, Any
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

# --- Yeni Platform ve Sıralama Tanımları ---
PLATFORM_KEYWORDS = {
    "Netflix": ["nf"],
    "Amazon": ["amzn"],
    "Disney": ["dsnp"],
    "HBO": ["blutv", "hbo", "hbomax"]
}

SORT_OPTIONS = [
    {"name": "Latest (Updated)", "value": "updated_on"},
    {"name": "Rating", "value": "rating"},
    {"name": "Released", "value": "released"}
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

# --- Yeni Yardımcı Fonksiyon: Platform Tespiti ---
def get_platform_tag(item: dict) -> Optional[str]:
    """
    Medya öğesinin dosya adını (stream) analiz ederek platform etiketini döndürür.
    """
    if "telegram" in item and item["telegram"]:
        filename = item["telegram"][0].get("name", "")
        if filename:
            try:
                # PTN'den gelen 'source' bilgisini kontrol et
                parsed = PTN.parse(filename)
                source = parsed.get("source", "").lower()
                
                # Dosya adındaki yaygın platform etiketlerini kontrol et
                filename_lower = filename.lower()
                
                for platform, keywords in PLATFORM_KEYWORDS.items():
                    # PTN source veya dosya adı kontrolü
                    if source in keywords or any(re.search(r'\b' + kw + r'\b', filename_lower) for kw in keywords):
                        return platform
            except Exception:
                pass
    return None

def create_catalog_config(media_type: str) -> List[Dict[str, Any]]:
    """
    Manifest için katalog yapılandırmasını oluşturur.
    """
    catalog_configs = []
    item_name = "Filmleri" if media_type == "movie" else "Dizileri"
    
    # 1. Ana Kataloglar
    base_catalogs = [
        {"id": f"latest_{media_type}s", "name": "Latest"},
        {"id": f"top_{media_type}s", "name": "Popular"},
    ]
    for cat in base_catalogs:
        catalog_configs.append({
            "type": media_type,
            "id": cat["id"],
            "name": cat["name"],
            "extra": [{"name": "genre", "options": GENRES}, {"name": "skip"}],
            "extraSupported": ["genre", "skip"]
        })

    # 2. Platform Katalogları
    for platform in PLATFORM_KEYWORDS.keys():
        platform_id_base = f"{platform.lower()}_{media_type}s"
        
        # Platformu kendi içinde sıralama seçenekleriyle dikey katalog yap
        platform_sort_options = [
            {"name": s["name"], "value": s["value"]} 
            for s in SORT_OPTIONS
        ]
        
        for sort_opt in platform_sort_options:
            catalog_configs.append({
                "type": media_type,
                "id": f"{platform_id_base}_{sort_opt['value']}",
                "name": f"{platform} {item_name} ({sort_opt['name']})",
                "extra": [{"name": "genre", "options": GENRES}, {"name": "skip"}],
                "extraSupported": ["genre", "skip"]
            })
            
    return catalog_configs


# --- Manifest ---
@router.get("/manifest.json")
async def manifest():
    all_catalogs = []
    all_catalogs.extend(create_catalog_config("movie"))
    all_catalogs.extend(create_catalog_config("series"))

    return {
        "id": "telegram.media",
        "version": ADDON_VERSION,
        "name": ADDON_NAME,
        "description": "Dizi ve film arşivim.",
        "types": ["movie", "series"],
        "resources": ["catalog", "meta", "stream"],
        "catalogs": all_catalogs,
    }


# --- Catalog ---
@router.get("/catalog/{media_type}/{id}/{extra:path}.json")
@router.get("/catalog/{media_type}/{id}.json")
async def catalog(media_type: str, id: str, extra: Optional[str] = None):
    stremio_skip = 0
    genre = None
    platform = None
    sort_field = None
    sort_direction = "desc"

    if extra:
        for p in extra.replace("&", "/").split("/"):
            if p.startswith("genre="):
                genre = unquote(p[6:])
            elif p.startswith("skip="):
                stremio_skip = int(p[5:] or 0)

    page = (stremio_skip // PAGE_SIZE) + 1
    
    # Katalog ID'sine göre sıralama ve platform belirleme
    if "top" in id:
        sort = [("rating", sort_direction)]
    elif "latest" in id:
        sort = [("updated_on", sort_direction)]
    else:
        # Platform tabanlı kataloglar için sıralamayı ID'den çıkar
        for p_name in PLATFORM_KEYWORDS.keys():
            p_id_base = p_name.lower()
            if p_id_base in id:
                platform = p_name
                for s_opt in SORT_OPTIONS:
                    s_val = s_opt["value"]
                    if id.endswith(s_val):
                        sort_field = s_val
                        break
                
                if sort_field == "released":
                    sort = [("release_year", sort_direction)]
                elif sort_field == "rating":
                    sort = [("rating", sort_direction)]
                else: # Default updated_on
                    sort = [("updated_on", sort_direction)]
                break
        
        if not sort: # Hiçbiri eşleşmezse default
            sort = [("updated_on", sort_direction)]


    # Veritabanı sorgusunu platform etiketiyle filtrele
    if media_type == "movie":
        data = await db.sort_movies(sort, page, PAGE_SIZE, genre, platform=platform)
        items = data.get("movies", [])
    else:
        data = await db.sort_tv_shows(sort, page, PAGE_SIZE, genre, platform=platform)
        items = data.get("tv_shows", [])
    
    # Platform filtresi uygulandıysa, sadece etiketi eşleşenleri meta'ya dönüştür.
    # Not: db.sort_movies ve db.sort_tv_shows fonksiyonlarının `platform` parametresini desteklediği varsayılmıştır. 
    # Eğer desteklemiyorsa, platform filtresi burada manuel olarak yapılmalıdır.
    
    filtered_items = []
    if platform:
        for item in items:
            detected_platform = get_platform_tag(item)
            if detected_platform == platform:
                filtered_items.append(item)
    else:
        filtered_items = items


    return {"metas": [convert_to_stremio_meta(i) for i in filtered_items]}


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
    
    # Dizi bölümleri için stream'leri episode'dan al
    if media_type == "series" and season is not None and episode is not None:
        episode_streams = []
        for s in media.get("seasons", []):
            if s.get("season_number") == season:
                for e in s.get("episodes", []):
                    if e.get("episode_number") == episode:
                        episode_streams = e.get("telegram", [])
                        break
                break
        stream_data = episode_streams
    else:
        # Film veya tüm sezonlar için stream'leri media'dan al
        stream_data = media["telegram"]

    for q in stream_data:
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
            "_size": parse_size(size)    # ← sadece sıralama için
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
