"""Канал PRO, рубрика «События дня» · #СОБЫТИЯ — каждый вечер в 20:30 МСК (обещано в навигации канала).

Календарь дня: время МСК, флаг рынка, событие, факт против прогноза (итоги — из журнала недели, их собирает run_results)
и 3–4 главных события завтра. Без ИИ — только данные календаря, ничего не додумываем.

Куда: переменная PRO_DAILY — "on" → канал PRO (только после «да» владельца), "test" → тестовый канал каждый вечер,
пусто/"off" → в авто не выходит. Ручной запуск `mode=pro_day` — всегда в тестовый канал.
Карточка: пока без картинки; шаблон рубрики делает Дизайнер (knowledge/дизайн/рубрики-pro-v2.md) — тогда card_url.
"""
import os
import re
import sys
from datetime import datetime, timedelta

from bot import calendar_future, edition, pro, weekly

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


def run(state, now, msk, fetch_calendar, tg_html, test=False):
    """Собрать и отправить. test=True — всегда тестовый канал. Возвращает html или None."""
    chat = os.environ.get("TELEGRAM_CHAT_ID") if test or _mode() != "on" else os.environ.get("PRO_CHAT_ID")
    try:
        events = calendar_future.build(fetch_calendar(state), horizon_days=2)
    except Exception as e:
        print("События дня: календарь не собран —", e, file=sys.stderr)
        return None
    html = build(events, weekly.load_week()["results"], now, msk)
    if not html:
        print("События дня: сегодня событий нет — пост не нужен", file=sys.stderr)
        return None
    html = tg_html(html)
    print("----- #СОБЫТИЯ -----\n" + html, file=sys.stderr)
    if chat:
        pro._send(chat, html, None)
    return html
