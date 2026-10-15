import unittest
from unittest.mock import patch

import bni_report


class TargetProfessionalSelectionTests(unittest.TestCase):
    def test_excludes_classifications_already_in_member_specialties(self) -> None:
        classifications = [
            "Electrician",
            "IT",
            "Digital",
            "Travel Agent",
            "Plumber - Residential",
            "Tax Services",
        ]
        roster = [
            {"specialty": "Electrician - Residential"},
            {"specialty": "IT Support Services"},
            {"specialty": "Digital Marketing"},
        ]

        with patch.object(bni_report.random, "sample", side_effect=lambda values, count: values[:count]) as sample:
            selected = bni_report.select_target_professionals(classifications, roster, count=5)

        self.assertEqual(selected, ["Travel Agent", "Plumber - Residential", "Tax Services"])
        self.assertEqual(
            sample.call_args.args,
            (["Travel Agent", "Plumber - Residential", "Tax Services"], 3),
        )

    def test_ignores_duplicate_and_malformed_categories(self) -> None:
        self.assertEqual(
            bni_report.select_target_professionals(["", None], [], count=5),
            [],
        )


if __name__ == "__main__":
    unittest.main()
