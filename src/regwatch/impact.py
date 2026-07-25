"""From "the text changed" to "these people have work to do".

The output of a diff is a fact about a document. The output of this module is a
work list: which controls relied on the amended provisions, who owns them, and
which changes landed on nothing at all.

That last category is the one worth building the tool for. A material change to
a provision **no control claims** is either a genuine gap in the register or a
provision the institution is not in scope for — and nobody can tell which from
outside the compliance function. Reporting it as `unmapped` rather than
silently dropping it is the difference between a report that surfaces the gap
and a report that hides it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from .diff import Change, DiffResult, Materiality
from .register import Control, Register


@dataclass
class ControlImpact:
    """One control, and every change that landed on it."""

    control: Control
    changes: list[Change] = field(default_factory=list)

    @property
    def highest(self) -> Materiality:
        return max(
            (c.materiality for c in self.changes),
            key=lambda m: m.rank,
            default=Materiality.UNKNOWN,
        )

    @property
    def needs_review(self) -> bool:
        return self.highest.rank >= Materiality.SUBSTANTIVE.rank

    def to_dict(self) -> dict[str, Any]:
        return {
            "control": self.control.id,
            "title": self.control.title,
            "owner": self.control.owner,
            "highest_materiality": self.highest.value,
            "needs_review": self.needs_review,
            "changes": [c.path for c in self.changes],
        }


@dataclass
class Assessment:
    """What a change round means for the control estate."""

    instrument: str
    from_version: str
    to_version: str
    impacts: list[ControlImpact] = field(default_factory=list)
    unmapped: list[Change] = field(default_factory=list)
    editorial_only: int = 0

    @property
    def needing_review(self) -> list[ControlImpact]:
        return [i for i in self.impacts if i.needs_review]

    @property
    def owners(self) -> dict[str, list[str]]:
        """Owner -> the controls of theirs that need review."""
        out: dict[str, list[str]] = {}
        for impact in self.needing_review:
            owner = impact.control.owner or "unassigned"
            out.setdefault(owner, []).append(impact.control.id)
        return {owner: sorted(ids) for owner, ids in sorted(out.items())}

    @property
    def unmapped_material(self) -> list[Change]:
        """Material changes no control claims. The finding, not a footnote."""
        return [c for c in self.unmapped if c.materiality.rank >= Materiality.SUBSTANTIVE.rank]

    def to_dict(self) -> dict[str, Any]:
        return {
            "instrument": self.instrument,
            "from_version": self.from_version,
            "to_version": self.to_version,
            "controls_affected": len(self.impacts),
            "controls_needing_review": len(self.needing_review),
            "unmapped_changes": len(self.unmapped),
            "unmapped_material": len(self.unmapped_material),
            "editorial_only": self.editorial_only,
            "impacts": [i.to_dict() for i in self.impacts],
            "unmapped": [c.to_dict() for c in self.unmapped_material],
            "owners": self.owners,
        }


def assess(result: DiffResult, register: Register) -> Assessment:
    """Map a diff onto the control register."""
    instrument = result.after.identifier or result.before.identifier
    assessment = Assessment(
        instrument=instrument,
        from_version=result.before.version,
        to_version=result.after.version,
    )

    by_control: dict[str, ControlImpact] = {}
    for change in result.changes:
        if change.materiality is Materiality.EDITORIAL:
            assessment.editorial_only += 1

        # A renumbering does not change what a control has to do, but it does
        # invalidate the citation in the control's own documentation - which is
        # what an examiner reads first. So it is attributed, at editorial
        # weight, rather than dropped.
        lookup = change.previous_path or change.path
        affected = register.affected_by(lookup, instrument)
        if not affected:
            assessment.unmapped.append(change)
            continue

        for control in affected:
            impact = by_control.setdefault(control.id, ControlImpact(control=control))
            impact.changes.append(change)

    assessment.impacts = sorted(
        by_control.values(),
        key=lambda i: (-i.highest.rank, -len(i.changes), i.control.id),
    )
    return assessment


@dataclass
class CoverageReport:
    """What the register does and does not claim, before anything changes.

    Worth running on its own schedule. Every finding here is one you would
    otherwise discover during a change round, at the point where it is a
    surprise rather than a task.
    """

    instrument: str
    total_provisions: int
    covered: int
    uncovered: list[str] = field(default_factory=list)
    dangling: list[tuple[str, str]] = field(default_factory=list)
    unowned: list[Control] = field(default_factory=list)
    stale: list[Control] = field(default_factory=list)

    @property
    def coverage(self) -> float:
        return self.covered / self.total_provisions if self.total_provisions else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "instrument": self.instrument,
            "provisions": self.total_provisions,
            "covered": self.covered,
            "coverage": round(self.coverage, 4),
            "uncovered": self.uncovered,
            "dangling": [{"control": c, "provision": p} for c, p in self.dangling],
            "unowned": [c.id for c in self.unowned],
            "stale": [c.id for c in self.stale],
        }


def coverage(document, register: Register, *, stale_after: int = 365,
             today: date | None = None) -> CoverageReport:
    """Which provisions of an instrument no control claims, and vice versa."""
    instrument = document.identifier
    controls = register.for_instrument(instrument)

    # Only provisions carrying text are claimable; a chapter heading is not an
    # obligation and counting it would inflate the denominator.
    provisions = [n.path for n in document.nodes() if n.body and n.kind != "article"]
    provisions += [n.path for n in document.nodes() if n.kind == "article" and n.body]

    covered, uncovered = 0, []
    for path in sorted(set(provisions)):
        if any(control.covers(path) for control in controls):
            covered += 1
        else:
            uncovered.append(path)

    # A control citing a provision that does not exist is worse than an
    # uncovered provision: it reads as compliance and is not.
    known = {n.path for n in document.nodes()}
    dangling = []
    for control in controls:
        for provision in control.provisions:
            from .register import normalise_citation
            if normalise_citation(provision) not in known:
                dangling.append((control.id, provision))

    return CoverageReport(
        instrument=instrument,
        total_provisions=len(set(provisions)),
        covered=covered,
        uncovered=uncovered,
        dangling=dangling,
        unowned=[c for c in controls if not c.is_owned],
        stale=[c for c in register.stale(stale_after, today) if c.instrument == instrument],
    )
