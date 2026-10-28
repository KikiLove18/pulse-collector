"""Бот персональных новостей рынков для трейдеров.

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
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path

import feedparser

from bot import calendar_future, edition, macro, pro, prompts, sources, weekly, pro_day, freech, pro_auto

MSK = timezone(timedelta(hours=3))
ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = ROOT / "state" / "state.json"
UA = "Mozilla/5.0 (market-digest-bot)"

MAX_PER_FEED = 25
MAX_DESC = 280
DIGEST_WINDOW_MAX_H = 24
SLOT_GRACE_MIN = 180      # окно утреннего слота: опоздание GitHub + повторы, если ИИ отказал (15.10 вечерняя выжимка пропала)
LAST_CHANCE_MIN = 150     # после стольких минут от слота — отправляем то, что готово, даже без части блоков
EVENING_GRACE_MIN = 90    # вечером окно короче: поздние рассылки владелец не хочет — не успели, ждём утро
EVENING_LAST_CHANCE_MIN = 60
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
    return {"price": price, "chg": (price / prev - 1) * 100 if prev else None, "ts": meta.get("regularMarketTime")}


WEEKDAY_ACC = ["понедельник", "вторник", "среду", "четверг", "пятницу", "субботу", "воскресенье"]


def stale_day(v):
    """Биржа закрыта (выходные, ночь до открытия): последнее изменение — за прошлый торговый день. Возвращает его дату или None.
    18.10 (вс) в «Итогах дня» ИИ подал пятничное падение Nasdaq как сегодняшнее."""
    if not v.get("ts"):
        return None
    ny = ZoneInfo("America/New_York")
    traded = datetime.fromtimestamp(v["ts"], ny).date()
    return traded if traded < datetime.now(ny).date() else None


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
            d = stale_day(v)  # биржа закрыта — показываем, за какой день изменение
            lines.append(f"{QUOTE_NAMES[k]}  <b>{_num(price, digits)}</b>  {_chg(v['chg'])}" + (f"  <i>{WEEKDAY[d.weekday()]}</i>" if d else ""))
    return lines


def quotes_for_ai(q):
    out = []
    for k, v in q.items():
        if k == "crypto_total":
            out.append(edition.ru_numbers(f"Капитализация крипторынка ${v['cap'] / 1e12:.2f} трлн ({v['chg']:+.2f}% за 24ч), доля BTC {v['btc_dom']:.1f}%"))
        elif k == "fng":
            out.append(f"Индекс страха и жадности крипты {v['value']} (вчера {v['prev']})")
        elif k == "^TNX" and v.get("chg") is not None:
            bp = v["price"] * v["chg"] / (100 + v["chg"]) * 100  # изменение доходности в базисных пунктах
            d = stale_day(v)
            out.append(edition.ru_numbers(f"Доходность 10-летних облигаций США: {v['price']:.3f}% ({bp:+.1f} б.п. "
                                          + (f"за {WEEKDAY_ACC[d.weekday()]} {d:%d.%m})" if d else "за день)")))
        else:
            d = stale_day(v)
            when = f"за {WEEKDAY_ACC[d.weekday()]} {d:%d.%m}, сейчас торгов нет" if d else "за день"
            chg = f" ({v['chg']:+.2f}% {when})" if v.get("chg") is not None else ""
            out.append(edition.ru_numbers(f"{QUOTE_NAMES[k]}: {round(v['price'], 4)}{chg}"))
    return out


# ---------- ИИ ----------

# Модели с исчерпанной ДНЕВНОЙ квотой (429 с «PerDay»/«per-day») больше не зовём до её сброса — полночь
# по Калифорнии, список хранится в state. 14.10 каждый вызов тратил ~50 с на 3 модели с исчерпанной квотой.
# Минутные 429 — как раньше: пауза и повтор.
AI_DEAD = {"day": set()}
GEMINI_MODELS = "gemini-3.5-flash,gemini-3.8-flash,gemini-flash-latest,gemini-flash-lite-latest"
# Бесплатно «большие» модели дают ~5–10 ответов в сутки каждая — берём их только для выжимок (AI_TIER["big"]),
# всё остальное (новость сейчас, лента, события, итоги) — на lite: у неё 38–55 ответов в сутки, дневной лимит не кончался.
GEMINI_LITE = "gemini-flash-lite-latest"
AI_TIER = {"big": False}


def gemini_models():
    return os.environ.get("GEMINI_MODEL") or (GEMINI_MODELS if AI_TIER["big"] else GEMINI_LITE)
AI_CALLS = {}
# Общий лимит времени на ИИ за запуск: 15.10 вечерняя выжимка висела больше часа (тайм-аут 180 с × повторы × 9 вызовов)
# и держала очередь. Вышло время — дальше ИИ не зовём, запуск завершается, следующий слот работает.
AI_RUN_BUDGET = 15 * 60
AI_TIMEOUT = 120
# Бесплатные модели OpenRouter: кто не ответил за 60 с — обычно не ответит вовсе. Ultra — единственная рабочая (23.10),
# ей на полном выпуске нужно больше: 22.10 она 10 раз не уложилась в 120 с
OR_TIMEOUT = {"nvidia/nemotron-3-ultra-550b-a55b:free": 180}
_AI_START = time.time()


def _fetch_json(req, timeout):
    """Запрос с пределом по часам: OpenRouter, пока думает, шлёт пробелы-«пульс», и тайм-аут сокета не срабатывает
    (15.10 один запрос шёл 53 мин). Зависший поток — фоновый, выходу из программы не мешает."""
    box = {}

    def run():
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                box["data"] = json.loads(r.read())
        except BaseException as e:
            box["err"] = e

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise TimeoutError(f"нет ответа за {int(timeout)} с")
    if "err" in box:
        raise box["err"]
    return box["data"]


def _retry_delay(raw):
    """Минутный лимит Gemini: в ответе «retry in 30s» / "retryDelay": "30s" — ждём столько (не больше минуты)."""
    m = re.search(r'retry(?:Delay)?"?:?\s*(?:in\s*)?"?([\d.]+)s', raw, re.I)
    return min(60.0, float(m.group(1)) + 1) if m else 8


def _ai_timeout():
    left = AI_RUN_BUDGET - (time.time() - _AI_START)
    if left <= 5:
        raise RuntimeError("время на ИИ за запуск вышло (15 мин)")
    return min(AI_TIMEOUT, left)


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


# Перегрузка (503), «нет ответа» и 404 провайдера — модель «занята» на 10 мин этого запуска: следующие вызовы её пропускают.
# 23.10 (Инженер): 104 неудачные попытки на 57 ответов — 60 из них 503 Gemini: каждая перегруженная модель пробовалась
# дважды с паузой 8 с и заново в каждом вызове запуска; nemotron-super 16 раз подряд — 404.
AI_BUSY = {}
BUSY_SEC = 600


def _busy(model, why):
    AI_BUSY[model] = time.time() + BUSY_SEC
    print(f"ИИ: {model} — {why}, пропускаю до конца запуска (10 мин)", file=sys.stderr)


def _alive(model):
    return model not in AI_DEAD["day"] and AI_BUSY.get(model, 0) < time.time()


def gemini_all_dead():
    """Все модели Gemini исчерпали дневную квоту — работаем на запасном OpenRouter (50 запросов в сутки)."""
    models = gemini_models()
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
    # ai_probe 23.10: отвечает только nemotron-ultra (68 с); super — 404 (убрана), gemma — 429 у провайдера, dots — >90 с,
    # inkling — только для «агентов» (403), nemotron-3.5 — 502 / >90 с
    models = os.environ.get("OPENROUTER_MODEL") or \
        "nvidia/nemotron-3-ultra-550b-a55b:free,google/gemma-4-31b-it:free,google/gemma-4-26b-a4b-it:free,dots-studio/dots-3-note-preview:free"
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
        timeout = min(_ai_timeout(), OR_TIMEOUT.get(model, 60))  # вне try: «время вышло» прерывает сразу
        try:
            data = _fetch_json(req, timeout)
            if not data.get("choices"):  # ошибка провайдера приходит с кодом 200 — повтор не поможет, дальше по списку
                last_err = f"нет ответа: {json.dumps(data.get('error') or data, ensure_ascii=False)[:300]}"
                print(f"OpenRouter {model}: {last_err}", file=sys.stderr)
                _busy(model, "ошибка провайдера")
                continue
            text = (data["choices"][0]["message"].get("content") or "").strip()  # бывает None у «думающих» моделей
            if text:
                print(f"ИИ: {model}", file=sys.stderr)
                AI_CALLS[model] = AI_CALLS.get(model, 0) + 1
                return text
            last_err = "пустой ответ"
            print(f"OpenRouter {model}: ошибка {last_err}", file=sys.stderr)
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            last_err = f"{e.code} {raw[:300]!r}"
            print(f"OpenRouter {model}: ошибка {last_err}", file=sys.stderr)
            if not (e.code == 429 and _day_quota_over(model, raw)):
                _busy(model, f"ошибка {e.code}")  # 404/400 — модели нет, 429 — лимит у провайдера: повтор не поможет
        except Exception as e:
            last_err = e
            print(f"OpenRouter {model}: ошибка {e}", file=sys.stderr)
            _busy(model, "нет ответа")
    raise RuntimeError(f"Все модели OpenRouter недоступны: {last_err or 'все модели исчерпали квоту или заняты'}")


PROBE_MODELS = ("nvidia/nemotron-3.5-super-vl:free,nvidia/nemotron-3.5-lightning:free,nvidia/nemotron-3-ultra-550b-a55b:free,"
                "google/gemma-4-31b-it:free,google/gemma-4-26b-a4b-it:free,thinkingmachines/inkling:free,"
                "thinkingmachines/inkling-small:free,dots-studio/dots-3-note-preview:free,nvidia/nemotron-3-super-120b-a12b:free")


def run_ai_probe():
    """Какие бесплатные модели OpenRouter годятся в запасные: по одному заданию в формате блока выпуска на модель,
    в журнал — время, длина, русский ли текст. Ничего не публикует. 16.10 gemma-4-31b — 429, nemotron-super — молчал 120 с."""
    key = os.environ["OPENROUTER_API_KEY"].strip()
    news = "\n".join(f"[macro] Reuters | 09:{i:02d} | Нефть Brent {'растёт' if i % 2 else 'дешевеет'} на {i / 10:.1f}% — "
                     f"трейдеры оценивают запасы и решения ОПЕК+ | https://example.com/{i}" for i in range(40))
    user = "КОТИРОВКИ:\nBrent 82,1 (+1,2%)\nWTI 78,4 (+1,0%)\nЗолото 2410 (−0,3%)\n\nНОВОСТИ:\n" + news
    system = prompts.COMMON + "\n" + prompts.BLOCKS[2]["focus"]
    rows = []
    for model in [m.strip() for m in (os.environ.get("PROBE_MODELS") or PROBE_MODELS).split(",") if m.strip()]:
        body = {"model": model, "temperature": 0.3,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        req = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
        t0 = time.time()
        try:
            text = (_fetch_json(req, 90)["choices"][0]["message"].get("content") or "").strip()
            ru = sum("а" <= c.lower() <= "я" for c in text) / max(1, len(text))
            res = f"ОК {len(text)} знаков, кириллица {ru:.0%}" if text else "пустой ответ"
        except urllib.error.HTTPError as e:
            res = f"ошибка {e.code} {e.read().decode('utf-8', 'replace')[:120]!r}"
        except Exception as e:
            res = f"ошибка {str(e)[:120]}"
        rows.append(f"{model}: {time.time() - t0:.0f} с — {res}")
        print(rows[-1], file=sys.stderr)
    print("\nИТОГ ПРОВЕРКИ МОДЕЛЕЙ:\n" + "\n".join(rows))


def ask_gemini(system, user):
    """Бесплатные модели бывают перегружены (503) — пробуем по очереди из списка через запятую."""
    key = os.environ["GEMINI_API_KEY"]
    models = gemini_models()
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
        for attempt in range(3):  # повтор — только после минутного лимита 429
            timeout = _ai_timeout()  # вне try: «время вышло» прерывает все повторы сразу
            try:
                data = _fetch_json(req, timeout)
                parts = data["candidates"][0]["content"]["parts"]
                text = "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
                if text:
                    print(f"ИИ: {model}", file=sys.stderr)
                    AI_CALLS[model] = AI_CALLS.get(model, 0) + 1
                    return text
                last_err = "пустой ответ"
                print(f"Gemini {model}: ошибка {last_err}", file=sys.stderr)
                break
            except urllib.error.HTTPError as e:
                raw = e.read().decode("utf-8", "replace")
                last_err = f"{e.code} {raw[:300]!r}"
                print(f"Gemini {model}: ошибка {last_err}", file=sys.stderr)
                if e.code == 429 and _day_quota_over(model, raw):  # повтор не поможет
                    break
                if e.code == 429 and attempt < 2:  # минутный лимит — ждём, сколько просит Google
                    wait = _retry_delay(raw)
                    print(f"Gemini {model}: минутный лимит, жду {int(wait)} с", file=sys.stderr)
                    time.sleep(wait)
                    continue
                if e.code in (500, 502, 503, 504):  # перегрузка: сразу следующая модель, эту — не трогаем 10 мин
                    _busy(model, f"перегрузка {e.code}")
                break
            except Exception as e:
                last_err = e
                print(f"Gemini {model}: ошибка {last_err}", file=sys.stderr)
                if isinstance(e, TimeoutError):
                    _busy(model, "нет ответа")
                break
    raise RuntimeError(f"Все модели Gemini недоступны: {last_err or 'все модели исчерпали квоту или заняты'}")


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
            part = re.sub(r"&(?!(?:amp|lt|gt|quot|#\d+);)", "&amp;", edition.ru_numbers(part))
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
        data = edition.ru_numbers_deep({**data, "drivers": drivers})
        data["at"] = datetime.now(timezone.utc).isoformat()
        return data if data["drivers"] else None
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
                    "sent": sent, "url": link.group(1) if link else "", "source": sources.src_name(link.group(2)) if link else ""})
    return out


def publish_card(html, card_url, chat=None):
    """В канал: одно сообщение — карточка сверху (превью) и текст. chat — другой канал (по умолчанию тестовый)."""
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), chat or os.environ.get("TELEGRAM_CHAT_ID")
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
    out = edition.run_news_now(ask_ai, allit, state, now, publish_card if not dry else (lambda h, u: publish(h, True)))
    state["breaking_ts"] = now.isoformat()
    if out and out.get("kind") == "urgent" and not dry:  # «⚡ Срочно» → подробный разбор в канал PRO
        card_url = os.environ.get("INGEST_URL", "").replace("/api/ingest", f"/card/{out['card']}.jpg")
        pro.big_event(ask_ai, tg_html, state, now, "urgent", out.get("title") or "Срочная новость",
                      clean(out["html"]), "\n".join(quotes_for_ai(fetch_markets())), card_url)


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


# Предотбор входа выжимки: 15.10 в ИИ уходило 750–840 новостей → минутные 429 у бесплатной lite.
# Квоты по рубрикам; внутри рубрики — по очереди от разных источников, свежие вперёд; дубли заголовков убираем.
DIGEST_QUOTA = {"macro": 30, "crypto": 25, "ru": 25, "telegram": 30, "x": 20, "commodities": 10, "people": 8, "hacks": 5}
DIGEST_OTHER = 10


def preselect(items):
    seen, by_cat = set(), {}
    for it in sorted(items, key=lambda x: x["time"], reverse=True):
        key = re.sub(r"\W+", " ", it["title"].lower()).strip()[:60]
        if not key or key in seen:
            continue
        seen.add(key)
        by_cat.setdefault(it["cat"], {}).setdefault(it["source"], []).append(it)
    out = []
    for cat, srcs in by_cat.items():
        quota = DIGEST_QUOTA.get(cat, DIGEST_OTHER)
        queues = sorted(srcs.values(), key=lambda q: (edition.source_rank(q[0]), -q[0]["time"].timestamp()))
        taken = []
        while len(taken) < quota and any(queues):
            for q in queues:
                if q and len(taken) < quota:
                    taken.append(q.pop(0))
        out += taken
    return sorted(out, key=lambda x: x["time"], reverse=True)


def run_digest(state, now, dry, final=True):
    """Возвращает True, если выпуск отправлен. ИИ отказал на блоке и это не последняя попытка (final=False) —
    сохраняем для приложения ленту, котировки и календарь, а выпуск повторяем в следующем запуске."""
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
    total = len(items)
    items = preselect(items)
    print(f"Предотбор для ИИ: {len(items)} из {total}", file=sys.stderr)

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

    morning = local.hour < 14  # утро — «что было ночью и что ждём сегодня», вечер — итоги дня
    emit("header", (f"<b>☀️ #НОВОСТИ_УТРА | {date}</b>\n<i>Рынок США · Крипта · Нефть, золото, доллар · Что говорят · Что было ночью</i>" if morning else
                    f"<b>🌙 #НОВОСТИ_ДНЯ | {date}</b>\n<i>Рынок США · Крипта · Нефть, золото, доллар · Что говорят · Итоги дня</i>"))
    failed = []
    for block in prompts.BLOCKS:
        head = f"<b>{block['title']} | {date}</b>"
        table = quote_lines(block["quotes"], q)
        try:
            body = ask_ai(prompts.COMMON + "\n" + block["focus"], user)
        except Exception as e:
            print(f"Блок {block['key']} не собран: {str(e)[:150]}", file=sys.stderr)
            failed.append(block["key"])
            continue
        text = head + "\n\n"
        if table:
            text += "<blockquote>" + "\n".join(table) + "</blockquote>\n\n"
        emit(block["key"], text + body)
        time.sleep(6)  # пауза между блоками — меньше минутных 429 у бесплатной модели
    if not failed:  # итог дня — по всем блокам; если ИИ уже отказал, не тратим время
        try:
            title = "🧭 #ГЛАВНОЕ_ЗА_НОЧЬ" if morning else "🧭 #ИТОГИ_ДНЯ"
            prompt = prompts.SUMMARY + (prompts.SUMMARY_MORNING if morning else prompts.SUMMARY_EVENING)
            emit("summary", f"<b>{title} | {date}</b>\n\n" + edition.not_advice(ask_ai(prompt, user)))
        except Exception as e:
            print(f"Итог дня не собран: {str(e)[:150]}", file=sys.stderr)
            failed.append("summary")
    hold = bool(failed) and not final
    if failed:
        print(f"ИИ отказал: {', '.join(failed)} — " + ("выпуск повторю в следующем запуске" if hold else "отправляю то, что готово"), file=sys.stderr)
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
    outlook = None if failed else make_outlook(user, events)
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
        if hold:  # только данные для приложения, без блоков — сервер ничего не разошлёт
            push_to_bot(f"{local:%Y-%m-%d %H:%M}", [], meta)
            return False
        try:  # выпуск: карточка + 4 главные новости целиком
            ed = edition.build_edition(ask_ai, news, "\n".join(quotes_for_ai(q)), now, len(news),
                                       qlines=quote_lines(["^GSPC", "^IXIC", "bitcoin", "BZ=F", "GC=F", "IMOEX", "USDRUB_CBR"], q), today=today_lines(events, local))
            if ed:
                card_id, ed_html, card_url = ed
                out.append({"block": "edition", "html": [card_id, tg_html(ed_html), "#today"]})
                publish_card(ed_html, card_url)
                # вечерний выпуск «итоги дня» — последний пост дня в бесплатном канале (владелец 27.10: что ждёт → календарь → карусели → итоги вечера);
                # в заголовке — день недели и число («Вторник, 27 октября»). FREECH_CHAT — переменная репозитория
                # с 27.10 (сетка Маркетолога v2) итоги 19:30 в канал рисует channel/post.js (рубрика itogi, шаблон со львом) — выпуск туда больше не идёт
                if os.environ.get("FREECH_CHAT") and os.environ.get("FREECH_EDITION") == "on" and local.hour >= 14 and not dry:
                    wd = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"][local.weekday()]
                    publish_card(re.sub(r" · (\d{1,2} \S+)</b>", lambda m: f" · {wd}, {m.group(1)}</b>", ed_html, count=1), card_url, os.environ["FREECH_CHAT"])
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
    elif hold:
        return False
    state["last_digest_ts"] = now.isoformat()
    return True


def run_digest_big(state, now, dry, final=True):
    """Выжимка — на больших моделях (потом lite и запасной), остальное в запуске — снова на lite."""
    AI_TIER["big"] = True
    try:
        return run_digest(state, now, dry, final)
    finally:
        AI_TIER["big"] = False


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
    seen = state.setdefault("results_fp", [])  # отпечатки «событие + факт»
    for r in calendar_future.released(now):
        key = r["key"]
        if key in done:
            continue
        # 15–16.10 Nasdaq на запрос за 16-е вернул факты 15-го — одно и то же событие ушло дважды с разными оценками
        fp = r["title"] + "|" + ("|".join(x["actual"] for x in r["rows"]) if r.get("rows") else str(r.get("eps")))
        if fp in seen:
            print("Результат: повтор тех же цифр, пропускаю —", r["title"], file=sys.stderr)
            done.append(key)
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
                res = edition.ru_numbers_deep(res)
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
            weekly.remember_results([payload], now)  # для «Разбора недели»
            if payload["impact"] == "High":
                publish(html, dry)  # и в тестовый канал
                rows = "\n".join(f'{x["name"]}: факт {x["actual"]}, прогноз {x["consensus"] or "—"}, прошлое {x["previous"] or "—"}'
                                  for x in r.get("rows") or [])
                pro.big_event(ask_ai, tg_html, state, now, "result", r["title"],
                              f'{res["headline"]}\n{rows}\nОценка: {label}. {res.get("meaning", "")}',
                              "\n".join(quotes_for_ai(fetch_markets())))
        done.append(key)
        seen.append(fp)
    state["results_done"] = done[-200:]
    state["results_fp"] = seen[-200:]


WEEKDAY = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]


def next_week_lines(state, now, days=7):
    """События ближайших дней для ИИ: «вт 21.10 17:30 МСК — Запасы нефти EIA (прогноз …)», сначала сильные, по времени."""
    try:
        evs = calendar_future.build(fetch_calendar(state), horizon_days=days + 1)
    except Exception as e:
        print("Неделя: календарь не собран", e, file=sys.stderr)
        return []
    nxt = []
    for e in sorted(evs, key=lambda e: (e.get("impact") != "High", e["dt"])):
        dt = datetime.fromisoformat(e["dt"]).astimezone(MSK)
        if now < dt <= now + timedelta(days=days) and (e.get("impact") in ("High", "Medium") or e.get("kind") in ("fed", "cb")):
            extra = ", ".join(x for x in (f"прогноз {e['forecast']}" if e.get("forecast") else "",
                                          f"прошлое {e['previous']}" if e.get("previous") else "") if x)
            nxt.append((dt, f"{WEEKDAY[dt.weekday()]} {dt:%d.%m %H:%M} МСК — {e['title']}" + (f" ({extra})" if extra else "")))
    return [line for _, line in sorted(nxt[:25])]


# Плановые рубрики канала PRO (сетка утверждена владельцем 19.10): день недели (0 = пн), часы МСК, функция в weekly
PRO_PLAN = {"week_ahead": (0, 9, "run_week_ahead"), "asset": (2, 12, "run_asset"), "guide": (3, 12, "run_guide"),
            "weekly": (6, 18, "run_weekly"), "month": ("1", 12, "run_month")}


# Хештеги рубрик — «разделы» канала: нажал хештег → все посты рубрики (у каналов Telegram нет вкладок-тем)
PRO_TAGS = {"week_ahead": "#НЕДЕЛЯ_ВПЕРЕДИ", "asset": "#АКТИВ_НЕДЕЛИ", "guide": "#ГАЙД", "weekly": "#РАЗБОР_НЕДЕЛИ",
            "month": "#ИТОГИ_МЕСЯЦА"}


def run_pro(state, now, dry, kind, until=None):
    """Рубрика канала PRO. Куда: PRO_POSTS=on — канал PRO, иначе — образец в тестовый канал."""
    chat = pro._target("PRO_POSTS")

    topic = pro.thread(state, chat, kind)  # группа с темами — своя тема рубрики; канал — None

    def send(html, card_url):  # Telegram: до 4096 знаков в сообщении; картинка — у первого
        parts = split_message(tg_html(html + "\n\n" + PRO_TAGS[kind]))
        pro._send(chat, parts[0], card_url, thread=topic)
        for p in parts[1:]:
            pro._send(chat, p, None, thread=topic)

    AI_TIER["big"] = True
    try:
        kw = {"until": until} if until else {}
        return getattr(weekly, PRO_PLAN[kind][2])(ask_ai, http_get, next_week_lines(state, now), send, now, dry, **kw)
    finally:
        AI_TIER["big"] = False


PRO_INTRO = """<b>📌 Как устроен «Пульс рынка PRO»</b>

Здесь — разборы, которых нет в бесплатных выпусках: не «что случилось», а почему это важно и что дальше.

<b>Рубрики</b> — WHERE:
📅 #НЕДЕЛЯ_ВПЕРЕДИ — по понедельникам: главные события недели и карта давления на 10 активов
🔍 #АКТИВ_НЕДЕЛИ — по средам: один актив — что давит, что поддерживает, сценарии
🧠 #ГАЙД — по четвергам: как устроен рынок, простыми словами
📊 #РАЗБОР_НЕДЕЛИ — по воскресеньям: что двигало рынками, как события связаны, таблица 12 активов
📈 #ИТОГИ_МЕСЯЦА — 1-го числа: месяц в цифрах
⚡ #СОБЫТИЕ — в момент события: решения ЦБ и ФРС, инфляция, сильные данные

Первые посты выложены DATE за прошедшую неделю — чтобы было с чего начать.

<i>Пульс рынка — новости и разборы для всех, а не советы лично вам. Это не индивидуальная инвестиционная рекомендация (ст. 6.1 закона «О рынке ценных бумаг»); решения о сделках вы принимаете сами.</i>"""


def run_pro_fill(state, now, live):
    """Наполнение канала PRO за прошедшую неделю (решение владельца 20.10): события → разбор недели → неделя впереди →
    первый гайд → пост «Как устроен канал». live=False — всё в тестовый канал (по умолчанию), True — в канал PRO."""
    if not live:
        os.environ["PRO_POSTS"] = os.environ["PRO_EVENTS"] = "test"
    pro.SILENT["on"] = True  # 7 постов подряд — без звука у подписчиков
    quotes_none = ""  # котировок на момент прошлых событий нет — ИИ пропустит «Реакцию рынка»
    week = weekly.load_week()
    groups = {}  # сильные данные прошлой недели — один пост на день
    for r in week["results"]:
        if r.get("impact") == "High" or "Отчёт" in (r.get("title") or ""):
            groups.setdefault(r["dt"][:10], []).append(r)
    AI_TIER["big"] = True
    try:
        for day in sorted(groups)[-2:]:
            rs = sorted(groups[day], key=lambda r: ("CPI" not in r["title"], "PPI" not in r["title"], "Базов" in r["title"]))
            d = datetime.fromisoformat(rs[0]["dt"]).astimezone(MSK)
            title = f"{d:%d.%m} — " + ", ".join(r["title"] for r in rs[:3])
            facts = (f"Событие прошлое ({d:%d.%m.%Y}), разбор публикуется задним числом — пиши в прошедшем времени, "
                     "раздел «Реакция рынка» пропусти.\n" + "\n".join(f'{r["title"]}: {r["headline"]} (итог: {pro.VERDICT_RU.get(r["verdict"], r["verdict"])})' for r in rs))
            pro.big_event(ask_ai, tg_html, {}, now, "result", title, facts, quotes_none, tstate=state)
            time.sleep(3)
    finally:
        AI_TIER["big"] = False
    sun = datetime(2026, 10, 18, 18, 0, tzinfo=MSK)
    run_pro(state, sun, False, "weekly", until=datetime(2026, 10, 18, tzinfo=MSK))
    time.sleep(3)
    run_pro(state, datetime(2026, 10, 19, 9, 0, tzinfo=MSK), False, "week_ahead", until=datetime(2026, 10, 18, tzinfo=MSK))
    time.sleep(3)
    if live:
        run_pro(state, now, False, "guide")
    else:  # образец гайда — без отметки «опубликован»
        g = sorted(p for p in weekly.GUIDES_DIR.glob("*.html") if not p.name.startswith("_"))
        if g:
            ch = pro._target("PRO_POSTS")
            pro._send(ch, tg_html(g[0].read_text(encoding="utf-8").strip() + "\n\n#ГАЙД"), None, thread=pro.thread(state, ch, "guide"))
    time.sleep(3)
    chat = pro._target("PRO_POSTS")
    forum = state.get("pro_topics", {}).get(str(chat), {}).get("forum")
    intro = PRO_INTRO.replace("WHERE", "у каждой своя тема (вкладки вверху), а хештег покажет все посты рубрики" if forum
                              else "нажми хештег, чтобы увидеть все посты рубрики").replace("DATE", f"{now.astimezone(MSK):%d.%m}")
    mid = pro._send(chat, tg_html(intro), None)  # в группе с темами — в «Общее»
    if live and mid:
        pro.pin(chat, mid)


def due_pro(state, now):
    """Какая плановая рубрика PRO должна выйти сейчас (один раз в свой день, в окне 3 часа)."""
    local = now.astimezone(MSK)
    sent = state.setdefault("pro_sent", {})
    for kind, (day, hour, _) in PRO_PLAN.items():
        today = local.day == 1 if day == "1" else local.weekday() == day
        if today and hour <= local.hour < hour + 3 and sent.get(kind) != f"{local:%Y-%m-%d}":
            return kind
    return None


def due_digest_slot(state, now):
    local = now.astimezone(MSK)
    for slot in os.environ.get("DIGEST_TIMES", "07:30,19:30").split(","):
        h, m = map(int, slot.strip().split(":"))
        start = local.replace(hour=h, minute=m, second=0, microsecond=0)
        key = f"{local:%Y-%m-%d} {slot.strip()}"
        grace, last = (EVENING_GRACE_MIN, EVENING_LAST_CHANCE_MIN) if h >= 12 else (SLOT_GRACE_MIN, LAST_CHANCE_MIN)
        if start <= local < start + timedelta(minutes=grace) and key not in state["sent_digests"]:
            return key, local >= start + timedelta(minutes=last)
    return None, False


def main():
    args = sys.argv[1:]
    dry = "--dry-run" in args
    mode = next((a for a in args if not a.startswith("--")), "auto")
    now = datetime.now(timezone.utc)
    state = load_state()
    ai_dead_load(state)
    if (mode in ("preview", "ai_check", "ai_probe", "pro_sample", "pro_fill", "pro_fill_live", "freech") or mode in PRO_PLAN) and "--force" not in args:
        # тесты тратят ту же квоту ИИ — не в последний час перед выжимкой
        local = now.astimezone(MSK)
        for slot in os.environ.get("DIGEST_TIMES", "07:30,19:30").split(","):
            h, mi = map(int, slot.strip().split(":"))
            start = local.replace(hour=h, minute=mi, second=0, microsecond=0)
            if timedelta(0) < start - local <= timedelta(minutes=60):
                print(f"Тест отменён: через {int((start - local).total_seconds() // 60)} мин выжимка {slot.strip()} — бережём квоту ИИ (--force — запустить всё равно)", file=sys.stderr)
                return
    if mode in PRO_PLAN:  # рубрика PRO вручную: weekly, week_ahead, asset, guide, month (без PRO_POSTS=on — в тестовый канал)
        run_pro(state, now, dry, mode)
        save_state(state)
        return
    if mode in ("pro_fill", "pro_fill_live"):  # наполнение канала PRO за прошлую неделю; _live — в канал PRO (только после «да» владельца)
        run_pro_fill(state, now, mode == "pro_fill_live")
        save_state(state)
        return
    if mode == "pro_edit":  # исправить опубликованный пост канала PRO по state/pro_edit.json (после «да» владельца на публикацию)
        spec = json.loads((ROOT / "state" / "pro_edit.json").read_text(encoding="utf-8"))
        pro.edit(pro._target("PRO_POSTS"), spec, tg_html)
        return
    if mode.split(":")[0] == "pro_day":  # «События дня» #СОБЫТИЯ — образец в тестовый канал; pro_day:2026-10-28 — на дату (до +3 дней)
        day = mode.split(":")[1] if ":" in mode else None
        at = datetime.fromisoformat(day + "T20:30:00+03:00") if day else now
        pro_day.run(state, at, MSK, fetch_calendar, tg_html, test=True, horizon=4 if day else 2, ask=ask_ai)
        return
    if mode == "pro_auto":  # «мощный пост» PRO: бриф → черновик ИИ → meta.pro_draft + образец в тестовый канал (в канал PRO — ничего)
        if pro_auto.run(state, now, ask_ai, send_test=None if dry else send_telegram, dry=dry) and not dry:
            state["pro_auto"] = f"{now.astimezone(MSK):%Y-%m-%d}"
            save_state(state)
        return
    if mode == "pro_check":  # права бота в канале PRO — без публикации
        print("Можно публиковать:", pro.check(os.environ.get("PRO_CHAT_ID") or "-1004428155051"), file=sys.stderr)
        return
    if mode == "pro_sample":  # образец «Большого события» в тестовый канал: последнее сильное событие из журнала недели
        os.environ["PRO_EVENTS"] = "test"
        r = next((r for r in reversed(weekly.load_week()["results"]) if r.get("impact") == "High"), None)
        if r:
            pro.big_event(ask_ai, tg_html, {}, now, "result", r["title"], f'{r["headline"]}\nОценка: {r["verdict"]}',
                          "\n".join(quotes_for_ai(fetch_markets())))
        return
    if mode == "freech":  # тексты рубрик бесплатного канала сейчас → meta.freech_posts + образец в тестовый канал
        if freech.run(state, now, MSK, ask_ai, send_test=send_telegram, dry=dry) and not dry:
            state["freech_day"] = f"{now.astimezone(MSK):%Y-%m-%d}"
            save_state(state)
        return
    if mode == "ai_probe":  # какие бесплатные модели OpenRouter отвечают (только журнал)
        run_ai_probe()
        return
    if mode == "ai_check":  # проверка запасного ИИ: Gemini «исчерпан» (только в памяти), образец через OpenRouter — в тестовый канал
        AI_DEAD["day"] |= {m.strip() for m in (os.environ.get("GEMINI_MODEL") or GEMINI_MODELS).split(",")}  # все, и большие, и lite
        mode = "preview"

    if mode == "digest":
        run_digest_big(state, now, dry)
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
        slot, final = due_digest_slot(state, now)
        if slot and run_digest_big(state, now, dry, final):
            state["sent_digests"].append(slot)
        run_events(state, now, dry)
        run_results(state, now, dry)
        kind = due_pro(state, now) if (os.environ.get("PRO_POSTS") or "").lower() == "on" and not slot else None
        if kind:  # плановая рубрика канала PRO; отметка заранее — при сбое ИИ не повторяем каждые 30 минут
            state["pro_sent"][kind] = f"{now.astimezone(MSK):%Y-%m-%d}"
            run_pro(state, now, dry, kind)
        if not slot and not dry and pro_day.due(state, now, MSK):  # #СОБЫТИЯ каждый вечер; отметка заранее — без повторов при сбое
            state["pro_daily"] = f"{now.astimezone(MSK):%Y-%m-%d}"
            pro_day.run(state, now, MSK, fetch_calendar, tg_html, ask=ask_ai)  # карусель evc v6 + разбор ИИ
        if not slot and not dry and pro_auto.due(state, now, (os.environ.get("PRO_AUTO") or "on").lower()):  # черновик поста PRO к 10:00 МСК; отметка заранее
            state["pro_auto"] = f"{now.astimezone(MSK):%Y-%m-%d}"
            try:
                pro_auto.run(state, now, ask_ai, send_test=send_telegram)
            except Exception as e:
                print("PRO авто: ошибка", e, file=sys.stderr)
        if not slot and not dry and freech.due(state, now, MSK):  # рубрики бесплатного канала к 11:30 МСК; отметка заранее — без повторов при сбое
            state["freech_day"] = f"{now.astimezone(MSK):%Y-%m-%d}"
            try:
                freech.run(state, now, MSK, ask_ai)
            except Exception as e:
                print("Бесплатный канал: ошибка", e, file=sys.stderr)
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
