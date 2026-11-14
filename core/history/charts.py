"""Local trend charts for a usage briefing.

A PNG is written under the car's history directory so a chat assistant can
attach it. An HTML file next to it shows week, month, and year views with
no network access. matplotlib is used when it is installed. The builtin
PNG writer is the fallback and needs no extra package.
"""

from __future__ import annotations

import html
import struct
import zlib
from pathlib import Path

_GLYPHS = {
    " ": ("     ", "     ", "     ", "     ", "     ", "     ", "     "),
    "-": ("     ", "     ", "     ", "#####", "     ", "     ", "     "),
    ".": ("     ", "     ", "     ", "     ", "     ", " ##  ", " ##  "),
    "0": (" ### ", "#   #", "#  ##", "# # #", "##  #", "#   #", " ### "),
    "1": ("  #  ", " ##  ", "  #  ", "  #  ", "  #  ", "  #  ", " ### "),
    "2": (" ### ", "#   #", "    #", "  ## ", " #   ", "#    ", "#####"),
    "3": (" ### ", "#   #", "    #", " ### ", "    #", "#   #", " ### "),
    "4": ("   # ", "  ## ", " # # ", "#  # ", "#####", "   # ", "   # "),
    "5": ("#####", "#    ", "#### ", "    #", "    #", "#   #", " ### "),
    "6": (" ### ", "#    ", "#    ", "#### ", "#   #", "#   #", " ### "),
    "7": ("#####", "    #", "   # ", "  #  ", " #   ", " #   ", " #   "),
    "8": (" ### ", "#   #", "#   #", " ### ", "#   #", "#   #", " ### "),
    "9": (" ### ", "#   #", "#   #", " ####", "    #", "    #", " ### "),
    "W": ("#   #", "#   #", "#   #", "# # #", "# # #", "# # #", " # # "),
    "e": ("     ", "     ", " ### ", "#    ", "#### ", "#    ", " ### "),
    "h": ("#    ", "#    ", "#### ", "#   #", "#   #", "#   #", "#   #"),
    "k": ("#    ", "#   #", "#  # ", "###  ", "#  # ", "#   #", "#   #"),
    "m": ("     ", "     ", "## # ", "# # #", "# # #", "#   #", "#   #"),
    "n": ("     ", "     ", "#### ", "#   #", "#   #", "#   #", "#   #"),
    "o": ("     ", "     ", " ### ", "#   #", "#   #", "#   #", " ### "),
    "M": ("#   #", "## ##", "# # #", "# # #", "#   #", "#   #", "#   #"),
    "a": ("     ", "     ", " ### ", "    #", " ####", "#   #", " ####"),
    "r": ("     ", "     ", "# ## ", "##   ", "#    ", "#    ", "#    "),
    "y": ("     ", "     ", "#   #", "#   #", " ####", "    #", " ### "),
}


def write_trend_png(path: Path, title: str, km_labels: list[str], km_values: list[float], charge_labels: list[str], charge_values: list[float]) -> str:
    """Write a PNG. Return ``matplotlib`` or ``builtin``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        _matplotlib_png(path, title, km_labels, km_values, charge_labels, charge_values)
        return "matplotlib"
    except Exception:
        _builtin_png(path, km_labels, km_values, charge_labels, charge_values)
        return "builtin"


def write_builtin_png(path: Path, km_labels: list[str], km_values: list[float], charge_labels: list[str], charge_values: list[float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _builtin_png(path, km_labels, km_values, charge_labels, charge_values)


def write_dashboard(path: Path, views: list[dict]) -> None:
    """Write one offline HTML file. ``views`` hold numbers and short labels only."""
    path.parent.mkdir(parents=True, exist_ok=True)
    sections = "\n".join(_section(view) for view in views)
    document = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Vehicle history</title>
<style>
body {{ font-family: sans-serif; margin: 24px; color: #1c1c1c; background: #f7f6f3; }}
h1 {{ font-size: 1.4rem; }}
section {{ background: white; padding: 16px 20px; margin: 0 0 16px; }}
h2 {{ font-size: 1.1rem; margin-top: 0; }}
.note {{ color: #444; }}
svg {{ width: 100%; height: auto; }}
</style>
</head>
<body>
<h1>Vehicle history</h1>
<p class="note">Saved on this computer. History is opportunistic, not a continuous record. This file does not contact the car.</p>
{sections}
</body>
</html>
"""
    path.write_text(document, encoding="utf-8")


def _section(view: dict) -> str:
    title = html.escape(str(view.get("title") or "Period"))
    note = html.escape(str(view.get("note") or ""))
    km = _svg("Kilometres", view.get("km_labels") or [], view.get("km_values") or [], "#2f6f4e")
    energy = _svg("kWh per charge", view.get("charge_labels") or [], view.get("charge_values") or [], "#2c5f8a")
    return f"<section><h2>{title}</h2><p>{note}</p>{km}{energy}</section>"


def _svg(caption: str, labels: list[str], values: list[float], color: str) -> str:
    width = 640
    height = 160
    left = 36
    bottom = 28
    plot_w = width - left - 12
    plot_h = height - bottom - 22
    numbers = [float(item) for item in values] or [0.0]
    peak = max(numbers) if numbers else 0.0
    peak = peak if peak > 0 else 1.0
    count = max(len(numbers), 1)
    gap = plot_w / count
    bars = []
    texts = []
    for index, value in enumerate(numbers):
        bar_h = 0 if not values else (float(value) / peak) * plot_h
        x = left + index * gap + gap * 0.15
        y = 18 + plot_h - bar_h
        bars.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{gap * 0.7:.1f}" height="{bar_h:.1f}" fill="{color}"/>'
        )
        if labels and index < len(labels) and (count <= 16 or index % max(1, count // 12) == 0):
            texts.append(
                f'<text x="{x:.1f}" y="{height - 8}" font-size="10" fill="#333">{html.escape(str(labels[index]))}</text>'
            )
    caption_text = html.escape(caption)
    return (
        f'<svg viewBox="0 0 {width} {height}" role="img">'
        f'<text x="{left}" y="14" font-size="12" fill="#222">{caption_text}</text>'
        f'<line x1="{left}" y1="{18 + plot_h}" x2="{width - 12}" y2="{18 + plot_h}" stroke="#bbb"/>'
        + "".join(bars)
        + "".join(texts)
        + "</svg>"
    )


def _matplotlib_png(path, title, km_labels, km_values, charge_labels, charge_values) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 1, figsize=(8.2, 5.2), dpi=100)
    _bars(axes[0], km_labels, km_values, "Kilometres", "#2f6f4e")
    _bars(axes[1], charge_labels, charge_values, "kWh per charge", "#2c5f8a")
    fig.suptitle(title)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def _bars(axis, labels, values, ylabel, color) -> None:
    points = [float(item) for item in values]
    axis.bar(range(len(points)), points, color=color)
    axis.set_ylabel(ylabel)
    if labels:
        step = max(1, len(labels) // 12)
        ticks = list(range(0, len(labels), step))
        axis.set_xticks(ticks)
        axis.set_xticklabels([labels[index] for index in ticks], rotation=0)
    else:
        axis.set_xticks([])


def _builtin_png(path: Path, km_labels, km_values, charge_labels, charge_values) -> None:
    width = 720
    height = 420
    pixels = bytearray([255, 255, 255] * width * height)
    _panel(pixels, width, 16, 24, 688, 170, "km", km_labels, km_values, (47, 111, 78))
    _panel(pixels, width, 16, 214, 688, 170, "kWh", charge_labels, charge_values, (44, 95, 138))
    path.write_bytes(_encode_png(width, height, pixels))


def _panel(pixels, width, x, y, panel_w, panel_h, caption, labels, values, color) -> None:
    _text(pixels, width, len(pixels) // (width * 3), x, y, caption, (20, 20, 20), 2)
    plot_top = y + 24
    plot_h = panel_h - 36
    numbers = [float(item) for item in values]
    if not numbers:
        _text(pixels, width, len(pixels) // (width * 3), x, plot_top + 20, "none", (80, 80, 80), 2)
        return
    peak = max(numbers) if max(numbers) > 0 else 1.0
    gap = panel_w / len(numbers)
    for index, value in enumerate(numbers):
        bar_h = int((float(value) / peak) * (plot_h - 8))
        bar_x = int(x + index * gap + gap * 0.2)
        bar_w = max(1, int(gap * 0.6))
        bar_y = plot_top + plot_h - bar_h
        _rect(pixels, width, len(pixels) // (width * 3), bar_x, bar_y, bar_w, bar_h, color)
    _hline(pixels, width, len(pixels) // (width * 3), x, plot_top + plot_h, panel_w, (180, 180, 180))


def _rect(pixels, width, height, x, y, rect_w, rect_h, color) -> None:
    for py in range(y, y + rect_h):
        if py < 0 or py >= height:
            continue
        for px in range(x, x + rect_w):
            if 0 <= px < width:
                index = (py * width + px) * 3
                pixels[index : index + 3] = bytes(color)


def _hline(pixels, width, height, x, y, line_w, color) -> None:
    _rect(pixels, width, height, x, y, line_w, 1, color)


def _text(pixels, width, height, x, y, text, color, scale) -> None:
    cursor = x
    for char in text:
        glyph = _GLYPHS.get(char, _GLYPHS[" "])
        for row, bits in enumerate(glyph):
            for col, mark in enumerate(bits):
                if mark != "#":
                    continue
                for sy in range(scale):
                    for sx in range(scale):
                        px = cursor + col * scale + sx
                        py = y + row * scale + sy
                        if 0 <= px < width and 0 <= py < height:
                            index = (py * width + px) * 3
                            pixels[index : index + 3] = bytes(color)
        cursor += 6 * scale


def _encode_png(width: int, height: int, pixels: bytearray) -> bytes:
    raw = bytearray()
    stride = width * 3
    for y in range(height):
        raw.append(0)
        raw.extend(pixels[y * stride : (y + 1) * stride])

    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b"")
