import unittest

import json_file_download_backfill as jfd


class TestFormatOrd(unittest.TestCase):
    def test_pads_single_digit(self):
        self.assertEqual(jfd.format_ord("0"), "00")

    def test_keeps_two_digits(self):
        self.assertEqual(jfd.format_ord("12"), "12")

    def test_missing_value_defaults_to_00(self):
        self.assertEqual(jfd.format_ord(None), "00")
        self.assertEqual(jfd.format_ord(""), "00")


class TestFileStem(unittest.TestCase):
    def test_strips_extension(self):
        self.assertEqual(jfd.file_stem("과업지시서.hwp", "fallback"), "과업지시서")

    def test_no_extension_returns_name(self):
        self.assertEqual(jfd.file_stem("과업지시서", "fallback"), "과업지시서")

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

    def test_std_notice_without_filename_uses_bid_no_as_stem(self):
        metadata = self.base_metadata(fileKind="표준공고서", fileName="")
        key = jfd.file_s3_key("raw/downloads", metadata, "application/pdf", "http://x/std.pdf")
        self.assertEqual(
            key,
            "raw/downloads/year=2026/month=07/day=04/biz_div=servc/bidNtceNo=20260700001_ord=00/20260700001_표준공고서.pdf",
        )

    def test_duplicate_key_gets_numeric_suffix(self):
        used_keys = set()
        metadata = self.base_metadata()
        first = jfd.file_s3_key("raw/downloads", metadata, "application/x-hwp", "http://x/a.hwp", used_keys)
        second = jfd.file_s3_key("raw/downloads", metadata, "application/x-hwp", "http://x/a.hwp", used_keys)
        third = jfd.file_s3_key("raw/downloads", metadata, "application/x-hwp", "http://x/a.hwp", used_keys)

        self.assertEqual(first, "raw/downloads/year=2026/month=07/day=04/biz_div=servc/bidNtceNo=20260700001_ord=00/과업지시서_공고첨부.hwp")
        self.assertEqual(second, "raw/downloads/year=2026/month=07/day=04/biz_div=servc/bidNtceNo=20260700001_ord=00/과업지시서_공고첨부_2.hwp")
        self.assertEqual(third, "raw/downloads/year=2026/month=07/day=04/biz_div=servc/bidNtceNo=20260700001_ord=00/과업지시서_공고첨부_3.hwp")

    def test_without_used_keys_no_dedup_applied(self):
        metadata = self.base_metadata()
        first = jfd.file_s3_key("raw/downloads", metadata, "application/x-hwp", "http://x/a.hwp")
        second = jfd.file_s3_key("raw/downloads", metadata, "application/x-hwp", "http://x/a.hwp")
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
