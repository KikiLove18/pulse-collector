"""Бот-выжимка новостей для трейдеров.

Запуск:
  python -m bot.main auto       — то, что делает GitHub по расписанию (выжимка в своё время + анонсы событий)
  python -m bot.main digest     — выжимка прямо сейчас
  python -m bot.main events     — анонсы ближайших событий календаря
  python -m bot.main breaking    — проверить, есть ли важная новость (днём — «🌤 Главное сейчас», исключительная — «⚡ Срочно»)
  python -m bot.main preview     — образцы всех новостных сообщений в тестовый канал (сервер шлёт их как подписчикам)
  --dry-run                     — ничего не отправлять в Telegram, показать результат в консоли

Ключи берутся из переменных окружения:
  GEMINI_API_KEY или OPENROUTER_API_KEY (+ OPENROUTER_MODEL), TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
  GEMINI_MODEL (по умолчанию gemini-flash-latest), DIGEST_TIMES (по умолчанию 07:30,19:30 по МСК — к 08:00 и 20:00 выпуск уже готов)
"""
import calendar as _cal
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path

import feedparser

from bot import calendar_future, edition, macro, prompts, sources

MSK = timezone(timedelta(hours=3))
ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = ROOT / "state" / "state.json"
UA = "Mozilla/5.0 (market-digest-bot)"

MAX_PER_FEED = 25
MAX_DESC = 280
DIGEST_WINDOW_MAX_H = 24
SLOT_GRACE_MIN = 90       # GitHub может запустить расписание с опозданием
EVENT_LEAD_MIN = 75       # за сколько минут до события присылать анонс


# ---------- состояние ----------

def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"sent_digests": [], "sent_events": [], "last_digest_ts": None}


def save_state(state):
    state["sent_digests"] = state["sent_digests"][-60:]
    state["sent_events"] = state["sent_events"][-200:]
    STATE_FILE.parent.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------- сбор ----------

def http_get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def clean(text):
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def pmap(fn, seq, workers=10):
    """Скачиваем параллельно (порядок результатов сохраняется): по одному сбор шёл 1–1,5 мин из каждого запуска."""
    with ThreadPoolExecutor(workers) as ex:
        return list(ex.map(fn, seq))


def _fetch_feed(src):
    try:
        return feedparser.parse(http_get(src[2])), None
    except Exception as e:  # одна упавшая лента не должна ломать выжимку
        return None, f"{src[1]}: {e}"


def fetch_news(since):
    items, errors = [], []
    for (cat, name, url), (feed, err) in zip(sources.FEEDS, pmap(_fetch_feed, sources.FEEDS)):
        if err:
            errors.append(err)
            continue
        for e in feed.entries[:MAX_PER_FEED]:
            t = e.get("published_parsed") or e.get("updated_parsed")
            if not t:
                continue
            ts = datetime.fromtimestamp(_cal.timegm(t), timezone.utc)
            if ts < since:
                continue
            title = clean(e.get("title"))
            desc = clean(e.get("summary"))
            if title.startswith("[No Title]"):
                title = ""
            if not (title or desc):
                continue  # например, пост Трампа только с видео
            items.append({
                "cat": cat, "source": name, "time": ts,
                "title": title, "desc": desc[:MAX_DESC], "link": e.get("link", ""),
            })
    items.sort(key=lambda x: x["time"], reverse=True)
    return items, errors


CALENDAR_TTL_H = 6  # ForexFactory отвечает 429 на частые запросы — держим копию


TG_MAX_PAGES = 8  # по ~20 постов на страницу; для частых каналов (MarketTwits) листаем назад
TG_MAX_ITEMS = 40  # не больше постов с канала — чтобы уложиться в бесплатный лимит ИИ


def fetch_telegram(since, channels, cat="telegram", prefix="TG"):
    """Посты публичных каналов из веб-версии t.me/s/. Рекламу пропускаем."""
    items, errors = [], []
    for ch_items, err in pmap(lambda ch: _fetch_channel(ch, since, cat, prefix), channels, workers=6):
        items += ch_items
        if err:
            errors.append(err)
    return items, errors


def _fetch_channel(ch, since, cat, prefix):
    ch_items, err = [], None
    url = f"https://t.me/s/{ch}"
    for _ in range(TG_MAX_PAGES):
        try:
            page = http_get(url).decode("utf-8", "replace")
        except Exception as e:
            err = f"t.me/{ch}: {e}"
            break
        oldest, min_id = None, None
        for block in page.split('class="tgme_widget_message_wrap')[1:]:
            post = re.search(r'data-post="([^"]+)"', block)
            when = re.search(r'<time datetime="([^"]+)"', block)
            if not (post and when):
                continue
            ts = datetime.fromisoformat(when.group(1)).astimezone(timezone.utc)
            pid = int(post.group(1).split("/")[-1])
            oldest = ts if oldest is None else min(oldest, ts)
            min_id = pid if min_id is None else min(min_id, pid)
            body = re.search(r'tgme_widget_message_text[^>]*>(.*?)</div>', block, re.S)
            if not body or ts < since:
                continue
            text = clean(re.sub(r"<br\s*/?>", " ", body.group(1)))
            if not text or "Реклама" in text[-200:]:
                continue
            x_links = re.findall(r'href="(https://(?:x|twitter)\.com/[^"]+)"', body.group(1))
            desc = text[120:600] + (f" [пост в X: {x_links[0]}]" if x_links else "")
            ch_items.append({
                "cat": cat, "source": f"{prefix} @{ch}", "time": ts,
                "title": text[:120], "desc": desc, "link": f"https://t.me/{post.group(1)}",
            })
        if oldest is None or oldest < since or not min_id:
            break
        url = f"https://t.me/s/{ch}?before={min_id}"
        time.sleep(0.5)
    ch_items.sort(key=lambda x: x["time"], reverse=True)
    return ch_items[:TG_MAX_ITEMS], err


def fetch_calendar(state):
    cache = state.get("calendar") or {}
    fresh = cache.get("ts") and datetime.now(timezone.utc) - datetime.fromisoformat(cache["ts"]) < timedelta(hours=CALENDAR_TTL_H)
    if not fresh:
        try:
            cache = {"ts": datetime.now(timezone.utc).isoformat(),
                     "data": json.loads(http_get(sources.CALENDAR_URL))}
            state["calendar"] = cache
        except Exception:
            if not cache.get("data"):
                raise
            print("Календарь: беру сохранённую копию", file=sys.stderr)
    data = cache["data"]
    events = []
    for ev in data:
        if ev.get("country") not in sources.CALENDAR_COUNTRIES:
            continue
        if ev.get("impact") not in sources.CALENDAR_IMPACT:
            continue
        ev = dict(ev)
        ev["dt"] = datetime.fromisoformat(ev["date"]).astimezone(MSK)
        events.append(ev)
    return events


def format_event(ev):
    parts = [f'{ev["dt"]:%d.%m %H:%M} МСК — {ev["title"]}']
    if ev.get("forecast"):
        parts.append(f'прогноз {ev["forecast"]}')
    if ev.get("previous"):
        parts.append(f'прошлое {ev["previous"]}')
    return ", ".join(parts)


# ---------- котировки ----------

QUOTE_NAMES = {
    "^GSPC": "S&P 500", "^IXIC": "Nasdaq", "^DJI": "Dow Jones", "^RUT": "Russell 2000", "^VIX": "VIX (страх)",
    "^TNX": "10-летние, %", "BZ=F": "Brent", "CL=F": "WTI", "GC=F": "Золото", "SI=F": "Серебро", "NG=F": "Газ (США)",
    "DX-Y.NYB": "Индекс доллара", "EURUSD=X": "EUR/USD", "RUB=X": "USD/RUB",
    "NVDA": "Nvidia", "AAPL": "Apple", "TSLA": "Tesla", "MSFT": "Microsoft", "COIN": "Coinbase", "MSTR": "Strategy",
    "bitcoin": "BTC", "ethereum": "ETH", "solana": "SOL", "ripple": "XRP", "binancecoin": "BNB",
    "the-open-network": "TON", "dogecoin": "DOGE",
    "IMOEX": "Индекс МосБиржи", "RTSI": "Индекс РТС", "RGBI": "Гособлигации (RGBI)",
    "SBER": "Сбербанк", "GAZP": "Газпром", "LKOH": "Лукойл", "YDEX": "Яндекс", "ROSN": "Роснефть", "T": "Т-Технологии",
    "VTBR": "ВТБ", "GMKN": "Норникель", "NVTK": "Новатэк", "MGNT": "Магнит", "PLZL": "Полюс", "USDRUB_CBR": "Доллар (курс ЦБ)",
}
# Группы для приложения (порядок = порядок на экране)
QUOTE_GROUPS = {
    "crypto": ["bitcoin", "ethereum", "solana", "ripple", "binancecoin", "the-open-network", "dogecoin"],
    "indices": ["^GSPC", "^IXIC", "^DJI", "^RUT"],
    "stocks": ["NVDA", "AAPL", "TSLA", "MSFT", "COIN", "MSTR"],
    "commodities": ["BZ=F", "CL=F", "GC=F", "SI=F", "NG=F"],
    "fx": ["DX-Y.NYB", "EURUSD=X", "RUB=X", "^TNX", "^VIX"],
    "ru": ["IMOEX", "RTSI", "RGBI", "USDRUB_CBR", "SBER", "GAZP", "LKOH", "YDEX", "ROSN", "T", "VTBR", "GMKN", "NVTK", "MGNT", "PLZL"],
}
YAHOO = [s for g in ("indices", "stocks", "commodities", "fx") for s in QUOTE_GROUPS[g]]
CG_IDS = QUOTE_GROUPS["crypto"]


def _yahoo(symbol):
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/"
           + urllib.parse.quote(symbol) + "?range=1d&interval=1d")
    meta = json.loads(http_get(url))["chart"]["result"][0]["meta"]
    price, prev = meta["regularMarketPrice"], meta["chartPreviousClose"]
    return {"price": price, "chg": (price / prev - 1) * 100 if prev else None}


def fetch_moex(q):
    """Мосбиржа (ISS, без ключа) и официальный курс ЦБ."""
    base = "https://iss.moex.com/iss/engines/stock/markets"
    try:
        d = json.loads(http_get(f"{base}/index/boards/SNDX/securities.json?securities=IMOEX,RGBI&iss.meta=off&iss.only=marketdata&marketdata.columns=SECID,CURRENTVALUE,LASTVALUE,LASTCHANGEPRC"))
        for sec, cur, last, chg in d["marketdata"]["data"]:
            q[sec] = {"price": cur or last, "chg": chg}
        d = json.loads(http_get(f"{base}/index/boards/RTSI/securities.json?securities=RTSI&iss.meta=off&iss.only=marketdata&marketdata.columns=SECID,CURRENTVALUE,LASTCHANGEPRC"))
        for sec, cur, chg in d["marketdata"]["data"]:
            q[sec] = {"price": cur, "chg": chg}
        shares = [k for k in QUOTE_GROUPS["ru"] if k not in ("IMOEX", "RTSI", "RGBI", "USDRUB_CBR")]
        d = json.loads(http_get(f"{base}/shares/boards/TQBR/securities.json?securities={','.join(shares)}&iss.meta=off&iss.only=marketdata,securities"
                                "&marketdata.columns=SECID,LAST,LASTTOPREVPRICE&securities.columns=SECID,PREVPRICE"))
        prev = {r[0]: r[1] for r in d["securities"]["data"]}
        for sec, last, chg in d["marketdata"]["data"]:
            price = last or prev.get(sec)
            if price:
                q[sec] = {"price": price, "chg": chg if last else 0.0}
    except Exception as e:
        print("Мосбиржа:", e, file=sys.stderr)
    try:
        v = json.loads(http_get("https://www.cbr-xml-daily.ru/daily_json.js"))["Valute"]["USD"]
        q["USDRUB_CBR"] = {"price": v["Value"], "chg": (v["Value"] / v["Previous"] - 1) * 100}
    except Exception as e:
        print("Курс ЦБ:", e, file=sys.stderr)


def fetch_markets():
    """Котировки для таблиц. Что не скачалось — просто пропускаем."""
    q = {}
    fetch_moex(q)
    def one(sym):
        try:
            return _yahoo(sym)
        except Exception as e:
            print(f"Котировка {sym}: {e}", file=sys.stderr)
    for sym, v in zip(YAHOO, pmap(one, YAHOO)):
        if v:
            q[sym] = v
    try:
        cg = json.loads(http_get("https://api.coingecko.com/api/v3/simple/price"
                                 "?ids=" + ",".join(CG_IDS) + "&vs_currencies=usd&include_24hr_change=true"))
        for k, v in cg.items():
            q[k] = {"price": v["usd"], "chg": v.get("usd_24h_change")}
        g = json.loads(http_get("https://api.coingecko.com/api/v3/global"))["data"]
        q["crypto_total"] = {"cap": g["total_market_cap"]["usd"], "chg": g["market_cap_change_percentage_24h_usd"],
                             "btc_dom": g["market_cap_percentage"]["btc"]}
    except Exception as e:
        print(f"CoinGecko: {e}", file=sys.stderr)
    try:
        f = json.loads(http_get("https://api.alternative.me/fng/?limit=2"))["data"]
        q["fng"] = {"value": int(f[0]["value"]), "prev": int(f[1]["value"])}
    except Exception as e:
        print(f"Fear&Greed: {e}", file=sys.stderr)
    return q


def _num(x, digits=2):
    s = f"{x:,.{digits}f}".replace(",", " ").replace(".", ",")
    return s


def _chg(c):
    if c is None:
        return ""
    arrow = "▲" if c >= 0 else "▼"
    return f"{arrow} {'+' if c >= 0 else '−'}{_num(abs(c))}%"


FNG_RU = [(25, "сильный страх"), (45, "страх"), (55, "нейтрально"), (75, "жадность"), (101, "сильная жадность")]


def quote_lines(keys, q):
    lines = []
    for k in keys:
        v = q.get(k)
        if not v:
            continue
        if k == "crypto_total":
            lines.append(f"Капитализация рынка  <b>${_num(v['cap'] / 1e12)} трлн</b>  {_chg(v['chg'])}")
            lines.append(f"Доля BTC  <b>{_num(v['btc_dom'], 1)}%</b>")
        elif k == "fng":
            label = next(t for lim, t in FNG_RU if v["value"] < lim)
            lines.append(f"Страх и жадность  <b>{v['value']}</b> ({label}, вчера {v['prev']})")
        else:
            digits = 4 if k == "EURUSD=X" else (3 if k == "^TNX" else 2)
            price = v["price"]
            if k in ("bitcoin", "^GSPC", "^IXIC", "^DJI") or price > 1000:
                digits = 0
            lines.append(f"{QUOTE_NAMES[k]}  <b>{_num(price, digits)}</b>  {_chg(v['chg'])}")
    return lines


def quotes_for_ai(q):
    out = []
    for k, v in q.items():
        if k == "crypto_total":
            out.append(f"Капитализация крипторынка ${v['cap'] / 1e12:.2f} трлн ({v['chg']:+.2f}% за 24ч), доля BTC {v['btc_dom']:.1f}%")
        elif k == "fng":
            out.append(f"Индекс страха и жадности крипты {v['value']} (вчера {v['prev']})")
        elif k == "^TNX" and v.get("chg") is not None:
            bp = v["price"] * v["chg"] / (100 + v["chg"]) * 100  # изменение доходности в базисных пунктах
            out.append(f"Доходность 10-летних облигаций США: {v['price']:.3f}% ({bp:+.1f} б.п. за день)")
        else:
            chg = f" ({v['chg']:+.2f}% за день)" if v.get("chg") is not None else ""
            out.append(f"{QUOTE_NAMES[k]}: {v['price']}{chg}")
    return out


# ---------- ИИ ----------

# Модели с исчерпанной ДНЕВНОЙ квотой (429 с «PerDay»/«per-day») больше не зовём до её сброса — полночь
# по Калифорнии, список хранится в state. 14.10 каждый вызов тратил ~50 с на 3 модели с исчерпанной квотой.
# Минутные 429 — как раньше: пауза и повтор.
AI_DEAD = {"day": set()}
GEMINI_MODELS = "gemini-3.5-flash,gemini-3.8-flash,gemini-flash-latest,gemini-flash-lite-latest"
AI_CALLS = {}


def _quota_day():
    return datetime.now(ZoneInfo("America/Los_Angeles")).date().isoformat()


def ai_dead_load(state):
    d = state.get("ai_dead") or {}
    if d.get("date") == _quota_day():
        AI_DEAD["day"] = set(d.get("models", []))


def ai_dead_save(state):
    state["ai_dead"] = {"date": _quota_day(), "models": sorted(AI_DEAD["day"])}


def _day_quota_over(model, err_text):
    if "PerDay" not in err_text and "per-day" not in err_text:
        return False
    AI_DEAD["day"].add(model)
    print(f"ИИ: {model} — дневная квота исчерпана, до сброса не зову", file=sys.stderr)
    return True


def _alive(model):
    return model not in AI_DEAD["day"]


def gemini_all_dead():
    """Все модели Gemini исчерпали дневную квоту — работаем на запасном OpenRouter (50 запросов в сутки)."""
    models = os.environ.get("GEMINI_MODEL") or GEMINI_MODELS
    return all(not _alive(m.strip()) for m in models.split(",") if m.strip())


def has_ai_key():
    return bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("OPENROUTER_API_KEY"))


def ask_ai(system, user):
    """Gemini, если есть его ключ; если Gemini недоступен (кончился дневной лимит 429, перегрузка 503) — OpenRouter,
    если есть его ключ (секрет OPENROUTER_API_KEY). 13.10 сборщик стоял ~15 ч из-за исчерпанной квоты Gemini без запасного."""
    if os.environ.get("GEMINI_API_KEY"):
        try:
            return ask_gemini(system, user)
        except RuntimeError as e:
            if not os.environ.get("OPENROUTER_API_KEY"):
                raise
            print(f"Gemini недоступен ({str(e)[:120]}) — переключаюсь на OpenRouter", file=sys.stderr)
    return ask_openrouter(system, user)


def ask_openrouter(system, user):
    """Бесплатные модели бывают перегружены — пробуем по очереди из списка через запятую."""
    key = os.environ["OPENROUTER_API_KEY"].strip()
    if not key.isascii() or not key.startswith("sk-or-"):  # 15.10 в секрете оказалось слово «ключ» вместо ключа
        raise RuntimeError("секрет OPENROUTER_API_KEY — не ключ OpenRouter (должен начинаться с sk-or-)")
    models = os.environ.get("OPENROUTER_MODEL") or \
        "google/gemma-4-31b-it:free,nvidia/nemotron-3-super-120b-a12b:free"
    last_err = None
    for model in [m.strip() for m in models.split(",") if m.strip() and _alive(m.strip())]:
        body = {
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0.3,
        }
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/chat/completions", data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        )
        for attempt in range(2):
            try:
                with urllib.request.urlopen(req, timeout=180) as r:
                    data = json.loads(r.read())
                text = data["choices"][0]["message"]["content"].strip()
                if text:
                    print(f"ИИ: {model}", file=sys.stderr)
                    AI_CALLS[model] = AI_CALLS.get(model, 0) + 1
                    return text
            except urllib.error.HTTPError as e:
                raw = e.read().decode("utf-8", "replace")
                last_err = f"{e.code} {raw[:300]!r}"
                print(f"OpenRouter {model}: ошибка {last_err}", file=sys.stderr)
                if e.code == 429 and _day_quota_over(model, raw):
                    break
                time.sleep(10)
            except Exception as e:
                last_err = e
                print(f"OpenRouter {model}: ошибка {e}", file=sys.stderr)
                time.sleep(10)
    raise RuntimeError(f"Все модели OpenRouter недоступны: {last_err or 'дневная квота исчерпана'}")


def ask_gemini(system, user):
    """Бесплатные модели бывают перегружены (503) — пробуем по очереди из списка через запятую."""
    key = os.environ["GEMINI_API_KEY"]
    models = os.environ.get("GEMINI_MODEL") or GEMINI_MODELS
    body = json.dumps({
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {"temperature": 0.3},
    }).encode()
    last_err = None
    for model in [m.strip() for m in models.split(",") if m.strip() and _alive(m.strip())]:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        req = urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/json", "x-goog-api-key": key})
        for attempt in range(2):
            try:
                with urllib.request.urlopen(req, timeout=180) as r:
                    data = json.loads(r.read())
                parts = data["candidates"][0]["content"]["parts"]
                text = "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
                if text:
                    print(f"ИИ: {model}", file=sys.stderr)
                    AI_CALLS[model] = AI_CALLS.get(model, 0) + 1
                    return text
            except urllib.error.HTTPError as e:
                raw = e.read().decode("utf-8", "replace")
                last_err = f"{e.code} {raw[:300]!r}"
                if e.code == 429 and _day_quota_over(model, raw):  # повтор через 8 с не поможет
                    print(f"Gemini {model}: ошибка {last_err}", file=sys.stderr)
                    break
            except Exception as e:
                last_err = e
            print(f"Gemini {model}: ошибка {last_err}", file=sys.stderr)
            time.sleep(8)
    raise RuntimeError(f"Все модели Gemini недоступны: {last_err or 'дневная квота исчерпана'}")


# ---------- Telegram ----------

def split_message(text, limit=4000):
    """Режем по строкам; если разрез внутри цитаты — закрываем её и открываем в следующей части."""
    chunks, cur, open_q = [], "", None
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > limit and cur:
            if open_q:
                cur += "</blockquote>"
            chunks.append(cur)
            cur = open_q or ""
        cur += line + "\n"
        for tag in re.findall(r"<blockquote(?: expandable)?>|</blockquote>", line):
            open_q = None if tag == "</blockquote>" else tag
    if cur.strip():
        chunks.append(cur)
    return chunks


ALLOWED_TAG = re.compile(r'(</?(?:b|i|u)>|<blockquote(?: expandable)?>|</blockquote>|<a href="[^"<>]*">|</a>)')


def tg_html(text):
    """Приводит ответ ИИ к HTML, который принимает Telegram: только <b>, <i>, <u>, <blockquote>, <a>, остальное экранируем."""
    text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text.strip())
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"<br\s*/?>", "\n", text)
    text = text.replace("<strong>", "<b>").replace("</strong>", "</b>")
    text = text.replace("<em>", "<i>").replace("</em>", "</i>")
    out = []
    for part in ALLOWED_TAG.split(text):
        if ALLOWED_TAG.fullmatch(part):
            out.append(part.replace("&", "&amp;") if part.startswith("<a ") else part)
        else:
            part = re.sub(r"&(?!(?:amp|lt|gt|quot|#\d+);)", "&amp;", part)
            out.append(part.replace("<", "&lt;").replace(">", "&gt;"))
    return "".join(out)


def send_telegram(text):
    text = tg_html(text)
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat = os.environ["TELEGRAM_CHAT_ID"]
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    for chunk in split_message(text):
        payload = {"chat_id": chat, "text": chunk, "parse_mode": "HTML",
                   "disable_web_page_preview": True}
        try:
            _post(url, payload)
        except urllib.error.HTTPError as e:
            print(f"Telegram: {e.code} {e.read()[:300]!r}", file=sys.stderr)
            # если ИИ сломал разметку — отправляем без неё, чтобы пост не потерялся
            print(f"Telegram HTML не принят ({e}), отправляю текстом", file=sys.stderr)
            payload.pop("parse_mode")
            payload["text"] = clean(chunk.replace("\n", " ⏎ ")).replace(" ⏎ ", "\n")
            _post(url, payload)


def _post(url, payload):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def make_outlook(user, events):
    """«Общая картина рынка»: цепочка причин + базовый сценарий на неделю по 7 активам (JSON)."""
    week = [e for e in events if e["dt"] <= (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()]
    cal = "\n".join(f'{datetime.fromisoformat(e["dt"]).astimezone(MSK):%d.%m %H:%M} МСК — {e["title"]}'
                    + (f' (прогноз {e["forecast"]})' if e.get("forecast") else "") for e in week)
    try:
        raw = ask_ai(prompts.OUTLOOK, user + "\n\nСОБЫТИЯ БЛИЖАЙШЕЙ НЕДЕЛИ:\n" + cal)
        raw = re.sub(r"^```(?:json)?|```$", "", raw.strip()).strip()
        data = json.loads(raw[raw.index("{"):raw.rindex("}") + 1])
        drivers = []
        for d in data.get("drivers", [])[:8]:
            d["impacts"] = [i for i in d.get("impacts", []) if i.get("expect") in ("up", "down", "flat")][:3]
            if d.get("news") and d["impacts"]:
                drivers.append(d)
        data["drivers"] = drivers
        data["at"] = datetime.now(timezone.utc).isoformat()
        return data if drivers else None
    except Exception as e:
        print("Общая картина не получилась:", e, file=sys.stderr)
        return None


def push_to_bot(edition, blocks, meta=None):
    """Отдать готовые блоки серверу бота — он разошлёт их каждому в его время. meta — данные для приложения."""
    url, secret = os.environ.get("INGEST_URL"), os.environ.get("INGEST_SECRET")
    if not (url and secret):
        return
    req = urllib.request.Request(url, data=json.dumps({"edition": edition, "blocks": blocks, "meta": meta or {}}).encode(),
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {secret}",
                                          "User-Agent": UA})  # без UA Cloudflare отвечает 403 (1010)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            print("Сервер бота:", r.read().decode(), file=sys.stderr)
    except Exception as e:
        print("Сервер бота недоступен:", e, file=sys.stderr)


SENT_MAP = {"🟢": "pos", "🔴": "neg", "⚪": "neu"}


def parse_news(block_key, html_text):
    """Достаём новости из блока: «1️⃣ флаг <b>заголовок</b>» + <blockquote>🟢 текст — <a href>источник</a></blockquote>."""
    out = []
    pat = re.compile(r"(?:^|\n)(?:❗️\s*)?\d\ufe0f?\u20e3\s*(.*?)<b>(.*?)</b>[^\n]*\n\s*<blockquote>(.*?)</blockquote>", re.S)
    for m in pat.finditer(html_text):
        prefix, title, body = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
        sent = next((v for k, v in SENT_MAP.items() if body.startswith(k)), "neu")
        link = re.search(r'<a href="([^"]+)">([^<]*)</a>', body)
        text = re.sub(r'\s*—\s*<a href="[^"]+">[^<]*</a>\s*$', "", body)
        text = re.sub(r"^[🟢🔴⚪]\ufe0f?\s*", "", text)
        text = re.sub(r"</?(?!u>|/u>)[^>]+>", "", text)  # оставляем только <u>
        out.append({"block": block_key, "icon": clean(prefix)[:8], "title": clean(title), "text": text.strip(),
                    "sent": sent, "url": link.group(1) if link else "", "source": link.group(2) if link else ""})
    return out


def publish_card(html, card_url):
    """В канал: одно сообщение — карточка сверху (превью) и текст."""
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat):
        print("----- выпуск -----\n" + html, file=sys.stderr)
        return
    payload = {"chat_id": chat, "text": tg_html(html), "parse_mode": "HTML"}
    if card_url:
        payload["link_preview_options"] = {"url": card_url, "show_above_text": True, "prefer_large_media": True}
    try:
        _post(f"https://api.telegram.org/bot{token}/sendMessage", payload)
    except Exception as e:
        print("Канал: не отправлено", e, file=sys.stderr)


def fresh_items(now, minutes=60):
    items, _ = fetch_news(now - timedelta(minutes=minutes))
    tg_items, _ = fetch_telegram(now - timedelta(minutes=minutes), sources.TG_CHANNELS)
    x_items, _ = fetch_telegram(now - timedelta(minutes=minutes), sources.X_MIRRORS, cat="x", prefix="X")
    return sorted(items + tg_items + x_items, key=lambda x: x["time"], reverse=True)


def run_breaking(state, now, dry):
    """Днём каждые 30 минут: важная новость — отдельным сообщением («🌤 Главное сейчас» или «⚡ Срочно»)."""
    if not has_ai_key():
        return
    last = state.get("breaking_ts")
    since = datetime.fromisoformat(last) if last else now - timedelta(minutes=35)
    minutes = min(60, max(35, int((now - since).total_seconds() // 60)))
    if os.environ.get("GEMINI_API_KEY") and gemini_all_dead() and now.minute < 30:
        # на запасном ИИ проверяем раз в час, а не в 30 мин: 50 запросов в сутки (окно ниже само вырастет до 60 мин)
        print("Новости дня: Gemini исчерпан — экономлю запасной ИИ, проверю в :30", file=sys.stderr)
        return
    allit = fresh_items(now, minutes)
    seen = set(state.get("news_seen", []))
    print(f"Новости дня: свежих {len(allit)}, новых с прошлой проверки {sum(it['link'] not in seen for it in allit)}", file=sys.stderr)
    state["news_seen"] = [it["link"] for it in allit][:300]
    if allit and all(it["link"] in seen for it in allit):  # ИИ уже видел ровно эти новости — не зовём
        state["breaking_ts"] = now.isoformat()
        return
    edition.run_news_now(ask_ai, allit, state, now, publish_card if not dry else (lambda h, u: publish(h, True)))
    state["breaking_ts"] = now.isoformat()


def publish(text, dry):
    if dry:
        print("\n" + "=" * 60 + "\n" + text + "\n" + "=" * 60)
    else:
        print("----- отправлено -----\n" + text, file=sys.stderr)  # видно в логе GitHub
        send_telegram(text)


# ---------- сценарии ----------

def today_lines(events, local, ru_first=False, tzmin=180):
    """«Что ждём сегодня» для утреннего обзора: события дня (сначала сильные), прогноз.
    Время — по поясу читателя (tzmin, минуты от UTC); для Москвы пишем «МСК»."""
    tz = timezone(timedelta(minutes=tzmin))
    flag = {"ru": "🇷🇺", "us": "🇺🇸", "oil": "🛢", "gold": "🥇", "crypto": "₿"}
    day = [e for e in events if datetime.fromisoformat(e["dt"]).astimezone(tz).date() == local.date()
           and datetime.fromisoformat(e["dt"]) >= datetime.now(timezone.utc) - timedelta(minutes=30)]
    day.sort(key=lambda e: (0 if e.get("impact") == "High" else 1, 0 if (ru_first and e.get("region") == "ru") else 1, e["dt"]))
    out = []
    for e in sorted(day[:5], key=lambda e: e["dt"]):
        t = datetime.fromisoformat(e["dt"]).astimezone(tz)
        fc = f' — прогноз <b>{e["forecast"]}</b>' if e.get("forecast") else ""
        out.append(f'{t:%H:%M}{" МСК" if tzmin == 180 else ""} {flag.get(e.get("region"), "")} {"<b>" + e["title"] + "</b>" if e.get("impact") == "High" else e["title"]}{fc}')
    return out


def run_morning(state, now, dry, tzmin):
    """Утренний обзор для часового пояса tzmin (минуты от UTC): сервер запускает за полчаса до утра читателей этого пояса.
    Новости за ночь (12 часов), свежие цены, события дня по местному времени. Сохраняется как блоки morning_<tz> и morning_<tz>_ru."""
    if not has_ai_key():
        return
    tz = timezone(timedelta(minutes=tzmin))
    local = now.astimezone(tz)
    items = fresh_items(now, 12 * 60)
    q = fetch_markets()
    try:
        events = calendar_future.build(fetch_calendar(state), horizon_days=2)
    except Exception as e:
        print("Календарь недоступен:", e, file=sys.stderr)
        events = []
    print(f"Утро UTC{tzmin / 60:+g}: новостей {len(items)}, котировок {len(q)}", file=sys.stderr)
    blocks = edition.build_morning(ask_ai, items, now, tzmin,
                                   quote_lines(["^GSPC", "^IXIC", "bitcoin", "BZ=F", "GC=F", "IMOEX", "USDRUB_CBR"], q),
                                   quote_lines(["IMOEX", "RGBI", "USDRUB_CBR", "SBER", "BZ=F", "GC=F"], q),
                                   today_lines(events, local, tzmin=tzmin), today_lines(events, local, ru_first=True, tzmin=tzmin),
                                   publish_card if not dry else (lambda h, u: publish(h, True)))
    if blocks and not dry and "--test" not in sys.argv:
        push_to_bot(f"morning {tzmin} {local:%Y-%m-%d}", blocks)


def run_digest(state, now, dry):
    last = state.get("last_digest_ts")
    since = datetime.fromisoformat(last) if last else now - timedelta(hours=18)
    since = max(since, now - timedelta(hours=DIGEST_WINDOW_MAX_H))

    items, errors = fetch_news(since)
    tg_items, tg_errors = fetch_telegram(since, sources.TG_CHANNELS)
    x_items, x_errors = fetch_telegram(since, sources.X_MIRRORS, cat="x", prefix="X")
    tg_items += x_items
    tg_errors += x_errors
    items = sorted(items + tg_items, key=lambda x: x["time"], reverse=True)
    errors += tg_errors
    for err in errors:
        print("Лента недоступна:", err, file=sys.stderr)
    try:
        today = [e for e in fetch_calendar(state) if e["dt"].date() == now.astimezone(MSK).date()]
    except Exception as e:
        print("Календарь недоступен:", e, file=sys.stderr)
        today = []

    try:
        upcoming = [e for e in fetch_calendar(state) if now <= e["dt"] <= now + timedelta(days=4)]
    except Exception:
        upcoming = []
    q = fetch_markets()
    local = now.astimezone(MSK)

    lines = [f"Сейчас: {local:%d.%m.%Y %H:%M} МСК", "", "КОТИРОВКИ:"] + quotes_for_ai(q)
    lines += ["", "КАЛЕНДАРЬ США НА СЕГОДНЯ:"] + ([format_event(e) for e in today] or ["важных событий нет"])
    lines += ["", "КАЛЕНДАРЬ США НА БЛИЖАЙШИЕ ДНИ:"] + ([format_event(e) for e in upcoming] or ["важных событий нет"])
    lines += ["", f"НОВОСТИ С {since.astimezone(MSK):%d.%m %H:%M} МСК ({len(items)} шт.):"]
    for it in items:
        lines.append(f'[{it["cat"]}] {it["source"]} | {it["time"].astimezone(MSK):%H:%M} | '
                     f'{it["title"]} — {it["desc"]} | {it["link"]}')
    user = "\n".join(lines)
    print(f"Собрано новостей: {len(items)}, событий сегодня: {len(today)}, котировок: {len(q)}", file=sys.stderr)

    if dry and not has_ai_key():
        publish("[нет ключа ИИ — показываю, что уйдёт в ИИ]\n\n" + user[:6000], dry)
        return

    date = f"{local:%d.%m.%Y}"
    out = []  # блоки для сервера бота: [{block, html: [части сообщения]}]

    def emit(block, text):  # блоки идут в приложение (лента), в чат — выпуск-карточка ниже
        out.append({"block": block, "html": split_message(tg_html(text))})
        if dry:
            publish(text, dry)

    emit("header", f"<b>☀️ #ВЫЖИМКА | {date}</b>\n<i>Рынок США · Крипта · Нефть, золото, доллар · Что говорят · Итог дня</i>")
    for block in prompts.BLOCKS:
        head = f"<b>{block['title']} | {date}</b>"
        table = quote_lines(block["quotes"], q)
        body = ask_ai(prompts.COMMON + "\n" + block["focus"], user)
        text = head + "\n\n"
        if table:
            text += "<blockquote>" + "\n".join(table) + "</blockquote>\n\n"
        emit(block["key"], text + body)
        time.sleep(2)
    emit("summary", f"<b>🧭 #ИТОГ_ДНЯ | {date}</b>\n\n" + edition.not_advice(ask_ai(prompts.SUMMARY, user)))
    news = []
    for b in out:
        news += parse_news(b["block"], "\n".join(b["html"]))
    for n in news:
        n["ts"] = now.isoformat()  # приложение хранит ленту 7 дней и показывает дату
    try:
        week = fetch_calendar(state)
    except Exception:
        week = []
    try:
        events = calendar_future.build(week)  # неделя FF + 7 дней Nasdaq + графики ФРС/BLS/BEA + отчёты компаний
    except Exception as e:
        print("Календарь на 60 дней не собран:", e, file=sys.stderr)
        events = [{"dt": e["dt"].isoformat(), "title": e["title"], "forecast": e.get("forecast", ""),
                   "previous": e.get("previous", ""), "impact": e.get("impact", "")} for e in week]
    group_of = {k: g for g, keys in QUOTE_GROUPS.items() for k in keys}
    quotes = [{"key": k, "name": QUOTE_NAMES.get(k, k), "group": group_of.get(k, ""), **v} for k, v in q.items()]
    outlook = make_outlook(user, events)
    print(f"Для приложения: новостей {len(news)}, событий {len(events)}, картина: {'да' if outlook else 'нет'}", file=sys.stderr)
    if not dry:
        meta = {"news": news, "events": events, "quotes": quotes}
        try:
            mac = macro.build(state)
            nxt = next((d.rstrip("*") for d in calendar_future.FOMC if d.rstrip("*") >= f"{local:%Y-%m-%d}"), None)
            if nxt:
                mac["next_fomc"] = nxt
            nxt_cb = next((d for d in calendar_future.CBR if d >= f"{local:%Y-%m-%d}"), None)
            if nxt_cb:
                mac["next_cbr"] = nxt_cb
            meta["macro"] = mac
        except Exception as e:
            print("Ключевые показатели:", e, file=sys.stderr)
        if outlook:
            meta["outlook"] = outlook
        try:  # выпуск: карточка + 4 главные новости целиком
            ed = edition.build_edition(ask_ai, news, "\n".join(quotes_for_ai(q)), now, len(news),
                                       qlines=quote_lines(["^GSPC", "^IXIC", "bitcoin", "BZ=F", "GC=F", "IMOEX", "USDRUB_CBR"], q), today=today_lines(events, local))
            if ed:
                card_id, ed_html, card_url = ed
                out.append({"block": "edition", "html": [card_id, tg_html(ed_html), "#today"]})
                publish_card(ed_html, card_url)
        except Exception as e:
            print("Выпуск не собран:", e, file=sys.stderr)
        try:  # отдельный выпуск для тех, кто выбрал Россию без США
            ed = edition.build_edition(ask_ai, news, "\n".join(quotes_for_ai(q)), now, len(news), focus="ru",
                                       qlines=quote_lines(["IMOEX", "RGBI", "USDRUB_CBR", "SBER", "BZ=F", "GC=F"], q), today=today_lines(events, local, ru_first=True))
            if ed:
                card_id, ed_html, _ = ed
                out.append({"block": "edition_ru", "html": [card_id, tg_html(ed_html), "#feed"]})
                print("Выпуск «Россия» собран", file=sys.stderr)
        except Exception as e:
            print("Выпуск «Россия» не собран:", e, file=sys.stderr)
        push_to_bot(f"{local:%Y-%m-%d %H:%M}", out, meta)
    state["last_digest_ts"] = now.isoformat()


def run_events(state, now, dry):
    try:
        events = fetch_calendar(state)
    except Exception as e:
        print("Календарь недоступен:", e, file=sys.stderr)
        return
    for ev in events:
        minutes = (ev["dt"] - now).total_seconds() / 60
        key = f'{ev["date"]}|{ev["title"]}'
        if ev.get("impact") != "High":
            continue  # анонсы — только для самых важных событий
        if not (0 < minutes <= EVENT_LEAD_MIN) or key in state["sent_events"]:
            continue
        system = prompts.EVENT_SYSTEM.replace("{minutes}", str(int(minutes)))
        user = format_event(ev)
        if dry and not has_ai_key():
            publish("[нет ключа] Анонс: " + user, dry)
        else:
            publish(edition.not_advice(ask_ai(system, user)), dry)
        state["sent_events"].append(key)


def push_result(payload):
    """Отдать результат события серверу: он сохранит его для приложения и разошлёт в бот."""
    url, secret = os.environ.get("INGEST_URL"), os.environ.get("INGEST_SECRET")
    if not (url and secret):
        return
    req = urllib.request.Request(url.replace("/ingest", "/result"), data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {secret}", "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            print("Результат отправлен:", r.read().decode()[:200], file=sys.stderr)
    except Exception as e:
        print("Сервер бота недоступен:", e, file=sys.stderr)


VERDICT = {"better": ("🟢", "Лучше ожиданий для рынков"), "worse": ("🔴", "Хуже ожиданий для рынков"), "inline": ("⚪", "В рамках ожиданий")}


def run_results(state, now, dry):
    """Вышли данные (CPI, PPI, NFP, решение ФРС…) или отчёт крупной компании — разбираем итог."""
    done = state.setdefault("results_done", [])
    for r in calendar_future.released(now):
        key = r["key"]
        if key in done:
            continue
        if r["kind"] == "earnings":
            v = "better" if r["surprise"] > 0 else "worse" if r["surprise"] < 0 else "inline"
            res = {"verdict": v, "headline": f'EPS {r["eps"]} при прогнозе {r["forecast"]} (сюрприз {r["surprise"]:+.1f}%)',
                   "meaning": "Компания заработала больше, чем ждали аналитики." if v == "better" else
                              "Компания заработала меньше, чем ждали аналитики." if v == "worse" else "Прибыль совпала с ожиданиями."}
        else:
            if not has_ai_key():
                continue
            rows = "\n".join(f'{x["name"]}: факт {x["actual"]}, прогноз {x["consensus"] or "—"}, прошлое {x["previous"] or "—"}' for x in r["rows"])
            try:
                raw = ask_ai(prompts.RESULT, f'Событие: {r["title"]}\n{rows}')
                raw = re.sub(r"^```(?:json)?|```$", "", raw.strip()).strip()
                res = json.loads(raw[raw.index("{"):raw.rindex("}") + 1])
                if res.get("verdict") not in VERDICT:
                    raise ValueError("verdict")
            except Exception as e:
                print("Разбор результата не получился:", e, file=sys.stderr)
                continue
        icon, label = VERDICT[res["verdict"]]
        html = (f'{icon} <b>Вышло: {r["title"]}</b>\n'
                f'<blockquote>📊 {res["headline"]}\n{icon} <b>{label}</b></blockquote>\n'
                f'{res.get("meaning", "")}\n\n<i>Не является инвестиционной рекомендацией.</i>')
        payload = {"key": key, "title": r["title"], "kind": r["kind"], "dt": r["dt"], "verdict": res["verdict"], "impact": r.get("impact", "Medium"),
                   "headline": res["headline"], "meaning": res.get("meaning", ""), "html": tg_html(html)}
        print("Результат:", r["title"], res["verdict"], file=sys.stderr)
        if dry:
            publish(html, dry)
        else:
            push_result(payload)
            if payload["impact"] == "High":
                publish(html, dry)  # и в тестовый канал
        done.append(key)
    state["results_done"] = done[-200:]


def due_digest_slot(state, now):
    local = now.astimezone(MSK)
    for slot in os.environ.get("DIGEST_TIMES", "07:30,19:30").split(","):
        h, m = map(int, slot.strip().split(":"))
        start = local.replace(hour=h, minute=m, second=0, microsecond=0)
        key = f"{local:%Y-%m-%d} {slot.strip()}"
        if start <= local < start + timedelta(minutes=SLOT_GRACE_MIN) and key not in state["sent_digests"]:
            return key
    return None


def main():
    args = sys.argv[1:]
    dry = "--dry-run" in args
    mode = next((a for a in args if not a.startswith("--")), "auto")
    now = datetime.now(timezone.utc)
    state = load_state()
    ai_dead_load(state)
    if mode == "ai_check":  # проверка запасного ИИ: Gemini «исчерпан» (только в памяти), образец через OpenRouter — в тестовый канал
        AI_DEAD["day"] |= {m.strip() for m in (os.environ.get("GEMINI_MODEL") or GEMINI_MODELS).split(",")}
        mode = "preview"

    if mode == "digest":
        run_digest(state, now, dry)
    elif mode == "events":
        run_events(state, now, dry)
    elif mode == "results":
        run_results(state, now, dry)
    elif mode == "breaking":
        run_breaking(state, now, dry)
    elif mode.startswith("morning:"):  # утро для пояса: python -m bot.main morning:420  (--test — только в тестовый канал)
        run_morning(state, now, dry, int(mode.split(":")[1]))
        return
    elif mode == "note":  # отправить state/note.html в тестовый канал (списки, пояснения для владельца)
        # несколько сообщений: каждое начинается со строки <!--img:URL--> (картинка над текстом)
        note = (ROOT / "state" / "note.html").read_text(encoding="utf-8")
        parts = re.split(r"^<!--img:(\S+)-->\n?", note, flags=re.M)
        if parts[0].strip():
            publish(parts[0], dry)
        for url, text in zip(parts[1::2], parts[2::2]):
            if dry:
                publish(text, dry)
            else:
                publish_card(text.strip(), url)
                time.sleep(1)
        return
    elif mode == "preview":  # образцы новостных сообщений в тестовый канал — сервер шлёт их так, как видят подписчики
        sample = edition.run_news_now(ask_ai, fresh_items(now, 180), {}, now, None, force=True) if has_ai_key() else None
        print("Сервер бота:", edition._post("/api/preview", {"day": sample}), file=sys.stderr)
        return
    elif mode in ("report", "report_test"):
        from bot import report
        report.run(dry, test=(mode == "report_test"))
        return
    else:
        slot = due_digest_slot(state, now)
        if slot:
            run_digest(state, now, dry)
            state["sent_digests"].append(slot)
        run_events(state, now, dry)
        run_results(state, now, dry)
        if not slot:
            try:
                run_breaking(state, now, dry)
            except Exception as e:
                print("Срочные: ошибка", e, file=sys.stderr)

    if AI_CALLS:
        print(f"ИИ за запуск: {sum(AI_CALLS.values())} {AI_CALLS}", file=sys.stderr)
    if not dry:
        ai_dead_save(state)
        save_state(state)


if __name__ == "__main__":
    main()
