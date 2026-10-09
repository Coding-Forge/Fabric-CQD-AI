import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import requests

from cqd.graph import GRAPH_ROOT, GraphClient, GraphError, GraphHttpError


RECORD_ID = "00000000-0000-0000-0000-000000000001"
BASE = f"{GRAPH_ROOT}/v1.0/communications/callRecords"


def response(body, status=200, headers=None):
    return Mock(
        status_code=status,
        headers=headers or {},
        text=json.dumps(body),
        json=Mock(return_value=body),
    )


class GraphTests(unittest.TestCase):
    def setUp(self):
        self.session = Mock(spec=requests.Session)
        self.sleep = Mock()
        self.client = GraphClient(lambda: "synthetic-token", self.session, self.sleep)

    def test_list_follows_all_pages(self):
        self.session.get.side_effect = [
            response({"value": [{"id": "a"}], "@odata.nextLink": f"{BASE}?page=2"}),
            response({"value": [{"id": "b"}]}),
        ]
        self.assertEqual(list(self.client.collection(BASE)), [{"id": "a"}, {"id": "b"}])

    def test_nested_pages_and_participants_preserve_raw_responses(self):
        self.session.get.side_effect = [
            response(
                {
                    "id": RECORD_ID,
                    "version": 2,
                    "sessions": [
                        {
                            "id": "s1",
                            "segments": [{"id": "g1"}],
                            "segments@odata.nextLink": f"{BASE}/{RECORD_ID}/sessions/s1/segments?page=2",
                        }
                    ],
                    "sessions@odata.nextLink": f"{BASE}/{RECORD_ID}/sessions?page=2",
                }
            ),
            response({"value": [{"id": "s2", "segments": []}]}),
            response({"value": [{"id": "g2"}]}),
            response({"value": [{"id": "p1"}], "@odata.nextLink": f"{BASE}/{RECORD_ID}/participants_v2?page=2"}),
            response({"value": [{"id": "p2"}]}),
            response({"id": RECORD_ID, "version": 2}),
        ]
        snapshot = self.client.snapshot(RECORD_ID)
        record = snapshot["record"]
        self.assertEqual(len(record["sessions"]), 2)
        self.assertEqual(record["sessions"][0]["segments"], [{"id": "g1"}, {"id": "g2"}])
        self.assertEqual(record["participants_v2"], [{"id": "p1"}, {"id": "p2"}])
        self.assertEqual(len(snapshot["responses"]), 6)
        self.assertIn("sessions@odata.nextLink", json.loads(snapshot["responses"][0]["body"]))

    def test_changing_version_rejects_snapshot(self):
        self.session.get.side_effect = [
            response({"id": RECORD_ID, "version": 1, "sessions": []}),
            response({"value": []}),
            response({"id": RECORD_ID, "version": 2}),
        ]
        with self.assertRaisesRegex(GraphError, "version changed"):
            self.client.snapshot(RECORD_ID)

    def test_retry_after_is_honored(self):
        self.session.get.side_effect = [
            response({}, 429, {"Retry-After": "17"}),
            response({"value": []}),
        ]
        self.assertEqual(list(self.client.collection(BASE)), [])
        self.sleep.assert_called_once_with(17.0)

    def test_http_date_retry_after(self):
        future = datetime.now(timezone.utc) + timedelta(seconds=60)
        delay = GraphClient._retry_delay(future.strftime("%a, %d %b %Y %H:%M:%S GMT"), 0)
        self.assertGreater(delay, 58)
        self.assertLessEqual(delay, 60)

    def test_invalid_retry_after_is_explicit(self):
        for header in ("invalid", "-1", "nan", "inf"):
            with self.subTest(header=header), self.assertRaises(GraphError):
                GraphClient._retry_delay(header, 0)

    def test_throttling_exhaustion_waits_before_redelivery(self):
        self.session.get.return_value = response({}, 429, {"Retry-After": "1"})
        with self.assertRaisesRegex(GraphError, "retries exhausted"):
            self.client.get(BASE)
        self.assertEqual(self.session.get.call_count, 5)
        self.assertEqual(self.sleep.call_count, 5)

    def test_transport_errors_do_not_leak_request_details(self):
        self.session.get.side_effect = requests.ConnectionError("sensitive request detail")
        with self.assertRaisesRegex(GraphError, "^Graph transport retries exhausted$"):
            self.client.get(BASE)

    def test_rejects_other_clouds_and_redirects(self):
        for url in (
            "https://graph.microsoft.com/v1.0/communications/callRecords",
            "https://example.org/v1.0/communications/callRecords",
            "http://graph.microsoft.us/v1.0/communications/callRecords",
            "https://graph.microsoft.us/v1.0/users",
            f"{BASE}Unexpected",
        ):
            with self.subTest(url=url), self.assertRaises(GraphError):
                self.client.get(url)
        self.session.get.assert_not_called()
        self.session.get.return_value = response({}, 302)
        with self.assertRaisesRegex(GraphError, "HTTP 302"):
            self.client.get(BASE)
        self.assertFalse(self.session.get.call_args.kwargs["allow_redirects"])

    def test_cycle_and_malformed_collection(self):
        self.session.get.return_value = response({"value": [], "@odata.nextLink": BASE})
        with self.assertRaisesRegex(GraphError, "cycle"):
            list(self.client.collection(BASE))
        self.session.get.return_value = response({"value": "invalid"})
        with self.assertRaisesRegex(GraphError, "object array"):
            list(self.client.collection(BASE))

    def test_http_errors_hide_body(self):
        self.session.get.return_value = response({"error": "sensitive detail"}, 403)
        with self.assertRaises(GraphHttpError) as error:
            self.client.get(BASE)
        self.assertEqual(error.exception.status, 403)
        self.assertEqual(error.exception.operation, "list call records")
        self.assertNotIn("sensitive detail", str(error.exception))

    def test_error_reports_operation_code_and_request_id_without_call_data(self):
        request_id = "00000000-0000-0000-0000-000000000003"
        self.session.get.return_value = response(
            {"error": {"code": "BadRequest", "message": "sensitive caller@example.org"}},
            400,
            {"request-id": request_id},
        )
        cases = (
            (BASE, "list call records"),
            (f"{BASE}/{RECORD_ID}?$expand=sessions($expand=segments)", "get expanded call record"),
            (f"{BASE}/{RECORD_ID}/participants_v2", "list participants"),
            (f"{BASE}/{RECORD_ID}/sessions?page=2", "list sessions"),
            (f"{BASE}/{RECORD_ID}/sessions/s1/segments?page=2", "list segments"),
            (f"{BASE}/{RECORD_ID}", "get call-record details"),
        )
        for url, operation in cases:
            with self.subTest(operation=operation):
                with self.assertRaises(GraphHttpError) as error:
                    self.client.get(url)
                self.assertEqual(error.exception.operation, operation)
                self.assertEqual(error.exception.error_code, "BadRequest")
                self.assertEqual(error.exception.request_id, request_id)
                self.assertNotIn(RECORD_ID, str(error.exception))
                self.assertNotIn("caller@example.org", str(error.exception))

    def test_error_diagnostics_reject_unsafe_fields_and_non_json_bodies(self):
        self.session.get.return_value = response(
            {"error": {"code": "caller@example.org\nsecret"}},
            400,
            {"request-id": "sensitive"},
        )
        with self.assertRaises(GraphHttpError) as error:
            self.client.get(BASE)
        self.assertIsNone(error.exception.error_code)
        self.assertIsNone(error.exception.request_id)
        self.session.get.return_value.json.side_effect = ValueError("sensitive")
        with self.assertRaises(GraphHttpError):
            self.client.get(BASE)

    def test_invalid_json(self):
        self.session.get.return_value = response({})
        self.session.get.return_value.json.side_effect = ValueError("sensitive detail")
        with self.assertRaisesRegex(GraphError, "^Graph response is not valid JSON$"):
            self.client.get(BASE)

    def test_invalid_window(self):
        now = datetime.now(timezone.utc)
        with self.assertRaises(ValueError):
            self.client.list_records(now, now)
        with self.assertRaises(ValueError):
            self.client.list_records(now.replace(tzinfo=None), now)

    def test_default_listing_url_preserves_format_and_utc_z_preserves_bounds(self):
        start = datetime(2026, 10, 7, 12, 30, 1, 123456, tzinfo=timezone.utc)
        end = start + timedelta(hours=24)
        default_url = GraphClient.record_list_url(start, end)
        z_url = GraphClient.record_list_url(start, end, utc_z=True)
        offset_filter = parse_qs(urlsplit(default_url).query)["$filter"][0]
        z_filter = parse_qs(urlsplit(z_url).query)["$filter"][0]
        self.assertEqual(
            offset_filter,
            "startDateTime ge 2026-10-07T12:30:01.123456+00:00 "
            "and startDateTime lt 2026-10-08T12:30:01.123456+00:00",
        )
        self.assertEqual(offset_filter.replace("+00:00", "Z"), z_filter)

    def test_missing_sessions_is_not_a_complete_snapshot(self):
        self.session.get.return_value = response({"id": RECORD_ID, "version": 1})
        with self.assertRaisesRegex(GraphError, "missing sessions"):
            self.client.snapshot(RECORD_ID)


if __name__ == "__main__":
    unittest.main()
