"""Create Notable Networker certificate PDFs from BNI report data.

The utility uses the existing monthly notable-networker rankings and chapter
leadership details in ``output/bni_report.json``.  One landscape Letter PDF is
created for each available award category.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

import weasyprint
from pybars import Compiler

from tradesheet import load_or_generate_report


ROOT = Path(__file__).resolve().parent
DEFAULT_REPORT = ROOT / "output" / "bni_report.json"
DEFAULT_OUTPUT_DIR = ROOT / "output" / "pdf"
TEMPLATE_PATH = ROOT / "templates" / "notable_networker_certificate.html.hbs"

AWARD_CATEGORIES = (
    ("most_one_to_ones", "Most 1-2-1s", "count"),
    ("most_given_referrals", "Most Referrals", "count"),
    ("most_closed_business", "Most TYFCB", "currency"),
    ("most_invited_visitors", "Most Invited Visitors", "count"),
)


def _normalise(value: Any) -> str:
    return " ".join(str(value or "").split())


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return slug or "unknown"


def _format_count(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"Award count is not numeric: {value!r}") from error
    if not number.is_integer():
        return f"{number:,.2f}".rstrip("0").rstrip(".")
    return f"{int(number):,}"


def _format_currency(value: Any) -> str:
    try:
        return f"${float(value):,.0f}"
    except (TypeError, ValueError) as error:
        raise ValueError(f"Award amount is not numeric: {value!r}") from error


def extract_president(report_data: dict[str, Any]) -> str:
    """Return the current chapter president from the downloaded leadership list."""
    roster = report_data.get("chapter_roster", {})
    leaders = roster.get("leaders", []) if isinstance(roster, dict) else []
    if not isinstance(leaders, list):
        return ""

    for leader in leaders:
        if not isinstance(leader, dict):
            continue
        if _normalise(leader.get("role")).casefold() == "president":
            return _normalise(leader.get("name"))
    return ""


def _award_month_slug(month: str) -> str:
    try:
        return datetime.strptime(month, "%B %Y").strftime("%Y-%m")
    except ValueError:
        return _slugify(month)


def assemble_certificate_contexts(report_data: dict[str, Any]) -> tuple[list[dict[str, str]], list[str]]:
    """Build display-ready contexts from rank-one notable-networker records.

    Returns the contexts in award display order plus labels for categories whose
    report data does not provide a valid rank-one winner.
    """
    notable = report_data.get("notable_networkers", {})
    if not isinstance(notable, dict):
        notable = {}
    award_month = _normalise(notable.get("month"))
    categories = notable.get("categories", {})
    if not isinstance(categories, dict):
        categories = {}

    contexts: list[dict[str, str]] = []
    missing_categories: list[str] = []
    for key, label, value_type in AWARD_CATEGORIES:
        records = categories.get(key, [])
        winner = next(
            (
                record
                for record in records
                if isinstance(record, dict) and record.get("rank") == 1
            ),
            None,
        ) if isinstance(records, list) else None
        winner_name = _normalise(winner.get("member")) if winner else ""
        if not award_month or not winner_name:
            missing_categories.append(label)
            continue

        value = winner.get("value")
        value_text = _format_currency(value) if value_type == "currency" else _format_count(value)
        contexts.append(
            {
                "award_month": award_month,
                "award_month_slug": _award_month_slug(award_month),
                "category_label": label,
                "category_slug": _slugify(label),
                "winner_name": winner_name,
                "winner_slug": _slugify(winner_name),
                "award_line": f"{label} - {value_text}",
            }
        )

    return contexts, missing_categories


def render_html(context: dict[str, str], template_path: Path = TEMPLATE_PATH) -> str:
    """Render one certificate from its Handlebars template."""
    if not template_path.exists():
        raise FileNotFoundError(f"Template not found: {template_path}")
    compiler = Compiler()
    return compiler.compile(template_path.read_text(encoding="utf-8"))(context)


def create_certificates(
    report_path: Path,
    output_dir: Path,
    target_date: date,
    offline: bool = False,
    force: bool = False,
    save_html: bool = False,
) -> list[Path]:
    """Create all available Notable Networker certificates and return their paths."""
    report_data = load_or_generate_report(
        target_date,
        offline=offline,
        report_path=report_path,
        force=force,
    )
    contexts, missing_categories = assemble_certificate_contexts(report_data)
    for label in missing_categories:
        print(f"Warning: no rank-1 winner for {label}; skipping its certificate.", file=sys.stderr)
    if not contexts:
        raise RuntimeError("No Notable Networker certificates can be created from this report.")

    president = extract_president(report_data)
    if not president:
        raise RuntimeError("The report does not contain a chapter President for certificate attribution.")

    output_dir.mkdir(parents=True, exist_ok=True)
    output_paths: list[Path] = []
    for context in contexts:
        full_context = {**context, "president_name": president}
        filename = (
            f"Notable-Networker-{context['award_month_slug']}-"
            f"{context['category_slug']}-{context['winner_slug']}.pdf"
        )
        output_path = output_dir / filename
        html_content = render_html(full_context)
        if save_html:
            html_path = output_path.with_suffix(".html")
            html_path.write_text(html_content, encoding="utf-8")
            print(f"Saved rendered HTML to: {html_path}")

        rendered = weasyprint.HTML(string=html_content, base_url=str(ROOT)).render()
        if len(rendered.pages) != 1:
            raise RuntimeError(
                f"Expected one page for {context['category_label']}, got {len(rendered.pages)}."
            )
        rendered.write_pdf(target=str(output_path))
        output_paths.append(output_path)
        print(f"Certificate generated: {output_path}")

    return output_paths


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create Notable Networker certificate PDFs via Handlebars and WeasyPrint."
    )
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT, help="Path to output/bni_report.json")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="Directory for certificate PDFs")
    parser.add_argument("--date", type=date.fromisoformat, default=date.today(), help="Report date in YYYY-MM-DD format")
    parser.add_argument("--offline", action="store_true", help="Use cached report data without contacting BNI Connect")
    parser.add_argument("--force-report", action="store_true", help="Force regeneration of bni_report.json")
    parser.add_argument("--save-html", action="store_true", help="Save rendered HTML beside every certificate PDF")
    args = parser.parse_args()

    try:
        paths = create_certificates(
            report_path=args.report,
            output_dir=args.output_dir,
            target_date=args.date,
            offline=args.offline,
            force=args.force_report,
            save_html=args.save_html,
        )
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as error:
        print(f"Certificate generation failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error

    print(f"Generated {len(paths)} Notable Networker certificate(s).")


if __name__ == "__main__":
    main()
