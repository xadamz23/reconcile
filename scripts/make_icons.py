"""Render the app icon SVGs to the PNG/ICO sizes the PWA and browsers need.

Run after editing static/icons/icon.svg, static/icons/favicon.svg or scripts/maskable.svg:

    uv run --no-project --with resvg-py --with pillow python scripts/make_icons.py

Outputs are committed, so the app itself has no image-rendering dependency.
"""

import io
from pathlib import Path

import resvg_py
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
ICONS = ROOT / "static" / "icons"


def render(svg: Path, size: int) -> Image.Image:
    png = resvg_py.svg_to_bytes(svg_path=str(svg), width=size, height=size)
    return Image.open(io.BytesIO(bytes(png))).convert("RGBA")


def main() -> None:
    icon, favicon, maskable = ICONS / "icon.svg", ICONS / "favicon.svg", ROOT / "scripts" / "maskable.svg"
    render(icon, 192).save(ICONS / "icon-192.png", optimize=True)
    render(icon, 512).save(ICONS / "icon-512.png", optimize=True)
    render(maskable, 512).save(ICONS / "maskable-512.png", optimize=True)
    # iOS rounds the corners itself, so it gets the full-bleed artwork.
    render(maskable, 180).convert("RGB").save(ICONS / "apple-touch-icon.png", optimize=True)
    render(favicon, 64).save(ICONS / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])
    print("Wrote icons to", ICONS)


if __name__ == "__main__":
    main()
