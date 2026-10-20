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


def _send(chat, html, card_url):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not (token and chat):
        print("----- канал PRO (не отправлено: нет токена) -----\n" + html, file=sys.stderr)
        return
    payload = {"chat_id": chat, "text": html, "parse_mode": "HTML"}
    if card_url:
        payload["link_preview_options"] = {"url": card_url, "show_above_text": True, "prefer_large_media": True}
    else:
        payload["link_preview_options"] = {"is_disabled": True}
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            print("Канал PRO:", json.loads(r.read()).get("ok"), file=sys.stderr)
    except Exception as e:
        print("Канал PRO: не отправлено", e, file=sys.stderr)


def big_event(ask_ai, tg_html, state, now, kind, title, facts, quotes_text="", card_url=None):
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
    _send(chat, html, card_url)
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
    print(f"Канал PRO: «{ch.get('title')}» ({ch.get('type')}), бот @{me.get('username')}: {m.get('status')}, "
          f"публиковать: {m.get('can_post_messages')}", file=sys.stderr)
    return m.get("status") in ("administrator", "creator") and m.get("can_post_messages") is not False
