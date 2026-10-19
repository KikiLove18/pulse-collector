"""Карточки-слайды для новостей: фон по теме + заголовок + ключевые цифры.

Фоны — фото в общественном достоянии (Wikimedia Commons) в bot/bg; для крипты фон рисуется.
"""
import math
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent
FONT = ROOT / "Inter.ttf"
W, H = 1280, 720          # разметка карточки — в этих единицах
S = 2                     # рисуем в 2 раза крупнее (2560×1440): чётче текст и фото на телефонах
THEMES = ["fed", "wallst", "oil", "gold", "capitol", "whitehouse", "port", "dollar", "tech", "tanker", "crypto", "kremlin"]
SENT = {"pos": ((46, 190, 130), "ПОЗИТИВ"), "neg": ((235, 87, 105), "НЕГАТИВ"), "neu": ((150, 165, 185), "НЕЙТРАЛЬНО")}


def font(size, weight=700):
    f = ImageFont.truetype(str(FONT), size)
    try:
        f.set_variation_by_axes([32, weight])
    except Exception:
        pass
    return f


def _cover(img, w=W, h=H):
    r = max(w / img.width, h / img.height)
    img = img.resize((int(img.width * r) + 1, int(img.height * r) + 1), Image.LANCZOS)
    x, y = (img.width - w) // 2, (img.height - h) // 2
    return img.crop((x, y, x + w, y + h))


def _crypto_bg(seed=7):
    """Рисованный фон для крипты: тёмная сетка и свечи."""
    rnd = random.Random(seed)
    img = Image.new("RGB", (W, H), (10, 18, 32))
    d = ImageDraw.Draw(img, "RGBA")
    for x in range(0, W, 64):
        d.line([(x, 0), (x, H)], fill=(80, 120, 180, 22))
    for y in range(0, H, 64):
        d.line([(0, y), (W, y)], fill=(80, 120, 180, 22))
    price, x = 420, 560
    while x < W + 20:
        o = price
        c = price + rnd.uniform(-38, 42)
        hi, lo = max(o, c) + rnd.uniform(5, 25), min(o, c) - rnd.uniform(5, 25)
        col = (46, 190, 130, 170) if c < o else (235, 87, 105, 150)
        d.line([(x + 9, hi), (x + 9, lo)], fill=col, width=2)
        d.rectangle([x, min(o, c), x + 18, max(o, c)], fill=col)
        price, x = c, x + 30
    glow = Image.new("RGBA", (W, H))
    ImageDraw.Draw(glow).ellipse((W - 520, 60, W - 80, 500), fill=(247, 147, 26, 70))
    img.paste(glow.filter(ImageFilter.GaussianBlur(90)), (0, 0), glow.filter(ImageFilter.GaussianBlur(90)))
    return img


def background(theme, w=W, h=H):
    """Запасной фон темы, если фото под новость не нашлось: bg/<тема>.jpg, bg/<тема>2.jpg… — случайный из набора."""
    paths = sorted((ROOT / "bg").glob(f"{theme}*.jpg"))
    if theme == "crypto" or not paths:
        return _crypto_bg(random.randint(1, 999)).resize((w, h), Image.LANCZOS)
    return _cover(Image.open(random.choice(paths)).convert("RGB"), w, h)


def _wrap(d, text, f, width, max_lines):
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if d.textlength(t, font=f) <= width:
            cur = t
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip(",.;:") + "…"
    return lines


def _save(img, out_path):
    """JPEG почти без потерь: 4:4:4 (чёткий текст), качество 95; база хранит до 2 МБ — при необходимости чуть сжимаем."""
    for q in (95, 92, 88):
        img.save(out_path, "JPEG", quality=q, subsampling=0, optimize=True)
        if Path(out_path).stat().st_size < 1_700_000:
            break


def render(card, out_path, date_label=""):
    """card: {theme, tag, sent, title, metrics: [{value, label}], kicker?}"""
    from bot.edition import ru_numbers  # 19.10: цифры на карточках — по-русски (ИИ пишет «+3.1%»)
    card = {**card, "title": ru_numbers(card.get("title", "")),
            "metrics": [{**m, "value": ru_numbers(str(m.get("value", "")))} for m in card.get("metrics") or []]}
    W2, H2 = W * S, H * S
    k = lambda v: int(round(v * S))      # единицы разметки → пиксели
    fnt = lambda size, weight=700: font(int(size * S), weight)
    img = None
    if card.get("photo"):  # фото под конкретную новость (bot/photos.py)
        try:
            img = _cover(Image.open(card["photo"]).convert("RGB"), W2, H2)
        except Exception:
            img = None
    img = img or background(card.get("theme", "wallst"), W2, H2)
    # затемнение: слева плотнее (для текста), справа видно фото
    # затемнение всегда СЛЕВА (под текстом): плотное до ~45% ширины, к правому краю — прозрачное
    col = Image.new("L", (W2, 1))
    for x in range(W2):
        t = x / W2
        a = 238 if t < 0.5 else int(238 * max(0.0, 1 - (t - 0.5) / 0.4) ** 1.5)
        col.putpixel((x, 0), a)
    alpha = col.resize((W2, H2))
    bottom = Image.linear_gradient("L").resize((W2, H2)).point(lambda v: int(150 * max(0, (v / 255 - 0.6) / 0.4)))
    from PIL import ImageChops
    alpha = ImageChops.lighter(alpha, bottom)
    shade = Image.new("RGBA", (W2, H2), (12, 22, 38, 0)); shade.putalpha(alpha)
    img = Image.alpha_composite(img.convert("RGBA"), shade)
    ov = Image.new("RGBA", (W2, H2), (0, 0, 0, 0))  # полупрозрачные плашки — на отдельном слое
    od = ImageDraw.Draw(ov)
    d = ImageDraw.Draw(img, "RGBA")
    pad = 64
    # шапка: логотип + бренд + дата
    logo_p = ROOT.parent / "miniapp" / "img" / "logo192.png"
    if logo_p.exists():
        lg = Image.open(logo_p).convert("RGBA").resize((k(68), k(68)), Image.LANCZOS)
        img.paste(lg, (k(pad), k(44)), lg)
    d.text((k(pad + 86), k(58)), "ПУЛЬС РЫНКА", font=fnt(34, 800), fill=(255, 255, 255, 245))
    if date_label:  # дата и время — справа сверху, на плашке
        fdl = fnt(30, 700)
        wdl = d.textlength(date_label, font=fdl) / S + 40
        od.rounded_rectangle((k(W - pad - wdl), k(46), k(W - pad), k(106)), radius=k(30), fill=(12, 22, 38, 200))
        od.text((k(W - pad - wdl + 20), k(59)), date_label, font=fdl, fill=(255, 255, 255, 240))
    # плашки: тема + настроение
    x, y = pad, 150
    tag = (card.get("tag") or "").upper()
    col, sl = SENT.get(card.get("sent", "neu"), SENT["neu"])
    for text, fill, fg in ((tag, (255, 255, 255, 40), (255, 255, 255)), (sl, col + (235,), (255, 255, 255))):
        if not text:
            continue
        f = fnt(30, 800)
        w = d.textlength(text, font=f) / S + 44
        od.rounded_rectangle((k(x), k(y), k(x + w), k(y + 56)), radius=k(28), fill=fill)
        od.text((k(x + 22), k(y + 10)), text, font=f, fill=fg)
        x += w + 12
    # заголовок
    title = card.get("title", "")
    size = 64 if len(title) < 60 else 56 if len(title) < 90 else 50
    f = fnt(size, 800)
    lines = _wrap(d, title, f, W2 * 0.72, 3)
    y = 232
    for ln in lines:
        d.text((k(pad), k(y)), ln, font=f, fill=(255, 255, 255))
        y += int(size * 1.18)
    if card.get("kicker"):
        for ln in _wrap(d, card["kicker"], fnt(24, 500), W2 * 0.6, 2):
            d.text((k(pad), k(y + 8)), ln, font=fnt(24, 500), fill=(255, 255, 255, 200))
            y += 32
    # цифры
    ms = (card.get("metrics") or [])[:3]
    x, by = pad, H - 196
    for m in ms:
        fv, fl = fnt(58, 800), fnt(24, 600)
        w = max(d.textlength(m.get("value", ""), font=fv), d.textlength(m.get("label", ""), font=fl)) / S + 52
        if x + w > W - 40:
            break
        od.rounded_rectangle((k(x), k(by), k(x + w), k(by + 140)), radius=k(26), fill=(12, 22, 38, 200), outline=(255, 255, 255, 70), width=k(2))
        od.text((k(x + 26), k(by + 16)), m.get("value", ""), font=fv, fill=(255, 255, 255, 255))
        od.text((k(x + 26), k(by + 92)), m.get("label", ""), font=fl, fill=(255, 255, 255, 215))
        x += w + 16
    img = Image.alpha_composite(img, ov)
    _save(img.convert("RGB"), out_path)
    return out_path


def render_promo(title, subtitle, out_path, badge="", mascot="heart", chips=()):
    """Карточка для сообщений бота (приветствие, подписка…): фирменный фон + персонаж (стикер) справа.
    Рисуется в 2 раза крупнее (2560×1440), разметка — в единицах 1280×720."""
    W2, H2 = W * S, H * S
    k = lambda v: int(round(v * S))
    fnt = lambda size, weight=700: font(int(size * S), weight)
    # фон: плавный градиент (через маленькую картинку и растяжение — быстро и без полос)
    small = Image.new("RGB", (128, 72))
    px = small.load()
    for y in range(72):
        for x in range(128):
            t = min(1, x / 128 * 0.6 + y / 72 * 0.4)
            px[x, y] = tuple(int(a + (b - a) * t) for a, b in zip((16, 30, 50), (58, 110, 168)))
    img = small.resize((W2, H2), Image.BICUBIC).convert("RGBA")
    ov = Image.new("RGBA", (W2, H2), (0, 0, 0, 0))
    od = ImageDraw.Draw(ov)
    od.ellipse((k(W - 600), k(-200), k(W + 140), k(540)), fill=(255, 255, 255, 20))
    od.ellipse((k(W - 440), k(340), k(W + 60), k(840)), fill=(255, 255, 255, 12))
    img = Image.alpha_composite(img, ov)
    cx = W - 250
    if mascot:
        mp = ROOT.parent / "miniapp" / "img" / "stickers" / f"{mascot}.png"
        if mp.exists():
            m = Image.open(mp).convert("RGBA")
            m = m.crop(m.getbbox())
            bottom = m.getchannel("A").crop((0, m.height - 2, m.width, m.height)).point(lambda v: 255 if v > 128 else 0)
            flat = mascot in ("callme", "heart", "laptop") or sum(bottom.getdata()) / 255 / 2 > m.width * 0.35
            if flat:
                r = min(k(560) / m.height, k(470) / m.width)
                m = m.resize((int(m.width * r), int(m.height * r)), Image.LANCZOS)
                img.paste(m, (k(cx) - m.width // 2, H2 - m.height), m)
            else:
                r = min(k(470) / m.width, k(520) / m.height)
                m = m.resize((int(m.width * r), int(m.height * r)), Image.LANCZOS)
                img.paste(m, (k(cx) - m.width // 2, k(H // 2 + 10) - m.height // 2), m)
    ov = Image.new("RGBA", (W2, H2), (0, 0, 0, 0))
    od = ImageDraw.Draw(ov)
    d = ImageDraw.Draw(img, "RGBA")
    pad = 64
    lp = ROOT.parent / "miniapp" / "img" / "logo192.png"
    if lp.exists():
        lg = Image.open(lp).convert("RGBA").resize((k(68), k(68)), Image.LANCZOS)
        img.paste(lg, (k(pad), k(44)), lg)
    d.text((k(pad + 86), k(58)), "ПУЛЬС РЫНКА", font=fnt(34, 800), fill=(255, 255, 255, 245))
    y = 146
    if badge:
        f = fnt(30, 800)
        w = d.textlength(badge, font=f) / S + 44
        od.rounded_rectangle((k(pad), k(y), k(pad + w), k(y + 56)), radius=k(28), fill=(255, 255, 255, 50))
        od.text((k(pad + 22), k(y + 10)), badge, font=f, fill=(255, 255, 255, 255))
        y += 84
    f = fnt(64, 800)
    for ln in _wrap(d, title, f, k(650), 3):
        d.text((k(pad), k(y)), ln, font=f, fill=(255, 255, 255))
        y += 74
    fs = fnt(34, 500)
    for ln in _wrap(d, subtitle, fs, k(640), 3):
        d.text((k(pad), k(y + 10)), ln, font=fs, fill=(255, 255, 255, 225))
        y += 44
    x, cyb = pad, H - 120
    for c in chips:
        f = fnt(32, 800)
        w = d.textlength(c, font=f) / S + 48
        od.rounded_rectangle((k(x), k(cyb), k(x + w), k(cyb + 68)), radius=k(34), fill=(255, 255, 255, 235))
        od.text((k(x + 24), k(cyb + 14)), c, font=f, fill=(22, 40, 66, 255))
        x += w + 14
    img = Image.alpha_composite(img, ov)
    _save(img.convert("RGB"), out_path)
    return out_path
