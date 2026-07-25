"""Register loading, citation matching, and mapping a diff onto controls."""

from __future__ import annotations

from datetime import date

import pytest

from regwatch.diff import Materiality, diff
from regwatch.document import load as load_document
from regwatch.impact import assess, coverage
from regwatch.register import Control, RegisterError, load, normalise_citation

TODAY = date(2026, 7, 25)


@pytest.fixture(scope="module")
def register():
    return load("register/controls.yaml")


@pytest.fixture(scope="module")
def versions():
    return (
        load_document("sources/dora/2023-01-16.txt"),
        load_document("sources/dora/2026-06-30.txt"),
    )


@pytest.fixture(scope="module")
def assessment(register, versions):
    return assess(diff(*versions), register)


# -- citation normalisation -----------------------------------------------------


@pytest.mark.parametrize("written", [
    "Article 11(4)(c)", "article 11 (4)(c)", "ARTICLE 11(4)(C)", "art. 11(4)(c)",
])
def test_citations_normalise_to_one_form(written):
    assert normalise_citation(written) == "Article 11(4)(c)"


def test_normalisation_keeps_the_levels_that_were_written():
    assert normalise_citation("Article 11") == "Article 11"
    assert normalise_citation("Article 11(4)") == "Article 11(4)"


def test_an_unparseable_citation_is_returned_as_written():
    assert normalise_citation("Annex I") == "Annex I"


# -- what a control covers ------------------------------------------------------


def test_a_control_on_an_article_covers_its_paragraphs():
    control = Control(id="C", title="t", owner="o", instrument="I",
                      provisions=["Article 17"])
    assert control.covers("Article 17(3)")
    assert control.covers("Article 17(3)(a)")


def test_a_control_on_a_point_does_not_cover_its_siblings():
    """Widening this to the whole article turns a change report into a list of
    everything, which is the same as no report."""
    control = Control(id="C", title="t", owner="o", instrument="I",
                      provisions=["Article 17(3)(a)"])
    assert control.covers("Article 17(3)(a)")
    assert not control.covers("Article 17(3)(b)")
    assert not control.covers("Article 17(2)")


def test_a_control_does_not_cover_a_different_article():
    control = Control(id="C", title="t", owner="o", instrument="I",
                      provisions=["Article 17"])
    assert not control.covers("Article 170(1)")


def test_ownership_treats_placeholders_as_unowned():
    for placeholder in ("TBC", "unassigned", "Unknown", ""):
        assert not Control(id="C", title="t", owner=placeholder, instrument="I").is_owned
    assert Control(id="C", title="t", owner="Head of Risk", instrument="I").is_owned


# -- loading --------------------------------------------------------------------


def test_the_bundled_register_loads(register):
    assert len(register) == 16
    assert register.get("ICT-INC-02") is not None


def test_a_duplicate_control_id_is_refused(tmp_path):
    path = tmp_path / "r.yaml"
    path.write_text(
        "controls:\n"
        "  - {id: A, title: t, instrument: I}\n"
        "  - {id: A, title: u, instrument: I}\n",
        encoding="utf-8",
    )
    with pytest.raises(RegisterError, match="duplicate control id"):
        load(path)


def test_a_control_without_a_title_names_itself(tmp_path):
    path = tmp_path / "r.yaml"
    path.write_text("controls:\n  - {id: A, instrument: I}\n", encoding="utf-8")
    with pytest.raises(RegisterError, match="A has no title"):
        load(path)


def test_a_bad_review_date_names_the_control(tmp_path):
    path = tmp_path / "r.yaml"
    path.write_text(
        "controls:\n  - {id: A, title: t, instrument: I, last_reviewed: 'last tuesday'}\n",
        encoding="utf-8",
    )
    with pytest.raises(RegisterError, match="A: last_reviewed"):
        load(path)


def test_a_register_with_no_controls_key_is_refused(tmp_path):
    path = tmp_path / "r.yaml"
    path.write_text("something: else\n", encoding="utf-8")
    with pytest.raises(RegisterError, match="no `controls:` list"):
        load(path)


def test_a_single_provision_may_be_written_as_a_string(tmp_path):
    path = tmp_path / "r.yaml"
    path.write_text(
        "controls:\n  - {id: A, title: t, instrument: I, provisions: 'Article 5'}\n",
        encoding="utf-8",
    )
    assert load(path).get("A").provisions == ["Article 5"]


# -- impact ---------------------------------------------------------------------


def test_the_amendment_lands_on_the_expected_controls(assessment):
    needing = {i.control.id for i in assessment.needing_review}
    assert needing == {
        "ICT-GOV-02", "ICT-GOV-04", "ICT-ACCESS-01",
        "ICT-INC-01", "ICT-INC-02", "ICT-TEST-02", "ICT-TPP-02",
    }


def test_the_reporting_deadline_change_reaches_the_reporting_control(assessment):
    impact = next(i for i in assessment.impacts if i.control.id == "ICT-INC-02")
    assert "Article 19(2)" in {c.path for c in impact.changes}
    assert impact.highest is Materiality.OBLIGATION


def test_four_material_changes_map_to_no_control(assessment):
    """The finding, not a footnote. Every one is a brand-new obligation, and a
    register cannot have covered a provision that did not exist."""
    assert {c.path for c in assessment.unmapped_material} == {
        "Article 6(5)", "Article 19(5)", "Article 25(3)", "Article 28(4)"
    }


def test_a_renumbering_still_reaches_the_control_that_cites_it(assessment):
    """It creates no new duty, but it invalidates the citation in the control's
    own documentation, which is what an examiner reads first."""
    impact = next(i for i in assessment.impacts if i.control.id == "ICT-INC-01")
    assert any(c.previous_path == "Article 17(3)(d)" for c in impact.changes)


def test_work_is_grouped_by_owner(assessment):
    owners = assessment.owners
    assert "Head of Regulatory Reporting" in owners
    assert owners["Head of Regulatory Reporting"] == ["ICT-INC-02"]


def test_an_unowned_control_is_still_reported_with_its_work(assessment):
    assert "TBC" in assessment.owners
    assert assessment.owners["TBC"] == ["ICT-TPP-02"]


def test_editorial_changes_are_counted_but_do_not_force_a_review(assessment):
    assert assessment.editorial_only == 1
    editorial_only = [
        i for i in assessment.impacts
        if i.highest is Materiality.EDITORIAL
    ]
    assert all(not i.needs_review for i in editorial_only)


# -- coverage -------------------------------------------------------------------


def test_coverage_reports_the_published_figure(register, versions):
    report = coverage(versions[1], register, today=TODAY)
    assert report.total_provisions == 43
    assert report.covered == 35
    assert round(report.coverage, 2) == 0.81


def test_a_mis_cited_provision_is_reported_as_dangling(register, versions):
    """Worse than an uncovered provision: it reads as compliance and is not."""
    report = coverage(versions[1], register, today=TODAY)
    assert ("ICT-INC-03", "Article 17(4)") in report.dangling


def test_the_new_obligations_show_as_uncovered(register, versions):
    report = coverage(versions[1], register, today=TODAY)
    assert {"Article 6(5)", "Article 19(5)", "Article 25(3)", "Article 28(4)"} <= set(
        report.uncovered
    )


def test_coverage_of_the_older_version_does_not_include_the_new_provisions(
    register, versions
):
    report = coverage(versions[0], register, today=TODAY)
    assert "Article 6(5)" not in report.uncovered


def test_stale_controls_are_measured_against_the_supplied_date(register, versions):
    report = coverage(versions[1], register, stale_after=365, today=TODAY)
    assert {c.id for c in report.stale} == {"ICT-GOV-02", "ICT-ACCESS-01", "ICT-TPP-02"}
    generous = coverage(versions[1], register, stale_after=600, today=TODAY)
    assert generous.stale == []


def test_the_unowned_control_is_named(register, versions):
    report = coverage(versions[1], register, today=TODAY)
    assert [c.id for c in report.unowned] == ["ICT-TPP-02"]
