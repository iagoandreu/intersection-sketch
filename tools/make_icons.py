"""Draw the toolbar icons.  py -3.10 tools/make_icons.py

Two overlapping grey bodies and, in orange, the curve where they meet.
"""

import os

from PIL import Image, ImageDraw

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   'resources', 'IntersectionSketch')


def icon(n):
    k = 4
    s = n * k
    u = s / 64.0
    im = Image.new('RGBA', (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((4 * u, 14 * u, 40 * u, 58 * u), radius=6 * u,
                        fill=(165, 170, 176, 255), outline=(90, 94, 100, 255), width=int(2 * u))
    d.ellipse((22 * u, 4 * u, 60 * u, 42 * u), fill=(120, 126, 134, 230),
              outline=(70, 74, 80, 255), width=int(2 * u))
    d.arc((22 * u, 4 * u, 60 * u, 42 * u), 90, 270, fill=(255, 138, 0, 255), width=int(5 * u))
    for x, y in ((41, 42), (24, 30), (30, 12)):
        d.ellipse(((x - 3.5) * u, (y - 3.5) * u, (x + 3.5) * u, (y + 3.5) * u),
                  fill=(255, 255, 255, 255), outline=(40, 40, 40, 255), width=int(1.5 * u))
    return im.resize((n, n), Image.LANCZOS)


os.makedirs(OUT, exist_ok=True)
for n in (16, 32, 64):
    icon(n).save(os.path.join(OUT, '%dx%d.png' % (n, n)))
print('icons in %s' % OUT)
