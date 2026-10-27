"""Тексты рубрик бесплатного канала @pulse_rynka_news (сетка v2, утверждена владельцем 27.10).

Сборщик раз в день (11:00–13:00 МСК) одним запросом к ИИ готовит рубрики дня и кладёт их в meta.freech_posts
через /api/ingest; картинки рисует и шлёт воркер (worker/freech.js, Разработчик). Здесь — только тексты.
Будни: «Лев объясняет» + Пн/Ср/Пт «Слово дня» или Вт/Чт «Цифра дня». Сб — «Неделя за 1 минуту». Вс — опрос и «Лев отвечает».
Формат дня (согласован с Разработчиком и Маркетологом): {at, lion{title,steps,you,src}, num{title,value,unit,text,src},
word{term,text,src}, week{items[{d,t}]}, poll{question,options}, answer{q,text}}. Храним 7 дней (state/freech.json).
Нет рубрики (ИИ не ответил, цифра не из новостей) — ключа нет, воркер пост пропускает. Ничего не выдумываем.
"""
import json
import re
import sys
from datetime import timedelta
from pathlib import Path

from bot import edition, weekly

FILE = Path(__file__).resolve().parent.parent / "state" / "freech.json"
KEEP_DAYS = 7
DAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
BLOCKS = {"ru": "Россия", "us": "США", "crypto": "Крипта", "commodities": "Сырьё", "x": "Люди"}

SYSTEM = """Ты — редактор бесплатного Telegram-канала «Пульс рынка» для новичков в инвестициях (Россия, США, крипта, сырьё).
Ведущий канала — Лев: объясняет рынки просто, спокойно, без жаргона, как умный друг. Читатель не трейдер.
Тебе дают новости (из источников сборщика) и итоги вышедших данных. Пиши ТОЛЬКО по ним: никаких фактов, цифр, имён и дат не из входа.
Запрещено: советы купить/продать/держать, прогнозы цен, обещания дохода, «точно вырастет». Можно: «что это значит», «на что смотреть».
Русский язык, без Markdown и HTML, без эмодзи. Пиши живо: не повторяй одно слово дважды в соседних предложениях (не «результат… результатами»), без канцелярита («такой показатель зафиксировали»). Ответ — ОДИН JSON-объект без пояснений, только с ключами из задания."""

TASKS = {
    "lion": '"lion": {"title": "заголовок до 60 знаков — о чём новость", "steps": ["3–4 коротких шага цепочки: событие → почему → что меняется → кого касается, каждый до 90 знаков"], '
            '"you": "что это значит для обычного человека, 1 предложение до 140 знаков", "src": "имя источника/рынка из входа"} — ОДНА самая понятная и важная новость дня',
    "num": '"num": {"title": "что за число, одной строкой до 50 знаков (например «Рост экономики Китая за квартал»)", "value": "одна цифра ровно как во входе, с единицей (например «16,5%» или «$92,4»)", "unit": "единица отдельно: %, $, ₽, млрд $, барр. — или пусто", "text": "что эта цифра значит простыми словами, 2 предложения до 220 знаков: начни с сути («На столько выросли…»), добавь сравнение или причину из входа", '
           '"src": "название источника из пометки «источник:» у этой новости (Reuters, Интерфакс, ТАСС…); нет пометки — пусто. Не страна и не рынок"} — «Цифра дня»: самая показательная цифра из новостей или итогов данных',
    "word": '"word": {"term": "один финансовый термин из сегодняшних новостей (например «ключевая ставка», «дивидендный гэп»)", '
            '"text": "что это, простыми словами, и при чём тут сегодняшняя новость, 2–3 предложения до 260 знаков", "src": "источник новости, где встретился термин (из пометки «источник:»), или пусто"} — «Слово дня»',
    "week": '"week": {"items": [{"d": "день недели коротко (Пн…Вс)", "t": "главное событие дня, до 80 знаков"}, … ровно 5 пунктов]} — «Неделя за 1 минуту»: 5 главных событий недели по порядку дней',
    "poll": '"poll": {"question": "вопрос читателям о главной теме недели, до 100 знаков, без советов и угадывания цен", "options": ["2–4 варианта, каждый до 40 знаков"]} — воскресный опрос',
    "answer": '"answer": {"q": "частый вопрос новичка по теме недели, до 90 знаков", "text": "ответ Льва простыми словами, 3–4 предложения до 400 знаков"} — «Лев отвечает»',
}


def plan(weekday):
    """Какие рубрики нужны в этот день (0 — понедельник)."""
    if weekday == 5:
        return ["week"]
    if weekday == 6:
        return ["poll", "answer"]
    return ["lion", "num" if weekday in (1, 3) else "word"]


def load():
    try:
        return json.loads(FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def due(state, now, msk):
    local = now.astimezone(msk)
    return 11 <= local.hour < 13 and state.get("freech_day") != f"{local:%Y-%m-%d}"


def _input(week, local, days):
    since = (local - timedelta(days=days)).isoformat()
    news = sorted((n for n in week["news"] if n["ts"] >= since), key=lambda n: (-n["imp"], n["ts"]))[:60]
    lines = [f"[{BLOCKS.get(n['block'], n['block'])}] {DAYS[int(_dt(n['ts'], local).weekday())][:2].title()} | {n['title']} — "
             f"{re.sub('<[^>]+>', '', n['text'])}" + (f" (источник: {n['src']})" if n.get("src") else "") for n in news]
    res = [json.dumps(r, ensure_ascii=False)[:300] for r in week.get("results", []) if r.get("dt", "") >= since][-15:]
    return lines, res


def _dt(ts, local):
    from datetime import datetime
    return datetime.fromisoformat(ts).astimezone(local.tzinfo)


def _clean(kind, v, source):
    """Проверка ответа ИИ: нужные поля, длины, цифра — из входа."""
    if not isinstance(v, dict):
        return None
    s = lambda x, n: str(x or "").strip()[:n]
    if kind == "lion":
        steps = [s(x, 120) for x in (v.get("steps") or []) if s(x, 120)][:4]
        out = {"title": s(v.get("title"), 80), "steps": steps, "you": s(v.get("you"), 180), "src": s(v.get("src"), 60)}
        return out if out["title"] and len(steps) >= 3 and out["you"] else None
    if kind == "num":
        out = {"title": s(v.get("title"), 60), "value": s(v.get("value"), 24), "unit": s(v.get("unit"), 12), "text": s(v.get("text"), 280), "src": s(v.get("src"), 60)}
        if out["src"] in ("США", "Россия", "Китай", "крипта", "сырьё", "Европа", "Мир"):
            out["src"] = ""
        digits = re.sub(r"\D", "", out["value"])
        return out if digits and digits in re.sub(r"\D", "", source) and out["text"] else None
    if kind == "word":
        out = {"term": s(v.get("term"), 40), "text": s(v.get("text"), 320), "src": s(v.get("src"), 80)}
        return out if out["term"] and out["text"] else None
    if kind == "week":
        items = [{"d": s(i.get("d"), 3), "t": s(i.get("t"), 100)} for i in (v.get("items") or []) if isinstance(i, dict) and s(i.get("t"), 100)][:5]
        return {"items": items} if len(items) >= 4 else None
    if kind == "poll":
        opts = [s(x, 50) for x in (v.get("options") or []) if s(x, 50)][:4]
        out = {"question": s(v.get("question"), 120), "options": opts}
        return out if out["question"] and len(opts) >= 2 else None
    if kind == "answer":
        out = {"q": s(v.get("q"), 110), "text": s(v.get("text"), 480)}
        return out if out["q"] and out["text"] else None
    return None


def build(now, msk, ask):
    """Рубрики дня → {kind: {...}}; пустые/непроверенные отбрасываются."""
    local = now.astimezone(msk)
    kinds = plan(local.weekday())
    lines, res = _input(weekly.load_week(), local, 7 if local.weekday() >= 5 else 1)
    if not lines:
        print("Бесплатный канал: нет новостей за период — рубрики пропущены", file=sys.stderr)
        return {}
    source = "\n".join(lines + res)
    user = (f"Сегодня {DAYS[local.weekday()]}, {local.day} {edition.MONTHS[local.month - 1]}.\n"
            f"Задание — верни JSON с ключами: " + "; ".join(TASKS[k] for k in kinds) +
            f"\n\nНовости ({'неделя' if local.weekday() >= 5 else 'последние сутки'}):\n" + "\n".join(lines) +
            ("\n\nИтоги вышедших данных:\n" + "\n".join(res) if res else ""))
    raw = ask(SYSTEM, user)
    try:
        data = edition._json(raw)
    except Exception as e:
        print("Бесплатный канал: ИИ вернул не JSON", e, raw[:200], file=sys.stderr)
        return {}
    out = {}
    for k in kinds:
        v = _clean(k, data.get(k), source)
        if v:
            out[k] = v
        else:
            print(f"Бесплатный канал: рубрика {k} не прошла проверку — пропуск", file=sys.stderr)
    return out


def preview(day, posts):
    """Текст образца для тестового канала (как выглядят данные рубрик)."""
    p = [f"<b>Бесплатный канал — тексты рубрик на {day}</b> (образец, картинки рисует воркер)"]
    if "lion" in posts:
        l = posts["lion"]
        p.append(f"\n<b>🦁 Лев объясняет: {l['title']}</b>\n" + "\n".join(f"{i}. {x}" for i, x in enumerate(l["steps"], 1)) + f"\n<i>Для вас:</i> {l['you']}\n<i>{l['src']}</i>")
    if "num" in posts:
        p.append(f"\n<b>🔢 Цифра дня: {posts['num']['value']}</b>\n{posts['num']['text']}" + (f"\n<i>{posts['num']['src']}</i>" if posts['num']['src'] else ""))
    if "word" in posts:
        p.append(f"\n<b>📖 Слово дня: {posts['word']['term']}</b>\n{posts['word']['text']}\n<i>{posts['word']['src']}</i>")
    if "week" in posts:
        p.append("\n<b>🗓 Неделя за 1 минуту</b>\n" + "\n".join(f"{i['d']} — {i['t']}" for i in posts["week"]["items"]))
    if "poll" in posts:
        p.append(f"\n<b>📊 Опрос:</b> {posts['poll']['question']}\n" + "\n".join(f"▫️ {o}" for o in posts["poll"]["options"]))
    if "answer" in posts:
        p.append(f"\n<b>🦁 Лев отвечает: {posts['answer']['q']}</b>\n{posts['answer']['text']}")
    return "\n".join(p)


def run(state, now, msk, ask, send_test=None, dry=False):
    local = now.astimezone(msk)
    day = f"{local:%Y-%m-%d}"
    posts = build(now, msk, ask)
    if not posts:
        return None
    store = load()
    store[day] = {"at": now.isoformat(timespec="seconds"), **posts}
    cut = f"{(local - timedelta(days=KEEP_DAYS)):%Y-%m-%d}"
    store = {d: v for d, v in sorted(store.items()) if d > cut}
    if dry:
        print(json.dumps(store[day], ensure_ascii=False, indent=1))
    else:
        FILE.parent.mkdir(exist_ok=True)
        FILE.write_text(json.dumps(store, ensure_ascii=False, indent=0), encoding="utf-8")
        try:
            edition._post("/api/ingest", {"edition": "freech", "blocks": [], "meta": {"freech_posts": store}})
            print(f"Бесплатный канал: рубрики {list(posts)} на {day} → meta.freech_posts", file=sys.stderr)
        except Exception as e:
            print("Бесплатный канал: ingest не удался", e, file=sys.stderr)
    if send_test:
        send_test(preview(day, posts))
    return posts
