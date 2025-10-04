import json, os, datetime, requests
try:
    from zoneinfo import ZoneInfo  # py3.9+
except Exception:
    ZoneInfo = None


# --- Настройки ---
TOKEN = os.getenv("TELEGRAM_TOKEN")  # токен берём ТОЛЬКО из секретов окружения
API = f"https://api.telegram.org/bot{TOKEN}" if TOKEN else None

MODE = os.getenv("MODE", "test")  # register / normal / test / debug

STATE_FILE = "state.json"
PEOPLE_FILE = "people.json"
HISTORY_FILE = "history.json"

# Таймзона и расписание
BOT_TZ = os.getenv("BOT_TZ", "Europe/Moscow")
FIRE_TIMES = os.getenv("FIRE_TIMES", "10:00,20:00,23:00")  # локальное время
WINDOW_MIN = int(os.getenv("WINDOW_MIN", "15"))  # допуск раннего/позднего запуска (минуты)

# --- Хелперы работы с файлами ---
def load_json(name, default):
    try:
        with open(name, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default

def save_json(name, data):
    with open(name, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# --- Telegram API ---
def _tg_call(method, payload):
    if not TOKEN:
        # оффлайн/локально — просто логируем
        print(f"[TG/{method}] {json.dumps(payload, ensure_ascii=False)}")
        # эмулируем ответ с message_id
        if method in ("sendMessage",):
            return {"message_id": 1}
        return None
    url = f"{API}/{method}"
    resp = requests.post(url, json=payload, timeout=30)
    try:
        data = resp.json()
    except Exception:
        raise RuntimeError(f"Telegram API error: status={resp.status_code} body={resp.text[:200]}")
    if not data.get("ok"):
        raise RuntimeError(f"Telegram API error: {data}")
    return data.get("result")

def send_message(chat_id, text, disable_notification=False):
    return _tg_call("sendMessage", {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_notification": disable_notification
    })

def edit_message(chat_id, message_id, text):
    try:
        return _tg_call("editMessageText", {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": "HTML",
        })
    except Exception as e:
        print(f"[WARN] edit failed: {e}")
        return None

def delete_message(chat_id, message_id):
    try:
        return _tg_call("deleteMessage", {"chat_id": chat_id, "message_id": message_id})
    except Exception as e:
        print(f"[WARN] delete failed: {e}")
        return None

# --- Данные ---
people_data = load_json(PEOPLE_FILE, {"start_date": str(datetime.date.today()), "people": []})
people = people_data.get("people", [])
state = load_json(STATE_FILE, {"last_day": None})
history = load_json(HISTORY_FILE, [])

def now_local():
    if ZoneInfo:
        try:
            return datetime.datetime.now(ZoneInfo(BOT_TZ))
        except Exception:
            pass
    return datetime.datetime.utcnow() + datetime.timedelta(hours=3)

def today_local_date():
    return now_local().date()

today_date = today_local_date()
today = today_date.isoformat()

def parse_fire_times(times: str):
    out = []
    for t in times.split(","):
        t = t.strip()
        if not t: continue
        hh, mm = t.split(":")
        out.append((int(hh), int(mm), f"{int(hh):02d}:{int(mm):02d}"))
    return out

FIRE_SLOTS = parse_fire_times(FIRE_TIMES)

def current_window_tag(now_dt: datetime.datetime):
    for hh, mm, tag in FIRE_SLOTS:
        target = now_dt.replace(hour=hh, minute=mm, second=0, microsecond=0)
        delta = abs((now_dt - target).total_seconds()) / 60.0
        if delta <= WINDOW_MIN:
            return tag
    return None

def rotation_index(start_date_iso, people_len, on_date):
    if people_len == 0:
        return None
    start = datetime.date.fromisoformat(start_date_iso)
    return (on_date - start).days % people_len

def build_summary(people, idx):
    if not people:
        return "Сводка: нет участников. Напишите боту: мусор мой"
    n = len(people)
    i_today = idx
    i_yest = (idx - 1) % n
    i_tom = (idx + 1) % n
    def fmt(p):
        tg = p.get("tg") or ""
        return f"{p.get('name','?')} {tg}".strip()
    return (
        f"🗓 Дата: {today}\n"
        f"🧹 Сегодня выносит: {fmt(people[i_today])}\n"
        f"📅 Вчера: {fmt(people[i_yest])}\n"
        f"📆 Завтра: {fmt(people[i_tom])}"
    )

def reset_new_day(recipients):
    # Пер-пользовательский сброс: безопасно при смешанном использовании режимов (test→normal)
    for u in recipients:
        key = str(u["chat_id"])
        s = state.get(key, {})
        if s.get("last_day") == today:
            state[key] = s
            continue
        pmid = s.get("ping_message_id")
        if pmid:
            delete_message(u["chat_id"], pmid)
        s["ping_message_id"] = None
        s["ping_count"] = 0
        s["fired_windows"] = []
        s["info_last_day"] = None
        s["last_day"] = today
        state[key] = s

def ensure_info(recipient_chat_id, text):
    key = str(recipient_chat_id)
    s = state.get(key, {})
    mid = s.get("info_message_id")
    if mid:
        if not edit_message(recipient_chat_id, mid, text):
            msg = send_message(recipient_chat_id, text, disable_notification=True)
            if msg and "message_id" in msg:
                s["info_message_id"] = msg["message_id"]
    else:
        msg = send_message(recipient_chat_id, text, disable_notification=True)
        if msg and "message_id" in msg:
            s["info_message_id"] = msg["message_id"]
    state[key] = s

def send_or_replace_ping(recipient_chat_id, text):
    key = str(recipient_chat_id)
    s = state.get(key, {})
    pmid = s.get("ping_message_id")
    if pmid:
        delete_message(recipient_chat_id, pmid)
    msg = send_message(recipient_chat_id, text)
    if msg and "message_id" in msg:
        s["ping_message_id"] = msg["message_id"]
    s["ping_count"] = int(s.get("ping_count", 0)) + 1
    state[key] = s

def can_fire_window(recipient_chat_id, tag):
    key = str(recipient_chat_id)
    s = state.get(key, {})
    fired = set(s.get("fired_windows") or [])
    return tag not in fired

def mark_fired_window(recipient_chat_id, tag):
    key = str(recipient_chat_id)
    s = state.get(key, {})
    fired = set(s.get("fired_windows") or [])
    fired.add(tag)
    s["fired_windows"] = sorted(list(fired))
    state[key] = s

# --- Режимы ---

def run_register_mode():
    print("=== Register mode ===")
    print("Пока не реализован long-poll getUpdates. Используйте ручное добавление в people.json.")


def run_normal_mode():
    print("=== Normal mode ===")
    if not people:
        print("Нет участников.")
        return
    idx = rotation_index(people_data["start_date"], len(people), today_date)
    summary = build_summary(people, idx)
    user_today = people[idx]

    recipients = people  # в нормальном режиме отправляем всем
    reset_new_day(recipients)
    # Инфо каждому (редактируемое)
    for u in recipients:
        ensure_info(u["chat_id"], summary)
    # Пинг ответственному: только в окне расписания
    tag = current_window_tag(now_local())
    if tag and can_fire_window(user_today["chat_id"], tag):
        ping_text = f"Напоминание: сегодня ваша очередь вынести мусор."
        send_or_replace_ping(user_today["chat_id"], ping_text)
        mark_fired_window(user_today["chat_id"], tag)


def run_test_mode():
    print("=== Test mode ===")
    testers = [p for p in people if p.get("tester")]
    if not testers:
        print("Нет тестеров (people[*].tester = true)")
        return
    if not people:
        print("Нет участников.")
        return
    idx = rotation_index(people_data["start_date"], len(people), today_date)
    summary = build_summary(people, idx)
    user_today = people[idx]

    recipients = testers  # в тестовом режиме отправляем только тестерам
    reset_new_day(recipients)

    # Инфо: одно редактируемое сообщение КАЖДОМУ тестеру с пометкой кому бы пошло
    info_text = (
        "[TEST] Это тестовая сводка. В бою отправляется каждому участнику.\n\n" + summary
    )
    for t in testers:
        ensure_info(t["chat_id"], info_text)

    # Пинг: одно сообщение КАЖДОМУ тестеру с явной пометкой адресата
    intended = f"{user_today.get('name')} {user_today.get('tg','')} (chat_id={user_today.get('chat_id')})"
    ping_text = (
        f"[TEST] Имитация пинга для: {intended}\n"
        f"Напоминание: сегодня очередь вынести мусор."
    )
    tag = current_window_tag(now_local())
    if tag:
        for t in testers:
            if can_fire_window(t["chat_id"], tag):
                send_or_replace_ping(t["chat_id"], ping_text)
                mark_fired_window(t["chat_id"], tag)


def run_debug_mode():
    print("=== Debug mode (admin panel) ===")
    print(json.dumps(state, ensure_ascii=False, indent=2))


if MODE == "register":
    run_register_mode()
elif MODE == "normal":
    run_normal_mode()
elif MODE == "test":
    run_test_mode()
elif MODE == "debug":
    run_debug_mode()
else:
    print("❌ Unknown MODE")

save_json(STATE_FILE, state)
save_json(HISTORY_FILE, history)
print("✅ Скрипт завершён.")
