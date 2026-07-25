"""Structural diff, and the distinction that makes it worth having.

Matching by path alone is wrong in the one case that matters most. Insert a
paragraph into Article 11 and every paragraph below it shifts: 11(4) becomes
11(5), 11(5) becomes 11(6), and a path-keyed diff reports the entire tail of
the article as modified. The reviewer sees fifteen changes, finds fourteen of
them identical to what they already read, and stops reading the fifteenth.

So matching happens in three passes, and **the order is the design**. Text
first: identical content is paired wherever it now sits, so a provision that
merely moved is reported as a renumbering rather than as a deletion plus an
addition. Only then by address, which catches text genuinely edited in place.
What survives both is genuinely new or genuinely gone.

Doing those two the other way round — the obvious way, matching paths first —
defeats the entire exercise. Insert a point (d) into a list and the old (d)
becomes (e). Path-first pairs the *new* (d) against the *old* (d) and calls it
an edit, then finds the old text sitting at (e) and calls that an addition.
Both statements are false, and the one real finding — a new obligation — is
buried under them.

Which of the real changes matter is not a diff question. `classify()` answers
it from the deontic verbs, the deadlines and the frequencies, because in
regulatory drafting those are where obligations live.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from .document import Document, Node


class ChangeType(str, enum.Enum):
    ADDED = "added"
    REMOVED = "removed"
    MODIFIED = "modified"
    RENUMBERED = "renumbered"


class Materiality(str, enum.Enum):
    """How much attention a change needs.

    Deliberately not a risk score. Whether a change is risky depends on what
    the institution does, which this tool does not know; whether it creates,
    removes or moves an obligation is visible in the text, and that is what
    this grades.
    """

    OBLIGATION = "obligation"     # a duty created, removed or widened
    SUBSTANTIVE = "substantive"   # the requirement changed in a way that alters conduct
    EDITORIAL = "editorial"       # wording, cross-references, renumbering
    UNKNOWN = "unknown"

    @property
    def rank(self) -> int:
        return {"obligation": 3, "substantive": 2, "editorial": 1, "unknown": 0}[self.value]


#: Deontic verbs. In EU legislative drafting these are not stylistic choices:
#: `shall` creates a duty, `may` creates a discretion, `should` (in recitals)
#: creates neither. A tracker that treats them as synonyms cannot tell a new
#: obligation from a clarification.
BINDING = re.compile(
    r"\b(shall|must|is required to|are required to|shall not|must not)\b", re.IGNORECASE
)
DISCRETIONARY = re.compile(r"\b(may|should|is encouraged to|where appropriate)\b", re.IGNORECASE)
#: Deadlines, periods and thresholds. A change from 72 to 24 hours is the whole
#: content of the amendment.
QUANTITY = re.compile(
    r"\b(\d+(?:[.,]\d+)?)\s*(hours?|days?|weeks?|months?|years?|%|per\s?cent|EUR|million|billion)\b",
    re.IGNORECASE,
)
#: Frequencies written in words, which is how legislative drafting usually
#: writes them. "at least once a year" becoming "at least twice a year" doubles
#: an institution's testing obligation and contains no digits at all — a
#: numeric-only detector grades it editorial on a 98% text similarity, which is
#: the most expensive kind of false negative this tool can produce.
FREQUENCY = re.compile(
    r"\b(once|twice|three times|four times|annually|biannually|quarterly|monthly|"
    r"weekly|daily|continuously|without undue delay|immediately)\b",
    re.IGNORECASE,
)
#: `Article 5(2)`, `point (c)`, `Regulation (EU) 2022/2554`
CROSS_REFERENCE = re.compile(
    r"\b(Article\s+\d+[a-z]?(\(\d+\))?(\([a-z]+\))?|point\s+\(\w+\)|"
    r"Regulation\s+\(EU\)\s+\d+/\d+|Directive\s+\(EU\)\s+\d+/\d+)",
    re.IGNORECASE,
)


@dataclass
class Change:
    """One difference between two versions of an instrument."""

    change_type: ChangeType
    path: str
    materiality: Materiality = Materiality.UNKNOWN
    before: str = ""
    after: str = ""
    previous_path: str = ""
    reasons: list[str] = field(default_factory=list)
    similarity: float = 0.0

    @property
    def heading(self) -> str:
        if self.change_type is ChangeType.RENUMBERED:
            return f"{self.previous_path} → {self.path}"
        return self.path

    @property
    def obligations_added(self) -> int:
        return max(0, len(BINDING.findall(self.after)) - len(BINDING.findall(self.before)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.change_type.value,
            "path": self.path,
            "previous_path": self.previous_path,
            "materiality": self.materiality.value,
            "similarity": round(self.similarity, 3),
            "reasons": self.reasons,
            "before": self.before,
            "after": self.after,
        }


def _normalise(text: str) -> str:
    return " ".join(text.split())


def _shorten(text: str, limit: int = 60) -> str:
    text = _normalise(text)
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _chapeau(node: Node) -> str:
    """The introductory text a point hangs from, empty for anything else."""
    if node.kind == "point" and node.parent is not None:
        return node.parent.body
    return ""


def classify(
    before: str, after: str, change_type: ChangeType, *, chapeau: str = ""
) -> tuple[Materiality, list[str]]:
    """Decide how much attention a change needs, and say why.

    `chapeau` is the introductory text a list of points hangs from. It matters
    because EU drafting puts the deontic verb there and not in the points:

        3. The ICT-related incident management process **shall**:
        (a) put in place early warning indicators;
        (b) establish procedures to identify, track, log ...

    Read in isolation, point (b) contains no binding term and grades as a
    clarification. Read with its chapeau it is a duty. Since lists are where
    the operational detail of an instrument lives, a classifier that ignores
    the chapeau systematically under-grades exactly the provisions a compliance
    function most needs to see.

    Every branch returns a sentence. A materiality grade a reviewer cannot
    interrogate is one they will overrule on instinct, and then the tool has
    added a step without adding a judgement.
    """
    reasons: list[str] = []

    if change_type is ChangeType.RENUMBERED:
        return Materiality.EDITORIAL, ["the text is unchanged; only its address moved"]

    before_binding = len(BINDING.findall(before))
    after_binding = len(BINDING.findall(after))

    if change_type is ChangeType.ADDED:
        if after_binding:
            verbs = sorted({m.lower() for m in BINDING.findall(after)})
            return Materiality.OBLIGATION, [
                f"new provision containing {after_binding} binding term(s): {', '.join(verbs)}"
            ]
        if chapeau and BINDING.search(chapeau):
            verbs = sorted({m.lower() for m in BINDING.findall(chapeau)})
            return Materiality.OBLIGATION, [
                f"new item in a list governed by {', '.join(verbs)}: "
                f"“{_shorten(chapeau)}”"
            ]
        if QUANTITY.search(after) or FREQUENCY.search(after):
            found = [f"{v} {u}" for v, u in QUANTITY.findall(after)]
            found += [m for m in FREQUENCY.findall(after)]
            return Materiality.OBLIGATION, [
                f"new provision setting a deadline or frequency: {', '.join(found)}"
            ]
        if DISCRETIONARY.search(after):
            return Materiality.SUBSTANTIVE, [
                "new provision, but framed as a discretion rather than a duty"
            ]
        return Materiality.SUBSTANTIVE, ["new provision with no binding term"]

    if change_type is ChangeType.REMOVED:
        if before_binding:
            return Materiality.OBLIGATION, [
                f"provision removed, withdrawing {before_binding} binding term(s)"
            ]
        if chapeau and BINDING.search(chapeau):
            return Materiality.OBLIGATION, [
                "item removed from a list that carries a binding term"
            ]
        return Materiality.SUBSTANTIVE, ["provision removed"]

    # -- modified -------------------------------------------------------------
    if after_binding > before_binding:
        reasons.append(
            f"binding terms increased from {before_binding} to {after_binding}"
        )
    elif after_binding < before_binding:
        reasons.append(
            f"binding terms decreased from {before_binding} to {after_binding}"
        )

    before_quantities = {(v.replace(",", "."), u.lower()) for v, u in QUANTITY.findall(before)}
    after_quantities = {(v.replace(",", "."), u.lower()) for v, u in QUANTITY.findall(after)}
    if before_quantities != after_quantities:
        gone = before_quantities - after_quantities
        arrived = after_quantities - before_quantities
        if gone and arrived:
            reasons.append(
                f"quantity changed: {', '.join(f'{v} {u}' for v, u in sorted(gone))} → "
                f"{', '.join(f'{v} {u}' for v, u in sorted(arrived))}"
            )
        elif arrived:
            reasons.append(
                f"quantity introduced: {', '.join(f'{v} {u}' for v, u in sorted(arrived))}"
            )
        elif gone:
            reasons.append(
                f"quantity removed: {', '.join(f'{v} {u}' for v, u in sorted(gone))}"
            )

    before_frequency = {m.lower() for m in FREQUENCY.findall(before)}
    after_frequency = {m.lower() for m in FREQUENCY.findall(after)}
    if before_frequency != after_frequency:
        gone = before_frequency - after_frequency
        arrived = after_frequency - before_frequency
        if gone and arrived:
            reasons.append(
                f"frequency changed: {', '.join(sorted(gone))} → {', '.join(sorted(arrived))}"
            )
        elif arrived:
            reasons.append(f"frequency introduced: {', '.join(sorted(arrived))}")
        else:
            reasons.append(f"frequency removed: {', '.join(sorted(gone))}")

    if reasons:
        return Materiality.OBLIGATION, reasons

    before_references = set(m[0] for m in CROSS_REFERENCE.findall(before))
    after_references = set(m[0] for m in CROSS_REFERENCE.findall(after))
    if before_references != after_references:
        reasons.append(
            "cross-references changed: "
            + ", ".join(sorted(after_references ^ before_references))
        )

    similarity = SequenceMatcher(None, _normalise(before), _normalise(after)).ratio()
    if similarity >= 0.95:
        reasons.append(f"wording changed with {similarity:.0%} of the text identical")
        return Materiality.EDITORIAL, reasons

    reasons.append(f"wording changed; {similarity:.0%} of the text is identical")
    return Materiality.SUBSTANTIVE, reasons


@dataclass
class DiffResult:
    before: Document
    after: Document
    changes: list[Change] = field(default_factory=list)
    unchanged: int = 0

    @property
    def material(self) -> list[Change]:
        """Everything above editorial. The list a reviewer should actually read."""
        return [c for c in self.changes if c.materiality.rank >= Materiality.SUBSTANTIVE.rank]

    def by_materiality(self) -> dict[str, int]:
        counts = {m.value: 0 for m in Materiality}
        for change in self.changes:
            counts[change.materiality.value] += 1
        return counts

    def to_dict(self) -> dict[str, Any]:
        return {
            "identifier": self.after.identifier,
            "from_version": self.before.version,
            "to_version": self.after.version,
            "nodes_before": len(self.before.nodes()),
            "nodes_after": len(self.after.nodes()),
            "unchanged": self.unchanged,
            "changes": [c.to_dict() for c in self.changes],
            "by_materiality": self.by_materiality(),
        }


def _leaves(document: Document) -> dict[str, Node]:
    """Nodes that carry text. Structural headings have no content to compare."""
    return {node.path: node for node in document.nodes() if node.body}


def diff(before: Document, after: Document) -> DiffResult:
    """Compare two versions of an instrument."""
    if before.identifier and after.identifier and before.identifier != after.identifier:
        raise ValueError(
            f"refusing to diff {before.identifier} against {after.identifier}; "
            "these are different instruments"
        )

    old = _leaves(before)
    new = _leaves(after)
    result = DiffResult(before=before, after=after)

    matched_old: set[str] = set()
    matched_new: set[str] = set()

    # -- pass 1: identical text, wherever it now sits -------------------------
    # Text first, address second. Doing it the other way round defeats the
    # whole exercise: insert a point (d) into a list and the old (d) becomes
    # (e), so a path-first match pairs the *new* (d) against the *old* (d),
    # reports it as an edit, and then reports the old text at (e) as an
    # addition. Both statements are false, and the one real finding — a new
    # obligation — is buried in the noise.
    by_digest: dict[str, list[str]] = {}
    for path, node in sorted(old.items()):
        by_digest.setdefault(node.digest, []).append(path)

    for path, node in sorted(new.items()):
        candidates = by_digest.get(node.digest)
        if not candidates:
            continue
        # Prefer the same address when the text is unchanged there, so an
        # unmoved provision is never reported as a renumbering of itself.
        previous = path if path in candidates else candidates[0]
        candidates.remove(previous)
        matched_old.add(previous)
        matched_new.add(path)
        if previous == path:
            result.unchanged += 1
            continue
        result.changes.append(Change(
            change_type=ChangeType.RENUMBERED,
            path=path,
            previous_path=previous,
            materiality=Materiality.EDITORIAL,
            before=node.body,
            after=node.body,
            reasons=[f"the text is unchanged; it moved from {previous}"],
            similarity=1.0,
        ))

    # -- pass 2: same address, edited text ------------------------------------
    for path in sorted(old.keys() & new.keys()):
        if path in matched_old or path in matched_new:
            continue
        matched_old.add(path)
        matched_new.add(path)
        old_text, new_text = old[path].body, new[path].body
        materiality, reasons = classify(
            old_text, new_text, ChangeType.MODIFIED, chapeau=_chapeau(new[path])
        )
        result.changes.append(Change(
            change_type=ChangeType.MODIFIED,
            path=path,
            materiality=materiality,
            before=old_text,
            after=new_text,
            reasons=reasons,
            similarity=SequenceMatcher(
                None, _normalise(old_text), _normalise(new_text)
            ).ratio(),
        ))

    # -- pass 3: what is left is genuinely new or gone ------------------------
    for path, node in sorted(new.items()):
        if path in matched_new:
            continue
        materiality, reasons = classify(
            "", node.body, ChangeType.ADDED, chapeau=_chapeau(node)
        )
        result.changes.append(Change(
            change_type=ChangeType.ADDED, path=path, materiality=materiality,
            after=node.body, reasons=reasons,
        ))

    for path, node in sorted(old.items()):
        if path in matched_old:
            continue
        materiality, reasons = classify(
            node.body, "", ChangeType.REMOVED, chapeau=_chapeau(node)
        )
        result.changes.append(Change(
            change_type=ChangeType.REMOVED, path=path, materiality=materiality,
            before=node.body, reasons=reasons,
        ))

    order = {
        ChangeType.ADDED: 0, ChangeType.MODIFIED: 1,
        ChangeType.REMOVED: 2, ChangeType.RENUMBERED: 3,
    }
    result.changes.sort(key=lambda c: (-c.materiality.rank, order[c.change_type], c.path))
    return result
