import asyncio
import json
import unittest
from datetime import datetime

import httpx

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


class TestIsInstitutionMatch(unittest.TestCase):
    def test_matches_exact_name(self):
        record = {"ntceInsttNm": "조달청"}
        self.assertTrue(rjb.is_institution_match(record, "조달청"))

    def test_matches_regional_branch_of_same_institution(self):
        # 한국전력공사처럼 지역본부/사업본부 명의로 공고가 올라오는 기관은
        # 완전일치로는 걸러지므로, 접두일치로 하위 조직까지 포함해야 한다.
        record = {"ntceInsttNm": "한국전력공사 강원지역본부"}
        self.assertTrue(rjb.is_institution_match(record, "한국전력공사"))

    def test_rejects_unrelated_institution(self):
        record = {"ntceInsttNm": "국방부 국군재정관리단"}
        self.assertFalse(rjb.is_institution_match(record, "한국전력공사"))


class TestToDay(unittest.TestCase):
    def test_parses_hyphenated_date(self):
        self.assertEqual(rjb.to_day("2026-06-01"), datetime(2026, 6, 1))

    def test_parses_compact_date(self):
        self.assertEqual(rjb.to_day("20260601"), datetime(2026, 6, 1))


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def make_page_payload(items, total):
    return {"response": {"body": {"totalCount": total, "items": items}}}


class FakeFailThenSucceedClient:
    """처음 N-1번은 예외를 던지고 마지막에 성공하는 가짜 httpx client."""

    def __init__(self, fail_times, payload):
        self.fail_times = fail_times
        self.payload = payload
        self.calls = 0

    async def get(self, url, params=None, timeout=None):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise httpx.ConnectError("boom", request=None)
        return FakeResponse(self.payload)


class ErrorShapedResponseClient:
    """조달청 API가 정상 response 대신 nkoneps 에러 응답을 돌려주는 상황을 흉내낸다."""

    def __init__(self, result_code, result_msg):
        self.result_code = result_code
        self.result_msg = result_msg
        self.calls = 0

    async def get(self, url, params=None, timeout=None):
        self.calls += 1
        return FakeResponse(
            {
                "nkoneps.com.response.ResponseError": {
                    "header": {"resultCode": self.result_code, "resultMsg": self.result_msg}
                }
            }
        )


class TestFetchPageErrorResponse(unittest.IsolatedAsyncioTestCase):
    async def test_raises_immediately_without_retry_on_error_shaped_response(self):
        client = ErrorShapedResponseClient("07", "입력범위값 초과 에러")
        sem = asyncio.Semaphore(1)
        counter = rjb.CallCounter()

        with self.assertRaises(rjb.G2BApiError):
            await rjb.fetch_page(client, sem, counter, "op", "202601010000", "202606302359", "조달청", 1)

        self.assertEqual(client.calls, 1)  # 재시도 없이 즉시 실패
        self.assertEqual(counter.count, 1)


class TestFetchPageRetry(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # 지수 백오프 실제 대기를 없애 테스트를 빠르게 한다.
        self._orig_sleep = asyncio.sleep
        asyncio.sleep = lambda *_args, **_kwargs: self._orig_sleep(0)

    async def asyncTearDown(self):
        asyncio.sleep = self._orig_sleep

    async def test_succeeds_after_transient_failures(self):
        client = FakeFailThenSucceedClient(fail_times=2, payload=make_page_payload([{"bidNtceNo": "1"}], 1))
        sem = asyncio.Semaphore(1)
        counter = rjb.CallCounter()

        records, total = await rjb.fetch_page(client, sem, counter, "op", "202606010000", "202606012359", "조달청", 1)

        self.assertEqual(records, [{"bidNtceNo": "1"}])
        self.assertEqual(total, 1)
        self.assertEqual(client.calls, 3)
        self.assertEqual(counter.count, 3)

    async def test_raises_after_max_retry_exhausted(self):
        client = FakeFailThenSucceedClient(fail_times=99, payload=make_page_payload([], 0))
        sem = asyncio.Semaphore(1)
        counter = rjb.CallCounter()

        with self.assertRaises(RuntimeError):
            await rjb.fetch_page(client, sem, counter, "op", "202606010000", "202606012359", "조달청", 1)

        self.assertEqual(client.calls, rjb.MAX_RETRY)


class RoutingFakeClient:
    """operation/기관/페이지 조합별로 미리 정해둔 응답을 돌려주는 가짜 client."""

    def __init__(self, responses):
        # responses: {(operation, ntce_instt_nm, page_no): payload_dict}
        self.responses = responses
        self.calls = []

    async def get(self, url, params=None, timeout=None):
        operation = url.rsplit("/", 1)[-1]
        key = (operation, params["ntceInsttNm"], params["pageNo"])
        self.calls.append(key)
        return FakeResponse(self.responses[key])


class TestTwoStageFetch(unittest.IsolatedAsyncioTestCase):
    async def test_first_pages_covers_every_combo(self):
        responses = {}
        for op_key, operation in rjb.OPERATIONS.items():
            for inst in rjb.TOP10_INSTITUTIONS:
                responses[(operation, inst, 1)] = make_page_payload([{"bidNtceNo": f"{op_key}-{inst}"}], 1)

        client = RoutingFakeClient(responses)
        sem = asyncio.Semaphore(8)
        counter = rjb.CallCounter()

        first_pages = await rjb.fetch_first_pages(client, sem, counter, "202606010000", "202606012359")

        expected_keys = {
            (op_key, inst) for op_key in rjb.OPERATIONS for inst in rjb.TOP10_INSTITUTIONS
        }
        self.assertEqual(set(first_pages.keys()), expected_keys)
        records, total = first_pages[("thng", rjb.TOP10_INSTITUTIONS[0])]
        self.assertEqual(total, 1)

    async def test_remaining_pages_computed_from_total_count(self):
        op_key = "thng"
        operation = rjb.OPERATIONS[op_key]
        inst = rjb.TOP10_INSTITUTIONS[0]
        total = rjb.NUM_OF_ROWS * 2 + 5  # 3페이지 필요

        responses = {
            (operation, inst, 2): make_page_payload([{"bidNtceNo": "p2"}], total),
            (operation, inst, 3): make_page_payload([{"bidNtceNo": "p3"}], total),
        }
        client = RoutingFakeClient(responses)
        sem = asyncio.Semaphore(8)
        counter = rjb.CallCounter()

        first_pages = {(op_key, inst): ([{"bidNtceNo": "p1"}], total)}
        remaining = await rjb.fetch_remaining_pages(client, sem, counter, "202606010000", "202606012359", first_pages)

        self.assertEqual(len(remaining[(op_key, inst)]), 2)
        page2_records, _ = remaining[(op_key, inst)][0]
        page3_records, _ = remaining[(op_key, inst)][1]
        self.assertEqual(page2_records, [{"bidNtceNo": "p2"}])
        self.assertEqual(page3_records, [{"bidNtceNo": "p3"}])

    async def test_remaining_pages_empty_when_single_page(self):
        first_pages = {("thng", rjb.TOP10_INSTITUTIONS[0]): ([{"bidNtceNo": "p1"}], 1)}
        client = RoutingFakeClient({})
        sem = asyncio.Semaphore(8)
        counter = rjb.CallCounter()

        remaining = await rjb.fetch_remaining_pages(client, sem, counter, "202606010000", "202606012359", first_pages)

        self.assertEqual(remaining, {})


class SelectiveFailClient:
    """특정 (operation, 기관, 페이지) 조합만 실패시키는 가짜 client."""

    def __init__(self, responses, fail_keys):
        self.responses = responses
        self.fail_keys = fail_keys

    async def get(self, url, params=None, timeout=None):
        operation = url.rsplit("/", 1)[-1]
        key = (operation, params["ntceInsttNm"], params["pageNo"])
        if key in self.fail_keys:
            raise httpx.ConnectError("boom", request=None)
        return FakeResponse(self.responses[key])


class TestProcessDay(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._orig_sleep = asyncio.sleep
        asyncio.sleep = lambda *_args, **_kwargs: self._orig_sleep(0)

    async def asyncTearDown(self):
        asyncio.sleep = self._orig_sleep

    async def test_partial_failure_keeps_successful_records(self):
        good_inst = rjb.TOP10_INSTITUTIONS[0]
        bad_inst = rjb.TOP10_INSTITUTIONS[1]
        operation = rjb.OPERATIONS["thng"]

        responses = {}
        for op_key, op in rjb.OPERATIONS.items():
            for inst in rjb.TOP10_INSTITUTIONS:
                if op == operation and inst == bad_inst:
                    continue  # 이 조합은 fail_keys로 실패 처리
                record = {
                    "bidNtceNo": f"{op_key}-{inst}",
                    "ntceInsttNm": inst,
                    "bidNtceDt": "2026-06-01 09:00:00",
                }
                responses[(op, inst, 1)] = make_page_payload([record], 1)

        fail_keys = {(operation, bad_inst, p) for p in range(1, rjb.MAX_RETRY + 1)}
        client = SelectiveFailClient(responses, fail_keys)
        sem = asyncio.Semaphore(8)
        counter = rjb.CallCounter()

        by_operation, failures = await rjb.process_day(client, sem, counter, datetime(2026, 6, 1))

        self.assertIn("thng", by_operation)
        self.assertTrue(any(r["ntceInsttNm"] == good_inst for r in by_operation["thng"]))
        self.assertFalse(any(r["ntceInsttNm"] == bad_inst for r in by_operation["thng"]))

        self.assertEqual(len(failures), 1)
        op_key, inst, page_no, exc = failures[0]
        self.assertEqual((op_key, inst, page_no), ("thng", bad_inst, 1))
        self.assertIsInstance(exc, RuntimeError)


class FakeS3:
    def __init__(self):
        self.put_calls = []

    async def put_object(self, Bucket, Key, Body, ContentType):
        self.put_calls.append((Bucket, Key, json.loads(Body.decode("utf-8"))))


def _all_success_client(day):
    responses = {}
    for op_key, op in rjb.OPERATIONS.items():
        for inst in rjb.TOP10_INSTITUTIONS:
            record = {
                "bidNtceNo": f"{op_key}-{inst}-{day:%Y%m%d}",
                "ntceInsttNm": inst,
                "bidClseDt": "2099-01-01 00:00:00",
                "bidNtceDt": f"{day:%Y-%m-%d} 09:00:00",
            }
            responses[(op, inst, 1)] = make_page_payload([record], 1)
    return RoutingFakeClient(responses)


class _AsyncClientCtx:
    """httpx.AsyncClient(...)를 흉내내는 async context manager 래퍼."""

    def __init__(self, fake_client):
        self._fake_client = fake_client

    async def __aenter__(self):
        return self._fake_client

    async def __aexit__(self, *exc_info):
        return False


class TestCollectRange(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._orig_sleep = asyncio.sleep
        asyncio.sleep = lambda *_args, **_kwargs: self._orig_sleep(0)
        self._orig_session = rjb.s3_session
        self._orig_async_client = httpx.AsyncClient
        self._orig_budget = rjb.CALL_BUDGET
        self.fake_s3 = FakeS3()

        class _FakeSession:
            def __init__(self, s3):
                self._s3 = s3

            def client(self, name):
                return self

            async def __aenter__(self):
                return self._s3

            async def __aexit__(self, *exc_info):
                return False

        rjb.s3_session = lambda: _FakeSession(self.fake_s3)

    async def asyncTearDown(self):
        asyncio.sleep = self._orig_sleep
        rjb.s3_session = self._orig_session
        httpx.AsyncClient = self._orig_async_client
        rjb.CALL_BUDGET = self._orig_budget

    async def test_processes_full_range_when_under_budget(self):
        start = datetime(2026, 6, 1)
        end = datetime(2026, 6, 2)

        httpx.AsyncClient = lambda *a, **kw: _AsyncClientCtx(_all_success_client(start))

        had_failure, stopped_early = await rjb.collect_range(start, end, "bidmate", 8)

        self.assertFalse(had_failure)
        self.assertFalse(stopped_early)
        self.assertTrue(self.fake_s3.put_calls)

    async def test_stops_early_past_call_budget(self):
        start = datetime(2026, 6, 1)
        end = datetime(2026, 6, 5)

        httpx.AsyncClient = lambda *a, **kw: _AsyncClientCtx(_all_success_client(start))
        rjb.CALL_BUDGET = 5  # 하루 처리(기관10*업무4=40콜)만으로도 즉시 초과하도록 낮춤

        had_failure, stopped_early = await rjb.collect_range(start, end, "bidmate", 8)

        self.assertFalse(had_failure)
        self.assertTrue(stopped_early)


class TestParseArgs(unittest.TestCase):
    def test_default_concurrency_is_eight(self):
        import sys as sys_module

        orig_argv = sys_module.argv
        sys_module.argv = ["raw_json_backfill.py", "--start", "2026-06-01"]
        try:
            args = rjb.parse_args()
        finally:
            sys_module.argv = orig_argv

        self.assertEqual(args.concurrency, 8)
        self.assertEqual(args.start, "2026-06-01")

    def test_concurrency_override(self):
        import sys as sys_module

        orig_argv = sys_module.argv
        sys_module.argv = ["raw_json_backfill.py", "--start", "2026-06-01", "--concurrency", "3"]
        try:
            args = rjb.parse_args()
        finally:
            sys_module.argv = orig_argv

        self.assertEqual(args.concurrency, 3)


if __name__ == "__main__":
    unittest.main()
