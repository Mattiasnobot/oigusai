from __future__ import annotations

import unittest
from datetime import date

from services.rt_authority import RTAuthorityError, verify_revision_currentness


class RTAuthorityXSDDateTests(unittest.TestCase):
    def test_rt_xsd_dates_with_positive_timezone_are_accepted(self):
        valid_from, valid_to = verify_revision_currentness(
            {
                "valid_from": "2026-03-16+02:00",
                "valid_to": "2026-09-30+03:00",
            },
            as_of=date(2026, 8, 21),
        )
        self.assertEqual(valid_from, date(2026, 3, 16))
        self.assertEqual(valid_to, date(2026, 9, 30))

    def test_rt_xsd_date_z_timezone_is_accepted(self):
        valid_from, valid_to = verify_revision_currentness(
            {
                "valid_from": "2026-03-16Z",
                "valid_to": "2026-09-30Z",
            },
            as_of=date(2026, 8, 21),
        )
        self.assertEqual(valid_from, date(2026, 3, 16))
        self.assertEqual(valid_to, date(2026, 9, 30))

    def test_rt_xsd_date_negative_timezone_is_accepted(self):
        valid_from, valid_to = verify_revision_currentness(
            {
                "valid_from": "2026-03-16-05:00",
                "valid_to": "2026-09-30-05:00",
            },
            as_of=date(2026, 8, 21),
        )
        self.assertEqual(valid_from, date(2026, 3, 16))
        self.assertEqual(valid_to, date(2026, 9, 30))

    def test_valid_to_boundary_remains_exclusive_with_timezone(self):
        with self.assertRaises(RTAuthorityError):
            verify_revision_currentness(
                {
                    "valid_from": "2026-03-16+02:00",
                    "valid_to": "2026-09-30+03:00",
                },
                as_of=date(2026, 9, 30),
            )

    def test_invalid_xml_schema_timezone_fails_closed(self):
        for value in ("2026-09-30+15:00", "2026-09-30+14:01", "2026-09-30+03:0"):
            with self.subTest(value=value), self.assertRaises(RTAuthorityError):
                verify_revision_currentness(
                    {
                        "valid_from": "2026-03-16+02:00",
                        "valid_to": value,
                    },
                    as_of=date(2026, 8, 21),
                )


if __name__ == "__main__":
    unittest.main()
