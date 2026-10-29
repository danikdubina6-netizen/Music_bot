import asyncio
import os
import json
import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from aiogram.filters import Command
import yt_dlp

TOKEN = os.getenv("BOT_TOKEN")
bot = Bot(token=TOKEN)
dp = Dispatcher()

PLAYLISTS_FILE = "playlists.json"

def load_playlists():
    if os.path.exists(PLAYLISTS_FILE):
        with open(PLAYLISTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_playlists(data):
    with open(PLAYLISTS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "🎵 **Музыкальный бот на связи!**\n\n"
        "• Напиши название трека (например: *АлСми - Рюмочка водочки*), и я найду его без капч и блокировок.\n"
        "• /playlist — твой сохраненный список треков."
    )

@dp.message(Command("playlist"))
async def cmd_playlist(message: Message):
    user_id = str(message.from_user.id)
    playlists = load_playlists()
    
    if user_id not in playlists or not playlists[user_id]:
        await message.answer("📭 Твой плейлист пока пуст.")
        return

    text = "📂 **Твой сохраненный плейлист:**\n\n"
    keyboard = []
    
    for idx, track in enumerate(playlists[user_id]):
        title = track['title']
        url = track['url']
        text += f"{idx + 1}. {title}\n"
        keyboard.append([InlineKeyboardButton(text=f"▶️ Скачать: {title[:25]}...", callback_data=f"dl_{url}")])

    markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
    await message.answer(text, reply_markup=markup, parse_mode="Markdown")

# Поиск через публичные API Invidious (обход блокировок YouTube)
@dp.message(F.text)
async def search_music(message: Message):
    query = message.text
    if query.startswith("/"):
        return

    waiting_msg = await message.answer(f"🔍 Ищу: *{query}*...", parse_mode="Markdown")

    # Публичные инстансы Invidious для быстрого поиска без капч
    instances = [
        "https://invidious.privacyredirect.com",
        "https://inv.nadeko.net",
        "https://vid.puffyan.us"
    ]

    video_id = None
    title = None

    async with aiohttp.ClientSession() as session:
        for instance in instances:
            try:
                async with session.get(f"{instance}/api/v1/search", params={"q": query, "type": "video"}, timeout=5) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data and len(data) > 0:
                            video_id = data[0]["videoId"]
                            title = data[0]["title"]
                            break
            except Exception:
                continue

    if not video_id:
        await waiting_msg.edit_text("❌ Не удалось найти трек. Попробуй изменить запрос.")
        return

    url = f"https://www.youtube.com/watch?v={video_id}"

    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"📥 Скачать трек", callback_data=f"dl_{url}")]
    ])

    await waiting_msg.edit_text(f"🎵 <b>{title}</b>", reply_markup=markup, parse_mode="HTML")

# Скачивание трека через yt-dlp по прямой ссылке (ссылки на конкретные видео YouTube скачивает без проблем)
@dp.callback_query(F.data.startswith("dl_"))
async def process_download(callback: CallbackQuery):
    url = callback.data[3:]
    await callback.message.edit_text("📥 Скачиваю и конвертирую трек...")

    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': 'downloads/%(id)s.%(ext)s',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        'noplaylist': True,
        'quiet': True,
        'geo_bypass': True
    }

    os.makedirs("downloads", exist_ok=True)

    try:
        def download():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                file_path = ydl.prepare_filename(info)
                base, _ = os.path.splitext(file_path)
                mp3_path = base + ".mp3"
                return mp3_path, info.get('title', 'Track'), url

        loop = asyncio.get_running_loop()
        file_path, title, track_url = await loop.run_in_executor(None, download)

        if os.path.exists(file_path):
            audio_file = FSInputFile(file_path)
            
            markup = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❤️ В мой плейлист", callback_data=f"add_pl_{track_url}")]
            ])

            await callback.message.answer_audio(audio=audio_file, caption=f"🎧 {title}", reply_markup=markup)
            await callback.message.delete()
            os.remove(file_path)
        else:
            await callback.message.edit_text("❌ Не удалось скачать файл.")

    except Exception as e:
        await callback.message.edit_text(f"⚠️ Ошибка скачивания: {e}")

# Добавление в плейлист
@dp.callback_query(F.data.startswith("add_pl_"))
async def add_to_playlist(callback: CallbackQuery):
    url = callback.data[7:]
    user_id = str(callback.from_user.id)
    
    try:
        with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
            info = ydl.extract_info(url, download=False)
            title = info.get('title', 'Любимый трек')
    except:
        title = "Любимый трек"

    playlists = load_playlists()
    if user_id not in playlists:
        playlists[user_id] = []

    if not any(t['url'] == url for t in playlists[user_id]):
        playlists[user_id].append({'title': title, 'url': url})
        save_playlists(playlists)
        await callback.answer("✅ Трек добавлен в плейлист!", show_alert=True)
    else:
        await callback.answer("ℹ️ Этот трек уже есть в плейлисте.", show_alert=True)

async def main():
    print("Бот запущен...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
        
