from bs4 import BeautifulSoup


def parse_chapter_roster_report(html_content):
    """Parse the BNI Chapter Roster Report HTML.

    Extracts:
    1. 'leaders': Chapter roles, member names, and contact phones.
    2. 'regional_leadership': Regional roles, names, and contact phones.
    3. 'roster': Chapter members with Member Name, Business Specialty,
       Company Name, and Phone number.
    """
    if not html_content:
        return None

    soup = BeautifulSoup(html_content, 'html.parser')

    data = {
        'leaders': [],
        'regional_leadership': [],
        'roster': [],
    }

    # 1 & 2. Leadership and Regional Leadership tables
    for t in soup.find_all('table'):
        first_tr = t.find('tr')
        if not first_tr:
            continue
        headers = [c.get_text(strip=True) for c in first_tr.find_all(['th', 'td'])]
        if 'Leaders' in headers and 'Regional Leadership' in headers:
            data_tr = first_tr.find_next_sibling('tr')
            if data_tr:
                tds = data_tr.find_all('td', recursive=False)
                if len(tds) >= 2:
                    # Leaders subtable
                    leaders_sub = tds[0].find('table')
                    if leaders_sub:
                        for row in leaders_sub.find_all('tr'):
                            cells = [c.get_text(strip=True).replace('\xa0', ' ') for c in row.find_all(['td', 'th'])]
                            if len(cells) >= 2 and cells[0]:
                                role = cells[0]
                                name = cells[1] if len(cells) > 1 else ''
                                phone = cells[2] if len(cells) > 2 else ''
                                if name:
                                    data['leaders'].append({
                                        'role': role,
                                        'name': name,
                                        'phone': phone,
                                    })

                    # Regional leadership subtable
                    reg_sub = tds[1].find('table')
                    if reg_sub:
                        for row in reg_sub.find_all('tr'):
                            cells = [c.get_text(strip=True).replace('\xa0', ' ') for c in row.find_all(['td', 'th'])]
                            if len(cells) >= 2 and cells[0]:
                                role = cells[0]
                                name = cells[1] if len(cells) > 1 else ''
                                phone = cells[2] if len(cells) > 2 else ''
                                if name:
                                    data['regional_leadership'].append({
                                        'role': role,
                                        'name': name,
                                        'phone': phone,
                                    })
            break

    # 3. Chapter Roster table
    for t in soup.find_all('table'):
        first_tr = t.find('tr')
        if not first_tr:
            continue
        headers = [c.get_text(strip=True) for c in first_tr.find_all(['th', 'td'])]
        upper_headers = [h.upper() for h in headers]
        if 'MEMBER NAME' in upper_headers and 'COMPANY NAME' in upper_headers:
            name_idx = upper_headers.index('MEMBER NAME')
            spec_idx = upper_headers.index('BUSINESS SPECIALTY') if 'BUSINESS SPECIALTY' in upper_headers else 1
            comp_idx = upper_headers.index('COMPANY NAME') if 'COMPANY NAME' in upper_headers else 2
            phone_idx = upper_headers.index('PHONE') if 'PHONE' in upper_headers else 3

            for row in t.find_all('tr')[1:]:
                cells = [c.get_text(strip=True).replace('\xa0', ' ') for c in row.find_all(['td', 'th'])]
                if len(cells) > max(name_idx, spec_idx, comp_idx, phone_idx):
                    name = cells[name_idx]
                    if not name or name.lower() in ['member name', 'sort first', 'sort last', 'total', 'totals']:
                        continue
                    if 'sort first' in name.lower() or 'sort last' in name.lower():
                        continue
                    data['roster'].append({
                        'name': name,
                        'specialty': cells[spec_idx],
                        'company': cells[comp_idx],
                        'phone': cells[phone_idx],
                    })
            break

    return data
