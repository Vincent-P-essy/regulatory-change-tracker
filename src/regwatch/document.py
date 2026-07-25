"""Regulatory text as a tree, because that is what it is.

A regulation is not a sequence of lines. It is a numbered hierarchy —
Article 11, paragraph 4, point (c) — and every reference anyone will ever make
to it, in a policy, a control description or a regulator's finding, is a path
through that hierarchy. Flatten it and you have thrown away the only addresses
that matter.

The practical consequence shows up the first time a legislator inserts a
paragraph. A line diff reports that Article 11 changed, then that Article 12
changed, then 13, 14, 15, and so on to the end of the instrument, because
everything below the insertion shifted. A person reading that output learns
nothing. A structural diff reports one inserted node and, separately, the fact
that a set of nodes were renumbered — which is exactly the distinction between
"there is a new obligation" and "the same obligation now has a different
address", and those two need entirely different responses.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: `Article 11`, `Article 11a`, and the heading that usually follows on the
#: same or the next line.
_ARTICLE = re.compile(r"^\s*Article\s+(?P<number>\d+[a-z]?)\s*(?P<heading>.*)$", re.IGNORECASE)
#: `1.` at the start of a line opens a numbered paragraph.
_PARAGRAPH = re.compile(r"^\s*(?P<number>\d+)\.\s+(?P<text>.+)$")
#: `(a)` opens a lettered point.
_POINT = re.compile(r"^\s*\((?P<letter>[a-z]{1,2}|[ivx]{1,4})\)\s+(?P<text>.+)$")
#: `CHAPTER II` / `TITLE III`
_DIVISION = re.compile(
    r"^\s*(?P<kind>CHAPTER|TITLE|SECTION)\s+(?P<number>[IVXLC]+)\s*(?P<heading>.*)$"
)


class DocumentError(ValueError):
    """Raised when a document cannot be read. Always names the file."""


@dataclass
class Node:
    """One addressable unit of a regulation."""

    kind: str                       # division | article | paragraph | point
    number: str                     # "11", "4", "c"
    text: str = ""
    heading: str = ""
    children: list[Node] = field(default_factory=list)
    parent: Node | None = field(default=None, repr=False, compare=False)

    @property
    def path(self) -> str:
        """The citation an auditor would write: `Article 11(4)(c)`.

        An article's path never mentions its chapter. Nobody cites
        `Chapter II / Article 6(5)`, and a control register keyed that way
        stops matching the moment a provision is moved between chapters —
        which is a thing legislators do.
        """
        if self.kind == "article":
            return f"Article {self.number}"
        if self.parent is None or self.parent.kind == "root":
            return f"{self.kind.title()} {self.number}"
        base = self.parent.path
        if self.kind in ("paragraph", "point"):
            return f"{base}({self.number})"
        return f"{base} / {self.kind} {self.number}"

    @property
    def body(self) -> str:
        """This node's own text, without its children's."""
        return self.text.strip()

    @property
    def full_text(self) -> str:
        """This node and everything beneath it."""
        parts = [self.body] if self.body else []
        parts.extend(child.full_text for child in self.children)
        return "\n".join(p for p in parts if p)

    @property
    def digest(self) -> str:
        """A hash of this node's own text, ignoring its children.

        Normalised on whitespace only. Case and punctuation are load-bearing in
        legislation — `shall` and `should` differ by four characters and by
        whether anyone has to do anything.
        """
        return hashlib.sha256(" ".join(self.body.split()).encode()).hexdigest()[:16]

    def walk(self) -> Iterator[Node]:
        for child in self.children:
            yield child
            yield from child.walk()

    def add(self, child: Node) -> Node:
        child.parent = self
        self.children.append(child)
        return child

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "number": self.number,
            "path": self.path,
            "heading": self.heading,
            "text": self.body,
            "digest": self.digest,
            "children": [c.to_dict() for c in self.children],
        }


@dataclass
class Document:
    """A regulatory instrument at one point in time."""

    identifier: str          # "EU 2022/2554"
    title: str
    version: str             # a date, an OJ reference, whatever the source gives
    root: Node = field(default_factory=lambda: Node(kind="root", number=""))
    source: str = ""

    def nodes(self) -> list[Node]:
        return list(self.root.walk())

    def by_path(self) -> dict[str, Node]:
        return {node.path: node for node in self.nodes()}

    def article(self, number: str) -> Node | None:
        for node in self.root.children:
            if node.kind == "article" and node.number == str(number):
                return node
            for descendant in node.walk():
                if descendant.kind == "article" and descendant.number == str(number):
                    return descendant
        return None

    def summary(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for node in self.nodes():
            counts[node.kind] = counts.get(node.kind, 0) + 1
        return {
            "identifier": self.identifier,
            "title": self.title,
            "version": self.version,
            "nodes": len(self.nodes()),
            **counts,
        }


def parse(text: str, *, identifier: str = "", title: str = "", version: str = "",
          source: str = "") -> Document:
    """Parse regulatory text into its numbered hierarchy.

    Front matter (`Identifier:`, `Title:`, `Version:`) overrides the arguments,
    so a snapshot file is self-describing and cannot be mislabelled by whoever
    invokes the parser.
    """
    lines = text.replace("\r\n", "\n").split("\n")

    meta: dict[str, str] = {}
    body_start = 0
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            body_start = index + 1
            continue
        match = re.match(r"^(Identifier|Title|Version|Source):\s*(.+)$", stripped, re.IGNORECASE)
        if match:
            meta[match.group(1).lower()] = match.group(2).strip()
            body_start = index + 1
        else:
            break

    document = Document(
        identifier=meta.get("identifier", identifier),
        title=meta.get("title", title),
        version=meta.get("version", version),
        source=meta.get("source", source),
    )

    division: Node | None = None
    article: Node | None = None
    paragraph: Node | None = None
    point: Node | None = None

    def container() -> Node:
        return division or document.root

    for raw in lines[body_start:]:
        line = raw.rstrip()
        if not line.strip():
            continue

        if (match := _DIVISION.match(line)) is not None:
            division = document.root.add(Node(
                kind=match.group("kind").lower(),
                number=match.group("number"),
                heading=match.group("heading").strip(),
            ))
            article = paragraph = point = None
            continue

        if (match := _ARTICLE.match(line)) is not None:
            article = container().add(Node(
                kind="article",
                number=match.group("number"),
                heading=match.group("heading").strip(),
            ))
            paragraph = point = None
            continue

        if article is not None and (match := _PARAGRAPH.match(line)) is not None:
            paragraph = article.add(Node(
                kind="paragraph",
                number=match.group("number"),
                text=match.group("text").strip(),
            ))
            point = None
            continue

        if paragraph is not None and (match := _POINT.match(line)) is not None:
            point = paragraph.add(Node(
                kind="point",
                number=match.group("letter"),
                text=match.group("text").strip(),
            ))
            continue

        # A continuation line belongs to whatever is currently open. Legislation
        # wraps mid-sentence and the wrap is not a structural boundary.
        target = point or paragraph or article
        if target is not None:
            target.text = f"{target.text} {line.strip()}".strip()

    return document


def load(path: str | Path) -> Document:
    source = Path(path)
    if not source.exists():
        raise DocumentError(f"document not found: {source}")
    try:
        text = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise DocumentError(f"cannot read {source}: {exc}") from None
    document = parse(text, source=str(source))
    if not document.identifier:
        raise DocumentError(
            f"{source} has no `Identifier:` line; a snapshot that cannot say "
            "which instrument it is cannot be diffed against another"
        )
    return document
