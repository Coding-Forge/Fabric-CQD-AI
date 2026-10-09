import argparse
import contextlib
import io
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from unittest.mock import Mock, patch

from cqd.graph import GraphHttpError
from cqd.probe import bounded_integer, collect, diagnose_listing, main, read_secret, sample_directory


TENANT_ID = "00000000-0000-0000-0000-000000000002"
RECORD_ID = "00000000-0000-0000-0000-000000000001"


class ProbeTests(unittest.TestCase):
    def test_exported_secret_does_not_require_terminal_or_prompt(self):
        with patch.dict(os.environ, {"my_secret": "synthetic-credential"}, clear=True), \
                patch("cqd.probe.sys.stdin.isatty", return_value=False), \
                patch("cqd.probe.getpass.getpass") as prompt:
            self.assertEqual(read_secret(argparse.ArgumentParser()), "synthetic-credential")
            prompt.assert_not_called()

    def test_missing_environment_secret_uses_hidden_prompt(self):
        with patch.dict(os.environ, {}, clear=True), \
                patch("cqd.probe.sys.stdin.isatty", return_value=True), \
                patch("cqd.probe.getpass.getpass", return_value="synthetic-credential") as prompt:
            self.assertEqual(read_secret(argparse.ArgumentParser()), "synthetic-credential")
            prompt.assert_called_once()

    def test_empty_environment_secret_fails_without_prompting(self):
        output = io.StringIO()
        with patch.dict(os.environ, {"my_secret": ""}, clear=True), \
                patch("cqd.probe.getpass.getpass") as prompt, \
                contextlib.redirect_stderr(output):
            with self.assertRaises(SystemExit) as error:
                read_secret(argparse.ArgumentParser())
            self.assertEqual(error.exception.code, 2)
            self.assertIn("credential is empty", output.getvalue())
            prompt.assert_not_called()

    def test_missing_secret_without_terminal_fails_explicitly(self):
        output = io.StringIO()
        with patch.dict(os.environ, {}, clear=True), \
                patch("cqd.probe.sys.stdin.isatty", return_value=False), \
                contextlib.redirect_stderr(output):
            with self.assertRaises(SystemExit) as error:
                read_secret(argparse.ArgumentParser())
            self.assertEqual(error.exception.code, 2)
            self.assertIn("Export my_secret", output.getvalue())

    def test_limits_collection_and_restricts_sample_files(self):
        graph = Mock()
        graph.list_records.return_value = iter([{"id": RECORD_ID}] * 5)
        graph.snapshot.return_value = {
            "record": {
                "id": RECORD_ID,
                "version": 1,
                "sessions": [],
                "participants_v2": [],
            },
            "responses": [],
        }
        with tempfile.TemporaryDirectory() as temporary, contextlib.redirect_stdout(io.StringIO()):
            root = Path(temporary) / "samples"
            self.assertEqual(collect(graph, TENANT_ID, 24, 2, root), 2)
            self.assertEqual(graph.snapshot.call_count, 2)
            folders = list(root.iterdir())
            self.assertEqual(len(folders), 1)
            files = list(folders[0].iterdir())
            self.assertEqual(len(files), 2)
            self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(folders[0].stat().st_mode), 0o700)
            for path in files:
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
                self.assertEqual(json.loads(path.read_text())["tenant_id"], TENANT_ID)

    def test_empty_window_is_explicit_and_creates_no_samples(self):
        graph = Mock()
        graph.list_records.return_value = iter([])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "samples"
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(collect(graph, TENANT_ID, 24, 3, root), 0)
            self.assertIn("Expanded record access has not been demonstrated", output.getvalue())
            self.assertFalse(root.exists())
            graph.snapshot.assert_not_called()

    def test_output_does_not_print_record_identifiers_or_bodies(self):
        graph = Mock()
        graph.list_records.return_value = iter([{"id": RECORD_ID}])
        graph.snapshot.return_value = {
            "record": {"id": RECORD_ID, "sessions": [], "participants_v2": []},
            "responses": [{"body": "synthetic-sensitive-body"}],
        }
        with tempfile.TemporaryDirectory() as temporary:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                collect(graph, TENANT_ID, 24, 1, Path(temporary) / "samples")
            self.assertNotIn(RECORD_ID, output.getvalue())
            self.assertNotIn("synthetic-sensitive-body", output.getvalue())

    def test_candidate_is_reported_before_detail_failure(self):
        graph = Mock()
        graph.list_records.return_value = iter([{"id": RECORD_ID}])
        graph.snapshot.side_effect = GraphHttpError(400, "get expanded call record", "BadRequest", None)
        with tempfile.TemporaryDirectory() as temporary:
            output = io.StringIO()
            with contextlib.redirect_stdout(output), self.assertRaises(GraphHttpError):
                collect(graph, TENANT_ID, 24, 1, Path(temporary) / "samples")
            self.assertIn("Listed sample candidate 1", output.getvalue())
            self.assertNotIn("no records matched", output.getvalue())
            self.assertNotIn(RECORD_ID, output.getvalue())

    def test_http_error_advice_is_status_specific(self):
        arguments = ["probe", "--tenant-id", TENANT_ID, "--client-id", RECORD_ID]
        for status in (400, 403):
            with self.subTest(status=status):
                output = io.StringIO()
                with patch("cqd.probe.sys.argv", arguments), \
                        patch("cqd.probe.read_secret", return_value="synthetic-credential"), \
                        patch("cqd.probe.ClientSecretCredential"), \
                        patch("cqd.probe.collect", side_effect=GraphHttpError(
                            status, "list call records", "BadRequest", None
                        )), contextlib.redirect_stderr(output):
                    self.assertEqual(main(), 1)
                self.assertIn("during list call records", output.getvalue())
                if status == 400:
                    self.assertNotIn("Check CallRecords.Read.All", output.getvalue())
                    self.assertIn("does not indicate missing permissions", output.getvalue())
                else:
                    self.assertIn("Check CallRecords.Read.All", output.getvalue())

    def test_listing_diagnostic_has_fixed_bounds_and_does_not_fetch_details(self):
        graph = Mock()
        graph.get.return_value = {"value": [{"id": RECORD_ID}], "@odata.nextLink": "synthetic-next-page"}
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(diagnose_listing(graph, 24), 0)
        urls = [call.args[0] for call in graph.get.call_args_list]
        self.assertEqual(len(urls), 4)
        self.assertFalse(urlsplit(urls[0]).query)
        filters = [parse_qs(urlsplit(url).query)["$filter"][0] for url in urls[1:]]
        self.assertEqual(filters[0].replace("+00:00", "Z"), filters[1])
        self.assertNotIn("+00:00", filters[2])
        self.assertNotIn(".", filters[2])
        self.assertIn("first_page_records=1", output.getvalue())
        self.assertIn("more_pages=yes", output.getvalue())
        self.assertNotIn(RECORD_ID, output.getvalue())
        graph.snapshot.assert_not_called()
        graph.list_records.assert_not_called()

    def test_listing_diagnostic_continues_after_failure_and_returns_failure(self):
        graph = Mock()
        graph.get.side_effect = [
            GraphHttpError(400, "list call records", "BadRequest", None),
            {"value": []},
            {"value": []},
            {"value": []},
        ]
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(diagnose_listing(graph, 24), 1)
        self.assertEqual(graph.get.call_count, 4)
        self.assertIn("FAILED", output.getvalue())
        self.assertIn("first_page_records=0", output.getvalue())

    def test_listing_diagnostic_rejects_malformed_collection(self):
        graph = Mock()
        graph.get.return_value = {"value": "invalid"}
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(diagnose_listing(graph, 24), 1)

    def test_diagnostic_cli_does_not_collect_or_create_samples(self):
        arguments = [
            "probe", "--tenant-id", TENANT_ID, "--client-id", RECORD_ID, "--diagnose-list"
        ]
        with patch("cqd.probe.sys.argv", arguments), \
                patch("cqd.probe.read_secret", return_value="synthetic-credential"), \
                patch("cqd.probe.ClientSecretCredential"), \
                patch("cqd.probe.diagnose_listing", return_value=1) as diagnostic, \
                patch("cqd.probe.collect") as collector:
            self.assertEqual(main(), 1)
            diagnostic.assert_called_once()
            collector.assert_not_called()

    def test_rejects_public_directory_and_symlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "samples"
            root.mkdir(mode=0o755)
            root.chmod(0o755)
            with self.assertRaisesRegex(ValueError, "private"):
                sample_directory(root)
            link = Path(temporary) / "link"
            link.symlink_to(root, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "symbolic link"):
                sample_directory(link)

    def test_cli_limits(self):
        parse = bounded_integer(1, 10)
        self.assertEqual(parse("3"), 3)
        for value in ("0", "11", "invalid"):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                parse(value)
