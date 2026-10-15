from datetime import datetime
import unittest
from unittest.mock import patch

import bni_report


class WeeklyReportFallbackTests(unittest.TestCase):
    def test_uses_prior_month_for_first_meeting_with_no_current_activity(self) -> None:
        class FakeBNISession:
            instance = None

            def __init__(self, *_args) -> None:
                self.requests = []
                FakeBNISession.instance = self

            def get_report(self, url, params):
                self.requests.append((url, params.copy()))
                if url != bni_report.REPORT_VP_URL:
                    return None
                return f"weekly-{params['Year']}-{params['Month']}"

        def parse_weekly(html, reference_date):
            if html == "weekly-2026-10":
                return {"renewals": [], "weekly_stats": {"date": "2026-10-07", "oto": 0, "refs": 0, "tyfcb": 0.0}}
            if html == "weekly-2026-09":
                return {"renewals": [], "weekly_stats": {"date": "2026-09-30", "oto": 12, "refs": 8, "tyfcb": 4500.0}}
            return None

        with (
            patch.object(bni_report, "BNISession", FakeBNISession),
            patch.object(bni_report, "BNI_USERNAME", "test-user"),
            patch.object(bni_report, "BNI_PASSWORD", "test-password"),
            patch.object(bni_report, "parse_weekly_summary_report", side_effect=parse_weekly),
        ):
            report = bni_report.generate_report(output_file=None, reference_date=datetime(2026, 10, 6))

        weekly_requests = [
            params for url, params in FakeBNISession.instance.requests
            if url == bni_report.REPORT_VP_URL
        ]
        self.assertEqual(weekly_requests, [
            {"IdOrg": bni_report.BNI_CHAPTER_ID, "Month": "10", "Year": "2026"},
            {"IdOrg": bni_report.BNI_CHAPTER_ID, "Month": "09", "Year": "2026"},
        ])
        self.assertEqual(report["vps_report_last_week"], {
            "meeting_date": "2026-09-30",
            "one_to_ones": 12,
            "referrals": 8,
            "closed_business": 4500.0,
            "closed_business_formatted": "$4,500.00",
        })


if __name__ == "__main__":
    unittest.main()
