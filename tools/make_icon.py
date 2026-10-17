"""Generate assets/icon.png and assets/icon.ico (Pillow only).

A rounded square with a pink-to-orange gradient, a white heart and a small
check mark. Run: python tools/make_icon.py
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
SIZE = 1024  # draw large, downscale for antialiasing
TOP = (255, 64, 129)     # pink
BOTTOM = (255, 138, 51)  # orange


def gradient(size: int) -> Image.Image:
    img = Image.new("RGB", (size, size))
    px = img.load()
    for y in range(size):
        for x in range(size):
            t = (x + y) / (2 * (size - 1))
            px[x, y] = tuple(round(a + (b - a) * t) for a, b in zip(TOP, BOTTOM))
    return img


def heart_mask(size: int) -> Image.Image:
    m = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(m)
    r = size * 0.145
    cy = size * 0.40
    cx1, cx2 = size * 0.5 - r, size * 0.5 + r
    d.ellipse([cx1 - r, cy - r, cx1 + r, cy + r], fill=255)
    d.ellipse([cx2 - r, cy - r, cx2 + r, cy + r], fill=255)
    k = r * 0.7071
    d.polygon(
        [(cx1 - k, cy + k), (cx2 + k, cy + k), (size * 0.5, cy + k + (r + k + r * 0.0))],
        fill=255,
    )
    return m


def build() -> Image.Image:
    s = SIZE
    base = gradient(s).convert("RGBA")
    corner = Image.new("L", (s, s), 0)
    ImageDraw.Draw(corner).rounded_rectangle([0, 0, s - 1, s - 1], radius=int(s * 0.22), fill=255)
    base.putalpha(corner)

    white = Image.new("RGBA", (s, s), (255, 255, 255, 255))
    base.paste(white, (0, 0), heart_mask(s))

    # check mark in the heart, drawn in the gradient pink
    d = ImageDraw.Draw(base)
    pts = [(s * 0.41, s * 0.46), (s * 0.48, s * 0.54), (s * 0.60, s * 0.38)]
    d.line(pts, fill=(255, 64, 129, 255), width=int(s * 0.045), joint="curve")
    for p in (pts[0], pts[-1]):
        w = s * 0.0225
        d.ellipse([p[0] - w, p[1] - w, p[0] + w, p[1] + w], fill=(255, 64, 129, 255))
    return base


def main() -> None:
    ASSETS.mkdir(exist_ok=True)
    big = build()
    png = big.resize((256, 256), Image.LANCZOS)
    png.save(ASSETS / "icon.png")
    png.save(ASSETS / "icon.ico", sizes=[(n, n) for n in (16, 24, 32, 48, 64, 128, 256)])
    print("wrote", ASSETS / "icon.png", ASSETS / "icon.ico")


if __name__ == "__main__":
    main()
