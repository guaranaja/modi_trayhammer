#!/usr/bin/env python3
"""
Modi Boxi-Compatible Warhammer 40K Mini Tray Generator
=======================================================

Generates printable STL trays sized to drop into Modi Boxi storage containers,
with the standard alignment tabs that mate with the Boxi rail system.

Footprints reverse-engineered from official L_Mini_Holder_*_32.stl files.

REQUIREMENTS
------------
    pip install numpy mapbox-earcut numpy-stl

USAGE
-----
Edit the CONFIG section near the bottom and run:

    python3 tray_gen.py

LAYOUT FORMAT
-------------
    BOXI_SIZE = 'L'         # 'L' (Large) — only one currently supported
    FRACTION  = '1/3'       # '1/3', '2/3', or '3/3' (whole)
    LAYOUT = {
        32: 10,             # 10 recesses for 32mm bases
        40: 3,              # 3 recesses for 40mm bases (if they fit)
    }

Supported 40K base sizes (mm):
    25, 28.5, 32, 40, 50, 60, 65, 80, 90, 100, 130, 160

If the requested recesses don't fit in the chosen tray size, the script will
warn and place as many as possible.

LIMITATIONS
-----------
- Only Large Boxi dimensions are tabulated. Medium/Small can be added by
  measuring those reference files.
- Tabs are reproduced as simple rectangular protrusions (1.4mm x 2.0mm). If
  the official tabs have chamfers or are otherwise shaped, those are not
  replicated and trays may have a slightly looser fit.
- Packer is shelf-based, not optimal. For dense-packed same-size recesses,
  efficiency is ~85% vs hand-tuned.
"""

from __future__ import annotations
import math
import struct
import numpy as np
import mapbox_earcut as earcut


# ---------------------------------------------------------------------------
# Modi Boxi geometry — measured from official L_Mini_Holder_{1,2,3}_32.stl
# ---------------------------------------------------------------------------

# Dimensions in mm. Key: (boxi_size, fraction) -> (plate_width, plate_depth)
# Width is the MAIN PLATE width (without tabs). Outer bbox is plate_width + 2*TAB_PROTRUSION.
BOXI_SIZES = {
    ('L', '1/3'): (203.50, 64.15),
    ('L', '2/3'): (203.50, 133.49),
    ('L', '3/3'): (203.50, 206.40),
}

PLATE_THICKNESS   = 5.0     # mm — total tray thickness
RECESS_DEPTH      = 4.0     # mm — recess depth into plate (floor is 1mm thick)
TAB_PROTRUSION    = 1.40    # mm — how far tab sticks out from plate edge
TAB_WIDTH         = 2.00    # mm — tab dimension parallel to plate edge

DEFAULT_CLEARANCE = 1.5     # mm — added to base diameter for recess fit
WALL_MIN          = 2.24    # mm — min plate material between adjacent recesses
                            # (matches official Modi Boxi spacing)
EDGE_MARGIN_X     = 3.27    # mm — left/right margin (matches official)
EDGE_MARGIN_Y     = 4.5     # mm — top/bottom margin (matches official)
CIRCLE_SEGMENTS   = 48      # tessellation per recess

SUPPORTED_BASES = (25, 28.5, 32, 40, 50, 60, 65, 80, 90, 100, 130, 160)

# Approximate widest cross-section of a typical 40K model on each base size
# (mm). Sourced from Battle Foam infantry cutouts and observed model widths.
# Used to set anti-collision spacing between adjacent recesses — without this,
# Intercessor shoulders/bolters touch at the default plate-wall pitch.
# Override by editing here or by passing a custom value at the prompt later.
MODEL_ENVELOPE = {
    25:    30,    # Cultists, Fire Warriors
    28.5:  32,
    32:    40,    # Intercessors, Primaris infantry (shoulders + bolter)
    40:    50,    # Sergeants, characters, classic Terminators
    50:    70,    # bikers — directional; using long axis as worst case
    60:    65,    # Aggressors, Centurions
    65:    65,
    80:    80,
    90:    90,
    100:  100,
    130:  150,    # Armiger-class — wider than base
    160:  160,
}
MODEL_CLEARANCE = 2.0  # mm — gap between adjacent model envelopes

# Insert-pocket options — standard metric flat washers (DIN 125 / ISO 7089).
# Pop one in each pocket; magnetized minis stick to it regardless of polarity.
# (label, OD_mm, thickness_mm, hint). Pocket auto-bumps plate thickness if
# needed so >= MAGNET_FLOOR_MIN of material remains below the pocket.
MAGNET_OPTIONS = (
    ("M2 washer   (5 x 0.3 mm)",  5.0, 0.3, "tiny — 25mm bases"),
    ("M2.5 washer (6 x 0.5 mm)",  6.0, 0.5, "small — 25-32mm bases"),
    ("M3 washer   (7 x 0.5 mm)",  7.0, 0.5, "small — 32mm infantry"),
    ("M4 washer   (9 x 0.8 mm)",  9.0, 0.8, "medium — 32-40mm bases"),
    ("M5 washer   (10 x 1 mm)",  10.0, 1.0, "10mm — biggest standard size"),
)
MAGNET_HOLE_CLEARANCE = 0.20  # mm — added to insert diameter for fit
MAGNET_FLOOR_MIN      = 0.40  # mm — minimum material below pocket bottom


# ---------------------------------------------------------------------------
# Mesh container
# ---------------------------------------------------------------------------

class Mesh:
    def __init__(self):
        self.tris = []

    def add(self, v0, v1, v2):
        self.tris.append((v0, v1, v2))

    def quad(self, a, b, c, d):
        self.add(a, b, c)
        self.add(a, c, d)

    def write_stl(self, path: str):
        with open(path, "wb") as f:
            f.write(b"\x00" * 80)
            f.write(struct.pack("<I", len(self.tris)))
            for v0, v1, v2 in self.tris:
                ux, uy, uz = v1[0]-v0[0], v1[1]-v0[1], v1[2]-v0[2]
                vx, vy, vz = v2[0]-v0[0], v2[1]-v0[1], v2[2]-v0[2]
                nx = uy*vz - uz*vy
                ny = uz*vx - ux*vz
                nz = ux*vy - uy*vx
                L = math.sqrt(nx*nx + ny*ny + nz*nz)
                if L > 1e-12:
                    nx, ny, nz = nx/L, ny/L, nz/L
                f.write(struct.pack("<3f", nx, ny, nz))
                f.write(struct.pack("<3f", *v0))
                f.write(struct.pack("<3f", *v1))
                f.write(struct.pack("<3f", *v2))
                f.write(struct.pack("<H", 0))


# ---------------------------------------------------------------------------
# Packing — graceful banded layout
# ---------------------------------------------------------------------------

def _band_geometry(count, d, pitch, band_w, mode):
    """
    Plan a single horizontal band of `count` items, recess diameter `d`,
    using center-to-center `pitch` (already accounting for plate wall AND
    model-envelope clearance), into a band of width `band_w`.

    Returns (rows, row_pitch, x_offset_alt, band_h, n_placed) where `rows`
    is per-row item counts. Returns None if even one item doesn't fit.

    mode='grid': axis-aligned. mode='hex': odd rows offset by pitch/2.
    """
    r = d / 2
    if band_w + 1e-6 < d:
        return None
    if count <= 0:
        return ([], 0, 0, 0, 0)

    n_per_row = max(1, int((band_w - d + 1e-6) / pitch) + 1)

    if mode == 'grid':
        n_per_row = min(n_per_row, count)
        n_rows = math.ceil(count / n_per_row)
        rows = []
        rem = count
        for _ in range(n_rows):
            take = min(n_per_row, rem)
            rows.append(take)
            rem -= take
        band_h = (n_rows - 1) * pitch + d
        return (rows, pitch, 0.0, band_h, count)

    # mode == 'hex'
    x_off = pitch / 2.0
    n_stag = max(0, int((band_w - 2 * r - x_off + 1e-6) / pitch) + 1)
    n_stag = min(n_stag, n_per_row)
    if n_stag == 0:
        return _band_geometry(count, d, pitch, band_w, 'grid')

    row_pitch_hex = pitch * math.sqrt(3) / 2
    rows = []
    rem = count
    stag = False
    while rem > 0:
        cap = n_stag if stag else n_per_row
        take = min(cap, rem)
        rows.append(take)
        rem -= take
        stag = not stag
    n_rows = len(rows)
    band_h = (n_rows - 1) * row_pitch_hex + d if n_rows > 1 else d
    return (rows, row_pitch_hex, x_off, band_h, count)


def _place_band(rows, row_pitch, x_offset_alt, d, pitch,
                usable_w, margin_x, band_top_y):
    """
    Yield (cx, cy, d) for each item in a band whose top edge sits at
    band_top_y in plate coords. Rows are listed top-to-bottom.
    The whole lattice is centered horizontally in usable_w.
    `pitch` is the center-to-center spacing within a row (already
    accounting for plate wall and model-envelope clearance).
    """
    r = d / 2
    in_row_pitch = pitch

    # Find bounding-box right edge so we can center the lattice
    max_right = 0.0
    for i, n in enumerate(rows):
        if n == 0:
            continue
        anchor = x_offset_alt if (i % 2 == 1 and x_offset_alt > 0) else 0.0
        right_edge = anchor + (n - 1) * in_row_pitch + d
        if right_edge > max_right:
            max_right = right_edge
    x_translate = (usable_w - max_right) / 2.0

    for i, n in enumerate(rows):
        if n == 0:
            continue
        anchor = x_offset_alt if (i % 2 == 1 and x_offset_alt > 0) else 0.0
        cy = band_top_y - r - i * row_pitch
        for col_i in range(n):
            cx = margin_x + x_translate + anchor + r + col_i * in_row_pitch
            yield (cx, cy, d)


def pack_gracefully(layout, plate_w, plate_h,
                    clearance=DEFAULT_CLEARANCE,
                    extra_spacing=0.0,
                    wall=WALL_MIN,
                    margin_x=EDGE_MARGIN_X,
                    margin_y=EDGE_MARGIN_Y):
    """
    Arrange recesses in clean horizontal bands by base size, largest at top.

    Spacing is auto-set per size from MODEL_ENVELOPE so adjacent minis don't
    physically collide above the tray. `extra_spacing` adds on top — bump
    this if you want even more breathing room than the defaults.

    Per-band ladder:
      1. clean grid (axis-aligned, identical rows)
      2. if total stack is too tall, switch the band with the biggest height
         savings from grid → hex; repeat until fits or no swaps left
      3. if still too tall, drop items: prefer drops that eliminate a full row;
         break ties toward smallest base size

    Returns (placements, summary) — same shape as the legacy packer.
    """
    for base in layout:
        if base not in SUPPORTED_BASES:
            print(f"  NOTE: {base}mm is not a standard 40K base size — using anyway")

    sizes = sorted([s for s, c in layout.items() if c > 0], reverse=True)
    if not sizes:
        return [], {'placed': 0, 'unplaced': 0, 'unplaced_items': []}

    usable_w = plate_w - 2 * margin_x
    usable_h = plate_h - 2 * margin_y

    # Drop sizes that don't fit width-wise at all
    width_overflow = [s for s in sizes if (s + clearance) > usable_w + 1e-6]
    sizes = [s for s in sizes if s not in width_overflow]

    def _pitch_for(s):
        d = s + clearance
        env = MODEL_ENVELOPE.get(s, d)
        return max(d + wall, env + MODEL_CLEARANCE) + extra_spacing

    def _inter_gap(s1, s2):
        d1 = s1 + clearance
        d2 = s2 + clearance
        env1 = MODEL_ENVELOPE.get(s1, d1)
        env2 = MODEL_ENVELOPE.get(s2, d2)
        ov1 = max(0.0, env1 - d1)
        ov2 = max(0.0, env2 - d2)
        return max(wall, (ov1 + ov2) / 2 + MODEL_CLEARANCE) + extra_spacing

    pitches = {s: _pitch_for(s) for s in sizes}

    truncated = {s: 0 for s in sizes}
    modes = {s: 'grid' for s in sizes}

    def compute_bands():
        out = {}
        for s in sizes:
            d = s + clearance
            n = layout[s] - truncated[s]
            out[s] = _band_geometry(n, d, pitches[s], usable_w, modes[s])
        return out

    def active_sizes(bands):
        return [s for s in sizes if bands.get(s) and bands[s][3] > 0]

    def total_v(bands):
        active = active_sizes(bands)
        if not active:
            return 0
        total = sum(bands[s][3] for s in active)
        for i in range(len(active) - 1):
            total += _inter_gap(active[i], active[i + 1])
        return total

    bands = compute_bands()

    # Phase 1: switch grid → hex, biggest savings first
    while total_v(bands) > usable_h + 1e-6:
        best_savings = 0.0
        best_s = None
        for s in sizes:
            if modes[s] == 'hex':
                continue
            d = s + clearance
            n = layout[s] - truncated[s]
            g = _band_geometry(n, d, pitches[s], usable_w, 'grid')
            h = _band_geometry(n, d, pitches[s], usable_w, 'hex')
            if g is None or h is None:
                continue
            savings = g[3] - h[3]
            if savings > best_savings:
                best_savings = savings
                best_s = s
        if best_s is None:
            break
        modes[best_s] = 'hex'
        bands = compute_bands()

    # Phase 2: truncate. Prefer drops that shrink a band (eliminate its last
    # row) over drops that just thin a row. Tie-break toward smallest base.
    def best_drop_target():
        candidates = []
        for s in reversed(sizes):  # smallest first
            n = layout[s] - truncated[s]
            if n <= 0:
                continue
            d = s + clearance
            cur = _band_geometry(n, d, pitches[s], usable_w, modes[s])
            nxt = _band_geometry(n - 1, d, pitches[s], usable_w, modes[s])
            if cur is None or nxt is None:
                continue
            h_savings = cur[3] - nxt[3]
            candidates.append((h_savings, s))
        if not candidates:
            return None
        candidates.sort(key=lambda x: (-x[0], x[1]))
        return candidates[0][1]

    safety = 0
    while total_v(bands) > usable_h + 1e-6 and safety < 10_000:
        s = best_drop_target()
        if s is None:
            break
        truncated[s] += 1
        bands = compute_bands()
        safety += 1

    # Assemble placements with equal vertical gaps
    active = active_sizes(bands)
    placements = []
    if active:
        total_band_h = sum(bands[s][3] for s in active)
        min_gaps = [_inter_gap(active[i], active[i + 1])
                    for i in range(len(active) - 1)]
        total_min_v = total_band_h + sum(min_gaps)
        leftover = max(0, usable_h - total_min_v)
        extra_per_slot = leftover / (len(active) + 1)

        cur_top = plate_h - margin_y - extra_per_slot
        for i, s in enumerate(active):
            rows, row_pitch, x_off, band_h, _placed = bands[s]
            d = s + clearance
            placements.extend(_place_band(
                rows, row_pitch, x_off, d, pitches[s],
                usable_w, margin_x, cur_top,
            ))
            cur_top -= band_h
            if i < len(active) - 1:
                cur_top -= min_gaps[i] + extra_per_slot

    unplaced = []
    for s in sizes:
        d = s + clearance
        for _ in range(truncated[s]):
            unplaced.append((s, d))
    for s in width_overflow:
        d = s + clearance
        for _ in range(layout[s]):
            unplaced.append((s, d))

    return placements, {
        'placed': len(placements),
        'unplaced': len(unplaced),
        'unplaced_items': unplaced,
    }


# ---------------------------------------------------------------------------
# Tray builder — outer polygon with tabs, plus holes
# ---------------------------------------------------------------------------

def build_outer_polygon(plate_w, plate_h, with_tabs=True):
    """
    Returns the outer polygon CCW (viewed from +Z) for the tray top.
    Origin (0,0) is at the bottom-left corner of the MAIN PLATE (not the tab bbox).
    Tabs protrude in -X (left) and +X (right) at the center of the Y axis.
    """
    if not with_tabs:
        return [(0, 0), (plate_w, 0), (plate_w, plate_h), (0, plate_h)]

    cy = plate_h / 2
    tab_y_lo = cy - TAB_WIDTH / 2
    tab_y_hi = cy + TAB_WIDTH / 2

    # Walk CCW from bottom-left:
    poly = [
        (0, 0),
        (plate_w, 0),
        # right edge up to tab
        (plate_w, tab_y_lo),
        (plate_w + TAB_PROTRUSION, tab_y_lo),
        (plate_w + TAB_PROTRUSION, tab_y_hi),
        (plate_w, tab_y_hi),
        (plate_w, plate_h),
        (0, plate_h),
        # left edge down to tab
        (0, tab_y_hi),
        (-TAB_PROTRUSION, tab_y_hi),
        (-TAB_PROTRUSION, tab_y_lo),
        (0, tab_y_lo),
    ]
    return poly


def build_tray(boxi_size, fraction, layout, clearance=DEFAULT_CLEARANCE,
               extra_spacing=0.0, magnet=None):
    """
    Build a tray Mesh.

    magnet: optional dict {'d': diameter_mm, 't': thickness_mm, 'label': str}.
            If present, each recess gets a centered cylindrical pocket sized
            for the magnet. Plate thickness is bumped if needed to keep the
            pocket blind (>= MAGNET_FLOOR_MIN of material under it).
    """
    key = (boxi_size, fraction)
    if key not in BOXI_SIZES:
        raise ValueError(f"Unsupported boxi/fraction: {key}. "
                         f"Available: {list(BOXI_SIZES.keys())}")
    plate_w, plate_h = BOXI_SIZES[key]

    placements, summary = pack_gracefully(
        layout, plate_w, plate_h,
        clearance=clearance, extra_spacing=extra_spacing,
    )

    plate_t = PLATE_THICKNESS
    recess_d = RECESS_DEPTH
    pocket_r = None
    pocket_t = None
    if magnet:
        pocket_r = (magnet['d'] + MAGNET_HOLE_CLEARANCE) / 2
        pocket_t = magnet['t']
        # Auto-bump plate so we always have >= MAGNET_FLOOR_MIN under the
        # pocket. Thin washers drill into the existing 1mm floor with no
        # bump; thicker ones (M4, M5) push the plate up.
        min_plate_t = recess_d + pocket_t + MAGNET_FLOOR_MIN
        if plate_t < min_plate_t:
            plate_t = min_plate_t

    mesh = Mesh()
    top_z = plate_t
    recess_bottom_z = plate_t - recess_d
    pocket_bottom_z = recess_bottom_z - pocket_t if magnet else None

    outer = build_outer_polygon(plate_w, plate_h, with_tabs=True)

    # ---- Plate bottom (normal -Z) ----
    verts_2d = np.array(outer, dtype=np.float64)
    rings = np.array([len(outer)], dtype=np.uint32)
    idx = earcut.triangulate_float64(verts_2d, rings).reshape(-1, 3)
    for i, j, k in idx:
        # Viewed from below; reverse winding from earcut's CCW-from-above.
        a = verts_2d[i]
        b = verts_2d[j]
        c = verts_2d[k]
        mesh.add(
            (float(a[0]), float(a[1]), 0),
            (float(c[0]), float(c[1]), 0),
            (float(b[0]), float(b[1]), 0),
        )

    # ---- Plate side walls (outward normals) ----
    n_outer = len(outer)
    for i in range(n_outer):
        x0, y0 = outer[i]
        x1, y1 = outer[(i+1) % n_outer]
        # Quad from (x0,y0,0) → (x1,y1,0) → (x1,y1,top_z) → (x0,y0,top_z)
        # Winding for outward normal (we're going CCW around outer at top,
        # so outward from +Z looking down means cross product of edge with +Z gives outward).
        mesh.quad(
            (x0, y0, 0),
            (x1, y1, 0),
            (x1, y1, top_z),
            (x0, y0, top_z),
        )

    # ---- Plate top with circular holes ----
    rings_data = list(outer)
    ring_ends = [len(rings_data)]
    for cx, cy, d in placements:
        r = d / 2
        for i in range(CIRCLE_SEGMENTS):
            ang = -2 * math.pi * i / CIRCLE_SEGMENTS  # CW for holes
            rings_data.append((cx + r*math.cos(ang), cy + r*math.sin(ang)))
        ring_ends.append(len(rings_data))

    verts_top = np.array(rings_data, dtype=np.float64)
    rings_top = np.array(ring_ends, dtype=np.uint32)
    idx_top = earcut.triangulate_float64(verts_top, rings_top).reshape(-1, 3)
    for i, j, k in idx_top:
        a = verts_top[i]
        b = verts_top[j]
        c = verts_top[k]
        mesh.add(
            (float(a[0]), float(a[1]), top_z),
            (float(b[0]), float(b[1]), top_z),
            (float(c[0]), float(c[1]), top_z),
        )

    # ---- Recess walls ----
    for cx, cy, d in placements:
        r = d / 2
        for i in range(CIRCLE_SEGMENTS):
            a0 = 2 * math.pi * i / CIRCLE_SEGMENTS
            a1 = 2 * math.pi * (i + 1) / CIRCLE_SEGMENTS
            x0, y0 = cx + r*math.cos(a0), cy + r*math.sin(a0)
            x1, y1 = cx + r*math.cos(a1), cy + r*math.sin(a1)
            top0 = (x0, y0, top_z)
            top1 = (x1, y1, top_z)
            bot0 = (x0, y0, recess_bottom_z)
            bot1 = (x1, y1, recess_bottom_z)
            mesh.quad(top0, top1, bot1, bot0)

    # ---- Recess bottoms (annulus + magnet pocket, or plain disk) ----
    # rim_min = how much recess-floor material must remain around the pocket.
    # If the magnet is too big for a given base, fall back to a plain disk
    # for that recess and warn (computed below as too_small_for_magnet).
    rim_min = 0.75
    too_small_for_magnet = []

    for cx, cy, d in placements:
        r = d / 2
        has_magnet = magnet is not None and pocket_r is not None and (pocket_r + rim_min) <= r
        if magnet is not None and not has_magnet:
            too_small_for_magnet.append(d - clearance)

        if has_magnet:
            # Annulus floor: outer ring CCW, inner ring CW (the hole)
            ring_pts = []
            for i in range(CIRCLE_SEGMENTS):
                ang = 2 * math.pi * i / CIRCLE_SEGMENTS
                ring_pts.append((cx + r * math.cos(ang), cy + r * math.sin(ang)))
            outer_end = len(ring_pts)
            for i in range(CIRCLE_SEGMENTS):
                ang = -2 * math.pi * i / CIRCLE_SEGMENTS
                ring_pts.append((cx + pocket_r * math.cos(ang),
                                 cy + pocket_r * math.sin(ang)))
            verts = np.array(ring_pts, dtype=np.float64)
            rings = np.array([outer_end, len(ring_pts)], dtype=np.uint32)
            idx = earcut.triangulate_float64(verts, rings).reshape(-1, 3)
            for i, j, k in idx:
                a = verts[i]; b = verts[j]; c = verts[k]
                mesh.add(
                    (float(a[0]), float(a[1]), recess_bottom_z),
                    (float(b[0]), float(b[1]), recess_bottom_z),
                    (float(c[0]), float(c[1]), recess_bottom_z),
                )

            # Pocket walls (inward normals)
            for i in range(CIRCLE_SEGMENTS):
                a0 = 2 * math.pi * i / CIRCLE_SEGMENTS
                a1 = 2 * math.pi * (i + 1) / CIRCLE_SEGMENTS
                x0, y0 = cx + pocket_r * math.cos(a0), cy + pocket_r * math.sin(a0)
                x1, y1 = cx + pocket_r * math.cos(a1), cy + pocket_r * math.sin(a1)
                top0 = (x0, y0, recess_bottom_z)
                top1 = (x1, y1, recess_bottom_z)
                bot0 = (x0, y0, pocket_bottom_z)
                bot1 = (x1, y1, pocket_bottom_z)
                mesh.quad(top0, top1, bot1, bot0)

            # Pocket floor (disk, +Z normal)
            center = (cx, cy, pocket_bottom_z)
            for i in range(CIRCLE_SEGMENTS):
                a0 = 2 * math.pi * i / CIRCLE_SEGMENTS
                a1 = 2 * math.pi * (i + 1) / CIRCLE_SEGMENTS
                p0 = (cx + pocket_r * math.cos(a0),
                      cy + pocket_r * math.sin(a0), pocket_bottom_z)
                p1 = (cx + pocket_r * math.cos(a1),
                      cy + pocket_r * math.sin(a1), pocket_bottom_z)
                mesh.add(center, p0, p1)
        else:
            # Plain disk floor
            center = (cx, cy, recess_bottom_z)
            for i in range(CIRCLE_SEGMENTS):
                a0 = 2 * math.pi * i / CIRCLE_SEGMENTS
                a1 = 2 * math.pi * (i + 1) / CIRCLE_SEGMENTS
                p0 = (cx + r * math.cos(a0), cy + r * math.sin(a0), recess_bottom_z)
                p1 = (cx + r * math.cos(a1), cy + r * math.sin(a1), recess_bottom_z)
                mesh.add(center, p0, p1)

    if too_small_for_magnet:
        from collections import Counter
        cnt = Counter(too_small_for_magnet)
        for base, n in sorted(cnt.items()):
            print(f"  NOTE: {n}x {base}mm recesses too small for "
                  f"{magnet['label']} magnet — left as plain pockets")

    return mesh, placements, summary, (plate_w, plate_h), plate_t


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def generate(boxi_size, fraction, layout, output_path="tray.stl",
             clearance=DEFAULT_CLEARANCE, extra_spacing=0.0, magnet=None):
    print(f"Boxi: {boxi_size}, Fraction: {fraction}")
    print(f"Requested layout: {layout}")
    print(f"Extra spacing: +{extra_spacing} mm (on top of auto per-size pitch)")
    if magnet:
        print(f"Washer pocket: {magnet['label']}")

    mesh, placements, summary, (pw, ph), plate_t = build_tray(
        boxi_size, fraction, layout, clearance=clearance,
        extra_spacing=extra_spacing, magnet=magnet,
    )

    print(f"Plate (main): {pw} x {ph} mm  (thickness {plate_t:.2f} mm)")
    print(f"Outer bbox:   {pw + 2*TAB_PROTRUSION} x {ph} mm (with tabs)")

    requested = summary['placed'] + summary['unplaced']
    if summary['unplaced']:
        pct = 100.0 * summary['placed'] / requested if requested else 0
        bar = "!" * 60
        print()
        print(bar)
        print(f"!! TRAY OVERPOPULATED — {summary['unplaced']} of {requested} "
              f"recesses didn't fit ({pct:.0f}% placed)")
        from collections import Counter
        cnt = Counter(b for b, _ in summary['unplaced_items'])
        dropped_str = ", ".join(f"{n}x {b}mm" for b, n in sorted(cnt.items()))
        print(f"   Dropped: {dropped_str}")
        print(f"   Fix:     split into multiple trays, reduce counts, "
              f"or accept partial fill")
        print(bar)
    else:
        print(f"Placed: {summary['placed']} of {requested} recesses "
              f"(100%) — all fit")

    mesh.write_stl(output_path)
    print(f"Wrote {len(mesh.tris)} triangles -> {output_path}")
    return placements, summary


# ============================================================================
# Defaults — pressing Enter at each prompt accepts these
# ============================================================================

DEFAULT_BOXI_SIZE = 'L'
DEFAULT_FRACTION  = '3/3'
DEFAULT_LAYOUT = {
    32: 20,    # Intercessors
    40: 4,     # Sergeants / characters
    60: 1,     # Aggressor / Nob biker
}
DEFAULT_OUTPUT = "tray.stl"


# ---------------------------------------------------------------------------
# Interactive prompts
# ---------------------------------------------------------------------------

def _ask(prompt, default=None, validator=None, error_msg=None):
    suffix = f" [{default}]" if default is not None else ""
    while True:
        raw = input(f"{prompt}{suffix}: ").strip()
        if not raw and default is not None:
            return default
        if not raw:
            print("  (required)")
            continue
        if validator is None:
            return raw
        try:
            return validator(raw)
        except (ValueError, KeyError) as e:
            print(f"  {error_msg or e}")


def _ask_yn(prompt, default=True):
    suffix = " [Y/n]" if default else " [y/N]"
    while True:
        raw = input(f"{prompt}{suffix}: ").strip().lower()
        if not raw:
            return default
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        print("  please answer y or n")


def _validate_choice(choices):
    def v(s):
        if s not in choices:
            raise ValueError(f"must be one of: {', '.join(choices)}")
        return s
    return v


def _validate_base(s):
    val = float(s) if '.' in s else int(s)
    if val not in SUPPORTED_BASES:
        print(f"  WARNING: {val}mm is not a standard 40K base size")
    return val


def _validate_positive_int(s):
    n = int(s)
    if n < 1:
        raise ValueError("must be >= 1")
    return n


def _validate_positive_float(s):
    f = float(s)
    if f <= 0:
        raise ValueError("must be > 0")
    return f


def _validate_magnet_choice(s):
    n = int(s)
    if not (1 <= n <= len(MAGNET_OPTIONS)):
        raise ValueError(f"must be between 1 and {len(MAGNET_OPTIONS)}")
    return n


def prompt_advanced_options():
    """Returns a dict with optional keys: 'magnet'."""
    opts = {}
    print()
    if not _ask_yn("Configure advanced options?", default=False):
        return opts

    print()
    print("Washer pockets — adds a centered cylindrical pocket in each recess")
    print("floor sized for a standard metric flat washer. Drop a washer in,")
    print("then minis with magnets in their bases stick to it. No polarity")
    print("issues (steel attracts both poles equally).")
    print()
    if _ask_yn("  Add washer pockets?", default=False):
        print()
        print("  Available washer sizes:")
        for i, (label, _d, _t, hint) in enumerate(MAGNET_OPTIONS):
            print(f"    {i+1}) {label}  — {hint}")
        choice = _ask("  Pick a size (number)",
                      validator=_validate_magnet_choice,
                      error_msg=f"must be 1..{len(MAGNET_OPTIONS)}")
        label, dia, thick, _hint = MAGNET_OPTIONS[choice - 1]
        opts['magnet'] = {'d': dia, 't': thick, 'label': label}
        floor_t = PLATE_THICKNESS - RECESS_DEPTH
        if thick + MAGNET_FLOOR_MIN > floor_t:
            need = RECESS_DEPTH + thick + MAGNET_FLOOR_MIN
            print(f"  Note: plate thickness will be bumped from "
                  f"{PLATE_THICKNESS:.1f} to {need:.2f} mm to keep "
                  f"{MAGNET_FLOOR_MIN} mm under the pocket.")

    return opts


def prompt_layout():
    """Walk the user through building a layout dict."""
    print()
    print("Layout — how many recesses of each base size?")
    print(f"  Supported bases (mm): {', '.join(str(b) for b in SUPPORTED_BASES)}")
    print(f"  Default layout: {DEFAULT_LAYOUT}")
    print()

    if _ask_yn("Use default layout?", default=True):
        return dict(DEFAULT_LAYOUT)

    layout = {}
    while True:
        base = _ask("  Base size (mm)", validator=_validate_base,
                    error_msg="invalid base size")
        count = _ask(f"  Count of {base}mm recesses", default=1,
                     validator=_validate_positive_int)
        layout[base] = layout.get(base, 0) + count
        print(f"  Current layout: {layout}")
        if not _ask_yn("Add another base size?", default=False):
            break
    return layout


def interactive():
    print("=" * 60)
    print("Modi Boxi-Compatible Mini Tray Generator")
    print("=" * 60)
    print()

    available_fractions = sorted({f for (b, f) in BOXI_SIZES.keys()
                                  if b == DEFAULT_BOXI_SIZE})

    boxi_size = DEFAULT_BOXI_SIZE  # only 'L' currently
    print(f"Boxi size: {boxi_size} (only Large is currently supported)")

    fraction = _ask(
        f"Fraction ({', '.join(available_fractions)})",
        default=DEFAULT_FRACTION,
        validator=_validate_choice(available_fractions),
    )

    layout = prompt_layout()

    print("\nSpacing is auto-set per base size from MODEL_ENVELOPE so adjacent")
    print("minis don't physically collide above the tray (e.g. Intercessor")
    print("shoulders/bolters). You can add extra padding on top of that.")
    extra = _ask("Extra spacing (mm, beyond the auto-computed minimum)",
                 default=0.0,
                 validator=lambda s: max(0.0, float(s)))

    output_path = _ask("Output filename", default=DEFAULT_OUTPUT)

    adv = prompt_advanced_options()

    print()
    print("-" * 60)
    return boxi_size, fraction, layout, extra, output_path, adv


if __name__ == "__main__":
    try:
        boxi_size, fraction, layout, extra, output_path, adv = interactive()
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled.")
        raise SystemExit(1)

    generate(boxi_size, fraction, layout,
             output_path=output_path,
             extra_spacing=extra,
             magnet=adv.get('magnet'))