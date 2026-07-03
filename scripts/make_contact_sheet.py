# One-off helper: build labeled contact sheets of release-review images.
import glob
import os
from PIL import Image, ImageDraw, ImageFont

from PyQt6.QtWidgets import QApplication
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtGui import QImage, QPainter
from PyQt6.QtCore import QSize, Qt

app = QApplication([])

TILE = 200          # image area per tile
CAP = 26            # caption strip height
PAD = 10
COLS = 6
BG = (40, 40, 40)
TILE_BG = (120, 120, 120)   # mid-gray so both light/dark art is visible
HDR_BG = (24, 24, 24)
TXT = (235, 235, 235)

try:
    cap_font = ImageFont.truetype("arial.ttf", 11)
    hdr_font = ImageFont.truetype("arialbd.ttf", 18)
except Exception:
    cap_font = ImageFont.load_default()
    hdr_font = ImageFont.load_default()


def load_image(path):
    """Return an RGBA PIL image for raster or SVG paths."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".svg":
        r = QSvgRenderer(path)
        if not r.isValid():
            return None
        qimg = QImage(QSize(TILE, TILE), QImage.Format.Format_ARGB32)
        qimg.fill(0)
        p = QPainter(qimg)
        r.render(p)
        p.end()
        buf = qimg.bits().asstring(qimg.sizeInBytes())
        return Image.frombytes("RGBA", (TILE, TILE), buf, "raw", "BGRA")
    try:
        return Image.open(path).convert("RGBA")
    except Exception:
        return None


def is_symlink_stub(path):
    """Tiny text files like '../game_screenshots/x.png' are git symlinks whose
    target is excluded from the repo/release (dangling, not distributed)."""
    try:
        if os.path.getsize(path) < 300:
            with open(path, "rb") as f:
                head = f.read(300)
            if head[:3] in (b"../", b"./", b"/") or head.startswith(b".."):
                return head.decode("utf-8", "replace").strip()
    except Exception:
        pass
    return None


def make_tile(path):
    tile = Image.new("RGBA", (TILE, TILE + CAP), TILE_BG + (255,))
    stub = is_symlink_stub(path)
    if stub:
        link = Image.new("RGBA", (TILE, TILE), (45, 55, 70, 255))
        d2 = ImageDraw.Draw(link)
        d2.text((10, TILE // 2 - 18), "  symlink ->", fill=(150, 180, 220), font=cap_font)
        d2.text((10, TILE // 2), "  " + stub[:26], fill=(150, 180, 220), font=cap_font)
        d2.text((10, TILE // 2 + 18), "  (not in repo/release)", fill=(200, 160, 90), font=cap_font)
        tile.paste(link, (0, 0))
        cap = os.path.basename(path)
        d = ImageDraw.Draw(tile)
        d.rectangle([0, TILE, TILE, TILE + CAP], fill=HDR_BG)
        d.text((4, TILE + 6), cap[:30], fill=TXT, font=cap_font)
        return tile
    img = load_image(path)
    if img is not None:
        im = img.copy()
        im.thumbnail((TILE - 8, TILE - 8))
        bg = Image.new("RGBA", (TILE, TILE), TILE_BG + (255,))
        bg.alpha_composite(im, ((TILE - im.width) // 2, (TILE - im.height) // 2))
        tile.paste(bg, (0, 0))
        cap = os.path.basename(path)
    else:
        cap = "[render fail] " + os.path.basename(path)
    d = ImageDraw.Draw(tile)
    d.rectangle([0, TILE, TILE, TILE + CAP], fill=HDR_BG)
    cap = cap if len(cap) <= 30 else cap[:28] + ".."
    d.text((4, TILE + 6), cap, fill=TXT, font=cap_font)
    return tile


def build_sheet(sections, out_path):
    # Pre-measure height.
    rows_total = 0
    for _, files in sections:
        n = len([f for f in files if os.path.exists(f)])
        rows_total += (n + COLS - 1) // COLS
    hdr_h = 34
    width = COLS * (TILE + PAD) + PAD
    height = PAD
    for title, files in sections:
        files = [f for f in files if os.path.exists(f)]
        rows = (len(files) + COLS - 1) // COLS
        height += hdr_h + rows * (TILE + CAP + PAD)
    sheet = Image.new("RGBA", (width, height), BG + (255,))
    d = ImageDraw.Draw(sheet)
    y = PAD
    for title, files in sections:
        files = [f for f in files if os.path.exists(f)]
        d.rectangle([0, y, width, y + hdr_h], fill=HDR_BG)
        d.text((PAD, y + 7), title, fill=(255, 210, 90), font=hdr_font)
        y += hdr_h
        for i, f in enumerate(files):
            col = i % COLS
            if col == 0 and i > 0:
                y += TILE + CAP + PAD
            x = PAD + col * (TILE + PAD)
            sheet.alpha_composite(make_tile(f), (x, y))
        if files:
            y += TILE + CAP + PAD
    sheet.convert("RGB").save(out_path)
    print(f"saved {out_path}  ({width}x{height})")


def g(pattern):
    return sorted(glob.glob(pattern))


# Sheet 1: game-branded scoreboard images + template previews
sheet1 = [
    ("[HIGH] layout/scoreboard - game-branded images", g("layout/scoreboard/*.png")),
    ("[HIGH] template preview images (TSH demo origin)",
     g("layout/bracket_top8/*_preview.png") + g("layout/commentators/*_preview.png")
     + g("layout/recent_sets/*_preview.png") + g("layout/scoreboard_simple/index*.png")
     + g("layout/scoreboard_topleft/index*.png") + g("layout/top_8/*_preview.png")
     + g("layout/tournament_history/*_preview.png")),
]

# Sheet 2: branding/icons + thumbnail_base + mock + misc + flags
sheet2 = [
    ("[HIGH] app icon / third-party logos",
     ["assets/icons/icon.png", "assets/icons/icon.svg", "assets/icons/smashgg.svg",
      "assets/icons/startgg.svg", "assets/icons/twitch.svg", "assets/icons/bsky.svg",
      "layout/logo.png"]),
    ("[MED] thumbnail_base - default thumbnail assets",
     ["assets/thumbnail_base/default_banner.png", "assets/thumbnail_base/foreground.png",
      "assets/thumbnail_base/standard_default_bg.png"]),
    ("[OK?] mock SVG placeholders (made this session)",
     g("assets/mock_data/mock_asset/*.svg")),
    ("[LOW] misc overlay icons",
     g("layout/scoreboard_Universal/*.png") + g("layout/scoreboard_team/*.png")),
    ("[LOW] additional_flag (pride/identity flags)", g("user_data/additional_flag/*.png")),
]

build_sheet(sheet1, "out/contact_sheet_1.png")
build_sheet(sheet2, "out/contact_sheet_2.png")
