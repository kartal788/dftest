import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from pymongo import MongoClient, UpdateOne
from collections import defaultdict
import psutil
from pyrogram import Client, filters, enums
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from deep_translator import GoogleTranslator
import os

# ---------------- CONFIG ----------------
OWNER_ID = int(os.getenv("OWNER_ID", 12345))
stop_event = asyncio.Event()
DOWNLOAD_DIR = "/"

# ---------------- DATABASE ----------------
db_raw = os.getenv("DATABASE", "")
if not db_raw:
    raise Exception("DATABASE ortam değişkeni bulunamadı!")

db_urls = [u.strip() for u in db_raw.split(",") if u.strip()]
MONGO_URL = db_urls[1] if len(db_urls) > 1 else db_urls[0]

client_db = MongoClient(MONGO_URL)
db_name = client_db.list_database_names()[0]
db = client_db[db_name]
movie_col = db["movie"]
series_col = db["tv"]

bot_start_time = time.time()

# ---------------- UTILS ----------------
def translate_text_safe(text, cache):
    if not text or str(text).strip() == "":
        return ""
    if text in cache:
        return cache[text]
    try:
        tr = GoogleTranslator(source='en', target='tr').translate(text)
    except:
        tr = text
    cache[text] = tr
    return tr

def progress_bar(current, total, bar_length=12):
    if total == 0:
        return "[⬡" + "⬡"*(bar_length-1) + "] 0.00%"
    percent = (current / total) * 100
    filled_length = int(bar_length * current // total)
    bar = "⬢" * filled_length + "⬡" * (bar_length - filled_length)
    return f"[{bar}] {min(percent,100):.2f}%"

def format_time_custom(total_seconds):
    if total_seconds is None or total_seconds < 0:
        return "0s0d00s"
    total_seconds = int(total_seconds)
    h, rem = divmod(total_seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}s{m}d{s:02}s"

async def handle_stop(callback_query: CallbackQuery):
    stop_event.set()
    try:
        await callback_query.message.edit_text(
            "⛔ İşlem **iptal edildi**!",
            parse_mode=enums.ParseMode.MARKDOWN
        )
        await callback_query.answer("Durdurma talimatı alındı.")
    except:
        pass

# ---------------- GLOBAL FLAGS ----------------
is_running = False
stop_event = asyncio.Event()

# ---------------- TRANSLATE WORKER ----------------
def translate_batch_worker(batch_docs):
    """
    Batch olarak gelen belgeleri çevirir.
    Her doc {'_id': ..., 'title': ..., 'description': ..., 'seasons': [...]} yapısında olmalıdır.
    """
    CACHE = {}
    results = []
    errors = []

    for doc in batch_docs:
        _id = doc.get("_id")
        upd = {}
        title_main = doc.get("title") or doc.get("name") or "İsim yok"

        try:
            # Film description çevirisi
            if "description" in doc and doc["description"]:
                upd["description"] = translate_text_safe(doc["description"], CACHE)

            # Dizi sezonları ve bölümleri
            seasons = doc.get("seasons")
            if seasons:
                for s in seasons:
                    for ep in s.get("episodes", []):
                        if not ep.get("cevrildi", False):
                            if ep.get("title"):
                                ep["title"] = translate_text_safe(ep["title"], CACHE)
                            if ep.get("overview"):
                                ep["overview"] = translate_text_safe(ep["overview"], CACHE)
                            ep["cevrildi"] = True
                upd["seasons"] = seasons

            upd["cevrildi"] = True
            results.append((_id, upd))
        except Exception as e:
            errors.append(f"ID: {_id} | Film/Dizi: {title_main} | Hata: {str(e)}")

    return results, errors


# ---------------- /cevir ----------------
@Client.on_message(filters.command("cevir") & filters.private & filters.user(OWNER_ID))
async def cevir(client: Client, message: Message):
    global stop_event, is_running

    if is_running:
        await message.reply_text("⛔ Zaten devam eden bir işlem var.")
        return

    is_running = True
    stop_event.clear()

    start_msg = await message.reply_text(
        "🇹🇷 Türkçe çeviri hazırlanıyor...\nİlerleme tek mesajda gösterilecektir.",
        parse_mode=enums.ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ İptal Et", callback_data="stop")]]),
    )

    start_time = time.time()

    # ---------------- TOPLAM HESAPLAMA ----------------
    movies_to_translate = movie_col.count_documents({})
    episodes_to_translate = 0
    for doc in series_col.find({}, {"seasons.episodes": 1}):
        for season in doc.get("seasons", []):
            episodes_to_translate += len(season.get("episodes", []))

    total_to_translate = movies_to_translate + episodes_to_translate
    translated_movies = 0
    translated_episodes = 0
    error_count = 0

    collections = [
        {"col": movie_col, "type": "film", "translated": 0, "errors_list": []},
        {"col": series_col, "type": "episode", "translated": 0, "errors_list": []},
    ]

    batch_size = 50
    workers = 4
    pool = ThreadPoolExecutor(max_workers=workers)
    loop = asyncio.get_event_loop()
    last_update = time.time()
    update_interval = 10

    try:
        for c in collections:
            col = c["col"]
            docs_cursor = col.find({}, {"_id": 1})
            ids = [d["_id"] for d in docs_cursor]
            idx = 0

            while idx < len(ids):
                if stop_event.is_set():
                    break

                batch_ids = ids[idx: idx + batch_size]
                batch_docs = list(col.find({"_id": {"$in": batch_ids}}))

                # Worker çağrısı
                results, errors = await loop.run_in_executor(pool, translate_batch_worker, batch_docs)

                for _id, upd in results:
                    try:
                        col.update_one({"_id": _id}, {"$set": upd})
                        if c["type"] == "film":
                            translated_movies += 1
                        else:
                            # Dizi bölümleri sayısını ekleyelim
                            seasons = upd.get("seasons", [])
                            ep_count = sum(len(s.get("episodes", [])) for s in seasons)
                            translated_episodes += ep_count
                    except:
                        errors.append(f"ID: {_id} | DB Güncelleme Hatası")

                error_count += len(errors)
                c["errors_list"].extend(errors)
                idx += len(batch_ids)

                # İlerleme mesajı
                elapsed = int(time.time() - start_time)
                h, rem = divmod(elapsed, 3600)
                m, s = divmod(rem, 60)
                elapsed_str = f"{h}h{m}m{s}s"

                remaining = (movies_to_translate - translated_movies) + (episodes_to_translate - translated_episodes)
                eta_str = "hesaplanıyor"
                if translated_movies + translated_episodes > 0:
                    avg = elapsed / (translated_movies + translated_episodes)
                    eta_sec = int(avg * remaining)
                    eh, er = divmod(eta_sec, 3600)
                    em, es = divmod(er, 60)
                    eta_str = f"{eh}h{em}m{es}s"

                if time.time() - last_update >= update_interval or idx >= len(ids):
                    last_update = time.time()
                    try:
                        await start_msg.edit_text(
                            (
                                f"🇹🇷 Türkçe çeviri hazırlanıyor...\n\n"
                                f"Toplam: {total_to_translate} (🎬 {movies_to_translate} | 📺 {episodes_to_translate})\n"
                                f"Çevrilen: 🎬 {translated_movies} | 📺 {translated_episodes}\n"
                                f"Kalan: 🎬 {movies_to_translate - translated_movies} | 📺 {episodes_to_translate - translated_episodes}\n"
                                f"Hatalı: {error_count}\n"
                                f"{progress_bar(translated_movies + translated_episodes, total_to_translate)}\n\n"
                                f"Süre: `{elapsed_str}` (`{eta_str}`)"
                            ),
                            parse_mode=enums.ParseMode.MARKDOWN,
                            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ İptal Et", callback_data="stop")]]),
                        )
                    except:
                        pass
    finally:
        pool.shutdown(wait=False)
        is_running = False

    # ---------------- FINAL ÖZET ----------------
    total_duration = int(time.time() - start_time)
    h, rem = divmod(total_duration, 3600)
    m, s = divmod(rem, 60)
    duration_str = f"{h}h{m}m{s}s"

    await start_msg.edit_text(
        (
            "📊 **Genel Özet**\n\n"
            f"Toplam: {total_to_translate} (🎬 {movies_to_translate} | 📺 {episodes_to_translate})\n"
            f"Çevrilen: 🎬 {translated_movies} | 📺 {translated_episodes}\n"
            f"Kalan: 🎬 {movies_to_translate - translated_movies} | 📺 {episodes_to_translate - translated_episodes}\n"
            f"Hatalı: {error_count}\n"
            f"Süre: {duration_str}"
        ),
        parse_mode=enums.ParseMode.MARKDOWN
    )

    # -------- HATA DOSYASI --------
    hata_icerigi = []
    for c in collections:
        if c["errors_list"]:
            hata_icerigi.append(f"*** {c['col'].name} Hataları ***")
            hata_icerigi.extend(c["errors_list"])
            hata_icerigi.append("")

    if hata_icerigi:
        log_path = "cevirhatalari.txt"
        with open(log_path, "w", encoding="utf-8") as f:
            f.write("\n".join(hata_icerigi))
        try:
            await client.send_document(
                chat_id=OWNER_ID,
                document=log_path,
                caption="⛔ Çeviri sırasında hatalar oluştu"
            )
        except:
            pass


# ---------------- /cevirekle ----------------
@Client.on_message(filters.command("cevirekle") & filters.private & filters.user(OWNER_ID))
async def cevirekle(client: Client, message: Message):
    status = await message.reply_text("🔄 'cevrildi' alanları ekleniyor...")
    total_updated = 0

    for col in (movie_col, series_col):
        # Üst seviye belgeler
        docs_cursor = col.find({"cevrildi": {"$ne": True}}, {"_id": 1})
        bulk_ops = [UpdateOne({"_id": doc["_id"]}, {"$set": {"cevrildi": True}}) for doc in docs_cursor]

        # Dizi bölümleri için
        if col == series_col:
            docs_cursor = col.find({"seasons.episodes.cevrildi": {"$ne": True}}, {"_id": 1})
            for doc in docs_cursor:
                bulk_ops.append(
                    UpdateOne(
                        {"_id": doc["_id"]},
                        {"$set": {"seasons.$[].episodes.$[].cevrildi": True}}
                    )
                )

        if bulk_ops:
            res = col.bulk_write(bulk_ops)
            total_updated += res.modified_count

    await status.edit_text(f"✅ 'cevrildi' alanları eklendi.\nToplam güncellenen kayıt: {total_updated}")

@Client.on_message(filters.command("cevirkaldir") & filters.private & filters.user(OWNER_ID))
async def cevirkaldir(client: Client, message: Message):
    status = await message.reply_text("🔄 'cevrildi' alanları kaldırılıyor...")
    total_updated = 0

    for col in (movie_col, series_col):
        # Üst seviye belgeler
        docs_cursor = col.find({"cevrildi": True}, {"_id": 1})
        bulk_ops = [UpdateOne({"_id": doc["_id"]}, {"$unset": {"cevrildi": ""}}) for doc in docs_cursor]

        # Dizi bölümleri için
        if col == series_col:
            docs_cursor = col.find({"seasons.episodes.cevrildi": True}, {"_id": 1})
            for doc in docs_cursor:
                bulk_ops.append(
                    UpdateOne(
                        {"_id": doc["_id"]},
                        {"$unset": {"seasons.$[].episodes.$[].cevrildi": ""}}
                    )
                )

        if bulk_ops:
            res = col.bulk_write(bulk_ops)
            total_updated += res.modified_count

    await status.edit_text(f"✅ 'cevrildi' alanları kaldırıldı.\nToplam güncellenen kayıt: {total_updated}")


# ---------------- /TUR ----------------
@Client.on_message(filters.command("tur") & filters.private & filters.user(OWNER_ID))
async def tur_ve_platform_duzelt(client: Client, message: Message):
    start_msg = await message.reply_text("🔄 Tür ve platform güncellemesi başlatıldı…")

    genre_map = {
        "Action": "Aksiyon", "Film-Noir": "Kara Film", "Game-Show": "Oyun Gösterisi", "Short": "Kısa",
        "Sci-Fi": "Bilim Kurgu", "Sport": "Spor", "Adventure": "Macera", "Animation": "Animasyon",
        "Biography": "Biyografi", "Comedy": "Komedi", "Crime": "Suç", "Documentary": "Belgesel",
        "Drama": "Dram", "Family": "Aile", "News": "Haberler", "Fantasy": "Fantastik",
        "History": "Tarih", "Horror": "Korku", "Music": "Müzik", "Musical": "Müzikal",
        "Mystery": "Gizem", "Romance": "Romantik", "Science Fiction": "Bilim Kurgu",
        "TV Movie": "TV Filmi", "Thriller": "Gerilim", "War": "Savaş", "Western": "Vahşi Batı",
        "Action & Adventure": "Aksiyon ve Macera", "Kids": "Çocuklar", "Reality": "Gerçeklik",
        "Reality-TV": "Gerçeklik", "Sci-Fi & Fantasy": "Bilim Kurgu ve Fantazi", "Soap": "Pembe Dizi",
        "War & Politics": "Savaş ve Politika", "Bilim-Kurgu": "Bilim Kurgu",
        "Aksiyon & Macera": "Aksiyon ve Macera", "Savaş & Politik": "Savaş ve Politika",
        "Bilim Kurgu & Fantazi": "Bilim Kurgu ve Fantazi", "Talk": "Talk-Show"
    }

    platform_map = {
        "MAX": "Max", "Hbomax": "Max", "TABİİ": "Tabii", "NF": "Netflix", "DSNP": "Disney",
        "Tod": "Tod", "Blutv": "Max", "Tv+": "Tv+", "Exxen": "Exxen",
        "Gain": "Gain", "HBO": "Max", "Tabii": "Tabii", "AMZN": "Amazon",
    }

    collections = [(movie_col, "Filmler"), (series_col, "Diziler")]
    total_fixed = 0

    for col, name in collections:
        docs_cursor = col.find({}, {"_id": 1, "genres": 1, "telegram": 1, "seasons": 1})
        bulk_ops = []

        for doc in docs_cursor:
            doc_id = doc["_id"]
            genres = doc.get("genres", [])
            updated = False

            # Türleri güncelle
            new_genres = [genre_map.get(g, g) for g in genres]
            if new_genres != genres:
                updated = True
            genres = new_genres

            # Telegram alanı üzerinden platform ekle
            for t in doc.get("telegram", []):
                name_field = t.get("name", "").lower()
                for key, val in platform_map.items():
                    if key.lower() in name_field and val not in genres:
                        genres.append(val)
                        updated = True

            # Sezonlardaki telegram kontrolleri
            for season in doc.get("seasons", []):
                for ep in season.get("episodes", []):
                    for t in ep.get("telegram", []):
                        name_field = t.get("name", "").lower()
                        for key, val in platform_map.items():
                            if key.lower() in name_field and val not in genres:
                                genres.append(val)
                                updated = True

            if updated:
                bulk_ops.append(UpdateOne({"_id": doc_id}, {"$set": {"genres": genres}}))
                total_fixed += 1

        if bulk_ops:
            col.bulk_write(bulk_ops)

    await start_msg.edit_text(f"✅ Tür ve platform güncellemesi tamamlandı.\nToplam değiştirilen kayıt: {total_fixed}")

# ---------------- /ISTATISTIK ----------------
def get_db_stats_and_genres(url):
    client = MongoClient(url)
    db = client[client.list_database_names()[0]]

    total_movies = db["movie"].count_documents({})
    total_series = db["tv"].count_documents({})

    stats = db.command("dbstats")
    storage_mb = round(stats.get("storageSize",0)/(1024*1024),2)
    storage_percent = round((storage_mb/512)*100,1)

    genre_stats=defaultdict(lambda:{"film":0,"dizi":0})
    for d in db["movie"].aggregate([{"$unwind":"$genres"},{"$group":{"_id":"$genres","count":{"$sum":1}}}]):
        genre_stats[d["_id"]]["film"]=d["count"]
    for d in db["tv"].aggregate([{"$unwind":"$genres"},{"$group":{"_id":"$genres","count":{"$sum":1}}}]):
        genre_stats[d["_id"]]["dizi"]=d["count"]
    return total_movies,total_series,storage_mb,storage_percent,genre_stats

def get_system_status():
    cpu = round(psutil.cpu_percent(interval=1),1)
    ram = round(psutil.virtual_memory().percent,1)
    disk = psutil.disk_usage(DOWNLOAD_DIR)
    free_disk = round(disk.free/(1024**3),2)
    free_percent = round((disk.free/disk.total)*100,1)
    
    uptime_sec = int(time.time() - bot_start_time)
    h, rem = divmod(uptime_sec, 3600)
    m, s = divmod(rem, 60)
    uptime = f"{h}sa {m}dk {s}sn"

    return cpu, ram, free_disk, free_percent, uptime

@Client.on_message(filters.command("istatistik") & filters.private & filters.user(OWNER_ID))
async def istatistik(client: Client, message: Message):
    total_movies,total_series,storage_mb,storage_percent,genre_stats=get_db_stats_and_genres(MONGO_URL)
    cpu,ram,free_disk,free_percent,uptime=get_system_status()

    genre_text="\n".join(f"{g:<14} | Film: {c['film']:<4} | Dizi: {c['dizi']:<4}" for g,c in sorted(genre_stats.items()))

    text=(
        f"⌬ <b>İstatistik</b>\n\n"
        f"┠ Filmler : {total_movies}\n"
        f"┠ Diziler : {total_series}\n"
        f"┖ Depolama: {storage_mb} MB (%{storage_percent})\n\n"
        f"<b>Tür Dağılımı</b>\n<pre>{genre_text}</pre>\n\n"
        f"┟ CPU → {cpu}% | Boş → {free_disk}GB [{free_percent}%]\n"
        f"┖ RAM → {ram}% | Süre → {uptime}"
    )

    await message.reply_text(text, parse_mode=enums.ParseMode.HTML)

# ---------------- CALLBACK QUERY ----------------
@Client.on_callback_query()
async def _cb(client: Client, query: CallbackQuery):
    if query.data=="stop":
        await handle_stop(query)
# ------------------- benzerleri sil -----------------
@Client.on_message(filters.command("benzerlerisil") & filters.private & filters.user(OWNER_ID))
async def benzerleri_sil(client: Client, message: Message):
    status = await message.reply_text("🔍 Yinelenen telegram kayıtları taranıyor...")

    total_docs = 0
    total_removed = 0
    log_lines = []

    collections = [
        (movie_col, "movie"),
        (series_col, "tv")
    ]

    for col, col_name in collections:
        cursor = col.find({}, {"telegram": 1, "seasons": 1, "title": 1, "tmdb_id": 1, "imdb_id": 1})

        for doc in cursor:
            updated = False

            # ---------- FILM ----------
            if col_name == "movie" and "telegram" in doc:
                seen = {}
                new_list = []

                for t in doc["telegram"]:
                    key = (t.get("quality"), t.get("name"), t.get("size"))
                    if key not in seen:
                        seen[key] = True
                        new_list.append(t)
                    else:
                        total_removed += 1
                        updated = True

                        log_lines.append(
                            f"[Koleksiyon] movie\n"
                            f"ID: {doc.get('tmdb_id')}\n"
                            f"Başlık: {doc.get('title')}\n"
                            f"Alan: telegram\n"
                            f"Quality: {t.get('quality')}\n"
                            f"Name: {t.get('name')}\n"
                            f"Size: {t.get('size')}\n"
                            f"{'-'*50}"
                        )

                if updated:
                    col.update_one(
                        {"_id": doc["_id"]},
                        {"$set": {"telegram": new_list}}
                    )
                    total_docs += 1

            # ---------- DIZI / BÖLÜM ----------
            if col_name == "tv":
                seasons = doc.get("seasons", [])
                for season in seasons:
                    season_no = season.get("season_number")
                    for ep in season.get("episodes", []):
                        if "telegram" not in ep:
                            continue

                        seen = {}
                        new_list = []

                        for t in ep["telegram"]:
                            key = (t.get("quality"), t.get("name"), t.get("size"))
                            if key not in seen:
                                seen[key] = True
                                new_list.append(t)
                            else:
                                total_removed += 1
                                updated = True

                                log_lines.append(
                                    f"[Koleksiyon] tv\n"
                                    f"ID: {doc.get('imdb_id')}\n"
                                    f"Dizi: {doc.get('title')}\n"
                                    f"Sezon: {season_no} | Bölüm: {ep.get('episode_number')}\n"
                                    f"Quality: {t.get('quality')}\n"
                                    f"Name: {t.get('name')}\n"
                                    f"Size: {t.get('size')}\n"
                                    f"{'-'*50}"
                                )

                        if updated:
                            ep["telegram"] = new_list

                if updated:
                    col.update_one(
                        {"_id": doc["_id"]},
                        {"$set": {"seasons": seasons}}
                    )
                    total_docs += 1

    # ---------- LOG DOSYASI ----------
    if log_lines:
        log_path = "silinenler.txt"
        with open(log_path, "w", encoding="utf-8") as f:
            f.write("\n".join(log_lines))

        await client.send_document(
            chat_id=OWNER_ID,
            document=log_path,
            caption="🗑️ Silinen yinelenen telegram kayıtları"
        )

    await status.edit_text(
        f"✅ İşlem tamamlandı\n\n"
        f"📄 Etkilenen kayıt: {total_docs}\n"
        f"🗑️ Silinen tekrar: {total_removed}"
    )
