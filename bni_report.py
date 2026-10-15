import os
import random
import re
import base64
import requests
from dotenv import load_dotenv
from datetime import datetime
from dateutil.relativedelta import relativedelta
from bs4 import BeautifulSoup
import json
import argparse

from parsers.weekly_summary import parse_weekly_summary_report
from parsers.palms_summary import (
    parse_palms_summary_report,
    parse_palms_member_stats,
    get_notable_networkers,
    format_currency,
)
from parsers.cumulative_summary import parse_cumulative_summary_report
from parsers.chapter_roster import parse_chapter_roster_report
from parsers.speaker_report import parse_speaker_report

# Load environment variables
load_dotenv()

def load_config():
    config_dir = os.path.join(os.path.dirname(__file__), "config")
    for filename in ("chapter.json", "config.json", "chapter.example.json"):
        path = os.path.join(config_dir, filename)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    return {}

def format_bni_date(date_val):
    if not date_val:
        return "01/01/2000"
    if isinstance(date_val, datetime):
        return date_val.strftime("%m/%d/%Y")
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(str(date_val).strip(), fmt).strftime("%m/%d/%Y")
        except ValueError:
            pass
    return str(date_val).strip()


def _normalise_classification(value):
    """Normalise a profession for whole-term comparisons."""
    return " ".join(re.findall(r"[a-z0-9]+", str(value or "").casefold()))


def load_classifications(categories_path):
    """Load unique classification names from the generated category catalogue."""
    try:
        with open(categories_path, "r", encoding="utf-8") as source:
            categories_data = json.load(source)
    except (OSError, json.JSONDecodeError) as error:
        print(f"Could not load classifications from {categories_path}: {error}")
        return []

    classifications = []
    seen = set()
    for category in categories_data.get("categories", []):
        if not isinstance(category, dict):
            continue
        for classification in category.get("classifications", []):
            name = str(classification or "").strip()
            key = _normalise_classification(name)
            if name and key and key not in seen:
                classifications.append(name)
                seen.add(key)
    return classifications


def select_target_professionals(classifications, roster, count=5):
    """Choose classifications that are not already represented in the chapter."""
    current_specialties = [
        _normalise_classification(member.get("specialty"))
        for member in roster
        if isinstance(member, dict) and member.get("specialty")
    ]

    eligible = []
    for classification in classifications:
        name = str(classification or "").strip()
        normalised_name = _normalise_classification(name)
        if not normalised_name:
            continue
        if any(f" {normalised_name} " in f" {specialty} " for specialty in current_specialties):
            continue
        eligible.append(name)

    return random.sample(eligible, min(count, len(eligible)))


def has_weekly_activity(weekly_stats):
    """Return whether a Weekly Summary PALMS report contains recorded activity."""
    return any(weekly_stats.get(field, 0) for field in ("oto", "refs", "tyfcb"))


CONFIG = load_config()

BNI_USERNAME = os.getenv("username")
BNI_PASSWORD = os.getenv("password")
BNI_CHAPTER_ID = os.getenv("IdOrg", str(CONFIG.get("chapter_id", "")))
BNI_REGION_ID = os.getenv("IdRegion", str(CONFIG.get("region_id", "")))
BNI_CHAPTER_NAME = CONFIG.get("chapter_name", "BNI Chapter")
SUMMARY_PALMS_START_DATE = format_bni_date(CONFIG.get("summary_palms_start_date", "01/01/2000"))

BASE_URL = "https://www.bniconnectglobal.com"
AUTH_API_URL = "https://api.bniconnectglobal.com/auth-api/authenticate"
JWT_CHECK_URL = f"{BASE_URL}/web/j_spring_security_jwt_check"
REPORT_VP_URL = f"{BASE_URL}/web/secure/reportsChapterVP"
REPORT_PALMS_URL = f"{BASE_URL}/web/secure/reportsChapterPALMS"
REPORT_CUMULATIVE_VP_URL = f"{BASE_URL}/web/secure/reportsChapterCumulativeVP"
REPORT_ROSTER_URL = f"{BASE_URL}/web/secure/reportsChapterRoster"
REPORT_SPEAKER_URL = f"{BASE_URL}/web/secure/reportsChapterCustomSpeakerReport"

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUTPUT_PATH = os.path.join(ROOT_DIR, "output", "bni_report.json")
DEFAULT_CATEGORIES_PATH = os.path.join(ROOT_DIR, "output", "categories.json")

class BNISession:
    def __init__(self, username, password):
        self.username = username
        self.password = password
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        })
        self.logged_in = False
        self.access_token = None

    def login(self):
        print("Logging in to BNI Connect...")
        self.session.get(f"{BASE_URL}/login/")
        self.session.cookies.set("AUTH_INIT", "Connect", domain="www.bniconnectglobal.com", path="/")

        auth_payload = {"client_id": "IDENTITY_PORTAL", "user_id": self.username, "password": self.password}
        auth_headers = {
            "Content-Type": "application/json",
            "Concept": "CONNECT",
            "authorization-version": "V2",
            "Authorization": "Basic " + base64.b64encode(
                ("IDENTITY_PORTAL:" + os.getenv("BNI_CLIENT_SECRET", "")).encode()
            ).decode()
        }

        response = self.session.post(AUTH_API_URL, json=auth_payload, headers=auth_headers)
        if response.status_code != 200:
            print(f"API authentication failed (HTTP {response.status_code}).")
            return False

        data = response.json().get("content", {})
        self.access_token = data.get("access_token")
        jwt_payload = {
            "j_refresh": data.get("refresh_token"),
            "j_access": data.get("access_token"),
            "j_expiry": 7199,
            "j_username": self.username,
            "j_password": self.password,
            "j_login_page": f"{BASE_URL}/login/"
        }
        jwt_headers = {
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            "Concept": "CONNECT",
            "Referer": f"{BASE_URL}/login/"
        }

        self.session.post(JWT_CHECK_URL, data=jwt_payload, headers=jwt_headers, allow_redirects=True)
        self.session.get(f"{BASE_URL}/web/", allow_redirects=False)
        jsid = self.session.cookies.get("JSESSIONID")
        if jsid:
            self.session.cookies.set("JSESSIONID", jsid, domain="www.bniconnectglobal.com", path="/web")
            self.session.cookies.set("OLDSESSIONID", jsid, domain="www.bniconnectglobal.com", path="/web")

        test_response = self.session.get(f"{BASE_URL}/web/secure/home")
        if test_response.status_code == 200 and "login" not in test_response.url:
            self.logged_in = True
            print("Login successful.")
            return True
        return False

    def get_member_list(self):
        """Return active chapter members from the dashboard member API.

        The dashboard uses the short-lived access token returned at sign-in for
        this endpoint; cookies alone are not enough.
        """
        if not self.logged_in and not self.login():
            return None
        response = self.session.get(
            "https://api.bniconnectglobal.com/member-api/slips/available-members-list",
            headers={
                "Authorization": f"Bearer {self.access_token}",
                "alphabetical": "true",
                "validate-member-status": "true",
            },
            timeout=30,
        )
        response.raise_for_status()
        return response.json().get("content", [])

    def get_report(self, url, params):
        if not self.logged_in and not self.login():
            return None
        params["__exports_details"] = "1"
        response = self.session.get(url, params=params)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            iframe = soup.find('iframe')
            if iframe and iframe.get('src'):
                iframe_url = iframe.get('src')
                if iframe_url.startswith("/"): iframe_url = f"{BASE_URL}{iframe_url}"
                return self.session.get(iframe_url, headers={"Referer": url}).text
            return response.text
        return None

def generate_report(output_file=DEFAULT_OUTPUT_PATH, reference_date=None):
    if not BNI_USERNAME or not BNI_PASSWORD:
        print("Error: credentials missing in .env")
        return None

    bni = BNISession(BNI_USERNAME, BNI_PASSWORD)
    if reference_date is not None:
        if isinstance(reference_date, datetime):
            today = reference_date
        else:
            today = datetime.combine(reference_date, datetime.min.time())
    else:
        today = datetime.now()
    now = datetime.now().astimezone()

    print(f"\n--- {BNI_CHAPTER_NAME} Report Summary ({today.strftime('%Y-%m-%d')}) ---\n")

    # 1 & 2. Weekly Summary PALMS (Upcoming Renewals + Weekly Stats)
    weekly_params = {"IdOrg": BNI_CHAPTER_ID, "Month": today.strftime("%m"), "Year": today.strftime("%Y")}
    weekly_html = bni.get_report(REPORT_VP_URL, weekly_params)
    weekly_data = parse_weekly_summary_report(weekly_html, reference_date=today) if weekly_html else None

    # At the first meeting of a month, BNI starts a new Weekly Summary report
    # before that meeting's PALMS data exists. The prior meeting therefore lives
    # in the preceding month's report.
    week_stats = weekly_data.get('weekly_stats', {}) if weekly_data else {}
    if not has_weekly_activity(week_stats):
        previous_report_date = today.replace(day=1) - relativedelta(months=1)
        previous_weekly_params = {
            "IdOrg": BNI_CHAPTER_ID,
            "Month": previous_report_date.strftime("%m"),
            "Year": previous_report_date.strftime("%Y"),
        }
        previous_weekly_html = bni.get_report(REPORT_VP_URL, previous_weekly_params)
        previous_weekly_data = (
            parse_weekly_summary_report(previous_weekly_html, reference_date=previous_report_date)
            if previous_weekly_html else None
        )
        previous_week_stats = previous_weekly_data.get('weekly_stats', {}) if previous_weekly_data else {}
        if has_weekly_activity(previous_week_stats):
            week_stats = previous_week_stats

    # 1. Upcoming Renewals
    renewals_by_month = []
    renewals_all = []
    print("1. Upcoming Renewals:")
    if weekly_data and weekly_data.get('renewals'):
        curr_m = None
        current_group = None
        for r in weekly_data['renewals']:
            month_str = r.get('month_str', '')
            ren_date = r.get('date')
            ren_date_str = ren_date.strftime("%Y-%m-%d") if isinstance(ren_date, datetime) else str(ren_date or "")

            if month_str != curr_m:
                curr_m = month_str
                print(f"   {curr_m}:")
                current_group = {"month": month_str, "members": []}
                renewals_by_month.append(current_group)

            print(f"     - {r['name']}")
            if current_group is not None:
                current_group["members"].append({
                    "name": r['name'],
                    "renewal_date": ren_date_str,
                })
            renewals_all.append({
                "name": r['name'],
                "renewal_date": ren_date_str,
                "month": month_str,
            })
    else:
        print("   No renewals found for the next 3 months.")
    print()

    # 2. VPs Report for the Last Week
    week_label = f" ({week_stats['date']})" if week_stats.get('date') else ""
    print(f"2. VPs Report for the Last Week{week_label}:")
    print(f"   - Total Number of 1-2-1 Meetings: {week_stats.get('oto', 0)}")
    print(f"   - Total Number of Referrals: {week_stats.get('refs', 0)}")
    print(f"   - Total Closed Business: ${week_stats.get('tyfcb', 0.0):,.2f}")
    print()

    vps_report_last_week = {
        "meeting_date": week_stats.get('date', ''),
        "one_to_ones": week_stats.get('oto', 0),
        "referrals": week_stats.get('refs', 0),
        "closed_business": week_stats.get('tyfcb', 0.0),
        "closed_business_formatted": f"${week_stats.get('tyfcb', 0.0):,.2f}",
    }

    # 3. VPs Report for the Last Month (Last full month relative to run date)
    last_month_start = today.replace(day=1) - relativedelta(months=1)
    last_month_end = today.replace(day=1) - relativedelta(days=1)
    last_month_name = last_month_start.strftime("%B %Y")
    last_month_params = {
        "IdOrg": BNI_CHAPTER_ID,
        "startDate": last_month_start.strftime("%m/%d/%Y"),
        "endDate": last_month_end.strftime("%m/%d/%Y"),
        "ShowDetails": "false",
    }
    last_month_html = bni.get_report(REPORT_PALMS_URL, last_month_params)
    month_data = parse_palms_summary_report(last_month_html) if last_month_html else None

    print(f"3. VPs Report for the Last Month ({last_month_name}):")
    if month_data and month_data.get('tyfcb', 0) > 0:
        print(f"   - Total Number of 1-2-1 Meetings: {month_data['otos']}")
        print(f"   - Total Number of Referrals: {month_data['refs']}")
        print(f"   - Total Closed Business: ${month_data['tyfcb']:,.2f}")
    else:
        print("   - Could not extract monthly stats.")
    print()

    vps_report_last_month = {
        "month": last_month_name,
        "start_date": last_month_start.strftime("%Y-%m-%d"),
        "end_date": last_month_end.strftime("%Y-%m-%d"),
        "one_to_ones": month_data.get('otos', 0) if month_data else 0,
        "referrals": month_data.get('refs', 0) if month_data else 0,
        "closed_business": month_data.get('tyfcb', 0.0) if month_data else 0.0,
        "closed_business_formatted": f"${month_data.get('tyfcb', 0.0):,.2f}" if month_data else "$0.00",
    }

    # 4. Notable Networkers (Last Month)
    member_stats = parse_palms_member_stats(last_month_html) if last_month_html else []
    notable = get_notable_networkers(member_stats, top_n=3)

    print(f"4. Notable Networkers ({last_month_name}):")
    print("   - Most 1-2-1 Meetings:")
    if notable.get('oto'):
        for name, val in notable['oto']:
            print(f"     - {name} - {val}")
    else:
        print("     - None recorded")

    print("   - Most Given Referrals:")
    if notable.get('refs'):
        for name, val in notable['refs']:
            print(f"     - {name} - {val}")
    else:
        print("     - None recorded")

    print("   - Most Closed Business (TYFCB):")
    if notable.get('tyfcb'):
        for name, val in notable['tyfcb']:
            print(f"     - {name} - ${val:,.2f}")
    else:
        print("     - None recorded")

    print("   - Most Invited Visitors:")
    if notable.get('visitors'):
        for name, val in notable['visitors']:
            print(f"     - {name} - {val}")
    else:
        print("     - None recorded")
    print()

    notable_networkers = {
        "month": last_month_name,
        "categories": {
            "most_one_to_ones": [
                {"rank": idx, "member": name, "value": val}
                for idx, (name, val) in enumerate(notable.get('oto', []), start=1)
            ],
            "most_given_referrals": [
                {"rank": idx, "member": name, "value": val}
                for idx, (name, val) in enumerate(notable.get('refs', []), start=1)
            ],
            "most_closed_business": [
                {"rank": idx, "member": name, "value": val, "value_formatted": f"${val:,.2f}"}
                for idx, (name, val) in enumerate(notable.get('tyfcb', []), start=1)
            ],
            "most_invited_visitors": [
                {"rank": idx, "member": name, "value": val}
                for idx, (name, val) in enumerate(notable.get('visitors', []), start=1)
            ],
        },
    }

    # 5. Rolling Average TYFCB (Last 12 Months: 12 completed full calendar months relative to run date)
    rolling_end = today.replace(day=1) - relativedelta(days=1)
    rolling_start = today.replace(day=1) - relativedelta(months=12)
    rolling_params = {
        "IdOrg": BNI_CHAPTER_ID,
        "startDate": rolling_start.strftime("%m/%d/%Y"),
        "endDate": rolling_end.strftime("%m/%d/%Y"),
        "ShowDetails": "false",
    }
    rolling_html = bni.get_report(REPORT_PALMS_URL, rolling_params)
    rolling_stats = None
    range_label = f"{rolling_start.strftime('%B %Y')} - {rolling_end.strftime('%B %Y')}"
    if rolling_html:
        rolling_stats = parse_palms_summary_report(rolling_html, months=12)
        print(f"5. Rolling Average TYFCB (Last 12 Months: {range_label}):")
        if rolling_stats and rolling_stats.get('tyfcb', 0) > 0:
            print(f"   - Total TYFCB: ${rolling_stats['tyfcb']:,.2f}")
            print(f"   - Monthly Average: ${rolling_stats.get('monthly_average', rolling_stats['tyfcb'] / 12.0):,.2f}")
        else:
            print("   - Total TYFCB: Could not extract.")
        print()

    rolling_avg_data = {
        "period_label": range_label,
        "start_date": rolling_start.strftime("%Y-%m-%d"),
        "end_date": rolling_end.strftime("%Y-%m-%d"),
        "months": 12,
        "total_tyfcb": rolling_stats.get('tyfcb', 0.0) if rolling_stats else 0.0,
        "total_tyfcb_formatted": f"${rolling_stats.get('tyfcb', 0.0):,.2f}" if rolling_stats else "$0.00",
        "monthly_average": rolling_stats.get('monthly_average', (rolling_stats.get('tyfcb', 0.0) / 12.0) if rolling_stats else 0.0) if rolling_stats else 0.0,
        "monthly_average_formatted": f"${rolling_stats.get('monthly_average', (rolling_stats.get('tyfcb', 0.0) / 12.0) if rolling_stats else 0.0):,.2f}" if rolling_stats else "$0.00",
    }

    # 6. All Time Chapter Stats
    all_time_params = {"IdOrg": BNI_CHAPTER_ID, "startDate": SUMMARY_PALMS_START_DATE, "endDate": today.strftime("%m/%d/%Y"), "ShowDetails": "false"}
    all_time_html = bni.get_report(REPORT_PALMS_URL, all_time_params)
    all_time_stats = None
    if all_time_html:
        all_time_stats = parse_palms_summary_report(all_time_html)
        print("6. All Time Chapter Stats:")
        if all_time_stats and all_time_stats.get('tyfcb', 0) > 0:
            print(f"   - Total referrals given: {all_time_stats['refs']}")
            print(f"   - Total Visitors recorded: {all_time_stats['visitors']}")
            print(f"   - Total 1-2-1s recorded: {all_time_stats['otos']}")
            print(f"   - Total TYFCB recorded: ${all_time_stats['tyfcb']:,.2f}")
        else:
            print("   - Chapter stats: Could not extract.")
        print()

    all_time_data = {
        "start_date": SUMMARY_PALMS_START_DATE,
        "as_of_date": today.strftime("%Y-%m-%d"),
        "total_referrals_given": all_time_stats.get('refs', 0) if all_time_stats else 0,
        "total_visitors": all_time_stats.get('visitors', 0) if all_time_stats else 0,
        "total_one_to_ones": all_time_stats.get('otos', 0) if all_time_stats else 0,
        "total_tyfcb": all_time_stats.get('tyfcb', 0.0) if all_time_stats else 0.0,
        "total_tyfcb_formatted": f"${all_time_stats.get('tyfcb', 0.0):,.2f}" if all_time_stats else "$0.00",
    }

    # 7. Chapter Roster Report
    roster_html = bni.get_report(REPORT_ROSTER_URL, {"IdOrg": BNI_CHAPTER_ID})
    roster_data = None
    if roster_html:
        roster_data = parse_chapter_roster_report(roster_html)
        if roster_data:
            print("7. Chapter Roster Report:")
            print("   Leaders:")
            for l in roster_data['leaders']:
                phone_str = f" ({l['phone']})" if l['phone'] else ""
                print(f"     - {l['role']}: {l['name']}{phone_str}")
            print()

            print("   Regional Leadership:")
            for r in roster_data['regional_leadership']:
                phone_str = f" ({r['phone']})" if r['phone'] else ""
                print(f"     - {r['role']}: {r['name']}{phone_str}")
            print()

            print(f"   Chapter Roster ({len(roster_data['roster'])} Members):")
            if roster_data['roster']:
                name_w = max(len(m['name']) for m in roster_data['roster'])
                spec_w = max(len(m['specialty']) for m in roster_data['roster'])
                comp_w = max(len(m['company']) for m in roster_data['roster'])
                phone_w = max(len(m['phone']) for m in roster_data['roster'])

                print(f"     {'Name':<{name_w}}  {'Specialty':<{spec_w}}  {'Company':<{comp_w}}  {'Phone':<{phone_w}}")
                print(f"     {'-'*name_w}  {'-'*spec_w}  {'-'*comp_w}  {'-'*phone_w}")
                for m in roster_data['roster']:
                    print(f"     {m['name']:<{name_w}}  {m['specialty']:<{spec_w}}  {m['company']:<{comp_w}}  {m['phone']:<{phone_w}}")
            print()

    chapter_roster_data = {
        "leaders": roster_data.get('leaders', []) if roster_data else [],
        "regional_leadership": roster_data.get('regional_leadership', []) if roster_data else [],
        "roster": roster_data.get('roster', []) if roster_data else [],
    }

    # 8. Upcoming Featured Presentation Speakers (four inclusive seven-day blocks)
    speaker_start = today
    speaker_end = today + relativedelta(days=27)
    future_featured_speakers = []
    if BNI_REGION_ID:
        speaker_params = {
            "IdRegion": BNI_REGION_ID,
            "IdOrg": BNI_CHAPTER_ID,
            "startDate": speaker_start.strftime("%m/%d/%Y"),
            "endDate": speaker_end.strftime("%m/%d/%Y"),
        }
        speaker_html = bni.get_report(REPORT_SPEAKER_URL, speaker_params)
        future_featured_speakers = parse_speaker_report(speaker_html) if speaker_html else []
    else:
        print("8. Upcoming Featured Presentation Speakers:")
        print("   Region ID is not configured; speaker report was not requested.\n")

    if BNI_REGION_ID:
        print(
            "8. Upcoming Featured Presentation Speakers "
            f"({speaker_start.strftime('%Y-%m-%d')} to {speaker_end.strftime('%Y-%m-%d')}):"
        )
        if future_featured_speakers:
            for speaker in future_featured_speakers:
                print(f"   - {speaker['meeting_date']}: {speaker['name']}")
        else:
            print("   No featured presentation speakers found.")
        print()

    upcoming_featured_speakers = {
        "start_date": speaker_start.strftime("%Y-%m-%d"),
        "end_date": speaker_end.strftime("%Y-%m-%d"),
        "speakers": future_featured_speakers,
    }

    # 9. Target Professionals
    classifications = load_classifications(DEFAULT_CATEGORIES_PATH)
    target_professionals = select_target_professionals(classifications, chapter_roster_data["roster"])
    print("9. Target Professionals:")
    if target_professionals:
        for classification in target_professionals:
            print(f"   - {classification}")
    else:
        print("   No eligible classifications found.")
    print()

    # Assemble complete report JSON
    gen_date = today.strftime("%Y-%m-%d")
    report_json = {
        "report_generation_date": gen_date,
        "report_generated_at": now.isoformat(),
        "report_metadata": {
            "chapter_id": BNI_CHAPTER_ID,
            "chapter_name": BNI_CHAPTER_NAME,
            "generated_date": gen_date,
            "generated_at": now.isoformat(),
        },
        "upcoming_renewals": {
            "by_month": renewals_by_month,
            "all": renewals_all,
        },
        "vps_report_last_week": vps_report_last_week,
        "vps_report_last_month": vps_report_last_month,
        "notable_networkers": notable_networkers,
        "rolling_average_tyfcb": rolling_avg_data,
        "all_time_stats": all_time_data,
        "chapter_roster": chapter_roster_data,
        "upcoming_featured_speakers": upcoming_featured_speakers,
        "target_professionals": target_professionals,
    }

    if output_file:
        output_path = os.path.abspath(output_file)
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(report_json, f, indent=2, ensure_ascii=False)
        print(f"Report data saved to JSON: {output_path}\n")

    return report_json


def main():
    parser = argparse.ArgumentParser(description="Generate BNI Chapter report summary and JSON data.")
    parser.add_argument(
        "--output", "-o",
        type=str,
        default=DEFAULT_OUTPUT_PATH,
        help=f"Path to output JSON file (default: {DEFAULT_OUTPUT_PATH})"
    )
    args = parser.parse_args()
    return generate_report(output_file=args.output)


if __name__ == "__main__":
    main()
