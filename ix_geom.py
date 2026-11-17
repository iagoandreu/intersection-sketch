"""Intersection Sketch - the geometry that needs no Fusion (stdlib only).

Points are (x, y, z) tuples in one unit (the add-in uses cm).
"""

import bisect
import math


def dist(a, b):
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)


def lerp(a, b, t):
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t)


def _key(p, q):
    return (int(round(p[0] / q)), int(round(p[1] / q)), int(round(p[2] / q)))


def chain(pieces, tol):
    """Join curve pieces (each a list of points, start to end) into contours.

    Ends closer than tol are the same vertex. A walk goes on through a vertex
    that has exactly one other piece; at a vertex where three or more pieces
    meet (two contours touching) it stops, so each walk stays one curve.
    Returns [{'pts': [...], 'closed': bool, 'pieces': n}], longest first.
    """
    ends = {}
    vid = {}

    def vertex(p):
        k = _key(p, tol)
        # neighbours too, so two ends straddling a rounding boundary still meet
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    kk = (k[0] + dx, k[1] + dy, k[2] + dz)
                    for v, pv in vid.get(kk, ()):
                        if dist(pv, p) <= tol:
                            return v
        v = len(ends)
        ends[v] = []
        vid.setdefault(k, []).append((v, p))
        return v

    rec = []
    for i, pts in enumerate(pieces):
        if len(pts) < 2:
            continue
        a, b = vertex(pts[0]), vertex(pts[-1])
        if a == b and length(pts, False) < 2 * tol:
            continue                    # a kernel speck: would make a 4-way vertex
        rec.append((a, b, pts))
        ends[a].append(len(rec) - 1)
        ends[b].append(len(rec) - 1)

    used = [False] * len(rec)
    out = []

    def walk(start_piece, from_v):
        a, b, pts = rec[start_piece]
        seq = list(pts) if a == from_v else list(reversed(pts))
        v = b if a == from_v else a
        used[start_piece] = True
        n = 1
        while True:
            nxt = [j for j in ends[v] if not used[j]]
            if len(ends[v]) != 2 or not nxt:
                break
            j = nxt[0]
            a2, b2, p2 = rec[j]
            more = list(p2) if a2 == v else list(reversed(p2))
            seq.extend(more[1:])
            v = b2 if a2 == v else a2
            used[j] = True
            n += 1
        return seq, v, n

    # open chains start at a vertex that is not a simple pass-through
    for v, js in ends.items():
        if len(js) == 2:
            continue
        for j in js:
            if not used[j]:
                seq, v_end, n = walk(j, v)
                # a loop through a touching point starts and ends there
                closed = v_end == v and len(seq) > 3
                out.append({'pts': seq[:-1] if closed else seq, 'closed': closed,
                            'pieces': n})
    for j in range(len(rec)):
        if not used[j]:
            seq, v, n = walk(j, rec[j][0])
            closed = v == rec[j][0]
            if closed:
                seq = seq[:-1]
            out.append({'pts': seq, 'closed': closed, 'pieces': n})
    for c in out:
        c['length'] = length(c['pts'], c['closed'])
    out.sort(key=lambda c: -c['length'])
    return out


def cumulative(pts, closed):
    seq = list(pts) + ([pts[0]] if closed else [])
    s = [0.0]
    for i in range(1, len(seq)):
        s.append(s[-1] + dist(seq[i - 1], seq[i]))
    return seq, s


def length(pts, closed):
    return cumulative(pts, closed)[1][-1]


class Arc:
    """A polyline by arc length: at(s) and distance from a point to it."""

    def __init__(self, pts, closed, cell):
        self.closed = closed
        self.seq, self.s = cumulative(pts, closed)
        self.L = self.s[-1]
        self.cell = cell
        self.grid = {}
        for i in range(len(self.seq) - 1):
            a, b = self.seq[i], self.seq[i + 1]
            n = max(1, int(math.ceil(dist(a, b) / cell)))
            for k in range(n + 1):
                q = lerp(a, b, k / float(n))
                self.grid.setdefault(self._cell(q), set()).add(i)

    def _cell(self, p):
        c = self.cell
        return (int(math.floor(p[0] / c)), int(math.floor(p[1] / c)), int(math.floor(p[2] / c)))

    def at(self, s):
        if self.closed:
            s %= self.L
        s = min(max(s, 0.0), self.L)
        lo, hi = 0, len(self.s) - 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if self.s[mid] <= s:
                lo = mid
            else:
                hi = mid
        seg = self.s[hi] - self.s[lo]
        t = (s - self.s[lo]) / seg if seg > 0 else 0.0
        return lerp(self.seq[lo], self.seq[hi], t)

    def distance(self, p):
        """Distance to the polyline, searching outward ring by ring."""
        k = self._cell(p)
        best = None
        for ring in range(0, 64):
            for dx in range(-ring, ring + 1):
                for dy in range(-ring, ring + 1):
                    for dz in range(-ring, ring + 1):
                        if max(abs(dx), abs(dy), abs(dz)) != ring:
                            continue
                        for i in self.grid.get((k[0] + dx, k[1] + dy, k[2] + dz), ()):
                            a, b = self.seq[i], self.seq[i + 1]
                            ab = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
                            L2 = ab[0] ** 2 + ab[1] ** 2 + ab[2] ** 2
                            t = 0.0 if L2 == 0 else max(0.0, min(1.0, (
                                (p[0] - a[0]) * ab[0] + (p[1] - a[1]) * ab[1]
                                + (p[2] - a[2]) * ab[2]) / L2))
                            d = dist(p, lerp(a, b, t))
                            if best is None or d < best:
                                best = d
            if best is not None and best <= ring * self.cell:
                return best
        # far from the curve (never the case for a fit check): every segment
        return min(dist(p, lerp(self.seq[i], self.seq[i + 1], max(0.0, min(1.0, (
            (p[0] - self.seq[i][0]) * (self.seq[i + 1][0] - self.seq[i][0])
            + (p[1] - self.seq[i][1]) * (self.seq[i + 1][1] - self.seq[i][1])
            + (p[2] - self.seq[i][2]) * (self.seq[i + 1][2] - self.seq[i][2]))
            / max(1e-300, dist(self.seq[i], self.seq[i + 1]) ** 2)))))
            for i in range(len(self.seq) - 1))


def seed_positions(L, spacing, closed, minimum):
    """Arc-length positions of the first fit points: evenly spaced, at least
    `minimum` of them (a closed spline needs a few to be a loop at all)."""
    n = max(minimum, int(math.ceil(L / spacing)))
    if closed:
        return [L * i / n for i in range(n)]
    return [L * i / n for i in range(n + 1)]


def span_of(positions, s, L, closed):
    """Index of the span (between fit points) that arc length s falls in."""
    if closed:
        s %= L
    i = bisect.bisect_right(positions, s) - 1
    if closed:
        return i % len(positions)
    return min(max(i, 0), len(positions) - 2)


def insert_where(positions, span_dev, tol, L, closed):
    """A new fit point at the arc-length middle of every span whose deviation
    is over tol. span i runs from positions[i] to positions[i + 1] (wrapping on
    a closed curve). Returns (new positions, how many were added)."""
    out, added = [], 0
    n = len(positions)
    spans = n if closed else n - 1
    for i in range(n):
        out.append(positions[i])
        if i < spans and span_dev[i] > tol:
            a = positions[i]
            b = positions[i + 1] if i + 1 < n else positions[0] + L
            out.append(0.5 * (a + b))
            added += 1
    if closed:
        out = sorted(p % L for p in out)
    return out, added
