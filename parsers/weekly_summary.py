import re
from datetime import datetime
from bs4 import BeautifulSoup

def parse_all_numerical_rows(html_content):
    if not html_content: return []
    soup = BeautifulSoup(html_content, 'html.parser')
    all_num_rows = []
    for t in soup.find_all('table'):
        for r in t.find_all('tr'):
            row_vals = []
            for c in r.find_all(['td', 'th']):
                val = c.get_text(strip=True)
                if re.search(r"\d", val): row_vals.append(val)
            if row_vals: all_num_rows.append(row_vals)
    return all_num_rows

def format_currency(value):
    try:
        return float(str(value).replace('$', '').replace(',', '').replace('(', '-').replace(')', '').strip())
    except: return 0.0


def format_integer(value):
    """Return a report count, treating blank or malformed cells as zero."""
    try:
        return int(float(str(value).replace(',', '').strip()))
    except (TypeError, ValueError):
        return 0

def parse_weekly_summary_report(html_content, reference_date=None):
    if not html_content:
        return None

    if reference_date is None:
        reference_date = datetime.now()

    soup = BeautifulSoup(html_content, 'html.parser')

    data = {
        'renewals': [],
        'weekly_stats': {'oto': 0, 'refs': 0, 'tyfcb': 0.0, 'date': ''},
        'monthly_total': {'oto': 0, 'refs': 0, 'tyfcb': 0.0},
    }

    grid = soup.find('table', id='grid_1')
    if grid:
        header_tr = grid.find('tr', id='header_1')
        if header_tr:
            headers = [c.get_text(strip=True) for c in header_tr.find_all('td')]
            data_tr = header_tr.find_next_sibling('tr')
            tds = data_tr.find_all('td', recursive=False) if data_tr else []

            # 1. Renewals from Member subtable
            if tds:
                member_sub = tds[0].find('table')
                if member_sub:
                    for row in member_sub.find_all('tr'):
                        cells = [c.get_text(strip=True) for c in row.find_all(['td', 'th'])]
                        if len(cells) >= 2 and re.match(r"^\d{2}/\d{2}$", cells[-1]):
                            try:
                                ren_date = datetime.strptime(cells[-1], "%m/%y")
                                months_diff = (ren_date.year - reference_date.year) * 12 + (ren_date.month - reference_date.month)
                                if 0 <= months_diff <= 3:
                                    data['renewals'].append({
                                        'name': cells[0],
                                        'date': ren_date,
                                        'month_str': ren_date.strftime("%B %Y"),
                                    })
                            except:
                                pass
                        elif cells and cells[0] == "Totals":
                            break
                    data['renewals'].sort(key=lambda x: (x['date'], x['name']))

            # 2. Week subtables
            if len(tds) >= 3:
                date_headers = headers[1:-1]
                date_tds = tds[1:-1]

                week_data = []
                for d, td in zip(date_headers, date_tds):
                    sub = td.find('table')
                    if not sub:
                        continue
                    trs = sub.find_all('tr', class_='total_row_small')
                    if len(trs) >= 4:
                        w_cells = [c.get_text(strip=True) for c in trs[0].find_all(['td', 'th'])]
                        ty_cells = [c.get_text(strip=True) for c in trs[3].find_all(['td', 'th'])]
                        if len(w_cells) >= 7:
                            gi = format_integer(w_cells[0])
                            go = format_integer(w_cells[1])
                            oto = format_integer(w_cells[6])
                            tyfcb = format_currency(ty_cells[0]) if ty_cells else 0.0
                            week_data.append({
                                'date': d,
                                'oto': oto,
                                'refs': gi + go,
                                'tyfcb': tyfcb,
                            })

                active = [w for w in week_data if (w['refs'] > 0 or w['oto'] > 0 or w['tyfcb'] > 0)]
                if active:
                    data['weekly_stats'] = active[-1]
                elif week_data:
                    data['weekly_stats'] = week_data[-1]

            # 3. Monthly totals from Totals subtable
            if tds:
                totals_sub = tds[-1].find('table')
                if totals_sub:
                    totals_trs = totals_sub.find_all('tr', class_='total_row_small')
                    total_rows = [
                        [c.get_text(strip=True) for c in tr.find_all(['td', 'th'])]
                        for tr in totals_trs
                    ]

                    # BNI adds blank rows for weeks with no activity.  Locate the
                    # populated 9-column totals row instead of assuming its index.
                    monthly_cells = next(
                        (cells for cells in reversed(total_rows)
                         if len(cells) >= 9 and any(cells)),
                        None,
                    )
                    if monthly_cells:
                        data['monthly_total']['refs'] = format_integer(monthly_cells[2])
                        data['monthly_total']['oto'] = format_integer(monthly_cells[8])

                    ty_cells = next(
                        (cells for cells in reversed(total_rows)
                         if len(cells) == 1 and cells[0]),
                        None,
                    )
                    if ty_cells:
                        data['monthly_total']['tyfcb'] = format_currency(ty_cells[0])

        return data

    # Fallback to legacy parser if grid_1 is absent
    tables = soup.find_all('table')
    if len(tables) >= 5:
        rows4 = tables[4].find_all('tr')
        for row in rows4[3:]:
            cells = row.find_all(['td', 'th'])
            txt = [c.get_text(strip=True) for c in cells]
            if len(txt) >= 2 and re.match(r"\d{2}/\d{2}", txt[1]):
                try:
                    ren_date = datetime.strptime(txt[1], "%m/%y")
                    months_diff = (ren_date.year - reference_date.year) * 12 + (ren_date.month - reference_date.month)
                    if 0 <= months_diff <= 3:
                        data['renewals'].append({
                            'name': txt[0],
                            'date': ren_date,
                            'month_str': ren_date.strftime("%B %Y"),
                        })
                except:
                    continue
            elif txt and txt[0] == "Totals":
                break
        data['renewals'].sort(key=lambda x: (x['date'], x['name']))

    all_num_rows = parse_all_numerical_rows(html_content)
    week_rows = [r for r in all_num_rows if len(r) == 7]
    if len(week_rows) >= 2:
        for r in reversed(week_rows[:-1]):
            if any(float(v) != 0 for v in r if v.replace('.', '', 1).isdigit()):
                data['weekly_stats']['oto'] = format_integer(r[6])
                data['weekly_stats']['refs'] = format_integer(r[0]) + format_integer(r[1])
                break

    for row in reversed(all_num_rows):
        if len(row) >= 9:
            tyfcb_val = row[9] if len(row) > 9 else "0.0"
            if tyfcb_val == "0.0" or not re.search(r"\.", tyfcb_val):
                idx = all_num_rows.index(row)
                if idx + 1 < len(all_num_rows) and len(all_num_rows[idx + 1]) == 1:
                    tyfcb_val = all_num_rows[idx + 1][0]
            data['monthly_total'] = {
                'oto': format_integer(row[8]),
                'refs': format_integer(row[2]),
                'tyfcb': format_currency(tyfcb_val),
            }
            break

    return data
