import os
import asyncio
import random
import logging
from typing import List, Dict, Optional, Set
from datetime import datetime, timedelta
from contextlib import suppress
from collections import Counter

from aiogram import Bot, Dispatcher, types
from aiogram.contrib.middlewares.logging import LoggingMiddleware
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ChatMember
from aiogram.utils import executor
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.utils.exceptions import MessageNotModified, MessageToDeleteNotFound

# ===================== КОНФИГУРАЦИЯ =====================
BOT_TOKEN = os.getenv("BOT_TOKEN")
BOT_USERNAME = "kazikcoolbot"

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден! Установите переменную окружения BOT_TOKEN")

EMOJIS = ["🍋", "🍇", "👑", "🍗", "💸", "🥇", "💎"]
MAX_PLAYERS = 4
MIN_PLAYERS = 2
MAX_LIFE = 5
REGISTRATION_TIMEOUT = 300
ROUND_PENALTY = -0.5  # Штраф за раунд

WIN_SCORES = {
    2: 12,
    3: 17,
    4: 22
}

# ===================== ИНИЦИАЛИЗАЦИЯ =====================
logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)
dp.middleware.setup(LoggingMiddleware())

# ===================== СОСТОЯНИЯ FSM =====================
class GameStates(StatesGroup):
    WAITING_FOR_EMOJI = State()
    WAITING_FOR_JUDGE = State()

# ===================== ГЛОБАЛЬНОЕ СОСТОЯНИЕ ИГРЫ =====================
class GameState:
    def __init__(self):
        self.players: List[int] = []
        self.registered: Dict[int, str] = {}
        self.is_active: bool = False
        self.is_registration_active: bool = False
        self.current_player_index: int = 0
        self.main_emoji: Optional[str] = None
        self.round_emojis: Optional[List[str]] = None
        self.player_lives: Dict[int, int] = {}
        self.player_scores: Dict[int, float] = {}  # Изменено на float
        self.player_choices: Dict[int, List[str]] = {}
        self.history: List[Dict] = []
        self.awaiting_judge: bool = False
        self.judge_player_id: Optional[int] = None
        self.current_player_id: Optional[int] = None
        self.chat_id: Optional[int] = None
        self.registration_message_id: Optional[int] = None
        self.registration_task: Optional[asyncio.Task] = None
        self.registration_start_time: Optional[datetime] = None
        self.selected_emojis: List[str] = []
        self.round_number: int = 0
        self.win_score: int = 22
        self.waiting_for_judge_from: Optional[int] = None
        self.current_judge_message_id: Optional[int] = None

game = GameState()

# ===================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =====================
async def is_user_admin(user_id: int, chat_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in [types.ChatMemberStatus.ADMINISTRATOR, types.ChatMemberStatus.CREATOR]
    except:
        return False

def get_main_emoji() -> str:
    return random.choice(EMOJIS)

def spin_casino() -> List[str]:
    return [random.choice(EMOJIS) for _ in range(5)]

def get_player_name(user_id: int) -> str:
    return game.registered.get(user_id, f"User{user_id}")

def get_players_list() -> str:
    if not game.players:
        return "Нет игроков"
    return "\n".join([f"• {get_player_name(uid)}" for uid in game.players])

def get_next_player() -> Optional[int]:
    if not game.players:
        return None
    
    start_index = game.current_player_index
    for i in range(len(game.players)):
        index = (start_index + i) % len(game.players)
        player_id = game.players[index]
        if game.player_lives.get(player_id, 0) > 0:
            game.current_player_index = index
            return player_id
    
    return None

def get_next_alive_player(after_player_id: int) -> Optional[int]:
    """Возвращает следующего живого игрока после указанного"""
    if not game.players:
        return None
    
    try:
        start_index = game.players.index(after_player_id)
    except ValueError:
        return None
    
    for i in range(1, len(game.players) + 1):
        index = (start_index + i) % len(game.players)
        player_id = game.players[index]
        if game.player_lives.get(player_id, 0) > 0:
            return player_id
    
    return None

def is_player_alive(user_id: int) -> bool:
    return game.player_lives.get(user_id, 0) > 0

def get_alive_players() -> List[int]:
    return [uid for uid in game.players if is_player_alive(uid)]

def format_time(seconds: int) -> str:
    minutes = seconds // 60
    secs = seconds % 60
    return f"{minutes:02d}:{secs:02d}"

def get_player_status(user_id: int) -> str:
    lives = game.player_lives.get(user_id, 0)
    score = game.player_scores.get(user_id, 0)
    # Форматируем очки с одним знаком после запятой
    score_str = f"{score:.1f}" if score % 1 != 0 else f"{int(score)}"
    return f"❤️{lives} | ⭐{score_str}/{game.win_score}"

def get_win_score() -> int:
    alive = len(get_alive_players())
    if alive <= 2:
        return WIN_SCORES[2]
    elif alive == 3:
        return WIN_SCORES[3]
    else:
        return WIN_SCORES[4]

# ===================== КЛАВИАТУРЫ =====================
def get_registration_keyboard(user_id: int):
    """Клавиатура для регистрации с двумя кнопками"""
    buttons = []
    
    # Всегда показываем обе кнопки
    if user_id in game.players:
        buttons.append(InlineKeyboardButton(text="🚪 Выйти из игры", callback_data="leave_game"))
    else:
        buttons.append(InlineKeyboardButton(text="✅ Войти в игру", callback_data="join_game"))
    
    # Добавляем кнопки начала игры
    if not game.is_active and len(game.players) >= MIN_PLAYERS:
        buttons.append(InlineKeyboardButton(text="🎮 Начать игру", callback_data="start_game"))
        buttons.append(InlineKeyboardButton(text="⚡ Быстрый старт", callback_data="fast_start_game"))
    
    return InlineKeyboardMarkup(row_width=1).add(*buttons)

def get_emoji_keyboard(emojis: List[str], selected: List[str]):
    """Клавиатура для выбора эмодзи (по одному)"""
    buttons = []
    
    selected_count = Counter(selected)
    total_count = Counter(emojis)
    
    for emoji in set(emojis):
        available = total_count[emoji] - selected_count[emoji]
        if available > 0:
            buttons.append(InlineKeyboardButton(
                text=f"{emoji} (осталось: {available})", 
                callback_data=f"select_emoji_{emoji}"
            ))
    
    if selected:
        buttons.append(InlineKeyboardButton(text="📤 Отправить", callback_data="submit_choices"))
        buttons.append(InlineKeyboardButton(text="🔄 Заново", callback_data="reset_choices"))
    
    buttons.append(InlineKeyboardButton(text="❌ Отменить ход", callback_data="cancel_turn"))
    
    return InlineKeyboardMarkup(row_width=2).add(*buttons)

def get_judge_keyboard():
    buttons = [
        InlineKeyboardButton(text="✅ Правда", callback_data="judge_true"),
        InlineKeyboardButton(text="❌ Ложь", callback_data="judge_false")
    ]
    return InlineKeyboardMarkup(row_width=2).add(*buttons)

# ===================== ТАЙМЕР РЕГИСТРАЦИИ =====================
async def registration_timer(chat_id: int, message_id: int):
    await asyncio.sleep(REGISTRATION_TIMEOUT)
    
    if not game.is_registration_active:
        return
    
    await end_registration(chat_id, "⏰ Время регистрации истекло! Начинаю игру!")

async def end_registration(chat_id: int, reason: str = "Регистрация завершена"):
    if not game.is_registration_active:
        return
    
    game.is_registration_active = False
    
    if game.registration_task:
        game.registration_task.cancel()
        game.registration_task = None
    
    if len(game.players) >= MIN_PLAYERS:
        await bot.send_message(chat_id, f"{reason}\n\nИгроков: {len(game.players)}/{MAX_PLAYERS}")
        await start_new_game(chat_id)
    else:
        await bot.send_message(chat_id, f"{reason}\n\nНедостаточно игроков для начала игры ({len(game.players)}/{MIN_PLAYERS})")
        game.players = []
        game.registered = {}
        game.registration_message_id = None

async def update_registration_message(chat_id: int):
    if not game.registration_message_id:
        return
    
    text = f"🎮 <b>Набор в игру</b>\n\n"
    text += f"Статус: {'🔴 Игра идет' if game.is_active else '🟢 Набор открыт'}\n"
    text += f"Игроков: {len(game.players)}/{MAX_PLAYERS}\n\n"
    text += f"<b>Участники:</b>\n{get_players_list()}\n\n"
    
    if game.is_registration_active and game.registration_start_time:
        elapsed = int((datetime.now() - game.registration_start_time).total_seconds())
        remaining = max(0, REGISTRATION_TIMEOUT - elapsed)
        text += f"\n⏳ Осталось времени: {format_time(remaining)}"
    
    if len(game.players) >= MAX_PLAYERS:
        text += f"\n⚠️ Лимит игроков достигнут ({MAX_PLAYERS})"
    elif not game.is_active and len(game.players) >= MIN_PLAYERS:
        text += f"\n✅ Достаточно игроков! Нажмите 'Начать игру'"
    elif not game.is_active:
        text += f"\n⏳ Нужно еще {MIN_PLAYERS - len(game.players)} игроков"
    
    try:
        await bot.edit_message_text(
            text,
            chat_id,
            game.registration_message_id,
            reply_markup=get_registration_keyboard(0),
            parse_mode="HTML"
        )
    except MessageNotModified:
        pass

# ===================== ОБРАБОТЧИКИ КОМАНД =====================
@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    if message.chat.type not in ["group", "supergroup"]:
        await message.answer("Этот бот работает только в группах!")
        return
    
    if not message.text or f"@{BOT_USERNAME}" not in message.text:
        return
    
    game.chat_id = message.chat.id
    
    if game.is_active:
        await message.answer("🔴 Игра уже идет! Дождитесь окончания.")
        return
    
    if game.is_registration_active and game.registration_message_id:
        await update_registration_message(message.chat.id)
        with suppress(MessageToDeleteNotFound):
            await message.delete()
        return
    
    # Начинаем новую регистрацию
    game.players = []
    game.registered = {}
    game.is_registration_active = True
    game.registration_start_time = datetime.now()
    
    text = f"🎮 <b>Набор в игру</b>\n\n"
    text += f"Статус: 🟢 Набор открыт\n"
    text += f"Игроков: 0/{MAX_PLAYERS}\n\n"
    text += f"<b>Участники:</b>\nНет игроков\n\n"
    text += f"⏳ Осталось времени: {format_time(REGISTRATION_TIMEOUT)}"
    
    sent_msg = await message.answer(text, reply_markup=get_registration_keyboard(message.from_user.id), parse_mode="HTML")
    game.registration_message_id = sent_msg.message_id
    
    try:
        await bot.pin_chat_message(message.chat.id, sent_msg.message_id, disable_notification=True)
    except:
        pass
    
    if game.registration_task:
        game.registration_task.cancel()
    game.registration_task = asyncio.create_task(registration_timer(message.chat.id, sent_msg.message_id))

@dp.message_handler(commands=['rules'])
async def cmd_rules(message: types.Message):
    rules_text = """
📜 <b>ПРАВИЛА ИГРЫ "КАЗИНО-БЛЕФ"</b>

🎯 <b>Цель игры:</b>
Стать последним выжившим или первым, кто наберет нужное количество очков:
• 2 игрока: 12 очков
• 3 игрока: 17 очков
• 4 игрока: 22 очка

🎲 <b>Как играть:</b>
1. Каждому игроку в ЛС выпадают 5 случайных эмодзи
2. Игрок выбирает эмодзи, которые хочет показать (по одному)
3. Можно выбрать несколько эмодзи, но нельзя выбрать больше, чем выпало
4. В группу отправляется ЗАМАСКИРОВАННЫЙ выбор (все эмодзи заменяются на главный)

⚖️ <b>Судья:</b>
Следующий игрок решает: правда или ложь?

💥 <b>Русская рулетка:</b>
• Если игрок соврал → он стреляет в себя
• Если игрок сказал правду, а судья сказал "ложь" → судья стреляет в себя
• Шанс смерти: 1/6
• Жизней: 5 (можно потерять все)

🏆 <b>Очки:</b>
• Выжил после выстрела: +3
• Обнаружил вруна: +4
• Врун умер из-за вас: +5
• Вытянул 4-5 одинаковых эмодзи: +4
• Попался на лжи: -2
• Каждый раунд: -0.5

⭐ <b>Победа:</b>
• Первый, кто наберет нужное количество очков
• Или последний выживший

🎰 <b>Эмодзи:</b>
🍋 🍇 👑 🍗 💸 🥇 💎

Удачи! 🍀
    """
    await message.answer(rules_text, parse_mode="HTML")

@dp.message_handler(commands=['stop'])
async def cmd_stop(message: types.Message):
    if message.chat.type not in ["group", "supergroup"]:
        await message.answer("Эта команда работает только в группах!")
        return
    
    if not await is_user_admin(message.from_user.id, message.chat.id):
        await message.answer("⛔ Эта команда доступна только администраторам группы!")
        return
    
    if not game.is_active and not game.players:
        await message.answer("Игра не активна или не начата")
        return
    
    if game.is_registration_active:
        game.is_registration_active = False
        if game.registration_task:
            game.registration_task.cancel()
            game.registration_task = None
        game.players = []
        game.registered = {}
        game.registration_message_id = None
        await message.answer("🏁 Регистрация отменена администратором!")
        return
    
    game.is_active = False
    game.players = []
    game.registered = {}
    game.player_lives = {}
    game.player_scores = {}
    game.player_choices = {}
    game.round_emojis = None
    game.main_emoji = None
    game.awaiting_judge = False
    game.chat_id = None
    
    await message.answer("🏁 Игра принудительно завершена администратором!")

@dp.message_handler(commands=['startgame'])
async def cmd_startgame(message: types.Message):
    if message.chat.type not in ["group", "supergroup"]:
        await message.answer("Эта команда работает только в группах!")
        return
    
    if not await is_user_admin(message.from_user.id, message.chat.id):
        await message.answer("⛔ Эта команда доступна только администраторам группы!")
        return
    
    if game.is_active:
        await message.answer("Игра уже идет!")
        return
    
    if len(game.players) < MIN_PLAYERS:
        await message.answer(f"Недостаточно игроков! Нужно минимум {MIN_PLAYERS}")
        return
    
    if game.is_registration_active:
        game.is_registration_active = False
        if game.registration_task:
            game.registration_task.cancel()
            game.registration_task = None
    
    game.chat_id = message.chat.id
    await start_new_game(message.chat.id)

@dp.message_handler(commands=['faststart'])
async def cmd_faststart(message: types.Message):
    if message.chat.type not in ["group", "supergroup"]:
        await message.answer("Эта команда работает только в группах!")
        return
    
    if not await is_user_admin(message.from_user.id, message.chat.id):
        await message.answer("⛔ Эта команда доступна только администраторам группы!")
        return
    
    if game.is_active:
        await message.answer("Игра уже идет!")
        return
    
    if len(game.players) < MIN_PLAYERS:
        await message.answer(f"Недостаточно игроков! Нужно минимум {MIN_PLAYERS}")
        return
    
    if game.is_registration_active:
        game.is_registration_active = False
        if game.registration_task:
            game.registration_task.cancel()
            game.registration_task = None
    
    game.chat_id = message.chat.id
    await bot.send_message(message.chat.id, "⚡ Быстрый старт! Игра начинается!")
    await start_new_game(message.chat.id)

@dp.message_handler(commands=['leaderboard'])
async def cmd_leaderboard(message: types.Message):
    if not game.history:
        await message.answer("📊 История побед пуста")
        return
    
    text = "🏆 <b>Таблица победителей</b>\n\n"
    for i, win in enumerate(game.history[-10:], 1):
        text += f"{i}. {win['winner']} - {win['date']}\n"
    
    await message.answer(text, parse_mode="HTML")

# ===================== ИГРОВАЯ ЛОГИКА =====================
async def start_new_game(chat_id: int):
    game.is_active = True
    game.main_emoji = get_main_emoji()
    game.player_lives = {uid: MAX_LIFE for uid in game.players}
    game.player_scores = {uid: 0.0 for uid in game.players}  # Изменено на float
    game.player_choices = {}
    game.current_player_index = 0
    game.awaiting_judge = False
    game.registration_message_id = None
    game.round_number = 0
    
    # Открепляем сообщение с регистрацией
    try:
        await bot.unpin_chat_message(chat_id, game.registration_message_id)
    except:
        pass
    game.registration_message_id = None
    
    alive_count = len(game.players)
    game.win_score = WIN_SCORES.get(alive_count, 22)
    
    # Показываем порядок игроков
    players_order = "\n".join([f"{i+1}. {get_player_name(uid)}" for i, uid in enumerate(game.players)])
    
    players_list = "\n".join([f"• {get_player_name(uid)} - {get_player_status(uid)}" for uid in game.players])
    text = f"🎮 <b>Игра началась!</b>\n\n"
    text += f"Главный эмодзи: {game.main_emoji}\n"
    text += f"Участников: {alive_count}\n"
    text += f"Для победы нужно: {game.win_score} очков\n\n"
    text += f"<b>Порядок ходов:</b>\n{players_order}\n\n"
    text += f"<b>Статус игроков:</b>\n{players_list}\n\n"
    text += f"Удачи всем! 🍀"
    
    await bot.send_message(chat_id, text, parse_mode="HTML")
    await start_new_round(chat_id)

async def start_new_round(chat_id: int):
    game.round_number += 1
    alive_players = get_alive_players()
    
    if len(alive_players) <= 1:
        await end_game(chat_id)
        return
    
    game.main_emoji = get_main_emoji()
    game.round_emojis = spin_casino()
    game.player_choices = {}
    game.selected_emojis = []
    
    first_alive = alive_players[0]
    game.current_player_index = game.players.index(first_alive)
    await start_player_turn(chat_id)

async def start_player_turn(chat_id: int):
    current_player = get_next_player()
    if not current_player:
        await end_game(chat_id)
        return
    
    game.current_player_id = current_player
    game.awaiting_judge = False
    game.selected_emojis = []
    
    player_name = get_player_name(current_player)
    main_emoji = game.main_emoji
    
    # Обновляем очки (каждый раунд -0.5)
    for uid in game.players:
        if is_player_alive(uid):
            game.player_scores[uid] = game.player_scores.get(uid, 0.0) + ROUND_PENALTY
    
    # В группу НЕ отправляем выпавшие эмодзи!
    text = f"🎰 <b>Раунд {game.round_number}</b>\n\n"
    text += f"Ход игрока: {player_name}\n"
    text += f"Главный эмодзи: {main_emoji}\n"
    text += f"Статус: {get_player_status(current_player)}\n\n"
    text += f"Игроку отправлены варианты в ЛС"
    
    await bot.send_message(chat_id, text, parse_mode="HTML")
    await send_choices_to_player(current_player)

async def send_choices_to_player(user_id: int):
    if not game.round_emojis:
        return
    
    # В ЛС отправляем реальные эмодзи
    emojis_str = "".join(game.round_emojis)
    main_emoji = game.main_emoji
    selected = game.selected_emojis
    
    text = f"🎰 <b>Ваш ход!</b>\n\n"
    text += f"Главный эмодзи: {main_emoji}\n"
    text += f"Ваши эмодзи: {emojis_str}\n\n"
    
    if selected:
        text += f"<b>Выбрано:</b> {', '.join(selected)}\n\n"
    
    text += f"<i>Выберите эмодзи (нажмите на кнопку), чтобы добавить его в выбор. Нажмите 'Отправить', чтобы закончить выбор.</i>"
    
    await bot.send_message(user_id, text, reply_markup=get_emoji_keyboard(game.round_emojis, selected), parse_mode="HTML")

async def end_game(chat_id: int):
    alive_players = get_alive_players()
    if not alive_players:
        winner_id = game.players[0]
    else:
        max_score = -999.0
        winner_id = alive_players[0]
        for uid in alive_players:
            score = game.player_scores.get(uid, 0.0)
            if score > max_score:
                max_score = score
                winner_id = uid
    
    winner_name = get_player_name(winner_id)
    winner_score = game.player_scores.get(winner_id, 0.0)
    
    game.history.append({
        "winner": winner_name,
        "date": datetime.now().strftime("%Y-%m-%d %H:%M")
    })
    
    text = f"🏆 <b>Игра окончена!</b>\n\n"
    score_str = f"{winner_score:.1f}" if winner_score % 1 != 0 else f"{int(winner_score)}"
    text += f"Победитель: {winner_name}\n"
    text += f"Очки: {score_str}\n"
    text += f"Жизней: {game.player_lives.get(winner_id, 0)}\n"
    text += f"Дата: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    
    await bot.send_message(chat_id, text, parse_mode="HTML")
    
    game.is_active = False
    game.players = []
    game.registered = {}
    game.player_lives = {}
    game.player_scores = {}
    game.player_choices = {}
    game.round_emojis = None
    game.main_emoji = None
    game.awaiting_judge = False
    game.chat_id = None

# ===================== ОБРАБОТЧИКИ CALLBACK =====================
@dp.callback_query_handler(lambda c: c.data == "join_game")
async def join_game(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    username = callback_query.from_user.full_name or callback_query.from_user.username or f"User{user_id}"
    
    if game.is_active:
        await callback_query.answer("❌ Игра уже начата!", show_alert=True)
        return
    
    if not game.is_registration_active:
        await callback_query.answer("❌ Регистрация закрыта!", show_alert=True)
        return
    
    if len(game.players) >= MAX_PLAYERS:
        await callback_query.answer(f"❌ Лимит игроков достигнут ({MAX_PLAYERS})!", show_alert=True)
        return
    
    if user_id in game.players:
        await callback_query.answer("Вы уже в игре!", show_alert=True)
        return
    
    game.players.append(user_id)
    game.registered[user_id] = username
    game.chat_id = callback_query.message.chat.id
    
    await update_registration_message(callback_query.message.chat.id)
    await callback_query.answer("✅ Вы вошли в игру!")

@dp.callback_query_handler(lambda c: c.data == "leave_game")
async def leave_game(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    
    if user_id not in game.players:
        await callback_query.answer("❌ Вы не состоите в игре!", show_alert=True)
        return
    
    game.players.remove(user_id)
    if user_id in game.registered:
        del game.registered[user_id]
    
    await update_registration_message(callback_query.message.chat.id)
    await callback_query.answer("✅ Вы вышли из игры")

@dp.callback_query_handler(lambda c: c.data == "start_game")
async def start_game_callback(callback_query: types.CallbackQuery):
    if not await is_user_admin(callback_query.from_user.id, callback_query.message.chat.id):
        await callback_query.answer("⛔ Только администраторы могут начать игру!", show_alert=True)
        return
    
    if game.is_active:
        await callback_query.answer("❌ Игра уже идет!", show_alert=True)
        return
    
    if len(game.players) < MIN_PLAYERS:
        await callback_query.answer(f"❌ Нужно минимум {MIN_PLAYERS} игроков!", show_alert=True)
        return
    
    await callback_query.answer("🎮 Игра начинается!")
    
    if game.is_registration_active:
        game.is_registration_active = False
        if game.registration_task:
            game.registration_task.cancel()
            game.registration_task = None
    
    game.chat_id = callback_query.message.chat.id
    await start_new_game(callback_query.message.chat.id)

@dp.callback_query_handler(lambda c: c.data == "fast_start_game")
async def fast_start_game_callback(callback_query: types.CallbackQuery):
    if not await is_user_admin(callback_query.from_user.id, callback_query.message.chat.id):
        await callback_query.answer("⛔ Только администраторы могут начать игру!", show_alert=True)
        return
    
    if game.is_active:
        await callback_query.answer("❌ Игра уже идет!", show_alert=True)
        return
    
    if len(game.players) < MIN_PLAYERS:
        await callback_query.answer(f"❌ Нужно минимум {MIN_PLAYERS} игроков!", show_alert=True)
        return
    
    await callback_query.answer("⚡ Быстрый старт!")
    
    if game.is_registration_active:
        game.is_registration_active = False
        if game.registration_task:
            game.registration_task.cancel()
            game.registration_task = None
    
    game.chat_id = callback_query.message.chat.id
    await bot.send_message(callback_query.message.chat.id, "⚡ Быстрый старт! Игра начинается!")
    await start_new_game(callback_query.message.chat.id)

# ===================== ОБРАБОТЧИКИ ВЫБОРА ЭМОДЗИ =====================
@dp.callback_query_handler(lambda c: c.data.startswith("select_emoji_"))
async def select_emoji(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    
    if not game.is_active or user_id != game.current_player_id:
        await callback_query.answer("❌ Сейчас не ваш ход!", show_alert=True)
        return
    
    emoji = callback_query.data.replace("select_emoji_", "")
    
    total_count = game.round_emojis.count(emoji)
    selected_count = game.selected_emojis.count(emoji)
    
    if selected_count >= total_count:
        await callback_query.answer("❌ Вы уже выбрали все доступные эмодзи этого типа!", show_alert=True)
        return
    
    game.selected_emojis.append(emoji)
    
    emojis_str = "".join(game.round_emojis)
    selected = game.selected_emojis
    
    text = f"🎰 <b>Ваш ход!</b>\n\n"
    text += f"Главный эмодзи: {game.main_emoji}\n"
    text += f"Ваши эмодзи: {emojis_str}\n\n"
    
    if selected:
        text += f"<b>Выбрано:</b> {', '.join(selected)}\n\n"
    
    text += f"<i>Выберите следующий эмодзи или нажмите 'Отправить'</i>"
    
    await callback_query.message.edit_text(text, reply_markup=get_emoji_keyboard(game.round_emojis, selected), parse_mode="HTML")
    await callback_query.answer(f"✅ Выбрано: {emoji}")

@dp.callback_query_handler(lambda c: c.data == "reset_choices")
async def reset_choices(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    
    if not game.is_active or user_id != game.current_player_id:
        await callback_query.answer("❌ Сейчас не ваш ход!", show_alert=True)
        return
    
    game.selected_emojis = []
    await send_choices_to_player(user_id)
    await callback_query.answer("🔄 Выбор сброшен")

@dp.callback_query_handler(lambda c: c.data == "submit_choices")
async def submit_choices(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    
    if not game.is_active or user_id != game.current_player_id:
        await callback_query.answer("❌ Сейчас не ваш ход!", show_alert=True)
        return
    
    if not game.selected_emojis:
        await callback_query.answer("❌ Вы не выбрали ни одного эмодзи!", show_alert=True)
        return
    
    game.player_choices[user_id] = game.selected_emojis.copy()
    player_name = get_player_name(user_id)
    main_emoji = game.main_emoji
    
    choices_text = ", ".join(game.selected_emojis)
    total_count = len(game.selected_emojis)
    
    main_count = game.round_emojis.count(main_emoji)
    is_truth = all(e == main_emoji for e in game.selected_emojis) and total_count == main_count
    
    # Проверяем на 4 или 5 одинаковых эмодзи
    emoji_counts = Counter(game.selected_emojis)
    for emoji, count in emoji_counts.items():
        if count >= 4:
            game.player_scores[user_id] = game.player_scores.get(user_id, 0.0) + 4
    
    # Маскируем выбор под главный эмодзи
    masked_choices = [main_emoji] * total_count
    masked_text = ", ".join(masked_choices)
    
    # В группу отправляем ТОЛЬКО замаскированный выбор!
    text = f"🎯 <b>Ход игрока {player_name}</b>\n\n"
    text += f"Главный эмодзи: {main_emoji}\n"
    text += f"Выбор: {masked_text}\n"
    text += f"Всего: {total_count} эмодзи\n\n"
    text += f"<i>Следующий игрок, ваша очередь судить!</i>"
    
    chat_id = callback_query.message.chat.id if callback_query.message.chat else None
    if not chat_id:
        await callback_query.answer("❌ Ошибка: не удалось определить группу")
        return
    
    # Отправляем сообщение в группу
    sent_msg = await bot.send_message(chat_id, text, reply_markup=get_judge_keyboard(), parse_mode="HTML")
    
    game.awaiting_judge = True
    game.judge_player_id = get_next_alive_player(user_id)
    game.waiting_for_judge_from = user_id
    game.current_judge_message_id = sent_msg.message_id
    
    await callback_query.message.delete()
    await callback_query.answer("✅ Ваш выбор отправлен в группу!")

# ===================== ОБРАБОТЧИКИ СУДЬИ =====================
@dp.callback_query_handler(lambda c: c.data.startswith("judge_"))
async def judge_decision(callback_query: types.CallbackQuery):
    if not game.awaiting_judge:
        await callback_query.answer("❌ Сейчас не время судить!")
        return
    
    judge_id = callback_query.from_user.id
    if judge_id != game.judge_player_id:
        await callback_query.answer("❌ Сейчас не ваш ход судить!", show_alert=True)
        return
    
    decision = callback_query.data.replace("judge_", "")
    current_player = game.waiting_for_judge_from
    chat_id = callback_query.message.chat.id
    
    choices = game.player_choices.get(current_player, [])
    main_emoji = game.main_emoji
    main_count = game.round_emojis.count(main_emoji)
    
    is_truth = all(e == main_emoji for e in choices) and len(choices) == main_count
    
    # Удаляем сообщение с кнопками судьи
    try:
        await callback_query.message.delete()
    except:
        pass
    
    if decision == "true":
        if is_truth:
            game.awaiting_judge = False
            await bot.send_message(chat_id, "✅ Судья подтвердил правду! Ход передан дальше.")
            await start_player_turn(chat_id)
            await callback_query.answer("✅ Правда!")
        else:
            game.player_scores[judge_id] = game.player_scores.get(judge_id, 0.0) - 2
            await handle_shot(current_player, chat_id, "игрок соврал", judge_id)
            await callback_query.answer("❌ Игрок соврал!")
    else:  # false
        if is_truth:
            game.player_scores[judge_id] = game.player_scores.get(judge_id, 0.0) + 4
            await handle_shot(judge_id, chat_id, "судья ошибся", judge_id)
            await callback_query.answer("❌ Судья ошибся!")
        else:
            game.player_scores[judge_id] = game.player_scores.get(judge_id, 0.0) + 4
            game.player_scores[current_player] = game.player_scores.get(current_player, 0.0) - 2
            await handle_shot(current_player, chat_id, "игрок соврал", judge_id)
            await callback_query.answer("✅ Судья прав!")

async def handle_shot(player_id: int, chat_id: int, reason: str, judge_id: int = None):
    lives = game.player_lives.get(player_id, MAX_LIFE)
    player_name = get_player_name(player_id)
    
    shot_chance = random.randint(1, 6)
    if shot_chance == 1:
        lives -= 1
        game.player_lives[player_id] = lives
        
        if lives <= 0:
            text = f"💀 <b>{player_name} погиб!</b>\n"
            text += f"Причина: {reason}\n"
            text += f"Осталось жизней: 0\n"
            
            if judge_id:
                game.player_scores[judge_id] = game.player_scores.get(judge_id, 0.0) + 5
                text += f"Судья получает +5 очков за разоблачение!"
            
            await bot.send_message(chat_id, text, parse_mode="HTML")
            
            winner = check_score_winner()
            if winner:
                await end_game(chat_id)
                return
            
            alive = get_alive_players()
            if len(alive) <= 1:
                await end_game(chat_id)
            else:
                game.awaiting_judge = False
                await start_player_turn(chat_id)
        else:
            game.player_scores[player_id] = game.player_scores.get(player_id, 0.0) + 3
            text = f"💥 <b>{player_name} выжил!</b>\n"
            text += f"Причина: {reason}\n"
            text += f"Осталось жизней: {lives}\n"
            text += f"Статус: {get_player_status(player_id)}"
            await bot.send_message(chat_id, text, parse_mode="HTML")
            
            winner = check_score_winner()
            if winner:
                await end_game(chat_id)
            else:
                game.awaiting_judge = False
                await start_player_turn(chat_id)
    else:
        game.player_scores[player_id] = game.player_scores.get(player_id, 0.0) + 3
        text = f"💪 <b>{player_name} выжил!</b>\n"
        text += f"Причина: {reason}\n"
        text += f"Осталось жизней: {lives}\n"
        text += f"Статус: {get_player_status(player_id)}"
        await bot.send_message(chat_id, text, parse_mode="HTML")
        
        winner = check_score_winner()
        if winner:
            await end_game(chat_id)
        else:
            game.awaiting_judge = False
            await start_player_turn(chat_id)

def check_score_winner() -> Optional[int]:
    """Проверяет, есть ли победитель по очкам"""
    for uid in game.players:
        if is_player_alive(uid):
            score = game.player_scores.get(uid, 0.0)
            if score >= game.win_score:
                return uid
    return None

# ===================== ЗАПУСК БОТА =====================
if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)