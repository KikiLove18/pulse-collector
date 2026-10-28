"""Канал PRO: «мощный пост» дня — черновик подписи от ИИ на «Утвердить» владельцу (t-1793163385436-operator, 28.10).

Факторы берутся из данных приложения (POST /api/meta воркера — только ключи APP_META, без ПД): итоги `results`, календарь
`events`, котировки `quotes`, новости `news` (24 ч), «Влияние» `outlook`, макро `macro`. Каждый факт даёт баллы теме
(ФРС, нефть, бигтех…), сильнейшая тема дня = пост. Правило и веса — market-news-bot knowledge/дизайн/pro-авто-пост.md
(там же исходник tools/pro_auto.py — отбор здесь тот же).

Каждое утро (авто, первый запуск после 07:30 МСК, не позже 10:00): бриф → ИИ (prompts.PRO_POST) → проверка формата
(один повтор) → meta.pro_draft {at, brief, caption, ok, problems} + образец в тестовый канал. В канал PRO — ничего:
карточки и «Утвердить» владельца делает Публикатор. PRO_AUTO=off — выключить. Ручной запуск: mode=pro_auto.
"""
import json
import re
import sys
from datetime import datetime, timedelta, timezone

from bot import edition, prompts

MSK = timezone(timedelta(hours=3))
HOUR, LAST = (7, 30), 10  # окно выхода МСК: с 07:30 до 10:00
TAGS = {"итог": "#ИТОГИ", "событие": "#СОБЫТИЕ"}
TAG_THEME = {"fed": "#ИНДИКАТОР", "cbr": "#ИНДИКАТОР", "infl": "#ИНДИКАТОР", "jobs": "#ИНДИКАТОР",
             "oil": "#АКТИВ", "gold": "#АКТИВ", "btc": "#АКТИВ", "bigtech": "#НАРРАТИВ", "ru": "#НАРРАТИВ"}


def _tag(theme):
    """Рубрика по самому весомому факту темы: вышли данные — #ИТОГИ, ждём событие — #СОБЫТИЕ, иначе по теме."""
    return (TAGS.get(theme["factors"][0]["kind"]) if theme["factors"] else None) or TAG_THEME[theme["key"]]


# тема → слова в событиях/новостях, котировки темы, рынок
THEMES = {
    "fed": ("ФРС и ставка США", r"ФРС|FOMC|Уорш|Fed\b|ставк[аиуе] ФРС", ["^TNX", "DX-Y.NYB", "^GSPC"], "us"),
    "cbr": ("ЦБ РФ и ключевая ставка", r"ЦБ РФ|Банк России|ключев\w+ ставк|Набиуллин", ["RGBI", "IMOEX"], "ru"),
    "infl": ("Инфляция", r"инфляц|CPI|PCE|ИПЦ", ["^TNX"], "us"),
    "jobs": ("Рынок труда США", r"Nonfarm|NFP|безработиц|занятост|заявк\w+ на пособ", ["^TNX"], "us"),
    "oil": ("Нефть", r"нефт|Brent|WTI|ОПЕК|Иран|хусит", ["BZ=F", "CL=F"], "commodities"),
    "gold": ("Золото", r"золот|gold", ["GC=F"], "commodities"),
    "btc": ("Биткоин и крипта", r"биткоин|bitcoin|крипт|ETF на BTC", ["bitcoin", "ethereum", "COIN", "MSTR"], "crypto"),
    "bigtech": ("Отчёты бигтеха и ИИ", r"Microsoft|Alphabet|Google|Apple|Amazon|Meta\b|Nvidia|Tesla|гиперскейлер|ИИ-", ["^IXIC", "NVDA", "MSFT", "AAPL", "TSLA"], "us"),
    "ru": ("Рынок России и рубль", r"МосБирж|IMOEX|рубл|санкц|Сбер|Газпром|ОФЗ", ["IMOEX", "USDRUB_CBR", "RUB=X"], "ru"),
}
MOVE = {"crypto": 4.0, "commodities": 2.0, "ru": 1.5, "us": 1.5}  # порог «сильного» движения за день, %
W = {"result_high": 5, "result_mid": 3, "surprise": 2, "event_cb": 6, "event_high": 4, "move_max": 6,
     "driver": 3, "impact": 1, "news": 0.5, "news_max": 4, "macro_fresh": 6}


def theme_of(text):
    return [k for k, t in THEMES.items() if re.search(t[1], text or "", re.I)]


def brief(meta, now):
    st = {k: {"score": 0.0, "factors": []} for k in THEMES}

    def add(keys, pts, kind, text, **extra):
        for k in keys:
            st[k]["score"] += pts
            st[k]["factors"].append({"kind": kind, "pts": round(pts, 1), "text": text, **extra})

    day_ago, ahead = now - timedelta(hours=30), now + timedelta(hours=36)
    for r in meta.get("results") or []:  # 1) итоги: что вышло за сутки и сюрприз ли
        dt = datetime.fromisoformat(r["dt"])
        if day_ago <= dt <= now:
            pts = W["result_high"] if r.get("impact") == "High" else W["result_mid"]
            pts += W["surprise"] if r.get("verdict") in ("better", "worse") else 0
            add(theme_of(r["title"]), pts, "итог", f"{r['title']}: {r.get('headline', '')}", verdict=r.get("verdict"), dt=r["dt"])
    seen = set()
    for e in sorted(meta.get("events") or [], key=lambda e: e["dt"]):  # 2) календарь: что важного в ближайшие 36 ч
        dt = datetime.fromisoformat(e["dt"])
        if now <= dt <= ahead and e.get("impact") == "High":
            keys = [k for k in theme_of(e["title"]) if (k, dt.date()) not in seen]  # решение + заявление + пресс-конференция = одно событие
            seen |= {(k, dt.date()) for k in keys}
            pts = W["event_cb"] if set(keys) & {"fed", "cbr"} else W["event_high"]
            add(keys, pts, "событие", f"{dt.astimezone(MSK):%d.%m %H:%M} МСК · {e['title']}"
                + (f" (прогноз {e['forecast']})" if e.get("forecast") else ""), dt=e["dt"])
    q = {x["key"]: x for x in meta.get("quotes") or [] if x.get("chg") is not None}
    for k, t in THEMES.items():  # 3) котировки: самое сильное движение темы относительно порога рынка
        moves = [(abs(q[s]["chg"]) / MOVE[t[3]], q[s]) for s in t[2] if s in q]
        if moves:
            m, x = max(moves, key=lambda v: v[0])
            if m >= 0.5:
                add([k], min(W["move_max"], m * 3), "котировка", f"{x['name']} {x['chg']:+.2f}% за день", price=x.get("price"))
    for d in (meta.get("outlook") or {}).get("drivers") or []:  # 4) «Влияние»: ИИ уже связал новость с активами
        keys = theme_of(d["news"] + " " + " ".join(i["asset"] for i in d.get("impacts") or []))
        add(keys[:2], W["driver"] + W["impact"] * len(d.get("impacts") or []), "влияние", d["news"],
            impacts=[f"{i['asset']}: {i['now']}, ждём {i['expect']} — {i['why']}" for i in d.get("impacts") or []])
    cnt = {k: [] for k in THEMES}
    for n in meta.get("news") or []:  # 5) новости за сутки: сколько раз тема в ленте
        if n.get("ts") and datetime.fromisoformat(n["ts"]) >= now - timedelta(hours=24):
            for k in theme_of(n["title"] + " " + n.get("text", "")):
                cnt[k].append(n["title"])
    for k, titles in cnt.items():
        if titles:
            add([k], min(W["news_max"], W["news"] * len(titles)), "новости", f"{len(titles)} новостей за сутки", top=titles[:4])
    mc = meta.get("macro") or {}  # 6) макро: решение ЦБ/ФРС сегодня-вчера
    for k, key in (("fed", "fed"), ("cbr", "cbr")):
        if (mc.get(key) or {}).get("date") and now.date() - datetime.fromisoformat(mc[key]["date"]).date() <= timedelta(days=1):
            add([k], W["macro_fresh"], "макро", json.dumps(mc[key], ensure_ascii=False))
    top = sorted(((v["score"], k) for k, v in st.items() if v["score"]), reverse=True)
    themes = [{"key": k, "title": THEMES[k][0], "market": THEMES[k][3], "score": round(s, 1),
               "factors": sorted(st[k]["factors"], key=lambda f: -f["pts"])} for s, k in top]
    kinds = lambda t: len({f["kind"] for f in t["factors"]})
    return {"at": now.astimezone(MSK).isoformat(timespec="minutes"), "pick": themes[0]["key"] if themes else None,
            "title": themes[0]["title"] if themes else None, "tag": _tag(themes[0]) if themes else None,
            "strong": bool(themes) and themes[0]["score"] >= 12 and kinds(themes[0]) >= 3,  # порог: ≥12 баллов из ≥3 разных факторов
            "macro": mc, "themes": themes[:5],
            "task": "Пост по теме pick в формате 28.10 (8 пунктов, ≈550–700 знаков, цифры — на слайды, в подписи — выводы словами, "
                    "без советов купить/продать, 👆 <b>… — на карточках выше</b> жирным). Факты сверить поиском перед тестом."}



def due(state, now, mode):
    local = now.astimezone(MSK)
    if mode == "off" or not (HOUR <= (local.hour, local.minute) and local.hour < LAST):
        return False
    return state.get("pro_auto") != f"{local:%Y-%m-%d}"


def check(caption):
    """Проверка формата 28.10 (knowledge/дизайн/pro-авто-пост.md): список замечаний, пустой — всё хорошо."""
    p, n = [], len(caption)
    if not 550 <= n <= 700:
        p.append(f"длина {n} (нужно 550–700)")
    for must in ("📌 <b>Главное</b>", "<b>Вывод:</b>", "👆 <b>"):
        if must not in caption:
            p.append(f"нет «{must}»")
    if not re.search(r"#(ИНДИКАТОР|НАРРАТИВ|АКТИВ|ИТОГИ|СОБЫТИЕ)", caption):
        p.append("нет хэштега рубрики")
    for bad in ("покупа", "прода", "не является", "[___]", "```"):
        if bad in caption.lower():
            p.append(f"запрещено «{bad}»")
    return p


def run(state, now, ask, send_test=None, dry=False):
    """Бриф → подпись ИИ → meta.pro_draft (+ образец в тестовый канал). Возвращает черновик или None."""
    meta = edition._post("/api/meta", {}) or {}
    b = brief(meta, now)
    for t in b["themes"]:
        print(f"PRO авто: {t['score']:5.1f}  {t['title']}  ·  " + ", ".join(sorted({f['kind'] for f in t['factors']})), file=sys.stderr)
    if not b["pick"]:
        print("PRO авто: данных нет — черновик не пишем", file=sys.stderr)
        return None
    user = "БРИФ дня (JSON):\n" + json.dumps({k: b[k] for k in ("at", "title", "tag", "strong", "themes", "macro")},
                                               ensure_ascii=False)
    caption, problems = "", ["ИИ не ответил"]
    for attempt in range(2):  # не прошло проверку — один повтор с замечаниями
        try:
            ask_user = user if not attempt else user + "\n\nПрошлый вариант не прошёл проверку: " + "; ".join(problems) + ". Исправь."
            caption = re.sub(r"^```(?:html)?|```$", "", ask(prompts.PRO_POST, ask_user).strip()).strip()
            caption = edition.ru_numbers(caption)
        except Exception as e:
            print("PRO авто: ошибка ИИ", e, file=sys.stderr)
            caption = ""
        problems = check(caption) if caption else ["ИИ не ответил"]
        if not problems:
            break
        print("PRO авто: проверка —", "; ".join(problems), file=sys.stderr)
    draft = {"at": b["at"], "brief": b, "caption": caption, "ok": not problems, "problems": problems}
    print("PRO авто: черновик\n" + caption, file=sys.stderr)
    if dry:
        return draft
    edition._post("/api/ingest", {"edition": "pro_draft", "blocks": [], "meta": {"pro_draft": draft}})
    if send_test:
        head = (f"🧪 <b>PRO · черновик поста дня</b> (в канал не ушёл)\nТема: {b['title']} · {b['themes'][0]['score']} баллов"
                + ("" if b["strong"] else " · <i>слабый день</i>")
                + ("" if not problems else "\n⚠️ проверить: " + "; ".join(problems)))
        send_test(head + "\n\n" + caption)
    return draft
