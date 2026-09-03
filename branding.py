"""Name, colours and app mark, in one place.

Everything that shows the app to the user reads from here: the tray icon, the
window icon, the .ico baked into the launcher executable and the favicon. The
mark is drawn rather than loaded so the running app never depends on an asset
file being present; `tools/make_icon.py` renders the same drawing to disk for
the places that genuinely need a file, like a Windows executable resource.
"""

from __future__ import annotations

from PIL import Image, ImageDraw

APP_NAME = "Arete"
# Reverse-DNS-ish and stable. Windows keys taskbar grouping, jump lists and the
# Task Manager app entry off this, so changing it later orphans pinned icons.
APP_ID = "Arete.Clips"
APP_TAGLINE = "Press F9, get a link to the last 30 seconds."

BACKGROUND = (18, 22, 31, 255)
# The mark is a status light as well as a logo: warm when the ring buffer is
# running, grey when capture failed and only the library works.
LIVE = ((248, 96, 122, 255), (255, 179, 71, 255))
IDLE = ((122, 134, 154, 255), (92, 102, 120, 255))

# The letter A as an apex, in a 0..1 square: a solid triangle with the counter
# and the gap between the legs cut back out of it. Kept deliberately heavy so
# the crossbar survives being resampled down to a 16px tray icon.
_OUTER = [(0.50, 0.11), (0.94, 0.89), (0.06, 0.89)]
_COUNTER = [(0.50, 0.37), (0.648, 0.60), (0.352, 0.60)]
_LEG_GAP = [(0.352, 0.72), (0.648, 0.72), (0.735, 0.89), (0.265, 0.89)]

_SUPERSAMPLE = 8


def _scale(points: list[tuple[float, float]], size: int) -> list[tuple[float, float]]:
    return [(x * size, y * size) for x, y in points]


def _gradient(size: int, top: tuple[int, ...], bottom: tuple[int, ...]) -> Image.Image:
    gradient = Image.new("RGBA", (1, size))
    for y in range(size):
        t = y / max(size - 1, 1)
        gradient.putpixel(
            (0, y), tuple(round(a + (b - a) * t) for a, b in zip(top, bottom))
        )
    return gradient.resize((size, size), Image.Resampling.BILINEAR)


def icon_image(size: int = 256, recording: bool = True) -> Image.Image:
    """The app mark at `size` px.

    Drawn oversized and resampled down rather than drawn at the target size:
    PIL has no antialiased polygon fill, and at 16px the difference between a
    supersampled edge and a jagged one is the difference between a logo and a
    smudge.
    """
    big = size * _SUPERSAMPLE
    canvas = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    inset = big * 0.02
    draw.rounded_rectangle(
        [inset, inset, big - inset, big - inset],
        radius=big * 0.22,
        fill=BACKGROUND,
    )

    # Build the A as a mask, then paint the gradient through it, so the letter
    # carries the colour rather than being flat.
    mark = Image.new("L", (big, big), 0)
    mark_draw = ImageDraw.Draw(mark)
    mark_draw.polygon(_scale(_OUTER, big), fill=255)
    mark_draw.polygon(_scale(_COUNTER, big), fill=0)
    mark_draw.polygon(_scale(_LEG_GAP, big), fill=0)

    top, bottom = LIVE if recording else IDLE
    canvas.paste(_gradient(big, top, bottom), (0, 0), mark)

    return canvas.resize((size, size), Image.Resampling.LANCZOS)
