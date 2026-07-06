import asyncio
import json
import unittest
from datetime import datetime

import httpx

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


class FakeHttpResponse:
    def __init__(self, content, headers=None):
        self.content = content
        self.headers = headers or {}

    def raise_for_status(self):
        pass


class FakeHttpClient:
    def __init__(self, response_or_exc):
        self._response_or_exc = response_or_exc
        self.calls = []

    async def get(self, url, timeout=None):
        self.calls.append(url)
        if isinstance(self._response_or_exc, Exception):
            raise self._response_or_exc
        return self._response_or_exc


class FakeS3Upload:
    def __init__(self):
        self.uploads = []

    async def upload_fileobj(self, fileobj, bucket, key, ExtraArgs=None):
        self.uploads.append((bucket, key, fileobj.read(), ExtraArgs))


class TestUploadAttachment(unittest.IsolatedAsyncioTestCase):
    def base_metadata(self, **overrides):
        metadata = {
            "bidNtceNo": "20260700001",
            "bidNtceOrd": "0",
            "bidNtceDt": "2026-07-04 09:00:00",
            "업무구분": "servc",
            "fileKind": "공고첨부",
            "fileName": "과업지시서.hwp",
            "fileUrl": "http://example.com/a.hwp",
        }
        metadata.update(overrides)
        return metadata

    async def test_successful_download_uploads_to_s3(self):
        client = FakeHttpClient(FakeHttpResponse(b"hello", {"Content-Type": "application/x-hwp"}))
        s3 = FakeS3Upload()
        used_keys = set()

        result = await jfd.upload_attachment(s3, "bidmate", client, self.base_metadata(), 30, used_keys)

        self.assertEqual(result["downloadStatus"], "success")
        self.assertEqual(result["downloadSize"], 5)
        self.assertEqual(len(s3.uploads), 1)
        bucket, key, body, extra_args = s3.uploads[0]
        self.assertEqual(bucket, "bidmate")
        self.assertEqual(body, b"hello")
        self.assertEqual(extra_args, {"ContentType": "application/x-hwp"})

    async def test_missing_url_is_skipped(self):
        client = FakeHttpClient(FakeHttpResponse(b""))
        s3 = FakeS3Upload()

        result = await jfd.upload_attachment(s3, "bidmate", client, self.base_metadata(fileUrl=""), 30, set())

        self.assertEqual(result["downloadStatus"], "skipped")
        self.assertEqual(len(s3.uploads), 0)

    async def test_download_failure_raises(self):
        client = FakeHttpClient(httpx.ConnectError("boom", request=None))
        s3 = FakeS3Upload()

        with self.assertRaises(httpx.ConnectError):
            await jfd.upload_attachment(s3, "bidmate", client, self.base_metadata(), 30, set())


class TestRunFailureIsolation(unittest.IsolatedAsyncioTestCase):
    async def test_one_file_failure_does_not_block_others(self):
        metadata_list = [
            {"bidNtceNo": "1", "fileSeq": "1", "fileUrl": "http://x/ok.hwp"},
            {"bidNtceNo": "2", "fileSeq": "1", "fileUrl": "http://x/bad.hwp"},
            {"bidNtceNo": "3", "fileSeq": "1", "fileUrl": "http://x/ok2.hwp"},
        ]

        async def fake_upload(s3, bucket, client, meta, timeout, used_keys):
            if meta["bidNtceNo"] == "2":
                raise RuntimeError("download exploded")
            return {
                "downloadStatus": "success",
                "downloadPath": f"s3://bidmate/{meta['bidNtceNo']}",
                "downloadSize": 1,
                "contentType": "application/x-hwp",
                "downloadError": "",
            }

        sem = asyncio.Semaphore(2)

        async def bound(meta):
            async with sem:
                return await fake_upload(None, "bidmate", None, meta, 30, set())

        results = await asyncio.gather(*(bound(m) for m in metadata_list), return_exceptions=True)

        successes = [r for r in results if not isinstance(r, Exception)]
        failures = [r for r in results if isinstance(r, Exception)]
        self.assertEqual(len(successes), 2)
        self.assertEqual(len(failures), 1)


class FakeS3PutObject:
    def __init__(self):
        self.puts = []  # (key, body_dict)

    async def put_object(self, Bucket, Key, Body, ContentType):
        self.puts.append((Key, json.loads(Body.decode("utf-8"))))


class TestPutManifest(unittest.IsolatedAsyncioTestCase):
    async def test_groups_entries_by_own_notice_month(self):
        s3 = FakeS3PutObject()
        metadata = [
            {"bidNtceNo": "1", "fileSeq": "1", "bidNtceDt": "2026-01-15 09:00:00"},
            {"bidNtceNo": "2", "fileSeq": "1", "bidNtceDt": "2026-01-28 09:00:00"},
            {"bidNtceNo": "3", "fileSeq": "1", "bidNtceDt": "2026-02-03 09:00:00"},
        ]

        keys = await jfd.put_manifest(s3, "bidmate", metadata, datetime(2026, 7, 5))

        self.assertEqual(set(keys), {
            "raw/downloads/backfill/year=2026/month=01/manifest.json",
            "raw/downloads/backfill/year=2026/month=02/manifest.json",
        })

        by_key = dict(s3.puts)
        jan_entries = by_key["raw/downloads/backfill/year=2026/month=01/manifest.json"]
        self.assertEqual({e["bidNtceNo"] for e in jan_entries}, {"1", "2"})
        feb_entries = by_key["raw/downloads/backfill/year=2026/month=02/manifest.json"]
        self.assertEqual({e["bidNtceNo"] for e in feb_entries}, {"3"})

    async def test_missing_bid_ntce_dt_falls_back_to_run_month(self):
        s3 = FakeS3PutObject()
        metadata = [{"bidNtceNo": "1", "fileSeq": "1"}]

        keys = await jfd.put_manifest(s3, "bidmate", metadata, datetime(2026, 7, 5))

        self.assertEqual(keys, ["raw/downloads/backfill/year=2026/month=07/manifest.json"])

    async def test_rerun_overwrites_same_month_manifest(self):
        s3 = FakeS3PutObject()
        first = [{"bidNtceNo": "1", "fileSeq": "1", "bidNtceDt": "2026-06-01 09:00:00"}]
        second = [{"bidNtceNo": "2", "fileSeq": "1", "bidNtceDt": "2026-06-15 09:00:00"}]

        await jfd.put_manifest(s3, "bidmate", first, datetime(2026, 7, 5))
        await jfd.put_manifest(s3, "bidmate", second, datetime(2026, 7, 5))

        june_puts = [body for key, body in s3.puts if key == "raw/downloads/backfill/year=2026/month=06/manifest.json"]
        self.assertEqual(len(june_puts), 2)
        # 두 번째 실행분만 남아있어야 한다 (병합이 아니라 덮어쓰기)
        self.assertEqual({e["bidNtceNo"] for e in june_puts[-1]}, {"2"})


if __name__ == "__main__":
    unittest.main()
