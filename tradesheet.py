"""Create the weekly BNI Tradesheet PDF from JSON report data and chapter configuration.

This utility loads data from output/bni_report.json (generating/regenerating it if missing
or if the report date does not match the tradesheet date), combines it with chapter
configuration, renders a 2-page Handlebars HTML document (Page 1 portrait, Page 2 landscape),
and converts it to PDF using WeasyPrint.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import weasyprint
from pybars import Compiler, strlist

ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = ROOT / "config" / "tradesheet.json"
DEFAULT_REPORT = ROOT / "output" / "bni_report.json"
TEMPLATES_DIR = ROOT / "templates"


def next_meeting_date(run_date: date | None = None) -> date:
    """Return the first Wednesday strictly after ``run_date``."""
    run_date = run_date or date.today()
    days_until_wednesday = (2 - run_date.weekday()) % 7 or 7
    return run_date + timedelta(days=days_until_wednesday)


def load_config(path: Path) -> dict[str, Any]:
    """Load the tradesheet configuration JSON."""
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as source:
        return json.load(source)


def load_or_generate_report(
    target_date: date,
    offline: bool = False,
    report_path: Path = DEFAULT_REPORT,
    force: bool = False,
) -> dict[str, Any]:
    """Load bni_report.json. If missing, forced, or date mismatches, regenerate report."""
    target_str = target_date.strftime("%Y-%m-%d")

    if report_path.exists() and not force:
        try:
            with report_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            rep_date = data.get("report_generation_date")
            if rep_date == target_str:
                print(f"Using existing report data from {report_path} (date: {rep_date})")
                return data
            if offline:
                print(f"Offline mode: using existing report from {rep_date} despite date mismatch with {target_str}")
                return data
            print(f"Report generation date ({rep_date}) does not match tradesheet date ({target_str}). Regenerating...")
        except Exception as e:
            print(f"Error reading existing report ({e}). Regenerating...")

    if offline:
        raise RuntimeError(f"Report file {report_path} is missing or invalid and offline mode was requested.")

    print(f"Generating BNI report for {target_str}...")
    from bni_report import generate_report

    ref_dt = datetime.combine(target_date, datetime.min.time())
    report_data = generate_report(output_file=str(report_path), reference_date=ref_dt)
    if not report_data:
        raise RuntimeError("Failed to generate BNI report data from BNI Connect.")
    return report_data


def assemble_context(
    report_data: dict[str, Any],
    cfg: dict[str, Any],
    meeting: date,
) -> dict[str, Any]:
    """Assemble the source documents and date values for the Handlebars template."""
    return {
        "tradesheet": cfg,
        "bni_report": report_data,
        "meeting_date": meeting.strftime("%d/%m/%Y"),
        "meeting_date_iso": meeting.strftime("%Y-%m-%d"),
        "meeting_day_name": meeting.strftime("%A"),
    }


def _format_number(this: Any, val: Any) -> str:
    if val is None or val == "":
        return "0"
    try:
        return f"{int(float(val)):,}"
    except (ValueError, TypeError):
        return str(val)


def _format_currency(this: Any, val: Any) -> str:
    if val is None or val == "":
        return "$0.00"
    try:
        return f"${float(val):,.2f}"
    except (ValueError, TypeError):
        return str(val)


def _format_currency_short(this: Any, val: Any) -> str:
    if val is None or val == "":
        return "$0.0M"
    try:
        num = float(val)
        if abs(num) >= 1_000_000:
            return f"${num / 1_000_000:.1f}M"
        elif abs(num) >= 1_000:
            return f"${num / 1_000:.1f}K"
        else:
            return f"${num:,.0f}"
    except (ValueError, TypeError):
        return str(val)


def _format_roster_date(this: Any, val: Any) -> str:
    if not val:
        return ""
    try:
        d = datetime.fromisoformat(str(val))
        return d.strftime("%d-%b-%y")
    except Exception:
        return str(val)


def _break_lines(this: Any, val: Any) -> Any:
    if not val:
        return ""
    return strlist([str(val).replace("\n", "<br>")])


def _eq(this: Any, left: Any, right: Any) -> bool:
    """Return whether two Handlebars values are equal."""
    return left == right


def _member_rows_by_last_name(this: Any, roster: Any) -> list[dict[str, Any]]:
    """Build paired display rows from a report roster without altering the context."""
    if not isinstance(roster, list):
        return []

    def sort_key(member: dict[str, Any]) -> tuple[bool, str, str]:
        name = str(member.get("name", "")).strip()
        name_parts = name.split()
        surname = name_parts[-1].casefold() if name_parts else ""
        return (not bool(name), surname, name.casefold())

    members = [member for member in roster if isinstance(member, dict)]
    ordered_members = sorted(members, key=sort_key)
    midpoint = (len(ordered_members) + 1) // 2
    left_column = ordered_members[:midpoint]
    right_column = ordered_members[midpoint:]

    return [
        {
            "left": left_column[index] if index < len(left_column) else None,
            "right": right_column[index] if index < len(right_column) else None,
        }
        for index in range(max(len(left_column), len(right_column)))
    ]


def _leadership_rows(this: Any, leaders: Any) -> list[dict[str, str]]:
    """Group report leadership records into ordered, display-ready table rows."""
    if not isinstance(leaders, list):
        return []

    def normalized(value: Any) -> str:
        return " ".join(str(value or "").split()).casefold()

    core_roles = [
        ("president", "President"),
        ("vice president", "Vice President"),
        ("secretary/treasurer", "Secretary/Treasurer"),
        ("slideshow master", "Slideshow Master"),
        ("mentor coordinator", "Mentor Coordinator"),
        ("go4green coordinator", "Go4Green Coordinator"),
    ]
    core_aliases = {
        **dict(core_roles),
        "go for green co-ordinator": "Go4Green Coordinator",
        "go for green coordinator": "Go4Green Coordinator",
    }
    team_roles = [
        ("growth_and_events", "Growth and Events"),
        ("membership_committee", "Membership Committee"),
        ("visitor_hosts", "Visitor Hosts"),
    ]

    names_by_role: dict[str, list[str]] = {
        label: [] for _, label in [*core_roles, *team_roles]
    }
    seen_names_by_role: dict[str, set[str]] = {
        label: set() for _, label in [*core_roles, *team_roles]
    }
    individual_roles: dict[str, str] = {}

    def append_name(label: str, name: str) -> None:
        name_key = normalized(name)
        if not name_key or name_key in seen_names_by_role[label]:
            return
        names_by_role[label].append(name.strip())
        seen_names_by_role[label].add(name_key)

    for leader in leaders:
        if not isinstance(leader, dict):
            continue
        role = normalized(leader.get("role"))
        name = str(leader.get("name") or "")
        if not role or not name.strip():
            continue

        label = core_aliases.get(role)
        if label is None:
            if role.startswith("growth and events"):
                label = "Growth and Events"
            elif role.startswith("membership committee"):
                label = "Membership Committee"
            elif role.startswith("visitor host"):
                label = "Visitor Hosts"
            else:
                label = individual_roles.setdefault(role, " ".join(str(leader["role"]).split()))
                if label not in names_by_role:
                    names_by_role[label] = []
                    seen_names_by_role[label] = set()

        append_name(label, name)

    display_order = [
        *(label for _, label in core_roles),
        *individual_roles.values(),
        *(label for _, label in team_roles),
    ]
    return [
        {"role": label, "names": ", ".join(names_by_role[label])}
        for label in display_order
        if names_by_role[label]
    ]


def render_html(context: dict[str, Any], templates_dir: Path = TEMPLATES_DIR) -> str:
    """Render the master tradesheet Handlebars template with partials and helpers."""
    compiler = Compiler()

    page1_path = templates_dir / "page1.html.hbs"
    page2_path = templates_dir / "page2.html.hbs"
    master_path = templates_dir / "tradesheet.html.hbs"

    if not master_path.exists():
        raise FileNotFoundError(f"Template not found: {master_path}")
    if not page1_path.exists():
        raise FileNotFoundError(f"Partial template not found: {page1_path}")
    if not page2_path.exists():
        raise FileNotFoundError(f"Partial template not found: {page2_path}")

    with page1_path.open("r", encoding="utf-8") as f:
        p1_template = compiler.compile(f.read())

    with page2_path.open("r", encoding="utf-8") as f:
        p2_template = compiler.compile(f.read())

    with master_path.open("r", encoding="utf-8") as f:
        master_template = compiler.compile(f.read())

    partials = {
        "page1": p1_template,
        "page2": p2_template,
    }

    helpers = {
        "format_number": _format_number,
        "format_currency": _format_currency,
        "format_currency_short": _format_currency_short,
        "format_roster_date": _format_roster_date,
        "break_lines": _break_lines,
        "eq": _eq,
        "member_rows_by_last_name": _member_rows_by_last_name,
        "leadership_rows": _leadership_rows,
    }

    return master_template(context, partials=partials, helpers=helpers)


def create_pdf(
    config_path: Path,
    report_path: Path,
    output_path: Path,
    meeting: date,
    offline: bool = False,
    force: bool = False,
    save_html_path: Path | None = None,
    report_date: date | None = None,
) -> Path:
    """Create the 2-page tradesheet PDF (page 1 portrait, page 2 landscape)."""
    cfg = load_config(config_path)
    report_date = report_date or date.today()
    report_data = load_or_generate_report(report_date, offline=offline, report_path=report_path, force=force)
    context = assemble_context(report_data, cfg, meeting)

    html_content = render_html(context)

    if save_html_path:
        save_html_path.parent.mkdir(parents=True, exist_ok=True)
        save_html_path.write_text(html_content, encoding="utf-8")
        print(f"Saved rendered HTML to: {save_html_path}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    html_doc = weasyprint.HTML(string=html_content, base_url=str(ROOT))
    rendered = html_doc.render()

    page_count = len(rendered.pages)
    if page_count != 2:
        print(f"Warning: Expected exactly 2 pages, but document has {page_count} pages.")

    rendered.write_pdf(target=str(output_path))
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Create the BNI Chapter Tradesheet PDF via Handlebars & WeasyPrint.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="Path to config/tradesheet.json")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT, help="Path to output/bni_report.json")
    parser.add_argument(
        "--date",
        type=date.fromisoformat,
        default=next_meeting_date(),
        help="Meeting date in YYYY-MM-DD format (defaults to the next Wednesday)",
    )
    parser.add_argument(
        "--report-date",
        type=date.fromisoformat,
        help="BNI report snapshot date in YYYY-MM-DD format (defaults to today)",
    )
    parser.add_argument("--output", type=Path, help="Output PDF file path")
    parser.add_argument("--offline", action="store_true", help="Use cached report data without contacting BNI Connect")
    parser.add_argument("--force-report", action="store_true", help="Force regeneration of bni_report.json even if date matches")
    parser.add_argument("--save-html", action="store_true", help="Save the generated HTML file alongside the PDF")

    args = parser.parse_args()
    meeting_date: date = args.date
    report_date: date = args.report_date or date.today()
    output_pdf = args.output or (ROOT / "output" / "pdf" / f"BNI-Tradesheet-{meeting_date.isoformat()}.pdf")
    html_path = output_pdf.with_suffix(".html") if args.save_html else None

    result = create_pdf(
        config_path=args.config,
        report_path=args.report,
        output_path=output_pdf,
        meeting=meeting_date,
        report_date=report_date,
        offline=args.offline,
        force=args.force_report,
        save_html_path=html_path,
    )
    print(f"Tradesheet successfully generated: {result}")


if __name__ == "__main__":
    main()
