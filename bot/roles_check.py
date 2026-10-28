"""Раз в неделю: сверка блока «АКТУАЛЬНЫЕ ДОЛЖНОСТИ» из prompts.py с поиском Google (Gemini + google_search).

Зачем: ИИ пишет должности по этому списку; устаревшая строка («глава ФРС — Пауэлл») уйдёт во все выпуски.
Запуск — cron воркера по понедельникам 06:00 МСК (mode=roles_check); итог — только в тестовый канал.
mode=roles_check:test — то же, но в копию списка подставлена одна неверная должность (проверить, что сигнал приходит).
"""
import html
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

from . import prompts

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


def parse(text):
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0)) if m else None


def run(now, models, send, test=False):
    block = roles_block()
    if test:
        assert FAKE[0] in block, "в списке нет строки для подмены"
        block = block.replace(*FAKE)
    claims = [c.strip(" .") for line in block.split("\n") for c in re.split(r";\s*(?![^()]*\))|(?<=[а-яa-z»)])\.\s+", line)]
    claims = [c for c in claims if "—" in c]
    today = now.strftime("%d.%m.%Y")
    tag = " (тест: подменена 1 строка)" if test else ""
    try:
        model, text = ask_grounded(SYSTEM.format(today=today), "\n".join(f"- {c}" for c in claims), models)
        res = parse(text)
        if res is None:
            raise RuntimeError(f"ответ не JSON: {text[:200]!r}")
    except Exception as e:
        send(f"⚠️ <b>Сверка должностей{tag}</b> не прошла: {html.escape(str(e))[:300]}\nПовторить: mode=roles_check")
        return None
    wrong = res.get("wrong") or []
    print(json.dumps(res, ensure_ascii=False, indent=1), file=sys.stderr)
    if not wrong:
        send(f"✅ <b>Сверка должностей{tag}</b> {today}: {len(claims)} строк, расхождений нет ({model}, поиск Google).")
        return res
    def link(w):
        url = re.sub(r'["<>]', "", str(w.get("source", "")))
        return f' — <a href="{url}">источник</a>' if url.startswith("http") else ""
    rows = [f"• {html.escape(w.get('claim', ''))}\n  → <b>{html.escape(w.get('fact', ''))}</b>{link(w)}" for w in wrong]
    send(f"⚠️ <b>Сверка должностей{tag}</b> {today}: расхождений — {len(wrong)} из {len(claims)}.\n\n" + "\n".join(rows)
         + "\n\nПроверить поиском и поправить «АКТУАЛЬНЫЕ ДОЛЖНОСТИ» в bot/prompts.py (Техник/Публикатор).")
    return res


if __name__ == "__main__":  # локально: python -m bot.roles_check — только печать списка
    print("\n".join(roles_block().split("\n")))
    print(datetime.now(timezone.utc))
