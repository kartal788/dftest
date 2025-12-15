from pyrogram import Client, filters
from Backend.helper.custom_filter import CustomFilters
from Backend.helper.database import Database
import json
from io import BytesIO

@Client.on_message(filters.command("ekle") & filters.private & CustomFilters.owner)
async def add_json_to_db(client, message):
    """
    /ekle komutu JSON dosyasını veya JSON string'ini alıp database'e ekler.
    Kullanım:
    1️⃣ JSON dosyası gönderip caption olarak: /ekle
    2️⃣ JSON string olarak komut: /ekle { "movie": [...], "tv": [...] }
    """

    db = Database()
    await db.connect()

    try:
        # 1️⃣ Eğer mesajda document varsa (JSON dosyası)
        if message.document:
            file = await message.download(in_memory=True)
            data = json.load(BytesIO(file))
        
        # 2️⃣ Eğer command param olarak JSON string verilmişse
        elif len(message.command) > 1:
            json_text = " ".join(message.command[1:])
            data = json.loads(json_text)
        
        else:
            await message.reply_text("⚠️ Lütfen JSON dosyası veya JSON string gönderin.")
            return

        # movie ekleme
        movies_added = 0
        for movie in data.get("movie", []):
            result = await db.insert_media(
                metadata_info=movie,
                channel=0,  # opsiyonel, gerçek chat_id veya 0
                msg_id=0,   # opsiyonel
                size=movie.get("telegram", [{}])[0].get("size", "unknown"),
                name=movie.get("telegram", [{}])[0].get("name", "unknown")
            )
            if result:
                movies_added += 1

        # tv ekleme
        tv_added = 0
        for tv in data.get("tv", []):
            result = await db.insert_media(
                metadata_info=tv,
                channel=0,
                msg_id=0,
                size=tv.get("seasons", [{}])[0].get("episodes", [{}])[0].get("telegram", [{}])[0].get("size", "unknown"),
                name=tv.get("seasons", [{}])[0].get("episodes", [{}])[0].get("telegram", [{}])[0].get("name", "unknown")
            )
            if result:
                tv_added += 1

        await message.reply_text(f"✅ Veritabanına kaydedildi:\nFilmler: {movies_added}\nDiziler: {tv_added}")

    except json.JSONDecodeError:
        await message.reply_text("❌ Geçersiz JSON formatı!")
    except Exception as e:
        await message.reply_text(f"❌ Hata: {e}")
    finally:
        await db.disconnect()
