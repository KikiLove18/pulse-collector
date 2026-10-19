"""Календарь событий на ~60 дней вперёд.

Источники (по приоритету):
1. ForexFactory — текущая неделя (точное время, прогноз).
2. Nasdaq economic calendar — ближайшие 7 дней (учитывает переносы).
3. Официальные графики ниже — дальше, с пометкой «по графику».
4. Nasdaq earnings — отчёты крупных компаний на 30 дней.

ГРАФИКИ НУЖНО ОБНОВЛЯТЬ РАЗ В ГОД (когда BLS/BEA публикуют даты на следующий год).
Проверено 09.10.2026: federalreserve.gov/monetarypolicy/fomccalendars.htm,
bls.gov/schedule/news_release/{cpi,ppi,empsit}.htm, bea.gov/news/schedule.
"""
import json
import re
import sys
import urllib.request
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"

# Решение ФРС: второй день заседания, 14:00 ET; * — с прогнозами (SEP)
FOMC = ["2026-10-28", "2026-12-09*", "2027-01-27", "2027-03-17*", "2027-04-28", "2027-06-09*",
        "2027-07-28", "2027-09-15*", "2027-10-27", "2027-12-08*"]
# Заседания Банка России по ключевой ставке (решение 13:30 МСК, пресс-конференция 15:00 МСК); проверено 10.10.2026 на cbr.ru/dkp/cal_mp
CBR = ["2026-10-23", "2026-12-18", "2027-02-12", "2027-03-19", "2027-04-30", "2027-06-11", "2027-07-30", "2027-09-10", "2027-10-22", "2027-12-17"]
MSKZ = ZoneInfo("Europe/Moscow")
SCHEDULE = [  # (дата, время ET, событие, важность)
    ("2026-10-14", "08:30", "Инфляция CPI", "High"),
    ("2026-11-10", "08:30", "Инфляция CPI", "High"),
    ("2026-12-10", "08:30", "Инфляция CPI", "High"),
    ("2026-10-15", "08:30", "Цены производителей PPI", "High"),
    ("2026-11-13", "08:30", "Цены производителей PPI", "High"),
    ("2026-12-15", "08:30", "Цены производителей PPI", "High"),
    ("2026-11-06", "08:30", "Рынок труда NFP и безработица", "High"),
    ("2026-12-04", "08:30", "Рынок труда NFP и безработица", "High"),
    ("2026-10-29", "08:30", "ВВП США, 3 кв. (первая оценка)", "High"),
    ("2026-11-25", "08:30", "ВВП США, 3 кв. (вторая оценка)", "Medium"),
    ("2026-12-23", "08:30", "ВВП США, 3 кв. (третья оценка)", "Medium"),
    ("2026-10-29", "08:30", "Доходы и расходы, инфляция PCE", "High"),
    ("2026-11-25", "08:30", "Доходы и расходы, инфляция PCE", "High"),
    ("2026-12-23", "08:30", "Доходы и расходы, инфляция PCE", "High"),
]

# Перевод и важность событий из живых календарей (ключ — начало английского названия)
RU = [
    ("Core CPI", "Инфляция CPI", "High"), ("CPI", "Инфляция CPI", "High"),
    ("Core PPI", "Цены производителей PPI", "High"), ("PPI", "Цены производителей PPI", "High"),
    ("Nonfarm Payrolls", "Рынок труда NFP", "High"), ("Unemployment Rate", "Уровень безработицы", "High"),
    ("Fed Interest Rate Decision", "Решение ФРС по ставке", "High"), ("FOMC Statement", "Заявление ФРС", "High"),
    ("FOMC Press Conference", "Пресс-конференция ФРС", "High"), ("FOMC Meeting Minutes", "Протокол заседания ФРС", "High"),
    ("Fed Chair", "Выступление главы ФРС", "High"),
    ("GDP", "ВВП США", "High"), ("Core PCE", "Базовая инфляция PCE", "High"), ("PCE", "Инфляция PCE", "Medium"),
    ("Core Retail Sales", "Базовые розничные продажи", "High"), ("Unemployment Claims", "Первичные заявки на пособие", "Medium"),
    ("Philly Fed Manufacturing Index", "Индекс деловой активности ФРБ Филадельфии", "Medium"),
    ("Retail Sales", "Розничные продажи", "High"), ("ISM Manufacturing PMI", "Деловая активность ISM (промышленность)", "High"),
    ("ISM Non-Manufacturing PMI", "Деловая активность ISM (услуги)", "High"), ("ISM Services PMI", "Деловая активность ISM (услуги)", "High"),
    ("Initial Jobless Claims", "Первичные заявки на пособие", "Medium"), ("JOLTS", "Вакансии JOLTS", "Medium"),
    ("ADP Nonfarm", "Занятость ADP", "Medium"), ("CB Consumer Confidence", "Доверие потребителей (CB)", "Medium"),
    ("Michigan Consumer Sentiment", "Настроения потребителей (Мичиган)", "Medium"),
    ("Crude Oil Inventories", "Запасы нефти EIA", "Medium"), ("FOMC Member", "Выступление члена ФРС", "Medium"),
    ("Durable Goods", "Заказы товаров длительного пользования", "Medium"), ("Existing Home Sales", "Продажи вторичного жилья", "Medium"),
]
EARNINGS = {"NVDA": "Nvidia", "AAPL": "Apple", "MSFT": "Microsoft", "AMZN": "Amazon", "GOOGL": "Alphabet", "META": "Meta",
            "TSLA": "Tesla", "AVGO": "Broadcom", "NFLX": "Netflix", "AMD": "AMD", "ORCL": "Oracle", "PLTR": "Palantir",
            "JPM": "JPMorgan", "GS": "Goldman Sachs", "COIN": "Coinbase", "MSTR": "Strategy", "HOOD": "Robinhood", "INTC": "Intel"}


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def _et(day, hhmm):
    h, m = map(int, hhmm.split(":"))
    y, mo, d = map(int, day.split("-"))
    return datetime(y, mo, d, h, m, tzinfo=ET)


def translate(name):
    for en, ru, imp in RU:
        if name.startswith(en):
            return ru, imp
    return None, None


def _econ_rows(day):
    """Строки Nasdaq за настоящий день `day` (ГГГГ-ММ-ДД). 19.10 проверено: Nasdaq по запросу date=X отдаёт события дня X−1
    (по date=22.10, четверг, — заявки MBA и запасы нефти EIA, они всегда в среду; «Мичиган» по пятницам — по date=24.10, субботе).
    Из-за этого 15–16.10 одни и те же факты ушли дважды, а календарь ставил события на день позже."""
    nxt = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
    rows = (_get(f"https://api.nasdaq.com/api/calendar/economicevents?date={nxt}").get("data") or {}).get("rows") or []
    mba = [r for r in rows if r.get("eventName", "").startswith("MBA Mortgage Applications")]
    if mba and date.fromisoformat(day).weekday() != 2:  # заявки MBA — по средам: если не среда, Nasdaq исправил сдвиг
        print(f"Календарь Nasdaq: заявки MBA выпали на {day} (не среда) — проверь сдвиг дат в _econ_rows", file=sys.stderr)
    return rows


def nasdaq_econ(days=7):
    out = []
    today = datetime.now(ET).date()
    for i in range(days):
        day = (today + timedelta(days=i)).isoformat()
        try:
            rows = _econ_rows(day)
        except Exception:
            continue
        for r in rows:
            if r.get("country") != "United States":
                continue
            name = r.get("eventName", "")
            ru, imp = translate(name)
            m = re.match(r"(?:FOMC Member|Fed Chair|Fed Governor)\s+(\w+)", name)
            if ru and m:
                ru = f"{ru} ({m.group(1)})"
            t = (r.get("gmt") or "").strip()  # фактически время ET
            if not ru or not t[:2].isdigit():
                continue
            clean = lambda v: "" if not v or "nbsp" in v else v.strip()
            out.append({"dt": _et(day, t[:5]), "title": ru, "impact": imp, "kind": "macro",
                        "forecast": clean(r.get("consensus")), "previous": clean(r.get("previous"))})
    return out


def earnings(days=45):
    out = []
    today = datetime.now(ET).date()
    for i in range(days):
        day = today + timedelta(days=i)
        if day.weekday() >= 5:
            continue
        try:
            rows = (_get(f"https://api.nasdaq.com/api/calendar/earnings?date={day.isoformat()}").get("data") or {}).get("rows") or []
        except Exception:
            continue
        for r in rows:
            sym = r.get("symbol")
            if sym in EARNINGS:
                when = {"time-pre-market": ("08:00", "до открытия"), "time-after-hours": ("16:05", "после закрытия")}.get(r.get("time"), ("09:30", ""))
                out.append({"dt": _et(day.isoformat(), when[0]), "title": f"Отчёт {EARNINGS[sym]} ({sym})",
                            "impact": "High" if sym in ("NVDA", "AAPL", "MSFT", "AMZN", "GOOGL", "META", "TSLA", "COIN", "MSTR") else "Medium",
                            "kind": "earnings", "forecast": (r.get("epsForecast") or "").strip(), "previous": "",
                            "note": when[1]})
    return out


def scheduled():
    out = []
    for d in FOMC:
        sep = d.endswith("*")
        d = d.rstrip("*")
        out.append({"dt": _et(d, "14:00"), "title": "Решение ФРС по ставке" + (" + прогнозы" if sep else ""), "impact": "High", "kind": "fed", "note": "по графику"})
        out.append({"dt": _et(d, "14:30"), "title": "Пресс-конференция главы ФРС", "impact": "High", "kind": "fed", "note": "по графику"})
    for d in CBR:
        y, mo, dd = map(int, d.split("-"))
        out.append({"dt": datetime(y, mo, dd, 13, 30, tzinfo=MSKZ), "title": "Решение Банка России по ключевой ставке", "impact": "High", "kind": "cbr", "note": "по графику"})
        out.append({"dt": datetime(y, mo, dd, 15, 0, tzinfo=MSKZ), "title": "Пресс-конференция главы Банка России", "impact": "High", "kind": "cbr", "note": "по графику"})
    for d, t, title, imp in SCHEDULE:
        out.append({"dt": _et(d, t), "title": title, "impact": imp, "kind": "macro", "note": "по графику"})
    return out + ru_recurring() + crypto_recurring()


def ru_recurring(days=60):
    """Регулярные события российского рынка (проверено 11.10.2026):
    - недельная инфляция Росстата — по средам вечером (19:00 МСК);
    - аукционы ОФЗ Минфина — по средам, сбор заявок 12:00–12:30 МСК (moex.com);
    - единый срок уплаты налогов — 28-е число месяца (НК РФ, ЕНП)."""
    out, today = [], datetime.now(MSKZ).date()
    for i in range(days):
        d = today + timedelta(days=i)
        if d.weekday() == 2:
            out.append({"dt": datetime(d.year, d.month, d.day, 12, 0, tzinfo=MSKZ), "title": "Аукционы ОФЗ Минфина", "impact": "Medium", "kind": "ru", "note": "по графику"})
            out.append({"dt": datetime(d.year, d.month, d.day, 19, 0, tzinfo=MSKZ), "title": "Недельная инфляция в России (Росстат)", "impact": "Medium", "kind": "ru", "note": "по графику"})
        if d.day == 28:
            out.append({"dt": datetime(d.year, d.month, d.day, 9, 0, tzinfo=MSKZ), "title": "Срок уплаты налогов — поддержка рубля", "impact": "Medium", "kind": "ru", "note": "весь день"})
    return out


LONDON = ZoneInfo("Europe/London")


def crypto_recurring(days=60):
    """Регулярные крипто-события (проверено 12.10.2026):
    - экспирация опционов и фьючерсов Deribit — последняя пятница месяца, 08:00 UTC (support.deribit.com); март/июнь/сентябрь/декабрь — квартальная;
    - экспирация фьючерсов на биткоин CME — последняя пятница месяца, 16:00 по Лондону; если это выходной в Великобритании
      и США (25 декабря) — предыдущий рабочий день (CME Rule 350)."""
    out, today = [], datetime.now(timezone.utc).date()
    end = today + timedelta(days=days)
    y, m = today.year, today.month
    while True:
        last = datetime(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)
        fri = last - timedelta(days=(last.weekday() - 4) % 7)
        if fri.date() > end:
            break
        if fri.date() >= today - timedelta(days=2):
            q = m in (3, 6, 9, 12)
            out.append({"dt": datetime(fri.year, fri.month, fri.day, 8, 0, tzinfo=timezone.utc), "title": "Экспирация опционов на BTC и ETH (Deribit)" + (" — квартальная" if q else ""),
                        "impact": "High" if q else "Medium", "kind": "crypto", "note": "последняя пятница месяца"})
            cme = fri - timedelta(days=1) if (fri.month, fri.day) == (12, 25) else fri
            out.append({"dt": datetime(cme.year, cme.month, cme.day, 16, 0, tzinfo=LONDON), "title": "Экспирация фьючерсов на биткоин CME",
                        "impact": "Medium", "kind": "crypto", "note": "16:00 по Лондону"})
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def region(e):
    """Страна/тема события для флага в календаре: ru, us, oil, gold, crypto."""
    t = e.get("title", "")
    if e.get("kind") == "crypto":
        return "crypto"
    if e.get("kind") in ("cbr", "ru") or re.search(r"Росси|Росстат|Минфин|ОФЗ|рубл", t):
        return "ru"
    if re.search(r"нефт|ОПЕК|OPEC|EIA|буров", t, re.I):
        return "oil"
    if re.search(r"золот", t, re.I):
        return "gold"
    return "us"


def _norm(title):
    t = re.sub(r"\s*\(.*?\)", "", title).strip().lower()  # «Выступление главы ФРС (Warsh)» = «Выступление главы ФРС»
    for k in ("cpi", "ppi", "nfp", "ввп", "pce", "фрс по ставке", "банка россии по", "пресс-конференция главы банка", "пресс-конференция главы фрс"):
        if k in t:
            return k
    return t


def build(ff_events, horizon_days=60, with_earnings=True):
    """ff_events — события ForexFactory этой недели (dt, title, impact, forecast, previous)."""
    now = datetime.now(timezone.utc)
    end = now + timedelta(days=horizon_days)
    merged, seen = [], set()

    def add(e, src):
        dt = e["dt"].astimezone(timezone.utc)
        if dt < now - timedelta(days=2) or dt > end:
            return
        key = (_norm(e["title"]), dt.date().isoformat())
        near = {(key[0], (dt.date() + timedelta(days=k)).isoformat()) for k in (-1, 0, 1)}
        if src in ("schedule", "nasdaq") and near & seen:
            return  # ForexFactory уже знает это событие (Nasdaq и график дают его со сдвигом на день)
        if key in seen:
            return
        seen.add(key)
        merged.append({**e, "dt": dt.isoformat(), "src": src})

    for e in ff_events:
        ru, imp = translate(e["title"])
        add({"dt": e["dt"], "title": ru or e["title"], "impact": e.get("impact") or imp, "kind": "fed" if "ФРС" in (ru or "") else "macro",
             "forecast": e.get("forecast", ""), "previous": e.get("previous", "")}, "ff")
    for e in nasdaq_econ():
        add(e, "nasdaq")
    if with_earnings:
        for e in earnings():
            add(e, "earnings")
    for e in scheduled():
        add(e, "schedule")
    merged.sort(key=lambda x: x["dt"])
    for e in merged:
        e["region"] = region(e)
    return merged


def released(now, hours=8):
    """События, которые вышли за последние `hours` часов и уже имеют факт (Nasdaq)."""
    out = []
    start = now - timedelta(hours=hours)
    days = sorted({start.astimezone(ET).date(), now.astimezone(ET).date()})
    for day in days:
        d = day.isoformat()
        try:
            rows = _econ_rows(d)
        except Exception:
            rows = []
        groups = {}
        for r in rows:
            if r.get("country") != "United States":
                continue
            ru, imp = translate(r.get("eventName", ""))
            t = (r.get("gmt") or "").strip()
            act = (r.get("actual") or "").replace("&nbsp;", "").strip()
            if imp not in ("High", "Medium") or not act or not t[:2].isdigit() or "Выступление" in (ru or ""):
                continue
            dt = _et(d, t[:5])
            if not (start <= dt.astimezone(timezone.utc) <= now):
                continue
            clean = lambda v: "" if not v or "nbsp" in v else v.strip()
            g = groups.setdefault((_norm(ru), t[:5]), {"title": ru, "dt": dt.astimezone(timezone.utc).isoformat(), "kind": "fed" if "ФРС" in ru else "macro", "impact": imp, "rows": []})
            g["rows"].append({"name": r["eventName"], "actual": act, "consensus": clean(r.get("consensus")), "previous": clean(r.get("previous"))})
        for (n, t), g in groups.items():
            out.append({**g, "key": f"{d}|{n}|{t}"})
        # отчёты компаний
        try:
            erows = (_get(f"https://api.nasdaq.com/api/calendar/earnings?date={d}").get("data") or {}).get("rows") or []
        except Exception:
            erows = []
        for r in erows:
            sym, eps, fc = r.get("symbol"), (r.get("eps") or "").strip(), (r.get("epsForecast") or "").strip()
            if sym not in EARNINGS or not eps or not fc:
                continue
            try:
                surprise = float((r.get("surprise") or "0").replace("%", "").replace("N/A", "0"))
            except ValueError:
                surprise = 0.0
            if not surprise:  # Nasdaq часто не даёт «surprise» (13.10 JPM $5.07 при $5.94 ушёл как «в рамках») — считаем сами
                try:
                    num = lambda v: -float(v.strip("()$ ").replace(",", "")) if v.strip().startswith("(") else float(v.strip("$ ").replace(",", ""))
                    a, f = num(eps), num(fc)
                    surprise = round((a - f) / abs(f) * 100, 1) if f else 0.0
                except ValueError:
                    pass
            out.append({"key": f"{d}|earn|{sym}", "title": f"Отчёт {EARNINGS[sym]} ({sym})", "kind": "earnings",
                        "dt": _et(d, "16:05").astimezone(timezone.utc).isoformat(), "eps": eps, "forecast": fc, "surprise": surprise,
                        "impact": "High" if sym in ("NVDA", "AAPL", "MSFT", "AMZN", "GOOGL", "META", "TSLA", "COIN", "MSTR") else "Medium"})
    return out
