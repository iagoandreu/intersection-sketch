"""Offline checks of the contour chaining and fit-point placement.

    py -3.10 tests/test_geom.py
"""

import math
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ix_geom as G                                            # noqa: E402

FAILED = []


def check(name, cond, detail=''):
    print('  %s %s%s' % ('ok  ' if cond else 'FAIL', name,
                         '   %s' % (detail,) if detail != '' else ''))
    if not cond:
        FAILED.append(name)


def circle(r, n, c=(0.0, 0.0, 0.0), a0=0.0, a1=2 * math.pi):
    return [(c[0] + r * math.cos(a0 + (a1 - a0) * k / n),
             c[1] + r * math.sin(a0 + (a1 - a0) * k / n), c[2]) for k in range(n + 1)]


def cut(pts, parts):
    """A curve broken into `parts` pieces, the way a contour comes as edges."""
    n = len(pts) - 1
    idx = sorted(set([0, n] + [int(n * k / parts) for k in range(1, parts)]))
    return [pts[idx[k]:idx[k + 1] + 1] for k in range(len(idx) - 1)]


print('chaining edges into contours')
rng = random.Random(4)
pieces = cut(circle(10.0, 400), 9) + cut(circle(3.0, 120, (30, 0, 0)), 4)
for k in range(len(pieces)):
    if rng.random() < 0.5:
        pieces[k] = pieces[k][::-1]                          # edges come either way
rng.shuffle(pieces)
cs = G.chain(pieces, 1e-6)
check('two contours, both closed', len(cs) == 2 and all(c['closed'] for c in cs),
      [(round(c['length'], 3), c['closed']) for c in cs])
check('longest first, at the right lengths',
      abs(cs[0]['length'] - 2 * math.pi * 10) < 0.01 and
      abs(cs[1]['length'] - 2 * math.pi * 3) < 0.01)
check('every piece used once', sum(c['pieces'] for c in cs) == len(pieces))
cs = G.chain(cut(circle(10.0, 400, a1=math.pi), 5), 1e-6)
check('an open arc stays open', len(cs) == 1 and not cs[0]['closed'])
# ends a hair apart (kernel tolerance) still join
pieces = cut(circle(10.0, 400), 6)
pieces = [[(p[0] + 2e-7, p[1], p[2]) if i == 0 else p for i, p in enumerate(q)] for q in pieces]
cs = G.chain(pieces, 1e-5)
check('ends 2e-7 apart join under a 1e-5 tolerance', len(cs) == 1 and cs[0]['closed'])
# two loops touching at one point: a figure eight stays two curves
eight = cut(circle(5.0, 200, (5, 0, 0), math.pi, 3 * math.pi), 3) + \
    cut(circle(5.0, 200, (-5, 0, 0), 0.0, 2 * math.pi), 3)
cs = G.chain(eight, 1e-6)
check('two loops touching at a point: two closed curves, not one walk',
      len(cs) == 2 and all(c['closed'] and abs(c['length'] - 2 * math.pi * 5) < 0.01
                           for c in cs),
      [(round(c['length'], 3), c['closed']) for c in cs])

# a 2 um kernel edge between two pieces (seen on a clinical test case) must not split the loop
pieces = cut(circle(10.0, 400), 6)
p0 = pieces[0][0]
pieces.append([p0, (p0[0] + 2e-7, p0[1], p0[2])])
cs = G.chain(pieces, 5e-6)
check('a speck edge shorter than the tolerance is dropped', len(cs) == 1 and cs[0]['closed'],
      [(round(c['length'], 6), c['closed']) for c in cs])

print('arc length')
arc = G.Arc(circle(10.0, 2000)[:-1], True, 0.5)
check('length of a closed polygon', abs(arc.L - 2 * math.pi * 10) < 1e-3, arc.L)
p = arc.at(arc.L / 4)
check('quarter way round', G.dist(p, (0, 10, 0)) < 1e-3, p)
check('at() wraps on a closed curve', G.dist(arc.at(arc.L + 1.0), arc.at(1.0)) < 1e-12)
check('distance from the centre is the radius', abs(arc.distance((0, 0, 0)) - 10) < 1e-3)
check('distance from a point on it is ~0', arc.distance(arc.at(3.3)) < 1e-9)
check('distance from far away', abs(arc.distance((0, 0, 50)) - math.hypot(50, 10)) < 1e-3)

print('fit points')
pos = G.seed_positions(100.0, 7.0, True, 6)
check('seeds: evenly spaced, at most the spacing', len(pos) == 15 and pos[1] - pos[0] <= 7.0)
dev = [0.0] * len(pos)
dev[3] = dev[14] = 1.0
new, k = G.insert_where(pos, dev, 0.5, 100.0, True)
check('a point added in each bad span, the wrap-round span included',
      k == 2 and len(new) == 17 and new == sorted(new) and
      any(abs(q - (pos[3] + pos[4]) / 2) < 1e-12 for q in new) and
      any(abs(q - ((pos[14] + 100.0) / 2) % 100.0) < 1e-12 for q in new), new)
check('span_of: inside, at a fit point, wrapped past the last',
      G.span_of(pos, 25.0, 100.0, True) == 3 and G.span_of(pos, pos[4], 100.0, True) == 4 and
      G.span_of(pos, 99.0, 100.0, True) == 14 and G.span_of(pos, 100.0, 100.0, True) == 0)
pos = G.seed_positions(10.0, 4.0, False, 2)
check('span_of on an open curve clamps to the last span',
      G.span_of(pos, 10.0, 10.0, False) == len(pos) - 2 and G.span_of(pos, 0.0, 10.0, False) == 0)
check('an open curve keeps both ends', pos[0] == 0.0 and pos[-1] == 10.0)

print()
if FAILED:
    print('%d FAILED: %s' % (len(FAILED), ', '.join(FAILED)))
    raise SystemExit(1)
print('all geometry checks passed')
