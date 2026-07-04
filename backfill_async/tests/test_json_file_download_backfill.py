import unittest
from datetime import datetime

from backfill_async import json_file_download_backfill as jfd


class TestFormatOrd(unittest.TestCase):
    def test_pads_single_digit(self):
        self.assertEqual(jfd.format_ord("0"), "00")

    def test_missing_value_defaults_to_00(self):
        self.assertEqual(jfd.format_ord(None), "00")
        self.assertEqual(jfd.format_ord(""), "00")


class TestFileStem(unittest.TestCase):
    def test_strips_extension(self):
        self.assertEqual(jfd.file_stem("과업지시서.hwp", "fallback"), "과업지시서")

    def test_empty_name_uses_fallback(self):
        self.assertEqual(jfd.file_stem("", "fallback"), "fallback")
        self.assertEqual(jfd.file_stem(None, "fallback"), "fallback")


class TestFileS3Key(unittest.TestCase):
    def base_metadata(self, **overrides):
        metadata = {
            "bidNtceNo": "20260700001",
            "bidNtceOrd": "0",
            "bidNtceDt": "2026-07-04 09:00:00",
            "업무구분": "servc",
            "fileKind": "공고첨부",
            "fileName": "과업지시서.hwp",
        }
        metadata.update(overrides)
        return metadata

    def test_builds_expected_key(self):
        key = jfd.file_s3_key("raw/downloads", self.base_metadata(), "application/x-hwp", "http://x/a.hwp")
        self.assertEqual(
            key,
            "raw/downloads/year=2026/month=07/day=04/biz_div=servc/bidNtceNo=20260700001_ord=00/과업지시서_공고첨부.hwp",
        )

    def test_duplicate_key_gets_numeric_suffix(self):
        used_keys = set()
        metadata = self.base_metadata()
        first = jfd.file_s3_key("raw/downloads", metadata, "application/x-hwp", "http://x/a.hwp", used_keys)
        second = jfd.file_s3_key("raw/downloads", metadata, "application/x-hwp", "http://x/a.hwp", used_keys)
        self.assertNotEqual(first, second)


class FakeBody:
    def __init__(self, data: bytes):
        self._data = data

    async def read(self):
        return self._data


class FakeS3Client:
    """get_paginator/paginate/get_object만 흉내내는 최소 가짜 S3 클라이언트."""

    def __init__(self, pages_by_prefix, objects):
        self.pages_by_prefix = pages_by_prefix
        self.objects = objects

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        return self

    def paginate(self, Bucket, Prefix):
        pages = self.pages_by_prefix.get(Prefix, [])

        async def gen():
            for page in pages:
                yield page

        return gen()

    async def get_object(self, Bucket, Key):
        return {"Body": FakeBody(self.objects[Key])}


class TestIterCuratedRange(unittest.IsolatedAsyncioTestCase):
    async def test_yields_records_from_matching_day_prefix(self):
        import json as json_module

        prefix = "raw/curated/backfill/year=2026/month=06/day=01/"
        key = f"{prefix}biz_div=thng.json"
        record = {"bid_ntce_no": "1", "attachments": []}
        payload = json_module.dumps([record]).encode("utf-8")

        s3 = FakeS3Client(
            pages_by_prefix={prefix: [{"Contents": [{"Key": key}]}]},
            objects={key: payload},
        )

        results = [
            item
            async for item in jfd.iter_curated_range(
                s3, "bidmate", "raw/curated/backfill", datetime(2026, 6, 1), datetime(2026, 6, 1)
            )
        ]

        self.assertEqual(len(results), 1)
        got_key, got_record = results[0]
        self.assertEqual(got_key, key)
        self.assertEqual(got_record, record)

    async def test_ignores_non_json_keys(self):
        prefix = "raw/curated/backfill/year=2026/month=06/day=01/"
        s3 = FakeS3Client(
            pages_by_prefix={prefix: [{"Contents": [{"Key": f"{prefix}readme.txt"}]}]},
            objects={},
        )

        results = [
            item
            async for item in jfd.iter_curated_range(
                s3, "bidmate", "raw/curated/backfill", datetime(2026, 6, 1), datetime(2026, 6, 1)
            )
        ]

        self.assertEqual(results, [])


if __name__ == "__main__":
    unittest.main()
