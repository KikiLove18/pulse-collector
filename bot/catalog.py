"""Каталог активов для поиска в приложении: 50 основных крипты, Мосбиржи, США и сырьё (владелец 03.11, Директор q-1793691114/q-1793691648).
Отдельно от quotes: воркер хранит его в meta.catalog и отдаёт /api/app/catalog лениво (ETag), главный экран не тяжелеет.
Запросов за сборку: CoinGecko 1, Мосбиржа 1, Yahoo spark — пакетами по 20 (США 3 + сырьё 1). Что не скачалось — без цены, каталог не падает.
Формат элемента: k ключ (как в quotes/profile.watch), t тикер, n рус, en англ, al синонимы, m ru|us|crypto|com|fx, r ранг, p цена, ch изм. %, i — свой значок /img/a/<k>.png."""
import json
import re
import sys
import urllib.parse

# США — крупнейшие по капитализации, в каталог идут первые TOP (список ручной, пересматривать раз в квартал). Ключ = символ Yahoo; совпадение с Мосбиржей — суффикс .US
US_TOP = [
    ("NVDA", "Nvidia", "Nvidia", ["Нвидиа", "Нвидия"]), ("AAPL", "Apple", "Apple", ["Эпл", "Эппл", "Айфон"]),
    ("MSFT", "Microsoft", "Microsoft", ["Майкрософт"]), ("GOOGL", "Alphabet (Google)", "Alphabet", ["Гугл", "Google", "Альфабет"]),
    ("AMZN", "Amazon", "Amazon", ["Амазон"]), ("META", "Meta (Facebook)", "Meta Platforms", ["Мета", "Фейсбук", "Facebook"]),
    ("AVGO", "Broadcom", "Broadcom", ["Бродком"]), ("TSLA", "Tesla", "Tesla", ["Тесла", "Маск"]),
    ("BRK-B", "Berkshire Hathaway", "Berkshire Hathaway", ["Баффет", "Беркшир"]), ("TSM", "TSMC", "Taiwan Semiconductor", ["Тайвань", "ТСМЦ"]),
    ("LLY", "Eli Lilly", "Eli Lilly", ["Илай Лилли", "Лилли"]), ("JPM", "JPMorgan", "JPMorgan Chase", ["Джей Пи Морган", "Морган"]),
    ("WMT", "Walmart", "Walmart", ["Уолмарт"]), ("V", "Visa", "Visa", ["Виза"]), ("ORCL", "Oracle", "Oracle", ["Оракл"]),
    ("MA", "Mastercard", "Mastercard", ["Мастеркард"]), ("XOM", "ExxonMobil", "Exxon Mobil", ["Эксон"]),
    ("NFLX", "Netflix", "Netflix", ["Нетфликс"]), ("COST", "Costco", "Costco", ["Костко"]), ("JNJ", "Johnson & Johnson", "Johnson & Johnson", ["Джонсон"]),
    ("PLTR", "Palantir", "Palantir", ["Палантир"]), ("HD", "Home Depot", "Home Depot", ["Хоум Депо"]), ("PG", "Procter & Gamble", "Procter & Gamble", ["Проктер"]),
    ("ABBV", "AbbVie", "AbbVie", ["Эббви"]), ("BAC", "Bank of America", "Bank of America", ["Бэнк оф Америка"]),
    ("UNH", "UnitedHealth", "UnitedHealth", ["Юнайтед Хелс"]), ("KO", "Coca-Cola", "Coca-Cola", ["Кока-кола", "Кола"]),
    ("AMD", "AMD", "Advanced Micro Devices", ["АМД"]), ("CVX", "Chevron", "Chevron", ["Шеврон"]), ("GE", "GE Aerospace", "GE Aerospace", ["Дженерал Электрик"]),
    ("CSCO", "Cisco", "Cisco", ["Циско"]), ("WFC", "Wells Fargo", "Wells Fargo", ["Уэллс Фарго"]), ("PM", "Philip Morris", "Philip Morris", ["Филип Моррис"]),
    ("IBM", "IBM", "IBM", ["АйБиЭм"]), ("CRM", "Salesforce", "Salesforce", ["Сейлсфорс"]), ("MS", "Morgan Stanley", "Morgan Stanley", ["Морган Стэнли"]),
    ("GS", "Goldman Sachs", "Goldman Sachs", ["Голдман"]), ("ABT", "Abbott", "Abbott Laboratories", ["Эбботт"]), ("MU", "Micron", "Micron", ["Микрон"]),
    ("LIN", "Linde", "Linde", ["Линде"]), ("MCD", "McDonald's", "McDonald's", ["Макдональдс"]), ("AXP", "American Express", "American Express", ["Амекс"]),
    ("T.US", "AT&T", "AT&T", ["ЭйТиэндТи"]), ("MRK", "Merck", "Merck", ["Мерк"]), ("DIS", "Disney", "Walt Disney", ["Дисней"]),
    ("RTX", "RTX (Raytheon)", "RTX", ["Рейтеон"]), ("UBER", "Uber", "Uber", ["Убер"]), ("PEP", "PepsiCo", "PepsiCo", ["Пепси"]),
    ("INTU", "Intuit", "Intuit", ["Интуит"]), ("APP", "AppLovin", "AppLovin", ["Эпловин"]), ("CAT", "Caterpillar", "Caterpillar", ["Катерпиллар"]),
    ("NOW", "ServiceNow", "ServiceNow", ["Сервиснау"]), ("TMO", "Thermo Fisher", "Thermo Fisher", ["Термо Фишер"]), ("VZ", "Verizon", "Verizon", ["Верайзон"]),
    ("BKNG", "Booking", "Booking Holdings", ["Букинг"]), ("ISRG", "Intuitive Surgical", "Intuitive Surgical", ["Интуитив"]),
    ("C", "Citigroup", "Citigroup", ["Ситигрупп", "Сити"]), ("BLK", "BlackRock", "BlackRock", ["Блэкрок"]), ("QCOM", "Qualcomm", "Qualcomm", ["Квалкомм"]),
    ("SCHW", "Charles Schwab", "Charles Schwab", ["Шваб"]), ("TXN", "Texas Instruments", "Texas Instruments", ["Техас"]),
    ("ANET", "Arista Networks", "Arista Networks", ["Ариста"]), ("SPGI", "S&P Global", "S&P Global", ["Эс энд Пи"]), ("AMGN", "Amgen", "Amgen", ["Амджен"]),
    ("ADBE", "Adobe", "Adobe", ["Адоби", "Фотошоп"]), ("BA", "Boeing", "Boeing", ["Боинг"]), ("NEE", "NextEra Energy", "NextEra Energy", ["НекстЭра"]),
    ("GILD", "Gilead", "Gilead Sciences", ["Гилеад"]), ("PFE", "Pfizer", "Pfizer", ["Пфайзер"]), ("LRCX", "Lam Research", "Lam Research", ["Лам"]),
    ("AMAT", "Applied Materials", "Applied Materials", ["Эпплайд"]), ("UNP", "Union Pacific", "Union Pacific", ["Юнион Пасифик"]),
    ("SHOP", "Shopify", "Shopify", ["Шопифай"]), ("PGR", "Progressive", "Progressive", ["Прогрессив"]), ("KLAC", "KLA", "KLA", ["КЛА"]),
    ("DHR", "Danaher", "Danaher", ["Данахер"]), ("HON", "Honeywell", "Honeywell", ["Ханивелл"]), ("TJX", "TJX", "TJX Companies", ["ТиДжейЭкс"]),
    ("ETN", "Eaton", "Eaton", ["Итон"]), ("PANW", "Palo Alto Networks", "Palo Alto Networks", ["Пало Альто"]), ("COF", "Capital One", "Capital One", ["Кэпитал Уан"]),
    ("BX", "Blackstone", "Blackstone", ["Блэкстоун"]), ("CRWD", "CrowdStrike", "CrowdStrike", ["Краудстрайк"]), ("ADP", "ADP", "Automatic Data Processing", ["АДП"]),
    ("DE", "Deere", "Deere & Company", ["Джон Дир"]), ("LMT", "Lockheed Martin", "Lockheed Martin", ["Локхид"]), ("CMCSA", "Comcast", "Comcast", ["Комкаст"]),
    ("INTC", "Intel", "Intel", ["Интел"]), ("SBUX", "Starbucks", "Starbucks", ["Старбакс"]), ("MDT", "Medtronic", "Medtronic", ["Медтроник"]),
    ("ADI", "Analog Devices", "Analog Devices", ["Аналог"]), ("NKE", "Nike", "Nike", ["Найк"]), ("SNPS", "Synopsys", "Synopsys", ["Синопсис"]),
    ("MO", "Altria", "Altria", ["Алтрия"]), ("SO", "Southern Company", "Southern Company", ["Саузерн"]), ("COIN", "Coinbase", "Coinbase", ["Коинбейс"]),
    ("MSTR", "Strategy", "Strategy (MicroStrategy)", ["Микростратеджи", "MicroStrategy", "Сэйлор"]), ("HOOD", "Robinhood", "Robinhood", ["Робинхуд"]),
    ("SPOT", "Spotify", "Spotify", ["Спотифай"]), ("PYPL", "PayPal", "PayPal", ["Пейпал"]), ("ABNB", "Airbnb", "Airbnb", ["Эйрбиэнби"]),
    ("F", "Ford", "Ford", ["Форд"]), ("GM", "General Motors", "General Motors", ["Дженерал Моторс"]),
]
# Сырьё — фьючерсы Yahoo (первые пять уже в quotes)
COMMOD = [
    ("BZ=F", "Нефть Brent", "Brent Crude", ["Брент", "нефть"]), ("CL=F", "Нефть WTI", "WTI Crude", ["нефть", "ВТИ"]),
    ("GC=F", "Золото", "Gold", ["gold", "золото"]), ("SI=F", "Серебро", "Silver", ["silver"]), ("NG=F", "Газ (США)", "Natural Gas", ["газ", "gas"]),
    ("HG=F", "Медь", "Copper", ["copper"]), ("PL=F", "Платина", "Platinum", ["platinum"]), ("PA=F", "Палладий", "Palladium", ["palladium", "Норникель"]),
    ("ALI=F", "Алюминий", "Aluminum", ["aluminium", "Русал"]), ("ZW=F", "Пшеница", "Wheat", ["зерно", "wheat"]),
    ("ZC=F", "Кукуруза", "Corn", ["corn"]), ("ZS=F", "Соя", "Soybeans", ["soy"]), ("KC=F", "Кофе", "Coffee", ["coffee"]),
    ("SB=F", "Сахар", "Sugar", ["sugar"]), ("CC=F", "Какао", "Cocoa", ["cocoa", "шоколад"]), ("CT=F", "Хлопок", "Cotton", ["cotton"]),
]
CRYPTO_RU = {"bitcoin": ["Биткоин", "Биток"], "ethereum": ["Эфир", "Эфириум"], "tether": ["Тезер", "Доллар"], "ripple": ["Рипл", "Икс-ар-пи"],
             "binancecoin": ["Бинанс"], "solana": ["Солана"], "usd-coin": ["ЮСДС"], "dogecoin": ["Доги", "Догикоин"], "tron": ["Трон"],
             "cardano": ["Кардано"], "the-open-network": ["Тон", "Телеграм"], "chainlink": ["Чейнлинк"], "litecoin": ["Лайткоин"],
             "avalanche-2": ["Аваланч"], "shiba-inu": ["Шиба"], "polkadot": ["Полкадот"], "stellar": ["Стеллар"], "monero": ["Монеро"],
             "bitcoin-cash": ["Биткоин кэш"], "sui": ["Суи"], "hyperliquid": ["Гиперликвид"], "pepe": ["Пепе"], "near": ["Нир"],
             "uniswap": ["Юнисвоп"], "aptos": ["Аптос"], "ethereum-classic": ["Эфир классик"]}


RU_NAMES = {"X5": "X5 (Пятёрочка)", "VKCO": "ВК (VK)", "OZON": "Озон", "HEAD": "Хэдхантер", "MTSS": "МТС", "AFKS": "АФК Система", "MOEX": "Мосбиржа"}


def _clean_ru(name):
    """«Сбербанк России ПАО ао» → «Сбербанк России», «ап» → «(преф)»."""
    pref = bool(re.search(r"\bап\b|-п$|\bпр\.", name))
    s = re.sub(r'\b(ПАО|ОАО|АО|МКПАО|МК ПАО|ао|ап|ГДР|адр)\b|["«»]', " ", name)
    s = re.sub(r"\(\s*\)|акц\.?\s*пр\.?|\d+\s*вып\.?|\(\d+\s*в\)", " ", s)
    s = re.sub(r"^(НК|ГК|АКБ)\s+", "", re.sub(r"\s+", " ", s).strip(" -,"))
    return s + (" (преф)" if pref and "преф" not in s else "")


def _spark(http_get, symbols):
    """Yahoo spark: до 20 символов за запрос → {символ: (цена, изм.%)}"""
    out = {}
    for i in range(0, len(symbols), 20):
        part = symbols[i:i + 20]
        for attempt in (1, 2):  # пакет иногда не отвечает (таймаут) — один повтор
            try:
                d = json.loads(http_get("https://query1.finance.yahoo.com/v8/finance/spark?range=1d&interval=1d&symbols="
                                        + ",".join(urllib.parse.quote(s) for s in part)))
                break
            except Exception as e:
                print("Каталог, Yahoo:", e, file=sys.stderr)
                d = {}
        for s, v in d.items():
            if not isinstance(v, dict):
                continue
            price = (v.get("close") or [None])[-1] or v.get("fulldayPrice")
            prev = v.get("chartPreviousClose")
            if price:
                out[s] = (price, (price / prev - 1) * 100 if prev else None)
    return out


def _r(x, d=4):
    """цена: меньше 1 — d значащих цифр (мелкие монеты), иначе 2 знака"""
    return None if x is None else float(f"{x:.{d}g}") if abs(x) < 1 else round(x, 2)


def _c(x):
    return None if x is None else round(x, 2)


TOP = 50  # владелец 03.11 14:40: 50 основных в группе, приложение не медленнее (каталог ≤8 КБ сжатым)


def _item(k, t, n, en, al, m, r, p, ch):
    """пустое и повторы не пишем — каталог легче"""
    x = {"k": k, "t": t, "n": n, "m": m, "r": r, "p": p}
    if en and en != n:
        x["en"] = en
    al = [a for a in dict.fromkeys(al) if a and a.lower() not in (n.lower(), t.lower(), k.lower(), (en or "").lower())]
    if al:
        x["al"] = al
    if ch is not None:
        x["ch"] = ch
    return x


def build_catalog(http_get, q, names, ru_keys):
    """q — уже скачанные quotes (их цены не качаем второй раз; их активы всегда в каталоге), names — QUOTE_NAMES, ru_keys — акции Мосбиржи из quotes."""
    items = []
    # крипта: топ-100 по капитализации одним запросом
    try:
        cg = json.loads(http_get("https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=market_cap_desc&per_page=100&page=1"))
        cg = cg[:TOP] + [c for c in cg[TOP:] if c["id"] in q]
        for i, c in enumerate(cg, 1):
            sym = (c.get("symbol") or "").upper()
            items.append(_item(c["id"], sym, c.get("name") or sym, "", CRYPTO_RU.get(c["id"], []), "crypto", i,
                               _r(c.get("current_price"), 6), _c(c.get("price_change_percentage_24h"))))
    except Exception as e:
        print("Каталог, CoinGecko:", e, file=sys.stderr)
    # Мосбиржа: все акции TQBR одним запросом, топ-100 по капитализации (оборот до открытия торгов нулевой — список прыгал бы)
    try:
        d = json.loads(http_get("https://iss.moex.com/iss/engines/stock/markets/shares/boards/TQBR/securities.json?iss.meta=off"
                                "&iss.only=marketdata,securities&marketdata.columns=SECID,LAST,LASTTOPREVPRICE"
                                "&securities.columns=SECID,SHORTNAME,SECNAME,LATNAME,PREVPRICE,ISSUESIZE"))
        md = {r[0]: r[1:] for r in d["marketdata"]["data"]}
        sec = sorted(d["securities"]["data"], key=lambda r: -((r[4] or 0) * (r[5] or 0)))
        top = [r for r in sec if r[0] in ru_keys] + [r for r in sec if r[0] not in ru_keys]
        top = sorted(top[:TOP], key=lambda r: -((r[4] or 0) * (r[5] or 0)))
        for i, (sid, short, full, lat, prev, _) in enumerate(top, 1):
            last, chg = md.get(sid, (None, None))
            n = names.get(sid) or RU_NAMES.get(sid) or _clean_ru(full or short)
            items.append(_item(sid, sid, n, re.sub(r"\s+(ao|ap)$", "", lat or ""), [_clean_ru(short), _clean_ru(full or "")], "ru", i,
                               _r(last or prev), _c(chg if last else 0.0)))
    except Exception as e:
        print("Каталог, Мосбиржа:", e, file=sys.stderr)
    # США и сырьё: Yahoo spark пакетами; то, что уже есть в quotes, не качаем
    rows = [(r, "us") for r in US_TOP[:TOP] + [x for x in US_TOP[TOP:] if x[0] in q]] + [(r, "com") for r in COMMOD]
    sym = lambda k: k[:-3] if k.endswith(".US") else k
    need = [sym(r[0]) for r, _ in rows if not (q.get(r[0]) or {}).get("price")]
    px = _spark(http_get, need)
    rank = {"us": 0, "com": 0}
    for (k, n, en, al), m in rows:
        rank[m] += 1
        v = q.get(k) or {}
        p, ch = (v.get("price"), v.get("chg")) if v.get("price") else px.get(sym(k), (None, None))
        items.append(_item(k, sym(k).replace("=F", ""), n, en, al, m, rank[m], _r(p), _c(ch)))
    return items
