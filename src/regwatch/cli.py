"""Command line interface.

`diff` answers what changed. `impact` answers who has to do something about it.
`coverage` answers the question worth asking before anything changes at all:
which provisions does the register not claim, and which controls cite something
that no longer exists.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import __version__
from .diff import Change, ChangeType, DiffResult, Materiality, diff
from .document import DocumentError
from .document import load as load_document
from .impact import assess, coverage
from .register import RegisterError
from .register import load as load_register

MATERIALITY_STYLE = {
    Materiality.OBLIGATION: "bright_red",
    Materiality.SUBSTANTIVE: "yellow",
    Materiality.EDITORIAL: "cyan",
    Materiality.UNKNOWN: "dim",
}
TYPE_STYLE = {
    ChangeType.ADDED: "green",
    ChangeType.REMOVED: "bright_red",
    ChangeType.MODIFIED: "yellow",
    ChangeType.RENUMBERED: "dim cyan",
}


def _today(args: argparse.Namespace) -> date:
    """A fixed date can be supplied so the published figures stay reproducible."""
    return date.fromisoformat(args.today) if getattr(args, "today", None) else date.today()


def cmd_show(args: argparse.Namespace, console: Console) -> int:
    document = load_document(args.document)
    summary = document.summary()

    header = Text()
    header.append(f"{document.title}\n", style="bold")
    header.append(f"{document.identifier}   version {document.version}\n", style="dim")
    header.append(
        f"{summary['nodes']} addressable provisions: "
        + ", ".join(
            f"{count} {kind}(s)"
            for kind, count in summary.items()
            if kind in ("chapter", "title", "section", "article", "paragraph", "point")
        )
    )
    console.print(Panel(header, title="regwatch show", border_style="blue", expand=False))

    if args.article:
        node = document.article(args.article)
        if node is None:
            console.print(f"[red]no Article {args.article} in this version[/]")
            return 2
        table = Table(header_style="dim", show_lines=False)
        table.add_column("provision", style="bold")
        table.add_column("text", overflow="fold")
        for child in [node, *node.walk()]:
            if child.body or child.heading:
                table.add_row(child.path, child.body or f"[dim]{child.heading}[/]")
        console.print(table)
        return 0

    table = Table(header_style="dim")
    table.add_column("provision", style="bold")
    table.add_column("heading / opening words", overflow="fold")
    for node in document.root.children:
        table.add_row(
            Text(node.path, style="bold blue" if node.kind != "article" else "bold"),
            node.heading or node.body[:80],
        )
        for child in node.children:
            if child.kind == "article":
                table.add_row(f"  {child.path}", child.heading)
    console.print(table)
    return 0


def _change_table(changes: list[Change], *, show_text: bool) -> Table:
    table = Table(header_style="dim", show_lines=show_text)
    table.add_column("provision", style="bold", no_wrap=not show_text)
    table.add_column("change")
    table.add_column("materiality")
    table.add_column("why", overflow="fold")
    for change in changes:
        why = "; ".join(change.reasons)
        if show_text and change.change_type is ChangeType.MODIFIED:
            why += f"\n\n[dim]before:[/] {change.before}\n[dim]after:[/]  {change.after}"
        elif show_text and change.change_type is ChangeType.ADDED:
            why += f"\n\n[dim]new text:[/] {change.after}"
        table.add_row(
            change.heading,
            Text(change.change_type.value, style=TYPE_STYLE[change.change_type]),
            Text(change.materiality.value, style=MATERIALITY_STYLE[change.materiality]),
            why,
        )
    return table


def _diff_header(result: DiffResult) -> Panel:
    counts = result.by_materiality()
    header = Text()
    header.append(f"{result.after.identifier}\n", style="bold")
    header.append(
        f"{result.before.version} → {result.after.version}\n", style="dim"
    )
    header.append(f"{len(result.changes)} changes, {result.unchanged} provisions untouched\n")
    header.append(f"{counts['obligation']} obligation  ", style="bright_red")
    header.append(f"{counts['substantive']} substantive  ", style="yellow")
    header.append(f"{counts['editorial']} editorial", style="cyan")
    return Panel(header, title="regwatch diff", border_style="blue", expand=False)


def cmd_diff(args: argparse.Namespace, console: Console) -> int:
    before = load_document(args.before)
    after = load_document(args.after)
    result = diff(before, after)

    console.print(_diff_header(result))
    changes = result.changes if args.all else result.material
    if not changes:
        console.print("[green]nothing above editorial.[/]")
    else:
        console.print(_change_table(changes, show_text=args.text))

    if not args.all:
        editorial = len(result.changes) - len(result.material)
        if editorial:
            console.print(
                f"\n[dim]{editorial} editorial change(s) hidden — renumberings and "
                "wording. Pass --all to see them.[/]"
            )

    renumbered = [c for c in result.changes if c.change_type is ChangeType.RENUMBERED]
    if renumbered:
        console.print(
            f"[dim]{len(renumbered)} provision(s) kept their text and changed "
            "address. A line diff would have reported these as deletions plus "
            "additions, and everything below them as modified.[/]"
        )

    if args.json:
        Path(args.json).write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
        console.print(f"[dim]wrote {args.json}[/]")

    return 1 if args.fail and result.material else 0


def cmd_impact(args: argparse.Namespace, console: Console) -> int:
    before = load_document(args.before)
    after = load_document(args.after)
    register = load_register(args.register)
    result = diff(before, after)
    assessment = assess(result, register)

    header = Text()
    header.append(f"{assessment.instrument}  ", style="bold")
    header.append(f"{assessment.from_version} → {assessment.to_version}\n", style="dim")
    header.append(f"{len(register)} controls in the register\n", style="dim")
    header.append(
        f"{len(assessment.needing_review)} need review  ",
        style="bright_red" if assessment.needing_review else "green",
    )
    header.append(
        f"{len(assessment.unmapped_material)} material change(s) map to no control",
        style="bright_red" if assessment.unmapped_material else "dim",
    )
    console.print(Panel(header, title="regwatch impact", border_style="blue", expand=False))

    if assessment.needing_review:
        table = Table(
            title="Controls to review", title_style="bold", header_style="dim"
        )
        table.add_column("control", style="bold")
        table.add_column("owner")
        table.add_column("last reviewed", justify="right")
        table.add_column("worst")
        table.add_column("provisions", overflow="fold")
        for impact in assessment.needing_review:
            control = impact.control
            owner = Text(control.owner or "unassigned")
            if not control.is_owned:
                owner = Text(control.owner or "unassigned", style="bright_red")
            reviewed = Text("never", style="bright_red")
            if control.last_reviewed:
                days = control.days_since_review(_today(args))
                reviewed = Text(
                    control.last_reviewed.isoformat(),
                    style="yellow" if days and days > 365 else "dim",
                )
            table.add_row(
                control.id, owner, reviewed,
                Text(impact.highest.value, style=MATERIALITY_STYLE[impact.highest]),
                ", ".join(c.heading for c in impact.changes),
            )
        console.print(table)

    if assessment.unmapped_material:
        console.print()
        table = Table(
            title="Material changes no control claims",
            title_style="bold bright_red", header_style="dim",
        )
        table.add_column("provision", style="bold")
        table.add_column("change")
        table.add_column("why", overflow="fold")
        for change in assessment.unmapped_material:
            table.add_row(
                change.heading,
                Text(change.change_type.value, style=TYPE_STYLE[change.change_type]),
                "; ".join(change.reasons),
            )
        console.print(table)
        console.print(
            "[dim]Either the register has a gap or the entity is out of scope for "
            "these. Nobody outside the compliance function can tell which, which "
            "is exactly why they are reported rather than dropped.[/]"
        )

    if assessment.owners:
        console.print()
        table = Table(title="By owner", title_style="bold", header_style="dim")
        table.add_column("owner", style="bold")
        table.add_column("controls", overflow="fold")
        for owner, controls in assessment.owners.items():
            table.add_row(
                Text(owner, style="bright_red" if owner.lower() in ("tbc", "unassigned") else ""),
                ", ".join(controls),
            )
        console.print(table)

    if args.json:
        Path(args.json).write_text(json.dumps(assessment.to_dict(), indent=2), encoding="utf-8")
        console.print(f"[dim]wrote {args.json}[/]")

    return 1 if args.fail and (assessment.needing_review or assessment.unmapped_material) else 0


def cmd_coverage(args: argparse.Namespace, console: Console) -> int:
    document = load_document(args.document)
    register = load_register(args.register)
    report = coverage(document, register, stale_after=args.stale_after, today=_today(args))

    header = Text()
    header.append(f"{report.instrument}  ", style="bold")
    header.append(f"version {document.version}\n", style="dim")
    header.append(
        f"{report.covered} of {report.total_provisions} provisions claimed "
        f"({report.coverage:.0%})\n",
        style="bold",
    )
    header.append(
        f"{len(report.dangling)} dangling citation(s)  ",
        style="bright_red" if report.dangling else "dim",
    )
    header.append(
        f"{len(report.unowned)} unowned  ",
        style="bright_red" if report.unowned else "dim",
    )
    header.append(
        f"{len(report.stale)} not reviewed in {args.stale_after} days",
        style="yellow" if report.stale else "dim",
    )
    console.print(Panel(header, title="regwatch coverage", border_style="blue", expand=False))

    if report.dangling:
        table = Table(
            title="Controls citing provisions that do not exist",
            title_style="bold bright_red", header_style="dim",
        )
        table.add_column("control", style="bold")
        table.add_column("cites")
        for control_id, provision in report.dangling:
            table.add_row(control_id, provision)
        console.print(table)
        console.print(
            "[dim]Worse than an uncovered provision: this reads as compliance and "
            "is not.[/]\n"
        )

    if report.uncovered and not args.quiet:
        table = Table(
            title="Provisions no control claims", title_style="bold", header_style="dim"
        )
        table.add_column("provision", style="bold")
        table.add_column("text", overflow="fold")
        index = document.by_path()
        for path in report.uncovered:
            node = index.get(path)
            table.add_row(path, (node.body[:110] + "…") if node and len(node.body) > 110
                          else (node.body if node else ""))
        console.print(table)

    if report.unowned or report.stale:
        console.print()
        table = Table(title="Register hygiene", title_style="bold", header_style="dim")
        table.add_column("control", style="bold")
        table.add_column("owner")
        table.add_column("last reviewed", justify="right")
        for control in sorted({c.id: c for c in report.unowned + report.stale}.values(),
                              key=lambda c: c.id):
            days = control.days_since_review(_today(args))
            table.add_row(
                control.id,
                Text(control.owner or "unassigned",
                     style="bright_red" if not control.is_owned else ""),
                Text(
                    f"{control.last_reviewed} ({days}d)" if control.last_reviewed else "never",
                    style="yellow" if days and days > args.stale_after else "bright_red",
                ),
            )
        console.print(table)

    if args.json:
        Path(args.json).write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
        console.print(f"[dim]wrote {args.json}[/]")

    return 1 if args.fail and (report.dangling or report.unowned) else 0


def cmd_digest(args: argparse.Namespace, console: Console) -> int:
    """The message that goes to the people who have to act on it."""
    before = load_document(args.before)
    after = load_document(args.after)
    register = load_register(args.register)
    result = diff(before, after)
    assessment = assess(result, register)
    counts = result.by_materiality()

    lines = [
        f"# {after.identifier} — {before.version} to {after.version}",
        "",
        f"{after.title}",
        "",
        f"**{counts['obligation']} changes create, withdraw or alter an obligation.** "
        f"{counts['substantive']} substantive, {counts['editorial']} editorial. "
        f"{result.unchanged} provisions are untouched.",
        "",
    ]

    if assessment.unmapped_material:
        lines += [
            "## No control claims these",
            "",
            "Either the register has a gap here or the entity is out of scope. "
            "Someone has to decide which.",
            "",
        ]
        for change in assessment.unmapped_material:
            lines.append(f"- **{change.heading}** ({change.change_type.value}) — "
                         f"{'; '.join(change.reasons)}")
            if change.after:
                lines.append(f"  > {change.after}")
        lines.append("")

    if assessment.needing_review:
        lines += ["## Controls to review", ""]
        for owner, control_ids in assessment.owners.items():
            lines.append(f"**{owner}**")
            for control_id in control_ids:
                impact = next(i for i in assessment.impacts if i.control.id == control_id)
                provisions = ", ".join(c.heading for c in impact.changes)
                lines.append(
                    f"- `{control_id}` {impact.control.title} — {provisions} "
                    f"({impact.highest.value})"
                )
            lines.append("")

    lines += [
        "## Everything above editorial",
        "",
        "| provision | change | materiality | why |",
        "|---|---|---|---|",
    ]
    for change in result.material:
        lines.append(
            f"| {change.heading} | {change.change_type.value} | "
            f"{change.materiality.value} | {'; '.join(change.reasons)} |"
        )
    lines.append("")
    lines.append(
        "*Materiality grades how a provision changed, not how risky the change "
        "is for this institution. That judgement is the reviewer's.*"
    )

    text = "\n".join(lines) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        console.print(f"[green]wrote[/] {args.out}  [dim]{len(lines)} lines[/]")
    else:
        console.print(text, markup=False, highlight=False)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="regwatch",
        description="Track regulatory amendments and map them onto internal controls.",
    )
    parser.add_argument("--version", action="version", version=f"regwatch {__version__}")
    parser.add_argument("--today", help="fix today's date (ISO) so output is reproducible")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("show", help="the structure a document parsed into")
    p.add_argument("document")
    p.add_argument("--article", help="show one article and everything under it")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("diff", help="what changed between two versions")
    p.add_argument("before")
    p.add_argument("after")
    p.add_argument("--all", action="store_true", help="include editorial changes")
    p.add_argument("--text", action="store_true", help="show the before and after text")
    p.add_argument("--fail", action="store_true", help="exit 1 when anything is material")
    p.add_argument("--json")
    p.set_defaults(func=cmd_diff)

    p = sub.add_parser("impact", help="which controls the changes land on")
    p.add_argument("before")
    p.add_argument("after")
    p.add_argument("--register", required=True)
    p.add_argument("--fail", action="store_true")
    p.add_argument("--json")
    p.set_defaults(func=cmd_impact)

    p = sub.add_parser("coverage", help="what the register does and does not claim")
    p.add_argument("document")
    p.add_argument("--register", required=True)
    p.add_argument("--stale-after", type=int, default=365)
    p.add_argument("--quiet", action="store_true", help="skip the uncovered-provision list")
    p.add_argument("--fail", action="store_true")
    p.add_argument("--json")
    p.set_defaults(func=cmd_coverage)

    p = sub.add_parser("digest", help="a markdown brief for the people who must act")
    p.add_argument("before")
    p.add_argument("after")
    p.add_argument("--register", required=True)
    p.add_argument("--out")
    p.set_defaults(func=cmd_digest)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    console = Console()
    try:
        return int(args.func(args, console))
    except DocumentError as exc:
        console.print(f"[bold red]document error:[/] {exc}")
        return 2
    except RegisterError as exc:
        console.print(f"[bold red]register error:[/] {exc}")
        return 2
    except (OSError, ValueError) as exc:
        console.print(f"[bold red]error:[/] {exc}")
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
