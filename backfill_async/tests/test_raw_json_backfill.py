import unittest
from datetime import datetime

from backfill_async import raw_json_backfill as rjb


class TestS3DayJsonKey(unittest.TestCase):
    def test_builds_day_and_biz_div_path(self):
        day = datetime(2026, 6, 1)
        key = rjb.s3_day_json_key("raw/raw", "servc", day)
        self.assertEqual(key, "raw/raw/year=2026/month=06/day=01/biz_div=servc.json")


class TestGroupByDay(unittest.TestCase):
    def test_groups_records_by_notice_day(self):
        records = [
            {"bidNtceNo": "1", "bidNtceDt": "2026-06-01 09:00:00"},
            {"bidNtceNo": "2", "bidNtceDt": "2026-06-01 15:30:00"},
            {"bidNtceNo": "3", "bidNtceDt": "2026-06-02 10:00:00"},
        ]
        now = datetime(2026, 6, 3)

        groups = rjb.group_by_day(records, now)

        self.assertEqual(set(groups.keys()), {datetime(2026, 6, 1), datetime(2026, 6, 2)})
        self.assertEqual(len(groups[datetime(2026, 6, 1)]), 2)
        self.assertEqual(len(groups[datetime(2026, 6, 2)]), 1)

    def test_missing_notice_date_falls_back_to_now(self):
        records = [{"bidNtceNo": "1", "bidNtceDt": None}]
        now = datetime(2026, 6, 3, 12, 0, 0)

        groups = rjb.group_by_day(records, now)

        self.assertEqual(set(groups.keys()), {datetime(2026, 6, 3)})


class TestIsOpen(unittest.TestCase):
    def test_open_when_close_date_in_future(self):
        now = datetime(2026, 6, 1)
        record = {"bidClseDt": "2026-06-10 18:00:00"}
        self.assertTrue(rjb.is_open(record, now))

    def test_closed_when_close_date_in_past(self):
        now = datetime(2026, 6, 15)
        record = {"bidClseDt": "2026-06-10 18:00:00"}
        self.assertFalse(rjb.is_open(record, now))

    def test_open_when_close_date_missing(self):
        now = datetime(2026, 6, 1)
        self.assertTrue(rjb.is_open({}, now))


class TestIsExactInstitution(unittest.TestCase):
    def test_matches_exact_name(self):
        record = {"ntceInsttNm": "조달청"}
        self.assertTrue(rjb.is_exact_institution(record, "조달청"))

    def test_rejects_partial_match(self):
        record = {"ntceInsttNm": "조달청 서울지방조달청"}
        self.assertFalse(rjb.is_exact_institution(record, "조달청"))


class TestToDay(unittest.TestCase):
    def test_parses_hyphenated_date(self):
        self.assertEqual(rjb.to_day("2026-06-01"), datetime(2026, 6, 1))

    def test_parses_compact_date(self):
        self.assertEqual(rjb.to_day("20260601"), datetime(2026, 6, 1))


if __name__ == "__main__":
    unittest.main()
