import re
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

def parse_palms_summary_report(html_content, months=None):
    if not html_content:
        return None

    soup = BeautifulSoup(html_content, 'html.parser')

    main_table = soup.find('table', id='report_main1')
    tables_to_check = [main_table] if main_table else []
    tables_to_check.extend([t for t in soup.find_all('table') if t != main_table])

    for table in tables_to_check:
        header_map = {}
        for tr in table.find_all('tr'):
            cells = [c.get_text(strip=True) for c in tr.find_all(['th', 'td'])]
            if not cells:
                continue

            upper_cells = [c.upper() for c in cells]
            if 'MEMBER NAME' in upper_cells or ('RGI' in upper_cells and 'RGO' in upper_cells):
                header_map = {name: i for i, name in enumerate(upper_cells)}
                continue

            first_cell = cells[0].strip().lower() if cells else ''
            if first_cell == 'total':
                if header_map:
                    rgi_idx = header_map.get('RGI', header_map.get('GI'))
                    rgo_idx = header_map.get('RGO', header_map.get('GO'))
                    v_idx = header_map.get('V', header_map.get('VISITORS'))
                    oto_idx = header_map.get('1-2-1', header_map.get('1-2-1S', header_map.get("1-2-1'S")))
                    tyfcb_idx = header_map.get('TYFCB')

                    rgi = int(format_currency(cells[rgi_idx])) if rgi_idx is not None and rgi_idx < len(cells) else 0
                    rgo = int(format_currency(cells[rgo_idx])) if rgo_idx is not None and rgo_idx < len(cells) else 0
                    vis = int(format_currency(cells[v_idx])) if v_idx is not None and v_idx < len(cells) else 0
                    oto = int(format_currency(cells[oto_idx])) if oto_idx is not None and oto_idx < len(cells) else 0
                    tyfcb = format_currency(cells[tyfcb_idx]) if tyfcb_idx is not None and tyfcb_idx < len(cells) else 0.0

                    result = {
                        'refs': rgi + rgo,
                        'visitors': vis,
                        'otos': oto,
                        'tyfcb': tyfcb,
                    }
                    if months and months > 0:
                        result['months'] = months
                        result['monthly_average'] = tyfcb / float(months)
                    return result
                elif len(cells) >= 13:
                    # Standard 14-column PALMS format:
                    # [Total, P, A, L, M, S, RGI, RGO, RRI, RRO, V, 1-2-1, TYFCB, (CEU)]
                    rgi = int(format_currency(cells[6]))
                    rgo = int(format_currency(cells[7]))
                    vis = int(format_currency(cells[10]))
                    oto = int(format_currency(cells[11]))
                    tyfcb = format_currency(cells[12])

                    result = {
                        'refs': rgi + rgo,
                        'visitors': vis,
                        'otos': oto,
                        'tyfcb': tyfcb,
                    }
                    if months and months > 0:
                        result['months'] = months
                        result['monthly_average'] = tyfcb / float(months)
                    return result

    return None


def parse_palms_member_stats(html_content):
    """Extract individual member PALMS stats from a Summary PALMS Report HTML."""
    if not html_content:
        return []

    soup = BeautifulSoup(html_content, 'html.parser')
    main_table = soup.find('table', id='report_main1')
    tables_to_check = [main_table] if main_table else []
    tables_to_check.extend([t for t in soup.find_all('table') if t != main_table])

    for table in tables_to_check:
        rows = table.find_all('tr')
        if not rows:
            continue

        header_map = None
        header_row_idx = 0
        for r_idx, r in enumerate(rows):
            cells = [c.get_text(strip=True) for c in r.find_all(['th', 'td'])]
            upper_cells = [c.upper() for c in cells]
            if 'MEMBER NAME' in upper_cells and 'RGI' in upper_cells:
                header_map = {name: i for i, name in enumerate(upper_cells)}
                header_row_idx = r_idx
                break

        if not header_map:
            continue

        name_idx = header_map.get('MEMBER NAME', 0)
        rgi_idx = header_map.get('RGI', header_map.get('GI'))
        rgo_idx = header_map.get('RGO', header_map.get('GO'))
        v_idx = header_map.get('V', header_map.get('VISITORS'))
        oto_idx = header_map.get('1-2-1', header_map.get('1-2-1S', header_map.get("1-2-1'S")))
        tyfcb_idx = header_map.get('TYFCB')

        members = []
        for tr in rows[header_row_idx + 1:]:
            cells = [c.get_text(strip=True) for c in tr.find_all(['th', 'td'])]
            if not cells or len(cells) <= name_idx:
                continue

            name = cells[name_idx].replace('\xa0', ' ').strip()
            if not name or name.lower() in ['total', 'totals', 'visitors', 'bni', 'sort first', 'sort last', 'member name']:
                continue
            if 'sort first' in name.lower() or 'sort last' in name.lower():
                continue

            rgi = int(format_currency(cells[rgi_idx])) if rgi_idx is not None and rgi_idx < len(cells) else 0
            rgo = int(format_currency(cells[rgo_idx])) if rgo_idx is not None and rgo_idx < len(cells) else 0
            vis = int(format_currency(cells[v_idx])) if v_idx is not None and v_idx < len(cells) else 0
            oto = int(format_currency(cells[oto_idx])) if oto_idx is not None and oto_idx < len(cells) else 0
            tyfcb = format_currency(cells[tyfcb_idx]) if tyfcb_idx is not None and tyfcb_idx < len(cells) else 0.0

            members.append({
                'name': name,
                'oto': oto,
                'refs': rgi + rgo,
                'rgi': rgi,
                'rgo': rgo,
                'visitors': vis,
                'tyfcb': tyfcb,
            })

        if members:
            return members

    return []


def get_notable_networkers(member_stats, top_n=3):
    """Return top N notable networkers across the 4 categories.

    Uses deterministic tie-breaking (value descending, name ascending).
    """
    if not member_stats:
        return {'oto': [], 'refs': [], 'tyfcb': [], 'visitors': []}

    top_oto = sorted(member_stats, key=lambda x: (-x['oto'], x['name']))[:top_n]
    top_refs = sorted(member_stats, key=lambda x: (-x['refs'], x['name']))[:top_n]
    top_tyfcb = sorted(member_stats, key=lambda x: (-x['tyfcb'], x['name']))[:top_n]
    top_visitors = sorted(member_stats, key=lambda x: (-x['visitors'], x['name']))[:top_n]

    return {
        'oto': [(m['name'], m['oto']) for m in top_oto],
        'refs': [(m['name'], m['refs']) for m in top_refs],
        'tyfcb': [(m['name'], m['tyfcb']) for m in top_tyfcb],
        'visitors': [(m['name'], m['visitors']) for m in top_visitors],
    }
