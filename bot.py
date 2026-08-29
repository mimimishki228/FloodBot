import asyncio
import random
import logging
from typing import List, Dict, Optional, Set
from datetime import datetime
from collections import Counter

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, ChatMember
from aiogram.enums import ParseMode, ChatMemberStatus
from aiogram.client.default import DefaultBotProperties

# ===================== КОНФИГУРАЦИЯ =====================
BOT_TOKEN = "ВАШ_ТОКЕН_БОТА"  # Замените на ваш токен
BOT_USERNAME = "kazikcoolbot"  # Имя бота без @

# Эмодзи для игры
EMOJIS = ["🍋", "🍇", "👑", "🍗", "💸", "🥇", "💎"]
MAX_PLAYERS = 4
MIN_PLAYERS = 2
MAX_LIFE = 5  # Максимум жизней в русской рулетке

# ===================== ИНИЦИАЛИЗАЦИЯ =====================
logging.basicConfig(level=logging.INFO)
storage = MemoryStorage()
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=storage)

# ===================== СОСТОЯНИЯ FSM =====================
class GameStates(StatesGroup):
    WAITING_FOR_EMOJI = State()  # Выбор эмодзи
    WAITING_FOR_COUNT = State()  # Выбор количества
    WAITING_FOR_ACTION = State()  # Ожидание действия (заново/отправить)
    WAITING_FOR_JUDGE = State()  # Ожидание решения судьи

# ===================== ГЛОБАЛЬНОЕ СОСТОЯНИЕ ИГРЫ =====================
class GameState:
    def __init__(self):
        self.players: List[int] = []  # ID игроков
        self.registered: Dict[int, str] = {}  # user_id -> username
        self.is_active: bool = False
        self.current_player_index: int = 0
        self.main_emoji: Optional[str] = None
        self.round_emojis: Optional[List[str]] = None
        self.player_lives: Dict[int, int] = {}  # user_id -> lives
        self.player_choices: Dict[int, List[Dict]] = {}  # user_id -> [{"emoji": str, "count": int}]
        self.history: List[Dict] = []  # История победителей
        self.awaiting_judge: bool = False
        self.judge_player_id: Optional[int] = None
        self.current_player_id: Optional[int] = None
        self.round_start_time: Optional[datetime] = None
        self.game_starter_id: Optional[int] = None  # Кто начал игру
        self.chat_id: Optional[int] = None  # ID чата, где идет игра

game = GameState()

# ===================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =====================
async def is_user_admin(user_id: int, chat_id: int) -> bool:
    """Проверяет, является ли пользователь администратором группы"""
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR]
    except:
        return False

def get_main_emoji() -> str:
    """Выбирает главный эмодзи для раунда"""
    return random.choice(EMOJIS)

def spin_casino() -> List[str]:
    """Генерирует 5 случайных эмодзи"""
    return [random.choice(EMOJIS) for _ in range(5)]

def count_emoji(emojis: List[str], target: str) -> int:
    """Считает количество target эмодзи в списке"""
    return emojis.count(target)

def get_player_name(user_id: int) -> str:
    """Возвращает имя игрока"""
    return game.registered.get(user_id, f"User{user_id}")

def get_players_list() -> str:
    """Возвращает список игроков для отображения"""
    if not game.players:
        return "Нет игроков"
    return "\n".join([f"• {get_player_name(uid)}" for uid in game.players])

def get_player_status(user_id: int) -> str:
    """Возвращает статус игрока (жизни)"""
    lives = game.player_lives.get(user_id, MAX_LIFE)
    return f"❤️" * lives + f"🖤" * (MAX_LIFE - lives)

def get_next_player() -> Optional[int]:
    """Возвращает следующего живого игрока"""
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

def is_player_alive(user_id: int) -> bool:
    """Проверяет, жив ли игрок"""
    return game.player_lives.get(user_id, 0) > 0

def get_alive_players() -> List[int]:
    """Возвращает список живых игроков"""
    return [uid for uid in game.players if is_player_alive(uid)]

# ===================== КЛАВИАТУРЫ =====================
def get_join_leave_keyboard(user_id: int):
    """Клавиатура с входом/выходом для конкретного пользователя"""
    buttons = []
    
    # Проверяем, достигнут ли лимит игроков
    if len(game.players) >= MAX_PLAYERS:
        if user_id not in game.players:
            buttons.append(InlineKeyboardButton(text="❌ Лимит игроков превышен", callback_data="ignore"))
        else:
            buttons.append(InlineKeyboardButton(text="🚪 Выйти из игры", callback_data="leave_game"))
    else:
        if user_id in game.players:
            buttons.append(InlineKeyboardButton(text="🚪 Выйти из игры", callback_data="leave_game"))
        else:
            buttons.append(InlineKeyboardButton(text="✅ Войти в игру", callback_data="join_game"))
    
    # Добавляем кнопку начала игры, если достаточно игроков и игра не активна
    if not game.is_active and len(game.players) >= MIN_PLAYERS:
        buttons.append(InlineKeyboardButton(text="🎮 Начать игру", callback_data="start_game"))
    
    return InlineKeyboardMarkup(inline_keyboard=[buttons])

def get_emoji_keyboard(emojis: List[str]):
    """Клавиатура для выбора эмодзи"""
    buttons = []
    # Показываем уникальные эмодзи
    unique_emojis = list(set(emojis))
    for emoji in unique_emojis:
        buttons.append(InlineKeyboardButton(text=f"{emoji} (×{emojis.count(emoji)})", 
                                           callback_data=f"select_emoji_{emoji}"))
    
    # Добавляем кнопку отмены
    buttons.append(InlineKeyboardButton(text="❌ Отменить ход", callback_data="cancel_turn"))
    
    # Разбиваем по 2 кнопки в ряд
    keyboard = []
    for i in range(0, len(buttons), 2):
        keyboard.append(buttons[i:i+2])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_count_keyboard(emoji: str, max_count: int, current_choices: List[Dict]):
    """Клавиатура для выбора количества"""
    buttons = []
    used_counts = [choice["count"] for choice in current_choices if choice["emoji"] == emoji]
    
    for i in range(1, max_count + 1):
        if i not in used_counts:  # Не показываем уже выбранные количества
            buttons.append(InlineKeyboardButton(text=str(i), callback_data=f"set_count_{emoji}_{i}"))
    
    # Добавляем кнопки управления
    buttons.append(InlineKeyboardButton(text="🔄 Заново", callback_data="reset_choices"))
    buttons.append(InlineKeyboardButton(text="📤 Отправить", callback_data="submit_choices"))
    
    # Разбиваем по 3 кнопки в ряд
    keyboard = []
    for i in range(0, len(buttons), 3):
        keyboard.append(buttons[i:i+3])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_action_keyboard():
    """Клавиатура с действиями (Заново/Отправить)"""
    buttons = [
        InlineKeyboardButton(text="🔄 Заново", callback_data="reset_choices"),
        InlineKeyboardButton(text="📤 Отправить", callback_data="submit_choices")
    ]
    return InlineKeyboardMarkup(inline_keyboard=[buttons])

def get_judge_keyboard():
    """Клавиатура для судьи (следующий игрок)"""
    buttons = [
        InlineKeyboardButton(text="✅ Правда (Следующий)", callback_data="judge_true"),
        InlineKeyboardButton(text="❌ Ложь", callback_data="judge_false")
    ]
    return InlineKeyboardMarkup(inline_keyboard=[buttons])

# ===================== ОБРАБОТЧИКИ КОМАНД =====================
@dp.message(Command("start"))
async def cmd_start(message: Message, command: CommandObject):
    """Команда /start - показывает статус игры и кнопки"""
    if message.chat.type != "group" and message.chat.type != "supergroup":
        await message.answer("Этот бот работает только в группах!")
        return
    
    # Проверяем, что команда вызвана с упоминанием бота
    if command.args != BOT_USERNAME and message.text != f"/start@{BOT_USERNAME}":
        return
    
    # Сохраняем ID чата
    game.chat_id = message.chat.id
    
    text = f"🎮 <b>Набор в игру</b>\n\n"
    text += f"Статус: {'🔴 Игра идет' if game.is_active else '🟢 Набор открыт'}\n"
    text += f"Игроков: {len(game.players)}/{MAX_PLAYERS}\n\n"
    text += f"<b>Участники:</b>\n{get_players_list()}\n\n"
    
    if len(game.players) >= MAX_PLAYERS:
        text += f"\n⚠️ Лимит игроков достигнут ({MAX_PLAYERS})"
    elif not game.is_active and len(game.players) >= MIN_PLAYERS:
        text += f"\n✅ Достаточно игроков! Нажмите 'Начать игру'"
    elif not game.is_active:
        text += f"\n⏳ Нужно еще {MIN_PLAYERS - len(game.players)} игроков"
    
    await message.answer(text, reply_markup=get_join_leave_keyboard(message.from_user.id))

@dp.message(Command("stop"))
async def cmd_stop(message: Message):
    """Команда /stop - принудительное завершение игры (только админы)"""
    if message.chat.type != "group" and message.chat.type != "supergroup":
        await message.answer("Эта команда работает только в группах!")
        return
    
    # Проверяем, является ли пользователь администратором
    if not await is_user_admin(message.from_user.id, message.chat.id):
        await message.answer("⛔ Эта команда доступна только администраторам группы!")
        return
    
    if not game.is_active and not game.players:
        await message.answer("Игра не активна или не начата")
        return
    
    # Завершаем игру
    game.is_active = False
    game.players = []
    game.registered = {}
    game.player_lives = {}
    game.player_choices = {}
    game.round_emojis = None
    game.main_emoji = None
    game.awaiting_judge = False
    game.game_starter_id = None
    game.chat_id = None
    
    await message.answer("🏁 Игра принудительно завершена администратором!")

@dp.message(Command("startgame"))
async def cmd_startgame(message: Message):
    """Команда /startgame - начало игры (только админы)"""
    if message.chat.type != "group" and message.chat.type != "supergroup":
        await message.answer("Эта команда работает только в группах!")
        return
    
    # Проверяем, является ли пользователь администратором
    if not await is_user_admin(message.from_user.id, message.chat.id):
        await message.answer("⛔ Эта команда доступна только администраторам группы!")
        return
    
    if game.is_active:
        await message.answer("Игра уже идет!")
        return
    
    if len(game.players) < MIN_PLAYERS:
        await message.answer(f"Недостаточно игроков! Нужно минимум {MIN_PLAYERS}")
        return
    
    if len(game.players) > MAX_PLAYERS:
        await message.answer(f"Слишком много игроков! Максимум {MAX_PLAYERS}")
        return
    
    # Инициализируем игру
    game.chat_id = message.chat.id
    await start_new_game(message.chat.id, message.from_user.id)

@dp.message(Command("leaderboard"))
async def cmd_leaderboard(message: Message):
    """Команда /leaderboard - показывает победителей"""
    if not game.history:
        await message.answer("📊 История побед пуста")
        return
    
    text = "🏆 <b>Таблица победителей</b>\n\n"
    for i, win in enumerate(game.history[-10:], 1):  # Показываем последние 10
        text += f"{i}. {win['winner']} - {win['date']}\n"
    
    await message.answer(text)

# ===================== ИГРОВАЯ ЛОГИКА =====================
async def start_new_game(chat_id: int, starter_id: int):
    """Начинает новую игру"""
    game.is_active = True
    game.main_emoji = get_main_emoji()
    game.player_lives = {uid: MAX_LIFE for uid in game.players}
    game.player_choices = {}
    game.current_player_index = 0
    game.awaiting_judge = False
    game.game_starter_id = starter_id
    
    # Отправляем сообщение о начале игры
    players_list = "\n".join([f"• {get_player_name(uid)}" for uid in game.players])
    text = f"🎮 <b>Игра началась!</b>\n\n"
    text += f"Главный эмодзи: {game.main_emoji}\n"
    text += f"Участники:\n{players_list}\n\n"
    text += f"Удачи всем! 🍀"
    
    await bot.send_message(chat_id, text)
    
    # Начинаем первый раунд
    await start_new_round(chat_id)

async def start_new_round(chat_id: int):
    """Начинает новый раунд"""
    # Проверяем, есть ли живые игроки
    alive_players = get_alive_players()
    if len(alive_players) <= 1:
        # Конец игры
        await end_game(chat_id)
        return
    
    # Выбираем главный эмодзи
    game.main_emoji = get_main_emoji()
    game.round_emojis = spin_casino()
    game.player_choices = {}
    
    # Находим первого живого игрока
    first_alive = alive_players[0]
    game.current_player_index = game.players.index(first_alive)
    
    # Начинаем ход первого игрока
    await start_player_turn(chat_id)

async def start_player_turn(chat_id: int):
    """Начинает ход текущего игрока"""
    current_player = get_next_player()
    if not current_player:
        await end_game(chat_id)
        return
    
    game.current_player_id = current_player
    game.awaiting_judge = False
    
    # Отправляем сообщение в группу о начале хода
    player_name = get_player_name(current_player)
    main_emoji = game.main_emoji
    emojis_str = "".join(game.round_emojis)
    
    text = f"🎰 <b>Ход игрока {player_name}</b>\n\n"
    text += f"Главный эмодзи: {main_emoji}\n"
    text += f"Выпало: {emojis_str}\n\n"
    text += f"Игроку отправлены варианты в ЛС"
    
    await bot.send_message(chat_id, text)
    
    # Отправляем игроку его эмодзи для выбора
    await send_choices_to_player(current_player)

async def send_choices_to_player(user_id: int):
    """Отправляет игроку варианты для выбора"""
    if not game.round_emojis:
        return
    
    # Показываем игроку его эмодзи
    emojis_str = "".join(game.round_emojis)
    main_emoji = game.main_emoji
    
    text = f"🎰 <b>Ваш ход!</b>\n\n"
    text += f"Главный эмодзи: {main_emoji}\n"
    text += f"Ваши эмодзи: {emojis_str}\n\n"
    text += f"<i>Выберите эмодзи, который хотите показать (можно выбрать несколько)</i>"
    
    # Удаляем предыдущие сообщения от бота в ЛС
    await bot.send_message(user_id, text, reply_markup=get_emoji_keyboard(game.round_emojis))

async def end_game(chat_id: int):
    """Завершает игру и определяет победителя"""
    alive_players = get_alive_players()
    if not alive_players:
        winner_id = game.players[0]  # Если все мертвы, победитель - первый игрок
    else:
        winner_id = alive_players[0]
    
    winner_name = get_player_name(winner_id)
    
    # Сохраняем в историю
    game.history.append({
        "winner": winner_name,
        "date": datetime.now().strftime("%Y-%m-%d %H:%M")
    })
    
    text = f"🏆 <b>Игра окончена!</b>\n\n"
    text += f"Победитель: {winner_name}\n"
    text += f"Всего игроков: {len(game.players)}\n"
    text += f"Дата: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    
    await bot.send_message(chat_id, text)
    
    # Сбрасываем состояние игры
    game.is_active = False
    game.players = []
    game.registered = {}
    game.player_lives = {}
    game.player_choices = {}
    game.round_emojis = None
    game.main_emoji = None
    game.awaiting_judge = False
    game.game_starter_id = None
    game.chat_id = None

# ===================== ОБРАБОТЧИКИ CALLBACK =====================
@dp.callback_query(F.data == "join_game")
async def join_game(callback: CallbackQuery):
    """Регистрация игрока"""
    user_id = callback.from_user.id
    username = callback.from_user.full_name or callback.from_user.username or f"User{user_id}"
    
    if game.is_active:
        await callback.answer("❌ Игра уже начата!", show_alert=True)
        return
    
    # Проверяем лимит игроков
    if len(game.players) >= MAX_PLAYERS:
        await callback.answer(f"❌ Лимит игроков достигнут ({MAX_PLAYERS})!", show_alert=True)
        return
    
    if user_id in game.players:
        await callback.answer("Вы уже в игре!", show_alert=True)
        return
    
    game.players.append(user_id)
    game.registered[user_id] = username
    game.chat_id = callback.message.chat.id
    
    # Обновляем сообщение
    text = f"🎮 <b>Набор в игру</b>\n\n"
    text += f"Статус: {'🔴 Игра идет' if game.is_active else '🟢 Набор открыт'}\n"
    text += f"Игроков: {len(game.players)}/{MAX_PLAYERS}\n\n"
    text += f"<b>Участники:</b>\n{get_players_list()}\n\n"
    
    if len(game.players) >= MAX_PLAYERS:
        text += f"\n⚠️ Лимит игроков достигнут ({MAX_PLAYERS})"
    elif not game.is_active and len(game.players) >= MIN_PLAYERS:
        text += "\n✅ Достаточно игроков! Нажмите 'Начать игру'"
    
    await callback.message.edit_text(text, reply_markup=get_join_leave_keyboard(user_id))
    await callback.answer("✅ Вы вошли в игру!")

@dp.callback_query(F.data == "leave_game")
async def leave_game(callback: CallbackQuery):
    """Выход из игры"""
    user_id = callback.from_user.id
    
    if user_id not in game.players:
        await callback.answer("Вы не в игре!", show_alert=True)
        return
    
    game.players.remove(user_id)
    if user_id in game.registered:
        del game.registered[user_id]
    
    text = f"🎮 <b>Набор в игру</b>\n\n"
    text += f"Статус: {'🔴 Игра идет' if game.is_active else '🟢 Набор открыт'}\n"
    text += f"Игроков: {len(game.players)}/{MAX_PLAYERS}\n\n"
    text += f"<b>Участники:</b>\n{get_players_list()}"
    
    await callback.message.edit_text(text, reply_markup=get_join_leave_keyboard(user_id))
    await callback.answer("✅ Вы вышли из игры")

@dp.callback_query(F.data == "start_game")
async def start_game_callback(callback: CallbackQuery):
    """Начало игры через кнопку (только админы)"""
    # Проверяем, является ли пользователь администратором
    if not await is_user_admin(callback.from_user.id, callback.message.chat.id):
        await callback.answer("⛔ Только администраторы могут начать игру!", show_alert=True)
        return
    
    if game.is_active:
        await callback.answer("❌ Игра уже идет!", show_alert=True)
        return
    
    if len(game.players) < MIN_PLAYERS:
        await callback.answer(f"❌ Нужно минимум {MIN_PLAYERS} игроков!", show_alert=True)
        return
    
    if len(game.players) > MAX_PLAYERS:
        await callback.answer(f"❌ Слишком много игроков! Максимум {MAX_PLAYERS}", show_alert=True)
        return
    
    await callback.answer("🎮 Игра начинается!")
    game.chat_id = callback.message.chat.id
    await start_new_game(callback.message.chat.id, callback.from_user.id)

@dp.callback_query(F.data == "ignore")
async def ignore_callback(callback: CallbackQuery):
    await callback.answer()

# ===================== ОБРАБОТЧИКИ ВЫБОРА ЭМОДЗИ =====================
@dp.callback_query(F.data.startswith("select_emoji_"))
async def select_emoji(callback: CallbackQuery):
    """Выбор эмодзи для игры"""
    user_id = callback.from_user.id
    
    if not game.is_active or user_id != game.current_player_id:
        await callback.answer("❌ Сейчас не ваш ход!", show_alert=True)
        return
    
    emoji = callback.data.replace("select_emoji_", "")
    max_count = game.round_emojis.count(emoji)
    
    # Проверяем, не выбрали ли уже все возможные комбинации
    current_choices = game.player_choices.get(user_id, [])
    used_counts = [c["count"] for c in current_choices if c["emoji"] == emoji]
    
    if len(used_counts) >= max_count:
        await callback.answer("❌ Все варианты для этого эмодзи уже выбраны!", show_alert=True)
        return
    
    # Показываем выбор количества
    text = f"Выберите количество для {emoji}"
    await callback.message.edit_text(text, reply_markup=get_count_keyboard(emoji, max_count, current_choices))
    await callback.answer()

@dp.callback_query(F.data.startswith("set_count_"))
async def set_count(callback: CallbackQuery):
    """Выбор количества эмодзи"""
    user_id = callback.from_user.id
    
    if not game.is_active or user_id != game.current_player_id:
        await callback.answer("❌ Сейчас не ваш ход!", show_alert=True)
        return
    
    _, emoji, count = callback.data.split("_")
    count = int(count)
    
    # Добавляем выбор
    if user_id not in game.player_choices:
        game.player_choices[user_id] = []
    
    game.player_choices[user_id].append({"emoji": emoji, "count": count})
    
    # Показываем текущие выборы
    current_choices = game.player_choices[user_id]
    choices_text = "\n".join([f"{c['emoji']} × {c['count']}" for c in current_choices])
    
    text = f"✅ Выбрано:\n{choices_text}\n\n"
    text += f"Выберите еще эмодзи или нажмите 'Заново'/'Отправить'"
    
    await callback.message.edit_text(text, reply_markup=get_action_keyboard())
    await callback.answer(f"✅ Выбрано {emoji} × {count}")

@dp.callback_query(F.data == "reset_choices")
async def reset_choices(callback: CallbackQuery):
    """Сброс выбора"""
    user_id = callback.from_user.id
    
    if not game.is_active or user_id != game.current_player_id:
        await callback.answer("❌ Сейчас не ваш ход!", show_alert=True)
        return
    
    # Очищаем выборы
    if user_id in game.player_choices:
        game.player_choices[user_id] = []
    
    # Отправляем заново выбор эмодзи
    await send_choices_to_player(user_id)
    await callback.answer("🔄 Выбор сброшен")

@dp.callback_query(F.data == "submit_choices")
async def submit_choices(callback: CallbackQuery):
    """Отправка выбора в группу"""
    user_id = callback.from_user.id
    
    if not game.is_active or user_id != game.current_player_id:
        await callback.answer("❌ Сейчас не ваш ход!", show_alert=True)
        return
    
    if user_id not in game.player_choices or not game.player_choices[user_id]:
        await callback.answer("❌ Вы не сделали ни одного выбора!", show_alert=True)
        return
    
    choices = game.player_choices[user_id]
    player_name = get_player_name(user_id)
    main_emoji = game.main_emoji
    
    # Формируем сообщение для группы
    choices_text = "\n".join([f"{c['emoji']} × {c['count']}" for c in choices])
    total_count = sum(c["count"] for c in choices)
    
    # Проверяем, правда ли это или ложь
    main_count = game.round_emojis.count(main_emoji)
    is_truth = all(c["emoji"] == main_emoji for c in choices) and total_count == main_count
    
    text = f"🎯 <b>Ход игрока {player_name}</b>\n\n"
    text += f"Главный эмодзи: {main_emoji}\n"
    text += f"Выбор: {choices_text}\n"
    text += f"Всего: {total_count} эмодзи\n\n"
    text += f"<i>Следующий игрок, ваша очередь судить!</i>"
    
    # Отправляем в группу
    chat_id = callback.message.chat.id if callback.message.chat else None
    if not chat_id:
        await callback.answer("❌ Ошибка: не удалось определить группу")
        return
    
    await bot.send_message(chat_id, text, reply_markup=get_judge_keyboard())
    
    # Устанавливаем состояние ожидания судьи
    game.awaiting_judge = True
    game.judge_player_id = get_next_player()
    
    # Удаляем сообщение с выбором у игрока
    await callback.message.delete()
    await callback.answer("✅ Ваш выбор отправлен в группу!")

@dp.callback_query(F.data == "cancel_turn")
async def cancel_turn(callback: CallbackQuery):
    """Отмена хода"""
    user_id = callback.from_user.id
    
    if not game.is_active or user_id != game.current_player_id:
        await callback.answer("❌ Сейчас не ваш ход!", show_alert=True)
        return
    
    # Очищаем выборы и завершаем ход
    if user_id in game.player_choices:
        game.player_choices[user_id] = []
    
    await callback.message.delete()
    await callback.answer("❌ Ход отменен")

# ===================== ОБРАБОТЧИКИ СУДЬИ =====================
@dp.callback_query(F.data.startswith("judge_"))
async def judge_decision(callback: CallbackQuery):
    """Решение судьи (правда/ложь)"""
    if not game.awaiting_judge:
        await callback.answer("❌ Сейчас не время судить!")
        return
    
    judge_id = callback.from_user.id
    if judge_id != game.judge_player_id:
        await callback.answer("❌ Сейчас не ваш ход судить!", show_alert=True)
        return
    
    decision = callback.data.replace("judge_", "")
    current_player = game.current_player_id
    chat_id = callback.message.chat.id
    
    if decision == "true":
        # Судья считает, что игрок сказал правду
        # Передаем ход следующему игроку
        game.awaiting_judge = False
        await callback.message.edit_text(callback.message.text + "\n\n✅ Судья подтвердил правду!")
        await start_player_turn(chat_id)
        await callback.answer("✅ Правда! Ход передан дальше")
        
    else:  # false
        # Судья считает, что игрок соврал
        # Проверяем, соврал ли игрок на самом деле
        choices = game.player_choices.get(current_player, [])
        main_emoji = game.main_emoji
        main_count = game.round_emojis.count(main_emoji)
        
        is_truth = all(c["emoji"] == main_emoji for c in choices) and sum(c["count"] for c in choices) == main_count
        
        if is_truth:
            # Игрок сказал правду, судья ошибается
            # Судья стреляет в себя
            await handle_shot(judge_id, chat_id, "судья ошибся")
        else:
            # Игрок соврал, игрок стреляет в себя
            await handle_shot(current_player, chat_id, "игрок соврал")
        
        game.awaiting_judge = False

async def handle_shot(player_id: int, chat_id: int, reason: str):
    """Обрабатывает выстрел в русскую рулетку"""
    lives = game.player_lives.get(player_id, MAX_LIFE)
    player_name = get_player_name(player_id)
    
    # Шанс смерти (1/6 для каждого выстрела)
    shot_chance = random.randint(1, 6)
    if shot_chance == 1:
        # Смерть
        lives -= 1
        game.player_lives[player_id] = lives
        
        if lives <= 0:
            text = f"💀 <b>{player_name} погиб!</b>\n"
            text += f"Причина: {reason}\n"
            text += f"Осталось жизней: 0"
            await bot.send_message(chat_id, text)
            
            # Проверяем, есть ли живые игроки
            alive = get_alive_players()
            if len(alive) <= 1:
                await end_game(chat_id)
            else:
                # Продолжаем игру со следующего игрока
                await start_player_turn(chat_id)
        else:
            text = f"💥 <b>{player_name} выжил!</b>\n"
            text += f"Причина: {reason}\n"
            text += f"Осталось жизней: {lives}"
            await bot.send_message(chat_id, text)
            
            # Продолжаем игру
            await start_player_turn(chat_id)
    else:
        # Выжил
        text = f"💪 <b>{player_name} выжил!</b>\n"
        text += f"Причина: {reason}\n"
        text += f"Осталось жизней: {lives}"
        await bot.send_message(chat_id, text)
        
        # Продолжаем игру
        await start_player_turn(chat_id)

# ===================== ЗАПУСК БОТА =====================
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())