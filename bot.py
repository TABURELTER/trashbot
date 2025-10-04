import json, os, datetime, requests, subprocess

TOKEN = os.getenv("TELEGRAM_TOKEN")
API = f"https://api.telegram.org/bot{TOKEN}"
MODE = os.getenv("MODE", "normal")  # "normal", "register", "test"

def load_json(name, default):
    try:
        with open(name, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default

def save_json(name, data):
    with open(name, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def send_message(chat_id, text, reply_markup=None):
    requests.post(f"{API}/sendMessage", json={
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
        "reply_markup": reply_markup
    })

def get_updates():
    return requests.get(f"{API}/getUpdates").json().get("result", [])

# === Загрузка данных ===
people_data = load_json("people.json", {"start_date": str(datetime.date.today()), "people": []})
state = load_json("state.json", {"done_date": None})
history = load_json("history.json", [])

# === РЕЖИМ РЕГИСТРАЦИИ ===
if MODE == "register":
    updates = get_updates()
    added = []
    for u in updates:
        msg = u.get("message")
        if not msg: continue
        text = msg.get("text", "").lower().strip()
        if text == "мусор мой":
            chat_id = msg["chat"]["id"]
            name = msg["from"].get("first_name", "Без имени")
            username = msg["from"].get("username", "")
            if not any(p["chat_id"] == chat_id for p in people_data["people"]):
                people_data["people"].append({
                    "name": name,
                    "tg": f"@{username}" if username else name,
                    "chat_id": chat_id
                })
                added.append(name)
                send_message(chat_id, "✅ Ты успешно добавлен в очередь мусора!")

    if added:
        save_json("people.json", people_data)
        # === Автопуш в репозиторий ===
        subprocess.run(["git", "config", "--global", "user.email", "action@github.com"])
        subprocess.run(["git", "config", "--global", "user.name", "GitHub Action"])
        subprocess.run(["git", "add", "people.json"])
        subprocess.run(["git", "commit", "-m", f"Добавлены новые пользователи: {', '.join(added)}"])
        subprocess.run(["git", "push", "origin", "main"])
        print(f"Добавлены пользователи: {', '.join(added)}")
    else:
        print("Новых регистраций нет.")
    exit(0)

# === НОРМАЛЬНЫЙ РЕЖИМ ===
people = people_data.get("people", [])
if not people:
    print("Нет зарегистрированных пользователей.")
    exit(0)

today = datetime.date.today()
start_date = datetime.date.fromisoformat(people_data["start_date"])
days_passed = (today - start_date).days
index_today = days_passed % len(people)

today_p = people[index_today]
yesterday_p = people[(index_today - 1) % len(people)]
tomorrow_p = people[(index_today + 1) % len(people)]

keyboard = {"inline_keyboard": [[{"text": "🗑 Выкинул", "callback_data": "done"}]]}

# === Проверка callback кнопок ===
updates = get_updates()
for u in updates:
    if "callback_query" in u:
        cb = u["callback_query"]
        if cb["data"] == "done":
            state["done_date"] = str(today)
            save_json("state.json", state)
            history.append({
                "date": str(today),
                "person": today_p["name"],
                "time": datetime.datetime.now().strftime("%H:%M")
            })
            save_json("history.json", history)
            requests.post(f"{API}/answerCallbackQuery", json={
                "callback_query_id": cb["id"],
                "text": "Принято 👍"
            })
            send_message(cb["from"]["id"], "Спасибо! Сегодня больше не напомню 🧹")

# === Сброс статуса ночью ===
hour = datetime.datetime.now().hour
if hour == 3:
    state["done_date"] = None
    save_json("state.json", state)

# === Текст рассылки ===
info_text = (
    f"🗓 Сегодня {today.strftime('%A, %d %B %Y')}\n\n"
    f"🧹 Сегодня выносит: {today_p['name']}\n"
    f"📅 Вчера: {yesterday_p['name']}\n"
    f"📆 Завтра: {tomorrow_p['name']}\n"
)

# === Уведомления (08:00 и 20:00) ===
already_done = state.get("done_date") == str(today)
if not already_done and hour in [8, 20]:
    send_message(today_p["chat_id"], f"Привет, {today_p['name']}! Сегодня твоя очередь выносить мусор 🗑", reply_markup=keyboard)

# === Информационное сообщение всем ===
for p in people:
    send_message(p["chat_id"], info_text)

# === Тестовый запуск ===
if os.getenv("TEST_RUN") == "1":
    for p in people:
        send_message(p["chat_id"], "🔧 Тестовый запуск TrashBot прошёл успешно!")
