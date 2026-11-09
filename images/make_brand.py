"""Sibling brand for ha-vicohome-bridge, in the un-subscribird "Electric Freedom" palette.

Usage: python images/make_brand.py vicohome_mqtt images/banner.png <un-subscribird logo png>
Writes icon.png (128x128) and logo.png (250x100) into addon_dir, and a README banner.
"""
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont

S = 8  # supersampling
ABYSS = (0x14, 0x21, 0x32, 255)
ORANGE = (0xF3, 0x66, 0x33, 255)
SKY = (0x3E, 0xA0, 0xD8, 255)
CYAN = (0x75, 0xD6, 0xF9, 255)
WING = (0x2B, 0x7F, 0xB4, 255)
MIST = (0xA9, 0xB8, 0xC8, 255)
# un-subscribird's wordmark, measured from its logo: "un-" / "subscri" / "bird"
UNSUB_RED = (0xD7, 0x4B, 0x29, 255)
UNSUB_AMBER = (0xEC, 0x87, 0x24, 255)
UNSUB_TEAL = (0x2D, 0x80, 0xA5, 255)


FONTS = Path(__file__).resolve().parent / "fonts"  # see BRANDING.md for where to get them


def font(choices, px):
    """First font that loads: (file, variable-font style) pairs, brand font first."""
    for name, style in choices:
        try:
            f = ImageFont.truetype(str(name), px)
        except OSError:
            continue
        if style:
            f.set_variation_by_name(style)
        if name != choices[0][0]:
            print(f"warning: {choices[0][0]} not found, using {name}", file=sys.stderr)
        return f
    return ImageFont.load_default(px)


HEAVY = [(FONTS / "Montserrat.ttf", "ExtraBold"), ("seguibl.ttf", None), ("DejaVuSans-Bold.ttf", None)]
BODY = [(FONTS / "Inter.ttf", "Medium"), ("segoeuib.ttf", None), ("DejaVuSans.ttf", None)]


def mark(size, tile=True):
    """Camera and bird joined by a bridge: the add-on is the link between the two."""
    n = size * S
    out = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(out)
    u = n / 128

    def B(x0, y0, x1, y1):
        return (x0 * u, y0 * u, x1 * u, y1 * u)

    def P(*pts):
        return [(x * u, y * u) for x, y in pts]

    if tile:
        d.rounded_rectangle(B(0, 0, 127.9, 127.9), radius=28 * u, fill=ABYSS)

    # bridge: cyan arch with hangers, orange deck
    d.arc(B(22, 62, 106, 138), 196, 344, fill=CYAN, width=int(4 * u))
    for x, top in ((44, 74), (64, 66), (84, 74)):
        d.line(P((x, top), (x, 98)), fill=CYAN, width=int(2.5 * u))
    d.rounded_rectangle(B(10, 97, 118, 105), radius=4 * u, fill=ORANGE)

    # camera on the left bank
    d.rounded_rectangle(B(12, 62, 46, 96), radius=7 * u, fill=SKY)
    d.ellipse(B(18, 68, 40, 90), fill=ABYSS)
    d.ellipse(B(23, 73, 35, 85), fill=CYAN)
    d.ellipse(B(25.5, 75.5, 30, 80), fill=(255, 255, 255, 255))
    # signal from the camera
    cx, cy = 29, 54
    for r in (8, 15):
        d.arc(B(cx - r, cy - r, cx + r, cy + r), 225, 315, fill=CYAN, width=int(3.5 * u))
    d.ellipse(B(cx - 3, cy - 3, cx + 3, cy + 3), fill=CYAN)

    # bird on the right bank
    d.polygon(P((84, 84), (70, 98), (80, 97), (92, 88)), fill=WING)  # tail
    d.ellipse(B(76, 56, 112, 97), fill=SKY)  # body
    d.ellipse(B(88, 68, 110, 96), fill=ORANGE)  # belly
    d.chord(B(74, 62, 100, 92), 100, 280, fill=WING)  # wing
    d.ellipse(B(86, 36, 114, 64), fill=SKY)  # head
    d.polygon(P((111, 46), (122, 50), (111, 55)), fill=ORANGE)  # beak
    d.ellipse(B(100, 44, 107, 51), fill=(255, 255, 255, 255))
    d.ellipse(B(102, 45.5, 106, 49.5), fill=ABYSS)
    d.polygon(P((92, 38), (96, 28), (100, 37)), fill=SKY)  # crest
    return out.resize((size, size), Image.LANCZOS)


def wifi_text(draw, x, y, text, f, fill):
    """Draw text with a wifi symbol in place of the first i's dot, as un-subscribird does on "bird"."""
    head, _, tail = text.partition("i")
    draw.text((x, y), head + "ı" + tail, font=f, fill=fill)  # dotless i
    # Find the dot: the pixels "i" has and "ı" lacks.
    w, h = int(f.getlength("i")) * 3, int(f.size * 2)
    with_dot, dotless = Image.new("L", (w, h)), Image.new("L", (w, h))
    ImageDraw.Draw(with_dot).text((w // 3, 0), "i", font=f, fill=255)
    ImageDraw.Draw(dotless).text((w // 3, 0), "ı", font=f, fill=255)
    x0, y0, x1, y1 = ImageChops.subtract(with_dot, dotless).getbbox()
    size = y1 - y0
    cx = x + draw.textlength(head, font=f) + (x0 + x1) / 2 - w // 3
    cy = y + (y0 + y1) / 2 + size * 0.15
    r = size * 0.42
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=fill)
    for arc_r in (size * 1.15, size * 1.9):
        draw.arc((cx - arc_r, cy - arc_r, cx + arc_r, cy + arc_r), 228, 312, fill=fill,
                 width=max(1, round(size * 0.38)))
    return draw.textlength(text, font=f)


def wordmark(draw, x, y, px, s=1):
    """'vicohome' in Tech Sky Blue, '-bridge' in Liberation Orange (mirrors un-subscri|bird)."""
    f = font(HEAVY, px * s)
    draw.text((x * s, y * s), "vicohome", font=f, fill=SKY)
    w = draw.textlength("vicohome", font=f)
    return w + wifi_text(draw, x * s + w, y * s, "-bridge", f, ORANGE)


def logo(w=250, h=100):
    out = Image.new("RGBA", (w * S, h * S), (0, 0, 0, 0))
    d = ImageDraw.Draw(out)
    d.rounded_rectangle((0, 0, w * S - 1, h * S - 1), radius=18 * S, fill=ABYSS)
    m = mark(76, tile=False).resize((76 * S, 76 * S), Image.LANCZOS)
    out.paste(m, (6 * S, 10 * S), m)
    f = font(HEAVY, 24 * S)
    d.text((86 * S, 6 * S), "vicohome", font=f, fill=SKY)
    wifi_text(d, 86 * S, 41 * S, "-bridge", f, ORANGE)
    d.text((88 * S, 77 * S), "Camera in. Wings out.", font=font(BODY, 11 * S), fill=MIST)
    return out.resize((w, h), Image.LANCZOS)


def banner(mascot_path, w=1280, h=320):
    out = Image.new("RGBA", (w, h), ABYSS)
    d = ImageDraw.Draw(out)
    m = mark(200, tile=False)
    out.paste(m, (50, 60), m)
    # the un-subscribird mascot on the far bank
    art = Image.open(mascot_path).convert("RGBA")
    # The wing tip shares columns with the "u" of the wordmark; blank the letter, keep the wing.
    ImageDraw.Draw(art).rectangle((1165, 640, 1300, 900), fill=(0, 0, 0, 0))
    bird = art.crop((222, 377, 1300, 1119))
    bird.thumbnail((340, 280), Image.LANCZOS)
    bx = w - bird.width - 30
    out.paste(bird, (bx, (h - bird.height) // 2), bird)
    tx = 280
    wordmark(d, tx, 78, 54)
    d.text((tx + 4, 160), "Camera in. Wings out.", font=font(BODY, 28), fill=CYAN)
    # un-subscribird keeps its own wordmark colours
    f, x = font(BODY, 16), tx + 4
    for text, fill in (("The Home Assistant link between your Vicohome camera and ", MIST),
                       ("un-", UNSUB_RED), ("subscri", UNSUB_AMBER), ("bird", UNSUB_TEAL), (".", MIST)):
        d.text((x, 210), text, font=f, fill=fill)
        x += d.textlength(text, font=f)
    return out


if __name__ == "__main__":
    addon, banner_path, mascot = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
    mark(128).save(addon / "icon.png", optimize=True)
    logo().save(addon / "logo.png", optimize=True)
    banner_path.parent.mkdir(parents=True, exist_ok=True)
    banner(mascot).convert("RGB").save(banner_path, optimize=True)
