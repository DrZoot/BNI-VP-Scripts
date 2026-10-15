import contextlib
from datetime import date
from decimal import Decimal
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import requests

import csr_sources
import monthly_csr as csr
from parsers.csr import (
    ReportError, number, parse_chapter_traffic_lights, parse_csr_palms,
    parse_member_traffic_lights, parse_membership_dues,
)


FIXTURES = Path(__file__).parent / "fixtures"
CHAPTER = {"id": "1", "region_id": "2", "name": "BNI Example"}
MONTH = date(2026, 9, 1)
AS_OF = date(2026, 10, 15)


def fixture(name):
    return (FIXTURES / f"csr_{name}.html").read_text()


def member_reports():
    names = ["Casey Review", "Morgan Renew", "Taylor Late", "Current Again", "Sam Suspended"]
    scores = [100, 69, 49, 0, 70]
    colours = ["G", "Y", "R", "B", "G"]
    members = {"members": [
        {"id": i + 1, "membro": name, "grupo": CHAPTER["name"], "idRegiao": "2",
         "joinDate": "2026-03-31T00:00:00Z" if i == 0 else "2020-01-01T00:00:00Z",
         "pontos": 100, "cor": "G"}
        for i, name in enumerate(names)
    ]}
    histories = {"histories": {
        str(i + 1): [
            {"anoMes": "202608", "pontos": 100, "cor": "G", "grupo": CHAPTER["name"], "idRegiao": "2"},
            {"anoMes": "202609", "pontos": score, "cor": colours[i], "grupo": CHAPTER["name"], "idRegiao": "2"},
        ] for i, score in enumerate(scores)
    }}
    return members, histories


class FakeConnect:
    def __init__(self, *_args):
        pass

    def close(self):
        pass

    def palms(self, start, end):
        html = fixture("palms").replace("1/09/26", start.strftime("%d/%m/%y")).replace("30/09/26", end.strftime("%d/%m/%y"))
        return html.replace("170328.00", "312000.00") if start != MONTH else html

    def dues(self, as_of):
        return fixture("dues").replace("15/10/2026", as_of.strftime("%d/%m/%Y"))

    def traffic_lights(self, month):
        html = fixture("ctl").replace("September", month.strftime("%B")).replace("2026", str(month.year))
        if month == date(2026, 8, 1):
            html = html.replace("<td>BNI Example</td><td>26</td>", "<td>BNI Example</td><td>24</td>")
        return html


class FakeR2Y:
    def __init__(self, *_args):
        pass

    def close(self):
        pass

    def members(self):
        return member_reports()[0]

    def histories(self, _members):
        return member_reports()[1]


class ParserTests(unittest.TestCase):
    def test_palms_preserves_six_separate_totals(self):
        result = parse_csr_palms(fixture("palms"), CHAPTER["name"], MONTH, date(2026, 9, 30))
        self.assertEqual(result, {"referrals_inside": Decimal(93), "referrals_outside": Decimal(56),
                                 "visitors": Decimal(11), "one_to_ones": Decimal(213),
                                 "tyfcb": Decimal("170328.00"), "ceus": Decimal(233)})

    def test_zero_is_available_but_missing_column_is_not_zero(self):
        html = fixture("palms")
        for value in ("93", "56", "11", "213", "170328.00", "233"):
            html = html.replace(f"<td>{value}</td>", "<td>0</td>")
        result = parse_csr_palms(html, CHAPTER["name"], MONTH, date(2026, 9, 30))
        self.assertTrue(all(value == 0 for value in result.values()))
        html = html.replace("<td>CEU</td>", "<td>Unavailable column</td>")
        self.assertIsNone(parse_csr_palms(html, CHAPTER["name"], MONTH, date(2026, 9, 30))["ceus"])

    def test_currency_and_fractional_ceu(self):
        html = fixture("palms").replace("170328.00", "$170,328.00").replace("<td>233</td>", "<td>1.5</td>")
        data = parse_csr_palms(html, CHAPTER["name"], MONTH, date(2026, 9, 30))
        self.assertEqual(data["tyfcb"], Decimal("170328.00"))
        self.assertEqual(data["ceus"], Decimal("1.5"))
        self.assertIsNone(number("NaN"))
        self.assertIsNone(number("--"))
        self.assertEqual(number("($1,234.50)"), Decimal("-1234.50"))

    def test_repeated_header_does_not_double_count_palms(self):
        html = fixture("palms")
        begin = html.index("<tr><td>Member Name")
        end = html.index("</tr>", begin) + len("</tr>")
        html = html.replace("<tr><td>Total", html[begin:end] + "<tr><td>Total")
        self.assertEqual(parse_csr_palms(html, CHAPTER["name"], MONTH, date(2026, 9, 30))["referrals_inside"], 93)

    def test_wrong_chapter_dates_and_error_pages_are_rejected(self):
        for html in (fixture("palms").replace("BNI Example", "Another Chapter"),
                     fixture("palms").replace("1/09/26", "1/08/26"), "<h1>Access denied</h1>"):
            with self.subTest(html=html[:50]), self.assertRaises(ReportError):
                parse_csr_palms(html, CHAPTER["name"], MONTH, date(2026, 9, 30))

    def test_ctl_selects_own_chapter_and_never_uses_regional_position(self):
        data = parse_chapter_traffic_lights(fixture("ctl"), CHAPTER["name"], MONTH)
        self.assertEqual(data["chapter_size"], 26)
        self.assertEqual(data["total_score"], 80)
        self.assertEqual(data["referrals_per_member_week"], Decimal("1.29"))
        self.assertIsNone(data["national_ranking"])
        html = fixture("ctl").replace("<td>5</td>", "<td>-5</td>")
        self.assertEqual(parse_chapter_traffic_lights(html, CHAPTER["name"], MONTH)["member_growth"], -5)
        with self.assertRaises(ReportError):
            parse_chapter_traffic_lights(fixture("ctl"), CHAPTER["name"], date(2026, 8, 1))

    def test_explicit_national_rank_is_supported(self):
        html = fixture("ctl").replace("<td>Score</td><td></td>", "<td>Score</td><td>National Rank</td>").replace("<td>80</td><td></td>", "<td>80</td><td>12</td>")
        self.assertEqual(parse_chapter_traffic_lights(html, CHAPTER["name"], MONTH)["national_ranking"], 12)

    def test_dues_sections_and_statuses(self):
        dues = parse_membership_dues(fixture("dues"), CHAPTER["name"], AS_OF)
        self.assertEqual((len(dues.members), len(dues.late), len(dues.departed), len(dues.new)), (5, 1, 3, 0))
        counts = csr.renewal_counts(dues, member_reports()[0], AS_OF)
        self.assertEqual([row.value for row in counts], [1, 2, None, 1, 1, None])
        # Dropped is not "not renewing", Pending Renewal is not "under review",
        # and a past expired transition is not counted after renewal.
        self.assertIsNone(counts[2].value)
        self.assertIsNone(counts[5].value)
        self.assertEqual(counts[0].names, ["Casey Review"])
        self.assertEqual(counts[1].names, ["Casey Review", "Morgan Renew"])
        self.assertEqual(counts[3].names, ["Taylor Late"])
        self.assertEqual(counts[4].names, ["Jordan Expired"])
        for row in counts:
            if row.value is not None:
                self.assertEqual(row.value, len(row.names))

    def test_explicit_committee_categories_include_their_members(self):
        dues = parse_membership_dues(fixture("dues"), CHAPTER["name"], AS_OF)
        dues.members[0]["status"] = "Under Review"
        dues.members[0]["due_date"] = date(2026, 10, 31)
        dues.members[1]["status"] = "Not Renewing"
        counts = csr.renewal_counts(dues, member_reports()[0], AS_OF)
        self.assertEqual((counts[2].value, counts[2].names), (1, ["Casey Review"]))
        self.assertEqual((counts[5].value, counts[5].names), (1, ["Morgan Renew"]))

    def test_distinct_members_with_the_same_name_keep_their_expiry_count(self):
        dues = parse_membership_dues(fixture("dues"), CHAPTER["name"], AS_OF)
        dues.members[0]["id"] = "101"
        dues.members.append({**dues.members[0], "id": "102"})
        expiring = csr.renewal_counts(dues, member_reports()[0], AS_OF)[1]
        self.assertEqual(expiring.value, 3)
        self.assertEqual(expiring.names, ["Casey Review", "Casey Review", "Morgan Renew"])

    def test_dues_missing_section_or_snapshot_is_rejected(self):
        with self.assertRaises(ReportError):
            parse_membership_dues(fixture("dues"), CHAPTER["name"], date(2026, 10, 14))
        html = fixture("dues").replace("Late Members Since 01/09/2026", "Missing activity section")
        with self.assertRaises(ReportError):
            parse_membership_dues(html, CHAPTER["name"], AS_OF)

    def test_unknown_due_and_join_dates_stay_unavailable(self):
        html = fixture("dues").replace("31/12/2026", "--")
        dues = parse_membership_dues(html, CHAPTER["name"], AS_OF)
        members = member_reports()[0]
        members["members"][0]["joinDate"] = ""
        counts = csr.renewal_counts(dues, members, AS_OF)
        self.assertIsNone(counts[0].value)
        self.assertIsNone(counts[1].value)

    def test_mtl_uses_selected_month_and_counts_real_zero_as_grey(self):
        members, history = member_reports()
        result = parse_member_traffic_lights(members, history, CHAPTER["name"], "2", MONTH)
        self.assertEqual(result["average"], Decimal("57.6"))
        self.assertEqual([result[key] for key in ("green", "yellow", "red", "grey")], [40, 20, 20, 20])
        self.assertEqual(sum(result[key] for key in ("green", "yellow", "red", "grey")), 100)

    def test_unscored_member_is_not_automatically_grey(self):
        members, history = member_reports()
        history["histories"]["5"] = []
        result = parse_member_traffic_lights(members, history, CHAPTER["name"], "2", MONTH)
        self.assertEqual(result["scored_members"], 4)
        self.assertEqual(result["grey"], 25)

    def test_incomplete_or_cross_chapter_histories_fail_instead_of_partial_average(self):
        for mode in ("missing", "wrong chapter", "duplicate"):
            members, history = member_reports()
            if mode == "missing":
                del history["histories"]["5"]
            elif mode == "wrong chapter":
                history["histories"]["5"][1]["grupo"] = "BNI Other"
            else:
                history["histories"]["5"].append(history["histories"]["5"][1].copy())
            with self.subTest(mode=mode), self.assertRaises(ReportError):
                parse_member_traffic_lights(members, history, CHAPTER["name"], "2", MONTH)


class SummaryTests(unittest.TestCase):
    def test_calendar_boundaries(self):
        self.assertEqual(csr.shift_month(date(2026, 1, 15), -1), date(2025, 12, 1))
        self.assertEqual(csr.month_bounds(date(2024, 2, 1))[1], date(2024, 2, 29))
        self.assertEqual(csr.shift_month(MONTH, -11), date(2025, 10, 1))
        self.assertEqual(csr.shift_month(date(2026, 11, 1), 2), date(2027, 1, 1))
        self.assertEqual(csr.shift_month(date(2026, 5, 1), -7), date(2025, 10, 1))

    @patch.object(csr, "ConnectReports", FakeConnect)
    @patch.object(csr, "Reporting2You", FakeR2Y)
    def test_live_and_offline_text_agree_and_keep_original_as_of(self):
        with tempfile.TemporaryDirectory() as root:
            summary, path, text = csr.generate_csr(CHAPTER, MONTH, root, as_of=AS_OF)
            self.assertFalse(summary.errors)
            self.assertEqual(path.read_text(), text)
            self.assertIn("AUD $12,000.00", text)
            self.assertIn("57.60%", text)
            self.assertIn("  - Casey Review", text)
            self.assertRegex(text, r"First-year member 7-month reviews\s+1\n  - Casey Review")
            with patch.object(csr, "ConnectReports", side_effect=AssertionError("Offline must not sign in")), patch.object(csr, "Reporting2You", side_effect=AssertionError("Offline must not sign in")):
                offline, _, cached_text = csr.generate_csr(CHAPTER, MONTH, root, offline=True, as_of=date(2027, 1, 15))
            self.assertEqual(offline.as_of, AS_OF)
            self.assertEqual(text, cached_text)
            metadata = json.loads((path.parent / "metadata.json").read_text())
            self.assertEqual(len(metadata["sources"]), 7)
            self.assertNotIn("password", json.dumps(metadata))
            self.assertNotIn("token", json.dumps(metadata))

    @patch.object(csr, "ConnectReports", FakeConnect)
    @patch.object(csr, "Reporting2You", FakeR2Y)
    def test_monthly_growth_uses_the_two_month_end_sizes(self):
        with tempfile.TemporaryDirectory() as root:
            metadata, sources = csr.collect_sources(CHAPTER, MONTH, AS_OF, Path(root))
            for previous_size, expected in ((24, 2), (26, 0), (28, -2)):
                with self.subTest(previous_size=previous_size):
                    sources["previous_ctl"] = FakeConnect().traffic_lights(date(2026, 8, 1)).replace("<td>BNI Example</td><td>24</td>", f"<td>BNI Example</td><td>{previous_size}</td>")
                    summary = csr.compile_summary(CHAPTER, MONTH, metadata, sources)
                    president = dict(summary.sections)["President - Chapter Traffic Lights"]
                    self.assertEqual(next(row.value for row in president if row.label == "Member growth (1 month)"), expected)
                    self.assertEqual(next(row.value for row in president if row.label == "Member growth (6 months)"), 5)
                    self.assertFalse(summary.errors)

    @patch.object(csr, "ConnectReports", FakeConnect)
    @patch.object(csr, "Reporting2You", FakeR2Y)
    def test_missing_or_wrong_previous_month_does_not_produce_zero_growth(self):
        with tempfile.TemporaryDirectory() as root:
            metadata, sources = csr.collect_sources(CHAPTER, MONTH, AS_OF, Path(root))
            for previous in (None, sources["ctl"]):
                altered = dict(sources)
                if previous is None:
                    altered.pop("previous_ctl")
                else:
                    altered["previous_ctl"] = previous
                summary = csr.compile_summary(CHAPTER, MONTH, metadata, altered)
                president = dict(summary.sections)["President - Chapter Traffic Lights"]
                self.assertIsNone(next(row.value for row in president if row.label == "Member growth (1 month)"))
                self.assertTrue(summary.errors)
                self.assertEqual(next(row.value for row in president if row.label == "Member growth (6 months)"), 5)

    @patch.object(csr, "Reporting2You", FakeR2Y)
    def test_january_downloads_the_previous_december_snapshot(self):
        requested = []

        class RecordingConnect(FakeConnect):
            def traffic_lights(self, month):
                requested.append(month)
                return super().traffic_lights(month)

        with tempfile.TemporaryDirectory() as root, patch.object(csr, "ConnectReports", RecordingConnect):
            metadata, sources = csr.collect_sources(CHAPTER, date(2026, 1, 1), date(2026, 2, 15), Path(root))
        self.assertEqual(requested, [date(2026, 1, 1), date(2025, 12, 1)])
        self.assertIn("previous_ctl", metadata["sources"])
        self.assertEqual(parse_chapter_traffic_lights(sources["previous_ctl"], CHAPTER["name"], date(2025, 12, 1))["chapter_size"], 26)

    @patch.object(csr, "ConnectReports", FakeConnect)
    @patch.object(csr, "Reporting2You", FakeR2Y)
    def test_rolling_tyfcb_is_above_chair_value_and_survives_missing_size(self):
        with tempfile.TemporaryDirectory() as root:
            metadata, sources = csr.collect_sources(CHAPTER, MONTH, AS_OF, Path(root))
            summary = csr.compile_summary(CHAPTER, MONTH, metadata, sources)
            chair_rows = dict(summary.sections)["Current chair value"]
            self.assertEqual([row.value for row in chair_rows], [312000, 12000])
            text = csr.render_summary(summary)
            self.assertLess(text.index("AUD $312,000.00"), text.index("AUD $12,000.00"))
            sources.pop("ctl")
            chair_rows = dict(csr.compile_summary(CHAPTER, MONTH, metadata, sources).sections)["Current chair value"]
            self.assertEqual(chair_rows[0].value, 312000)
            self.assertIsNone(chair_rows[1].value)

    def test_zero_and_unavailable_membership_lists_are_distinct(self):
        summary = csr.CsrSummary(CHAPTER["name"], MONTH, AS_OF)
        summary.sections = [("Membership renewal counts", [
            csr.Metric("Late renewals", "count", Decimal(0), names=[]),
            csr.Metric("Not renewing", "count", reason="renewal decisions are not recorded"),
        ])]
        text = csr.render_summary(summary)
        self.assertRegex(text, r"Late renewals\s+0\n  None")
        self.assertNotIn("Unavailable (renewal decisions are not recorded)\n  None", text)

    @patch.object(csr, "ConnectReports", FakeConnect)
    @patch.object(csr, "Reporting2You", FakeR2Y)
    def test_modified_cache_or_wrong_chapter_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            _, path, _ = csr.generate_csr(CHAPTER, MONTH, root, as_of=AS_OF)
            source = path.parent / "reports" / "summary-palms.html"
            source.write_text(source.read_text() + "tampered")
            summary, _, text = csr.generate_csr(CHAPTER, MONTH, root, offline=True)
            self.assertTrue(summary.errors)
            self.assertIn("checksum", text)
            with self.assertRaises(ReportError):
                csr.cached_sources({**CHAPTER, "id": "999"}, MONTH, path.parent)

    def test_malformed_cache_metadata_fails_cleanly(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root) / "2026-09"
            directory.mkdir()
            for data in ([], {"version": 1, "chapter": CHAPTER, "month": "2026-09", "as_of": "2026-10-15", "sources": [], "errors": {}}):
                (directory / "metadata.json").write_text(json.dumps(data))
                with self.assertRaises(ReportError):
                    csr.cached_sources(CHAPTER, MONTH, directory)

    @patch.object(csr, "Reporting2You", FakeR2Y)
    def test_offline_checksums_preserve_birt_carriage_return_line_endings(self):
        class BirtConnect(FakeConnect):
            def palms(self, start, end):
                return super().palms(start, end).replace("\n", "\r")

            def dues(self, as_of):
                return super().dues(as_of).replace("\n", "\r\n")

            def traffic_lights(self, month):
                return super().traffic_lights(month).replace("\n", "\r")

        with tempfile.TemporaryDirectory() as root, patch.object(csr, "ConnectReports", BirtConnect):
            _, _, live_text = csr.generate_csr(CHAPTER, MONTH, root, as_of=AS_OF)
            summary, _, text = csr.generate_csr(CHAPTER, MONTH, root, offline=True)
            self.assertFalse(summary.errors)
            self.assertEqual(text, live_text)

    @patch.object(csr, "Reporting2You", FakeR2Y)
    def test_permission_denial_preserves_other_metrics_without_using_old_report(self):
        class DeniedConnect(FakeConnect):
            def traffic_lights(self, month):
                raise ReportError("Reporting service returned HTTP 403")
        with tempfile.TemporaryDirectory() as root, patch.object(csr, "ConnectReports", FakeConnect):
            csr.generate_csr(CHAPTER, MONTH, root, as_of=AS_OF)
            with patch.object(csr, "ConnectReports", DeniedConnect):
                summary, path, text = csr.generate_csr(CHAPTER, MONTH, root, as_of=AS_OF)
            self.assertTrue(summary.errors)
            self.assertIn("170,328.00", text)
            self.assertIn("HTTP 403", text)
            metadata = json.loads((path.parent / "metadata.json").read_text())
            self.assertNotIn("ctl", metadata["sources"])
            cached, _, _ = csr.generate_csr(CHAPTER, MONTH, root, offline=True)
            self.assertTrue(cached.errors)

    @patch.object(csr, "ConnectReports", FakeConnect)
    @patch.object(csr, "Reporting2You", FakeR2Y)
    def test_cli_success_and_unexpected_failure_exit_codes(self):
        with tempfile.TemporaryDirectory() as root, patch.object(csr, "load_config", return_value={"chapter_id": "1", "region_id": "2", "chapter_name": "BNI Example"}), patch.dict("os.environ", {"IdOrg": "1", "IdRegion": "2"}), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(csr.main(["--month", "2026-09", "--output-dir", root]), 0)
            with patch.object(csr, "Reporting2You", side_effect=ReportError("Reporting2You sign-in failed")):
                self.assertEqual(csr.main(["--month", "2026-09", "--output-dir", root]), 1)


def response(data=None, text="", status=200, url="https://example.invalid/"):
    value = Mock(status_code=status, text=text, url=url)
    value.json.return_value = data
    return value


class DownloadTests(unittest.TestCase):
    def test_reporting2you_uses_separate_credentials_and_batched_history(self):
        members, history = member_reports()
        session = Mock(headers={})
        session.request.side_effect = [
            response({"data": {"login": {"token": "private-token"}}}),
            response({"data": {"loginVariables": {"memberGroup": "BNI Example", "memberIdRegion": "2", "memberDashBoardLT": True}}}),
            response({"data": {"memberTrafficLights": members["members"]}}),
            response({"data": {f"m{i}": history["histories"][str(i + 1)] for i in range(5)}}),
        ]
        with patch.object(csr_sources, "TimeoutSession", return_value=session):
            client = csr_sources.Reporting2You("separate-user", "separate-password", CHAPTER)
            rows = client.members()
            result = client.histories(rows)
            client.close()
        self.assertEqual(result, history)
        self.assertEqual(session.request.call_args_list[0].kwargs["json"]["variables"]["password"], "separate-password")
        self.assertEqual(session.request.call_args_list[2].kwargs["json"]["variables"]["type"], "LT")
        self.assertEqual(session.request.call_count, 4)
        session.close.assert_called_once()

    def test_graphql_errors_and_network_errors_do_not_echo_secrets(self):
        session = Mock(headers={})
        session.request.return_value = response({"errors": [{"message": "private-password private-token"}]})
        with patch.object(csr_sources, "TimeoutSession", return_value=session), self.assertRaises(ReportError) as raised:
            csr_sources.Reporting2You("user", "private-password", CHAPTER)
        self.assertNotIn("private", str(raised.exception))
        session.request.side_effect = requests.ConnectionError("https://host/?token=private-token")
        with self.assertRaises(ReportError) as raised:
            csr_sources._request(session, "GET", "https://example.invalid/")
        self.assertNotIn("private-token", str(raised.exception))

    def test_report_specific_token_exchange_and_chapter_context(self):
        legacy = Mock(headers={})
        legacy.request.return_value = response(url="https://www.bniconnectglobal.com/web/reports/?mtoken=private-master")
        reports = Mock(headers={})
        reports.request.side_effect = [
            response({"content": {"access_token": "private-access"}}),
            response({"content": {"orgs_filter_menu": [{"id": 3, "sub": [{"id": 2, "sub": [{"id": 1}]}]}]}}),
            response({"content": {"execution_type": "SHORT"}}),
            response(text=fixture("ctl")),
        ]
        shared = Mock(session=Mock(headers={}))
        shared.login.return_value = True
        with patch.object(csr_sources, "BNISession", return_value=shared), patch.object(csr_sources, "TimeoutSession", side_effect=[legacy, reports]):
            client = csr_sources.ConnectReports("user", "password", CHAPTER)
            self.assertIn("Traffic Lights Report", client.traffic_lights(MONTH))
            client.close()
        params = reports.request.call_args_list[-1].kwargs["params"]
        self.assertEqual((params["IdOrg"], params["IdRegion"], params["__heading_country"]), ("1", "2", 3))
        self.assertEqual(reports.request.call_args_list[0].kwargs["json"], {"masterToken": "private-master", "variant": "connect"})

    def test_service_error_pages_and_http_failures(self):
        with self.assertRaises(ReportError):
            csr_sources._html(response(text="<h1>HTTP Status 500</h1>"), "Membership Dues Report")
        session = Mock()
        session.request.return_value = response(status=403)
        with self.assertRaisesRegex(ReportError, "HTTP 403"):
            csr_sources._request(session, "GET", "https://example.invalid/")
        with self.assertRaises(ReportError):
            csr_sources._json(response(data=[]))


if __name__ == "__main__":
    unittest.main()
