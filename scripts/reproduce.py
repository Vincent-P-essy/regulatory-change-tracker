"""Regenerate every figure quoted in the README, and fail if any of them moved.

Run in CI. A README that says four material changes land on no control should
stop being merged the moment that stops being true.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

from regwatch.diff import ChangeType, diff
from regwatch.document import load as load_document
from regwatch.impact import assess, coverage
from regwatch.register import load as load_register

ROOT = Path(__file__).resolve().parent.parent
EXPECTED = ROOT / "results" / "expected.json"

#: Fixed so the staleness figures do not drift with the wall clock.
TODAY = date(2026, 7, 25)


def measure() -> dict:
    before = load_document(ROOT / "sources" / "dora" / "2023-01-16.txt")
    after = load_document(ROOT / "sources" / "dora" / "2026-06-30.txt")
    register = load_register(ROOT / "register" / "controls.yaml")

    result = diff(before, after)
    assessment = assess(result, register)
    report = coverage(after, register, today=TODAY)

    return {
        "instrument": result.after.identifier,
        "versions": [before.version, after.version],
        "provisions": {
            "before": len(before.nodes()),
            "after": len(after.nodes()),
        },
        "diff": {
            "changes": len(result.changes),
            "unchanged": result.unchanged,
            "by_materiality": result.by_materiality(),
            "by_type": {
                t.value: len([c for c in result.changes if c.change_type is t])
                for t in ChangeType
            },
            "material_paths": sorted(c.path for c in result.material),
            "renumbered": sorted(
                f"{c.previous_path} -> {c.path}"
                for c in result.changes
                if c.change_type is ChangeType.RENUMBERED
            ),
        },
        "impact": {
            "controls": len(register),
            "needing_review": sorted(i.control.id for i in assessment.needing_review),
            "unmapped_material": sorted(c.path for c in assessment.unmapped_material),
            "owners": assessment.owners,
        },
        "coverage": {
            "provisions": report.total_provisions,
            "covered": report.covered,
            "coverage": round(report.coverage, 4),
            "dangling": sorted(f"{c}:{p}" for c, p in report.dangling),
            "unowned": sorted(c.id for c in report.unowned),
            "stale_365": sorted(c.id for c in report.stale),
        },
    }


def main() -> int:
    measured = measure()
    EXPECTED.parent.mkdir(parents=True, exist_ok=True)

    if "--write" in sys.argv or not EXPECTED.exists():
        EXPECTED.write_text(json.dumps(measured, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {EXPECTED.relative_to(ROOT)}")
        return 0

    expected = json.loads(EXPECTED.read_text(encoding="utf-8"))
    if measured == expected:
        d, i, c = measured["diff"], measured["impact"], measured["coverage"]
        print(
            f"reproduced: {d['changes']} changes "
            f"({d['by_materiality']['obligation']} obligation, "
            f"{d['by_materiality']['substantive']} substantive, "
            f"{d['by_materiality']['editorial']} editorial), "
            f"{len(i['needing_review'])} controls to review, "
            f"{len(i['unmapped_material'])} material change(s) mapped to nothing, "
            f"register coverage {c['coverage']:.0%}"
        )
        return 0

    print("figures moved — the README is now wrong about at least one of these:\n")
    for key in sorted(set(measured) | set(expected)):
        if measured.get(key) != expected.get(key):
            print(f"  {key}")
            print(f"    expected {json.dumps(expected.get(key))}")
            print(f"    measured {json.dumps(measured.get(key))}")
    print("\nRe-run with --write once you have updated the README.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
