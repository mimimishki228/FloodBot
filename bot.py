import asyncio
import random
import logging
from typing import List, Dict, Optional
from datetime import datetime

from aiogram import Bot, Dispatcher, types
from aiogram.contrib.middlewares.logging import LoggingMiddleware
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ChatMember
from aiogram.utils import executor
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup

# ===================== КОНФИГУРАЦИЯ =====================
BOT_TOKEN = "ВАШ_ТОКЕН_БОТА"  # Замените на ваш токен
BOT_USERNAME = "kazikcoolbot"  # Имя бота без @

# Эмодзи для игры
EMOJIS = ["🍋", "🍇", "👑", "🍗", "💸", "🥇", "💎"]
MAX_PLAYERS = 4
MIN_PLAYERS = 2
MAX_LIFE = 5

# ===================== ИНИЦИАЛИЗАЦИЯ =====================
logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)
dp.middleware.setup(LoggingMiddleware())

# ===================== СОСТОЯНИЯ FSM =====================
class GameStates(StatesGroup):
    WAITING_FOR_EMOJI = State()
    WAITING_FOR_COUNT = State()
    WAITING_FOR_ACTION = State()
    WAITING_FOR_JUDGE = State()

# ===================== ГЛОБАЛЬНОЕ СОСТОЯНИЕ ИГРЫ =====================
class GameState:
    def __init__(self):
        self.players: List[int] = []
        self.registered: Dict[int, str] = {}
        self.is_active: bool = False
        self.current_player_index: int = 0
        self.main_emoji: Optional[str] = None
        self.round_emojis: Optional[List[str]] = None
        self.player_lives: Dict[int, int] = {}
        self.player_choices: Dict[int, List[Dict]] = {}
        self.history: List[Dict] = []
        self.awaiting_judge: bool = False
        self.judge_player_id: Optional[int] = None
        self.current_player_id: Optional[int] = None
        self.chat_id: Optional[int] = None

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

def is_player_alive(user_id: int) -> bool:
    return game.player_lives.get(user_id, 0) > 0

def get_alive_players() -> List[int]:
    return [uid for uid in game.players if is_player_alive(uid)]

# ===================== КЛАВИАТУРЫ =====================
def get_join_leave_keyboard(user_id: int):
    buttons = []
    
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
    
    if not game.is_active and len(game.players) >= MIN_PLAYERS:
        buttons.append(InlineKeyboardButton(text="🎮 Начать игру", callback_data="start_game"))
    
    return InlineKeyboardMarkup(row_width=1).add(*buttons)

def get_emoji_keyboard(emojis: List[str]):
    buttons = []
    unique_emojis = list(set(emojis))
    for emoji in unique_emojis:
        buttons.append(InlineKeyboardButton(text=f"{emoji} (×{emojis.count(emoji)})", 
                                           callback_data=f"select_emoji_{emoji}"))
    
    buttons.append(InlineKeyboardButton(text="❌ Отменить ход", callback_data="cancel_turn"))
    return InlineKeyboardMarkup(row_width=2).add(*buttons)

def get_count_keyboard(emoji: str, max_count: int, current_choices: List[Dict]):
    buttons = []
    used_counts = [choice["count"] for choice in current_choices if choice["emoji"] == emoji]
    
    for i in range(1, max_count + 1):
        if i not in used_counts:
            buttons.append(InlineKeyboardButton(text=str(i), callback_data=f"set_count_{emoji}_{i}"))
    
    buttons.append(InlineKeyboardButton(text="🔄 Заново", callback_data="reset_choices"))
    buttons.append(InlineKeyboardButton(text="📤 Отправить", callback_data="submit_choices"))
    
    return InlineKeyboardMarkup(row_width=3).add(*buttons)

def get_action_keyboard():
    buttons = [
        InlineKeyboardButton(text="🔄 Заново", callback_data="reset_choices"),
        InlineKeyboardButton(text="📤 Отправить", callback_data="submit_choices")
    ]
    return InlineKeyboardMarkup(row_width=2).add(*buttons)

def get_judge_keyboard():
    buttons = [
        InlineKeyboardButton(text="✅ Правда (Следующий)", callback_data="judge_true"),
        InlineKeyboardButton(text="❌ Ложь", callback_data="judge_false")
    ]
    return InlineKeyboardMarkup(row_width=2).add(*buttons)

# ===================== ОБРАБОТЧИКИ КОМАНД =====================
@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    if message.chat.type not in ["group", "supergroup"]:
        await message.answer("Этот бот работает только в группах!")
        return
    
    # Проверяем упоминание бота
    if not message.text or f"@{BOT_USERNAME}" not in message.text:
        return
    
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
    
    await message.answer(text, reply_markup=get_join_leave_keyboard(message.from_user.id), parse_mode="HTML")

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
    
    game.is_active = False
    game.players = []
    game.registered = {}
    game.player_lives = {}
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
    
    if len(game.players) > MAX_PLAYERS:
        await message.answer(f"Слишком много игроков! Максимум {MAX_PLAYERS}")
        return
    
    game.chat_id = message.chat.id
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
    game.player_choices = {}
    game.current_player_index = 0
    game.awaiting_judge = False
    
    players_list = "\n".join([f"• {get_player_name(uid)}" for uid in game.players])
    text = f"🎮 <b>Игра началась!</b>\n\n"
    text += f"Главный эмодзи: {game.main_emoji}\n"
    text += f"Участники:\n{players_list}\n\n"
    text += f"Удачи всем! 🍀"
    
    await bot.send_message(chat_id, text, parse_mode="HTML")
    await start_new_round(chat_id)

async def start_new_round(chat_id: int):
    alive_players = get_alive_players()
    if len(alive_players) <= 1:
        await end_game(chat_id)
        return
    
    game.main_emoji = get_main_emoji()
    game.round_emojis = spin_casino()
    game.player_choices = {}
    
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
    
    player_name = get_player_name(current_player)
    main_emoji = game.main_emoji
    emojis_str = "".join(game.round_emojis)
    
    text = f"🎰 <b>Ход игрока {player_name}</b>\n\n"
    text += f"Главный эмодзи: {main_emoji}\n"
    text += f"Выпало: {emojis_str}\n\n"
    text += f"Игроку отправлены варианты в ЛС"
    
    await bot.send_message(chat_id, text, parse_mode="HTML")
    await send_choices_to_player(current_player)

async def send_choices_to_player(user_id: int):
    if not game.round_emojis:
        return
    
    emojis_str = "".join(game.round_emojis)
    main_emoji = game.main_emoji
    
    text = f"🎰 <b>Ваш ход!</b>\n\n"
    text += f"Главный эмодзи: {main_emoji}\n"
    text += f"Ваши эмодзи: {emojis_str}\n\n"
    text += f"<i>Выберите эмодзи, который хотите показать (можно выбрать несколько)</i>"
    
    await bot.send_message(user_id, text, reply_markup=get_emoji_keyboard(game.round_emojis), parse_mode="HTML")

async def end_game(chat_id: int):
    alive_players = get_alive_players()
    if not alive_players:
        winner_id = game.players[0]
    else:
        winner_id = alive_players[0]
    
    winner_name = get_player_name(winner_id)
    
    game.history.append({
        "winner": winner_name,
        "date": datetime.now().strftime("%Y-%m-%d %H:%M")
    })
    
    text = f"🏆 <b>Игра окончена!</b>\n\n"
    text += f"Победитель: {winner_name}\n"
    text += f"Всего игроков: {len(game.players)}\n"
    text += f"Дата: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    
    await bot.send_message(chat_id, text, parse_mode="HTML")
    
    game.is_active = False
    game.players = []
    game.registered = {}
    game.player_lives = {}
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
    
    if len(game.players) >= MAX_PLAYERS:
        await callback_query.answer(f"❌ Лимит игроков достигнут ({MAX_PLAYERS})!", show_alert=True)
        return
    
    if user_id in game.players:
        await callback_query.answer("Вы уже в игре!", show_alert=True)
        return
    
    game.players.append(user_id)
    game.registered[user_id] = username
    game.chat_id = callback_query.message.chat.id
    
    text = f"🎮 <b>Набор в игру</b>\n\n"
    text += f"Статус: {'🔴 Игра идет' if game.is_active else '🟢 Набор открыт'}\n"
    text += f"Игроков: {len(game.players)}/{MAX_PLAYERS}\n\n"
    text += f"<b>Участники:</b>\n{get_players_list()}\n\n"
    
    if len(game.players) >= MAX_PLAYERS:
        text += f"\n⚠️ Лимит игроков достигнут ({MAX_PLAYERS})"
    elif not game.is_active and len(game.players) >= MIN_PLAYERS:
        text += "\n✅ Достаточно игроков! Нажмите 'Начать игру'"
    
    await callback_query.message.edit_text(text, reply_markup=get_join_leave_keyboard(user_id), parse_mode="HTML")
    await callback_query.answer("✅ Вы вошли в игру!")

@dp.callback_query_handler(lambda c: c.data == "leave_game")
async def leave_game(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    
    if user_id not in game.players:
        await callback_query.answer("Вы не в игре!", show_alert=True)
        return
    
    game.players.remove(user_id)
    if user_id in game.registered:
        del game.registered[user_id]
    
    text = f"🎮 <b>Набор в игру</b>\n\n"
    text += f"Статус: {'🔴 Игра идет' if game.is_active else '🟢 Набор открыт'}\n"
    text += f"Игроков: {len(game.players)}/{MAX_PLAYERS}\n\n"
    text += f"<b>Участники:</b>\n{get_players_list()}"
    
    await callback_query.message.edit_text(text, reply_markup=get_join_leave_keyboard(user_id), parse_mode="HTML")
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
    
    if len(game.players) > MAX_PLAYERS:
        await callback_query.answer(f"❌ Слишком много игроков! Максимум {MAX_PLAYERS}", show_alert=True)
        return
    
    await callback_query.answer("🎮 Игра начинается!")
    game.chat_id = callback_query.message.chat.id
    await start_new_game(callback_query.message.chat.id)

@dp.callback_query_handler(lambda c: c.data == "ignore")
async def ignore_callback(callback_query: types.CallbackQuery):
    await callback_query.answer()

# ===================== ОБРАБОТЧИКИ ВЫБОРА ЭМОДЗИ =====================
@dp.callback_query_handler(lambda c: c.data.startswith("select_emoji_"))
async def select_emoji(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    
    if not game.is_active or user_id != game.current_player_id:
        await callback_query.answer("❌ Сейчас не ваш ход!", show_alert=True)
        return
    
    emoji = callback_query.data.replace("select_emoji_", "")
    max_count = game.round_emojis.count(emoji)
    
    current_choices = game.player_choices.get(user_id, [])
    used_counts = [c["count"] for c in current_choices if c["emoji"] == emoji]
    
    if len(used_counts) >= max_count:
        await callback_query.answer("❌ Все варианты для этого эмодзи уже выбраны!", show_alert=True)
        return
    
    text = f"Выберите количество для {emoji}"
    await callback_query.message.edit_text(text, reply_markup=get_count_keyboard(emoji, max_count, current_choices))
    await callback_query.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("set_count_"))
async def set_count(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    
    if not game.is_active or user_id != game.current_player_id:
        await callback_query.answer("❌ Сейчас не ваш ход!", show_alert=True)
        return
    
    _, emoji, count = callback_query.data.split("_")
    count = int(count)
    
    if user_id not in game.player_choices:
        game.player_choices[user_id] = []
    
    game.player_choices[user_id].append({"emoji": emoji, "count": count})
    
    current_choices = game.player_choices[user_id]
    choices_text = "\n".join([f"{c['emoji']} × {c['count']}" for c in current_choices])
    
    text = f"✅ Выбрано:\n{choices_text}\n\n"
    text += f"Выберите еще эмодзи или нажмите 'Заново'/'Отправить'"
    
    await callback_query.message.edit_text(text, reply_markup=get_action_keyboard())
    await callback_query.answer(f"✅ Выбрано {emoji} × {count}")

@dp.callback_query_handler(lambda c: c.data == "reset_choices")
async def reset_choices(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    
    if not game.is_active or user_id != game.current_player_id:
        await callback_query.answer("❌ Сейчас не ваш ход!", show_alert=True)
        return
    
    if user_id in game.player_choices:
        game.player_choices[user_id] = []
    
    await send_choices_to_player(user_id)
    await callback_query.answer("🔄 Выбор сброшен")

@dp.callback_query_handler(lambda c: c.data == "submit_choices")
async def submit_choices(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    
    if not game.is_active or user_id != game.current_player_id:
        await callback_query.answer("❌ Сейчас не ваш ход!", show_alert=True)
        return
    
    if user_id not in game.player_choices or not game.player_choices[user_id]:
        await callback_query.answer("❌ Вы не сделали ни одного выбора!", show_alert=True)
        return
    
    choices = game.player_choices[user_id]
    player_name = get_player_name(user_id)
    main_emoji = game.main_emoji
    
    choices_text = "\n".join([f"{c['emoji']} × {c['count']}" for c in choices])
    total_count = sum(c["count"] for c in choices)
    
    main_count = game.round_emojis.count(main_emoji)
    is_truth = all(c["emoji"] == main_emoji for c in choices) and total_count == main_count
    
    text = f"🎯 <b>Ход игрока {player_name}</b>\n\n"
    text += f"Главный эмодзи: {main_emoji}\n"
    text += f"Выбор: {choices_text}\n"
    text += f"Всего: {total_count} эмодзи\n\n"
    text += f"<i>Следующий игрок, ваша очередь судить!</i>"
    
    chat_id = callback_query.message.chat.id if callback_query.message.chat else None
    if not chat_id:
        await callback_query.answer("❌ Ошибка: не удалось определить группу")
        return
    
    await bot.send_message(chat_id, text, reply_markup=get_judge_keyboard(), parse_mode="HTML")
    
    game.awaiting_judge = True
    game.judge_player_id = get_next_player()
    
    await callback_query.message.delete()
    await callback_query.answer("✅ Ваш выбор отправлен в группу!")

@dp.callback_query_handler(lambda c: c.data == "cancel_turn")
async def cancel_turn(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    
    if not game.is_active or user_id != game.current_player_id:
        await callback_query.answer("❌ Сейчас не ваш ход!", show_alert=True)
        return
    
    if user_id in game.player_choices:
        game.player_choices[user_id] = []
    
    await callback_query.message.delete()
    await callback_query.answer("❌ Ход отменен")

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
    current_player = game.current_player_id
    chat_id = callback_query.message.chat.id
    
    if decision == "true":
        game.awaiting_judge = False
        await callback_query.message.edit_text(callback_query.message.text + "\n\n✅ Судья подтвердил правду!", parse_mode="HTML")
        await start_player_turn(chat_id)
        await callback_query.answer("✅ Правда! Ход передан дальше")
        
    else:
        choices = game.player_choices.get(current_player, [])
        main_emoji = game.main_emoji
        main_count = game.round_emojis.count(main_emoji)
        
        is_truth = all(c["emoji"] == main_emoji for c in choices) and sum(c["count"] for c in choices) == main_count
        
        if is_truth:
            await handle_shot(judge_id, chat_id, "судья ошибся")
        else:
            await handle_shot(current_player, chat_id, "игрок соврал")
        
        game.awaiting_judge = False

async def handle_shot(player_id: int, chat_id: int, reason: str):
    lives = game.player_lives.get(player_id, MAX_LIFE)
    player_name = get_player_name(player_id)
    
    shot_chance = random.randint(1, 6)
    if shot_chance == 1:
        lives -= 1
        game.player_lives[player_id] = lives
        
        if lives <= 0:
            text = f"💀 <b>{player_name} погиб!</b>\n"
            text += f"Причина: {reason}\n"
            text += f"Осталось жизней: 0"
            await bot.send_message(chat_id, text, parse_mode="HTML")
            
            alive = get_alive_players()
            if len(alive) <= 1:
                await end_game(chat_id)
            else:
                await start_player_turn(chat_id)
        else:
            text = f"💥 <b>{player_name} выжил!</b>\n"
            text += f"Причина: {reason}\n"
            text += f"Осталось жизней: {lives}"
            await bot.send_message(chat_id, text, parse_mode="HTML")
            await start_player_turn(chat_id)
    else:
        text = f"💪 <b>{player_name} выжил!</b>\n"
        text += f"Причина: {reason}\n"
        text += f"Осталось жизней: {lives}"
        await bot.send_message(chat_id, text, parse_mode="HTML")
        await start_player_turn(chat_id)

# ===================== ЗАПУСК БОТА =====================
if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)