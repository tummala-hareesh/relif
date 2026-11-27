import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from relif.store import Store


class Clock:
    def __init__(self, start=datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc)):
        self.now = start

    def __call__(self):
        self.now += timedelta(seconds=1)
        return self.now


class StoreTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="relif-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = self.tmp / "home"
        self.store = Store.init(self.home, clock=Clock())
        self.addCleanup(self.store.close)

    def receipt(self, name="receipt.pdf", content=b"%PDF-1.4 hardware store 42.10"):
        path = self.tmp / "in" / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(content)
        return path
