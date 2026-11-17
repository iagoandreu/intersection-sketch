"""Read-only survey of the active design: timeline, bodies, parameters, STL export.

Dev tool for analysing how a design was built. Nothing here changes the design.
"""

import adsk.core
import adsk.fusion

import json
import os
import traceback

CM = 10.0

ATTRS = ['operation', 'targetBody', 'toolBodies', 'isKeepToolBodies', 'bodies', 'thickness',
         'isSymmetric', 'distance', 'extentOne', 'extentTwo', 'extentType', 'taperAngleOne',
         'participantBodies', 'profile', 'sections', 'isSolid', 'faces', 'inputEntities',
         'splittingTool', 'splitType', 'isSplittingToolExtended', 'offsetDistance', 'radius',
         'edgeSets', 'meshBodies', 'sourceBodies', 'scaleFactor', 'transform', 'moveType',
         'entities', 'planeCount', 'quantityOne', 'mirrorPlane', 'boundaryCurves', 'trimTool',
         'facesToOffset', 'facesToRemove', 'chainedCurveOptions', 'continuity',
         'tangentEdges', 'healthState', 'name']


def _short(v, depth=0):
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    try:
        if isinstance(v, (list, tuple)):
            return [_short(x, depth + 1) for x in v[:8]] + (['... %d' % len(v)] if len(v) > 8 else [])
    except Exception:
        pass
    ot = getattr(v, 'objectType', '?').split('::')[-1]
    try:
        if ot == 'ModelParameter' or ot == 'UserParameter':
            return {'param': v.name, 'expr': v.expression, 'value': v.value}
    except Exception:
        pass
    for nm in ('expression',):
        try:
            return {'type': ot, nm: getattr(v, nm)}
        except Exception:
            pass
    try:
        n = v.count
        if depth < 1:
            items = []
            for i in range(min(n, 8)):
                items.append(_short(v.item(i), depth + 1))
            return {'type': ot, 'count': n, 'items': items}
        return {'type': ot, 'count': n}
    except Exception:
        pass
    out = {'type': ot}
    for nm in ('name', 'value'):
        try:
            out[nm] = getattr(v, nm)
        except Exception:
            pass
    if ot in ('DistanceExtentDefinition', 'ThroughAllExtentDefinition', 'ToEntityExtentDefinition'):
        try:
            out['distance'] = _short(v.distance, depth + 1)
        except Exception:
            pass
    if ot == 'BRepBody':
        try:
            out['faces'] = v.faces.count
        except Exception:
            pass
    return out


def _fillet(ent):
    sets = []
    try:
        for i in range(ent.edgeSets.count):
            es = ent.edgeSets.item(i)
            d = {'type': es.objectType.split('::')[-1]}
            for nm in ('radius', 'startRadius', 'endRadius', 'distance', 'isTangentChain'):
                try:
                    d[nm] = _short(getattr(es, nm))
                except Exception:
                    pass
            try:
                d['edges'] = es.edges.count
            except Exception:
                try:
                    d['edges'] = es.entities.count
                except Exception:
                    pass
            sets.append(d)
    except Exception:
        sets.append('edgeSets unreadable')
    return sets


def _sketch(sk):
    c = sk.sketchCurves
    d = {'plane': None, 'curves': c.count}
    try:
        d['plane'] = sk.referencePlane.name if sk.referencePlane else None
    except Exception:
        pass
    for nm in ('sketchLines', 'sketchArcs', 'sketchCircles', 'sketchFittedSplines',
               'sketchControlPointSplines', 'sketchEllipses', 'sketchConicCurves'):
        try:
            n = getattr(c, nm).count
            if n:
                d[nm] = n
        except Exception:
            pass
    try:
        d['profiles'] = sk.profiles.count
    except Exception:
        pass
    try:
        d['texts'] = sk.sketchTexts.count
    except Exception:
        pass
    try:
        d['dimensions'] = sk.sketchDimensions.count
        d['fully_constrained'] = sk.isFullyConstrained
    except Exception:
        pass
    try:
        d['visible'] = sk.isVisible
    except Exception:
        pass
    try:
        d['projected_or_included'] = sum(1 for k in range(c.count) if c.item(k).isReference)
    except Exception:
        pass
    return d


def timeline(app, job):
    d = adsk.fusion.Design.cast(app.activeProduct)
    tl = d.timeline
    out = []
    for i in range(tl.count):
        it = tl.item(i)
        r = {'i': i, 'name': it.name}
        for nm in ('isSuppressed', 'isRolledBack', 'isGroup'):
            try:
                r[nm] = getattr(it, nm)
            except Exception:
                pass
        try:
            r['health'] = int(it.healthState)
            if it.healthState != 0:
                r['msg'] = it.errorOrWarningMessage
        except Exception:
            pass
        try:
            g = it.parentGroup
            r['group'] = g.name if g else None
        except Exception:
            pass
        ent = None
        try:
            ent = it.entity
        except Exception as e:
            r['entity_error'] = str(e)[:120]
        if ent is not None:
            ot = ent.objectType.split('::')[-1]
            r['type'] = ot
            if ot == 'Sketch':
                r['sketch'] = _sketch(ent)
            elif ot == 'FilletFeature':
                r['fillet'] = _fillet(ent)
            elif ot == 'Occurrence':
                try:
                    r['component'] = ent.component.name
                except Exception:
                    pass
            else:
                for nm in ATTRS:
                    if nm in ('healthState', 'name'):
                        continue
                    try:
                        v = getattr(ent, nm)
                    except Exception:
                        continue
                    if callable(v):
                        continue
                    r[nm] = _short(v)
            try:
                r['component'] = ent.parentComponent.name
            except Exception:
                pass
        out.append(r)
    return {'count': tl.count, 'marker': tl.markerPosition, 'items': out}


def _bbox(bb):
    return [[round(bb.minPoint.x * CM, 2), round(bb.minPoint.y * CM, 2), round(bb.minPoint.z * CM, 2)],
            [round(bb.maxPoint.x * CM, 2), round(bb.maxPoint.y * CM, 2), round(bb.maxPoint.z * CM, 2)]]


def bodies(app, job):
    d = adsk.fusion.Design.cast(app.activeProduct)
    root = d.rootComponent
    comps = [(root, 'root')]
    for occ in root.allOccurrences:
        comps.append((occ.component, occ.fullPathName))
    seen = set()
    out = []
    for comp, path in comps:
        if comp.name in seen and comp is not root:
            continue
        seen.add(comp.name)
        for b in comp.bRepBodies:
            r = {'comp': path, 'name': b.name, 'kind': 'brep', 'visible': b.isVisible,
                 'solid': b.isSolid, 'faces': b.faces.count, 'edges': b.edges.count}
            try:
                r['volume_mm3'] = round(b.volume * 1000, 1) if b.isSolid else None
                r['area_mm2'] = round(b.area * 100, 1)
                r['bbox_mm'] = _bbox(b.boundingBox)
            except Exception:
                pass
            try:
                r['tspline_faces'] = sum(1 for f in b.faces
                                         if f.geometry.surfaceType == adsk.core.SurfaceTypes.NurbsSurfaceType)
            except Exception:
                pass
            out.append(r)
        for m in comp.meshBodies:
            r = {'comp': path, 'name': m.name, 'kind': 'mesh', 'visible': m.isVisible}
            try:
                dm = m.displayMesh
                r['triangles'] = dm.triangleCount
                r['nodes'] = dm.nodeCount
            except Exception:
                pass
            try:
                r['bbox_mm'] = _bbox(m.boundingBox)
            except Exception:
                pass
            out.append(r)
    return out


def params(app, job):
    d = adsk.fusion.Design.cast(app.activeProduct)
    up = [{'name': p.name, 'expr': p.expression, 'comment': p.comment} for p in d.userParameters]
    mp = []
    for p in d.allParameters:
        try:
            role = p.role
        except Exception:
            role = ''
        mp.append({'name': p.name, 'expr': p.expression, 'role': role,
                   'feature': getattr(getattr(p, 'createdBy', None), 'name', None)})
    return {'user': up, 'all_count': len(mp), 'all': mp}


def _write_mesh_stl(m, path):
    import struct
    pm = m.mesh
    xyz = pm.nodeCoordinatesAsDouble
    tri = pm.triangleNodeIndices
    n = len(tri) // 3
    with open(path, 'wb') as fh:
        fh.write(bytes(80) + struct.pack('<I', n))
        for k in range(n):
            a, b, c = tri[3 * k], tri[3 * k + 1], tri[3 * k + 2]
            fh.write(struct.pack('<12fH', 0, 0, 0,
                                 xyz[3 * a] * CM, xyz[3 * a + 1] * CM, xyz[3 * a + 2] * CM,
                                 xyz[3 * b] * CM, xyz[3 * b + 1] * CM, xyz[3 * b + 2] * CM,
                                 xyz[3 * c] * CM, xyz[3 * c + 1] * CM, xyz[3 * c + 2] * CM, 0))
    return n


def _write_calc_stl(b, path):
    """Fallback when the exporter writes nothing: the body's own tessellation at
    the High preset (never custom tolerances - those can take Fusion down)."""
    import struct
    calc = b.meshManager.createMeshCalculator()
    calc.setQuality(adsk.fusion.TriangleMeshQualityOptions.HighQualityTriangleMesh)
    tm = calc.calculate()
    xyz = tm.nodeCoordinatesAsDouble
    tri = tm.nodeIndices
    n = len(tri) // 3
    with open(path, 'wb') as fh:
        fh.write(bytes(80) + struct.pack('<I', n))
        for k in range(n):
            a, bb, c = tri[3 * k], tri[3 * k + 1], tri[3 * k + 2]
            fh.write(struct.pack('<12fH', 0, 0, 0,
                                 xyz[3 * a] * CM, xyz[3 * a + 1] * CM, xyz[3 * a + 2] * CM,
                                 xyz[3 * bb] * CM, xyz[3 * bb + 1] * CM, xyz[3 * bb + 2] * CM,
                                 xyz[3 * c] * CM, xyz[3 * c + 1] * CM, xyz[3 * c + 2] * CM, 0))
    return n


def export(app, job):
    """STL of named root bodies (brep via the exporter, mesh from its nodes) to
    job['dir']. A hidden body exports nothing, so its light bulb is switched on
    for the export and restored."""
    d = adsk.fusion.Design.cast(app.activeProduct)
    em = d.exportManager
    names = list(job['names'])
    root = d.rootComponent
    done = {}
    for name in names:
        hit = None
        for b in root.bRepBodies:
            if b.name == name:
                hit = b
                break
        if hit is None:
            for m in root.meshBodies:
                if m.name == name:
                    hit = m
                    break
        if hit is None:
            done[name] = 'not found'
            continue
        path = os.path.join(job['dir'], ''.join(
            ch if ch.isalnum() or ch in '-_.' else '_' for ch in name) + '.stl')
        try:
            if adsk.fusion.MeshBody.cast(hit):
                done[name] = {'path': path, 'triangles': _write_mesh_stl(hit, path)}
                continue
            was = hit.isLightBulbOn
            hit.isLightBulbOn = True
            try:
                o = em.createSTLExportOptions(hit, path)
                o.meshRefinement = adsk.fusion.MeshRefinementSettings.MeshRefinementHigh
                o.isBinaryFormat = True
                ok = em.execute(o)
            finally:
                hit.isLightBulbOn = was
            done[name] = {'path': path, 'ok': ok, 'exists': os.path.exists(path)}
            if not os.path.exists(path):
                done[name]['triangles'] = _write_calc_stl(hit, path)
        except Exception:
            done[name] = traceback.format_exc()[-300:]
    return done


def sketch_curves(app, job):
    """World polylines (mm) of every curve in the named sketches."""
    d = adsk.fusion.Design.cast(app.activeProduct)
    out = {}
    for name in job['names']:
        sk = d.rootComponent.sketches.itemByName(name)
        curves = []
        for c in sk.sketchCurves:
            if c.isConstruction:
                continue
            ev = c.worldGeometry.evaluator
            ok, a, b = ev.getParameterExtents()
            ok, pts = ev.getStrokes(a, b, 0.001)
            curves.append({'type': c.objectType.split('::')[-1],
                           'pts': [[p.x * CM, p.y * CM, p.z * CM] for p in pts]})
        out[name] = curves
    return out


def cylinders(app, job):
    """Axis, radius and axial extent of every cylindrical face of root bodies
    whose name matches one of job['match'] (substring)."""
    d = adsk.fusion.Design.cast(app.activeProduct)
    out = []
    for b in d.rootComponent.bRepBodies:
        if not any(m in b.name for m in job['match']):
            continue
        for f in b.faces:
            g = f.geometry
            if g.surfaceType != adsk.core.SurfaceTypes.CylinderSurfaceType:
                continue
            o, ax = g.origin, g.axis
            ax.normalize()
            ts = []
            for v in b.vertices:
                p = v.geometry
                ts.append((p.x - o.x) * ax.x + (p.y - o.y) * ax.y + (p.z - o.z) * ax.z)
            bb = b.boundingBox
            out.append({'body': b.name, 'visible': b.isVisible,
                        'origin_mm': [o.x * CM, o.y * CM, o.z * CM],
                        'axis': [ax.x, ax.y, ax.z], 'radius_mm': g.radius * CM,
                        't_range_mm': [min(ts) * CM, max(ts) * CM] if ts else None,
                        'volume_mm3': b.volume * 1000})
    return out


def run(app, job):
    res = {'timeline': timeline, 'bodies': bodies, 'params': params, 'export': export,
           'sketch_curves': sketch_curves, 'cylinders': cylinders}[job['what']](app, job)
    if job.get('out'):
        with open(job['out'], 'w', encoding='utf-8') as fh:
            json.dump(res, fh, indent=1, default=str)
        return {'written': job['out']}
    return res
