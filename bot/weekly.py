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
WEEK_QUOTES = [("^GSPC", "S&P 500", "y"), ("^IXIC", "Nasdaq", "y"), ("IMOEX", "Индекс МосБиржи", "moex"),
               ("bitcoin", "Биткоин", "cg"), ("ethereum", "Эфир", "cg"), ("BZ=F", "Нефть Brent", "y"),
               ("GC=F", "Золото", "y"), ("DX-Y.NYB", "Индекс доллара", "y"), ("RUB=X", "Доллар/рубль", "y")]

WEEKLY = """Ты — главный редактор закрытого канала «Пульс рынка PRO» для трейдеров и частных инвесторов (Россия, США, крипта, сырьё).
Пишешь воскресный «Разбор недели». Читатель уже видел новости по отдельности в боте — здесь он платит за СМЫСЛ:
что на самом деле двигало рынками, как события связаны между собой и что из этого следует на следующую неделю.

Тебе дают: изменения цен за неделю (посчитаны кодом — им верь), заметные новости недели с силой влияния imp 1–3,
итоги вышедших макроданных и календарь следующей недели.

Формат — Telegram HTML (только <b>, <i>, <u>, <a href>, <blockquote>), без Markdown, без заголовка (его добавит программа).
Структура, между разделами пустая строка:

<b>🧭 Главное за неделю</b>
1–2 предложения: какая одна сила определила неделю (ставки, нефть и Ближний Восток, санкции, отчёты, регуляция…).

<b>📌 Пять сюжетов недели</b>
4–5 пунктов «1️⃣ флаг <b>сюжет</b>», под каждым <blockquote>3–4 предложения: что произошло (с цифрами из входных данных),
почему это важно и как отразилось на ценах из таблицы. Склеивай новости об одном событии в один сюжет. Главный вывод — в <u></u>.</blockquote>

<b>🔗 Как это связано</b>
2–4 предложения: цепочка причин между сюжетами (например: удары по танкерам → нефть ↑ → инфляционные ожидания ↑ → доходности ↑ → техсектор ↓).

<b>📅 Что ждём на неделе</b>
4–6 самых важных событий ТОЛЬКО из списка «КАЛЕНДАРЬ СЛЕДУЮЩЕЙ НЕДЕЛИ» (день недели и дату переписывай из списка): по каждому
одной строкой — почему важно и на какие активы смотреть.

<b>🎯 Сценарии</b>
<blockquote>Базовый: … (что наиболее вероятно и почему)
Риск: … (что может сломать картину и какой сигнал это покажет)</blockquote>

Правила: по-русски, просто, без воды; сложные термины поясняй в скобках 2–4 словами. Цифры — только из входных данных,
ничего не додумывай. Никаких советов «покупай/продавай», никаких обещаний доходности. Объём — до 3000 знаков.

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
    keep = [{"ts": o["ts"], "imp": o["imp"], "block": o["block"], "title": o["title"], "text": o.get("text", "")[:300]}
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

def _week_yahoo(sym, http_get):
    url = "https://query1.finance.yahoo.com/v8/finance/chart/" + urllib.parse.quote(sym) + "?range=1mo&interval=1d"
    r = json.loads(http_get(url))["chart"]["result"][0]
    closes = [c for c in r["indicators"]["quote"][0]["close"] if c]
    return closes[-1], (closes[-1] / closes[-6] - 1) * 100  # 5 торговых дней назад


def _week_moex(sec, http_get, now):
    frm = (now - timedelta(days=14)).strftime("%Y-%m-%d")
    d = json.loads(http_get(f"https://iss.moex.com/iss/history/engines/stock/markets/index/securities/{sec}.json"
                            f"?from={frm}&iss.meta=off&history.columns=TRADEDATE,CLOSE"))["history"]["data"]
    rows = [(day, c) for day, c in d if c]
    last_day = datetime.fromisoformat(rows[-1][0])
    base = [c for day, c in rows if datetime.fromisoformat(day) <= last_day - timedelta(days=7)]
    return rows[-1][1], (rows[-1][1] / base[-1] - 1) * 100


def week_quotes(http_get, now):
    out = {}
    cg = {}
    try:
        ids = ",".join(k for k, _, s in WEEK_QUOTES if s == "cg")
        for c in json.loads(http_get("https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&ids=" + ids
                                     + "&price_change_percentage=7d")):
            cg[c["id"]] = (c["current_price"], c.get("price_change_percentage_7d_in_currency"))
    except Exception as e:
        print("Неделя, CoinGecko:", e, file=sys.stderr)
    for key, name, src in WEEK_QUOTES:
        try:
            if src == "y":
                out[key] = _week_yahoo(key, http_get)
            elif src == "moex":
                out[key] = _week_moex(key, http_get, now)
            elif key in cg and cg[key][1] is not None:
                out[key] = cg[key]
        except Exception as e:
            print(f"Неделя, {name}: {e}", file=sys.stderr)
    return out


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


def run_weekly(ask_ai, http_get, calendar_next, publish_card, now, dry):
    week = load_week()
    wq = week_quotes(http_get, now)
    user = build_input(wq, week, calendar_next, now)
    print(f"Разбор недели: новостей в журнале {len(week['news'])}, итогов {len(week['results'])}, цен {len(wq)}, "
          f"вход ИИ {len(user)} знаков", file=sys.stderr)
    body, cover = _split_cover(ask_ai(WEEKLY, user))
    table = quote_table(wq)
    head = f"<b>📊 Разбор недели · {week_label(now)}</b>"
    html = head + "\n\n" + ("<b>Неделя в цифрах</b>\n<blockquote>" + "\n".join(table) + "</blockquote>\n\n" if table else "") \
        + body + f"\n\n<i>{edition.NOT_ADVICE}</i>"
    metrics = [{"value": f"{'+' if wq[k][1] >= 0 else '−'}{_num(abs(wq[k][1]), 1)}%", "label": n}
               for k, n, _ in WEEK_QUOTES if k in wq][:3]
    card = {"theme": cover.get("theme") if cover.get("theme") in cards.THEMES else "wallst", "tag": "РАЗБОР НЕДЕЛИ",
            "sent": cover.get("sent") if cover.get("sent") in cards.SENT else "neu",
            "title": (re.sub(r"^\s*Разбор недели\s*[:—-]\s*", "", cover.get("title") or "", flags=re.I) or "Главное за неделю")[:70],
            "metrics": metrics}
    card_id = f"wk{now.astimezone(MSK):%Y%m%d}"
    if dry:
        print("\n" + html + "\n\nОБЛОЖКА: " + json.dumps(card, ensure_ascii=False))
        return html
    print("----- разбор недели -----\n" + html, file=sys.stderr)  # текст поста виден в журнале запуска
    url, _ = edition.upload_card(card, card_id, week_label(now))
    publish_card(html, url)
    return html
