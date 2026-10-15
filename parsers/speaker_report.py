"""Parser for the BNI Chapter Speaker Report."""

from datetime import datetime

from bs4 import BeautifulSoup


def _normalise_header(value):
    return " ".join(value.replace("\xa0", " ").split()).casefold()


def _parse_meeting_date(value):
    for date_format in ("%d/%m/%y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, date_format)
        except ValueError:
            continue
    return None


def parse_speaker_report(html_content):
    """Extract scheduled featured speakers from a BNI Speaker Report export.

    The report includes a second ``Sort First``/``Sort Last`` header row, so
    rows are accepted only when their meeting-date cell can be parsed.
    """
    if not html_content:
        return []

    soup = BeautifulSoup(html_content, "html.parser")
    required_headers = {"member name", "chapter name", "meeting date"}

    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        for header_index, header_row in enumerate(rows):
            headers = [
                _normalise_header(cell.get_text(" ", strip=True))
                for cell in header_row.find_all(["th", "td"])
            ]
            if not required_headers.issubset(headers):
                continue

            columns = {header: headers.index(header) for header in required_headers}
            speakers = []
            for row in rows[header_index + 1:]:
                cells = [
                    cell.get_text(" ", strip=True).replace("\xa0", " ").strip()
                    for cell in row.find_all(["th", "td"])
                ]
                if len(cells) <= max(columns.values()):
                    continue

                meeting = _parse_meeting_date(cells[columns["meeting date"]])
                name = cells[columns["member name"]]
                if not meeting or not name or name.casefold().startswith("sort "):
                    continue

                speakers.append(
                    {
                        "name": name,
                        "chapter_name": cells[columns["chapter name"]],
                        "meeting_date": meeting.strftime("%Y-%m-%d"),
                    }
                )

            # BNI's export wraps the data table in an outer layout table that
            # repeats these headers but contains no speaker rows. Keep looking
            # until the populated table is reached.
            if speakers:
                return sorted(speakers, key=lambda speaker: (speaker["meeting_date"], speaker["name"]))

    return []
