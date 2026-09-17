"""Draws Navi's app icons (original artwork: a glowing fairy on a night sky).

Run from anywhere: python web/make_icons.py   (needs Pillow)
"""
import math
import os
import random

from PIL import Image, ImageChops, ImageDraw, ImageFilter

S = 1024  # drawn at this size, then scaled down
HERE = os.path.dirname(os.path.abspath(__file__))


def radial(size, inner, outer, power=1.0):
    """RGBA radial gradient from inner (centre) to outer (edge) colours."""
    g = Image.radial_gradient("L").resize((size, size))
    if power != 1.0:
        g = g.point(lambda v: int(255 * (v / 255) ** power))
    a = Image.new("RGBA", (size, size), inner)
    b = Image.new("RGBA", (size, size), outer)
    return Image.composite(b, a, g)


def glow(size, center, radius, color, blur):
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    x, y = center
    d.ellipse((x - radius, y - radius, x + radius, y + radius), fill=color)
    return layer.filter(ImageFilter.GaussianBlur(blur))


def wing(base, angle_deg, length, width, curl):
    """Leaf-shaped wing outline, plus its vein lines."""
    a = math.radians(angle_deg)
    dx, dy = math.cos(a), math.sin(a)
    px, py = -dy, dx
    top, bottom, spine = [], [], []
    n = 60
    for i in range(n + 1):
        t = i / n
        w = width * math.sin(math.pi * t) ** 0.7 * (1 - 0.35 * t)
        bend = curl * t * t
        cx = base[0] + dx * length * t + px * bend
        cy = base[1] + dy * length * t + py * bend
        top.append((cx + px * w * 0.55, cy + py * w * 0.55))
        bottom.append((cx - px * w * 0.45, cy - py * w * 0.45))
        spine.append((cx, cy))
    outline = top + bottom[::-1]
    veins = []
    for k in range(1, 7):
        t = k / 7.5
        i = int(t * n)
        sx, sy = spine[i]
        for side, frac in ((1, 0.5), (-1, 0.4)):
            w = width * math.sin(math.pi * t) ** 0.7 * (1 - 0.35 * t) * frac
            ex = sx + dx * length * 0.08 + px * w * side
            ey = sy + dy * length * 0.08 + py * w * side
            veins.append(((sx, sy), (ex, ey)))
    return outline, spine, veins


def fairy(size, orb_center, orb_r):
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    wings = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(wings)
    cx, cy = orb_center
    specs = [  # angle, length, width, curl (all relative to orb radius)
        (-126, 3.3, 1.05, -0.5),
        (-28, 3.5, 1.0, -0.6),
        (152, 1.7, 0.62, 0.3),
        (62, 1.6, 0.58, -0.3),
    ]
    for ang, ln, wd, curl in specs:
        a = math.radians(ang)
        base = (cx + math.cos(a) * orb_r * 0.55, cy + math.sin(a) * orb_r * 0.55)
        outline, spine, veins = wing(base, ang, ln * orb_r, wd * orb_r, curl * orb_r)
        d.polygon(outline, fill=(225, 238, 255, 150))
        d.line(outline + [outline[0]], fill=(245, 250, 255, 210), width=max(2, size // 300))
        d.line(spine, fill=(255, 255, 255, 200), width=max(2, size // 260))
        for p, q in veins:
            d.line([p, q], fill=(255, 255, 255, 120), width=max(1, size // 420))
    wings = wings.filter(ImageFilter.GaussianBlur(size / 1400))
    # Light from the orb tints the wings near it.
    tint = glow(size, orb_center, orb_r * 3.2, (90, 190, 255, 140), orb_r * 1.4)
    wings = Image.alpha_composite(wings, ImageChops.multiply(tint, wings))

    layer = Image.alpha_composite(layer, glow(size, orb_center, orb_r * 2.6, (40, 150, 255, 90), orb_r * 1.3))
    layer = Image.alpha_composite(layer, wings)
    layer = Image.alpha_composite(layer, glow(size, orb_center, orb_r * 1.55, (60, 180, 255, 190), orb_r * 0.6))
    for scale, color, blur in ((1.1, (40, 150, 255, 255), 0.22),
                               (0.92, (100, 210, 255, 255), 0.2),
                               (0.66, (195, 242, 255, 255), 0.18),
                               (0.36, (255, 255, 255, 255), 0.12)):
        layer = Image.alpha_composite(layer, glow(size, orb_center, orb_r * scale, color, orb_r * blur))
    # Trailing sparkles.
    for fx, fy, fr in ((-1.2, 2.0, 0.22), (-0.3, 2.9, 0.15), (-1.9, 2.8, 0.12), (0.9, 2.3, 0.1)):
        c = (cx + fx * orb_r, cy + fy * orb_r)
        layer = Image.alpha_composite(layer, glow(size, c, orb_r * fr * 2.2, (70, 180, 255, 150), orb_r * fr))
        layer = Image.alpha_composite(layer, glow(size, c, orb_r * fr, (220, 245, 255, 255), orb_r * fr * 0.25))
    return layer


def sky(size, seed=7):
    img = radial(size, (18, 52, 110, 255), (3, 7, 20, 255), power=0.8)
    rng = random.Random(seed)
    stars = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(stars)
    for _ in range(140):
        x, y = rng.uniform(0, size), rng.uniform(0, size)
        r = rng.uniform(0.6, 2.2) * size / 512
        a = rng.randint(60, 220)
        d.ellipse((x - r, y - r, x + r, y + r), fill=(200, 225, 255, a))
    return Image.alpha_composite(img, stars.filter(ImageFilter.GaussianBlur(size / 1500)))


def main():
    art = fairy(S, (S * 0.5, S * 0.47), S * 0.12)
    icon = Image.alpha_composite(sky(S), art).convert("RGB")
    for px in (512, 180):
        icon.resize((px, px), Image.LANCZOS).save(os.path.join(HERE, f"icon-{px}.png"), optimize=True)
    # Transparent version for the page header.
    bbox = art.getbbox()
    art.crop(bbox).resize((400, int(400 * (bbox[3] - bbox[1]) / (bbox[2] - bbox[0]))),
                          Image.LANCZOS).save(os.path.join(HERE, "navi.png"), optimize=True)
    art.resize((64, 64), Image.LANCZOS).save(os.path.join(HERE, "favicon.png"), optimize=True)


if __name__ == "__main__":
    main()
