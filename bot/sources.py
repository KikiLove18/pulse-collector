"""Источники новостей. Чтобы добавить ленту — допиши строку в FEEDS."""

# (категория, название, ссылка на RSS)
FEEDS = [
    # Крипта
    ("crypto", "CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss"),
    ("crypto", "Cointelegraph", "https://cointelegraph.com/rss"),
    ("crypto", "The Block", "https://www.theblock.co/rss.xml"),
    ("crypto", "Decrypt", "https://decrypt.co/feed"),
    ("hacks", "Rekt", "https://rekt.news/rss/feed.xml"),
    ("crypto", "CryptoSlate", "https://cryptoslate.com/feed/"),
    ("crypto", "The Defiant", "https://thedefiant.io/api/feed"),
    # Рынок и макро США
    ("macro", "WSJ Markets", "https://feeds.content.dowjones.io/public/rss/RSSMarketsMain"),
    ("macro", "WSJ Economy", "https://feeds.content.dowjones.io/public/rss/socialeconomyfeed"),
    ("macro", "CNBC Markets", "https://www.cnbc.com/id/10000664/device/rss/rss.html"),
    ("macro", "CNBC Economy", "https://www.cnbc.com/id/20910258/device/rss/rss.html"),
    ("macro", "ФРС (пресс-релизы)", "https://www.federalreserve.gov/feeds/press_all.xml"),
    ("macro", "Investing.com", "https://www.investing.com/rss/news_25.rss"),
    ("macro", "MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
    # Сырьё
    ("commodities", "OilPrice", "https://oilprice.com/rss/main"),
    # Россия: рынок, ЦБ, экономика
    ("ru", "РБК", "https://rssexport.rbc.ru/rbcnews/news/30/full.rss"),
    ("ru", "Ведомости", "https://www.vedomosti.ru/rss/news.xml"),
    ("ru", "Интерфакс", "https://www.interfax.ru/rss.asp"),
    ("ru", "Коммерсантъ", "https://www.kommersant.ru/RSS/news.xml"),
    ("ru", "ТАСС", "https://tass.ru/rss/v2.xml"),
    ("ru", "Банки.ру", "https://www.banki.ru/xml/news.rss"),
    ("ru", "Банк России", "https://www.cbr.ru/rss/eventrss"),
    # Люди
    ("people", "Трамп (Truth Social)", "https://www.trumpstruth.org/feed"),
]

# Экономический календарь на неделю (ForexFactory)
CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
# Какие страны и важность событий берём
CALENDAR_COUNTRIES = {"USD"}
CALENDAR_IMPACT = {"High", "Medium"}

# Публичные Telegram-каналы — читаем их веб-версию t.me/s/<имя>, аккаунт не нужен
# Список совпадает с реестром SRC в worker/index.js: у каждого человека включены только каналы его рынков
TG_CHANNELS = [
    # Россия
    "centralbank_russia", "minfin", "russianmacro", "rbc_news", "interfaxonline", "smartlabnews", "if_market_news",
    "cbrstocks", "sberinvestments", "gpb_investments", "alfa_investments", "investfuture", "banksta", "thebell_io",
    # мировые рынки и США
    "markettwits", "bitkogan", "bitkogan_hotline", "headlines_for_traders", "proeconomics",
    # крипта
    "cryptokogan", "incrypted", "forklog", "coinpost", "konenkov_official", "invest_zonaa", "nainspire",
]

# Официальные Telegram-каналы популярных X-аккаунтов — бесплатная замена X API
X_MIRRORS = [
    "walterbloomberg",       # @DeItaone: заголовки, заявления Трампа, Маска, ФРС
    "WatcherGuru",           # @WatcherGuru
    "wublockchainenglish",   # @WuBlockchain
    "BitcoinMagazineTelegram",  # @BitcoinMagazine
    "whale_alert_io",        # @whale_alert
    "lookonchainchannel",    # @lookonchain
    "financialjuice",        # @financialjuice
]
