@Client.on_message(filters.command("pixeldrain") & filters.private & CustomFilters.owner)
async def pixeldrain_handler(client: Client, message: Message):
    if not PIXELDRAIN_API_KEY:
        await message.reply_text("PIXELDRAIN API key yok.")
        return

    args = message.command[1:]
    status = await message.reply_text("Veriler alınıyor...")

    try:
        files = await asyncio.to_thread(fetch_all_files_safe)

        # /pixeldrain sil
        if args and args[0].lower() == "sil":
            deleted = 0
            for f in files:
                file_id = f.get("id")
                if not file_id:
                    continue

                r = requests.delete(
                    f"{API_BASE}/file/{file_id}",
                    headers=get_headers(),
                    timeout=10
                )
                if r.status_code == 200:
                    deleted += 1

                await asyncio.sleep(0.3)

            await status.edit_text(
                f"🗑️ Silme tamamlandı\nSilinen dosya: {deleted}"
            )
            return

        # 🔹 GRUPLAMA
        grouped = {}
        total_bytes = 0

        for f in files:
            name = f.get("name", "Bilinmiyor")
            size = f.get("size", 0)
            total_bytes += size

            if name not in grouped:
                grouped[name] = {
                    "count": 0,
                    "size": 0
                }

            grouped[name]["count"] += 1
            grouped[name]["size"] += size

        # 🔹 ÇIKTI
        text = "📦 **PixelDrain Dosyalar (Gruplu)**\n\n"

        for i, (name, info) in enumerate(grouped.items(), start=1):
            text += (
                f"{i}. `{name}`\n"
                f"   Adet: {info['count']} | "
                f"Toplam: {human_size(info['size'])}\n"
            )

            if len(text) > 3500:
                text += "\n⚠️ Liste kısaltıldı."
                break

        text += (
            "\n\n📊 **Toplam Kullanım**\n"
            f"Toplam Dosya: {len(files)}\n"
            f"Farklı Dosya: {len(grouped)}\n"
            f"Toplam Boyut: {human_size(total_bytes)}\n\n"
            "🗑️ Tüm dosyaları silmek için:\n"
            "`/pixeldrain sil`"
        )

        await status.edit_text(text)

    except Exception as e:
        await status.edit_text("❌ Hata oluştu")
        print("PixelDrain hata:", e)
