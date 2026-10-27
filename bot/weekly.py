"""«Разбор недели» для закрытого канала «Пульс рынка PRO» (обещан в закрепе канала: по воскресеньям).

Платный канал отличается от бота глубиной, а не количеством: в боте — «что случилось», здесь — почему,
что из этого следует и что ждём на следующей неделе. Новости недели копит сам сборщик (state/week.json:
каждая заметная новость Ленты и итоги событий), изменения цен за неделю считает код, текст пишет ИИ.
Пока владелец не одобрил публикацию в канал PRO — только в тестовый канал (TELEGRAM_CHAT_ID).
"""
import json
import re
import sys
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bot import cards, edition, prompts

MSK = timezone(timedelta(hours=3))
WEEK_FILE = Path(__file__).resolve().parent.parent / "state" / "week.json"
KEEP_DAYS = 8
MONTHS = edition.MONTHS

# что показываем в таблице «Неделя в цифрах»: (ключ, название, источник)
WEEK_QUOTES = [("^GSPC", "S&P 500", "y"), ("^IXIC", "Nasdaq", "y"), ("IMOEX", "Индекс МосБиржи", "moex"), ("SBER", "Сбербанк", "moexs"), ("GAZP", "Газпром", "moexs"),
               ("bitcoin", "Биткоин", "cg"), ("ethereum", "Эфир", "cg"), ("BZ=F", "Нефть Brent", "y"),
               ("GC=F", "Золото", "y"), ("SI=F", "Серебро", "y"), ("DX-Y.NYB", "Индекс доллара", "y"), ("RUB=X", "Доллар/рубль", "y")]

WEEKLY = """Ты — главный редактор закрытого канала «Пульс рынка PRO» для трейдеров и частных инвесторов (Россия, США, крипта, сырьё).
Пишешь воскресный «Разбор недели». Читатель уже видел новости по отдельности в боте — здесь он платит за СМЫСЛ:
что на самом деле двигало рынками, как события связаны между собой и что из этого следует на следующую неделю.

Тебе дают: изменения цен за неделю (посчитаны кодом — им верь), заметные новости недели с силой влияния imp 1–3,
итоги вышедших макроданных и календарь следующей недели.

Формат — Telegram HTML (только <b>, <i>, <u>, <a href>, <blockquote>, <blockquote expandable>), без Markdown, без заголовка (его добавит программа).
Структура, между разделами пустая строка:

<b>🧭 Главное за неделю</b>
1–2 предложения: какая одна сила определила неделю (ставки, нефть и Ближний Восток, санкции, отчёты, регуляция…).

<b>📌 Пять сюжетов недели</b>
4–5 пунктов. Каждый: «1️⃣ флаг <b>сюжет</b> — одна строка: главный вывод в <u></u>», под ним <blockquote expandable>3–4 предложения:
что произошло (с цифрами из входных данных), почему это важно и как отразилось на ценах из таблицы.</blockquote>
Склеивай новости об одном событии в один сюжет. Хотя бы один сюжет — про Россию, если в новостях есть [ru].

<b>🇷🇺 Россия</b>
2–3 предложения: рубль, индекс МосБиржи, ставка Банка России, нефть для бюджета — что было и что важно на неделе.

<b>🔗 Как это связано</b>
2–4 предложения: цепочка причин между сюжетами (например: удары по танкерам → нефть ↑ → инфляционные ожидания ↑ → доходности ↑ → техсектор ↓).

<b>📅 Что ждём на неделе</b>
4–6 самых важных событий ТОЛЬКО из списка «КАЛЕНДАРЬ СЛЕДУЮЩЕЙ НЕДЕЛИ» (день недели и дату переписывай из списка), каждое строкой:
«флаг (🇷🇺 🇺🇸 🛢 ₿) <b>пн 20.10</b> — событие: почему важно и на какие активы смотреть».

<b>🎯 Сценарии</b>
<blockquote>Базовый: … (что наиболее вероятно и почему)
Риск: … (что может сломать картину и какой сигнал это покажет)</blockquote>

Правила: по-русски, просто, без воды; сложные термины поясняй в скобках 2–4 словами. Цифры — только из входных данных,
ничего не додумывай. Никаких советов «покупай/продавай», никаких обещаний доходности, никаких ценовых уровней и целей в сценариях. Объём — до 3300 знаков.

В конце, ОТДЕЛЬНОЙ последней строкой, JSON для обложки без ```:
{"cover": {"title": "главная тема недели до 45 знаков, без слов «Разбор недели»", "theme": "один из: THEMES", "sent": "pos|neg|neu"}}
""".replace("THEMES", ", ".join(cards.THEMES)) + prompts.RU_ONLY


# ---------- журнал недели ----------

def load_week():
    try:
        return json.loads(WEEK_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"news": [], "results": []}


def save_week(week, now):
    cut = (now - timedelta(days=KEEP_DAYS)).isoformat()
    week["news"] = [n for n in week["news"] if n["ts"] >= cut][-600:]
    week["results"] = [r for r in week["results"] if r.get("dt", "") >= cut][-80:]
    WEEK_FILE.parent.mkdir(exist_ok=True)
    WEEK_FILE.write_text(json.dumps(week, ensure_ascii=False, indent=0), encoding="utf-8")


def remember_news(out, now):
    """Из Ленты — заметные новости (imp ≥ 2) в журнал недели."""
    keep = [{"ts": o["ts"], "imp": o["imp"], "block": o["block"], "title": o["title"], "text": o.get("text", "")[:300],
             "src": o.get("source", "")}
            for o in out if o.get("imp", 1) >= 2]
    if not keep:
        return
    week = load_week()
    week["news"] += keep
    save_week(week, now)


def remember_results(results, now):
    """Итоги вышедших событий (факт против прогноза) — в журнал недели, без повторов по ключу."""
    week = load_week()
    have = {r.get("key") for r in week["results"]}
    for r in results or []:
        if r.get("key") and r["key"] not in have:
            week["results"].append({k: r.get(k) for k in ("key", "title", "dt", "verdict", "impact", "headline")})
    save_week(week, now)


# ---------- цены за неделю ----------

def _week_yahoo(sym, http_get, until=None):
    url = "https://query1.finance.yahoo.com/v8/finance/chart/" + urllib.parse.quote(sym) + "?range=1mo&interval=1d"
    r = json.loads(http_get(url))["chart"]["result"][0]
    cut = until.timestamp() if until else float("inf")  # until — цены на прошлую дату (наполнение канала задним числом)
    closes = [c for c, t in zip(r["indicators"]["quote"][0]["close"], r["timestamp"]) if c and t < cut]
    return closes[-1], (closes[-1] / closes[-6] - 1) * 100  # 5 торговых дней назад


def _week_moex(sec, http_get, now, shares=False, until=None):
    frm = (now - timedelta(days=14)).strftime("%Y-%m-%d")
    path = "shares/boards/TQBR/securities" if shares else "index/securities"
    d = json.loads(http_get(f"https://iss.moex.com/iss/history/engines/stock/markets/{path}/{sec}.json"
                            f"?from={frm}&iss.meta=off&history.columns=TRADEDATE,CLOSE"))["history"]["data"]
    rows = [(day, c) for day, c in d if c and (not until or day < f"{until:%Y-%m-%d}")]
    last_day = datetime.fromisoformat(rows[-1][0])
    base = [c for day, c in rows if datetime.fromisoformat(day) <= last_day - timedelta(days=7)]
    return rows[-1][1], (rows[-1][1] / base[-1] - 1) * 100


CRYPTO_Y = {"bitcoin": "BTC-USD", "ethereum": "ETH-USD"}  # для цен на прошлую дату (у CoinGecko markets — только сейчас)


def week_quotes(http_get, now, until=None):
    out = {}
    cg = {}
    try:
        if until:
            raise LookupError("цены на прошлую дату — крипта из Yahoo")
        ids = ",".join(k for k, _, s in WEEK_QUOTES if s == "cg")
        for c in json.loads(http_get("https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&ids=" + ids
                                     + "&price_change_percentage=7d")):
            cg[c["id"]] = (c["current_price"], c.get("price_change_percentage_7d_in_currency"))
    except LookupError:
        pass
    except Exception as e:
        print("Неделя, CoinGecko:", e, file=sys.stderr)
    for key, name, src in WEEK_QUOTES:
        try:
            if src == "y" or (until and key in CRYPTO_Y):
                out[key] = _week_yahoo(CRYPTO_Y.get(key, key), http_get, until)
            elif src in ("moex", "moexs"):
                out[key] = _week_moex(key, http_get, now, shares=src == "moexs", until=until)
            elif key in cg and cg[key][1] is not None:
                out[key] = cg[key]
        except Exception as e:
            print(f"Неделя, {name}: {e}", file=sys.stderr)
    return out


# 20.10: ИИ написал «Brent выше $110» при закрытии $109,86 — круглые отметки не из данных запрещаем
WEEKLY += ("\nЦЕНЫ: называй только цены и проценты из таблицы «ИЗМЕНЕНИЕ ЦЕН ЗА НЕДЕЛЮ» — никаких «выше $110», "
           "«преодолел отметку», «уровней» и целей.")


def _num(x, digits):
    return f"{x:,.{digits}f}".replace(",", " ").replace(".", ",")


def quote_table(wq):
    lines = []
    for key, name, _ in WEEK_QUOTES:
        if key not in wq:
            continue
        price, chg = wq[key]
        digits = 0 if price > 1000 else 2
        arrow = "▲" if chg >= 0 else "▼"
        lines.append(f"{name}  <b>{_num(price, digits)}</b>  {arrow} {'+' if chg >= 0 else '−'}{_num(abs(chg), 1)}%")
    return lines


def upload_table(title, rows, card_id, note=""):
    """Таблица-картинка (cards.render_table) → /card/<id>.jpg для превью сообщения."""
    import base64
    import os
    import tempfile
    path = os.path.join(tempfile.gettempdir(), f"{card_id}.jpg")
    try:
        cards.render_table(title, rows, path, "", note)
        res = edition._post("/api/card", {"id": card_id, "b64": base64.b64encode(open(path, "rb").read()).decode()})
        return (res or {}).get("url")
    except Exception as e:
        print("Таблица недели не сделана:", str(e)[:150], file=sys.stderr)
        return None


def _live():
    """Публикация идёт в канал PRO (а не образец в тестовый канал)."""
    import os
    return (os.environ.get("PRO_POSTS") or "").lower() == "on" and bool(os.environ.get("PRO_CHAT_ID"))


def _cid(card_id):
    """У образца своя картинка: Telegram запоминает превью по адресу, и 20.10 в канал PRO могла уйти тестовая карта."""
    return card_id if _live() else card_id + "t"


# ---------- выпуск ----------

def week_label(now):
    l = now.astimezone(MSK)
    a = l - timedelta(days=6)
    return (f"{a.day}–{l.day} {MONTHS[l.month - 1]}" if a.month == l.month
            else f"{a.day} {MONTHS[a.month - 1]} – {l.day} {MONTHS[l.month - 1]}")


def build_input(wq, week, calendar_next, now):
    since = (now - timedelta(days=7)).isoformat()
    news = sorted((n for n in week["news"] if n["ts"] >= since), key=lambda n: (-n["imp"], n["ts"]))[:120]
    lines = [f"Сегодня: {now.astimezone(MSK):%d.%m.%Y}, неделя {week_label(now)}", "",
             "ИЗМЕНЕНИЕ ЦЕН ЗА НЕДЕЛЮ:"]
    for key, name, _ in WEEK_QUOTES:
        if key in wq:
            lines.append(f"{name}: {wq[key][0]:.2f} ({wq[key][1]:+.1f}% за неделю)")
    lines += ["", "ИТОГИ МАКРОДАННЫХ НЕДЕЛИ:"]
    last = {}  # одно событие бывает записано дважды (разные даты/ключи) — берём последний итог по названию
    for r in sorted((r for r in week["results"] if r.get("dt", "") >= since), key=lambda r: r.get("dt", "")):
        last[r.get("title")] = r
    lines += [f"{r.get('dt', '')[:10]} {r.get('title')}: {r.get('headline')} ({r.get('verdict')}, {r.get('impact')})"
              for r in last.values()] or ["нет данных"]
    lines += ["", f"ЗАМЕТНЫЕ НОВОСТИ НЕДЕЛИ ({len(news)}):"]
    lines += [f"[{n['block']}] imp {n['imp']} | {n['ts'][:10]} | {n['title']} — {n['text']}" for n in news]
    lines += ["", "КАЛЕНДАРЬ СЛЕДУЮЩЕЙ НЕДЕЛИ:"] + (calendar_next or ["нет данных"])
    return "\n".join(lines)


def _split_cover(raw):
    """Последняя строка ответа — JSON обложки; остальное — текст поста."""
    body, cover = raw.strip(), {}
    i = body.rfind('{"cover"')
    if i >= 0:
        try:
            cover = json.loads(body[i:body.rindex("}") + 1]).get("cover") or {}
            body = body[:i].strip()
        except Exception:
            pass
    return body, cover


def run_weekly(ask_ai, http_get, calendar_next, publish_card, now, dry, until=None):
    week = load_week()
    wq = week_quotes(http_get, now, until)
    user = build_input(wq, week, calendar_next, now)
    print(f"Разбор недели: новостей в журнале {len(week['news'])}, итогов {len(week['results'])}, цен {len(wq)}, "
          f"вход ИИ {len(user)} знаков", file=sys.stderr)
    body, cover = _split_cover(ask_ai(WEEKLY, user))
    table = quote_table(wq)
    head = f"<b>📊 Разбор недели · {week_label(now)}</b>"
    html = head + "\n\n" + ("<b>Неделя в цифрах</b>\n<blockquote>" + "\n".join(table) + "</blockquote>\n\n" if table else "") \
        + body
    metrics = [{"value": f"{'+' if wq[k][1] >= 0 else '−'}{_num(abs(wq[k][1]), 1)}%", "label": n}
               for k, n, _ in WEEK_QUOTES if k in wq][:3]
    card = {"theme": cover.get("theme") if cover.get("theme") in cards.THEMES else "wallst", "tag": "РАЗБОР НЕДЕЛИ",
            "sent": cover.get("sent") if cover.get("sent") in cards.SENT else "neu",
            "title": (re.sub(r"^\s*Разбор недели\s*[:—-]\s*", "", cover.get("title") or "", flags=re.I) or "Главное за неделю")[:70],
            "metrics": metrics}
    card_id = _cid(f"wk{now.astimezone(MSK):%Y%m%d}")
    rows = [(n, _num(wq[k][0], 0 if wq[k][0] > 1000 else 2), wq[k][1]) for k, n, _ in WEEK_QUOTES if k in wq]
    note = ""
    if rows:
        best, worst = max(rows, key=lambda r: r[2]), min(rows, key=lambda r: r[2])
        note = edition.ru_numbers(f"Лучший: {best[0]} {best[2]:+.1f}% · худший: {worst[0]} {worst[2]:+.1f}% · "
                                  "изменение с прошлой недели по закрытию")
    if dry:
        print("\n" + html + "\n\nОБЛОЖКА: " + json.dumps(card, ensure_ascii=False))
        return html
    print("----- разбор недели -----\n" + html, file=sys.stderr)  # текст поста виден в журнале запуска
    url = upload_table(f"Неделя в цифрах · {week_label(now)}", rows, card_id, note) if rows else None
    if not url:  # таблицы нет — обычная карточка с главной темой
        url, _ = edition.upload_card(card, card_id, week_label(now))
    publish_card(html, url)
    return html


# ---------- другие рубрики канала PRO (сетка 19.10) ----------

def _news_lines(week, days, limit=100, now=None):
    since = ((now or datetime.now(timezone.utc)) - timedelta(days=days)).isoformat()
    news = sorted((n for n in week["news"] if n["ts"] >= since), key=lambda n: (-n["imp"], n["ts"]))[:limit]
    return [f"[{n['block']}] imp {n['imp']} | {n['ts'][:10]} | {n['title']} — {n['text']}" for n in news]


def _quotes_lines(wq):
    return [f"{name}: {wq[k][0]:.2f} ({wq[k][1]:+.1f}% за неделю)" for k, name, _ in WEEK_QUOTES if k in wq]


def _ask_post(ask_ai, system, user, key):
    """Ответ ИИ: текст поста, а последней строкой — JSON с данными для картинки ({"map": …} или {"asset": …})."""
    raw = ask_ai(system, user).strip()
    i = raw.rfind('{"' + key + '"')
    if i < 0:
        return raw, {}
    try:
        return raw[:i].strip(), json.loads(raw[i:raw.rindex("}") + 1])
    except Exception:
        return raw[:i].strip(), {}


def _upload_png(render, card_id, *args):
    import base64
    import os
    import tempfile
    path = os.path.join(tempfile.gettempdir(), f"{card_id}.jpg")
    try:
        render(*args[:2], path, *args[2:])
        res = edition._post("/api/card", {"id": card_id, "b64": base64.b64encode(open(path, "rb").read()).decode()})
        return (res or {}).get("url")
    except Exception as e:
        print("Картинка не сделана:", str(e)[:150], file=sys.stderr)
        return None


ASSETS = ["Индекс МосБиржи", "Рубль", "Сбербанк", "Газпром", "S&P 500", "Nasdaq", "Биткоин", "Нефть Brent", "Золото", "Доллар (DXY)"]

WEEK_AHEAD = """Ты — главный редактор закрытого канала «Пульс рынка PRO». Сегодня понедельник: рубрика «Неделя впереди».
Тебе дают: календарь этой недели, заметные новости последних дней, изменения цен за прошлую неделю.
Оцени для каждого актива из списка, куда скорее давят события этой недели: up (поддержка), down (давление), flat (без перевеса).
Это сценарий, а не прогноз цены. Цифры и события — только из входных данных. Никаких ценовых уровней и целей
(«Брент выше $115», «S&P 500 к 7 500» — нельзя; пиши словами: «подбросит цены на нефть»).
Формат ответа: сначала текст поста в Telegram HTML (<b>, <i>, <u>, <blockquote>, <blockquote expandable>), без заголовка, до 2200 знаков:
<b>🗓 Главное на неделе</b> — 3–5 ключевых событий строками «🇷🇺 <b>пт 23.10</b> — событие: что от него ждут и на какие активы смотреть»
(в начале строки — эмодзи-флаг: 🇷🇺 Россия, 🇺🇸 США, 🇪🇺 Европа, 🇨🇳 Китай, 🛢 нефть, ₿ крипта; слово «флаг» не пиши), по дате;
<b>🧭 Расклад сил</b> — 2–3 предложения: что сейчас определяет рынки; главный вывод в <u></u>;
<b>⚠️ Что может сломать картину</b> — <blockquote expandable>2–3 риска и сигналы, по которым их заметить</blockquote>
Затем ОТДЕЛЬНОЙ последней строкой JSON без ```:
{"map": [{"asset": "название из списка", "way": "up|down|flat", "why": "главный фактор, до 55 знаков"}]}
Список активов (все, в этом порядке): ASSETS. Никаких советов «покупай/продавай».""".replace("ASSETS", ", ".join(ASSETS)) + prompts.RU_ONLY


def run_week_ahead(ask_ai, http_get, calendar_next, publish_card, now, dry, until=None):
    week = load_week()
    wq = week_quotes(http_get, now, until)
    user = "\n".join(["КАЛЕНДАРЬ ЭТОЙ НЕДЕЛИ:"] + (calendar_next or ["нет данных"]) + ["", "ЦЕНЫ ЗА ПРОШЛУЮ НЕДЕЛЮ:"]
                     + _quotes_lines(wq) + ["", "ЗАМЕТНЫЕ НОВОСТИ ПОСЛЕДНИХ ДНЕЙ:"] + _news_lines(week, 4, 80, now))
    post, data = _ask_post(ask_ai, WEEK_AHEAD, user, "map")
    rows = [(m["asset"], m.get("way", "flat"), m.get("why", "")) for m in data.get("map", []) if m.get("asset")]
    l = now.astimezone(MSK)
    end = l + timedelta(days=6)
    label = f"{l.day}–{end.day} {MONTHS[end.month - 1]}" if l.month == end.month else f"{l.day} {MONTHS[l.month - 1]} – {end.day} {MONTHS[end.month - 1]}"
    html = f"<b>📅 Неделя впереди · {label}</b>\n\n" + post
    print("----- неделя впереди -----\n" + html + "\nКАРТА: " + json.dumps(rows, ensure_ascii=False), file=sys.stderr)
    if dry:
        return html
    url = _upload_png(cards.render_pressure, _cid(f"wa{l:%Y%m%d}"), "Неделя впереди: что давит на рынки", rows, label,
                      "Сценарий на неделю, а не прогноз цены") if rows else None
    publish_card(html, url)
    return html


ASSET = """Ты — главный редактор закрытого канала «Пульс рынка PRO». Рубрика «Актив недели»: глубокий разбор ОДНОГО актива,
который сейчас больше всего в фокусе (по новостям и движению цены). Не повторяй активы из списка «УЖЕ РАЗБИРАЛИ».
Цифры и события — только из входных данных.
Формат ответа: сначала текст поста в Telegram HTML без заголовка, до 2500 знаков:
<b>Почему сейчас</b> — 2 предложения: что происходит с активом;
<b>📉 Что давит</b> — 2–3 фактора, у каждого одна строка: фактор и как он действует;
<b>📈 Что поддерживает</b> — 2–3 фактора так же;
<b>🔗 Как это работает</b> — <blockquote expandable>цепочка «событие → … → цена» для главного фактора, простыми словами</blockquote>
<b>📅 Ближайшие события</b> — 2–3 события из календаря, которые сдвинут актив;
<b>🎯 Сценарии</b> — <blockquote>Базовый: … / Риск: …</blockquote>
Затем ОТДЕЛЬНОЙ последней строкой JSON без ```:
{"asset": "название", "theme": "фон: один из THEMES", "metrics": [{"value": "число из входных данных", "label": "2–3 слова"}]}
Никаких советов «покупай/продавай». Никаких ценовых уровней, коридоров и целей («$105–112», «выше $115») — сценарии
описывай словами: что должно случиться и куда это обычно толкает актив.""".replace("THEMES", ", ".join(cards.THEMES)) + prompts.RU_ONLY


def run_asset(ask_ai, http_get, calendar_next, publish_card, now, dry):
    week = load_week()
    wq = week_quotes(http_get, now)
    done = week.setdefault("assets_done", [])[-6:]
    user = "\n".join(["УЖЕ РАЗБИРАЛИ: " + (", ".join(done) or "ничего"), "", "ЦЕНЫ ЗА НЕДЕЛЮ:"] + _quotes_lines(wq)
                     + ["", "КАЛЕНДАРЬ НЕДЕЛИ:"] + (calendar_next or ["нет данных"]) + ["", "НОВОСТИ:"] + _news_lines(week, 7, 100))
    post, data = _ask_post(ask_ai, ASSET, user, "asset")
    if data.get("asset") in done:  # 21.10 ИИ взял Brent при «УЖЕ РАЗБИРАЛИ: Нефть Brent» — просим ещё раз, жёстче
        print(f"Актив недели: ИИ повторил «{data['asset']}» — прошу другой", file=sys.stderr)
        post, data = _ask_post(ask_ai, ASSET, f"ЗАПРЕЩЕНО выбирать: {', '.join(done)}. Выбери другой актив.\n\n" + user, "asset")
    name = data.get("asset") or "Актив недели"
    html = f"<b>🔍 Актив недели: {name}</b>\n\n" + post
    print("----- актив недели -----\n" + html, file=sys.stderr)
    if dry:
        return html
    card = {"theme": data.get("theme") if data.get("theme") in cards.THEMES else "wallst", "tag": "АКТИВ НЕДЕЛИ", "sent": "neu",
            "title": f"{name}: что давит и что поддерживает", "metrics": (data.get("metrics") or [])[:3]}
    url, _ = edition.upload_card(card, _cid(f"as{now.astimezone(MSK):%Y%m%d}"), week_label(now))
    publish_card(html, url)
    if _live():  # образцы в тестовый канал не считаем «уже разобранными»
        week["assets_done"] = (done + [name])[-6:]
        save_week(week, now)
    return html


GUIDES_DIR = Path(__file__).resolve().parent.parent / "guides"


def run_guide(ask_ai, http_get, calendar_next, publish_card, now, dry, until=None):
    """Гайд — НЕ пишет ИИ на ходу: берём следующий утверждённый владельцем файл guides/NN-*.html (первая строка — заголовок).
    Опубликованные помним в state/week.json. Нет утверждённых — ничего не публикуем."""
    week = load_week()
    done = set(week.setdefault("guides_done", []))
    files = sorted(p for p in GUIDES_DIR.glob("*.html") if p.name not in done and not p.name.startswith("_"))
    if not files:
        print("Гайд: утверждённых новых нет", file=sys.stderr)
        return None
    html = files[0].read_text(encoding="utf-8").strip()
    print("----- гайд -----\n" + html, file=sys.stderr)
    if dry:
        return html
    publish_card(html, None)
    if _live():  # образец в тестовый канал — гайд остаётся в очереди
        week["guides_done"] = sorted(done | {files[0].name})
        save_week(week, now)
    return html


MONTH = """Ты — главный редактор закрытого канала «Пульс рынка PRO». Рубрика «Итоги месяца» — 1-го числа.
Тебе дают изменения цен за месяц (посчитаны кодом), заметные новости и итоги макроданных. Цифры — только из входных данных.
Формат — Telegram HTML без заголовка, до 2500 знаков:
<b>🧭 Месяц в одной фразе</b> — что определило месяц;
<b>📌 Три главные истории</b> — 1️⃣ 2️⃣ 3️⃣ с <blockquote expandable>3 предложения</blockquote>;
<b>🏆 Лучшие и худшие</b> — 2–3 строки по таблице цен: кто вырос, кто упал и почему;
<b>📅 Что важно в новом месяце</b> — 3–4 события из календаря.
Никаких советов «покупай/продавай».""" + prompts.RU_ONLY


def _month_change(key, src, http_get, now):
    """Изменение за прошлый календарный месяц: закрытие последнего дня месяца к закрытию последнего дня позапрошлого."""
    if src == "y":
        r = json.loads(http_get("https://query1.finance.yahoo.com/v8/finance/chart/" + urllib.parse.quote(key)
                                + "?range=3mo&interval=1d"))["chart"]["result"][0]
        pts = [(datetime.fromtimestamp(t, timezone.utc).date(), c) for t, c in zip(r["timestamp"], r["indicators"]["quote"][0]["close"]) if c]
    elif src in ("moex", "moexs"):
        path = "shares/boards/TQBR/securities" if src == "moexs" else "index/securities"
        frm = (now - timedelta(days=75)).strftime("%Y-%m-%d")
        d = json.loads(http_get(f"https://iss.moex.com/iss/history/engines/stock/markets/{path}/{key}.json"
                                f"?from={frm}&iss.meta=off&history.columns=TRADEDATE,CLOSE"))["history"]["data"]
        pts = [(datetime.fromisoformat(day).date(), c) for day, c in d if c]
    else:
        d = json.loads(http_get(f"https://api.coingecko.com/api/v3/coins/{key}/market_chart?vs_currency=usd&days=70&interval=daily"))
        pts = [(datetime.fromtimestamp(t / 1000, timezone.utc).date(), c) for t, c in d["prices"]]
    first = now.astimezone(MSK).date().replace(day=1)
    prev_end = first - timedelta(days=1)
    prev_start = prev_end.replace(day=1)
    a = [c for day, c in pts if day < prev_start]
    b = [c for day, c in pts if day <= prev_end]
    return b[-1], (b[-1] / a[-1] - 1) * 100


def run_month(ask_ai, http_get, calendar_next, publish_card, now, dry):
    week = load_week()
    mq = {}
    for key, name, src in WEEK_QUOTES:
        try:
            mq[key] = _month_change(key, src, http_get, now)
        except Exception as e:
            print(f"Месяц, {name}: {e}", file=sys.stderr)
    prev = (now.astimezone(MSK).date().replace(day=1) - timedelta(days=1))
    mname = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"][prev.month - 1]
    user = "\n".join(["ЦЕНЫ ЗА МЕСЯЦ:"] + [f"{n}: {mq[k][0]:.2f} ({mq[k][1]:+.1f}% за месяц)" for k, n, _ in WEEK_QUOTES if k in mq]
                     + ["", "ИТОГИ МАКРОДАННЫХ:"] + [f"{r.get('dt', '')[:10]} {r.get('title')}: {r.get('headline')}" for r in week["results"]]
                     + ["", "КАЛЕНДАРЬ:"] + (calendar_next or ["нет данных"]) + ["", "НОВОСТИ:"] + _news_lines(week, 8, 100))
    body = ask_ai(MONTH, user).strip()
    html = f"<b>📈 Итоги месяца · {mname} {prev.year}</b>\n\n{body}"
    print("----- итоги месяца -----\n" + html, file=sys.stderr)
    if dry:
        return html
    rows = [(n, _num(mq[k][0], 0 if mq[k][0] > 1000 else 2), mq[k][1]) for k, n, _ in WEEK_QUOTES if k in mq]
    url = _upload_png(cards.render_table, _cid(f"mo{prev:%Y%m}"), f"{mname.capitalize()} в цифрах", rows, f"{prev.year}",
                      "Изменение за месяц по закрытию") if rows else None
    publish_card(html, url)
    return html
