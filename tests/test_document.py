"""Parsing regulatory text into the hierarchy people actually cite."""

from __future__ import annotations

import pytest

from regwatch.document import DocumentError, Node, load, parse

SAMPLE = """\
Identifier: EU 2022/2554
Title: A test instrument
Version: 2026-01-01

CHAPTER II ICT risk management

Article 6 ICT risk management framework
1. Financial entities shall have a sound framework.
2. The framework shall be reviewed at least once a year.
3. The process shall:
(a) put in place early warning indicators;
(b) establish procedures to identify and classify incidents.

Article 7 Something else
1. A single paragraph that runs over
   more than one line in the source text.
"""


def test_front_matter_becomes_document_metadata():
    document = parse(SAMPLE)
    assert document.identifier == "EU 2022/2554"
    assert document.title == "A test instrument"
    assert document.version == "2026-01-01"


def test_articles_are_found_under_their_chapter():
    document = parse(SAMPLE)
    chapter = document.root.children[0]
    assert chapter.kind == "chapter"
    assert [c.number for c in chapter.children] == ["6", "7"]


def test_article_headings_are_kept():
    assert parse(SAMPLE).article("6").heading == "ICT risk management framework"


def test_paragraph_paths_are_what_an_auditor_writes():
    paths = {n.path for n in parse(SAMPLE).nodes()}
    assert "Article 6(1)" in paths
    assert "Article 6(3)(a)" in paths


def test_an_article_path_never_mentions_its_chapter():
    """Nobody cites `Chapter II / Article 6`, and a register keyed that way
    stops matching the moment a provision moves between chapters."""
    assert parse(SAMPLE).article("6").path == "Article 6"
    assert all("Chapter" not in n.path for n in parse(SAMPLE).nodes() if n.kind != "chapter")


def test_continuation_lines_join_the_open_provision():
    document = parse(SAMPLE)
    text = document.by_path()["Article 7(1)"].body
    assert text == "A single paragraph that runs over more than one line in the source text."


def test_points_hang_off_their_paragraph():
    document = parse(SAMPLE)
    paragraph = document.by_path()["Article 6(3)"]
    assert [c.number for c in paragraph.children] == ["a", "b"]
    assert paragraph.children[0].parent is paragraph


def test_digest_ignores_whitespace_only_differences():
    a = Node(kind="paragraph", number="1", text="Entities  shall\n do a thing.")
    b = Node(kind="paragraph", number="1", text="Entities shall do a thing.")
    assert a.digest == b.digest


def test_digest_does_not_ignore_shall_versus_should():
    """Four characters apart, and the difference is whether anyone has to do
    anything. Normalising them together would be the worst possible bug."""
    a = Node(kind="paragraph", number="1", text="Entities shall do a thing.")
    b = Node(kind="paragraph", number="1", text="Entities should do a thing.")
    assert a.digest != b.digest


def test_full_text_gathers_children():
    document = parse(SAMPLE)
    full = document.by_path()["Article 6(3)"].full_text
    assert "early warning indicators" in full
    assert "identify and classify" in full


def test_body_excludes_children():
    document = parse(SAMPLE)
    assert "early warning" not in document.by_path()["Article 6(3)"].body


def test_summary_counts_each_kind():
    summary = parse(SAMPLE).summary()
    assert summary["article"] == 2
    assert summary["point"] == 2
    assert summary["chapter"] == 1


def test_a_missing_file_names_itself(tmp_path):
    with pytest.raises(DocumentError, match="not found"):
        load(tmp_path / "nothing.txt")


def test_a_document_with_no_identifier_is_refused(tmp_path):
    """Two snapshots that cannot say which instrument they are cannot be
    diffed against each other, so the refusal happens at load."""
    path = tmp_path / "anonymous.txt"
    path.write_text("Article 1 Heading\n1. Something.\n", encoding="utf-8")
    with pytest.raises(DocumentError, match="Identifier"):
        load(path)


def test_article_lookup_returns_none_for_an_absent_article():
    assert parse(SAMPLE).article("99") is None


def test_letter_and_roman_points_both_parse():
    document = parse(
        "Identifier: X\n\nArticle 1 H\n1. The list shall:\n(a) do this;\n(iv) do that.\n"
    )
    assert {n.number for n in document.by_path()["Article 1(1)"].children} == {"a", "iv"}


# -- the bundled sources parse --------------------------------------------------


@pytest.mark.parametrize("version", ["2023-01-16", "2026-06-30"])
def test_bundled_source_parses(version):
    document = load(f"sources/dora/{version}.txt")
    assert document.identifier == "EU 2022/2554"
    assert document.version == version
    assert len(document.nodes()) > 40
    assert document.article("19") is not None
