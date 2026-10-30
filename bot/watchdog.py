"""Сторож воркера с другой площадки: встал cron или сломан вебхук — все сигналы воркера замолчат вместе с ним, поэтому проверка и сигнал идут отсюда, с GitHub, напрямую в Telegram.
python bot/watchdog.py check — печатает key=<событие> в GITHUB_OUTPUT, пусто, если всё в порядке. python bot/watchdog.py send — шлёт WATCH_TEXT владельцу.
WATCH_FAKE=cron|down|webhook — подмена для проверки: сигнал уходит в тестовый канал."""
import json, os, sys, time, urllib.parse, urllib.request

APP = "https://app.pulse-rynka.ru"
TEST = "-1004362280478"
TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
FAKE = os.environ.get("WATCH_FAKE", "")


def get(url):
    return json.load(urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "pulse-watchdog"}), timeout=20))


def check():
    """(ключ события, текст) или None. Один ключ — один сигнал, поэтому в ключе то, что отличает один сбой от другого."""
    day = time.strftime("%Y%m%d", time.gmtime())
    try:
        h = get(APP + "/api/health?cron=1")
        if FAKE == "down":
            raise OSError("подмена")
    except Exception as e:
        print("воркер не отвечает:", e)
        return f"down-{day}", "⚠️ <b>Бот не отвечает.</b> Приложение и рассылки сейчас не работают, это сообщение пришло в обход бота. Сообщите Технику: «воркер не отвечает»."
    age = h.get("cron_age_min")
    print("cron, минут назад:", age)
    if FAKE == "cron":
        age = 99
    if age is None or age >= 30:
        # минуты последнего cron одинаковы на весь сбой: один сигнал на сбой
        stamp = round((time.time() / 60 - age) / 5) if age is not None else day  # cron идёт по 5-минутным окнам
        return f"cron-{stamp}", f"⚠️ <b>Расписание бота стоит уже {age} мин.</b> Не выходят новости, рассылки, посты и не приходят другие сигналы о сбоях. Сообщите Технику: «cron воркера встал»."
    try:
        w = get(f"https://api.telegram.org/bot{TOKEN}/getWebhookInfo")["result"]
    except Exception as e:  # сбой самой проверки — не повод для сигнала, через 30 мин повторим
        print("getWebhookInfo не ответил:", type(e).__name__)
        return None
    pending, last = w.get("pending_update_count", 0), w.get("last_error_date", 0)
    print("вебхук: очередь", pending, "ошибка минут назад", int((time.time() - last) / 60) if last else "нет")
    bad_url = not w.get("url", "").startswith(APP)
    if FAKE == "webhook" or bad_url or pending >= 20 or (pending >= 5 and time.time() - last < 1800):
        return f"webhook-{day}", f"⚠️ <b>Бот не получает сообщения людей.</b> В очереди {pending}{', адрес вебхука неверный' if bad_url else ''}. Сообщите Технику: «сломан вебхук»."
    return None


if sys.argv[1] == "check":
    r = check()
    with open(os.environ["GITHUB_OUTPUT"], "a") as o:
        if r:
            o.write(f"key=watchdog-{r[0]}{'-' + os.environ['GITHUB_RUN_ID'] if FAKE else ''}\ntext={r[1]}\n")
else:
    data = urllib.parse.urlencode({"chat_id": TEST if FAKE else os.environ["OWNER_CHAT_ID"], "text": ("Проверка сторожа. " if FAKE else "") + os.environ["WATCH_TEXT"], "parse_mode": "HTML"}).encode()
    print(urllib.request.urlopen(f"https://api.telegram.org/bot{TOKEN}/sendMessage", data, timeout=20).status)
