"""Render the app icon in three Windows styles into assets/icons/*.ico (+ PNG previews).

Motif: stacked media tiles under a tilted clapper bar, a ribbon fold across the front tile (from the
Windows 11 "Bloom" wallpaper) and a white play glyph. Palette: Bloom blues.
win11 — Fluent: soft gradients, rounded tiles, gentle depth.
win10 — flat Metro: same shapes, solid fills.
win7  — Aero: glossy tiles with a glass highlight.
Usage: python tools/make_icons.py
"""
import math
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

OUT = Path(__file__).resolve().parent.parent / "assets" / "icons"
S = 1024
SIZES = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256]

# Windows 11 Bloom blues, light to deep
SKY, LIGHT, BRIGHT, MID, DEEP, NIGHT = (150, 205, 255), (84, 168, 255), (36, 132, 250), (16, 98, 232), (10, 70, 196), (6, 42, 140)


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3)) + (255,)


def grad(top, bottom, angle=90):
    """Linear gradient over the whole canvas; angle 90 = top->bottom, 45 = top-left -> bottom-right."""
    g = Image.new("RGBA", (S, S))
    a = math.radians(angle)
    dx, dy = math.cos(a), math.sin(a)
    px = g.load()
    span = abs(dx) * S + abs(dy) * S
    for y in range(0, S, 2):
        for x in range(0, S, 2):
            t = ((x * dx + y * dy) - min(0, dx * S) - min(0, dy * S)) / span
            c = lerp(top, bottom, max(0.0, min(1.0, t)))
            px[x, y] = c
            if x + 1 < S:
                px[x + 1, y] = c
            if y + 1 < S:
                px[x, y + 1] = c
                if x + 1 < S:
                    px[x + 1, y + 1] = c
    return g


def rrect(box, r):
    m = Image.new("L", (S, S), 0)
    ImageDraw.Draw(m).rounded_rectangle(box, r, fill=255)
    return m


def paint(base, fill, mask):
    layer = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    layer.paste(fill if isinstance(fill, Image.Image) else Image.new("RGBA", (S, S), fill), (0, 0), mask)
    return Image.alpha_composite(base, layer)


def shadow(base, mask, dy=18, blur=26, alpha=110):
    sh = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    sh.paste((6, 30, 90, alpha), (0, dy), mask)
    return Image.alpha_composite(base, sh.filter(ImageFilter.GaussianBlur(blur)))


def play(cx, cy, r, soft=14):
    m = Image.new("L", (S, S), 0)
    ImageDraw.Draw(m).polygon([(cx - r * 0.45, cy - r * 0.62), (cx - r * 0.45, cy + r * 0.62), (cx + r * 0.62, cy)], fill=255)
    return m.filter(ImageFilter.GaussianBlur(soft)).point(lambda v: 255 if v > 128 else 0)


def ribbon(front):
    """A Bloom-like fold: a curved light band sweeping across the front tile, clipped to it."""
    m = Image.new("L", (S, S), 0)
    d = ImageDraw.Draw(m)
    d.ellipse((-260, 520, 980, 1500), fill=255)
    d.ellipse((-200, 610, 1100, 1650), fill=0)
    return ImageChops.multiply(m.filter(ImageFilter.GaussianBlur(10)), front)


def clapper(tilt_deg, colors, stripe_color):
    """Tilted clapper bar with diagonal stripes, hinged at the top-left of the front tile."""
    bar = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    bm = rrect((150, 210, 874, 330), 46)
    bar = paint(bar, colors, bm)
    st = Image.new("L", (S, S), 0)
    d = ImageDraw.Draw(st)
    for i in range(5):
        x = 230 + i * 140
        d.polygon([(x, 210), (x + 70, 210), (x + 10, 330), (x - 60, 330)], fill=255)
    bar = paint(bar, stripe_color, ImageChops.multiply(st, bm))
    return bar.rotate(tilt_deg, center=(160, 330), resample=Image.BICUBIC)


def stripes(box, step=124, width=62):
    """Diagonal clapper stripes inside `box`."""
    x0, y0, x1, y1 = box
    m = Image.new("L", (S, S), 0)
    d = ImageDraw.Draw(m)
    h = y1 - y0
    x = x0 - h
    while x < x1 + h:
        d.polygon([(x, y1), (x + width, y1), (x + width + h * 0.7, y0), (x + h * 0.7, y0)], fill=255)
        x += step
    return m


def build(style):
    """Front tile = body with a striped clapper band along its top edge; the clapper arm, same width and
    corner radius, is hinged at the tile's top-left corner and opened a little. Two layers stack behind
    to the lower right, Bloom-blue from light to deep."""
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    glossy, flat = style == "win7", style == "win10"
    r = 24 if flat else 118
    L, R, TOP, BOT = 120, 836, 400, 900          # front tile
    band = 118                                   # clapper band height inside the tile
    layer2 = rrect((L + 64, TOP + 56, R + 64, BOT + 56), r)
    layer1 = rrect((L + 32, TOP + 28, R + 32, BOT + 28), r)
    front = rrect((L, TOP, R, BOT), r)
    # layers behind
    img = shadow(img, layer2, alpha=0 if flat else 100)
    img = paint(img, SKY + (255,) if flat else grad(SKY, LIGHT), layer2)
    img = paint(img, LIGHT + (255,) if flat else grad(LIGHT, BRIGHT), layer1)
    img = shadow(img, front, dy=10, blur=18, alpha=0 if flat else 90)
    img = paint(img, MID + (255,) if flat else grad(BRIGHT, NIGHT, 60), front)
    # clapper band: the top strip of the same tile, a shade deeper, with light stripes
    band_m = ImageChops.multiply(front, rrect((L - 10, TOP - 10, R + 10, TOP + band), 0))
    img = paint(img, DEEP + (255,) if flat else grad(MID, NIGHT, 0), band_m)
    img = paint(img, (232, 243, 255, 255), ImageChops.multiply(band_m, stripes((L, TOP, R, TOP + band))))
    seam = rrect((L, TOP + band - 6, R, TOP + band + 6), 0)
    img = paint(img, NIGHT + (255,), ImageChops.multiply(seam, front))
    if not flat:
        img = paint(img, (190, 225, 255, 70 if glossy else 85), ribbon(front))
    # clapper arm: same width and corner radius as the tile, hinged at its top-left corner
    arm = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    arm_h = 112
    arm_m = rrect((L, TOP - arm_h - 6, R, TOP - 6), min(r, 52))
    arm = paint(arm, DEEP + (255,) if flat else grad(BRIGHT, DEEP, 0), arm_m)
    arm = paint(arm, (232, 243, 255, 255), ImageChops.multiply(arm_m, stripes((L, TOP - arm_h - 6, R, TOP - 6))))
    hinge = (L + 40, TOP - 6)
    arm = arm.rotate(9, center=hinge, resample=Image.BICUBIC)
    if not flat:
        sh = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        sh.paste((6, 30, 90, 90), (0, 8), arm.split()[3])
        img = Image.alpha_composite(img, sh.filter(ImageFilter.GaussianBlur(10)))
    img = Image.alpha_composite(img, arm)
    hm = Image.new("L", (S, S), 0)
    ImageDraw.Draw(hm).ellipse((hinge[0] - 30, hinge[1] - 30, hinge[0] + 30, hinge[1] + 30), fill=255)
    img = paint(img, NIGHT + (255,), hm)
    hm2 = Image.new("L", (S, S), 0)
    ImageDraw.Draw(hm2).ellipse((hinge[0] - 13, hinge[1] - 13, hinge[0] + 13, hinge[1] + 13), fill=255)
    img = paint(img, (232, 243, 255, 255), hm2)
    # play glyph in the body below the band
    g = play((L + R) / 2 + 20, (TOP + band + BOT) / 2 + 6, 190, soft=4 if flat else 16)
    if not flat:
        gs = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        gs.paste((4, 30, 100, 120), (0, 10), g)
        img = Image.alpha_composite(img, gs.filter(ImageFilter.GaussianBlur(12)))
    img = paint(img, (255, 255, 255, 255) if flat else grad((255, 255, 255), (214, 232, 255)), g)
    if glossy:
        sheen = Image.new("L", (S, S), 0)
        ImageDraw.Draw(sheen).ellipse((-40, 160, 1000, 690), fill=110)
        img = paint(img, (255, 255, 255, 110), ImageChops.multiply(sheen, front).filter(ImageFilter.GaussianBlur(6)))
        rim = Image.new("L", (S, S), 0)
        ImageDraw.Draw(rim).rounded_rectangle((L, TOP, R, BOT), r, outline=200, width=8)
        img = paint(img, (200, 230, 255, 160), rim)
    return img


def save(img, name):
    OUT.mkdir(parents=True, exist_ok=True)
    frames = [img.resize((s, s), Image.LANCZOS) for s in SIZES]
    frames[-1].save(OUT / f"{name}.ico", format="ICO", sizes=[(s, s) for s in SIZES], append_images=frames[:-1])
    img.resize((256, 256), Image.LANCZOS).save(OUT / f"{name}.png")


if __name__ == "__main__":
    # the flat design was chosen for every Windows version; the per-OS files stay so the installer's
    # OS detection keeps working if the styles are split again later
    flat = build("win10")
    for name in ("win11", "win10", "win7"):
        save(flat, name)
        print("ok", name)
