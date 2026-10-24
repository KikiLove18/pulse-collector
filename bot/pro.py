"""Закрытый канал «Пульс рынка PRO»: «Большие события в моменте» (обещано в закрепе канала).

В боте человек получает короткое «⚡ Срочно» или итог данных; в канал PRO — разбор глубже: что случилось, почему важно,
как отреагировал рынок, что дальше и на что смотреть. Повод — срочная новость (решение ЦБ/ФРС, обвал 5%+) или
вышедшие данные с сильным влиянием (CPI, NFP, решение ФРС…). Не больше PRO_PER_DAY в сутки.

Куда: переменная PRO_EVENTS — "on" → канал PRO (PRO_CHAT_ID, только после «да» владельца), "off" → никуда,
иначе (по умолчанию) — образцы в тестовый канал (TELEGRAM_CHAT_ID).
"""
import json
import os
import sys
import urllib.request
from datetime import timedelta, timezone

from bot import prompts

MSK = timezone(timedelta(hours=3))
PRO_PER_DAY = 3
SILENT = {"on": False}  # True — все отправки без звука (наполнение канала задним числом)
VERDICT_RU = {"better": "лучше ожиданий", "worse": "хуже ожиданий", "neutral": "в рамках ожиданий", "inline": "в рамках ожиданий"}

BIG_EVENT = """Ты — главный редактор закрытого канала «Пульс рынка PRO» для трейдеров и частных инвесторов (Россия, США, крипта, сырьё).
Только что случилось крупное событие. Подписчики уже получили в боте короткое сообщение — здесь они платят за разбор:
почему это важно, как связано с другими событиями и чего ждать дальше.

Тебе дают: что произошло (заголовок, факты, источник) и котировки на сейчас.
Формат — Telegram HTML (только <b>, <i>, <u>, <a href>, <blockquote>, <blockquote expandable>), без Markdown, без заголовка.
Структура, между разделами пустая строка:

<b>Что произошло</b>
2–3 предложения с цифрами из входных данных.

<b>Почему это важно</b>
2–3 предложения: что это меняет для ставок, инфляции, доллара, нефти, рубля — по смыслу события. Главный вывод — в <u></u>.

<b>Реакция рынка</b>
1–2 предложения по котировкам из входных данных (что уже двинулось). Нет нужных котировок — пропусти раздел.

<b>Что дальше</b>
<blockquote expandable>2–4 предложения: ближайшие сигналы и события, которые подтвердят или сломают картину, на какие активы смотреть.</blockquote>

Правила: по-русски, просто, сложные термины — в скобках 2–4 словами. Цифры только из входных данных, ничего не додумывай.
Никаких советов «покупай/продавай», никаких обещаний доходности, никаких ценовых уровней и целей в сценариях. Объём — до 1500 знаков.""" + prompts.RU_ONLY


def _target(var="PRO_EVENTS"):
    """var: PRO_EVENTS (большие события) или PRO_POSTS (плановые рубрики) — у каждого своё «да» владельца."""
    mode = (os.environ.get(var) or "test").lower()
    if mode == "off":
        return None
    if mode == "on" and os.environ.get("PRO_CHAT_ID"):
        return os.environ["PRO_CHAT_ID"]
    return os.environ.get("TELEGRAM_CHAT_ID")


def _send(chat, html, card_url, silent=False, thread=None):
    """Возвращает message_id (или None). silent — без звука у подписчиков (наполнение канала пачкой)."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not (token and chat):
        print("----- канал PRO (не отправлено: нет токена) -----\n" + html, file=sys.stderr)
        return None
    payload = {"chat_id": chat, "text": html, "parse_mode": "HTML", "disable_notification": silent or SILENT["on"]}
    if str(chat) == str(os.environ.get("PRO_CHAT_ID") or ""):
        payload["protect_content"] = True  # материалы PRO — без пересылки и сохранения (Юрист 25.10, оферта разд. 13); тестовый канал — как раньше
    if thread:
        payload["message_thread_id"] = thread
    if card_url:
        payload["link_preview_options"] = {"url": card_url, "show_above_text": True, "prefer_large_media": True}
    else:
        payload["link_preview_options"] = {"is_disabled": True}
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            res = json.loads(r.read())
            print("Канал PRO:", res.get("ok"), "id", (res.get("result") or {}).get("message_id"), file=sys.stderr)
            return (res.get("result") or {}).get("message_id")
    except Exception as e:
        print("Канал PRO: не отправлено", e, file=sys.stderr)
    return None


# Группа с темами (решение владельца 20.10, a-pro-topics): если PRO_CHAT_ID — группа-форум, каждая рубрика идёт в свою тему.
# Темы бот создаёт сам при первом посте (нужно право «Управление темами»), номера хранит в state["pro_topics"][chat].
# Обычный канал (и тестовый канал) — без тем, как раньше.
TOPICS = {"event": ("⚡ События", 16478047), "weekly": ("📊 Разбор недели", 7322096), "week_ahead": ("📅 Неделя впереди", 9367192),
          "asset": ("🔍 Актив недели", 16766590), "guide": ("🧠 Гайды", 13338331), "month": ("📈 Итоги месяца", 16749490)}


def _call(method, **kw):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/{method}", data=json.dumps(kw).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())["result"]


def thread(state, chat, kind):
    """Номер темы для рубрики kind или None (не форум, нет токена, не удалось создать — тогда пост уйдёт в «Общее»)."""
    if not (chat and os.environ.get("TELEGRAM_BOT_TOKEN") and kind in TOPICS):
        return None
    info = state.setdefault("pro_topics", {}).setdefault(str(chat), {})
    try:
        if "forum" not in info:
            info["forum"] = bool(_call("getChat", chat_id=chat).get("is_forum"))
        if not info["forum"]:
            return None
        if kind not in info:
            name, color = TOPICS[kind]
            info[kind] = _call("createForumTopic", chat_id=chat, name=name, icon_color=color)["message_thread_id"]
            print(f"Канал PRO: создана тема «{name}» ({info[kind]})", file=sys.stderr)
        return info[kind]
    except Exception as e:
        print("Канал PRO: тема не найдена/не создана —", str(e)[:150], file=sys.stderr)
        return None


def edit(chat, spec, tg_html):
    """Исправить опубликованный пост (state/pro_edit.json): message_id или offset_from_pinned (от закрепа «Как устроен»).
    expect — с чего должен начинаться новый текст (защита от ошибки в номере)."""
    mid = spec.get("message_id")
    if not mid:
        pinned = _call("getChat", chat_id=chat).get("pinned_message") or {}
        if not (pinned.get("text") or "").startswith("📌 Как устроен"):
            print("Канал PRO: закреп не «Как устроен» — номер поста не посчитать, ничего не правлю", file=sys.stderr)
            return False
        mid = pinned["message_id"] + int(spec["offset_from_pinned"])
    html = tg_html(spec["html"])
    if spec.get("expect") and spec["expect"] not in html[:200]:
        print("Канал PRO: текст правки не похож на ожидаемый — не правлю", file=sys.stderr)
        return False
    payload = {"chat_id": chat, "message_id": mid, "text": html, "parse_mode": "HTML",
               "link_preview_options": ({"url": spec["card_url"], "show_above_text": True, "prefer_large_media": True}
                                        if spec.get("card_url") else {"is_disabled": True})}
    try:
        _call("editMessageText", **payload)
        print(f"Канал PRO: пост {mid} исправлен", file=sys.stderr)
        return True
    except Exception as e:
        print(f"Канал PRO: пост {mid} не исправлен —", str(e)[:200], file=sys.stderr)
        return False


def pin(chat, message_id):
    """Закрепить без уведомления (нужно право бота «закреплять сообщения»)."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/pinChatMessage",
                                 data=json.dumps({"chat_id": chat, "message_id": message_id, "disable_notification": True}).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            print("Канал PRO: закреплено", json.loads(r.read()).get("ok"), file=sys.stderr)
    except Exception as e:
        print("Канал PRO: не закреплено", e, file=sys.stderr)


def big_event(ask_ai, tg_html, state, now, kind, title, facts, quotes_text="", card_url=None, tstate=None):
    """kind: urgent | result. Возвращает html или None (лимит, выключено, ИИ отказал)."""
    chat = _target()
    if not chat:
        return None
    day = f"{now.astimezone(MSK):%Y-%m-%d}"
    st = state.setdefault("pro_events", {})
    if st.get("day") != day:
        st.clear()
        st.update(day=day, count=0, titles=[])
    if st["count"] >= PRO_PER_DAY or title in st["titles"]:
        print("Канал PRO: лимит на сегодня или уже было —", title, file=sys.stderr)
        return None
    user = f"СОБЫТИЕ: {title}\n{facts}\n\nКОТИРОВКИ СЕЙЧАС:\n{quotes_text or 'нет данных'}"
    try:
        body = ask_ai(BIG_EVENT, user)
    except Exception as e:
        print("Канал PRO: разбор не собран —", str(e)[:150], file=sys.stderr)
        return None
    head = ("⚡ <b>Разбор: " if kind == "urgent" else "📊 <b>Вышли данные: ") + title + "</b>"
    body = body.strip()
    if len(body) > 3600:  # ИИ разошёлся — режем по абзацу (раздел «Что дальше» в конце, его не жалко)
        body = body[:body.rfind("\n\n", 0, 3600)]
    html = tg_html(head + "\n\n" + body + "\n\n<i>Не инвестрекомендация.</i>\n\n#СОБЫТИЕ")
    print("----- канал PRO -----\n" + html, file=sys.stderr)
    _send(chat, html, card_url, thread=thread(tstate if tstate is not None else state, chat, "event"))
    st["count"] += 1
    st["titles"].append(title)
    return html


def check(chat):
    """Может ли бот публиковать в канале PRO — ничего не отправляет (getChat + getChatMember)."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    def call(method, **kw):
        req = urllib.request.Request(f"https://api.telegram.org/bot{token}/{method}", data=json.dumps(kw).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())["result"]
    me = call("getMe")
    ch = call("getChat", chat_id=chat)
    m = call("getChatMember", chat_id=chat, user_id=me["id"])
    if ch.get("type") == "channel":
        print(f"Канал PRO: «{ch.get('title')}» (канал), бот @{me.get('username')}: {m.get('status')}, "
              f"публиковать: {m.get('can_post_messages')}, править/закреплять: {m.get('can_edit_messages')}", file=sys.stderr)
        return m.get("status") in ("administrator", "creator") and m.get("can_post_messages") is not False
    perms = ch.get("permissions") or {}
    print(f"PRO-группа: «{ch.get('title')}» ({ch.get('type')}), темы включены: {ch.get('is_forum')}, "
          f"участники могут писать: {perms.get('can_send_messages')}; "
          f"бот @{me.get('username')}: {m.get('status')}, темы: {m.get('can_manage_topics')}, закреплять: {m.get('can_pin_messages')}",
          file=sys.stderr)
    return (m.get("status") == "administrator" and bool(ch.get("is_forum")) and bool(m.get("can_manage_topics"))
            and m.get("can_pin_messages") is not False)
