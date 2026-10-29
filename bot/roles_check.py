"""Раз в неделю: сверка блока «АКТУАЛЬНЫЕ ДОЛЖНОСТИ» из prompts.py: Википедия по каждой строке + ИИ сравнивает (ROLES_GROUNDED=on — Gemini с поиском Google, нужен платный ключ).

Зачем: ИИ пишет должности по этому списку; устаревшая строка («глава ФРС — Пауэлл») уйдёт во все выпуски.
Запуск — cron воркера по понедельникам 06:00 МСК (mode=roles_check); итог — только в тестовый канал.
mode=roles_check:test — то же, но в копию списка подставлена одна неверная должность (проверить, что сигнал приходит).
"""
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from . import prompts

# поиск Google (google_search) на бесплатном ключе недоступен: модели 3.x — 429, 2.x сняты (29.10) → по умолчанию Википедия + обычный ИИ
FAKE = ("глава ЕЦБ — Кристин Лагард", "глава ЕЦБ — Марио Драги")  # для roles_check:test

SYSTEM = """Ты — фактчекер. Сегодня {today}. Тебе дают список «должность — человек».
Проверь КАЖДОЕ утверждение поиском Google на сегодняшнюю дату. Сомнительные мелочи (формулировка, неполное название) не считай ошибкой —
ошибка только если человек сейчас НЕ занимает эту должность или должность занимает другой человек.
Ответь ТОЛЬКО JSON без пояснений: {{"checked": <сколько утверждений проверил>, "wrong": [{{"claim": "<как в списке>", "fact": "<как на самом деле, коротко>", "source": "<ссылка>"}}]}}.
Если всё верно — "wrong": []."""


def roles_block(text=None):
    text = text or prompts.COMMON
    return text[text.index("АКТУАЛЬНЫЕ ДОЛЖНОСТИ"):text.index("Если в новостях должность")].split("\n", 1)[1].strip()


def ask_grounded(system, user, models):
    """Gemini с инструментом google_search; JSON-режим с поиском несовместим — разбираем текст."""
    key = os.environ["GEMINI_API_KEY"]
    body = json.dumps({
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "tools": [{"google_search": {}}],
        "generationConfig": {"temperature": 0},
    }).encode()
    last = None
    for model in [m.strip() for m in models.split(",") if m.strip()]:
        req = urllib.request.Request(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            data=body, headers={"Content-Type": "application/json", "x-goog-api-key": key})
        try:
            with urllib.request.urlopen(req, timeout=240) as r:
                data = json.load(r)
            parts = data["candidates"][0]["content"]["parts"]
            text = "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
            if text:
                print(f"roles_check: {model}", file=sys.stderr)
                return model, text
            last = "пустой ответ"
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            last = f"{e.code} " + " ".join(raw.split())[:1500]
        except Exception as e:
            last = str(e)[:200]
        print(f"roles_check {model}: {last}", file=sys.stderr)
    raise RuntimeError(f"Gemini с поиском недоступен: {last}")


WIKI = "https://ru.wikipedia.org/w/api.php"
UA = {"User-Agent": "pulse-collector roles_check (github.com/KikiLove18/pulse-collector)"}
WIKI_SYSTEM = """Ты — фактчекер. Сегодня {today}. Тебе дают утверждения «должность — человек» и к каждому — начало статьи Википедии
об этом человеке (статья может отставать на несколько месяцев). Для КАЖДОГО утверждения сравни должность с выдержкой:
ошибка — если выдержка говорит, что человек занимал эту должность в прошлом (срок «2011—2019», «бывший», «экс-», назван преемник),
или никогда её не занимал, а занимает другую. Новое назначение, которого нет в выдержке (статья отстаёт), — НЕ ошибка.
Нет данных или выдержка не о том человеке — НЕ ошибка. Ответь ТОЛЬКО JSON: {{"checked": <число>, "wrong": [{{"claim": "<как в списке>", "fact": "<что в Википедии, коротко>", "source": "<ссылка на статью>"}}]}}."""


def _wiki(params):
    """Википедия режет частые запросы с общих адресов GitHub (429) — пауза между запросами и повтор с ожиданием."""
    q = urllib.parse.urlencode({**params, "format": "json", "formatversion": 2, "maxlag": 5})
    for wait in (3, 10, 0):
        time.sleep(1.5)
        try:
            with urllib.request.urlopen(urllib.request.Request(f"{WIKI}?{q}", headers=UA), timeout=30) as r:
                data = json.load(r)
            if "query" in data:
                return data
            err = data.get("error", {}).get("code", "нет query")
        except urllib.error.HTTPError as e:
            err = e.code
            wait = wait and min(20, max(wait, int(e.headers.get("Retry-After") or 0)))
        if not wait:
            raise RuntimeError(f"Википедия: {err}")
        time.sleep(wait)


POS = re.compile(r"^(глава|президент|министр|председатель|пресс|вице|торговый|зампред|первый|наследный|канцлер|член|исполнительный|основатель|со-CEO)", re.I)


def name_of(claim):
    """Человек из строки: «глава ЕЦБ — Кристин Лагард» → «Кристин Лагард», «Миллер — Газпром» → «Миллер»."""
    c = re.sub(r"^[А-Яа-я ]+:\s*", "", re.sub(r"\(.*?\)", "", claim)).strip()
    left, _, right = c.partition(" — ")
    side = right if POS.match(left) or re.fullmatch(r"[A-Za-z .&-]+", left.strip()) else left
    return re.sub(r"^(глава|со-CEO)\s+", "", re.split(r",| и ", side)[0].strip()).strip()


def _pages(titles):
    """Начала статей пачками по 20: {запрошенное: (название, текст)}; страницы-неоднозначности и отсутствующие — пропуск."""
    out = {}
    for i in range(0, len(titles), 20):
        part = titles[i:i + 20]
        try:
            q = _wiki({"action": "query", "prop": "extracts|pageprops", "ppprop": "disambiguation", "exintro": 1,
                       "explaintext": 1, "exchars": 900, "redirects": 1, "titles": "|".join(part)})["query"]
        except Exception as e:
            print(f"wiki extracts: {e}", file=sys.stderr)
            continue
        alias = {x["from"]: x["to"] for x in q.get("normalized", []) + q.get("redirects", [])}
        pages = {p["title"]: p for p in q["pages"]}
        for t in part:
            p = pages.get(alias.get(alias.get(t, t), alias.get(t, t)))
            if p and not p.get("missing") and "pageprops" not in p and p.get("extract"):
                out[t] = (p["title"], p["extract"])
    return out


def wiki_extracts(claims):
    """По каждому утверждению — статья Википедии о ЧЕЛОВЕКЕ (по имени; иначе поиск «имя + должность») и её начало.
    Статья о человеке говорит, кем он был и до какого года, — так видна подмена («глава ЕЦБ — Марио Драги»)."""
    names = {c: name_of(c) for c in claims}
    found = _pages(sorted(set(names.values())))
    miss, t0 = [c for c in claims if names[c] not in found], time.time()
    hits = {}
    for c in miss:
        if time.time() - t0 > 240:  # не дольше 4 минут: остальное — «не найдено», ИИ это не считает ошибкой
            break
        try:
            q = f"{names[c]} {re.sub(r'[(].*?[)]|^[А-Яа-я ]+:', '', c).replace(names[c], '').replace('—', ' ')}"
            res = _wiki({"action": "query", "list": "search", "srsearch": q, "srlimit": 1})["query"]["search"]
            if res:
                hits[c] = res[0]["title"]
        except Exception as e:
            print(f"wiki search {c[:40]}: {e}", file=sys.stderr)
    more = _pages(sorted(set(hits.values())))
    print(f"wiki: по имени {len(claims) - len(miss)}, поиском {sum(1 for c in hits if hits[c] in more)} из {len(miss)}, {time.time() - t0:.0f} с", file=sys.stderr)
    return [(c, *(found.get(names[c]) or more.get(hits.get(c)) or (None, ""))) for c in claims]


def parse(text):
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0)) if m else None


def run(now, models, send, test=False, ask=None):
    block = roles_block()
    if test:
        assert FAKE[0] in block, "в списке нет строки для подмены"
        block = block.replace(*FAKE)
    claims = [c.strip(" .") for line in block.split("\n") for c in re.split(r";\s*(?![^()]*\))|(?<=[а-яa-z»)])\.\s+", line)]
    claims = [c for c in claims if "—" in c]
    today = now.strftime("%d.%m.%Y")
    tag = " (тест: подменена 1 строка)" if test else ""
    try:
        if os.environ.get("ROLES_GROUNDED") == "on":  # поиск Google — только на платном ключе Gemini
            model, text = ask_grounded(SYSTEM.format(today=today), "\n".join(f"- {c}" for c in claims), models)
            how = f"{model}, поиск Google"
        else:
            ext = wiki_extracts(claims)
            found = sum(1 for _, t, x in ext if x)
            text = ask(WIKI_SYSTEM.format(today=today), "\n\n".join(
                f"- {c}\n  Википедия «{t}» (https://ru.wikipedia.org/wiki/{urllib.parse.quote((t or '').replace(' ', '_'))}): {x or 'не найдено'}"
                for c, t, x in ext))
            how = f"Википедия {found}/{len(claims)} + ИИ"
        res = parse(text)
        if res is None:
            raise RuntimeError(f"ответ не JSON: {text[:200]!r}")
    except Exception as e:
        send(f"⚠️ <b>Сверка должностей{tag}</b> не прошла: {html.escape(str(e))[:300]}\nПовторить: mode=roles_check")
        return None
    wrong = res.get("wrong") or []
    print(json.dumps(res, ensure_ascii=False, indent=1), file=sys.stderr)
    if not wrong:
        send(f"✅ <b>Сверка должностей{tag}</b> {today}: {len(claims)} строк, расхождений нет ({how}).")
        return res
    def link(w):
        url = re.sub(r'["<>]', "", str(w.get("source", "")))
        return f' — <a href="{url}">источник</a>' if url.startswith("http") else ""
    rows = [f"• {html.escape(w.get('claim', ''))}\n  → <b>{html.escape(w.get('fact', ''))}</b>{link(w)}" for w in wrong]
    send(f"⚠️ <b>Сверка должностей{tag}</b> {today}: расхождений — {len(wrong)} из {len(claims)}.\n\n" + "\n".join(rows)
         + f"\n\nПроверить поиском и поправить «АКТУАЛЬНЫЕ ДОЛЖНОСТИ» в bot/prompts.py (Техник/Публикатор). Сверено: {how}.")
    return res


if __name__ == "__main__":  # локально: python -m bot.roles_check — только печать списка
    print("\n".join(roles_block().split("\n")))
    print(datetime.now(timezone.utc))
