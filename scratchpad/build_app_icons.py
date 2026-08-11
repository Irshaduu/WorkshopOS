"""Regenerate every app icon from the one piece of source artwork.

Source: ``static/images/icons/app_icon_source.png`` — the FD·OS mark, supplied
by the owner and already run through TinyPNG. Everything else in
``static/images/icons/`` is derived from it by this script; nothing there should
be hand-edited, and a new mark means replacing the source and re-running this.

Two things it does that a plain resize would not:

**It crops to the ink first.** The supplied artwork sits in a lot of empty
canvas — the mark occupies well under half the height. Resized as-is to 32px the
mark would be about twelve pixels across in the middle of a white square, which
is the failure the icon it replaces already had (a photograph of the wordmark on
a concrete wall, unreadable below 128px). Cropping to the ink and re-padding to a
known margin is what makes the small sizes legible.

**It pads by purpose, not uniformly.** The 192 and 512 are declared
``"purpose": "any maskable"`` in ``manifest.json``, so Android crops them to
whatever shape the launcher uses and only the central 80% is guaranteed to
survive — those get the mark at 76% of the canvas. A favicon is never masked and
is fighting for legibility at 16px, so it gets 92%. The apple-touch icon sits
between: iOS rounds the corners but does not crop the middle.

The background is forced to pure white. The supplied file is near-white but not
white (the same thing the printed letterhead's artwork needed), and a 253-grey
square is visible as a faint box against a white browser tab.
"""
import os

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ICON_DIR = os.path.join(HERE, '..', 'static', 'images', 'icons')
SOURCE = os.path.join(ICON_DIR, 'app_icon_source.png')

# (filename, pixel size, fraction of the canvas the mark's WIDTH may occupy)
TARGETS = [
    ('icon-16.png', 16, 0.92),
    ('icon-32.png', 32, 0.92),
    ('icon-180.png', 180, 0.84),   # apple-touch — rounded, not cropped
    ('icon-192.png', 192, 0.76),   # maskable — must survive an 80% crop
    ('icon-512.png', 512, 0.76),   # maskable
]

# favicon.ico carries the small sizes browsers actually pick from.
ICO_SIZES = [16, 32, 48]
ICO_COVERAGE = 0.92

# Anything at least this bright on every channel counts as background.
WHITE_CUTOFF = 246


def ink_box(img):
    """The bounding box of everything that is not background white."""
    greyscale = img.convert('L')
    # point() -> 255 where there is ink, 0 where there is background.
    mask = greyscale.point(lambda v: 255 if v < WHITE_CUTOFF else 0)
    box = mask.getbbox()
    if box is None:
        raise SystemExit('No ink found in the source artwork — check WHITE_CUTOFF.')
    return box


def render(mark, size, coverage):
    """Centre `mark` on a white square of `size`, scaled to `coverage` of it.

    Scaled by whichever axis binds first, so a mark wider than it is tall (this
    one is roughly 2:1) is limited by its width and a tall one by its height.
    """
    target = size * coverage
    scale = min(target / mark.width, target / mark.height)
    w = max(1, round(mark.width * scale))
    h = max(1, round(mark.height * scale))

    resized = mark.resize((w, h), Image.LANCZOS)
    canvas = Image.new('RGB', (size, size), (255, 255, 255))
    canvas.paste(resized, ((size - w) // 2, (size - h) // 2))
    return canvas


def main():
    src = Image.open(SOURCE).convert('RGB')
    box = ink_box(src)
    mark = src.crop(box)
    print(f'source {src.size}  ink {box}  mark {mark.size} '
          f'({mark.width / mark.height:.2f}:1)')

    for name, size, coverage in TARGETS:
        out = os.path.join(ICON_DIR, name)
        render(mark, size, coverage).save(out, 'PNG', optimize=True)
        print(f'  {name:<14} {size:>4}px  coverage {coverage:.0%}  '
              f'{os.path.getsize(out):>7,} bytes')

    ico_path = os.path.join(ICON_DIR, 'favicon.ico')
    largest = render(mark, max(ICO_SIZES), ICO_COVERAGE)
    largest.save(ico_path, format='ICO',
                 sizes=[(s, s) for s in ICO_SIZES])
    print(f'  {"favicon.ico":<14} {ICO_SIZES}  '
          f'{os.path.getsize(ico_path):>7,} bytes')


if __name__ == '__main__':
    main()
