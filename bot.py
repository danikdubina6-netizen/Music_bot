import asyncio
import os
import json
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from aiogram.filters import Command
import yt_dlp

TOKEN = os.getenv("BOT_TOKEN")
bot = Bot(token=TOKEN)
dp = Dispatcher()

# Файл для хранения плейлистов пользователей
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
        "🎵 **Музыкальный бот с плейлистами готов!**\n\n"
        "• Отправь название трека, и я найду несколько вариантов.\n"
        "• Используй /playlist, чтобы посмотреть сохраненные треки."
    )

@dp.message(Command("playlist"))
async def cmd_playlist(message: Message):
    user_id = str(message.from_user.id)
    playlists = load_playlists()
    
    if user_id not in playlists or not playlists[user_id]:
        await message.answer("📭 Твой плейлист пока пуст. Добавляй треки кнопкой под музыкой!")
        return

    text = "📂 **Твой сохраненный плейлист:**\n\n"
    keyboard = []
    
    for idx, track in enumerate(playlists[user_id]):
        title = track['title']
        text += f"{idx + 1}. {title}\n"
        # Кнопка для быстрого скачивания трека из плейлиста
        keyboard.append([InlineKeyboardButton(text=f"▶️ {title[:30]}...", callback_data=f"play_saved_{idx}")])

    markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
    await message.answer(text, reply_markup=markup, parse_mode="Markdown")

@dp.message(F.text)
async def search_music(message: Message):
    query = message.text
    if query.startswith("/"):
        return

    waiting_msg = await message.answer(f"🔍 Ищу варианты для: *{query}*...", parse_mode="Markdown")

    # Быстрый поиск топ-3 результатов без скачивания
    ydl_opts = {
        'default_search': 'ytsearch3',
        'noplaylist': True,
        'extract_flat': True,
        'quiet': True
    }

    try:
        def search():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(query, download=False)
                return info.get('entries', [])

        loop = asyncio.get_running_loop()
        entries = await loop.run_in_executor(None, search)

        if not entries:
            await waiting_msg.edit_text("❌ Ничего не найдено по твоему запросу.")
            return

        keyboard = []
        # Сохраняем временные ссылки для кнопок (передаем URL видео)
        for entry in entries:
            title = entry.get('title', 'Unknown Track')
            url = entry.get('url') or f"https://www.youtube.com/watch?v={entry.get('id')}"
            # Обрезаем длинные названия для кнопки
            btn_text = title[:45] + ("..." if len(title) > 45 else "")
            keyboard.append([InlineKeyboardButton(text=btn_text, callback_data=f"dl_{url[:50]}")])

        markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        await waiting_msg.edit_text("🎵 Выбери трек для скачивания:", reply_markup=markup)

    except Exception as e:
        await waiting_msg.edit_text(f"⚠️ Ошибка при поиске: {e}")

# Обработка нажатия на кнопку скачивания из поиска
@dp.callback_query(F.data.startswith("dl_"))
async def process_download(callback: CallbackQuery):
    url = callback.data[3:] # получаем сохраненную ссылку
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
        'quiet': True
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
            
            # Кнопки под отправленным треком: сохранить в плейлист
            markup = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❤️ В мой плейлист", callback_data=f"add_pl_{track_url[:50]}")]
            ])

            await callback.message.answer_audio(audio=audio_file, caption=f"🎧 {title}", reply_markup=markup)
            await callback.message.delete() # Удаляем плашку "скачиваю..."
            os.remove(file_path)
        else:
            await callback.message.edit_text("❌ Не удалось скачать файл.")

    except Exception as e:
        await callback.message.edit_text(f"⚠️ Ошибка: {e}")

# Добавление трека в плейлист
@dp.callback_query(F.data.startswith("add_pl_"))
async def add_to_playlist(callback: CallbackQuery):
    url = callback.data[7:]
    user_id = str(callback.from_user.id)
    
    # Быстро узнаем название по URL
    try:
        with yt_dlp.YoutubeDL({'quiet': True, 'extract_flat': True}) as ydl:
            info = ydl.extract_info(url, download=False)
            title = info.get('title', 'Трек из плейлиста')
    except:
        title = "Любимый трек"

    playlists = load_playlists()
    if user_id not in playlists:
        playlists[user_id] = []

    # Проверяем, нет ли уже такого трека
    if not any(t['url'] == url for t in playlists[user_id]):
        playlists[user_id].append({'title': title, 'url': url})
        save_playlists(playlists)
        await callback.answer("✅ Трек успешно добавлен в твой плейлист!", show_alert=True)
    else:
        await callback.answer("ℹ️ Этот трек уже есть в твоем плейлисте.", show_alert=True)

async def main():
    print("Бот с плейлистами запущен...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
      
