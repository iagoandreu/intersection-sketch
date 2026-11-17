"""Intersection Sketch - all the working code. RE-IMPORTED ON EVERY COMMAND EVENT.

Because of that reload, nothing may keep state in a module global between events;
per-invocation state lives in the _ix_state module (state()).

WHAT IT DOES
    Two bodies that overlap meet along one or more closed curves - where the
    surface of one passes through the surface of the other. This finds those
    curves EXACTLY and writes each as an editable 3D sketch: one closed fitted
    spline per contour, whose fit points you drag.

HOW
    1. Copies of the two bodies are intersected in the TemporaryBRepManager
       (the modelling kernel's boolean; nothing enters the design).
    2. Every face of the intersection solid came either from body A (the part
       of A's skin inside B) or from body B. Each is put on its side by asking
       the design which of the two bodies has a face through its point.
    3. The contour is the edges between an A face and a B face, chained into
       curves (ix_geom.chain) - several curves where the bodies meet in
       several places.
    4. Each curve becomes a fitted spline: fit points every `spacing` along
       the exact curve to start with, then one more in the middle of every
       span where the spline strays from the exact curve by more than the
       tolerance, until none does. Few points where the curve is plain, more
       where it bends - and the report says how far the spline is from the
       true intersection.

UNITS
    Fusion internal is centimetres; the dialog and the reports are millimetres.
"""

import adsk.core
import adsk.fusion

import importlib
import json
import os
import sys
import time
import traceback
import types

import ix_geom

G = importlib.reload(ix_geom)           # the geometry hot-reloads too

ADDIN_DIR = os.path.dirname(os.path.realpath(__file__))
CM = 10.0
MM = 0.1

CMD_ID = 'IntersectionSketch_Create'
CMD_NAME = 'Intersection Sketch'
RES_DIR = os.path.join(ADDIN_DIR, 'resources', 'IntersectionSketch')
SETTINGS_PATH = os.path.join(ADDIN_DIR, 'last_settings.json')
LOG_PATH = os.path.join(ADDIN_DIR, 'IntersectionSketch.log')
VERSION = '1.0.0'

PANELS = ['SketchCreatePanel', 'SolidCreatePanel', 'SurfaceCreatePanel']

MAX_CONTOURS = 12
DEFAULTS = {'tol_mm': 0.05, 'spacing_mm': 5.0, 'exact': True}
COLOURS = [(255, 138, 0), (20, 170, 255), (60, 200, 120), (230, 60, 160),
           (250, 210, 40), (150, 110, 255)]
ON_FACE_CM = 0.001          # a face of the intersection lies on A or B to this
CHAIN_TOL_CM = 0.0005       # contour edge ends this close are one vertex
STROKE_CM = 0.0005          # exact curves sampled to 5 microns
MAX_POINTS = 600            # fit points per contour, at most
MAX_ROUNDS = 8


def log(msg):
    try:
        with open(LOG_PATH, 'a', encoding='utf-8') as fh:
            fh.write('%s  %s\n' % (time.strftime('%H:%M:%S'), msg))
    except Exception:
        pass


def short_error(tb):
    for line in reversed(tb.strip().splitlines()):
        line = line.strip()
        if line and not line.startswith(('File "', 'Traceback', '^', '~')):
            return line
    return tb.strip().splitlines()[-1]


def _esc(t):
    return str(t).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def state():
    st = sys.modules.get('_ix_state')
    if st is None:
        st = types.ModuleType('_ix_state')
        st.found = None
        st.handlers = []
        st.last_report = None
        sys.modules['_ix_state'] = st
    return st


def load_settings():
    o = dict(DEFAULTS)
    try:
        with open(SETTINGS_PATH, encoding='utf-8') as fh:
            o.update({k: v for k, v in json.load(fh).items() if k in DEFAULTS})
    except Exception:
        pass
    return o


def save_settings(o):
    try:
        with open(SETTINGS_PATH, 'w', encoding='utf-8') as fh:
            json.dump({k: o[k] for k in DEFAULTS}, fh, indent=1)
    except Exception:
        pass


def _tup(p):
    return (p.x, p.y, p.z)


def _pt(a):
    return adsk.core.Point3D.create(a[0], a[1], a[2])


def body_key(b):
    try:
        return b.entityToken
    except Exception:
        return '%s|%s' % (b.name, id(b))


# --------------------------------------------------------------------------- #
# the intersection
# --------------------------------------------------------------------------- #

def _side_of(root, face, keys):
    """Which of the two bodies a face of the intersection solid came from:
    'A', 'B', or None (it lies on both - coincident skins - or on neither)."""
    hits = set()
    for q in _face_points(face):
        found = root.findBRepUsingPoint(_pt(q), adsk.fusion.BRepEntityTypes.BRepFaceEntityType,
                                        ON_FACE_CM, False)
        for k in range(found.count):
            f = adsk.fusion.BRepFace.cast(found.item(k))
            if f is None:
                continue
            bk = body_key(f.body)
            if bk == keys['A']:
                hits.add('A')
            elif bk == keys['B']:
                hits.add('B')
        if len(hits) == 1:
            return hits.pop()
        hits.clear()
    return None


def _face_points(face):
    """pointOnFace, then a few more if that one is ambiguous (on an edge)."""
    yield _tup(face.pointOnFace)
    ev = face.evaluator
    rng = ev.parametricRange()
    for fu, fv in ((0.31, 0.43), (0.67, 0.29), (0.52, 0.71)):
        uv = adsk.core.Point2D.create(rng.minPoint.x + fu * (rng.maxPoint.x - rng.minPoint.x),
                                      rng.minPoint.y + fv * (rng.maxPoint.y - rng.minPoint.y))
        if ev.isParameterOnFace(uv):
            ok, p = ev.getPointAtParameter(uv)
            if ok:
                yield _tup(p)


def _edge_points(edge):
    ev = edge.evaluator
    ok, p0, p1 = ev.getParameterExtents()
    ok, pts = ev.getStrokes(p0, p1, STROKE_CM)
    pts = [_tup(p) for p in pts]
    sv = _tup(edge.startVertex.geometry)
    if pts and G.dist(pts[-1], sv) < G.dist(pts[0], sv):
        pts.reverse()
    return pts


def find_contours(app, body_a, body_b):
    """The curves where the two bodies' surfaces meet, in world cm.
    Returns {'contours': [...], 'info': {...}}; raises ValueError for the dialog."""
    t0 = time.time()
    for b in (body_a, body_b):
        if not adsk.fusion.BRepBody.cast(b):
            raise ValueError('%s is a mesh body; convert it first (Mesh to Part)' % b.name)
        if not b.isSolid:
            raise ValueError('%s is not a closed solid' % b.name)
    if body_key(body_a) == body_key(body_b):
        raise ValueError('pick two different bodies')
    tbm = adsk.fusion.TemporaryBRepManager.get()
    inter = tbm.copy(body_a)
    tool = tbm.copy(body_b)
    try:
        ok = tbm.booleanOperation(inter, tool, adsk.fusion.BooleanTypes.IntersectionBooleanType)
    except Exception:
        raise ValueError('the modelling kernel could not intersect the two bodies: %s'
                         % short_error(traceback.format_exc()))
    if not ok or inter is None or inter.faces.count == 0:
        raise ValueError('the bodies do not overlap')
    t_bool = time.time() - t0
    root = adsk.fusion.Design.cast(app.activeProduct).rootComponent
    keys = {'A': body_key(body_a), 'B': body_key(body_b)}
    side = {}
    n_side = {'A': 0, 'B': 0, None: 0}
    faces = list(inter.faces)
    for i, f in enumerate(faces):
        s = _side_of(root, f, keys)
        side[i] = s
        n_side[s] += 1
    index = {}
    for i, f in enumerate(faces):
        index[_tup(f.pointOnFace)] = i
    # a face on both skins (a sliver where they are tangent) or on neither joins
    # its neighbours' side, or A if they are mixed; otherwise the contour breaks
    # there - one of its edges borders A, the other B, and neither counts
    for i, f in enumerate(faces):
        if side[i] is None:
            near = set(side.get(index.get(_tup(g.pointOnFace))) for e in f.edges
                       for g in e.faces if g != f) - {None}
            side[i] = near.pop() if len(near) == 1 else 'A'
    pieces = []
    for e in inter.edges:
        fs = [index.get(_tup(f.pointOnFace)) for f in e.faces]
        if len(fs) != 2 or None in fs:
            continue
        sa, sb = side[fs[0]], side[fs[1]]
        if sa and sb and sa != sb:
            pieces.append(_edge_points(e))
    if not pieces:
        raise ValueError('the bodies overlap but no edge separates a face of one from '
                         'a face of the other (faces A %d, B %d, unclear %d)'
                         % (n_side['A'], n_side['B'], n_side[None]))
    contours = G.chain(pieces, CHAIN_TOL_CM)
    for c in contours:
        n = len(c['pts'])
        c['centre'] = tuple(sum(p[k] for p in c['pts']) / n for k in range(3))
    info = {'intersection_faces': len(faces), 'from_a': n_side['A'], 'from_b': n_side['B'],
            'unclear_faces': n_side[None], 'edges': len(pieces),
            'boolean_s': round(t_bool, 2), 'secs': round(time.time() - t0, 2)}
    return {'contours': contours, 'info': info, 'a': body_a.name, 'b': body_b.name}


# --------------------------------------------------------------------------- #
# the editable spline
# --------------------------------------------------------------------------- #

def _spline(sk, pts, closed):
    coll = adsk.core.ObjectCollection.create()
    for p in pts:
        coll.add(sk.modelToSketchSpace(_pt(p)))
    spl = sk.sketchCurves.sketchFittedSplines.add(coll)
    if closed:
        spl.isClosed = True
    return spl


def _span_deviation(spl, fit_world, closed, arc, k=8):
    """Largest distance from the exact curve, per span between fit points."""
    ev = spl.worldGeometry.evaluator
    ok, t0, t1 = ev.getParameterExtents()
    ts = []
    for p in fit_world:
        ok, t = ev.getParameterAtPoint(_pt(p))
        ts.append(t if ok else None)
    n = len(fit_world)
    spans = n if closed else n - 1
    params, owner = [], []
    for i in range(spans):
        a = ts[i]
        b = ts[i + 1] if i + 1 < n else t1
        if a is None or b is None:
            continue
        if b < a:                         # the wrap-round span of a closed spline
            b += (t1 - t0)
        for j in range(1, k):
            t = a + (b - a) * j / float(k)
            if t > t1:
                t -= (t1 - t0)
            params.append(t)
            owner.append(i)
    dev = [0.0] * spans
    if params:
        ok, pts = ev.getPointsAtParameters(params)
        for i, p in zip(owner, pts):
            dev[i] = max(dev[i], arc.distance(_tup(p)))
    return dev


def _exact_deviation(spl, pos, closed, arc, dev):
    """The other direction: how far the exact curve strays from the spline, per
    span. A spline can stay near the curve yet cut a corner of it; only this
    sees that."""
    ev = spl.worldGeometry.evaluator
    ok, t0, t1 = ev.getParameterExtents()
    ok, pts = ev.getStrokes(t0, t1, STROKE_CM)
    back = G.Arc([_tup(p) for p in pts], closed, arc.cell)
    step = max(1, len(arc.seq) // 4000)
    for k in range(0, len(arc.seq), step):
        i = G.span_of(pos, arc.s[k], arc.L, closed)
        dev[i] = max(dev[i], back.distance(arc.seq[k]))
    return dev


def fit_contour(sk, c, tol, spacing):
    """One contour as a fitted spline within tol of the exact curve.
    Returns the spline and its numbers."""
    pts, closed = c['pts'], c['closed']
    arc = G.Arc(pts, closed, max(0.02, spacing / 4.0))
    pos = G.seed_positions(arc.L, spacing, closed, 6 if closed else 2)
    spl = None
    rounds = 0
    while True:
        rounds += 1
        fit = [arc.at(s) for s in pos]
        if spl is not None:
            spl.deleteMe()
        spl = _spline(sk, fit, closed)
        dev = _exact_deviation(spl, pos, closed, arc, _span_deviation(spl, fit, closed, arc))
        worst = max(dev) if dev else 0.0
        if worst <= tol or rounds >= MAX_ROUNDS or len(pos) >= MAX_POINTS:
            break
        pos, added = G.insert_where(pos, dev, tol, arc.L, closed)
        if not added:
            break
    return spl, {'points': len(pos), 'rounds': rounds, 'max_dev_mm': round(worst * CM, 4),
                 'length_mm': round(arc.L * CM, 2), 'closed': closed}


def create_sketches(app, found, which, tol_mm, spacing_mm, exact):
    """A 3D sketch per chosen contour, in the root component. Returns a report."""
    design = adsk.fusion.Design.cast(app.activeProduct)
    root = design.rootComponent
    out = []
    t0 = time.time()
    for i in which:
        c = found['contours'][i]
        sk = root.sketches.add(root.xYConstructionPlane)
        sk.name = 'Intersection %d - %s x %s' % (i + 1, found['a'], found['b'])
        sk.isComputeDeferred = True
        try:
            if exact:
                _reference(sk, c)
            spl, rep = fit_contour(sk, c, tol_mm * MM, spacing_mm * MM)
        finally:
            sk.isComputeDeferred = False
        rep['sketch'] = sk.name
        out.append(rep)
    return {'sketches': out, 'tol_mm': tol_mm, 'spacing_mm': spacing_mm,
            'secs': round(time.time() - t0, 2)}


def _reference(sk, c, n_max=400):
    """The exact curve as fixed construction lines through its strokes - the
    true intersection to edit the spline against, which cannot be dragged by
    accident."""
    pts = c['pts'][::max(1, len(c['pts']) // n_max)]
    if c['closed']:
        pts = pts + [pts[0]]
    lines = sk.sketchCurves.sketchLines
    prev = None
    for p in pts:
        q = sk.modelToSketchSpace(_pt(p))
        if prev is None:
            prev = q
            continue
        a = prev if isinstance(prev, adsk.core.Point3D) else prev.geometry
        if a.distanceTo(q) < 1e-6:
            continue
        ln = lines.addByTwoPoints(prev, q)
        ln.isConstruction = True
        ln.isFixed = True
        prev = ln.endSketchPoint
    return len(pts)


def format_report(found, made):
    L = ['Intersection of %s and %s' % (found['a'], found['b'])]
    for r in made['sketches']:
        L.append('  %s: %s, %.1f mm, %d fit points, spline within %.4f mm of the exact '
                 'curve' % (r['sketch'], 'closed' if r['closed'] else 'open',
                            r['length_mm'], r['points'], r['max_dev_mm']))
    L.append('  tolerance %.3f mm, start spacing %.1f mm, %.1f s'
             % (made['tol_mm'], made['spacing_mm'], made['secs']))
    return '\n'.join(L)


# --------------------------------------------------------------------------- #
# preview
# --------------------------------------------------------------------------- #

def clear_graphics(app):
    try:
        d = adsk.fusion.Design.cast(app.activeProduct)
        cg = d.rootComponent.customGraphicsGroups
        while cg.count:
            cg.item(0).deleteMe()
    except Exception:
        pass


def draw(app, found, chosen):
    """Every contour in its own colour; the ones not chosen thin."""
    clear_graphics(app)
    if not found:
        return
    d = adsk.fusion.Design.cast(app.activeProduct)
    g = d.rootComponent.customGraphicsGroups.add()
    for i, c in enumerate(found['contours'][:MAX_CONTOURS]):
        pts = c['pts']
        coords = []
        for p in pts:
            coords.extend(p)
        idx = []
        for k in range(len(pts) - 1):
            idx.extend((k, k + 1))
        if c['closed']:
            idx.extend((len(pts) - 1, 0))
        e = g.addLines(adsk.fusion.CustomGraphicsCoordinates.create(coords), idx, False)
        rgb = COLOURS[i % len(COLOURS)]
        e.color = adsk.fusion.CustomGraphicsSolidColorEffect.create(
            adsk.core.Color.create(rgb[0], rgb[1], rgb[2], 255))
        e.depthPriority = 2
        e.weight = 4.0 if i in chosen else 1.0
    try:
        app.activeViewport.refresh()
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# the command
# --------------------------------------------------------------------------- #

def _sel(inputs, cid):
    s = inputs.itemById(cid)
    return s.selection(0).entity if s and s.selectionCount else None


def chosen_from(inputs):
    st = state()
    if not st.found:
        return []
    out = []
    for i in range(min(len(st.found['contours']), MAX_CONTOURS)):
        cb = inputs.itemById('c%d' % i)
        if cb and cb.value:
            out.append(i)
    return out


def read_opts(inputs):
    return {'tol_mm': inputs.itemById('tol').value * CM,
            'spacing_mm': inputs.itemById('spacing').value * CM,
            'exact': inputs.itemById('exact').value}


def on_created(app, cmd):
    st = state()
    st.found = None
    st.error = None
    o = load_settings()
    inputs = cmd.commandInputs
    cmd.setDialogInitialSize(400, 420)
    cmd.okButtonText = 'Create sketch'
    for cid, label in (('a', 'Body A'), ('b', 'Body B')):
        s = inputs.addSelectionInput(cid, label, 'A solid body')
        s.addSelectionFilter('SolidBodies')
        s.setSelectionLimits(1, 1)
    inputs.addTextBoxCommandInput('info', '', 'Select the two bodies.', 4, True)
    g = inputs.addGroupCommandInput('gC', 'Contours')
    g.isExpanded = True
    for i in range(MAX_CONTOURS):
        cb = g.children.addBoolValueInput('c%d' % i, 'Contour %d' % (i + 1), True, '', True)
        cb.isVisible = False
    g = inputs.addGroupCommandInput('gF', 'Spline')
    g.isExpanded = True
    t = g.children.addValueInput('tol', 'Tolerance', 'mm',
                                 adsk.core.ValueInput.createByReal(o['tol_mm'] * MM))
    t.tooltip = ('Fit points are added until the spline is this close to the exact '
                 'intersection everywhere. Smaller: truer, more points to edit.')
    sp = g.children.addValueInput('spacing', 'Start spacing', 'mm',
                                  adsk.core.ValueInput.createByReal(o['spacing_mm'] * MM))
    sp.tooltip = 'Fit points start this far apart; more go in only where needed.'
    ex = g.children.addBoolValueInput('exact', 'Exact curve as construction', True, '',
                                      o['exact'])
    ex.tooltip = ('Also draws the true intersection in the sketch, fixed and as '
                  'construction, to edit the spline against.')
    st.command = cmd


def _refresh(app, inputs):
    """Recompute when both bodies are picked; fill the contour list."""
    st = state()
    a, b = _sel(inputs, 'a'), _sel(inputs, 'b')
    st.found = None
    st.error = None
    for i in range(MAX_CONTOURS):
        inputs.itemById('c%d' % i).isVisible = False
    box = inputs.itemById('info')
    if a is None or b is None:
        box.formattedText = 'Select the two bodies.'
        clear_graphics(app)
        return
    try:
        st.found = find_contours(app, a, b)
    except ValueError as exc:
        st.error = str(exc)
    except Exception:
        st.error = short_error(traceback.format_exc())
        log(traceback.format_exc())
    if st.error:
        box.formattedText = '<b>%s</b>' % _esc(st.error)
        clear_graphics(app)
        return
    cs = st.found['contours']
    for i, c in enumerate(cs[:MAX_CONTOURS]):
        cb = inputs.itemById('c%d' % i)
        cb.isVisible = True
        cb.value = True
        cb.text = '%s, %.1f mm%s' % (_colour_name(i), c['length'] * CM,
                                     '' if c['closed'] else ', open')
    more = len(cs) - MAX_CONTOURS
    box.formattedText = ('<b>%d contour%s</b> where the two bodies meet (%.1f s).%s<br/>'
                         'Untick any you do not want; each ticked one becomes its own '
                         'sketch.' % (len(cs), '' if len(cs) == 1 else 's',
                                      st.found['info']['secs'],
                                      ' Showing the %d longest.' % MAX_CONTOURS if more > 0 else ''))
    draw(app, st.found, chosen_from(inputs))


def _colour_name(i):
    return ['Orange', 'Blue', 'Green', 'Pink', 'Yellow', 'Violet'][i % len(COLOURS)]


def on_input_changed(app, inputs, changed_id):
    if changed_id in ('a', 'b'):
        _refresh(app, inputs)
    else:
        draw(app, state().found, chosen_from(inputs))


def on_validate(inputs):
    st = state()
    return bool(st.found) and bool(chosen_from(inputs))


def on_execute(app, ui, inputs):
    st = state()
    o = read_opts(inputs)
    save_settings(o)
    clear_graphics(app)
    if not st.found:
        return
    try:
        made = create_sketches(app, st.found, chosen_from(inputs), o['tol_mm'],
                               o['spacing_mm'], o['exact'])
        text = format_report(st.found, made)
        st.last_report = made
    except Exception:
        tb = traceback.format_exc()
        log(tb)
        text = 'Intersection Sketch failed: %s' % short_error(tb)
        st.last_report = {'error': text}
        ui.messageBox(text, CMD_NAME)
    log(text)
    try:
        adsk.core.Application.log(text)
    except Exception:
        pass


def on_destroy(app):
    clear_graphics(app)
    st = state()
    st.found = None
    st.command = None


# --------------------------------------------------------------------------- #
# installation
# --------------------------------------------------------------------------- #

TOOLTIP = ('Sketch the curves where two bodies meet - exactly, as editable 3D splines. '
           'Several contours can be picked or all created, one sketch each.')


def install(app, ui, created_handler_factory):
    old = ui.commandDefinitions.itemById(CMD_ID)
    if old:
        old.deleteMe()
    res = RES_DIR if os.path.isdir(RES_DIR) else ''
    cd = ui.commandDefinitions.addButtonDefinition(CMD_ID, CMD_NAME, TOOLTIP, res)
    cd.commandCreated.add(created_handler_factory())
    placed = []
    for pid in PANELS:
        panel = ui.allToolbarPanels.itemById(pid)
        if panel:
            panel.controls.addCommand(cd)
            placed.append(pid)
    return ', '.join(placed)


def uninstall(ui):
    cd = ui.commandDefinitions.itemById(CMD_ID)
    if cd:
        cd.deleteMe()
    for pid in PANELS:
        panel = ui.allToolbarPanels.itemById(pid)
        if panel:
            c = panel.controls.itemById(CMD_ID)
            if c:
                c.deleteMe()


# --------------------------------------------------------------------------- #
# dev bridge
# --------------------------------------------------------------------------- #

def _bodies(app):
    d = adsk.fusion.Design.cast(app.activeProduct)
    out = []
    for b in d.rootComponent.bRepBodies:
        out.append(b)
    for occ in d.rootComponent.allOccurrences:
        for b in occ.bRepBodies:
            out.append(b)
    return out


def _named(app, name):
    for b in _bodies(app):
        if b.name == name:
            return b
    raise ValueError('no body %r' % name)


def _contour_json(found):
    return [{'length_mm': round(c['length'] * CM, 3), 'closed': c['closed'],
             'pieces': c['pieces'], 'points': len(c['pts']),
             'centre_mm': [round(v * CM, 2) for v in c['centre']]}
            for c in found['contours']]


def dev_ping(app, job):
    return {'version': VERSION, 'document': app.activeDocument.name}


def dev_contours(app, job):
    """Read only: what the dialog would list for two bodies (by name)."""
    found = find_contours(app, _named(app, job['a']), _named(app, job['b']))
    return {'info': found['info'], 'contours': _contour_json(found)}


def dev_diag(app, job):
    """Read only: why contours come out open - edge side pairs, unclear faces,
    open ends and what meets there."""
    a, b = _named(app, job['a']), _named(app, job['b'])
    tbm = adsk.fusion.TemporaryBRepManager.get()
    inter = tbm.copy(a)
    tbm.booleanOperation(inter, tbm.copy(b), adsk.fusion.BooleanTypes.IntersectionBooleanType)
    root = adsk.fusion.Design.cast(app.activeProduct).rootComponent
    keys = {'A': body_key(a), 'B': body_key(b)}
    faces = list(inter.faces)
    side = [_side_of(root, f, keys) for f in faces]
    index = {}
    collide = 0
    for i, f in enumerate(faces):
        k = _tup(f.pointOnFace)
        collide += k in index
        index[k] = i
    pairs = {}
    for e in inter.edges:
        fs = [index.get(_tup(f.pointOnFace)) for f in e.faces]
        key = '+'.join(sorted(str(side[i]) if i is not None else 'noindex' for i in fs))
        pairs[key] = pairs.get(key, 0) + 1
    unclear = []
    for i, f in enumerate(faces):
        if side[i] is not None:
            continue
        hits = []
        for q in _face_points(f):
            found = root.findBRepUsingPoint(_pt(q), adsk.fusion.BRepEntityTypes.BRepFaceEntityType,
                                            ON_FACE_CM, False)
            hits.append(sorted(set(adsk.fusion.BRepFace.cast(found.item(k)).body.name
                                   for k in range(found.count))))
        unclear.append({'area_mm2': round(f.area * 100, 4), 'edges': f.edges.count,
                        'centre_mm': [round(v * CM, 2) for v in _tup(f.pointOnFace)],
                        'hits': hits, 'neighbour_sides': sorted(
                            str(side[index.get(_tup(g.pointOnFace))]) for e in f.edges
                            for g in e.faces if g != f)})
    found = find_contours(app, a, b)
    ends = []
    for ci, c in enumerate(found['contours']):
        if not c['closed']:
            for p in (c['pts'][0], c['pts'][-1]):
                ends.append((ci, p))
    gaps = []
    for i, (ci, p) in enumerate(ends):
        best = min(((G.dist(p, q) * CM, cj) for j, (cj, q) in enumerate(ends) if j != i),
                   default=(None, None))
        gaps.append({'contour': ci, 'at_mm': [round(v * CM, 3) for v in p],
                     'nearest_end_mm': round(best[0], 4) if best[0] is not None else None,
                     'of_contour': best[1]})
    return {'pointOnFace_collisions': collide, 'edge_side_pairs': pairs,
            'unclear': unclear, 'open_ends': gaps}


def dev_create(app, job):
    """Create the sketches for two bodies (by name). which: indices or all."""
    found = find_contours(app, _named(app, job['a']), _named(app, job['b']))
    which = job.get('which') or list(range(len(found['contours'])))
    made = create_sketches(app, found, which, float(job.get('tol_mm', DEFAULTS['tol_mm'])),
                           float(job.get('spacing_mm', DEFAULTS['spacing_mm'])),
                           bool(job.get('exact', DEFAULTS['exact'])))
    made['text'] = format_report(found, made)
    made['info'] = found['info']
    return made


def dev_delete_sketches(app, job):
    """Remove sketches this tool made (names starting 'Intersection ')."""
    d = adsk.fusion.Design.cast(app.activeProduct)
    gone = []
    for sk in list(d.rootComponent.sketches):
        if sk.name.startswith('Intersection ') and (not job.get('names') or sk.name in job['names']):
            gone.append(sk.name)
            sk.deleteMe()
    return {'deleted': gone}


def dev_verify(app, job):
    """Read only: two-way distance between a sketch's fitted spline and the
    exact contour (index `contour`), from the spline's own strokes."""
    found = find_contours(app, _named(app, job['a']), _named(app, job['b']))
    c = found['contours'][int(job.get('contour', 0))]
    arc = G.Arc(c['pts'], c['closed'], 0.05)
    d = adsk.fusion.Design.cast(app.activeProduct)
    sk = d.rootComponent.sketches.itemByName(job['name'])
    spl = [s for s in sk.sketchCurves.sketchFittedSplines if not s.isConstruction][0]
    ev = spl.worldGeometry.evaluator
    ok, p0, p1 = ev.getParameterExtents()
    ok, pts = ev.getStrokes(p0, p1, 0.0002)
    pts = [_tup(p) for p in pts]
    there = [arc.distance(p) for p in pts]
    back = G.Arc(pts, c['closed'], 0.05)
    step = max(1, len(c['pts']) // 3000)
    here = [back.distance(p) for p in c['pts'][::step]]
    there.sort()
    return {'spline_to_exact_max_mm': round(there[-1] * CM, 4),
            'spline_to_exact_p95_mm': round(there[int(0.95 * len(there))] * CM, 4),
            'exact_to_spline_max_mm': round(max(here) * CM, 4),
            'samples': [len(pts), len(here)],
            'spline_length_mm': round(G.length(pts, c['closed']) * CM, 3),
            'exact_length_mm': round(c['length'] * CM, 3)}


def dev_survey(app, job):
    """Read only: timeline / bodies / params / STL export, from ix_survey.py."""
    if ADDIN_DIR not in sys.path:
        sys.path.insert(0, ADDIN_DIR)
    import ix_survey
    return importlib.reload(ix_survey).run(app, job)


def dev_sketches(app, job):
    """Names, timeline index and visibility of this tool's sketches."""
    d = adsk.fusion.Design.cast(app.activeProduct)
    return [{'name': sk.name, 'timeline_index': sk.timelineObject.index if sk.timelineObject else None,
             'visible': sk.isVisible}
            for sk in d.rootComponent.sketches if sk.name.startswith('Intersection ')]


def dev_sketch_info(app, job):
    """Fit points and size of a sketch's fitted splines."""
    d = adsk.fusion.Design.cast(app.activeProduct)
    sk = d.rootComponent.sketches.itemByName(job['name'])
    out = []
    for spl in sk.sketchCurves.sketchFittedSplines:
        out.append({'fit_points': spl.fitPoints.count, 'closed': spl.isClosed,
                    'construction': spl.isConstruction,
                    'length_mm': round(spl.length * CM, 3)})
    return {'splines': out, 'lines': sk.sketchCurves.sketchLines.count,
            'timeline_index': sk.timelineObject.index if sk.timelineObject else None}


def dev_capture(app, job):
    vp = app.activeViewport
    path = job.get('path') or os.path.join(ADDIN_DIR, 'capture.png')
    adsk.doEvents()
    vp.refresh()
    return {'saved': bool(vp.saveAsImageFile(path, int(job.get('width', 1100)),
                                             int(job.get('height', 800)))), 'path': path}


def dev_start_addin(app, job):
    """Start (or restart) another add-in installed next to this one, as the Run
    button of Scripts and Add-Ins does: import its shell and call run(). Used to
    bring up a newly installed add-in without restarting Fusion. No document is
    touched."""
    import importlib
    name = job['name']
    folder = os.path.join(os.path.dirname(ADDIN_DIR), name)
    if not os.path.exists(os.path.join(folder, name + '.py')):
        return {'error': 'no add-in at %s' % folder}
    if folder not in sys.path:
        sys.path.insert(0, folder)
    mod = sys.modules.get(name)
    if mod is not None:
        try:
            mod.stop({})
        except Exception:
            pass
        mod = importlib.reload(mod)
    else:
        mod = importlib.import_module(name)
    mod.run({})
    return {'started': name, 'file': mod.__file__}


DEV_COMMANDS = {
    'ping': dev_ping,
    'contours': dev_contours,
    'diag': dev_diag,
    'create': dev_create,
    'delete_sketches': dev_delete_sketches,
    'sketch_info': dev_sketch_info,
    'sketches': dev_sketches,
    'verify': dev_verify,
    'survey': dev_survey,
    'capture': dev_capture,
    'start_addin': dev_start_addin,
}
