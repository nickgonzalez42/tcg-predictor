#!/usr/bin/env python3
"""
Compose a thumbnail for a weekly report (2026-10-30, user request: "a mix of
cards, graphics, and stats based on the headline").

One JPEG per report, 1200x630, written beside the static article so it ships
with it and is served from /reports/{slug}.jpg. JPEG, not PNG: the card art
is photographic and the index loads a dozen of these at once, which as PNGs
came to roughly 350 KB each. It does triple duty: the hero
on the article, the picture on the reports index card, and the share image a
link preview shows.

Everything in it comes from the report itself: its headline, its kind, the
cards it singled out that week (art from the image CDN) and the numbers its
own lede quotes. Nothing is invented here.

Fonts are the site's own stack resolved to files: the display face falls back
to Impact in CSS, and the mono face to Menlo, so the picture matches the page
without vendoring a webfont.
"""

import io
import os

import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont

CDN = "https://d18pzythfo0bgy.cloudfront.net"
W, H = 1200, 630

BG = (16, 22, 35)
TEXT = (232, 236, 244)
MUTED = (139, 150, 173)
BRAND = (255, 203, 5)
UP = (63, 217, 138)
DOWN = (255, 122, 122)
KIND_COLOR = {"market": (127, 176, 234), "model": (198, 120, 221)}

DISPLAY = "/System/Library/Fonts/Supplemental/Impact.ttf"
MONO = "/System/Library/Fonts/Menlo.ttc"
BODY = "/System/Library/Fonts/Helvetica.ttc"


def _font(path, size, index=0):
    try:
        return ImageFont.truetype(path, size, index=index)
    except OSError:
        return ImageFont.load_default(size)


def card_art(game, pid, timeout=20):
    """One card's art from the image CDN, or None when it has none.

    requests, not urllib: the bundled Python has no system trust store here,
    so urllib cannot verify the CDN's certificate (2026-10-30).
    """
    try:
        r = requests.get(f"{CDN}/{game}/{pid}.jpg", timeout=timeout)
        if r.status_code != 200:
            return None
        return Image.open(io.BytesIO(r.content)).convert("RGBA")
    except Exception:
        return None


def _wrap(draw, text, font, width, max_lines):
    words, lines, line = text.split(), [], ""
    for w in words:
        trial = f"{line} {w}".strip()
        if draw.textlength(trial, font=font) <= width or not line:
            line = trial
        else:
            lines.append(line)
            line = w
            if len(lines) == max_lines:
                break
    if line and len(lines) < max_lines:
        lines.append(line)
    if len(lines) == max_lines and words:
        # Ellipsis rather than a sentence that stops mid-word.
        while lines and draw.textlength(lines[-1] + "…", font=font) > width:
            lines[-1] = lines[-1].rsplit(" ", 1)[0]
        joined = " ".join(lines)
        if len(joined) < len(text):
            lines[-1] += "…"
    return lines


def _dots(img):
    """The site's dot field, faded out to the right so art reads clearly."""
    dots = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(dots)
    for y in range(0, H, 7):
        for x in range(0, W, 7):
            d.point((x, y), fill=(255, 255, 255, 16))
    mask = Image.linear_gradient("L").rotate(270, expand=True).resize((W, H))
    dots.putalpha(Image.eval(mask, lambda v: int(v * 0.75)))
    img.alpha_composite(dots)


def _chip(draw, xy, label, color, font):
    x, y = xy
    tw = draw.textlength(label, font=font)
    draw.rounded_rectangle([x, y, x + tw + 28, y + 36], radius=18, outline=color, width=2)
    draw.text((x + 14, y + 9), label, font=font, fill=color)
    return y + 36


def _spark(draw, xy, series, width=600, height=74):
    """A diverging bar per game: how far it moved that week, up above the
    line and down below it. Real numbers from the report, not decoration."""
    x0, y0 = xy
    if not series:
        return
    peak = max(abs(item[1]) for item in series) or 1.0
    mid = y0 + height / 2
    slot = width / len(series)
    bar = min(26, slot * 0.6)
    draw.line([x0, mid, x0 + width, mid], fill=(46, 58, 82), width=1)
    f = _font(MONO, 12, index=0)
    for i, item in enumerate(series):
        label, value = item[0], item[1]
        cx = x0 + slot * (i + 0.5)
        h = (abs(value) / peak) * (height / 2 - 10)
        top, bottom = (mid - h, mid) if value >= 0 else (mid, mid + h)
        draw.rounded_rectangle([cx - bar / 2, top, cx + bar / 2, bottom], radius=3,
                               fill=UP if value >= 0 else DOWN)
        name = label[:3].upper()
        draw.text((cx - draw.textlength(name, font=f) / 2, y0 + height + 6), name,
                  font=f, fill=MUTED)


def render(out_path, kind, headline, published, stats=(), cards=(), spark=()):
    """Write one report's thumbnail.

    kind      'market' | 'model'
    stats     (value, label, tone) triples, tone 'up' | 'down' | None
    cards     (game, product_id) candidates; the first three with art are used
    spark     (label, value) pairs drawn as a diverging bar per game
    """
    img = Image.new("RGBA", (W, H), BG + (255,))
    _dots(img)
    draw = ImageDraw.Draw(img)

    f_head = _font(DISPLAY, 66)
    f_mono = _font(MONO, 19, index=1)
    f_small = _font(MONO, 16, index=0)
    f_stat = _font(DISPLAY, 46)
    f_label = _font(MONO, 15, index=0)

    # Card art first: it sits behind the text column's right edge.
    art, used = [], []
    for card in cards:
        game, pid = card[0], card[1]
        a = card_art(game, pid)
        if a is not None:
            art.append(a)
            used.append(card)
        if len(art) == 3:
            break
    # A fan of up to three, the first on top. Positions are centres so the
    # rotation's wider bounding box cannot push a card off the canvas.
    spots = [((842, 296), -9), ((948, 332), 0), ((1046, 292), 9)][:len(art)]
    for n in range(len(art) - 1, -1, -1):
        (cx, cy), angle = spots[n]
        a = art[n]
        card = a.resize((228, int(228 * a.height / a.width)), Image.LANCZOS)
        card = card.rotate(angle, expand=True, resample=Image.BICUBIC)
        # The shadow is the card's own silhouette, blurred — a rectangle
        # behind a rotated card reads as a mistake.
        shadow = Image.new("RGBA", card.size, (0, 0, 0, 0))
        shadow.putalpha(card.getchannel("A").point(lambda v: int(v * 0.55)))
        shadow = shadow.filter(ImageFilter.GaussianBlur(9))
        pos = (cx - card.size[0] // 2, cy - card.size[1] // 2)
        img.alpha_composite(shadow, (pos[0] + 6, pos[1] + 14))
        img.alpha_composite(card, pos)

    # Brand rule across the top, as on the site's nav.
    draw.rectangle([0, 0, W, 6], fill=BRAND)

    y = 56
    label = "MARKET REPORT" if kind == "market" else "MODEL REPORT"
    y = _chip(draw, (64, y), label, KIND_COLOR.get(kind, MUTED), f_mono) + 26

    # 640 keeps the longest line clear of the card fan, which starts at ~728.
    # A long headline steps the type down rather than losing its ending.
    head_text = headline.upper()
    for size, step in ((66, 70), (58, 62), (50, 54), (44, 48)):
        f_head = _font(DISPLAY, size)
        lines = _wrap(draw, head_text, f_head, 640, 3)
        if not lines[-1].endswith("…") or size == 44:
            break
    for line in lines:
        draw.text((64, y), line, font=f_head, fill=TEXT)
        y += step
    y += 6
    draw.text((64, y), published.upper(), font=f_small, fill=MUTED)

    if spark:
        _spark(draw, (64, 392), list(spark)[:12])

    # Stats along the bottom, each a number over its label. A figure the
    # headline already quotes is dropped rather than printed twice.
    # Only long figures count as duplicates: a bare "8" appears inside
    # almost any headline that quotes a number.
    flat = headline.replace(",", "")
    def already(v):
        v = str(v).replace(",", "").strip("±%+")
        return len(v) >= 3 and v in flat
    fresh = [s for s in stats if not already(s[0])]
    # Headlines about breadth quote the same counts the stats would, which
    # can leave a single lonely figure. Below two, the dropped ones come
    # back: a little repetition beats an empty row.
    shown = fresh if len(fresh) >= 2 else fresh + [s for s in stats if s not in fresh]
    x = 64
    for value, slabel, tone in shown[:3]:
        colour = UP if tone == "up" else DOWN if tone == "down" else BRAND
        draw.text((x, H - 136), str(value), font=f_stat, fill=colour)
        draw.text((x, H - 78), slabel.upper(), font=f_label, fill=MUTED)
        x += max(draw.textlength(str(value), font=f_stat),
                 draw.textlength(slabel.upper(), font=f_label)) + 56

    draw.text((W - 230, H - 48), "CARDSTOCK.GUIDE", font=f_small, fill=MUTED)

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    img.convert("RGB").save(out_path, "JPEG", quality=72, optimize=True,
                            progressive=True)
    return used    # the cards that actually had art, for the SVG twin


# ---- the interactive twin ---------------------------------------------------
# The article shows an SVG rather than the JPEG (2026-10-30, user request):
# the same picture, but every card and every game bar is a link that names
# itself on hover. The JPEG stays for link previews, which cannot render SVG.
#
# It is inline markup, not a file: an <img> pointing at an SVG refuses to
# load the card art (another origin) and takes no clicks. The markup rides in
# the report body, so the static page and the app both get it, the app
# through the report sanitizer's SVG allowlist.

SVG_FONT = "'Big Shoulders Display', Impact, 'Arial Narrow', sans-serif"
SVG_MONO = "'IBM Plex Mono', ui-monospace, Menlo, monospace"


def _esc(t):
    return (str(t).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def render_svg(kind, headline, published, stats=(), cards=(), spark=()):
    """Inline SVG twin of the thumbnail. `cards` are (game, pid[, name])."""
    from PIL import Image as _I, ImageDraw
    measure = ImageDraw.Draw(_I.new("RGB", (1, 1)))
    kind_colour = {"market": "#7fb0ea", "model": "#c678dd"}.get(kind, "#8b96ad")
    label = "MARKET REPORT" if kind == "market" else "MODEL REPORT"
    out = [f'<svg class="report-hero-svg" viewBox="0 0 {W} {H}" role="img" '
           f'xmlns="http://www.w3.org/2000/svg">',
           f'<title>{_esc(headline)}</title>',
           f'<rect x="0" y="0" width="{W}" height="{H}" fill="#101623"/>',
           f'<rect x="0" y="0" width="{W}" height="6" fill="#ffcb05"/>']

    spots = [(842, 296, -9), (948, 332, 0), (1046, 292, 9)]
    for n in range(min(len(cards), 3) - 1, -1, -1):
        card = cards[n]
        game, pid = card[0], int(card[1])
        name = card[2] if len(card) > 2 and card[2] else "Open this card"
        cx, cy, angle = spots[n]
        cw, ch = 228, 319
        out.append(
            f'<a class="report-hero-card" href="/catalog/{_esc(game)}/{pid}">'
            f'<title>{_esc(name)}</title>'
            f'<image href="{CDN}/{_esc(game)}/{pid}.jpg" x="{cx - cw // 2}" '
            f'y="{cy - ch // 2}" width="{cw}" height="{ch}" '
            f'preserveAspectRatio="xMidYMid slice" '
            f'transform="rotate({angle} {cx} {cy})"/></a>')

    chip_w = int(measure.textlength(label, font=_font(MONO, 19, index=1))) + 28
    out.append(f'<rect x="64" y="56" width="{chip_w}" height="36" rx="18" fill="none" '
               f'stroke="{kind_colour}" stroke-width="2"/>')
    out.append(f'<text x="78" y="81" font-family="{SVG_MONO}" font-size="19" '
               f'fill="{kind_colour}">{_esc(label)}</text>')

    y, size, step, lines = 166, 66, 70, [headline.upper()]
    for size, step in ((66, 70), (58, 62), (50, 54), (44, 48)):
        lines = _wrap(measure, headline.upper(), _font(DISPLAY, size), 640, 3)
        if not lines[-1].endswith("…") or size == 44:
            break
    for line in lines:
        out.append(f'<text x="64" y="{y}" font-family="{SVG_FONT}" font-weight="900" '
                   f'font-size="{size}" fill="#e8ecf4">{_esc(line)}</text>')
        y += step
    out.append(f'<text x="64" y="{y - step + 30}" font-family="{SVG_MONO}" font-size="16" '
               f'fill="#8b96ad">{_esc(published.upper())}</text>')

    series = list(spark)[:12]
    if series:
        peak = max(abs(v) for _l, v, *_rest in series) or 1.0
        mid, slot = 429, 600 / len(series)
        bw = min(26, slot * 0.6)
        out.append(f'<line x1="64" y1="{mid}" x2="664" y2="{mid}" stroke="#2e3a52"/>')
        for i, item in enumerate(series):
            name, value = item[0], item[1]
            key = item[2] if len(item) > 2 else ""
            cx = 64 + slot * (i + 0.5)
            h = max((abs(value) / peak) * 27, 2)
            top = mid - h if value >= 0 else mid
            colour = "#3fd98a" if value >= 0 else "#ff7a7a"
            body = (f'<title>{_esc(name)} {value:+.1f}% this week</title>'
                    f'<rect class="report-hero-bar" x="{cx - bw / 2:.0f}" y="{top:.0f}" '
                    f'width="{bw:.0f}" height="{h:.0f}" rx="3" fill="{colour}"/>'
                    f'<text x="{cx:.0f}" y="{mid + 52:.0f}" font-family="{SVG_MONO}" '
                    f'font-size="12" fill="#8b96ad" text-anchor="middle">'
                    f'{_esc(name[:3].upper())}</text>')
            out.append(f'<a href="/catalog?game={_esc(key)}">{body}</a>' if key else body)

    flat = headline.replace(",", "")

    def already(v):
        v = str(v).replace(",", "").strip("±%+")
        return len(v) >= 3 and v in flat

    fresh = [st for st in stats if not already(st[0])]
    shown = fresh if len(fresh) >= 2 else fresh + [st for st in stats if st not in fresh]
    x = 64
    for value, slabel, tone in shown[:3]:
        colour = "#3fd98a" if tone == "up" else "#ff7a7a" if tone == "down" else "#ffcb05"
        out.append(f'<text x="{x:.0f}" y="{H - 96}" font-family="{SVG_FONT}" '
                   f'font-weight="900" font-size="46" fill="{colour}">{_esc(value)}</text>')
        out.append(f'<text x="{x:.0f}" y="{H - 66}" font-family="{SVG_MONO}" font-size="15" '
                   f'fill="#8b96ad">{_esc(str(slabel).upper())}</text>')
        x += max(measure.textlength(str(value), font=_font(DISPLAY, 46)),
                 measure.textlength(str(slabel).upper(), font=_font(MONO, 15))) + 56
    out.append("</svg>")
    return "".join(out)
