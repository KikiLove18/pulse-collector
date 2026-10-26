"""Канал PRO, рубрика «События дня» · #СОБЫТИЯ — каждый вечер в 20:30 МСК (обещано в навигации канала).

Календарь дня: время МСК, флаг рынка, событие, факт против прогноза (итоги — из журнала недели, их собирает run_results)
и 3–4 главных события завтра. Без ИИ — только данные календаря, ничего не додумываем.

Куда: переменная PRO_DAILY — "on" → канал PRO (только после «да» владельца), "test" → тестовый канал каждый вечер,
пусто/"off" → в авто не выходит. Ручной запуск `mode=pro_day` — всегда в тестовый канал.
Карусель v6 (утверждена владельцем 26.10): evc_data() — данные шаблона `evc` Дизайнера (поля items/sum/next, как DATA.evc
в pro-рубрики/rubrics-v6.html); цифры ставит код, разбор (s, hint, why, fol, mk, nx) пишет ИИ одним запросом.
Рендер картинок — шаблоном из bot/pro_tpl/ (пакет Дизайнера), пока его нет — данные только в журнал запуска.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
import uuid
from datetime import datetime, timedelta

from bot import calendar_future, edition, pro, prompts, weekly

FLAG = {"ru": "🇷🇺", "us": "🇺🇸", "oil": "🛢", "gold": "🥇", "crypto": "₿"}
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]
HOUR = (20, 30)          # время выхода МСК
MAX_TODAY, MAX_TOMORROW = 9, 4  # с картинкой подпись ≤1024 знаков — держим список коротким
SKIP = ("Выступление", "Пресс-конференция ФРС", "Заявление ФРС")  # речи и дубли решения ФРС — не события дня


def _mode():
    return (os.environ.get("PRO_DAILY") or "off").lower()


def due(state, now, msk):
    """Пора ли: сегодня ещё не выходило, 20:30–22:59 МСК, PRO_DAILY on/test."""
    local = now.astimezone(msk)
    if _mode() not in ("on", "test"):
        return False
    if (local.hour, local.minute) < HOUR or local.hour >= 23:
        return False
    return state.get("pro_daily") != f"{local:%Y-%m-%d}"


def _num(v):
    """«$2.01» → «$2,01» (ru_numbers принял бы за дату), «198K» → «198 тыс.», «−0.3M» → «−0,3 млн»."""
    if not v:
        return ""
    v = re.sub(r"\$(\d+)\.(\d+)", r"$\1,\2", str(v))
    v = re.sub(r"(\d)K\b", r"\1 тыс.", re.sub(r"(\d)M\b", r"\1 млн", re.sub(r"(\d)B\b", r"\1 млрд", v)))
    return edition.ru_numbers(v)


def _match(ev, results):
    """Итог для события: то же время (±75 мин) и похожее название."""
    dt = datetime.fromisoformat(ev["dt"])
    n = calendar_future._norm(ev["title"])
    for r in results:
        try:
            rdt = datetime.fromisoformat(r["dt"])
        except Exception:
            continue
        rn = calendar_future._norm(r.get("title") or "")
        if abs((rdt - dt).total_seconds()) <= 75 * 60 and (rn == n or rn in n or n in rn):
            return r
    return None


def _line(ev, msk, res=None):
    t = datetime.fromisoformat(ev["dt"]).astimezone(msk)
    flag = FLAG.get(ev.get("region") or calendar_future.region(ev), "")
    head = f"{t:%H:%M} {flag} {'<b>' + ev['title'] + '</b>' if ev.get('impact') == 'High' else ev['title']}"
    if res:
        verdict = pro.VERDICT_RU.get(res.get("verdict"), "")
        return f"{head} — {_num(res.get('headline'))}" + (f" · {verdict}" if verdict else "")
    extra = ", ".join(x for x in (f"прогноз {_num(ev['forecast'])}" if ev.get("forecast") else "",
                                  f"прошлое {_num(ev['previous'])}" if ev.get("previous") else "") if x)
    return head + (f" — {extra}" if extra else "")


def build(events, results, now, msk):
    """HTML поста или None (сегодня нет событий — выходной)."""
    local = now.astimezone(msk)
    today, tomorrow = local.date(), (local + timedelta(days=1)).date()

    def day_of(e):
        return datetime.fromisoformat(e["dt"]).astimezone(msk).date()

    def keep(e):
        return e.get("impact") in ("High", "Medium") and not any(s in e["title"] for s in SKIP)

    evs = sorted((e for e in events if keep(e)), key=lambda e: e["dt"])
    todays = [e for e in evs if day_of(e) == today]
    if not todays:
        return None
    if len(todays) > MAX_TODAY:  # сильные — всегда, средние — сколько влезет
        strong = [e for e in todays if e.get("impact") == "High"]
        rest = [e for e in todays if e.get("impact") != "High"][:max(0, MAX_TODAY - len(strong))]
        todays = sorted(strong + rest, key=lambda e: e["dt"])[:MAX_TODAY]
    res_today = [r for r in results if (r.get("dt") or "")[:10] in (str(today), str(today - timedelta(days=1)))]
    lines = [_line(e, msk, _match(e, res_today)) for e in todays]
    nxt = [e for e in evs if day_of(e) == tomorrow]
    nxt = sorted(nxt, key=lambda e: (e.get("impact") != "High", e["dt"]))[:MAX_TOMORROW]
    html = f"<b>События дня · {today.day} {MONTHS[today.month - 1]}</b>\n\n" + "\n".join(lines)
    if nxt:
        html += "\n\n<b>Завтра</b>\n" + "\n".join(_line(e, msk) for e in sorted(nxt, key=lambda e: e["dt"]))
    html += "\n\nВремя — московское. Жирным — сильные события.\n\n#СОБЫТИЯ"
    return html


DAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
WEEKDAYS = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
MAX_SLIDES = 6
VD = {"better": ("better", "Лучше ожиданий"), "worse": ("worse", "Хуже ожиданий"),
      "inline": ("", "Как ждали"), "neutral": ("", "Как ждали")}


def _nums(headline):
    """«Первичные заявки: 194K при прогнозе 200K (прошлое 200K)» → [[Факт,194 тыс.],[Прогноз,200 тыс.],[Было,200 тыс.]]."""
    h = re.sub(r"(\d)М\b", r"\1M", re.sub(r"\s*\(сюрприз[^)]*\)", "", headline or ""))  # кириллическая «М» в итогах EIA
    m = re.match(r"(?:.*?:\s*)?(?:факт\s+|EPS\s+)?(.+?)\s+при прогнозе\s+(.+?)(?:\s*\(прошлое\s+(.+?)\))?$", h)
    if not m:
        return [["Факт", _num(h)]] if h else []
    out = [["Факт", _num(m[1])], ["Прогноз", _num(m[2])]]
    return out + [["Было", _num(m[3])]] if m[3] else out


def evc_data(events, results, now, msk, ask):
    """Данные карусели evc на сегодня или None (ничего не вышло). ask(system, user) — ИИ; сбой ИИ → без разбора."""
    local = now.astimezone(msk)
    today = local.date()
    outs = [r for r in results if r.get("dt") and datetime.fromisoformat(r["dt"]).astimezone(msk).date() == today]
    outs = sorted(outs, key=lambda r: (r.get("impact") != "High", r["dt"]))[:MAX_SLIDES]
    if not outs:
        return None
    outs.sort(key=lambda r: r["dt"])
    items = []
    for r in outs:
        ev = next((e for e in events if _match(e, [r])), {})
        nums = _nums(r.get("headline"))
        vd, vt = VD.get(r.get("verdict"), ("", ""))
        items.append({"tm": f"{datetime.fromisoformat(r['dt']).astimezone(msk):%H:%M}",
                      "fl": FLAG.get(ev.get("region") or (calendar_future.region(ev) if ev else "us"), "🇺🇸"),
                      "who": "", "t": r["title"], "big": nums[0][1] if nums else "", "img": "",
                      "nums": nums, "vd": vd, "vt": vt, "s": _num(r.get("headline")), "hint": "", "why": "", "fol": "",
                      "mk": [], "nx": ""})
    later = sorted((e for e in events if datetime.fromisoformat(e["dt"]).astimezone(msk).date() > today
                    and e.get("impact") == "High" and not any(x in e["title"] for x in SKIP)), key=lambda e: e["dt"])[:6]

    def when(e):
        t = datetime.fromisoformat(e["dt"]).astimezone(msk)
        return f"{DAYS[t.weekday()]} {t:%d.%m} · {t:%H:%M}"

    data = {"tag": "СОБЫТИЯ", "day": f"{WEEKDAYS[today.weekday()]}, {today.day} {MONTHS[today.month - 1]}",
            "k": "События дня · итоги · время МСК", "sub": "Что вышло, почему и что это значит — листайте →",
            "cover": [], "items": items, "sum": [],
            "next": {"k": "ГЛАВНОЕ ВПЕРЕДИ", "t": when(later[0]), "s": later[0]["title"]} if later else None,
            "src": "Источники: календарь Пульса рынка · Не является инвестиционной рекомендацией"}
    user = "СЕГОДНЯ ВЫШЛО:\n" + "\n".join(
        f"{i + 1}. {it['tm']} {it['fl']} {it['t']}: " + ", ".join(f"{k} {v}" for k, v in it["nums"]) + f" — {it['vt'] or 'без прогноза'}"
        for i, it in enumerate(items)) + "\n\nЗАВТРА И ДАЛЕЕ:\n" + ("\n".join(f"{when(e)} {e['title']}" for e in later) or "—")
    try:
        ai = edition._json(ask(prompts.PRO_DAY, user))
        for a in ai.get("items") or []:
            i = int(a.get("n") or 0) - 1
            if 0 <= i < len(items):
                it = items[i]
                for k in ("who", "s", "hint", "why", "fol", "nx"):
                    if isinstance(a.get(k), str) and a[k].strip():
                        it[k] = edition.ru_numbers(a[k].strip())
                mk = [m for m in a.get("mk") or [] if isinstance(m, list) and len(m) == 4 and m[0] in ("up", "dn", "eq")]
                it["mk"] = [[m[0], m[1][:1].upper() + m[1][1:], m[2], m[3]] for m in mk[:3]]
                if it["who"] and f"({it['who']})" in it["t"]:  # «… (S&P Global)» — источник уже в строке who
                    it["t"] = it["t"].replace(f"({it['who']})", "").replace(" ,", ",").strip(" ,")
        data["sum"] = [s for s in ai.get("sum") or [] if isinstance(s, list) and len(s) == 2][:3]
        if data["next"] and isinstance(ai.get("next"), str) and ai["next"].strip():
            data["next"]["s"] = ai["next"].strip()
    except Exception as e:
        print("События дня: разбор ИИ не получен —", e, file=sys.stderr)
    data["ai"] = all(it["why"] and it["mk"] for it in items)
    return data


def run(state, now, msk, fetch_calendar, tg_html, test=False, horizon=2, ask=None):
    """Собрать и отправить. test=True — всегда тестовый канал. Возвращает html или None."""
    chat = os.environ.get("TELEGRAM_CHAT_ID") if test or _mode() != "on" else os.environ.get("PRO_CHAT_ID")
    try:
        events = calendar_future.build(fetch_calendar(state), horizon_days=horizon)
    except Exception as e:
        print("События дня: календарь не собран —", e, file=sys.stderr)
        return None
    html = build(events, weekly.load_week()["results"], now, msk)
    pics = []
    if ask:  # карусель v6 (шаблон evc из bot/pro_tpl): разбор ИИ → картинки → альбом; сбой — пост текстом, как раньше
        try:
            evc = evc_data(events, weekly.load_week()["results"], now, msk, ask)
            print("----- evc.json -----\n" + json.dumps(evc, ensure_ascii=False, indent=1), file=sys.stderr)
            if evc and evc.get("items"):
                pics = _render(evc)
        except Exception as e:
            print("События дня: карусель не собрана —", e, file=sys.stderr)
    if not html:
        print("События дня: сегодня событий нет — пост не нужен", file=sys.stderr)
        return None
    html = tg_html(html)
    print("----- #СОБЫТИЯ -----\n" + html, file=sys.stderr)
    if chat:
        if not (pics and _album(chat, pics, html)):
            pro._send(chat, html, None)
    return html


TPL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pro_tpl")


def _render(evc):
    """Карусель evc v6 → список путей к jpg. Playwright ставится сам (только вечером, ~1 мин)."""
    if subprocess.run(["node", "-e", "require('playwright')"], cwd=TPL, capture_output=True).returncode:
        subprocess.run(["npm", "i", "--no-save", "--no-audit", "--no-fund", "playwright@1"], cwd=TPL, check=True, capture_output=True, timeout=300)
        subprocess.run(["npx", "playwright", "install", "chromium"], cwd=TPL, check=True, capture_output=True, timeout=300)
    out = tempfile.mkdtemp(prefix="evc")
    f = os.path.join(out, "evc.json")
    with open(f, "w", encoding="utf-8") as fh:
        json.dump(evc, fh, ensure_ascii=False)
    r = subprocess.run(["node", "render.js", "evc", "--full", "--data", f, "--out", out], cwd=TPL, capture_output=True, text=True, timeout=300)
    print("Рендер evc:", r.returncode, (r.stdout + r.stderr)[-600:], file=sys.stderr)
    pics = sorted((x for x in os.listdir(out) if re.fullmatch(r"evc\d+\.jpg", x)), key=lambda x: int(re.sub(r"\D", "", x)))
    return [os.path.join(out, x) for x in pics][:10]


def _album(chat, pics, html):
    """Альбом (sendMediaGroup); подпись — текст поста, если влезает в 1024, иначе текст отдельным сообщением. True — ушло."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        return False
    cap = len(re.sub(r"<[^>]+>", "", html)) <= 1000
    media = [{"type": "photo", "media": f"attach://p{i}", **({"caption": html, "parse_mode": "HTML"} if cap and i == 0 else {})} for i in range(len(pics))]
    fields = {"chat_id": str(chat), "media": json.dumps(media, ensure_ascii=False)}
    if str(chat) == str(os.environ.get("PRO_CHAT_ID") or ""):
        fields["protect_content"] = "true"
    b = uuid.uuid4().hex
    body = b"".join(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode() for k, v in fields.items())
    for i, p in enumerate(pics):
        with open(p, "rb") as fh:
            body += f'--{b}\r\nContent-Disposition: form-data; name="p{i}"; filename="evc{i + 1}.jpg"\r\nContent-Type: image/jpeg\r\n\r\n'.encode() + fh.read() + b"\r\n"
    body += f"--{b}--\r\n".encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMediaGroup", data=body,
                                 headers={"Content-Type": f"multipart/form-data; boundary={b}"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            ok = json.loads(r.read()).get("ok")
    except Exception as e:
        print("События дня: альбом не отправлен —", e, file=sys.stderr)
        return False
    print("События дня: альбом", len(pics), "карт., ok", ok, file=sys.stderr)
    if ok and not cap:
        pro._send(chat, html, None)
    return bool(ok)
