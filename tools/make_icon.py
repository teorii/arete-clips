r"""Render the app mark to the files Windows and the browser need.

Run after changing anything in `branding.py`:

    .venv\Scripts\python tools\make_icon.py

Writes assets/arete.ico (the multi-resolution icon Windows picks from for the
taskbar, Alt-Tab and Task Manager), assets/arete.png (a 256px preview) and
frontend/public/favicon.svg (the same mark as vector, for the web app).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from branding import BACKGROUND, LIVE, _COUNTER, _LEG_GAP, _OUTER, icon_image  # noqa: E402

ASSETS = ROOT / "assets"
# Every size Windows asks for. Leaving one out makes the shell scale a
# neighbour instead, which is where blurry taskbar icons come from.
ICO_SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]

SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" role="img" aria-label="Arete">
  <defs>
    <linearGradient id="a" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="{top}"/>
      <stop offset="1" stop-color="{bottom}"/>
    </linearGradient>
  </defs>
  <rect x="{inset}" y="{inset}" width="{span}" height="{span}" rx="{radius}" fill="{background}"/>
  <path fill="url(#a)" fill-rule="evenodd" d="{path}"/>
</svg>
"""


def _hex(colour: tuple[int, ...]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*colour[:3])


def _path(points: list[tuple[float, float]], size: int) -> str:
    head, *rest = [(round(x * size, 2), round(y * size, 2)) for x, y in points]
    return f"M{head[0]} {head[1]}" + "".join(f"L{x} {y}" for x, y in rest) + "Z"


def write_ico(path: Path) -> None:
    # Pillow resamples for the extra sizes if handed one image, but each size is
    # tuned by supersampling in branding.icon_image, so pass them all in.
    largest = icon_image(max(ICO_SIZES))
    largest.save(
        path,
        format="ICO",
        sizes=[(s, s) for s in ICO_SIZES],
        append_images=[icon_image(s) for s in ICO_SIZES if s != max(ICO_SIZES)],
    )


def write_svg(path: Path) -> None:
    size = 256
    path.write_text(
        SVG.format(
            top=_hex(LIVE[0]),
            bottom=_hex(LIVE[1]),
            background=_hex(BACKGROUND),
            inset=round(size * 0.02, 2),
            span=round(size * 0.96, 2),
            radius=round(size * 0.22, 2),
            path="".join(_path(p, size) for p in (_OUTER, _COUNTER, _LEG_GAP)),
        ),
        encoding="utf-8",
    )


def main() -> int:
    ASSETS.mkdir(exist_ok=True)
    write_ico(ASSETS / "arete.ico")
    icon_image(256).save(ASSETS / "arete.png")
    write_svg(ROOT / "frontend" / "public" / "favicon.svg")
    print(f"wrote {ASSETS / 'arete.ico'}, {ASSETS / 'arete.png'} and the favicon")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
