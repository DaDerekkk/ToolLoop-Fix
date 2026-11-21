"""Offline byte-transport checks only. Fixture is synthetic, not model output."""
from io import BytesIO
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from scripts import colab_receipt_transport as tx


class ReceiptTransportTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.source = Path(temp.name) / "toolloop_probe.json"
        self.target = Path(temp.name) / "new.json"
        self.nonce = "ba" * 16
        self.raw = (json.dumps(dict(schema_version=1, scope="single_public_tool_call_proposal_only",
                                    training_performed=False, tool_executed=False, status="failed",
                                    stage="synthetic_fixture_no_model"), indent=2) + "\n").encode()
        self.source.write_bytes(self.raw)
        self.frame = tx.emit(self.source, self.nonce).encode() + b"\n"

    def reject(self, data):
        with self.assertRaises(ValueError):
            tx.receive(BytesIO(data), self.nonce, self.target)
        self.assertFalse(self.target.exists())

    def test_lossless_noisy_stream(self):
        tx.receive(BytesIO(b"unretained CLI noise\n" + self.frame + b"done\n"), self.nonce, self.target)
        self.assertEqual(self.target.read_bytes(), self.raw)

    def test_missing_truncated_duplicate_and_foreign(self):
        for data in (b"unretained diagnostics only\n", self.frame[:-1], self.frame[:-9] + b"\n",
                     self.frame * 2, self.frame.replace(self.nonce.encode(), b"11" * 16)):
            with self.subTest(size=len(data)):
                self.reject(data)

    def test_changed_content_length_or_hash(self):
        fields = self.frame.rstrip().split(b":")
        for idx, value in ((2, b"1"), (3, b"0" * 64), (4, b"A" + fields[4][1:]),
                           (4, b"?" + fields[4][1:])):
            parts = fields.copy()
            parts[idx] = value
            self.reject(b":".join(parts) + b"\n")

    def test_bounds_and_public_schema(self):
        self.reject(b"x" * (tx.MAX_STREAM + 1))
        for data in (b"x" * (tx.MAX_BYTES + 1), b'{"private":"not a probe report"}'):
            self.source.write_bytes(data)
            with self.assertRaises(ValueError):
                tx.emit(self.source, self.nonce)

    def test_no_overwrite(self):
        self.target.write_bytes(b"old")
        with self.assertRaises(FileExistsError):
            tx.receive(BytesIO(self.frame), self.nonce, self.target)
        self.assertEqual(self.target.read_bytes(), b"old")

    def test_offline_cli_pair_and_no_diagnostic_leak(self):
        cmd = [sys.executable, tx.__file__, "--nonce", self.nonce]
        encoded = subprocess.run(cmd + ["--emit", self.source], capture_output=True, check=True)
        subprocess.run(cmd + ["--receive", self.target], input=encoded.stdout, capture_output=True, check=True)
        self.assertEqual(self.target.read_bytes(), self.raw)
        invalid = subprocess.run(cmd + ["--receive", self.target], input=b"untrusted noise\n", capture_output=True)
        self.assertNotEqual(invalid.returncode, 0)
        self.assertNotIn(b"untrusted", invalid.stdout + invalid.stderr)
