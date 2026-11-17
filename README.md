# Intersection Sketch

A Fusion 360 add-in. Pick two solid bodies and get the curve where their surfaces
meet as an editable sketch: one closed (or open) fitted spline per contour. You
edit it by dragging fit points, like any spline you drew yourself.

## How to use

### Where the button is

Design workspace. The **Intersection Sketch** button is placed in three panels:

- **Solid** tab > **Create** panel
- **Surface** tab > **Create** panel
- the **Create** panel of the **Sketch** tab (shown while you edit a sketch)

### Before you start

- You need two **solid** (closed B-Rep) bodies that overlap. A typical pair is a
  bone converted to a solid and a guide or envelope body that cuts through it.
- Mesh bodies cannot be picked. Convert them to solids first (for example with
  Mesh to Part).
- The two bodies must be different bodies, and they must overlap. Bodies that
  only touch, or do not touch at all, give no contour.
- No external Python is needed. Everything runs inside Fusion.

### Step by step

1. Click **Intersection Sketch**.
2. **Body A**: select the first solid body.
3. **Body B**: select the second solid body.
4. Wait for the text box. It says how many contours were found and how long it
   took (several seconds is normal on a bone). If something is wrong, it shows
   the reason in bold instead (for example "the bodies do not overlap").
5. **Contours** group: one checkbox per contour, all ticked. Each shows its colour
   in the viewport (Orange, Blue, Green, Pink, Yellow, Violet, then repeating),
   its length in mm, and "open" if it does not close. Ticked contours are drawn
   thick in the viewport, unticked ones thin. Untick the ones you do not want. At
   most the 12 longest contours are listed.
6. **Spline** group: set the fit options (table below).
7. Click **Create sketch**. The button stays disabled until at least one contour
   is ticked.

| Input | Default | What it does |
|---|---|---|
| **Body A** | none | First solid body. One body. |
| **Body B** | none | Second solid body. One body, not the same as Body A. |
| **Contour 1 ... 12** | ticked | Which contours become sketches. One sketch per ticked contour. |
| **Tolerance** | 0.05 mm | Fit points are added until the spline is this close to the exact intersection everywhere, measured in both directions. Smaller: truer curve, more points to edit. |
| **Start spacing** | 5 mm | Fit points start this far apart along the curve. More are added only where needed. |
| **Exact curve as construction** | on | Also draws the exact intersection in the same sketch, as fixed construction lines, so you can edit the spline against it without moving it by accident. |

Tolerance, Start spacing and Exact curve are remembered for the next time you open
the dialog (`last_settings.json` in the add-in folder).

### What you get

- One 3D sketch per ticked contour, in the **root component**, named
  `Intersection N - <Body A name> x <Body B name>`.
- The sketch is created on the XY plane, but the curve is fully 3D.
- In each sketch: one fitted spline (closed if the contour is closed). Drag its fit
  points to edit it like any spline you drew.
- With **Exact curve as construction** on: up to about 400 fixed construction
  lines that follow the exact intersection.
- The two bodies are not changed. Nothing else is added to the design.

### Checking the result

- The report goes to the Text Commands window (File > View > Show Text Commands)
  and to `IntersectionSketch.log` in the add-in folder. Per sketch it gives: open
  or closed, length in mm, number of fit points, and "spline within X mm of the
  exact curve".
- Compare that last number with your Tolerance. The fit stops after 8 rounds or
  600 fit points per contour, so on a very rough curve the spline can end above
  the tolerance. The report shows the real value either way.
- A contour reported as **open** on two closed solids usually means the kernel
  left a gap. Check the ends in the viewport before you build on it.
- Turn on the construction lines and look along the curve where it bends most.
  That is where the spline is most likely to cut a corner.
- Errors during creation appear in a message box and in the log file.

### Where it fits

Use it after the bone is a solid and a first guide or envelope body exists: the
contour where they meet becomes an editable outline on the bone. That outline can
then drive a patch, a guide base or a plate footprint (for example with Outline
Patch or Guide Base) before you add drill tubes and export for printing.

### Known limits

- Solid bodies only. Mesh bodies must be converted first.
- The bodies must overlap. A contour exists only where one surface passes
  through the other.
- Where the two surfaces are tangent the kernel can leave tiny sliver faces. The
  tool handles the common cases, but a contour can still come out open.
- At most 12 contours are listed (the longest). Smaller ones are not shown.
- The sketches always go in the root component, even if the bodies are inside
  other components.
- Each fit is limited to 600 fit points and 8 refinement rounds.

## How it works

1. **The exact intersection.** Copies of the two bodies are intersected with
   `TemporaryBRepManager`, so nothing is added to the design. Each face of the
   result is traced back to A or B with `findBRepUsingPoint`. The contour is the
   set of edges between an A face and a B face, joined end to end.
2. **Faces that lie on both skins.** Where the two surfaces are tangent, the kernel
   can leave a sliver face that lies on both. Such a face takes its neighbours'
   side; otherwise the contour would break there. Kernel speck edges, shorter than
   the joining tolerance, are dropped.
3. **The fitted spline.** Fit points start every *spacing* along the curve. Each
   round measures, span by span, both directions:
   - how far the spline strays from the exact curve;
   - how far the exact curve strays from the spline, which catches cut corners.

   A point is added at the middle of every span over tolerance. This stops after 8
   rounds or 600 points.

The report gives the fit points of each contour and its real worst deviation.

Example on a distal radius test case, an Alpha Shape body (r 3.00 mm, n 9) x the radius:

| Step | Measured |
|---|---|
| Intersection | 221 faces in 0.5 s |
| Contour | one closed contour, 158.2 mm |
| Fit | 139 fit points; Hausdorff 0.042 mm both ways |
| Total time | 28 s |

## Install

```
powershell -ExecutionPolicy Bypass -File install.ps1
```

The first time, start it in Fusion: Utilities > Add-Ins > Scripts and Add-Ins >
Add-Ins > Intersection Sketch > Run, and tick Run on Startup.

## Files

| | |
|---|---|
| `IntersectionSketch.py` | Shell: lifecycle and the optional dev bridge. |
| `ix_core.py` | All the Fusion-side work. It hot-reloads on every command event. |
| `ix_geom.py` | Chaining, arc length and fit-point placement (stdlib only). |
| `tests/test_geom.py` | Offline checks: `py -3.10 tests/test_geom.py`. |
| `tools/ix.py` | Dev-bridge client. It is active when `devbridge.on` exists. |

## Licence

MIT, Iago Andreu.
