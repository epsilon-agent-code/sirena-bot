import os
import asyncio
import random
import json
import time
import sys
from datetime import datetime, timezone, timedelta
from pyrogram import Client, filters
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

# === ЛОГИРОВАНИЕ ===
api_id = os.getenv('API_ID')
api_hash = os.getenv('API_HASH')
string_session = os.getenv('STRING_SESSION')
dashscope_key = os.getenv('DASHSCOPE_API_KEY')

print(f"API_ID: {api_id or 'NOT SET'}", file=sys.stderr)
print(f"API_HASH: {api_hash[:10] + '...' if api_hash else 'NOT SET'}", file=sys.stderr)
print(f"STRING_SESSION: {len(string_session) if string_session else 0} chars", file=sys.stderr)
print(f"DASHSCOPE: {dashscope_key[:15] + '...' if dashscope_key else 'NOT SET'}", file=sys.stderr)

if not all([api_id, api_hash, string_session, dashscope_key]):
    print("❌ ОШИБКА: Не все переменные окружения установлены!", file=sys.stderr)
    sys.exit(1)

print("✅ Все переменные на месте!", file=sys.stderr)

app = Client(
    "my_userbot",
    api_id=int(os.getenv("API_ID")),
    api_hash=os.getenv("API_HASH"),
    session_string=os.getenv("STRING_SESSION")
)

print("🔌 Подключение к Telegram...", file=sys.stderr)

try:
    ai_client = OpenAI(
        base_url="https://ws-y7znpxq9v24qsaeo.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1",
        api_key=os.getenv("DASHSCOPE_API_KEY")
    )
    print("✅ OpenAI клиент создан!", file=sys.stderr)
except Exception as e:
    print(f"❌ Ошибка создания OpenAI клиента: {e}", file=sys.stderr)
    sys.exit(1)

# === НАСТРОЙКИ ===
SAVED_MESSAGES_ID = 777000  # ID Saved Messages в Telegram
WAIT_AFTER_OWNER_ACTIVE = 300  # 5 минут — ждать после активности хозяина
ANTI_SPAM_DELAY = 60  # Не отвечать одному человеку чаще раза в минуту
MOSCOW_TZ = timezone(timedelta(hours=3))

# === СОСТОЯНИЕ ===
is_away = False
current_status = "занят"
last_owner_activity = 0  # Время последней активности хозяина
NIGHT_START_HOUR = 23
NIGHT_END_HOUR = 7

# === ИИ-СУДЬЯ ===
user_mutes = {}  # {user_id: timestamp_окончания_мута}
last_siren_response = {}  # {user_id: timestamp}

def is_muted(user_id: int) -> bool:
    if user_id not in user_mutes:
        return False
    if time.time() > user_mutes[user_id]:
        del user_mutes[user_id]
        return False
    return True

async def ai_judge(user_id: int, text: str, history: list) -> tuple[str, str]:
    user_msgs = [msg["content"] for msg in history if msg["role"] == "user"][-5:]
    
    try:
        response = ai_client.chat.completions.create(
            model="qwen-plus",
            messages=[{
                "role": "system",
                "content": f"""Ты — Сирена, ИИ-помощник. Оцени поведение пользователя.

Последние сообщения:
{user_msgs}

Новое сообщение: {text}

НОРМАЛЬНОЕ ПОВЕДЕНИЕ (action = ignore):
- Приветствия, обычные вопросы, просьбы о помощи, дружелюбное общение

НАРУШЕНИЯ:
- warn: лёгкий спам, навязчивость
- mute_temp: повторяющийся спам (10 мин)
- mute_long: агрессия, оскорбления (24 часа)
- mute_perm: угрозы, троллинг (навсегда)

Ответь СТРОГО JSON:
{{"action": "ignore", "message": ""}}"""
            }],
            max_tokens=100
        )
        result = response.choices[0].message.content.strip()
        decision = json.loads(result)
        return decision.get("action", "ignore"), decision.get("message", "")
    except Exception as e:
        print(f"Ошибка ИИ-судьи: {e}")
        return "ignore", ""

def apply_mute(user_id: int, action: str):
    now = time.time()
    durations = {
        "mute_temp": 600,
        "mute_long": 86400,
        "mute_perm": 31536000
    }
    if action in durations:
        user_mutes[user_id] = now + durations[action]

def is_night_time():
    now = datetime.now(MOSCOW_TZ)
    hour = now.hour
    if NIGHT_START_HOUR > NIGHT_END_HOUR:
        return hour >= NIGHT_START_HOUR or hour < NIGHT_END_HOUR
    else:
        return NIGHT_START_HOUR <= hour < NIGHT_END_HOUR

# === ОТСЛЕЖИВАНИЕ АКТИВНОСТИ ХОЗЯИНА ===
@app.on_message(filters.me & ~filters.service)
async def track_owner_activity(client, message):
    global last_owner_activity
    last_owner_activity = time.time()

# === КОМАНДЫ ТОЛЬКО В ИЗБРАННОМ ===
@app.on_message(filters.me & filters.command(["away", "back", "help", "night", "status", "unmute", "mutes"]))
async def commands_handler(client, message):
    global is_away, current_status, NIGHT_START_HOUR, NIGHT_END_HOUR
    print(f"📍 Команда от: chat_id={message.chat.id}, chat_type={message.chat.type}", file=sys.stderr)
    # Проверяем что это Saved Messages
    if message.chat.id != SAVED_MESSAGES_ID:
        return
    
    cmd = message.command[0]
    
    if cmd == "away":
        is_away = True
        if len(message.command) > 1:
            current_status = " ".join(message.command[1:])
        else:
            current_status = "занят"
        await message.edit_text(f"✅ Автоответчик ВКЛ\nСтатус: {current_status}\nНочь: {NIGHT_START_HOUR}:00 - {NIGHT_END_HOUR}:00")
    
    elif cmd == "back":
        is_away = False
        current_status = "занят"
        await message.edit_text("❌ Автоответчик ВЫКЛ")
    
    elif cmd == "help":
        help_text = (
            "--- КОМАНДЫ СИРЕНЫ ---\n\n"
            "/away [статус] — включить автоответчик\n"
            "  Пример: /away на встрече\n"
            "  Пример: /away сплю\n\n"
            "/back — выключить автоответчик\n\n"
            "/help — этот список\n\n"
            "/night [старт] [конец] — часы ночи\n"
            "  Пример: /night 23 7\n"
            "  Сейчас: " + str(NIGHT_START_HOUR) + ":00 - " + str(NIGHT_END_HOUR) + ":00\n\n"
            "/status — текущий статус\n\n"
            "/unmute [user_id] — размутить\n"
            "/mutes — список замьюченных\n\n"
            "--- ЛОГИКА ---\n\n"
            "1. Команды работают ТОЛЬКО в избранном\n"
            "2. Если ты онлайн — Сирена молчит\n"
            "3. После твоего выхода ждёт 5 минут\n"
            "4. Антиспам: не отвечает чаще 1 раза в минуту\n"
            "5. ИИ-судья мутит спамеров и агрессоров\n"
            "6. Ночью ИИ говорит что хозяин спит\n"
            "7. Отвечает только в ЛС, группы игнорирует"
        )
        await message.edit_text(help_text)
    
    elif cmd == "status":
        now = datetime.now(MOSCOW_TZ)
        time_since_active = int(time.time() - last_owner_activity) if last_owner_activity > 0 else 999999
        owner_active = time_since_active < WAIT_AFTER_OWNER_ACTIVE
        
        status_text = (
            f"--- СТАТУС СИРЕНЫ ---\n\n"
            f"Автоответчик: {'ВКЛ' if is_away else 'ВЫКЛ'}\n"
            f"Статус хозяина: {current_status}\n"
            f"Время (МСК): {now.strftime('%H:%M')}\n"
            f"Ночной режим: {'АКТИВЕН' if is_night_time() else 'нет'}\n"
            f"Часы ночи: {NIGHT_START_HOUR}:00 - {NIGHT_END_HOUR}:00\n"
            f"Хозяин активен: {'ДА' if owner_active else 'нет'}\n"
            f"Последняя активность: {time_since_active} сек назад\n"
            f"Замьючено: {len(user_mutes)} чел."
        )
        await message.edit_text(status_text)
    
    elif cmd == "night":
        if len(message.command) >= 3:
            try:
                NIGHT_START_HOUR = int(message.command[1])
                NIGHT_END_HOUR = int(message.command[2])
                await message.edit_text(f"✅ Ночные часы: {NIGHT_START_HOUR}:00 - {NIGHT_END_HOUR}:00")
            except ValueError:
                await message.edit_text("❌ Используй числа. Пример: /night 23 7")
        else:
            await message.edit_text(f"Текущие часы: {NIGHT_START_HOUR}:00 - {NIGHT_END_HOUR}:00\nИспользуй: /night 23 7")
    
    elif cmd == "unmute":
        if len(message.command) > 1:
            try:
                user_id = int(message.command[1])
                if user_id in user_mutes:
                    del user_mutes[user_id]
                    await message.edit_text(f"✅ Пользователь {user_id} разблокирован")
                else:
                    await message.edit_text("❌ Пользователь не в муте")
            except:
                await message.edit_text("❌ Неверный ID")
        else:
            await message.edit_text("Используй: /unmute [user_id]")
    
    elif cmd == "mutes":
        if user_mutes:
            text = "📊 Замьюченные:\n"
            for uid, expiry in user_mutes.items():
                mins_left = int((expiry - time.time()) / 60)
                text += f"• ID {uid}: ещё {mins_left} мин\n"
            await message.edit_text(text)
        else:
            await message.edit_text("✅ Никто не замьючен")

# === АВТООТВЕТЧИК ===
@app.on_message(filters.private & ~filters.me & ~filters.bot)
async def auto_responder(client, message):
    global is_away, current_status
    
    # 1. Проверка: автоответчик включён?
    if not is_away:
        return
    
    # 2. Проверка: хозяин онлайн или был активен менее 5 минут назад
    if last_owner_activity > 0 and (time.time() - last_owner_activity) < WAIT_AFTER_OWNER_ACTIVE:
        return
    
    # 3. Проверка: есть ли текст
    if not message.text:
        return
    
    user_id = message.from_user.id
    text = message.text.strip()
    
    if len(text) < 3:
        return
    
    # 4. Проверка: мут
    if is_muted(user_id):
        return
    
    # 5. Проверка: антиспам (не отвечать чаще раза в минуту)
    now = time.time()
    last_reply = last_siren_response.get(user_id, 0)
    if now - last_reply < ANTI_SPAM_DELAY:
        return
    
    # Получаем историю
    history = []
    try:
        async for msg in client.get_chat_history(message.chat.id, limit=10):
            if msg.text:
                role = "assistant" if msg.outgoing else "user"
                history.insert(0, {"role": role, "content": msg.text})
    except Exception as e:
        print(f"Ошибка истории: {e}")
    
    # ИИ-судья
    action, ai_message = await ai_judge(user_id, text, history)
    if action == "warn":
        await message.reply_text(ai_message)
        return
    elif action in ["mute_temp", "mute_long", "mute_perm"]:
        apply_mute(user_id, action)
        await message.reply_text(ai_message if ai_message else "Ты замьючен.")
        return
    
    await asyncio.sleep(random.uniform(4, 12))
    
    # Определяем промпт в зависимости от времени и контекста
    time_diff = now - last_reply if last_reply > 0 else 9999
    
    if is_night_time():
        # НОЧНОЙ РЕЖИМ — хозяин спит
        system_prompt = f"""Ты — Сирена, ИИ-помощница моего хозяина.

СЕЙЧАС НОЧЬ. Хозяин СПИТ.

ПРАВИЛА:
1. ОБЯЗАТЕЛЬНО представься: "Привет, я Сирена" (только в первом сообщении)
2. Скажи что хозяин сейчас спит
3. Скажи что он ответит утром / когда проснётся
4. Если собеседник задал вопрос — скажи что передашь хозяину утром
5. Если собеседник прощается — попрощайся кратко
6. НЕ используй эмодзи
7. Отвечай кратко (1-3 предложения)
8. НИКОГДА не называй имя хозяина
9. Говори от СВОЕГО имени, не от имени хозяина

История переписки:
{history[:8] if history else "Это первое сообщение"}

Новое сообщение: {text}"""
    
    elif last_reply == 0:
        # ПЕРВОЕ СООБЩЕНИЕ (день)
        system_prompt = f"""Ты — Сирена, ИИ-помощница моего хозяина.

Это ПЕРВОЕ сообщение от этого человека. Хозяин сейчас {current_status}.

ПРАВИЛА:
1. ОБЯЗАТЕЛЬНО начни с: "Привет, я Сирена. Мой хозяин сейчас {current_status} и не может ответить лично."
2. Можешь добавить короткое предложение помощи
3. НЕ используй эмодзи
4. Отвечай кратко
5. НИКОГДА не называй имя хозяина
6. Говори от СВОЕГО имени

Новое сообщение: {text}"""
    
    elif time_diff < 300:
        # ДИАЛОГ ИДЁТ (менее 5 минут с последнего ответа)
        system_prompt = f"""Ты — Сирена. Диалог УЖЕ идёт.

Хозяин {current_status}.

ПРАВИЛА:
1. НЕ повторяй приветствие
2. Отвечай естественно по контексту
3. Если благодарность — ответь кратко "Пожалуйста!" или "Всегда рада помочь!"
4. Если прощание — попрощайся кратко
5. Если вопрос — ответь по делу
6. НЕ называй имя хозяина
7. НЕ используй эмодзи
8. Отвечай кратко (1-2 предложения)

История:
{history[:8]}

Новое сообщение: {text}"""
    
    else:
        # ПРОШЛО БОЛЬШЕ 5 МИНУТ
        system_prompt = f"""Ты — Сирена. Прошло больше 5 минут с твоего последнего ответа.

Хозяин всё ещё {current_status}.

ПРАВИЛА:
1. Можешь кратко напомнить что хозяин занят (но НЕ обязательно)
2. Отвечай по контексту
3. Если новое сообщение — можешь ответить как на первое, но без полного представления
4. НЕ называй имя хозяина
5. НЕ используй эмодзи
6. Отвечай кратко

История:
{history[:8]}

Новое сообщение: {text}"""
    
    try:
        response = ai_client.chat.completions.create(
            model="qwen-plus",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text}
            ],
            max_tokens=500
        )
        answer = response.choices[0].message.content
        await message.reply_text(answer)
        
        last_siren_response[user_id] = time.time()
        
    except Exception as e:
        print(f"Ошибка API: {e}")
        await message.reply_text(f"Привет, это Сирена. Мой хозяин сейчас {current_status}, ответит позже.")
        last_siren_response[user_id] = time.time()

# === ЗАПУСК ===
if __name__ == "__main__":
    print("🚀 Сирена запущена!", flush=True)
    print("📱 Команды работают ТОЛЬКО в избранном", flush=True)
    print(f"⏰ После твоей активности ждёт {WAIT_AFTER_OWNER_ACTIVE} сек", flush=True)
    print(f"🌙 Ночь: {NIGHT_START_HOUR}:00 - {NIGHT_END_HOUR}:00", flush=True)
    print(f"🛡️ Антиспам: {ANTI_SPAM_DELAY} сек между ответами одному человеку", flush=True)
    app.run()