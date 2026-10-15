"""Strict, source-specific parsers for Monthly Chapter Success Report data."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import re
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup


class ReportError(ValueError):
    """A report is unavailable, malformed, or for the wrong chapter/period."""


def normalise(value):
    return " ".join(re.findall(r"[a-z0-9]+", str(value).casefold()))


def number(value):
    text = str(value).strip().replace(",", "").replace("$", "").replace("%", "")
    text = re.sub(r"^AUD\s*", "", text, flags=re.I)
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    try:
        result = Decimal(text)
        return result if result.is_finite() else None
    except InvalidOperation:
        return None


def report_date(value):
    text = str(value).strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return None


def _soup(html, title):
    soup = BeautifulSoup(html or "", "html.parser")
    text = soup.get_text(" ", strip=True)
    if title.casefold() not in text.casefold():
        raise ReportError(f"Expected {title}; received an error page or another report")
    return soup


def _rows(table):
    for row in table.find_all("tr"):
        if row.find_parent("table") is table:
            cells = row.find_all(["td", "th"], recursive=False)
            yield cells, [c.get_text(" ", strip=True) for c in cells]


def _parameters(soup):
    result = {}
    table = soup.find("table", id="parameter_table")
    if table:
        for _, cells in _rows(table):
            if len(cells) == 2:
                result[normalise(cells[0])] = cells[1]
    return result


def _chapter(parameters, expected):
    if normalise(parameters.get("chapter", "")) != normalise(expected):
        raise ReportError("Report chapter does not match the configured chapter")


def parse_csr_palms(html, chapter, start, end):
    """Read all six CSR activity totals; never replace missing columns with zero."""
    soup = _soup(html, "Summary PALMS Report")
    params = _parameters(soup)
    _chapter(params, chapter)
    if report_date(params.get("from")) != start or report_date(params.get("to")) != end:
        raise ReportError("Summary PALMS dates do not match the requested period")
    aliases = {
        "referrals_inside": ("rgi", "gi"),
        "referrals_outside": ("rgo", "go"),
        "visitors": ("v", "visitors"),
        "one_to_ones": ("1 2 1", "1 2 1s", "1 2 1 s"),
        "tyfcb": ("tyfcb",),
        "ceus": ("ceu", "ceus"),
    }
    for table in soup.find_all("table"):
        header = None
        for _, cells in _rows(table):
            keys = [normalise(c) for c in cells]
            if "member name" in keys and ("rgi" in keys or "gi" in keys):
                header = {key: i for i, key in enumerate(keys)}
            elif header and cells and normalise(cells[0]) in ("total", "totals"):
                result = {}
                for field, names in aliases.items():
                    index = next((header[n] for n in names if n in header), None)
                    result[field] = number(cells[index]) if index is not None and index < len(cells) else None
                return result
    raise ReportError("Summary PALMS totals row was not found")


def parse_chapter_traffic_lights(html, chapter, month):
    soup = _soup(html, "Traffic Lights Report")
    params = _parameters(soup)
    try:
        report_month = datetime.strptime(params.get("month", ""), "%B").month
        report_year = int(params.get("year", ""))
    except ValueError as error:
        raise ReportError("Chapter Traffic Lights reporting month is missing") from error
    if (report_year, report_month) != (month.year, month.month):
        raise ReportError("Chapter Traffic Lights month does not match the requested month")
    prefixes = {
        "chapter_size": ("chapter size",),
        "member_growth": ("member growth",),
        "retention": ("retention",),
        "referrals_per_member_week": ("referrals",),
        "visitors_per_week": ("visitors",),
        "conversion": ("conversion",),
        "absenteeism": ("absenteeism",),
        "total_score": ("score", "total score"),
    }
    national_rank_labels = {"national rank", "national ranking", "australia rank", "australia ranking", "australian rank", "bni australia ctl ranking"}
    for table in soup.find_all("table"):
        header = None
        for _, cells in _rows(table):
            keys = [normalise(c) for c in cells]
            if "chapter name" in keys and "chapter size" in keys:
                header = keys
                continue
            if not header:
                continue
            name_index = header.index("chapter name")
            if len(cells) <= name_index or normalise(cells[name_index]) != normalise(chapter):
                continue
            result = {}
            for field, names in prefixes.items():
                index = next((i for i, key in enumerate(header) if any(key == p or key.startswith(p + " ") for p in names)), None)
                result[field] = number(cells[index]) if index is not None and index < len(cells) else None
            index = next((i for i, key in enumerate(header) if key in national_rank_labels), None)
            result["national_ranking"] = number(cells[index]) if index is not None and index < len(cells) else None
            return result
    raise ReportError("Configured chapter was not found in Chapter Traffic Lights")


@dataclass
class DuesData:
    members: list
    late: list
    departed: list
    new: list


def _member_record(nodes, cells, header):
    def get(key):
        index = header.get(key)
        return cells[index] if index is not None and index < len(cells) else ""
    name = get("member name")
    if not name or normalise(name) in ("sort first", "sort last", "member name", "total", "none"):
        return None
    index = header["member name"]
    member_id = ""
    if index < len(nodes):
        for link in nodes[index].find_all("a", href=True):
            query = parse_qs(urlparse(link["href"]).query)
            member_id = next((v[0] for k, v in query.items() if k.casefold() in ("memberid", "idmembership")), "")
    raw_due = get("due date")
    return {"name": name, "id": member_id, "status": get("membership status"),
            "due_date": report_date(raw_due), "due_text": raw_due,
            "join_date": report_date(get("start date"))}


def parse_membership_dues(html, chapter, as_of):
    soup = _soup(html, "Membership Dues Report")
    params = _parameters(soup)
    _chapter(params, chapter)
    if report_date(params.get("report date")) != as_of:
        raise ReportError("Membership Dues snapshot date does not match the requested date")
    records = {"members": [], "late": [], "departed": [], "new": []}
    found = set()
    # BIRT uses a containing table for each activity section and a separate
    # nested data table. Carry the section heading into that data table.
    for table in soup.find_all("table"):
        ancestors = [table, *table.find_parents("table")]
        section = "members"
        for ancestor in ancestors:
            first = next(iter(_rows(ancestor)), ([], []))[1]
            title = " ".join(first).casefold()
            if title.startswith("late members since"):
                section = "late"; break
            if title.startswith("expired or dropped members since"):
                section = "departed"; break
            if title.startswith("new members since"):
                section = "new"; break
        header = None
        for nodes, cells in _rows(table):
            keys = [normalise(c) for c in cells]
            if "member name" in keys and "due date" in keys:
                header = {key: i for i, key in enumerate(keys)}
                found.add(section)
                continue
            if header:
                record = _member_record(nodes, cells, header)
                if record:
                    records[section].append(record)
    if not {"members", "late", "departed", "new"}.issubset(found):
        raise ReportError("Membership Dues report is missing a membership/activity section")
    for key, rows in records.items():
        unique = {}
        for record in rows:
            identity = record["id"] or normalise(record["name"])
            unique[(identity, normalise(record["status"]), record["due_text"])] = record
        records[key] = list(unique.values())
    return DuesData(**records)


def validate_member_report(report, chapter, region):
    if not isinstance(report, dict) or not isinstance(report.get("members"), list):
        raise ReportError("Member Traffic Lights report has an invalid structure")
    seen = set()
    for member in report["members"]:
        if not isinstance(member, dict):
            raise ReportError("Member Traffic Lights contains an invalid member record")
        if normalise(member.get("grupo", "")) != normalise(chapter) or str(member.get("idRegiao")) != str(region):
            raise ReportError("Member Traffic Lights contains a different chapter or region")
        identity = str(member.get("id", ""))
        if not identity or identity == "None" or identity in seen:
            raise ReportError("Member Traffic Lights contains missing or duplicate member IDs")
        seen.add(identity)
    return report["members"]


def parse_member_traffic_lights(members_report, histories_report, chapter, region, month):
    """Use the selected column of the published member evolution population."""
    members = validate_member_report(members_report, chapter, region)
    if not isinstance(histories_report, dict) or not isinstance(histories_report.get("histories"), dict):
        raise ReportError("Member evolution history has an invalid structure")
    histories = histories_report["histories"]
    scores = []
    colours = {"green": 0, "yellow": 0, "red": 0, "grey": 0}
    colour_names = {"G": "green", "Y": "yellow", "R": "red", "B": "grey",
                    "GREEN": "green", "YELLOW": "yellow", "RED": "red", "GREY": "grey", "GRAY": "grey"}
    period = month.strftime("%Y%m")
    for member in members:
        identity = str(member["id"])
        if identity not in histories:
            raise ReportError("Member evolution history is incomplete")
        if not isinstance(histories[identity], list) or any(not isinstance(row, dict) for row in histories[identity]):
            raise ReportError("Member evolution contains invalid history records")
        records = [row for row in histories[identity] if row.get("anoMes") == period]
        if not records:
            # A new member may have no score in the selected historical column.
            continue
        if len(records) != 1:
            raise ReportError("Duplicate scores exist for the selected reporting month")
        row = records[0]
        if normalise(row.get("grupo", "")) != normalise(chapter) or str(row.get("idRegiao")) != str(region):
            raise ReportError("Member evolution history contains a different chapter or region")
        score = number(row.get("pontos", ""))
        if row.get("pontos") is None:
            continue
        colour = colour_names.get(str(row.get("cor", "")).upper())
        if score is None or not 0 <= score <= 100 or colour is None:
            raise ReportError("Member evolution contains an invalid score or colour")
        scores.append(score)
        colours[colour] += 1
    if not scores:
        raise ReportError("No scored members are available for the selected reporting month")
    count = Decimal(len(scores))
    return {**{key: Decimal(value) * 100 / count for key, value in colours.items()},
            "average": sum(scores) / count, "scored_members": len(scores),
            "report_members": len(members)}
