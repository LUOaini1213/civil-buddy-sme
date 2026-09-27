"""Write the SYNTHETIC façade panel lists (materials sheet, the format of test/benchmarks/excel).

24 unitised curtain-wall panels, 4200 x 1500 x 250 mm, 450 kg each, east elevation L5-L8.
facade_panels_rev_b.xlsx is the same list at a later revision, for the tender <-> packing link demo:
level L9 added (6 more panels) and the L8 panels re-weighed at 520 kg each (a thicker glass make-up).
facade_panels_mixed.xlsx is a mixed delivery written the way a panel schedule usually looks, not in the engine's own
column names: a title block above the header, "Mark / Unit Wt (kg) / Depth (mm)" headers and a TOTAL row. It holds
typical, corner and spandrel panels and two crates of steel brackets (see MIXED below).
The panels, marks and notes are invented for software testing; they are no contractor's data.
Run: python examples/facade-demo/make_panels.py [NAME ...]   (default: every list)
"""
import sys
from pathlib import Path

import openpyxl

HERE = Path(__file__).resolve().parent
HEAD = ["id", "name", "quantity", "weight_kg", "total_weight_kg", "length_mm", "width_mm", "height_mm", "note"]
FLOORS = ("L5", "L6", "L7", "L8")
PER_FLOOR, KG, DIMS = 6, 450, (4200, 1500, 250)
EN = ("Unitised curtain wall panel UCW-E1 east {floor} (SYNTHETIC)",
      "glass, fragile, transport upright on A-frame, do not stack, do not tip")
# name -> (label, note, floors, kg per panel by floor)
LISTS = {
    "facade_panels": (*EN, FLOORS, {}),
    "facade_panels_zh": ("单元式幕墙板块 UCW-E1 东立面{floor}（合成示例）", "玻璃 易碎 禁翻 直立运输 禁叠", FLOORS, {}),
    "facade_panels_rev_b": (EN[0].replace("(SYNTHETIC)", "rev B (SYNTHETIC)"), EN[1], (*FLOORS, "L9"), {"L8": 520}),
}
ABOUT = (
    "SYNTHETIC packing list for software testing and the Civil Buddy façade demo.",
    "Not a real shipment and not any contractor's data: panels, marks, weights and notes are invented.",
    "The packing engine reads the 'materials' sheet only. See examples/facade-demo/README.md.",
)


# the mixed list: (mark, description, qty, length, width, depth mm, unit kg, remarks). Corner panels are L-shaped; the
# sizes given are the bounding box the crate has to take. Everything here is invented.
GLASS = "glass, fragile, upright on A-frame, do not stack"
MIXED = (
    *[(f"UCW-E1-{f}", f"Typical unitised panel, east elevation {f} (SYNTHETIC)", 5, 4200, 1500, 250, 450, GLASS) for f in FLOORS],
    *[(f"UCW-C1-{f}", f"Corner unitised panel, NE corner {f}, L-shaped 1500 + 600 (SYNTHETIC)", 1, 4200, 1500, 600, 540, GLASS)
      for f in FLOORS],
    ("SP-E1", "Spandrel panel, opaque back-painted glass, L5-L8 (SYNTHETIC)", 8, 4200, 1200, 200, 300, GLASS),
    ("BRK-01", "Bracket crate: steel brackets and 4 m anchor channels in a timber crate (SYNTHETIC)", 2, 4000, 1000, 800, 520,
     "steel, stackable, fork-lift"),
)
# The bracket crate is 4 m long because it carries the anchor channels. A short crate (1200 x 1000 x 800) beside the
# 4.35 m panel crates is where the loader is weak today: it then places 2 crates per 40HQ instead of 4 and the
# plan does not fit (this list with BRK-01 at 1200 x 1000 x 800: 9 x 40HQ hold 19 of 33 crates, measured 2026-09-26).
# That is the loader, not the reading.
# At 850 kg a bracket crate is over the 4 m frame's net limit and the engine splits it in two by mass (a calculation
# split, reported in pack-plan.md); 520 kg keeps it one crate.
MIXED_TITLE = ("SYNTHETIC FACADE DELIVERY SCHEDULE - Harbourline Facade Pte. Ltd. (DEMO)",
               "Synthetic office tower, east elevation L5-L8, rev A - invented for software testing, not a real shipment")
MIXED_HEAD = ["Mark", "Description", "Qty", "Length (mm)", "Width (mm)", "Depth (mm)", "Unit Wt (kg)", "Total Wt (kg)", "Remarks"]


def mixed() -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Panel Schedule"
    for line in MIXED_TITLE:
        ws.append([line])
    ws.append([])
    ws.append(MIXED_HEAD)
    pieces = kg = 0
    for mark, text, qty, length, width, depth, each, remark in MIXED:
        ws.append([mark, text, qty, length, width, depth, each, qty * each, remark])
        pieces, kg = pieces + qty, kg + qty * each
    ws.append(["TOTAL", None, pieces, None, None, None, None, kg, None])
    about = wb.create_sheet("README")
    for line in (*ABOUT[:2], "There is no 'materials' sheet: the packing engine reads the first sheet, finds the header "
                 "under the title block and skips the TOTAL row. See examples/facade-demo/README.md."):
        about.append([line])
    wb.properties.title = "SYNTHETIC mixed façade delivery"
    wb.properties.subject = ABOUT[1]
    wb.save(HERE / "facade_panels_mixed.xlsx")
    print("wrote facade_panels_mixed.xlsx", "pieces", pieces, "kg", kg)


def main(names=None) -> None:
    if not names or "facade_panels_mixed" in names:
        mixed()
    for name, (label, note, floors, kg) in LISTS.items():
        if names and name not in names:
            continue
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "materials"
        ws.append(HEAD)
        total = 0
        for i, floor in enumerate(floors, 1):
            each = kg.get(floor, KG)
            total += PER_FLOOR * each
            ws.append([f"P{i:02d}", label.format(floor=floor), PER_FLOOR, each, PER_FLOOR * each, *DIMS, note])
        about = wb.create_sheet("README")
        for line in ABOUT:
            about.append([line])
        wb.properties.title = "SYNTHETIC façade panel list"
        wb.properties.subject = ABOUT[1]
        wb.save(HERE / f"{name}.xlsx")
        print("wrote", f"{name}.xlsx", "pieces", PER_FLOOR * len(floors), "kg", total)


if __name__ == "__main__":
    main(sys.argv[1:])
