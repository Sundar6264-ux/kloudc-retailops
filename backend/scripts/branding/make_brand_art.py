"""Build every KloudC RetailOps image the server, website and app ship from
the brand sources in backend/app/assets/branding/source/ (the KloudC paper
plane, the Plane-R app icon icon.svg / icon-1024.png and its rounded mark.svg,
and the KloudC RetailOps lockup; brand colours KloudC blue #004BAD and sky
#2EA3F2). Run it again after the sources change; every
output is overwritten.

    python scripts/branding/make_brand_art.py                         # server + website
    python scripts/branding/make_brand_art.py --app-repo ../../storeops-app

Outputs
  server   app/assets/branding/: logo.svg, logo-512.png, logo-128.png (the plane:
           the product logo a store without its own logo gets), app-icon-1024.png
  website  frontend/public/: logo.svg (favicon, the Plane-R mark), favicon-32.png, favicon-512.png,
           icon-192.png, apple-touch-icon.png, logo-header.png,
           kloudc-retailops-logo.svg, kloudc-retailops-logo.png, manifest.webmanifest
  app      assets/icon/app_icon.png, assets/icon/wordmark.png, the iOS AppIcon set
           (no alpha channel - an App Store rule) and the Android launcher mipmaps.

Needs Pillow (in requirements.txt).
"""

import argparse
import json
import pathlib
import shutil

from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parents[3]  # the repository root
SOURCE = ROOT / "backend" / "app" / "assets" / "branding" / "source"
PRODUCT_NAME = "KloudC RetailOps"
SHORT_NAME = "RetailOps"
TAGLINE = "Make Your POS Inventory Work Like Your Real Store."
KLOUDC_BLUE = "#004BAD"
WHITE = (255, 255, 255)

IOS_SET = pathlib.Path("ios/Runner/Assets.xcassets/AppIcon.appiconset")
ANDROID_MIPMAPS = {"mdpi": 48, "hdpi": 72, "xhdpi": 96, "xxhdpi": 144, "xxxhdpi": 192}


def _resize(img: Image.Image, size) -> Image.Image:
    if isinstance(size, int):
        size = (size, size)
    return img.resize(size, Image.LANCZOS)


def _rgb(img: Image.Image) -> Image.Image:
    """Flattened onto white (no alpha channel)."""
    if img.mode == "RGB":
        return img
    base = Image.new("RGB", img.size, WHITE)
    base.paste(img, mask=img.convert("RGBA").split()[3])
    return base


def _save(img: Image.Image, path: pathlib.Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, optimize=True)
    print("wrote", path)


def server_and_web() -> None:
    icon = Image.open(SOURCE / "icon-1024.png").convert("RGB")
    plane = Image.open(SOURCE / "plane-512.png").convert("RGBA")
    lockup = Image.open(SOURCE / "logo-1200x420.png").convert("RGBA")

    branding = ROOT / "backend" / "app" / "assets" / "branding"
    shutil.copyfile(SOURCE / "plane.svg", branding / "logo.svg")
    _save(plane, branding / "logo-512.png")
    _save(_resize(plane, 128), branding / "logo-128.png")
    _save(icon, branding / "app-icon-1024.png")

    public = ROOT / "frontend" / "public"
    shutil.copyfile(SOURCE / "mark.svg", public / "logo.svg")
    shutil.copyfile(SOURCE / "logo.svg", public / "kloudc-retailops-logo.svg")
    _save(_resize(icon, 32), public / "favicon-32.png")
    _save(_resize(icon, 512), public / "favicon-512.png")
    _save(_resize(icon, 192), public / "icon-192.png")
    _save(_resize(icon, 180), public / "apple-touch-icon.png")
    _save(_resize(plane, 128), public / "logo-header.png")
    _save(_resize(lockup, (600, 210)), public / "kloudc-retailops-logo.png")
    manifest = {
        "name": PRODUCT_NAME,
        "short_name": SHORT_NAME,
        "description": TAGLINE,
        "start_url": "/",
        "display": "standalone",
        "background_color": "#FFFFFF",
        "theme_color": KLOUDC_BLUE,
        "icons": [
            {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/favicon-512.png", "sizes": "512x512", "type": "image/png"},
            {"src": "/logo.svg", "sizes": "any", "type": "image/svg+xml"},
        ],
    }
    path = public / "manifest.webmanifest"
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print("wrote", path)


def app(app_repo: pathlib.Path) -> None:
    icon = Image.open(SOURCE / "icon-1024.png").convert("RGB")
    lockup = Image.open(SOURCE / "logo-1200x420.png").convert("RGBA")
    _save(icon, app_repo / "assets" / "icon" / "app_icon.png")
    _save(_rgb(lockup), app_repo / "assets" / "icon" / "wordmark.png")
    contents = json.loads((app_repo / IOS_SET / "Contents.json").read_text(encoding="utf-8"))
    for image in contents["images"]:
        points = float(image["size"].split("x")[0])
        px = round(points * int(image["scale"].rstrip("x")))
        _save(_rgb(_resize(icon, px)), app_repo / IOS_SET / image["filename"])
    for density, px in ANDROID_MIPMAPS.items():
        _save(_resize(icon, px), app_repo / "android" / "app" / "src" / "main" / "res" / f"mipmap-{density}" / "ic_launcher.png")


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the KloudC RetailOps images from the brand sources.")
    ap.add_argument("--app-repo", type=pathlib.Path, help="also write the app's icons (path to the app repository)")
    args = ap.parse_args()
    server_and_web()
    if args.app_repo:
        app(args.app_repo.resolve())


if __name__ == "__main__":
    main()
