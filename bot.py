import os
import asyncio
import random
import json
import time
import sys
from pyrogram import Client, filters
from openai import OpenAI
from dotenv import load_dotenv
from datetime import datetime, timezone, timedelta
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

is_away = False
current_status = "занят"
NIGHT_START_HOUR = 23  # Начало ночи
NIGHT_END_HOUR = 7     # Конец ночи
MOSCOW_TZ = timezone(timedelta(hours=3))  # МСК
# === ИИ-СУДЬЯ ===
user_mutes = {}  # {user_id: timestamp_окончания_мута}
# Время последнего ответа Сирены каждому пользователю
last_siren_response = {}  # {user_id: timestamp}
def is_muted(user_id: int) -> bool:
    """Проверяет, замьючен ли пользователь"""
    if user_id not in user_mutes:
        return False
    if time.time() > user_mutes[user_id]:
        del user_mutes[user_id]
        return False
    return True

async def ai_judge(user_id: int, text: str, history: list) -> tuple[str, str]:
    """ИИ решает что делать с пользователем"""
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
    """Применяет мут"""
    now = time.time()
    durations = {
        "mute_temp": 600,
        "mute_long": 86400,
        "mute_perm": 31536000
    }
    if action in durations:
        user_mutes[user_id] = now + durations[action]

# === КОМАНДЫ ===
@app.on_message(filters.command(["away", "back"], prefixes="/") & filters.me)
async def control_bot(client, message):
    global is_away, current_status
    cmd = message.command[0]
    if cmd == "away":
        is_away = True
        if len(message.command) > 1:
            current_status = " ".join(message.command[1:])
        await message.reply_text(f"✅ Режим включен. Статус: {current_status}")
    else:
        is_away = False
        await message.reply_text("❌ Режим выключен. Я снова в сети.")
@app.on_message(filters.command(["night"], prefixes="/") & filters.me)
async def set_night_hours(client, message):
    global NIGHT_START_HOUR, NIGHT_END_HOUR
    
    if len(message.command) >= 3:
        try:
            NIGHT_START_HOUR = int(message.command[1])
            NIGHT_END_HOUR = int(message.command[2])
            await message.reply_text(f"✅ Ночные часы: {NIGHT_START_HOUR}:00 - {NIGHT_END_HOUR}:00")
        except ValueError:
            await message.reply_text("❌ Используй числа. Пример: /night 23 7")
    else:
        await message.reply_text(f"Текущие часы: {NIGHT_START_HOUR}:00 - {NIGHT_END_HOUR}:00\nИспользуй: /night 23 7")
# === АВТООТВЕТЧИК ===
def is_night_time():
    """Проверяет текущее время - ночь ли сейчас"""
    now = datetime.now(MOSCOW_TZ)
    hour = now.hour

    if NIGHT_START_HOUR > NIGHT_END_HOUR:
        return hour >= NIGHT_START_HOUR or hour < NIGHT_END_HOUR
    else:
        return NIGHT_START_HOUR <= hour < NIGHT_END_HOUR

@app.on_message(filters.private & ~filters.me & ~filters.bot)
async def auto_responder(client, message):
    global is_away, current_status
    
    if not is_away or not message.text:
        return
    
    user_id = message.from_user.id
    text = message.text.strip()
    
    if len(text) < 3 or is_muted(user_id):
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
    
    # Определяем какой промпт использовать
    now = time.time()
    last_time = last_siren_response.get(user_id, 0)
    time_diff = now - last_time

    # ПРОВЕРКА: НОЧНОЙ РЕЖИМ
    if is_night_time():
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

    elif last_time == 0:
        # ПРОМПТ 1: Первое сообщение (день)
        system_prompt = f"""Ты — Сирена, ИИ-помощник. Это ПЕРВОЕ сообщение от этого человека.

Твой хозяин сейчас {current_status}.

ОБЯЗАТЕЛЬНО начни с: "Привет, это Сирена. Мой хозяин сейчас {current_status} и не может ответить лично."
После этого можешь добавить короткое предложение помощи.
НЕ используй эмодзи. Отвечай кратко.
ВАЖНО: НИКОГДА не называй имя хозяина. Если спросят — отвечай "Я не раскрываю личные данные"."""

    elif time_diff < 300:  # 5 минут = 300 секунд
        # ПРОМПТ 2: Диалог идёт, поддерживаем и можем завершить
        system_prompt = f"""Ты — Сирена. Диалог УЖЕ идёт (ты отвечала менее 5 минут назад).

Твой хозяин {current_status}.

История переписки:
{history[:8]}

Новое сообщение: {text}

ПРАВИЛА:
1. НЕ повторяй приветствие
2. Отвечай естественно по контексту
3. Если благодарность ("спасибо", "благодарю") → ответь кратко "Пожалуйста!" или "Всегда рада помочь!"
4. Если прощание ("пока", "до связи") → попрощайся кратко
5. Если вопрос → ответь по делу
6. Если собеседник явно хочет закончить разговор — помоги ему завершить естественно
7. НЕ называй имя хозяина
8. НЕ используй эмодзи
9. Отвечай кратко (1-2 предложения)"""

    else:
        # ПРОМПТ 3: Прошло больше 5 минут, напоминаем о статусе
        system_prompt = f"""Ты — Сирена. Прошло больше 5 минут с твоего последнего ответа.

Твой хозяин всё ещё {current_status}.

История переписки:
{history[:8]}

Новое сообщение: {text}

ПРАВИЛА:
1. Можешь кратко напомнить что хозяин всё ещё занят (но НЕ обязательно каждый раз)
2. Отвечай по контексту последнего сообщения
3. Если это продолжение старого разговора — поддержи его
4. Если новое сообщение — можешь ответить как на первое, но без полного представления
5. НЕ называй имя хозяина
6. НЕ используй эмодзи
7. Отвечай кратко"""

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

        # Запоминаем время ответа
        last_siren_response[user_id] = time.time()

    except Exception as e:
        print(f"Ошибка API: {e}")
        await message.reply_text(f"Привет, это Сирена. Мой хозяин сейчас {current_status}, ответит позже.")
        last_siren_response[user_id] = time.time()

# === КОМАНДЫ УПРАВЛЕНИЯ ===
@app.on_message(filters.command(["unmute", "mutes"], prefixes="/") & filters.me)
async def admin_commands(client, message):
    cmd = message.command[0]
    
    if cmd == "unmute":
        if len(message.command) > 1:
            try:
                user_id = int(message.command[1])
                if user_id in user_mutes:
                    del user_mutes[user_id]
                    await message.reply_text(f"✅ Пользователь {user_id} разблокирован")
                else:
                    await message.reply_text("❌ Пользователь не в муте")
            except:
                await message.reply_text("❌ Неверный ID")
        else:
            await message.reply_text("Используй: /unmute [user_id]")
    
    elif cmd == "mutes":
        if user_mutes:
            text = "📊 Замьюченные пользователи:\n"
            for uid, expiry in user_mutes.items():
                mins_left = int((expiry - time.time()) / 60)
                text += f"• ID {uid}: ещё {mins_left} мин\n"
            await message.reply_text(text)
        else:
            await message.reply_text("✅ Никто не замьючен")

# === ЗАПУСК ===
if __name__ == "__main__":
    print("🚀 Сирена запущена с ИИ-судьёй!", flush=True)
    print("📱 Userbot активен и ждёт сообщений...", flush=True)
    app.run()