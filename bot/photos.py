"""Фото для карточки под конкретную новость: поиск на Wikimedia Commons.

Берём только общественное достояние и CC0 (без обязательной подписи автора), горизонтальные, от 1600 px (берём копию 2560 px).
Использованные фото помним в state/photos.txt (строка = фото) — одно и то же фото не повторяется.
Файл текстовый, чтобы git объединял списки двух запусков сам (.gitattributes: merge=union).
Не нашли — карточка берёт стандартный фон темы (bot/bg).
"""
import json
import re
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://commons.wikimedia.org/w/api.php"
UA = "PulseMarketBot/1.0 (https://app.pulse-rynka.ru; news cards)"
USED = Path(__file__).resolve().parent.parent / "state" / "photos.txt"
KEEP = 300


def _get(url, timeout=20):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=timeout) as r:
        return r.read()


def _free(meta):
    lic = (meta.get("LicenseShortName", {}).get("value") or "").lower()
    return "public domain" in lic or lic.startswith("pd") or "cc0" in lic


def _used():
    try:
        return [l for l in USED.read_text(encoding="utf-8").splitlines() if l.strip()]
    except Exception:
        return []


NOT_PHOTO = re.compile(r"chart|graph|diagram|map\b|logo|screenshot|over time|plot|infographic|table|scheme|svg|icon|banknote|coin of|stamp|drawing|painting|poster|generation|statistic|Information Administration|percent|share of|by year|forecast", re.I)


OLD = re.compile(r"\b(1[0-8]\d\d|19[0-7]\d)s?c?\b|(?<!\d)1[89][0-7]0s|\b(NARA|LC-[A-Z]|Bestanddeel|postcard|guide to|back\.jpe?g$)", re.I)
# 18.10: к новости про мины в Ормузском проливе подобрался бомбардировщик B-52 — военную технику и людей не берём
MILITARY = re.compile(r"\b(B-\d+|F-\d+|F/A-\d+|Stratofortress|bomber|fighter|aircraft|carrier|strike group|navy|USS|HMS|army|air force|"
                      r"marine corps|missile|warship|destroyer|frigate|submarine|military|soldier|troops|firefight\w*|rescue|space shuttle|"
                      r"Atlantis|astronaut|iss\d{3}e)\b", re.I)
PEOPLE = re.compile(r"\b(President|Minister|Summit|portrait|meets|meeting with|visit|addressing|speech|press conference|marshals|"
                    r"Mr\.|Mrs\.|woman|man\b|girl|boy)\b", re.I)
WEAK = {"economy", "market", "business", "money", "finance", "financial", "global", "world", "city", "building", "new", "old"}


def _relevant(title, q):
    """В названии файла должны быть слова запроса: хотя бы 2 (или все, если слово одно) — иначе поиск даёт случайные фото."""
    words = [w for w in q.lower().split() if len(w) >= 3]
    t = title.lower()
    if NOT_PHOTO.search(t) or OLD.search(title) or MILITARY.search(title) or PEOPLE.search(title):
        return False  # графики, схемы, логотипы, архив (до 1980), военная техника, люди — не берём
    hit = [w for w in words if re.search(r"\b" + re.escape(w), t)]  # с начала слова: «oil» не ловит «toilet»
    strong = [w for w in hit if w not in WEAK]  # «economy», «market» — слишком общие: «Old Economy County Park»
    return bool(strong) and len(hit) >= min(2, len(words))


def find(queries):
    """queries — 1–3 английских запроса, от точного к общему. Возвращает путь к скачанному jpg или None."""
    used = _used()
    for q in [q for q in queries if q][:3]:
        params = {"action": "query", "format": "json", "generator": "search", "gsrsearch": f"{q} filetype:bitmap",
                  "gsrnamespace": 6, "gsrlimit": 40, "prop": "imageinfo", "iiprop": "url|size|extmetadata|mime", "iiurlwidth": 2560}
        try:
            pages = json.loads(_get(API + "?" + urllib.parse.urlencode(params))).get("query", {}).get("pages", {})
        except Exception as e:
            print("Фото: поиск не удался", q, e, file=sys.stderr)
            continue
        for p in sorted(pages.values(), key=lambda x: x.get("index", 99)):
            ii = (p.get("imageinfo") or [{}])[0]
            if p["title"] in used or ii.get("mime") != "image/jpeg" or not _relevant(p["title"], q):
                continue
            if ii.get("width", 0) < 1600 or ii.get("width", 0) < ii.get("height", 1) * 1.25 or not _free(ii.get("extmetadata", {})):
                continue
            try:
                data = _get(ii.get("thumburl") or ii["url"], timeout=40)
            except Exception:
                continue
            path = Path(tempfile.gettempdir()) / f"photo_{abs(hash(p['title']))}.jpg"
            path.write_bytes(data)
            USED.parent.mkdir(exist_ok=True)
            USED.write_text("\n".join((used + [p["title"]])[-KEEP:]) + "\n", encoding="utf-8")
            print(f"Фото: «{q}» → {p['title']}", file=sys.stderr)
            return str(path)
    print("Фото: подходящего нет —", queries, file=sys.stderr)
    return None
