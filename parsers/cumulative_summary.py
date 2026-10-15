import re
from bs4 import BeautifulSoup


def format_currency(value):
    try:
        return float(str(value).replace('$', '').replace(',', '').replace('(', '-').replace(')', '').strip())
    except:
        return 0.0


def parse_cumulative_summary_report(html_content):
    if not html_content:
        return None

    soup = BeautifulSoup(html_content, 'html.parser')
    grid = soup.find('table', id='grid_1')
    if not grid:
        return None

    header_tr = grid.find('tr', id='header_1')
    data_tr = header_tr.find_next_sibling('tr') if header_tr else None
    if not data_tr:
        return None

    tds = data_tr.find_all('td', recursive=False)
    if not tds:
        return None

    # Totals subtable is the last cell in data_tr
    totals_sub = tds[-1].find('table')
    if not totals_sub:
        return None

    ths = [c.get_text(strip=True).upper() for c in totals_sub.find_all('th')]
    header_map = {name: i for i, name in enumerate(ths)}
    gi_idx = header_map.get('GI', 0)
    go_idx = header_map.get('GO', 1)
    oto_idx = header_map.get('1-2-1S', header_map.get('1-2-1', 7))

    trs = totals_sub.find_all('tr', class_='total_row_small')

    refs = 0
    oto = 0
    tyfcb = 0.0

    for tr in trs:
        cells = [c.get_text(strip=True) for c in tr.find_all(['td', 'th'])]
        if len(cells) >= len(ths) and any(c.isdigit() for c in cells):
            gi = int(format_currency(cells[gi_idx])) if gi_idx < len(cells) else 0
            go = int(format_currency(cells[go_idx])) if go_idx < len(cells) else 0
            refs = gi + go
            oto = int(format_currency(cells[oto_idx])) if oto_idx < len(cells) else 0
        elif len(cells) == 1 and re.search(r'\d', cells[0]):
            tyfcb = format_currency(cells[0])

    return {
        'oto': oto,
        'refs': refs,
        'tyfcb': tyfcb,
    }
