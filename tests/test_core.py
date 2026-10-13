import json
import tempfile
import time
import unittest
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from ssh_runner import SSHRunner, read_channel
from storage import KeyStore, ServerStore


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.keys = KeyStore(self.root / "keys")
        self.keys.path("test_key").write_text("test fixture, not a real key")
        self.store = ServerStore(self.root / "servers.json", self.keys)

    def add(self):
        self.store.add("test", {"ip": "127.0.0.1", "user": "monitor", "key_name": "test_key"})

    def test_reject_paths(self):
        for name in ("../servers.json", "/tmp/key", "a/b", "a\\b", "..", ""):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.keys.path(name)

    def test_snapshot_survives_removal(self):
        self.add()
        snapshot = self.store.snapshot()
        self.store.remove("test")
        self.assertEqual(snapshot["test"]["ip"], "127.0.0.1")
        self.assertEqual(self.store.snapshot(), {})

    def test_snapshot_cannot_mutate_store(self):
        self.add()
        snapshot = self.store.snapshot()
        snapshot["test"]["ip"] = "changed"
        self.assertEqual(self.store.snapshot()["test"]["ip"], "127.0.0.1")

    def test_referenced_key_cannot_be_deleted(self):
        self.add()
        with self.assertRaises(ValueError):
            self.keys.delete("test_key", self.store.snapshot())
        self.assertTrue(self.keys.path("test_key").exists())

    def test_missing_key_cannot_be_added(self):
        with self.assertRaises(ValueError):
            self.store.add("test", {"ip": "localhost", "user": "m", "key_name": "missing"})

    def test_failed_save_keeps_previous_state(self):
        self.add()
        original = self.store.path.read_bytes()
        with patch("storage.os.replace", side_effect=OSError("simulated")):
            with self.assertRaises(OSError):
                self.store.remove("test")
        self.assertIn("test", self.store.snapshot())
        self.assertEqual(self.store.path.read_bytes(), original)

    def test_bad_json_is_not_overwritten(self):
        self.store.path.write_text("{")
        with self.assertRaises(json.JSONDecodeError):
            ServerStore(self.store.path, self.keys)
        self.assertEqual(self.store.path.read_text(), "{")

    def test_upload_never_overwrites_existing_key(self):
        staged = self.root / "staged"
        staged.write_text("validated by fake parser")
        fake = SimpleNamespace(PKey=SimpleNamespace(from_path=lambda path: object()))
        with patch.dict(sys.modules, {"paramiko": fake}):
            with self.assertRaises(ValueError):
                self.keys.install("test_key", staged)
        self.assertEqual(self.keys.path("test_key").read_text(), "test fixture, not a real key")

    def test_invalid_upload_leaves_no_key(self):
        staged = self.root / "staged"
        staged.write_text("invalid")
        fake = SimpleNamespace(PKey=SimpleNamespace(from_path=MagicMock(side_effect=ValueError)))
        with patch.dict(sys.modules, {"paramiko": fake}):
            with self.assertRaises(ValueError):
                self.keys.install("new_key", staged)
        self.assertFalse(self.keys.path("new_key").exists())


class FakeChannel:
    def __init__(self, output=b"ok", errors=b"warning", code=1):
        self.output, self.errors, self.code = output, errors, code
        self.closed = False

    def recv_ready(self):
        return bool(self.output)

    def recv_stderr_ready(self):
        return bool(self.errors)

    def recv(self, size):
        data, self.output = self.output[:size], self.output[size:]
        return data

    def recv_stderr(self, size):
        data, self.errors = self.errors[:size], self.errors[size:]
        return data

    def exit_status_ready(self):
        return True

    def recv_exit_status(self):
        return self.code

    def close(self):
        self.closed = True


class SSHOutputTests(unittest.TestCase):
    def test_keeps_both_streams_and_exit_code(self):
        channel = FakeChannel()
        result = read_channel(channel, time.monotonic() + 1)
        self.assertEqual((result.stdout, result.stderr, result.exit_code), ("ok", "warning", 1))
        self.assertTrue(channel.closed)

    def test_output_limit_closes_channel(self):
        channel = FakeChannel(output=b"x" * 20)
        with self.assertRaises(ValueError):
            read_channel(channel, time.monotonic() + 1, max_bytes=10)
        self.assertTrue(channel.closed)

    def test_timeout_closes_channel(self):
        channel = FakeChannel()
        with self.assertRaises(TimeoutError):
            read_channel(channel, time.monotonic() - 1)
        self.assertTrue(channel.closed)

    def test_auth_failure_closes_client_and_disables_key_fallback(self):
        client = MagicMock()
        client.connect.side_effect = RuntimeError("fake authentication failure")
        rejection = object()
        fake = SimpleNamespace(SSHClient=lambda: client, RejectPolicy=lambda: rejection)
        with tempfile.TemporaryDirectory() as directory:
            keys = KeyStore(Path(directory) / "keys")
            keys.path("test_key").write_text("fixture")
            runner = SSHRunner(keys, Path(directory) / "known_hosts")
            with patch.dict(sys.modules, {"paramiko": fake}):
                with self.assertRaises(RuntimeError):
                    runner.inspect({"ip": "localhost", "user": "test", "key_name": "test_key"}, ["uptime"])
        client.close.assert_called_once()
        client.set_missing_host_key_policy.assert_called_once_with(rejection)
        self.assertFalse(client.connect.call_args.kwargs["allow_agent"])
        self.assertFalse(client.connect.call_args.kwargs["look_for_keys"])

    def test_unknown_module_never_opens_connection(self):
        factory = MagicMock()
        fake = SimpleNamespace(SSHClient=factory)
        runner = SSHRunner(None, None)
        with patch.dict(sys.modules, {"paramiko": fake}):
            with self.assertRaises(KeyError):
                runner.inspect({}, ["not-whitelisted"])
        factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
