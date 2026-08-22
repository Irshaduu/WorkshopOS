"""
Fetch the third-party frontend assets into `static/vendor/`.

    python scratchpad/vendor_assets.py

WHY THESE ARE VENDORED AT ALL
-----------------------------
The app used to load Bootstrap's CSS, its icon font, its JS bundle, Chart.js and
the Barlow families from `cdn.jsdelivr.net` and `fonts.googleapis.com`, across 14
templates. Two things followed, and the second is the one that actually decided
it:

  1. The HTML arrives from our own origin while a subresource does not, so the
     page renders BROKEN rather than not at all — unstyled if the CSS drops, and
     with the drawer, every modal and every ⋮ dropdown dead if the JS drops. It
     needs our origin to work while the CDN specifically does not, which is
     narrower than "bad wifi" but is exactly what a cold cache on go-live day
     looks like.
  2. None of those tags carried an `integrity=` attribute, so a compromised CDN
     would have executed arbitrary JavaScript on every page of this app,
     including the settle screen.

Self-hosting answers both, and costs nothing at runtime: WhiteNoise serves these
content-hashed, pre-compressed and cached for a year, so after the first visit
they are free — which is faster than the CDN was, not slower.

⚠ NOTHING HERE IS HAND-EDITED. Same rule as `build_app_icons.py`: to change a
version, change it below and re-run, then run `collectstatic`. The versions are
the exact ones the CDN tags were pinned to, so the files arrive byte-identical
to what the app was already loading.

TWO THINGS THIS DOES THAT A PLAIN DOWNLOAD WOULD NOT
----------------------------------------------------
* **Strips `sourceMappingURL`.** The minified bundles end with a comment
  pointing at a `.map` file, and Django's ManifestStaticFilesStorage resolves
  those references — so `collectstatic` fails outright with MissingFileError
  unless the maps are vendored too. They are debugging aids for third-party
  minified code that nobody here will read, and shipping them would publish
  ~1MB more. Removing the comment changes no executable byte.
* **Drops the `vietnamese` font subset.** Google serves latin, latin-ext and
  vietnamese. This workshop writes English and Malayalam, and Barlow has no
  Malayalam glyphs either way, so that text already falls back to a system font.
  `unicode-range` is kept exactly as Google wrote it, which is what makes a
  browser download only the subset a page actually needs — so the 14 files here
  cost a normal English page just two.
"""
import os
import re
import urllib.request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENDOR = os.path.join(BASE, 'static', 'vendor')

BOOTSTRAP = '5.3.0'
BOOTSTRAP_ICONS = '1.11.0'
CHARTJS = '4.4.4'
FONTS_URL = ('https://fonts.googleapis.com/css2'
             '?family=Barlow+Condensed:wght@400;600;700;800'
             '&family=Barlow:wght@400;500;600&display=swap')
KEEP_SUBSETS = ('latin', 'latin-ext')

# Google serves a different stylesheet per browser; without a modern UA it
# answers with .ttf instead of the .woff2 every browser here supports.
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                    '(KHTML, like Gecko) Chrome/126.0 Safari/537.36'}

# Matches both the CSS (/*# ... */) and the JS (//# ...) forms.
SOURCE_MAP = re.compile(rb'\s*(?:/\*#\s*sourceMappingURL=[^*]+\*/|//#\s*sourceMappingURL=\S+)\s*$')


def fetch(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60).read()


def write(relative_path, data):
    path = os.path.join(VENDOR, *relative_path.split('/'))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as handle:
        handle.write(data)
    print(f'  {relative_path:52} {len(data):>8,} B')


def without_source_map(data):
    return SOURCE_MAP.sub(b'', data)


def vendor_libraries():
    cdn = 'https://cdn.jsdelivr.net/npm/'
    print(f'Bootstrap {BOOTSTRAP}, Bootstrap Icons {BOOTSTRAP_ICONS}, Chart.js {CHARTJS}:')
    write('bootstrap/bootstrap.min.css',
          without_source_map(fetch(f'{cdn}bootstrap@{BOOTSTRAP}/dist/css/bootstrap.min.css')))
    write('bootstrap/bootstrap.bundle.min.js',
          without_source_map(fetch(f'{cdn}bootstrap@{BOOTSTRAP}/dist/js/bootstrap.bundle.min.js')))
    write('chartjs/chart.umd.min.js',
          without_source_map(fetch(f'{cdn}chart.js@{CHARTJS}/dist/chart.umd.min.js')))

    # The icon CSS points at ./fonts/… relative to itself, so the font files
    # have to sit in a `fonts/` directory beside it. Django rewrites those
    # url() references at collectstatic time; get the layout wrong and it fails
    # loudly there rather than silently in a browser.
    icons = f'{cdn}bootstrap-icons@{BOOTSTRAP_ICONS}/font/'
    write('bootstrap-icons/bootstrap-icons.css', without_source_map(fetch(icons + 'bootstrap-icons.css')))
    write('bootstrap-icons/fonts/bootstrap-icons.woff2', fetch(icons + 'fonts/bootstrap-icons.woff2'))
    write('bootstrap-icons/fonts/bootstrap-icons.woff', fetch(icons + 'fonts/bootstrap-icons.woff'))


def vendor_fonts():
    print('\nBarlow / Barlow Condensed:')
    css = fetch(FONTS_URL).decode('utf-8')
    blocks = re.findall(r'/\* (\S+) \*/\s*(@font-face \{.*?\})', css, re.S)
    faces = []
    for subset, block in blocks:
        if subset not in KEEP_SUBSETS:
            continue
        family = re.search(r"font-family: '([^']+)'", block).group(1)
        weight = re.search(r'font-weight: (\d+)', block).group(1)
        url = re.search(r'url\((https://[^)]+)\)', block).group(1)
        name = f"{family.lower().replace(' ', '-')}-{weight}-{subset}.woff2"
        write(f'fonts/{name}', fetch(url))
        faces.append(re.sub(r'url\(https://[^)]+\)', f"url('./{name}')", block))

    header = (
        '/*\n'
        ' * Barlow and Barlow Condensed, served from this origin.\n'
        ' *\n'
        ' * GENERATED by scratchpad/vendor_assets.py — do not hand-edit.\n'
        ' *\n'
        f' * Source: {FONTS_URL}\n'
        ' *\n'
        ' * Only the latin and latin-ext subsets are kept, and `unicode-range` is\n'
        ' * left exactly as Google wrote it: that is what makes a browser download\n'
        ' * only the subset a page actually needs, so these 14 files cost a normal\n'
        ' * English page just two of them.\n'
        ' *\n'
        ' * `font-display: swap` is Google\'s own and is kept deliberately — text\n'
        ' * paints in a fallback immediately rather than hiding until the font\n'
        ' * arrives, which is the behaviour worth having on a workshop connection.\n'
        ' */\n'
    )
    path = os.path.join(VENDOR, 'fonts', 'barlow.css')
    with open(path, 'w', encoding='utf-8', newline='\n') as handle:
        handle.write(header + '\n' + '\n\n'.join(faces) + '\n')
    print(f'  {"fonts/barlow.css":52} {len(faces)} faces')


if __name__ == '__main__':
    vendor_libraries()
    vendor_fonts()
    print('\nNow run:  python manage.py collectstatic --noinput')
