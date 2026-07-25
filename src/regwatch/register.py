"""The internal control register, and the mapping that makes a diff actionable.

A change to Article 11(4) is a fact about a document. What a compliance
function needs is the next sentence: *which of our controls relied on that
paragraph, who owns them, and when were they last reviewed.*

The mapping is explicit — each control names the provisions it implements — and
it is explicit for a reason. Keyword matching between regulatory text and
control descriptions looks like it works on a demo and fails in the direction
that costs the most: it silently misses the control nobody wrote in the
regulator's vocabulary. An unmapped control is visible in `regwatch coverage`;
a control a fuzzy matcher failed to surface is invisible until an examination.

The tool therefore reports **which controls need review**, never which controls
are non-compliant. Whether an amended provision breaks a control is a judgement
about the control's design, and no diff can make it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml


class RegisterError(ValueError):
    """Raised when the register cannot be loaded. Always names the control."""


#: `Article 11(4)(c)` in any of the forms people actually write it.
_CITATION = re.compile(
    r"Article\s+(?P<article>\d+[a-z]?)"
    r"(?:\s*\((?P<paragraph>\d+)\))?"
    r"(?:\s*\((?P<point>[a-z]{1,2})\))?",
    re.IGNORECASE,
)


def normalise_citation(text: str) -> str:
    """Turn a written citation into the canonical path form.

    `art. 11 (4)(c)`, `Article 11(4)(c)` and `ARTICLE 11 (4) (C)` are the same
    provision, and a register that treats them as three is a register whose
    coverage report is wrong.
    """
    match = _CITATION.search(text.replace("art.", "Article").replace("Art.", "Article"))
    if not match:
        return text.strip()
    out = f"Article {match.group('article')}"
    if match.group("paragraph"):
        out += f"({match.group('paragraph')})"
    if match.group("point"):
        out += f"({match.group('point').lower()})"
    return out


@dataclass
class Control:
    """One internal control, and the provisions it exists to satisfy."""

    id: str
    title: str
    owner: str
    instrument: str
    provisions: list[str] = field(default_factory=list)
    last_reviewed: date | None = None
    description: str = ""
    evidence: str = ""

    @property
    def is_owned(self) -> bool:
        return bool(self.owner) and self.owner.lower() not in ("tbc", "unassigned", "unknown")

    def days_since_review(self, today: date | None = None) -> int | None:
        if self.last_reviewed is None:
            return None
        return ((today or date.today()) - self.last_reviewed).days

    def covers(self, path: str) -> bool:
        """Whether this control claims the provision, or a parent of it.

        A control mapped to `Article 11` is affected by a change to
        `Article 11(4)(c)`. The reverse does not hold: a control mapped to the
        specific point is not implicated by a change to a different point of
        the same article, and treating it as though it were is how a change
        report becomes a list of everything.
        """
        target = normalise_citation(path)
        for provision in self.provisions:
            claimed = normalise_citation(provision)
            if target == claimed or target.startswith(claimed + "("):
                return True
        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "owner": self.owner,
            "instrument": self.instrument,
            "provisions": self.provisions,
            "last_reviewed": self.last_reviewed.isoformat() if self.last_reviewed else None,
            "description": self.description,
            "evidence": self.evidence,
        }


@dataclass
class Register:
    controls: list[Control] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.controls)

    def for_instrument(self, identifier: str) -> list[Control]:
        return [c for c in self.controls if c.instrument == identifier]

    def get(self, control_id: str) -> Control | None:
        return next((c for c in self.controls if c.id == control_id), None)

    def affected_by(self, path: str, instrument: str) -> list[Control]:
        return [c for c in self.for_instrument(instrument) if c.covers(path)]

    def unowned(self) -> list[Control]:
        return [c for c in self.controls if not c.is_owned]

    def stale(self, days: int, today: date | None = None) -> list[Control]:
        reference = today or date.today()
        out = []
        for control in self.controls:
            if control.last_reviewed is None or (reference - control.last_reviewed).days > days:
                out.append(control)
        return out

    def to_dict(self) -> dict[str, Any]:
        return {"controls": [c.to_dict() for c in self.controls]}


def _as_date(value: Any, control_id: str) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, datetime):
        return value.date()
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except ValueError:
        raise RegisterError(
            f"control {control_id}: last_reviewed {value!r} is not an ISO date"
        ) from None


def load(path: str | Path) -> Register:
    source = Path(path)
    if not source.exists():
        raise RegisterError(f"control register not found: {source}")

    try:
        raw = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise RegisterError(f"{source} is not valid YAML: {exc}") from None

    entries = raw.get("controls")
    if not isinstance(entries, list):
        raise RegisterError(f"{source} has no `controls:` list")

    register = Register()
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise RegisterError(f"control #{index + 1} is not a mapping")
        control_id = str(entry.get("id", "")).strip()
        if not control_id:
            raise RegisterError(f"control #{index + 1} has no id")
        if control_id in seen:
            raise RegisterError(f"duplicate control id {control_id!r}")
        seen.add(control_id)

        for required in ("title", "instrument"):
            if not entry.get(required):
                raise RegisterError(f"control {control_id} has no {required}")

        provisions = entry.get("provisions") or []
        if isinstance(provisions, str):
            provisions = [provisions]

        register.controls.append(Control(
            id=control_id,
            title=str(entry["title"]).strip(),
            owner=str(entry.get("owner", "")).strip(),
            instrument=str(entry["instrument"]).strip(),
            provisions=[str(p).strip() for p in provisions],
            last_reviewed=_as_date(entry.get("last_reviewed"), control_id),
            description=str(entry.get("description", "")).strip(),
            evidence=str(entry.get("evidence", "")).strip(),
        ))
    return register
