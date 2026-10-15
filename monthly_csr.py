"""Download and present numerical actuals for the handwritten monthly CSR."""

import argparse
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from bni_report import load_config
from csr_sources import ConnectReports, Reporting2You
from parsers.csr import (
    ReportError, normalise, parse_chapter_traffic_lights,
    parse_csr_palms, parse_member_traffic_lights, parse_membership_dues,
    validate_member_report,
)


ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = ROOT / "output" / "csr"
PALMS_FIELDS = (
    ("referrals_inside", "Referrals given inside", "count"),
    ("referrals_outside", "Referrals given outside", "count"),
    ("visitors", "Visitors", "count"),
    ("one_to_ones", "One-to-ones", "count"),
    ("tyfcb", "TYFCB", "currency"),
    ("ceus", "CEUs", "count"),
)
CTL_FIELDS = (
    ("chapter_size", "Chapter size", "count"),
    ("member_growth", "Member growth (6 months)", "count"),
    ("retention", "Retention", "percent"),
    ("referrals_per_member_week", "Referrals / member / week", "ratio"),
    ("visitors_per_week", "Visitors / week", "ratio"),
    ("conversion", "Conversion", "percent"),
    ("absenteeism", "Absenteeism", "percent"),
    ("total_score", "Total score", "count"),
)
MTL_FIELDS = (
    ("green", "Green", "percent"), ("yellow", "Yellow", "percent"),
    ("red", "Red", "percent"), ("grey", "Grey", "percent"),
    ("average", "Average score for reporting month", "percent"),
)


def shift_month(month, offset):
    index = month.year * 12 + month.month - 1 + offset
    year, zero_month = divmod(index, 12)
    return date(year, zero_month + 1, 1)


def month_bounds(month):
    start = month.replace(day=1)
    return start, shift_month(start, 1) - timedelta(days=1)


def parse_month(value):
    try:
        result = datetime.strptime(value, "%Y-%m").date().replace(day=1)
        if result.strftime("%Y-%m") != value:
            raise ValueError
        return result
    except ValueError:
        raise argparse.ArgumentTypeError("Month must be YYYY-MM") from None


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="", dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(text)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _json_text(value):
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


def collect_sources(chapter, month, as_of, directory):
    """Save each successful report; record failures without using an old file."""
    metadata = {"version": 1, "chapter": chapter, "month": month.strftime("%Y-%m"),
                "as_of": as_of.isoformat(), "downloaded_at": datetime.now(ZoneInfo("Australia/Brisbane")).isoformat(),
                "sources": {}, "errors": {}}
    sources = {}

    def acquire(key, filename, fetch):
        try:
            value = fetch()
            text = _json_text(value) if filename.endswith(".json") else value
            _write(directory / "reports" / filename, text)
            metadata["sources"][key] = {"file": f"reports/{filename}", "sha256": hashlib.sha256(text.encode()).hexdigest()}
            sources[key] = value
        except ReportError as error:
            metadata["errors"][key] = str(error)

    connect = None
    connect_keys = ("palms", "rolling_palms", "dues", "ctl", "previous_ctl")
    try:
        connect = ConnectReports(os.getenv("username"), os.getenv("password"), chapter)
        start, end = month_bounds(month)
        acquire("palms", "summary-palms.html", lambda: connect.palms(start, end))
        acquire("rolling_palms", "summary-palms-12-months.html", lambda: connect.palms(shift_month(start, -11), end))
        acquire("dues", f"membership-dues-{as_of.isoformat()}.html", lambda: connect.dues(as_of))
        acquire("ctl", "chapter-traffic-lights.html", lambda: connect.traffic_lights(month))
        acquire("previous_ctl", "chapter-traffic-lights-previous-month.html", lambda: connect.traffic_lights(shift_month(month, -1)))
    except ReportError as error:
        for key in connect_keys:
            metadata["errors"].setdefault(key, str(error))
    finally:
        if connect:
            connect.close()
    r2y = None
    try:
        r2y = Reporting2You(os.getenv("r2y_username"), os.getenv("r2y_password"), chapter)
        acquire("mtl_members", "member-traffic-lights.json", r2y.members)
        if "mtl_members" in sources:
            acquire("mtl_history", "member-evolution.json", lambda: r2y.histories(sources["mtl_members"]))
        else:
            metadata["errors"]["mtl_history"] = "Member report was not available for evolution lookup"
    except ReportError as error:
        for key in ("mtl_members", "mtl_history"):
            metadata["errors"].setdefault(key, str(error))
    finally:
        if r2y:
            r2y.close()
    return metadata, sources


def cached_sources(chapter, month, directory):
    try:
        metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ReportError("No readable CSR cache exists for this month") from None
    if not isinstance(metadata, dict):
        raise ReportError("CSR cache metadata must be a JSON object")
    if (metadata.get("version") != 1 or metadata.get("chapter") != chapter
            or metadata.get("month") != month.strftime("%Y-%m")):
        raise ReportError("CSR cache chapter or reporting month does not match")
    try:
        date.fromisoformat(metadata["as_of"])
    except (KeyError, ValueError, TypeError):
        raise ReportError("CSR cache membership snapshot date is invalid") from None
    if not isinstance(metadata.get("sources"), dict) or not isinstance(metadata.get("errors"), dict):
        raise ReportError("CSR cache source metadata is invalid")
    sources = {}
    errors = dict(metadata.get("errors", {}))
    for key, entry in metadata.get("sources", {}).items():
        try:
            path = (directory / entry["file"]).resolve()
            if not path.is_relative_to((directory / "reports").resolve()):
                raise ReportError("Cached report path is outside its reports directory")
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != entry["sha256"]:
                raise ReportError("Cached report checksum does not match")
            text = raw.decode("utf-8")
            sources[key] = json.loads(text) if path.suffix == ".json" else text
        except (OSError, ValueError, KeyError, TypeError) as error:
            errors[key] = str(error) if isinstance(error, ReportError) else "Cached report is missing or unreadable"
    metadata["errors"] = errors
    return metadata, sources


@dataclass
class Metric:
    label: str
    kind: str
    value: Decimal | None = None
    reason: str = ""
    names: list[str] | None = None


@dataclass
class CsrSummary:
    chapter: str
    month: date
    as_of: date
    sections: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    notes: list = field(default_factory=list)


def renewal_counts(dues, members_report, as_of):
    """Count recorded status categories; committee decisions require source evidence."""
    current = {normalise(row["name"]): row for row in dues.members}
    expired_statuses = {"expired", "dropped", "not renewing", "not renewed"}
    eligible = [row for row in dues.members if normalise(row["status"]) not in expired_statuses]
    expiry_month = shift_month(as_of.replace(day=1), 2)
    dates_unknown = any(not row["due_date"] and "suspend" not in normalise(row["due_text"]) for row in eligible)
    def names_for(rows):
        names = {row.get("id") or normalise(row["name"]): " ".join(row["name"].split()) for row in rows}
        return sorted(names.values(), key=str.casefold)

    expiring_names = None if dates_unknown else names_for(row for row in eligible if row["due_date"] is not None and row["due_date"].replace(day=1) == expiry_month)

    def status_names(rows, status):
        matched = {}
        for row in rows:
            identity = normalise(row["name"])
            if normalise(row["status"]) != status:
                continue
            if identity in current and normalise(current[identity]["status"]) != status:
                continue  # Do not count an old transition for a member who has renewed.
            matched[identity] = current.get(identity, row)
        return names_for(matched.values())

    joins = {}
    for row in (members_report or {}).get("members", []):
        try:
            joined = date.fromisoformat(str(row.get("joinDate", ""))[:10])
        except ValueError:
            continue
        joins.setdefault(normalise(row.get("membro", "")), set()).add(joined)
    for row in dues.new:
        if row["join_date"]:
            joins.setdefault(normalise(row["name"]), set()).add(row["join_date"])
    original_dates = [joins.get(normalise(row["name"]), set()) for row in eligible]
    review_month = shift_month(as_of.replace(day=1), -7)
    review_names = None if any(len(values) != 1 for values in original_dates) else names_for(row for row, values in zip(eligible, original_dates) if next(iter(values)).replace(day=1) == review_month)
    # Pending Renewal is not evidence of Membership Committee review.
    review_supported = any(normalise(row["status"]) == "under review" for row in dues.members)
    not_renewing_supported = any(normalise(row["status"]) == "not renewing" for row in dues.members)
    under_review_names = names_for(row for row in dues.members if normalise(row["status"]) == "under review" and row["due_date"] is not None and row["due_date"].replace(day=1) == as_of.replace(day=1)) if review_supported else None
    not_renewing_names = status_names(dues.members, "not renewing") if not_renewing_supported else None
    late_names = status_names([*dues.members, *dues.late], "late")
    expired_names = status_names([*dues.members, *dues.departed], "expired")

    def category(label, names, reason=""):
        return Metric(label, "count", Decimal(len(names)) if names is not None else None, reason, names)

    return [
        category("First-year member 7-month reviews", review_names, "original join dates are missing or ambiguous" if review_names is None else ""),
        category("Expires in 2 months", expiring_names, "some membership due dates are missing" if dates_unknown else ""),
        category("Expires this month - under review", under_review_names, "committee review status is not recorded" if under_review_names is None else ""),
        category("Late renewals", late_names),
        category("Expired", expired_names),
        category("Not renewing", not_renewing_names, "renewal decisions are not recorded" if not_renewing_names is None else ""),
    ]


def compile_summary(chapter, month, metadata, sources):
    as_of = date.fromisoformat(metadata["as_of"])
    result = CsrSummary(chapter["name"], month, as_of)
    errors = dict(metadata.get("errors", {}))
    parsed = {}
    start, end = month_bounds(month)
    parsers = {
        "palms": lambda value: parse_csr_palms(value, chapter["name"], start, end),
        "rolling_palms": lambda value: parse_csr_palms(value, chapter["name"], shift_month(start, -11), end),
        "ctl": lambda value: parse_chapter_traffic_lights(value, chapter["name"], month),
        "previous_ctl": lambda value: parse_chapter_traffic_lights(value, chapter["name"], shift_month(month, -1)),
        "dues": lambda value: parse_membership_dues(value, chapter["name"], as_of),
    }
    for key, parser in parsers.items():
        if key in sources and key not in errors:
            try:
                parsed[key] = parser(sources[key])
            except ReportError as error:
                errors[key] = str(error)
        elif key not in errors:
            errors[key] = "Supporting report is missing"
    try:
        if "mtl_members" in sources and "mtl_members" not in errors:
            try:
                validate_member_report(sources["mtl_members"], chapter["name"], chapter["region_id"])
            except ReportError as error:
                errors["mtl_members"] = str(error)
        if any(key in errors or key not in sources for key in ("mtl_members", "mtl_history")):
            raise ReportError(errors.get("mtl_history") or errors.get("mtl_members") or "Supporting member report is missing")
        parsed["mtl"] = parse_member_traffic_lights(sources["mtl_members"], sources["mtl_history"], chapter["name"], chapter["region_id"], month)
    except ReportError as error:
        errors["mtl"] = str(error)

    def metrics(source, fields):
        values = parsed.get(source, {})
        rows = []
        for key, label, kind in fields:
            value = values.get(key)
            reason = errors.get(source, "required column or numeric value is missing") if value is None else ""
            if value is None:
                result.errors.append(f"{label}: {reason}")
            rows.append(Metric(label, kind, value, reason))
        return rows

    ranking = parsed.get("ctl", {}).get("national_ranking")
    rank_reason = errors.get("ctl", "a national ranking is not provided by the downloaded regional CTL report") if ranking is None else ""
    result.sections.append(("General information", [Metric("Current BNI Australia CTL ranking", "count", ranking, rank_reason)]))
    president_rows = metrics("ctl", CTL_FIELDS)
    size = parsed.get("ctl", {}).get("chapter_size")
    previous_size = parsed.get("previous_ctl", {}).get("chapter_size")
    growth = size - previous_size if size is not None and previous_size is not None else None
    growth_reason = (errors.get("ctl") or errors.get("previous_ctl") or "month-end chapter sizes are unavailable") if growth is None else ""
    if growth is None:
        result.errors.append(f"Member growth (1 month): {growth_reason}")
    president_rows.insert(2, Metric("Member growth (1 month)", "count", growth, growth_reason))
    result.sections.append(("President - Chapter Traffic Lights", president_rows))
    result.notes.append(f"One-month growth compares chapter size at {month_bounds(shift_month(month, -1))[1].isoformat()} and {end.isoformat()}.")
    result.sections.append(("Vice President - Summary PALMS", metrics("palms", PALMS_FIELDS)))
    result.sections.append(("Vice President - Member Traffic Lights", metrics("mtl", MTL_FIELDS)))
    if "mtl" in parsed:
        data = parsed["mtl"]
        result.notes.append(f"MTL percentages and average use {data['scored_members']} scored members from the published report population ({data['report_members']} members).")
    if "dues" in parsed:
        rows = renewal_counts(parsed["dues"], sources.get("mtl_members") if "mtl_members" not in errors else None, as_of)
        if rows[1].value is None:
            result.errors.append(f"Expires in 2 months: {rows[1].reason}")
        result.notes.append("Seven-month reviews use published join dates matched to the current dues roster. Suspended dues are excluded from calendar expiry counts.")
    else:
        reason = errors.get("dues", "Membership Dues report is missing")
        rows = [Metric(label, "count", reason=reason) for label in (
            "First-year member 7-month reviews", "Expires in 2 months", "Expires this month - under review", "Late renewals", "Expired", "Not renewing")]
        result.errors.append(f"Membership counts: {reason}")
    result.sections.append(("Membership renewal counts", rows))
    tyfcb = parsed.get("rolling_palms", {}).get("tyfcb")
    chair = tyfcb / size if tyfcb is not None and size is not None and size > 0 else None
    reason = "rolling 12-month TYFCB or month-end chapter size is unavailable" if chair is None else ""
    if chair is None:
        result.errors.append(f"Chair value: {errors.get('rolling_palms', reason)}")
    tyfcb_reason = errors.get("rolling_palms", "rolling 12-month TYFCB is unavailable") if tyfcb is None else ""
    result.sections.append(("Current chair value", [
        Metric("Rolling 12-month TYFCB", "currency", tyfcb, tyfcb_reason),
        Metric("Rolling 12-month TYFCB / month-end members", "currency", chair, reason),
    ]))
    result.notes.append(f"Chair value period: {shift_month(start, -11).isoformat()} to {end.isoformat()}.")
    return result


def format_metric(metric):
    if metric.value is None:
        return f"Unavailable ({metric.reason})"
    value = metric.value
    if metric.kind == "currency":
        return f"AUD ${value:,.2f}"
    if metric.kind == "percent":
        return f"{value:.2f}%"
    if metric.kind == "ratio":
        return f"{value:.2f}"
    return f"{value:,.0f}" if value == value.to_integral_value() else f"{value:,f}".rstrip("0").rstrip(".")


def render_summary(summary):
    lines = ["BNI MONTHLY CHAPTER SUCCESS REPORT - NUMERICAL ACTUALS", summary.chapter,
             f"Performance month: {summary.month.strftime('%B %Y')}",
             f"Membership snapshot: {summary.as_of.isoformat()} (completion month)"]
    for title, rows in summary.sections:
        lines.extend(["", title, "-" * len(title)])
        width = max(len(row.label) for row in rows)
        for row in rows:
            lines.append(f"{row.label:<{width}}  {format_metric(row)}")
            if row.names is not None:
                lines.extend(f"  - {name}" for name in row.names)
                if not row.names:
                    lines.append("  None")
    if summary.notes:
        lines.extend(["", "Source notes", "------------", *summary.notes])
    if summary.errors:
        lines.extend(["", "Some numerical results could not be retrieved. See unavailable fields above."])
    return "\n".join(lines) + "\n"


def generate_csr(chapter, month, output_dir=DEFAULT_OUTPUT_DIR, *, offline=False, as_of=None):
    as_of = as_of or datetime.now(ZoneInfo("Australia/Brisbane")).date()
    directory = Path(output_dir) / month.strftime("%Y-%m")
    metadata, sources = cached_sources(chapter, month, directory) if offline else collect_sources(chapter, month, as_of, directory)
    summary = compile_summary(chapter, month, metadata, sources)
    text = render_summary(summary)
    _write(directory / "summary.txt", text)
    if not offline:
        _write(directory / "metadata.json", _json_text(metadata))
    return summary, directory / "summary.txt", text


def main(argv=None):
    load_dotenv(ROOT / ".env")
    today = datetime.now(ZoneInfo("Australia/Brisbane")).date()
    parser = argparse.ArgumentParser(description="Download numerical actuals for the handwritten monthly BNI CSR.")
    parser.add_argument("--month", type=parse_month, default=shift_month(today, -1), help="Reporting month YYYY-MM (default: previous completed month)")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="Root directory for month-specific reports and summary.txt")
    parser.add_argument("--offline", action="store_true", help="Reuse this month's CSR source cache and its original membership snapshot date")
    args = parser.parse_args(argv)
    if args.month >= today.replace(day=1):
        parser.error("Choose a completed reporting month")
    config = load_config()
    chapter = {"id": str(os.getenv("IdOrg", config.get("chapter_id", ""))),
               "region_id": str(os.getenv("IdRegion", config.get("region_id", ""))),
               "name": config.get("chapter_name", "")}
    if not all(chapter.values()):
        parser.error("Chapter ID, region ID and chapter name must be configured")
    try:
        summary, path, text = generate_csr(chapter, args.month, args.output_dir, offline=args.offline, as_of=today)
    except (ReportError, OSError, ValueError) as error:
        message = str(error) if isinstance(error, ReportError) else f"Unable to create the CSR summary ({type(error).__name__})"
        print(message, file=sys.stderr)
        return 1
    print(text, end="")
    print(f"\nSaved: {path}")
    return 1 if summary.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
