"""Diff and materiality tests.

The first section is the one that matters: it pins the ordering of the matching
passes, which is the whole design. If someone "simplifies" the diff to match
paths first, these fail.
"""

from __future__ import annotations

import pytest

from regwatch.diff import ChangeType, Materiality, classify, diff
from regwatch.document import load, parse


def document(body: str, version: str = "v1") -> object:
    return parse(f"Identifier: TEST\nVersion: {version}\n\n{body}")


BEFORE_LIST = """\
Article 17 Incidents
1. The process shall:
(a) put in place early warning indicators;
(b) establish procedures to classify incidents;
(c) set out plans for communication to staff and media.
"""

AFTER_LIST = """\
Article 17 Incidents
1. The process shall:
(a) put in place early warning indicators;
(b) establish procedures to classify incidents;
(c) notify the management body within 2 hours of classification;
(d) set out plans for communication to staff and media.
"""


# -- insertion into a numbered list ---------------------------------------------


def test_an_inserted_point_is_one_addition_and_one_renumbering():
    """The case the design exists for. Matching paths first instead would call
    the new (c) an edit of the old (c) and the old text at (d) an addition —
    two false statements that bury the one real finding."""
    result = diff(document(BEFORE_LIST), document(AFTER_LIST, "v2"))
    kinds = [(c.change_type, c.path) for c in result.changes]
    assert (ChangeType.ADDED, "Article 17(1)(c)") in kinds
    assert (ChangeType.RENUMBERED, "Article 17(1)(d)") in kinds
    assert len(result.changes) == 2


def test_the_renumbered_point_records_where_it_came_from():
    result = diff(document(BEFORE_LIST), document(AFTER_LIST, "v2"))
    moved = next(c for c in result.changes if c.change_type is ChangeType.RENUMBERED)
    assert moved.previous_path == "Article 17(1)(c)"
    assert moved.heading == "Article 17(1)(c) → Article 17(1)(d)"


def test_nothing_is_reported_as_modified_when_text_merely_moved():
    result = diff(document(BEFORE_LIST), document(AFTER_LIST, "v2"))
    assert not [c for c in result.changes if c.change_type is ChangeType.MODIFIED]


def test_a_renumbering_is_editorial_not_substantive():
    result = diff(document(BEFORE_LIST), document(AFTER_LIST, "v2"))
    moved = next(c for c in result.changes if c.change_type is ChangeType.RENUMBERED)
    assert moved.materiality is Materiality.EDITORIAL
    assert moved not in result.material


def test_an_unmoved_provision_is_not_reported_as_a_renumbering_of_itself():
    result = diff(document(BEFORE_LIST), document(AFTER_LIST, "v2"))
    unchanged_paths = {c.path for c in result.changes}
    assert "Article 17(1)(a)" not in unchanged_paths
    assert result.unchanged == 3


# -- editing in place -----------------------------------------------------------


def test_text_edited_at_the_same_address_is_a_modification():
    before = document("Article 1 H\n1. Entities shall report within 72 hours.\n")
    after = document("Article 1 H\n1. Entities shall report within 24 hours.\n", "v2")
    (change,) = diff(before, after).changes
    assert change.change_type is ChangeType.MODIFIED
    assert change.path == "Article 1(1)"


def test_identical_documents_produce_no_changes():
    result = diff(document(BEFORE_LIST), document(BEFORE_LIST, "v2"))
    assert result.changes == []
    assert result.unchanged == 4


def test_diffing_two_different_instruments_is_refused():
    a = parse("Identifier: EU 2022/2554\n\nArticle 1 H\n1. Text.\n")
    b = parse("Identifier: EU 2022/2555\n\nArticle 1 H\n1. Text.\n")
    with pytest.raises(ValueError, match="different instruments"):
        diff(a, b)


# -- materiality ----------------------------------------------------------------


def test_a_new_provision_with_shall_is_an_obligation():
    materiality, reasons = classify("", "Entities shall do a thing.", ChangeType.ADDED)
    assert materiality is Materiality.OBLIGATION
    assert "shall" in reasons[0]


def test_a_new_provision_with_may_is_not_an_obligation():
    materiality, _ = classify("", "Entities may do a thing.", ChangeType.ADDED)
    assert materiality is Materiality.SUBSTANTIVE


def test_a_changed_deadline_is_an_obligation():
    materiality, reasons = classify(
        "Report within 72 hours.", "Report within 24 hours.", ChangeType.MODIFIED
    )
    assert materiality is Materiality.OBLIGATION
    assert "72 hours" in reasons[0] and "24 hours" in reasons[0]


def test_a_frequency_written_in_words_is_caught():
    """`at least once a year` to `at least twice a year` doubles a testing
    obligation and contains no digits. A numeric-only detector grades it
    editorial on a 98% similarity, which is the most expensive false negative
    this tool can produce."""
    materiality, reasons = classify(
        "Systems shall be scanned at least once a year.",
        "Systems shall be scanned at least twice a year.",
        ChangeType.MODIFIED,
    )
    assert materiality is Materiality.OBLIGATION
    assert "once" in reasons[0] and "twice" in reasons[0]


def test_a_list_item_inherits_the_binding_verb_from_its_chapeau():
    """EU drafting puts `shall` in the chapeau, not the points. Read alone,
    a point contains no binding term; read with its chapeau it is a duty, and
    lists are where the operational detail lives."""
    materiality, reasons = classify(
        "", "notify the management body of the incident;", ChangeType.ADDED,
        chapeau="The process shall:",
    )
    assert materiality is Materiality.OBLIGATION
    assert "list governed by shall" in reasons[0]


def test_a_list_item_without_a_binding_chapeau_is_not_promoted():
    materiality, _ = classify(
        "", "an example of a thing;", ChangeType.ADDED,
        chapeau="The following are examples:",
    )
    assert materiality is Materiality.SUBSTANTIVE


def test_the_chapeau_promotion_survives_a_real_diff():
    result = diff(document(BEFORE_LIST), document(AFTER_LIST, "v2"))
    added = next(c for c in result.changes if c.change_type is ChangeType.ADDED)
    assert added.materiality is Materiality.OBLIGATION


def test_a_pure_wording_change_is_editorial():
    materiality, _ = classify(
        "Entities shall have a sound and comprehensive framework in place.",
        "Entities shall have a sound and comprehensive framework in place today.",
        ChangeType.MODIFIED,
    )
    assert materiality is Materiality.EDITORIAL


def test_a_substantial_rewrite_is_substantive_not_editorial():
    materiality, reasons = classify(
        "Entities shall keep records of contracts.",
        "Entities shall keep records of contracts, including whether "
        "subcontracting of a critical function is permitted, and where.",
        ChangeType.MODIFIED,
    )
    assert materiality is Materiality.SUBSTANTIVE
    assert "identical" in reasons[-1]


def test_removing_a_binding_provision_is_an_obligation_change():
    materiality, reasons = classify("Entities shall do a thing.", "", ChangeType.REMOVED)
    assert materiality is Materiality.OBLIGATION
    assert "withdrawing" in reasons[0]


def test_materiality_ranks_are_ordered():
    assert Materiality.OBLIGATION.rank > Materiality.SUBSTANTIVE.rank
    assert Materiality.SUBSTANTIVE.rank > Materiality.EDITORIAL.rank


def test_material_excludes_editorial():
    result = diff(document(BEFORE_LIST), document(AFTER_LIST, "v2"))
    assert all(c.materiality is not Materiality.EDITORIAL for c in result.material)


# -- the bundled amendment ------------------------------------------------------


@pytest.fixture(scope="module")
def dora():
    return diff(load("sources/dora/2023-01-16.txt"), load("sources/dora/2026-06-30.txt"))


def test_the_bundled_amendment_produces_the_published_counts(dora):
    counts = dora.by_materiality()
    assert len(dora.changes) == 12
    assert counts["obligation"] == 10
    assert counts["substantive"] == 1
    assert counts["editorial"] == 1
    assert dora.unchanged == 31


def test_the_incident_notification_deadline_change_is_found(dora):
    change = next(c for c in dora.changes if c.path == "Article 19(2)")
    assert change.materiality is Materiality.OBLIGATION
    assert "4 hours" in change.reasons[0] and "2 hours" in change.reasons[0]


def test_the_scan_frequency_change_is_found(dora):
    change = next(c for c in dora.changes if c.path == "Article 25(1)")
    assert change.materiality is Materiality.OBLIGATION


def test_the_inserted_incident_point_and_its_renumbering_are_both_reported(dora):
    kinds = {(c.change_type, c.path) for c in dora.changes}
    assert (ChangeType.ADDED, "Article 17(3)(d)") in kinds
    assert (ChangeType.RENUMBERED, "Article 17(3)(e)") in kinds


def test_every_change_carries_at_least_one_reason(dora):
    for change in dora.changes:
        assert change.reasons
        assert all(len(r) > 10 for r in change.reasons)
