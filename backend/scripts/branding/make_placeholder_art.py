"""Generate the StoreOps placeholder artwork (logo, favicons, app icons).

A plain geometric storefront on a blue tile - a placeholder until a designer
makes the real logo. Nothing here is copied from any store's artwork. Run it
again after changing COLORS or the shapes below; every output is overwritten.

    python scripts/branding/make_placeholder_art.py                 # server + web files
    python scripts/branding/make_placeholder_art.py --app-repo ../../storeops-app

Needs Pillow (already in requirements.txt). The wordmark text uses a bold
system font when one is found (macOS Arial Bold / Helvetica, DejaVu on
Linux); without one the wordmark is the icon alone.
"""

import argparse
import pathlib
from typing import Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

PRODUCT_NAME = "StoreOps"
COLORS = {
    "tile": (36, 87, 166),  # #2457A6
    "awning_alt": (207, 224, 247),  # #CFE0F7
    "white": (255, 255, 255),
    "ink": (27, 43, 68),  # #1B2B44
}
SS = 4  # supersampling factor for smooth edges

ROOT = pathlib.Path(__file__).resolve().parents[3]  # repo root (storeops/)

FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


def _hex(rgb: Tuple[int, int, int]) -> str:
    return "#%02X%02X%02X" % rgb


def _storefront(draw: ImageDraw.ImageDraw, ox: float, oy: float, s: float, door_color) -> None:
    """The storefront drawn into a 512-unit box at (ox, oy), scaled by s."""

    def r(x0, y0, x1, y1, fill):
        draw.rectangle([ox + x0 * s, oy + y0 * s, ox + x1 * s - 1, oy + y1 * s - 1], fill=fill)

    # Awning: four stripes, each ending in a half-circle scallop.
    for i in range(4):
        x0 = 112 + i * 72
        fill = COLORS["white"] if i % 2 == 0 else COLORS["awning_alt"]
        r(x0, 136, x0 + 72, 216, fill)
        draw.pieslice(
            [ox + x0 * s, oy + 180 * s, ox + (x0 + 72) * s - 1, oy + 252 * s - 1], 0, 180, fill=fill
        )
    # Shop body, window and door.
    r(136, 252, 376, 392, COLORS["white"])
    r(160, 276, 216, 332, door_color)
    r(296, 276, 352, 332, door_color)
    r(228, 296, 284, 392, door_color)
    # Pavement line.
    r(112, 392, 400, 412, COLORS["white"])


def icon(size: int, rounded: bool, transparent_corners: bool = True) -> Image.Image:
    """Square icon. rounded=False gives a full-bleed tile (iOS/Android mask it)."""
    big = size * SS
    mode = "RGBA" if (rounded and transparent_corners) else "RGB"
    img = Image.new(mode, (big, big), (0, 0, 0, 0) if mode == "RGBA" else COLORS["tile"])
    d = ImageDraw.Draw(img)
    if rounded:
        d.rounded_rectangle([0, 0, big - 1, big - 1], radius=int(big * 0.22), fill=COLORS["tile"])
    _storefront(d, 0, 0, big / 512.0, COLORS["tile"])
    return img.resize((size, size), Image.LANCZOS)


def foreground(size: int) -> Image.Image:
    """Android adaptive-icon foreground: the storefront in the safe zone, transparent."""
    big = size * SS
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    inner = big * 0.66  # the 66% safe zone
    off = (big - inner) / 2
    tile = Image.new("RGBA", (int(inner), int(inner)), COLORS["tile"] + (255,))
    img.paste(tile, (int(off), int(off)))
    _storefront(d, off, off, inner / 512.0, COLORS["tile"])
    return img.resize((size, size), Image.LANCZOS)


def _font(px: int) -> Optional[ImageFont.FreeTypeFont]:
    for path in FONT_CANDIDATES:
        if pathlib.Path(path).exists():
            try:
                index = 1 if path.endswith(".ttc") else 0  # Helvetica.ttc face 1 = Bold
                return ImageFont.truetype(path, px, index=index)
            except OSError:
                continue
    return None


def wordmark(width: int, height: int) -> Image.Image:
    """Icon above the product name, on white (the app's login and splash)."""
    img = Image.new("RGB", (width, height), COLORS["white"])
    icon_px = int(height * 0.52)
    tile = icon(icon_px, rounded=True)
    font = _font(int(height * 0.17))
    text_h = 0
    if font is not None:
        bbox = font.getbbox(PRODUCT_NAME)
        text_h = bbox[3] - bbox[1]
    gap = int(height * 0.06) if font is not None else 0
    top = (height - icon_px - gap - text_h) // 2
    img.paste(tile, ((width - icon_px) // 2, top), tile)
    if font is not None:
        d = ImageDraw.Draw(img)
        bbox = font.getbbox(PRODUCT_NAME)
        tw = bbox[2] - bbox[0]
        d.text(((width - tw) // 2 - bbox[0], top + icon_px + gap - bbox[1]), PRODUCT_NAME, font=font, fill=COLORS["ink"])
    return img


def svg_logo() -> str:
    """The same shapes as _storefront, as an SVG (the website and branding default)."""
    t, w, a = _hex(COLORS["tile"]), _hex(COLORS["white"]), _hex(COLORS["awning_alt"])
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" width="512" height="512" '
        'role="img" aria-label="%s">' % PRODUCT_NAME,
        '<rect width="512" height="512" rx="113" fill="%s"/>' % t,
    ]
    for i in range(4):
        x0 = 112 + i * 72
        fill = w if i % 2 == 0 else a
        parts.append('<rect x="%d" y="136" width="72" height="80" fill="%s"/>' % (x0, fill))
        parts.append('<path d="M%d 216 a36 36 0 0 0 72 0 z" fill="%s"/>' % (x0, fill))
    parts += [
        '<rect x="136" y="252" width="240" height="140" fill="%s"/>' % w,
        '<rect x="160" y="276" width="56" height="56" fill="%s"/>' % t,
        '<rect x="296" y="276" width="56" height="56" fill="%s"/>' % t,
        '<rect x="228" y="296" width="56" height="96" fill="%s"/>' % t,
        '<rect x="112" y="392" width="288" height="20" fill="%s"/>' % w,
        "</svg>",
    ]
    return "\n".join(parts) + "\n"


def _save(img: Image.Image, path: pathlib.Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, optimize=True)
    print("wrote", path)


def server_files(root: pathlib.Path) -> None:
    branding = root / "backend" / "app" / "assets" / "branding"
    branding.mkdir(parents=True, exist_ok=True)
    (branding / "logo.svg").write_text(svg_logo())
    _save(icon(512, rounded=True), branding / "logo-512.png")
    _save(icon(128, rounded=True), branding / "logo-128.png")
    _save(icon(1024, rounded=False), branding / "app-icon-1024.png")

    public = root / "frontend" / "public"
    (public / "logo.svg").write_text(svg_logo())
    _save(icon(128, rounded=True), public / "logo-header.png")
    _save(icon(32, rounded=True), public / "favicon-32.png")
    _save(icon(512, rounded=True), public / "favicon-512.png")
    _save(icon(180, rounded=False), public / "apple-touch-icon.png")


def app_files(app_root: pathlib.Path) -> None:
    assets = app_root / "assets" / "icon"
    _save(icon(1024, rounded=False), assets / "app_icon.png")
    _save(wordmark(900, 577), assets / "wordmark.png")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--app-repo", help="Path to the storeops-app checkout (writes its icon sources too)")
    p.add_argument("--skip-server", action="store_true", help="Only write the app files")
    args = p.parse_args(argv)
    if not args.skip_server:
        server_files(ROOT)
    if args.app_repo:
        app_files(pathlib.Path(args.app_repo).resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
