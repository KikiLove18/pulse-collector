"""Проверка цифр до выхода (t-1793514944): всё, что написал ИИ, сверяем с текстом, который он прочитал.
Нет совпадения — фраза снимается; цифра в заголовке — новость не выходит. Сбой самой проверки выпуск не останавливает.
Выключатель: FACTCHECK=off (журнал остаётся) — на случай, если проверка начнёт снимать лишнее."""
import functools
import html as _html
import json
import os
import re
import sys
import urllib.request

MULT = [(re.compile(r"тыс|thousand|\bk\b", re.I), 1e3), (re.compile(r"млн|миллион|million|mln|\bm\b", re.I), 1e6),
        (re.compile(r"млрд|миллиард|billion|bln|bn\b|\bb\b", re.I), 1e9), (re.compile(r"трлн|триллион|trillion|\btn\b", re.I), 1e12)]
MONTHS = r"январ\w*|феврал\w*|март\w*|апрел\w*|ма[йяюе]\b|июн\w*|июл\w*|август\w*|сентябр\w*|октябр\w*|ноябр\w*|декабр\w*|jan\w*|feb\w*|mar\w*|apr\w*|may|jun\w*|jul\w*|aug\w*|sep\w*|oct\w*|nov\w*|dec\w*"
# не величины: индексы и названия, даты, время, кварталы, порядковые, номера документов
SKIP = [re.compile(p, re.I) for p in (
    r"https?://\S+", r"<[^>]+>", r"\bS&P\s?\d+|\bSP\d+|\b(?:Nasdaq|FTSE|Russell|Dow|DAX|Stoxx|Nikkei|IMOEX|RTS)\s?\d+|\bG\d+\b|\bM\d\b|\bQ[1-4]\b|\bH[12]\b|\bT\+\d|\b\d+-[KQ]\b|\b13F\b|\b401\(k\)|\b24/7\b|\bWeb\d\b|\b\dD\b|\b\dG\b",
    r"\b\d{1,2}[.:]\d{2}(?:[.:]\d{2,4})?\s?(?:МСК|UTC|ET|GMT)\b", r"\b\d{1,2}:\d{2}\b", r"\b\d{1,2}\.\d{1,2}\.\d{2,4}\b",
    rf"\b\d{{1,2}}(?:\s?[-–—]\s?\d{{1,2}})?\s+(?:{MONTHS})", rf"\b(?:{MONTHS})\s+\d{{1,2}}\b(?!\s?[%$])", r"\b\d[-–]?(?:й|го|м|е|я|th|st|nd|rd)\b", r"\b[IVX]+\s+квартал", r"№\s?\d+")]
NUM = re.compile(r"(?<![\w.,])(\d{1,3}(?:[   ]\d{3})+|\d+)(?:([.,])(\d+))?(?:[.,](\d{3}))?(\s?%)?(?:\s*(?:тыс|млн|млрд|трлн|миллион\w*|миллиард\w*|триллион\w*|тысяч\w*|thousand|million|billion|trillion|mln|bln|bn|tn)\.?|(?<=\d)[kmb]\b)?", re.I)
QTY = r"(\d[\d \u00a0.,]*(?:\s?(?:тыс\w*|млн|thousand|million|k)\b)?)\s*(BTC|биткоин\w*|bitcoin\w*|ETH|эфир\w*|ether\w*)"
AMT = r"(\d[\d \u00a0.,]*\d|\d)\s?(тыс\w*|млн|млрд|трлн|thousand|million|billion|trillion|bn|[kmb]\b)?"
# только прямая связка «643 BTC на $1,5 млрд»: в «848 517 BTC, прибыль $20,91 млрд» сумма про другое (Strategy, 01.11)
PAIR = re.compile(QTY + r"\s*(?:\(?\s*)(?:на сумму(?: около| более)?|на(?: сумму)?|worth(?: about| around| over)?|valued at|стоимостью(?: около| более)?|for(?: about| around| over)?)\s*(?:~\s*)?\$\s?" + AMT, re.I)
APPROX = re.compile(r"(?:около|более|свыше|почти|порядка|примерно|выше|превысил\w*|ниже|менее|over|about|nearly|almost|~)\s*[$€₽¥£]?\s*$", re.I)
SENT = re.compile(r"(?<=[.!?…])\s+(?=[А-ЯЁA-Z«\"<])")
_prices = {}


def _strip(text):
    t = _html.unescape(str(text or ""))
    for p in SKIP:
        t = p.sub(" ", t)
    return t


def _mult(word):
    return next((m for r, m in MULT if r.search(word or "")), 1)


@functools.lru_cache(maxsize=256)
def numbers(text):
    """[(набор возможных значений, единица округления, простое ли малое число)]: «1,500» в английском — полторы тысячи, в русском — 1,5."""
    out = []
    clean = _strip(text)
    for m in NUM.finditer(clean):
        whole, sep, frac, extra, pct = m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)
        head = re.sub(r"\D", "", whole)
        if not frac and len(head) == 4 and 1900 <= int(head) <= 2100 and not pct:
            continue  # год
        tail = m.group(0)[len(whole) + (len(frac) + 1 if frac else 0):]
        k = _mult(tail)
        vals, unit = set(), 10 ** -len(frac or "") * k
        if frac:
            vals.add(float(f"{head}.{frac}") * k)
            if len(frac) == 3 and sep == "," or extra:  # 1,500 и 1,500,000 — разделитель тысяч
                vals.add(float(head + frac + (extra or "")) * k)
        else:
            vals.add(float(head) * k)
        small = k == 1 and not pct and not frac and float(head) <= 12 and not re.search(r"\$\s?$", clean[:m.start()])
        if APPROX.search(clean[:m.start()]):  # «около 102» при 101,964, «превысил €100» при 101: ИИ округляет словами
            unit = max(unit, 0.06 * max(vals))
        out.append((vals, unit, small))
    return out


def _same(vals, unit, other):
    """Цифра ИИ совпала с цифрой источника с учётом округления: «1,52 млрд» → «1,5 млрд», но 12,4% → 13% уже нет."""
    return any(abs(a - b) <= 0.5 * unit * 1.02 or (b and abs(a - b) / abs(b) <= 0.002) for a in vals for b in other)


@functools.lru_cache(maxsize=64)
def _values(source):
    return list({tuple(sorted(v)) for v, _, _ in numbers(source)})


def _missing(text, source):
    """Цифры из text, которых нет в source. Малые целые («3 дня», «2 компании») не проверяем: слишком много совпадений и ложных тревог."""
    src = _values(source)
    return [sorted(v)[0] for v, unit, small in numbers(text) if not small and not any(_same(v, unit, s) for s in src)]


def _val(s):
    n = numbers(s)
    return sorted(n[0][0])[0] if n else None


def crypto_price(sym):
    """Цена монеты для проверки «цена × количество». Нет ответа — None, проверку пропускаем."""
    if sym not in _prices:
        try:
            ids = {"BTC": "bitcoin", "ETH": "ethereum"}[sym]
            req = urllib.request.Request(f"https://api.coingecko.com/api/v3/simple/price?ids={ids}&vs_currencies=usd", headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as r:
                _prices[sym] = float(json.loads(r.read())[ids]["usd"])
        except Exception as e:
            print("Проверка цифр: цена монеты недоступна:", str(e)[:100], file=sys.stderr)
            _prices[sym] = None
    return _prices[sym]


def bad_price(sentence):
    """«643 Bitcoin worth $1.5 billion» — монета по $2,3 млн (01.11, ошибка самого поста, ИИ её переписал): количество и сумма не сходятся."""
    for q, name, amt, word in PAIR.findall(_html.unescape(sentence)):
        qty, money = _val(q), _val(f"{amt} {word}")
        price = crypto_price("BTC" if name.lower().startswith(("btc", "битк", "bitc")) else "ETH") if qty and money else None
        if price and not 0.5 <= (money / qty) / price <= 2:
            return True
    return False


def source_text(it):
    """Что прочитал ИИ. Пост Telegram разрезан на title и desc по 120-му знаку, иногда посреди числа («974 млр» + «д рублей») — склеиваем без пробела."""
    return it["title"] + ("" if it["source"].startswith(("TG @", "X @")) else " ") + it.get("desc", "")


def _active():
    return (os.environ.get("FACTCHECK") or "on").lower() != "off"


def missing(text, source):
    """То же для прямых вызовов из выпусков: FACTCHECK=off — ничего не снимаем, заголовок-объект от ИИ или сбой — выпуск не падает."""
    if not _active():
        return []
    try:
        return _missing(str(text or ""), str(source or ""))
    except Exception as e:
        print("Проверка цифр пропущена:", e, file=sys.stderr)
        return []


def check_news(title, text, metrics, source, who="", everything=""):
    """→ (можно ли выпускать, текст без непроверенных фраз, плашки с цифрами). Цифры сверяем с источником новости, затем со всем, что читал ИИ
    (котировки, соседние записи о том же событии). Нет нигде — фраза снимается; в заголовке — новость не выходит."""
    try:
        wide = source + " " + everything
        bad = lambda t: _missing(t, wide) or bad_price(t)
        bad_t = bad(title)
        parts = SENT.split(str(text or ""))
        keep = [s for s in parts if not bad(s)]
        removed = len(parts) - len(keep)
        new = " ".join(keep).strip()
        if new.count("<u>") != new.count("</u>"):
            new = re.sub(r"</?u>", "", new)
        good = [m for m in metrics or [] if isinstance(m, dict) and not bad(str(m.get("value", "")))]
        if everything and _missing(title + " " + str(text or ""), source):
            print(f"Проверка цифр{' ' + who if who else ''}: цифра не из источника новости, но есть во входных данных — {str(title)[:70]}", file=sys.stderr)
        if bad_t or removed or len(good) != len(metrics or []):
            print(f"Проверка цифр{' ' + who if who else ''}: заголовок {'СНЯТ' if bad_t else 'ок'}, фраз снято {removed}, плашек снято {len(metrics or []) - len(good)} — {str(title)[:70]} | нет: {bad_t} {[_missing(s, wide) for s in parts if s not in keep]}", file=sys.stderr)
        if not _active():
            return True, text, metrics
        return not bad_t and (bool(new) or not text), new, good
    except Exception as e:  # сбой проверки — не повод останавливать выпуск
        print("Проверка цифр: сбой, новость идёт как есть:", str(e)[:150], file=sys.stderr)
        return True, text, metrics


NEWS_RE = re.compile(r"((?:^|\n)(?:❗️\s*)?)(\d️?⃣)(\s*.*?<b>)(.*?)(</b>[^\n]*\n\s*<blockquote>)(.*?)(</blockquote>)", re.S)
KEYCAP = "1️⃣ 2️⃣ 3️⃣ 4️⃣ 5️⃣ 6️⃣ 7️⃣ 8️⃣ 9️⃣".split()


def check_block(html, by_link, everything):
    """Блок выжимки: новость сверяем с её источником по ссылке; нет такой ссылки — со всем, что читал ИИ.
    Заголовок с чужой цифрой — новость снимается, номера идут подряд. Остальной текст блока — фразы сверяем со всем входом."""
    try:
        n = [0]

        def one(m):
            pre, cap, head, title, mid, body, end = m.groups()
            link = re.search(r'<a href="([^"]+)"', body)
            tail = re.search(r'\s*—\s*<a href="[^"]+">[^<]*</a>\s*$', body)
            core = body[:tail.start()] if tail else body
            source = by_link.get(link.group(1)) if link else None
            ok, new, _ = check_news(re.sub("<[^>]+>", "", title), core, [], source or "", "выжимка", everything)
            if not ok:
                return ""
            n[0] += 1
            return pre + KEYCAP[min(n[0], 9) - 1] + head + title + mid + new + (tail.group(0) if tail else "") + end

        out = NEWS_RE.sub(one, html)
        rest = NEWS_RE.split(out)[0]  # вступление до первой новости (итог дня целиком) — построчно
        if rest.strip():
            out = check_plain(rest, everything) + out[len(rest):]
        return out
    except Exception as e:
        print("Проверка цифр: сбой в блоке, блок идёт как есть:", str(e)[:150], file=sys.stderr)
        return html


def check_plain(html, source):
    """Текст без новостей с источником (итог дня): непроверенная фраза снимается; строки, где фразы не осталось, тоже."""
    try:
        lines = []
        for line in html.split("\n"):
            keep = [s for s in SENT.split(line) if not (_missing(s, source) or bad_price(s))]
            if len(keep) != len(SENT.split(line)):
                print("Проверка цифр итога: снято —", line[:120], file=sys.stderr)
                if not _active():
                    keep = SENT.split(line)
            new = " ".join(keep)
            if new.count("<u>") != new.count("</u>"):
                new = re.sub(r"</?u>", "", new)
            for tag in ("b", "i", "blockquote"):
                if new.count(f"<{tag}>") != new.count(f"</{tag}>"):
                    new = line if not _active() else re.sub(rf"</?{tag}>", "", new)
            if new.strip() or not line.strip():
                lines.append(new)
        return "\n".join(lines)
    except Exception as e:
        print("Проверка цифр: сбой в итоге, текст идёт как есть:", str(e)[:150], file=sys.stderr)
        return html
