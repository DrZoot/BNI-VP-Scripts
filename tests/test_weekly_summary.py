from datetime import datetime
from pathlib import Path
import unittest

from parsers.weekly_summary import parse_weekly_summary_report


ROOT = Path(__file__).resolve().parents[1]


class WeeklySummaryParserTests(unittest.TestCase):
    def test_parses_populated_monthly_totals(self) -> None:
        html = (ROOT / "tests/fixtures/weekly_summary.html").read_text(encoding="utf-8")

        result = parse_weekly_summary_report(html, reference_date=datetime(2026, 3, 1))

        self.assertEqual(result["monthly_total"], {"oto": 141, "refs": 133, "tyfcb": 135382.0})

    def test_defaults_blank_current_month_totals_to_zero(self) -> None:
        blank_cells = "".join("<td>&nbsp;</td>" for _ in range(9))
        html = f"""
            <table id="grid_1">
              <tr id="header_1"><td>Members</td><td>2026-10-01</td><td>Totals</td></tr>
              <tr>
                <td><table></table></td>
                <td><table>{''.join(f'<tr class="total_row_small">{blank_cells}</tr>' for _ in range(4))}</table></td>
                <td><table>
                  <tr class="total_row_small">{blank_cells}</tr>
                  <tr class="total_row_small">{blank_cells}</tr>
                  <tr class="total_row_small">{blank_cells}</tr>
                  <tr class="total_row_small"><td>&nbsp;</td></tr>
                </table></td>
              </tr>
            </table>
        """

        result = parse_weekly_summary_report(html, reference_date=datetime(2026, 10, 6))

        self.assertEqual(result["monthly_total"], {"oto": 0, "refs": 0, "tyfcb": 0.0})


if __name__ == "__main__":
    unittest.main()
