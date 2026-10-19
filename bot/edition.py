"""Выпуски для бота и канала: утро / вечер (4 главные новости) и срочные новости днём (до 2 в день).

Формат одного сообщения: карточка-картинка сверху (превью ссылки) + полный текст новостей, как в приложении.
"""
import base64
import json
import os
import re
import sys
import tempfile
import urllib.request
from datetime import datetime, timedelta, timezone

from PIL import Image

from bot import cards, photos, prompts

MSK = timezone(timedelta(hours=3))
THEMES = ", ".join(cards.THEMES)
# Юрист 15.10: каждое сообщение в чат — с пометкой кодом, не через ИИ (выпуски персональные, ст. 6.1 39-ФЗ)
NOT_ADVICE = "Не инвестрекомендация."


def not_advice(html):
    """Дописать пометку, если ИИ её пропустил."""
    return html if "рекомендаци" in html else html.rstrip() + f"\n\n<i>{NOT_ADVICE}</i>"
DAY_NEWS_PER_DAY = 12    # днём — всего за сутки (сервер присылает человеку его лимит: 4 или 10, по его рынкам)
DAY_NEWS_GAP_MIN = 40    # не чаще, чем раз в 40 минут
FEED_PER_RUN = 6         # в ленту приложения — до 6 заметных новостей за каждую проверку (каждые 30 минут)
FEED_CARDS = 3           # карточки-картинки к Ленте: до 3 за проверку, только imp ≥ 2 (Инженер 16.10: ≤ 5 мин на запуск, ~7 МБ/сутки)
FEED_TAG = {"us": "Рынок США", "crypto": "Крипта", "commodities": "Сырьё", "ru": "Россия", "x": "Что говорят"}
FEED_THEME = {"us": "wallst", "crypto": "crypto", "commodities": "oil", "ru": "kremlin", "x": "whitehouse"}
URGENT_PER_DAY = 1
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]
SENT_ICON = {"pos": "🟢", "neg": "🔴", "neu": "⚪️"}

PICK = """Ты — выпускающий редактор. Дан список новостей с номерами и котировки.
Выбери 4 самые важные для рынков (разные темы, если возможно) и придумай обложку-карточку для первой.
Верни СТРОГО JSON без ```:
{"pick": [номер, номер, номер, номер],
 "cover": {"title": "заголовок обложки до 70 знаков", "tag": "1–2 слова: ФРС / Крипта / Нефть / Рынок США / Золото…",
   "theme": "один из: THEMES", "sent": "pos|neg|neu",
   "metrics": [{"value": "короткое число, напр. −$485 млн", "label": "2–3 слова"}],
   "photo": ["2–3 англ. слова: что конкретно изображить", "более общий запрос", "1–2 слова: самое общее"]}}
metrics — 2–3 штуки, ТОЛЬКО числа из входных данных.
photo — запросы для поиска фото на Wikimedia Commons: предмет или место из новости, без имён людей и брендов-логотипов.
Пример для новости про дата-центр: ["data center servers", "server room", "servers"]; про нефть в Ормузском проливе: ["oil tanker strait", "oil tanker", "tanker"]. theme: fed — ФРС/ставки; wallst — акции и индексы; oil, tanker — нефть и Ближний Восток;
gold — золото; capitol, whitehouse — политика США и Трамп; port — торговля и пошлины; dollar — доллар и инфляция; tech — ИИ и техкомпании; crypto — крипта; kremlin — Россия: ЦБ РФ, Мосбиржа, рубль, Кремль.
""".replace("THEMES", THEMES)

NEWS_NOW = """Ты — выпускающий редактор сервиса новостей для трейдеров и инвесторов. Дан список свежих новостей (последние ~30–60 минут).
Подписчики получают днём до 4 отдельных сообщений — только по-настоящему важные новости, в момент их выхода.
Выбери ОДНУ новость, если она достойна отдельного сообщения прямо сейчас: заметно двигает или может двинуть цены —
ставки и заявления ЦБ/ФРС, вышедшие макроданные, крупные компании и их отчёты, нефть и ОПЕК+, крупные движения крипты,
решения регуляторов, санкции и геополитика с влиянием на рынки. Рутина, аналитика, прогнозы блогеров, мелкие компании, реклама — НЕ отправлять.
Если сомневаешься — send=false.
urgent=true — ТОЛЬКО исключительное (не чаще раза в несколько дней): решение Банка России или ФРС по ставке, неожиданное решение регулятора
с сильной реакцией рынка, обвал или взлёт индекса, BTC или нефти на 5%+, крупная военная эскалация, взлом биржи на $100 млн+.
Верни СТРОГО JSON без ```:
{"send": true|false, "urgent": true|false, "pick": номер, "title": "заголовок до 80 знаков",
 "text": "2–3 предложения по-русски с цифрами: что произошло и что это обычно значит для рынка; главное следствие оберни в <u></u>",
 "sent": "pos|neg|neu", "block": "us|crypto|commodities|ru|x", "tag": "1–2 слова", "theme": "один из: THEMES",
 "metrics": [{"value": "...", "label": "..."}], "photo": ["2–3 англ. слова: предмет или место из новости", "более общий запрос", "1–2 слова: самое общее"],
 "feed": [{"pick": номер, "title": "заголовок до 90 знаков", "text": "2–3 коротких предложения по-русски, 200–300 знаков: что произошло — с цифрами (сколько, на сколько %, по сравнению с чем); главное следствие для рынка — в <u></u>", "block": "us|crypto|commodities|ru|x", "sent": "pos|neg|neu", "icon": "1 эмодзи-флаг или тема",
           "theme": "фон карточки, один из: THEMES", "imp": 1|2|3, "also": [номера других новостей списка о том же событии],
           "metrics": [{"value": "короткое число из новости, напр. +3%, $190 млрд, 16,5%", "label": "2–3 слова: что это"}]}]}
photo — для поиска фото на Wikimedia Commons, без имён людей. Никаких советов «покупай/продавай». Цифры только из входных данных.
feed — ВСЕГДА (даже при send=false): до 6 самых заметных новостей из списка для ленты приложения — разные темы, без рутины и рекламы, дубли склеивай.
Текст в feed — коротко и по делу: цифры и главный факт, без вводных слов, общих фраз и пересказа фона.
Только факты из входных данных, ничего не додумывай.
metrics в feed — 1–3 главные цифры новости (как на карточке): только числа из текста новости; нет цифр — пустой список.
Одно событие — ОДНА запись в feed. pick — самый надёжный источник: официальный (ЦБ, ФРС, Минфин, биржа, сама компания) >
крупные СМИ (Reuters, Bloomberg, CNBC, Интерфакс, ТАСС, РИА, РБК, Коммерсант, Ведомости) > Telegram-каналы > X. Остальные новости о том же — в also.
«УЖЕ В ЛЕНТЕ» ниже — то, что уже показано за сутки: такое событие не добавляй снова, кроме случая, когда появились НОВЫЕ факты
(решение принято, вышли цифры, реакция рынка) — тогда заголовок начни с того, что нового.
imp — сила влияния на рынок: 3 — двигает рынок целиком (ставки ЦБ/ФРС, макроданные, ОПЕК+, санкции, движение индекса/BTC/нефти 3%+);
2 — заметно для отрасли, крупной компании или актива; 1 — фон, контекст.
""".replace("THEMES", THEMES)


PICK, NEWS_NOW = PICK + prompts.RU_ONLY, NEWS_NOW + prompts.RU_ONLY


def _json(raw):
    raw = re.sub(r"^```(?:json)?|```$", "", raw.strip()).strip()
    return json.loads(raw[raw.index("{"):raw.rindex("}") + 1])


def _post(path, payload):
    url, secret = os.environ.get("INGEST_URL"), os.environ.get("INGEST_SECRET")
    if not (url and secret):
        return None
    req = urllib.request.Request(url.replace("/api/ingest", path), data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {secret}",
                                          "User-Agent": "Mozilla/5.0 (market-digest-bot)"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def upload_card(card, card_id, date_label):
    path = os.path.join(tempfile.gettempdir(), f"{card_id}.jpg")
    cards.render(card, path, date_label)
    b64 = base64.b64encode(open(path, "rb").read()).decode()
    res = _post("/api/card", {"id": card_id, "b64": b64})
    return (res or {}).get("url"), path


def feed_cards(out, now, dry):
    """Картинка к важным новостям Ленты — как в Телеграме (фон темы + заголовок + цифры), но 960×540: в D1 ~100 КБ вместо ~800.
    Включается переменной FEED_CARDS=on (чистку карточек старше 7 дней делает сервер при каждой новой)."""
    themes = [o.pop("_theme", None) for o in out]  # служебное поле — на сервер не уходит
    if os.environ.get("FEED_CARDS") != "on" and not dry:
        return
    top = sorted((i for i, o in enumerate(out) if o["imp"] >= 2), key=lambda i: -out[i]["imp"])[:FEED_CARDS]
    for n, j in enumerate(top):
        o, theme = out[j], themes[j]
        card = {"theme": theme if theme in cards.THEMES else FEED_THEME.get(o["block"], "wallst"),
                "tag": FEED_TAG.get(o["block"], "Рынки"), "sent": o["sent"], "title": o["title"][:90], "metrics": o["metrics"]}
        card_id = f"f{now:%Y%m%d%H%M}{n}"
        path = os.path.join(tempfile.gettempdir(), f"{card_id}.jpg")
        try:
            cards.render(card, path, date_label(now))
            Image.open(path).resize((960, 540), Image.LANCZOS).save(path, "JPEG", quality=82, optimize=True)
            if dry:
                print(f"Лента: карточка {path} ({os.path.getsize(path) // 1024} КБ)", file=sys.stderr)
                continue
            _post("/api/card", {"id": card_id, "b64": base64.b64encode(open(path, "rb").read()).decode()})
            o["card"] = card_id
        except Exception as e:
            print(f"Лента: карточка не сделана — {str(e)[:120]}", file=sys.stderr)


def news_html(n):
    src = f' — <a href="{n["url"]}">{n["source"]}</a>' if n.get("url") else ""
    return (f'{n.get("icon") or ""} <b>{n["title"]}</b>\n'
            f'<blockquote>{SENT_ICON.get(n.get("sent"), "⚪️")} {n.get("text", "")}{src}</blockquote>')


def date_label(now):
    l = now.astimezone(MSK)
    return f"{l.day} {MONTHS[l.month - 1][:3].upper()} · {l:%H:%M} МСК"


FOCUS_RU = """
ФОКУС ВЫПУСКА: читатель следит за РОССИЙСКИМ рынком (Мосбиржа, ЦБ и ставка, рубль, ОФЗ, ипотека, нефть и металлы для России).
Выбирай в первую очередь новости [ru]; сырьё [commodities] — только если оно важно для России. Тема обложки — kremlin, если новость про Россию."""


NUM = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]


def morning_html(news, pick, qlines, today):
    """Утро — не повтор вчерашнего вечера: 3 заголовка ночи (без ссылок и пересказа), как изменились цены, что ждём днём."""
    night = "\n".join(f'{NUM[i]} {news[k].get("icon") or ""} <b>{news[k]["title"]}</b>' for i, k in enumerate(pick[:3]))
    out = "🌙 <b>Что было ночью</b>\n" + night
    if qlines:
        out += "\n\n📊 <b>Как это сказалось на ценах</b>\n<blockquote>" + "\n".join(qlines) + "</blockquote>"
    out += "\n\n📅 <b>Что ждём сегодня</b>\n" + ("\n".join("➣ " + t for t in today[:5]) if today else "➣ Крупных событий в календаре нет — рынок будет смотреть на новости")
    return out


MORNING = """Ты — выпускающий редактор утреннего обзора рынков. Дан список новостей за ночь (последние ~12 часов) с номерами.
Выбери по 3 самые важные для рынков новости (разные темы) — для общего обзора ("all") и для читателей российского рынка ("ru": Россия, ЦБ, рубль, Мосбиржа, сырьё для России).
Заголовок — по-русски, коротко и по сути, до 90 знаков, без кликбейта. icon — флаг страны или 1 эмодзи темы.
Верни СТРОГО JSON без ```:
{"all": [{"pick": номер, "title": "...", "icon": "🇺🇸"}], "ru": [{"pick": номер, "title": "...", "icon": "🇷🇺"}],
 "cover": {"title": "заголовок обложки до 70 знаков по первой новости all", "tag": "1–2 слова", "theme": "один из: THEMES", "sent": "pos|neg|neu",
   "metrics": [{"value": "число из новостей", "label": "2–3 слова"}], "photo": ["2–3 англ. слова", "общий запрос", "1–2 слова"]}}
Цифры — только из входных данных.
""".replace("THEMES", THEMES)
MORNING += prompts.RU_ONLY


def build_morning(ask_ai, items, now, tzmin, qlines, qlines_ru, today, today_ru, publish_channel):
    """Утренний обзор для пояса tzmin: 3 заголовка ночи + цены + что ждём сегодня. Возвращает блоки для сервера."""
    if not items:
        return []
    lst = "\n".join(f'{i}. {it["source"]} | {it["title"]} — {it["desc"][:200]}' for i, it in enumerate(items[:120]))
    try:
        data = _json(ask_ai(MORNING, lst))
    except Exception as e:
        print("Утро: разбор не удался:", e, file=sys.stderr)
        return []
    local = now.astimezone(timezone(timedelta(minutes=tzmin)))
    c = data.get("cover") or {}
    cover = {"title": c.get("title") or ((data.get("all") or [{}])[0].get("title", "")), "tag": c.get("tag", ""), "sent": c.get("sent", "neu"),
             "theme": c.get("theme") if c.get("theme") in cards.THEMES else "wallst", "metrics": (c.get("metrics") or [])[:3], "photo": photos.find(c.get("photo") or [])}
    if not cover["title"]:
        return []
    card_id = f"mo{local:%Y%m%d}{tzmin}"
    url, _ = upload_card(cover, card_id, f"{local.day} {MONTHS[local.month - 1][:3].upper()} · УТРО")
    out = []
    for key, ql, td, tag in (("all", qlines, today, ""), ("ru", qlines_ru, today_ru, " · 🇷🇺 Россия")):
        heads = [{"title": h.get("title", ""), "icon": h.get("icon", "")} for h in (data.get(key) or []) if h.get("title")][:3]
        if not heads:
            continue
        head = f"<b>☀️ Доброе утро! Что было ночью и что ждём днём{tag} · {local.day} {MONTHS[local.month - 1]}</b>"
        html = head + "\n\n" + morning_html(heads, [0, 1, 2][:len(heads)], ql, td) + f"\n\n<i>Подробности, влияние на цены и календарь — в приложении 👇 {NOT_ADVICE}</i>"
        out.append({"block": f"morning_{tzmin}" + ("_ru" if key == "ru" else ""), "html": [card_id, html[:4000], "#today"]})
        if key == "all":
            publish_channel(html, url)
    print(f"Утро UTC{tzmin / 60:+g}: собрано {len(out)} вариантов", file=sys.stderr)
    return out


def build_edition(ask_ai, news, quotes_text, now, total_news, focus="", qlines=None, today=None):
    """Утренний (до 14:00 МСК) или вечерний выпуск. Возвращает (card_id, html) или None.
    Утро: «что было ночью» (заголовки) + «цены к утру» (qlines) + «что ждём сегодня» (today). Вечер: 4 главные новости целиком.
    focus="ru" — выпуск для тех, кто выбрал российский рынок без США: только новости России (+ сырьё)."""
    if focus == "ru":
        ru = [n for n in news if n["block"] == "ru"]
        if len(ru) < 3:
            return None  # российских новостей мало — такие читатели получат общий выпуск
        news = ru + [n for n in news if n["block"] == "commodities"]
    if not news:
        return None
    lst = "\n".join(f'{i}. [{n["block"]}/{n.get("sent")}] {n["title"]} — {re.sub("<[^>]+>", "", n.get("text", ""))[:220]}'
                    for i, n in enumerate(news))
    try:
        n_pick = 3 if now.astimezone(MSK).hour < 14 else 4
        data = _json(ask_ai(PICK.replace("Выбери 4", f"Выбери {n_pick}") + (FOCUS_RU if focus == "ru" else ""), "КОТИРОВКИ:\n" + quotes_text + "\n\nНОВОСТИ:\n" + lst))
        pick = [int(i) for i in data.get("pick", []) if 0 <= int(i) < len(news)][:n_pick]
        cover = data.get("cover") or {}
    except Exception as e:
        print("Выпуск: выбор новостей не удался:", e, file=sys.stderr)
        pick, cover = list(range(min(4, len(news)))), {}
    if not pick:
        return None
    first = news[pick[0]]
    cover = {"title": cover.get("title") or first["title"], "tag": cover.get("tag", ""), "sent": cover.get("sent") or first.get("sent", "neu"),
             "theme": cover.get("theme") if cover.get("theme") in cards.THEMES else "wallst", "metrics": (cover.get("metrics") or [])[:3],
             "photo": photos.find(cover.get("photo") or [])}
    local = now.astimezone(MSK)
    morning = local.hour < 14
    card_id = f"ed{local:%Y%m%d%H%M}" + ("ru" if focus == "ru" else "")
    url, _ = upload_card(cover, card_id, date_label(now))
    tag = " · 🇷🇺 Россия" if focus == "ru" else ""
    # сервер пересобирает заголовок по времени каждого человека
    head = (f"<b>☀️ Доброе утро! Что было ночью и что ждём днём{tag} · {local.day} {MONTHS[local.month - 1]}</b>" if morning
            else f"<b>🌙 Добрый вечер! Итоги дня{tag} · {local.day} {MONTHS[local.month - 1]}</b>")
    if morning:
        html = head + "\n\n" + morning_html(news, pick, qlines or [], today or []) + f"\n\n<i>Подробности, влияние на цены и календарь — в приложении 👇 {NOT_ADVICE}</i>"
        return card_id, html[:4000], url
    body = "\n\n".join(news_html(news[i]) for i in pick)
    rest = max(0, total_news - len(pick))
    tail = (f"\n\n<i>Ещё {rest} новостей, влияние на цены и календарь — в приложении 👇 {NOT_ADVICE}</i>" if rest
            else f"\n\n<i>{NOT_ADVICE}</i>")
    html = head + "\n\n" + body + tail
    if len(html) > 4000:  # лимит Telegram — укорачиваем тексты
        body = "\n\n".join(news_html({**news[i], "text": re.sub("<[^>]+>", "", news[i].get("text", ""))[:300] + "…"}) for i in pick)
        html = head + "\n\n" + body + tail
    return card_id, html, url


MAJOR_MEDIA = ("reuters", "bloomberg", "cnbc", "wsj", "financial times", "интерфакс", "тасс", "риа", "рбк", "коммерсант",
               "ведомости", "банк россии", "фрс", "минфин", "мосбиржа", "московская биржа", "marketwatch", "coindesk", "the block")


def source_rank(it):
    """Чем меньше, тем надёжнее: крупные СМИ и официальные > прочие сайты > Telegram-каналы > X."""
    src = it["source"]
    if src.startswith("X @"):
        return 3
    if src.startswith("TG @"):
        return 2
    return 0 if any(m in src.lower() for m in MAJOR_MEDIA) else 1


def _words(title):
    return set(re.findall(r"\d+(?:[.,]\d+)?|[^\W\d]{4,}", title.lower()))  # числа отличают обновление от повтора


def seen_before(title, recent):
    """Тот же сюжет уже в ленте за сутки: совпадает 70%+ значимых слов и чисел заголовка (ИИ это пропускает)."""
    w = _words(title)
    return bool(w) and any(len(w & _words(r)) / len(w | _words(r)) >= 0.7 for r in recent)


def push_feed(feed, items, now, dry, state=None):
    """Свежие новости — в ленту приложения (сервер склеит с прошлыми и хранит 7 дней)."""
    out = []
    for f in feed[:FEED_PER_RUN]:
        try:
            src = items[int(f.get("pick", -1))]
        except Exception:
            continue
        if not f.get("title"):
            continue
        if state is not None and seen_before(f["title"], [r[1] for r in state.get("feed_recent", [])] + [o["title"] for o in out]):
            print(f"Лента: повтор, пропускаю — {f['title'][:70]}", file=sys.stderr)
            continue
        name = lambda it: it["source"].replace("TG @", "").replace("X @", "")
        group = [src]
        for i in f.get("also") or []:
            try:
                group.append(items[int(i)])
            except Exception:
                continue
        src = min(group, key=source_rank)  # главный — самый надёжный источник (ИИ это часто путает)
        also = []
        for it in group:
            if name(it) != name(src) and name(it) not in also:
                also.append(name(it))
        try:
            imp = min(3, max(1, int(f.get("imp") or 1)))
        except Exception:
            imp = 1
        out.append({"block": f.get("block") or "us", "icon": (f.get("icon") or "")[:8], "title": f["title"][:120], "text": f.get("text", ""),
                    "sent": f.get("sent") if f.get("sent") in ("pos", "neg", "neu") else "neu", "url": src["link"],
                    "source": name(src), "also": also[:10], "imp": imp, "ts": now.isoformat(), "_theme": f.get("theme"),
                    "metrics": [{"value": str(x.get("value", ""))[:16], "label": str(x.get("label", ""))[:24]}
                                for x in (f.get("metrics") or []) if isinstance(x, dict) and x.get("value")][:3]})
    if not out:
        return
    if state is not None:  # что уже в ленте за сутки — ИИ не повторит это событие в следующих проверках
        day_ago = (now - timedelta(hours=24)).isoformat()
        state["feed_recent"] = [r for r in state.get("feed_recent", []) if r[0] > day_ago][-150:] + [[o["ts"], o["title"]] for o in out]
    feed_cards(out, now, dry)
    print(f"Лента: +{len(out)} новостей, текст в среднем {sum(len(o['text']) for o in out) // len(out)} знаков", file=sys.stderr)
    if dry:
        for o in out:
            print(f"Лента (образец): [imp {o['imp']}] {o['title']} — {o['text'][:120]}… | {o['source']} + также {o['also']} | цифры {o['metrics']}", file=sys.stderr)
    if not dry:
        from bot import weekly  # журнал недели для «Разбора недели» (там же импорт edition)
        weekly.remember_news(out, now)
        try:
            _post("/api/ingest", {"edition": "feed", "blocks": [], "meta": {"news": out}})
        except Exception as e:
            print("Лента: сервер недоступен", e, file=sys.stderr)


def run_news_now(ask_ai, items, state, now, publish_channel, force=False):
    """Раз в 30 минут (08–22 МСК): есть ли важная новость, чтобы прислать её отдельным сообщением.
    Обычная — «🌤 Главное сейчас» (до DAY_NEWS_PER_DAY за сутки, не чаще раза в час), исключительная — «⚡ Срочно» (1 в сутки).
    force=True — для образцов: взять самую важную, даже если она не дотягивает. Возвращает {kind, card, html} или None."""
    local = now.astimezone(MSK)
    day = f"{local:%Y-%m-%d}"
    b = state.setdefault("news_now", {})
    if b.get("day") != day:
        b.clear(); b.update(day=day, day_count=0, urgent_count=0, last="", titles=[])
    if not items or (not force and not 7 <= local.hour < 24):
        return None
    gap_ok = not b["last"] or now - datetime.fromisoformat(b["last"]) >= timedelta(minutes=DAY_NEWS_GAP_MIN)
    can_send = force or (8 <= local.hour < 22 and (b["urgent_count"] < URGENT_PER_DAY or (b["day_count"] < DAY_NEWS_PER_DAY and gap_ok)))
    lst = "\n".join(f'{i}. {it["source"]} | {it["title"]} — {it["desc"][:600]} | {it["link"]}' for i, it in enumerate(items[:80]))
    try:
        data = _json(ask_ai(NEWS_NOW + ("\nДЛЯ ОБРАЗЦА: обязательно выбери самую важную новость, send=true." if force else ""),
                            lst + "\n\nУже отправленные сегодня (не повторять): " + "; ".join(b["titles"])
                            + "\n\nУЖЕ В ЛЕНТЕ за сутки: " + "; ".join(r[1] for r in state.get("feed_recent", []))))
    except Exception as e:
        print("Новости дня: разбор не удался:", e, file=sys.stderr)
        return None
    push_feed(data.get("feed") or [], items, now, force, state)
    if not can_send:
        return None
    urgent = bool(data.get("urgent")) and b["urgent_count"] < URGENT_PER_DAY
    if not force and not (data.get("send") or urgent):
        print("Важных новостей сейчас нет", file=sys.stderr)
        return None
    if not force and not urgent and (b["day_count"] >= DAY_NEWS_PER_DAY or not gap_ok):
        print("Новости дня: лимит или рано после прошлой", file=sys.stderr)
        return None
    try:
        src = items[int(data.get("pick", 0))]
    except Exception:
        src = items[0]
    card = {"title": data.get("title", src["title"]), "tag": data.get("tag", ""), "sent": data.get("sent", "neu"),
            "theme": data.get("theme") if data.get("theme") in cards.THEMES else "wallst", "metrics": (data.get("metrics") or [])[:3],
            "photo": photos.find(data.get("photo") or [])}
    card_id = f"{'br' if urgent else 'dn'}{local:%Y%m%d%H%M}"
    url, _ = upload_card(card, card_id, date_label(now))
    n = {"title": card["title"], "text": data.get("text", ""), "sent": card["sent"], "url": src["link"], "source": src["source"].replace("TG @", "").replace("X @", "")}
    head = f"⚡ <b>Срочно · {card['tag']}</b>" if urgent else f"🌤 <b>Главное сейчас · {card['tag']}</b>"
    html = head + "\n\n" + news_html(n) + f"\n\n<i>{NOT_ADVICE}</i>"
    out = {"kind": "urgent" if urgent else "day", "card": card_id, "html": html, "block": data.get("block") or "", "hash": "#feed"}
    if force:
        return out
    _post("/api/breaking", out)
    publish_channel(html, url)
    b["urgent_count" if urgent else "day_count"] += 1
    b["last"] = now.isoformat()
    b["titles"].append(card["title"])
    print(("Срочная" if urgent else "Новость дня") + " отправлена:", card["title"], file=sys.stderr)
    return out
