"""Свои Telegram-каналы и сайты подписчиков PRO: читаем публичные посты и отдаём воркеру.

Репозиторий и журналы запусков публичные: адреса не печатаем и в state/ не пишем — только количества
и имя класса ошибки. Текст постов не идёт в ИИ и не попадает в общую Ленту, выжимки и рассылки.
"""
import calendar
import html
import http.client
import ipaddress
import re
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import feedparser

from .edition import _post

BUDGET_S = 90          # на весь проход
PER_SOURCE_S = 20      # на один источник
TIMEOUT_S = 10         # на одну операцию с сетью
MAX_BYTES = 1_500_000  # предел размера ответа
MAX_REDIRECTS = 3
MAX_POSTS = 10
PUT_CHUNK = 20
UA = "Mozilla/5.0 (market-digest-bot)"
TG_RE = re.compile(r"^[A-Za-z0-9_]{4,32}$")
AZURE_SERVICE = ipaddress.ip_address("168.63.129.16")  # служебный адрес раннера GitHub, is_global его не отсекает


def _public(ip):
    a = ipaddress.ip_address(ip.split("%")[0])
    a = getattr(a, "ipv4_mapped", None) or a
    return a.is_global and not a.is_multicast and a != AZURE_SERVICE


def _connect(address, timeout=TIMEOUT_S, source_address=None, *_a, **_k):
    """Соединение только с публичным адресом: проверяем то, к чему подключаемся, — подмена DNS не проходит."""
    infos = socket.getaddrinfo(address[0], address[1], type=socket.SOCK_STREAM)
    if not infos or not all(_public(i[4][0]) for i in infos):
        raise ValueError("internal")
    err = None
    for fam, typ, proto, _, sa in infos:
        s = socket.socket(fam, typ, proto)
        try:
            s.settimeout(timeout)
            s.connect(sa)
            return s
        except OSError as e:
            err = e
            s.close()
    raise err


class _Http(http.client.HTTPConnection):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self._create_connection = _connect


class _Https(http.client.HTTPSConnection):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self._create_connection = _connect


class _HttpH(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_Http, req)


class _HttpsH(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_Https, req, context=self._context)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_a, **_k):  # переходы проверяем сами, каждый заново
        return None


_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _HttpH, _HttpsH, _NoRedirect)


def _ok_url(url):
    p = urllib.parse.urlsplit(url)
    return p.scheme in ("http", "https") and p.hostname and not p.username and p.port in (None, 80, 443)


def safe_get(url, deadline):
    """Только http/https, порты 80/443, публичные адреса, предел времени и размера."""
    for _ in range(MAX_REDIRECTS + 1):
        if not _ok_url(url):
            raise ValueError("url")
        try:
            with _opener.open(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=TIMEOUT_S) as r:
                buf = bytearray()
                while True:
                    if time.monotonic() > deadline:
                        raise TimeoutError
                    chunk = r.read(65536)
                    if not chunk:
                        return bytes(buf)
                    buf += chunk
                    if len(buf) > MAX_BYTES:
                        raise ValueError("size")
        except urllib.error.HTTPError as e:
            location = e.headers.get("Location")
            e.close()
            if e.code not in (301, 302, 303, 307, 308) or not location:
                raise
            url = urllib.parse.urljoin(url, location)
    raise ValueError("redirects")


def _text(raw, limit):
    raw = re.sub(r"<[^>]+>", " ", raw or "")
    return re.sub(r"\s+", " ", html.unescape(raw)).strip()[:limit]


def _read_tg(handle, deadline):
    if not TG_RE.match(handle):
        return "fail", []
    page = safe_get(f"https://t.me/s/{handle}", deadline).decode("utf-8", "replace")
    blocks = page.split('class="tgme_widget_message_wrap')[1:]
    if not blocks:  # закрытый канал или нет такого: t.me/s/ уводит на страницу без постов
        return "fail", []
    posts = []
    for block in blocks:
        post = re.search(r'data-post="([A-Za-z0-9_]+/\d+)"', block)
        when = re.search(r'<time datetime="([^"]+)"', block)
        body = re.search(r'tgme_widget_message_text[^>]*>(.*?)</div>', block, re.S)
        if not (post and when and body):
            continue
        text = _text(re.sub(r"<br\s*/?>", " ", body.group(1)), 700)
        if not text:
            continue
        try:
            ts = int(datetime.fromisoformat(when.group(1)).timestamp()) * 1000
        except ValueError:
            ts = 0
        posts.append({"t": text[:200], "x": text[200:700], "u": f"https://t.me/{post.group(1)}", "ts": ts})
    posts.sort(key=lambda p: p["ts"], reverse=True)
    return "ok", posts[:MAX_POSTS]


def _feed_posts(feed):
    posts = []
    for e in feed.entries:
        title, link = _text(e.get("title"), 200), e.get("link") or ""
        if not (title and link.startswith(("http://", "https://"))):
            continue
        when = e.get("published_parsed") or e.get("updated_parsed")
        posts.append({"t": title, "x": _text(e.get("summary"), 500), "u": link[:500],
                      "ts": calendar.timegm(when) * 1000 if when else 0})
    posts.sort(key=lambda p: p["ts"], reverse=True)
    return posts[:MAX_POSTS]


def _feed_link(page, base):
    for tag in re.findall(r"<link\b[^>]*>", page, re.I):
        if re.search(r"application/(rss|atom)\+xml", tag, re.I) and re.search(r"alternate", tag, re.I):
            href = re.search(r"""href\s*=\s*["']([^"']+)""", tag, re.I)
            if href:
                return urllib.parse.urljoin(base, html.unescape(href.group(1)))
    return None


def _read_web(url, deadline):
    data = safe_get(url, deadline)
    posts = _feed_posts(feedparser.parse(data))
    if posts:
        return "ok", posts
    link = _feed_link(data[:300000].decode("utf-8", "replace"), url)
    if not link:
        return "no_feed", []
    posts = _feed_posts(feedparser.parse(safe_get(link, deadline)))
    return ("ok", posts) if posts else ("no_feed", [])


def _read_one(item, stop_at):
    """Не успели — None: статус останется прежним, источник встанет первым в следующий раз."""
    now = time.monotonic()
    if now > stop_at:
        return None
    deadline = now + PER_SOURCE_S
    try:
        read = _read_tg if item["kind"] == "tg" else _read_web
        status, posts = read(item["handle"], deadline)
    except Exception:
        status, posts = "fail", []
    return {"key": item["key"], "status": status, "posts": posts}


def run(budget_s=BUDGET_S):
    """Один проход. Любой сбой здесь не должен мешать остальному запуску."""
    try:
        items = (_post("/api/own/list", {}) or {}).get("items") or []
    except Exception as e:
        print(f"Свои источники: список не получен ({type(e).__name__})", file=sys.stderr)
        return
    stop_at = time.monotonic() + budget_s
    with ThreadPoolExecutor(max_workers=8) as ex:
        res = [r for r in ex.map(lambda i: _read_one(i, stop_at), items) if r]
    sent = 0
    for i in range(0, len(res), PUT_CHUNK):
        try:
            _post("/api/own/put", {"items": res[i:i + PUT_CHUNK]})
            sent += len(res[i:i + PUT_CHUNK])
        except Exception as e:
            print(f"Свои источники: запись не прошла ({type(e).__name__})", file=sys.stderr)
    count = lambda s: sum(r["status"] == s for r in res)
    print(f"Свои источники: всего {len(items)}, читается {count('ok')}, сбой {count('fail')}, без RSS {count('no_feed')}, "
          f"не успели {len(items) - len(res)}, записано {sent}", file=sys.stderr)


def due(state, now, msk):
    return state.get("own_hour") != f"{now.astimezone(msk):%Y-%m-%d %H}"
