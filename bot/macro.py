"""Ключевые «долгие» показатели для главной: ставка ФРС, инфляция CPI, безработица.

Источники (без ключей):
- ставка ФРС — последнее заявление FOMC с federalreserve.gov (лента press_monetary.xml);
- CPI и безработица — BLS Public API v1 (до 25 запросов в день, кэшируем на сутки).
"""
import html
import json
import re
import urllib.request
from datetime import datetime, timezone

UA = "Mozilla/5.0 (market-digest-bot)"


def _get(url, data=None):
    headers = {"User-Agent": UA}
    if data is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(data).encode()
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.read().decode("utf-8", "replace")


def _frac(s):
    """«3-3/4» → 3.75, «4» → 4.0"""
    s = s.strip()
    m = re.match(r"(\d+)(?:-(\d+)/(\d+))?$", s)
    if not m:
        return float(s)
    whole = int(m.group(1))
    return whole + (int(m.group(2)) / int(m.group(3)) if m.group(2) else 0)


def fed_rate():
    feed = _get("https://www.federalreserve.gov/feeds/press_monetary.xml")
    for title, link, date in re.findall(r"<item>.*?<title>(.*?)</title>.*?<link>(.*?)</link>.*?<pubDate>(.*?)</pubDate>", feed, re.S):
        if "FOMC statement" not in title:
            continue
        link = re.sub(r"<!\[CDATA\[|\]\]>", "", link).strip()
        text = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", _get(link))))
        m = re.search(r"(raise|lower|maintain)\w* the target range for the federal funds rate(?: by [^ ]+ percentage point)? (?:to|at) ([\d\-/]+) to ([\d\-/]+) percent", text)
        if not m:
            continue
        action = {"raise": "up", "lower": "down", "maintain": "hold"}[m.group(1)]
        d = datetime.strptime(re.sub(r"<!\[CDATA\[|\]\]>", "", date).strip()[:16], "%a, %d %b %Y")
        return {"lo": _frac(m.group(2)), "hi": _frac(m.group(3)), "action": action, "date": d.date().isoformat(), "url": link}
    return None


def bls():
    """CPI (всё и базовый, г/г) и безработица — одним запросом."""
    raw = json.loads(_get("https://api.bls.gov/publicAPI/v1/timeseries/data/",
                          {"seriesid": ["CUUR0000SA0", "CUUR0000SA0L1E", "LNS14000000"]}))
    series = {s["seriesID"]: s["data"] for s in raw.get("Results", {}).get("series", [])}
    out = {}

    def yoy(sid):
        data = [x for x in series.get(sid, []) if x["period"].startswith("M") and x["period"] != "M13"]
        if len(data) < 13:
            return None
        cur, prev = data[0], next((x for x in data if x["year"] == str(int(data[0]["year"]) - 1) and x["period"] == data[0]["period"]), None)
        if not prev:
            return None
        return {"value": round((float(cur["value"]) / float(prev["value"]) - 1) * 100, 1), "month": f'{cur["year"]}-{cur["period"][1:]}'}

    cpi, core = yoy("CUUR0000SA0"), yoy("CUUR0000SA0L1E")
    if cpi:
        out["cpi"] = {**cpi, "core": core["value"] if core else None}
    un = series.get("LNS14000000", [])
    if un:
        out["unemp"] = {"value": float(un[0]["value"]), "prev": float(un[1]["value"]) if len(un) > 1 else None,
                        "month": f'{un[0]["year"]}-{un[0]["period"][1:]}'}
    return out


def cbr_rate():
    """Ключевая ставка Банка России (SOAP-сервис cbr.ru) — текущая и предыдущее значение."""
    from datetime import date, timedelta as td
    body = ('<?xml version="1.0" encoding="utf-8"?><soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"><soap:Body>'
            f'<KeyRateXML xmlns="http://web.cbr.ru/"><fromDate>{(date.today() - td(days=400)).isoformat()}</fromDate><ToDate>{date.today().isoformat()}</ToDate></KeyRateXML></soap:Body></soap:Envelope>')
    req = urllib.request.Request("https://www.cbr.ru/DailyInfoWebServ/DailyInfo.asmx", data=body.encode(),
                                 headers={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": '"http://web.cbr.ru/KeyRateXML"', "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=25) as r:
        xml = r.read().decode("utf-8", "replace")
    rows = re.findall(r"<DT>(\d{4}-\d{2}-\d{2})[^<]*</DT><Rate>([\d.]+)</Rate>", xml)
    if not rows:
        return None
    rows.sort()
    cur = float(rows[-1][1])
    since, prev = rows[-1][0], None
    for d, v in reversed(rows):  # когда ставка стала текущей и какой была до этого
        if float(v) != cur:
            prev = float(v)
            break
        since = d
    action = "hold" if prev is None else ("up" if cur > prev else "down")
    return {"rate": cur, "prev": prev, "date": since, "action": action}


def build(state):
    """Раз в сутки обновляем, иначе берём из кэша в state."""
    cache = state.get("macro") or {}
    today = datetime.now(timezone.utc).date().isoformat()
    if cache.get("day") == today and (cache.get("data") or {}).get("cbr"):
        return cache["data"]
    data = dict(cache.get("data") or {})
    try:
        fr = fed_rate()
        if fr:
            data["fed"] = fr
    except Exception as e:
        print("Ставка ФРС:", e)
    try:
        cb = cbr_rate()
        if cb:
            data["cbr"] = cb
    except Exception as e:
        print("Ставка ЦБ:", e)
    try:
        data.update(bls())
    except Exception as e:
        print("BLS:", e)
    state["macro"] = {"day": today, "data": data}
    return data
