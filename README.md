# Modi Trayhammer

Parametric Warhammer 40K mini tray generator. Outputs STL files sized to drop into [Modi Boxi](https://modiboxi.com/) storage containers, with the standard alignment tabs and recesses for the base sizes you specify.

Project home: [github.com/guaranaja/modi_trayhammer](https://github.com/guaranaja/modi_trayhammer)

## What it does

- **Interactive prompts** — no editing source needed. Pick Boxi fraction, layout, packing style, output filename.
- **Three packing styles**:
    - **Banded** *(default)* — each base size in one horizontal row, largest at top, items centered with equal spacing. Hex fallback per band when tight. Maximum density, intentional look.
    - **Fractal** — Apollonian-inspired greedy packing. Largest item anchors the center, subsequent items snug into the tightest valid tangent position (tangent-to-one, tangent-to-two, or tangent-to-edge). Mixed sizes intermingle organically. Lower density than banded.
    - **Uniform** — single hex lattice where every adjacent pair is at the same center-to-center pitch. Sizes intermix on the grid, larger items cluster centrally. Pitch starts at the worst-pair envelope distance and auto-tightens (with a printed warning) if needed to fit all requested items.
- **Model-envelope-aware spacing** — center-to-center pitch is auto-set per base size from a table of typical model widths (e.g. Intercessors on 32mm bases need ~42mm pitch, not 35mm, to keep shoulders and bolters from touching). Add extra padding interactively if you want more breathing room.
- **Overpopulation alarm** — if your layout doesn't fit, you get a loud, specific warning telling you what was dropped and how to fix it.
- **Washer pockets (advanced option)** — drill a centered pocket into each recess floor sized for a standard metric flat washer (M2–M5). Drop a washer in, glue a magnet into your mini's base, and the mini sticks — no polarity issues, since steel attracts both poles equally. Plate thickness auto-bumps if the washer is thicker than the existing floor.

## Requirements

```
pip install numpy mapbox-earcut
```

(`numpy-stl` is mentioned in older docs but isn't required — STL is written manually.)

## Usage

```
python traygen.py
```

The script walks you through:

1. **Fraction** — `1/3`, `2/3`, or `3/3` of a Large Boxi
2. **Layout** — either use the default `{32: 20, 40: 4, 60: 1}` or loop through adding `(base_size, count)` entries
3. **Packing style** — `1` banded (dense, intentional) or `2` fractal (organic, less dense)
4. **Extra spacing** — additional mm beyond the auto-computed per-size pitch (default `0`)
5. **Output filename** — default `tray.stl`
6. **Advanced options** *(optional)* — washer pockets

## Supported base sizes (mm)

`25, 28.5, 32, 40, 50, 60, 65, 80, 90, 100, 130, 160`

Anything else still works but prints a warning.

## How spacing is computed

For each base size, the center-to-center pitch is:

```
pitch = max(base_diameter + base_clearance + plate_wall,
            model_envelope + model_clearance) + extra_spacing
```

| Constant            | Value  | Purpose |
|---------------------|--------|---------|
| `base_clearance`    | 1.5 mm | recess fit around the base |
| `plate_wall`        | 2.24 mm | min material between recess edges (matches official Modi Boxi) |
| `model_clearance`   | 2.0 mm | air gap between adjacent model envelopes |
| `extra_spacing`     | user input (default 0) | extra padding on top of the minimum |

Model envelopes live in the `MODEL_ENVELOPE` table at the top of [traygen.py](traygen.py) — edit there to tune for your specific models.

## What's not yet supported

- Only **Large** Boxi dimensions are tabulated. Medium/Small need their plate width, height, tab positions, and edge margins added to `BOXI_SIZES`.
- Tabs are rectangular protrusions (1.4 × 2.0 mm). If the official tabs have chamfers, those aren't replicated — fit may be slightly looser.
- Bikers are tricky: 50mm bases with 70mm-long bikes are treated as if the envelope is 70mm in both axes (worst case), which wastes some space on the cross-axis. Orientation-aware packing would help.
- No 3D STL preview — you'll see the geometry only when you open the file in a slicer or viewer.

## License

GPL-3.0 (per the parent project at [guaranaja/modi_trayhammer](https://github.com/guaranaja/modi_trayhammer)).

Modi Boxi is a separate product by Mod Innovations LLC; this generator just produces compatible inserts.
