import contextlib
import io
from pathlib import Path
import sqlite3
import tempfile
import unittest

from backup import snapshot, verify, sha256


class BackupTests(unittest.TestCase):
    def test_copy_retention_and_source_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            source = folder / 'live.sqlite3'
            with sqlite3.connect(source) as db:
                db.execute('create table receipts (id integer primary key, name text)')
                db.execute('insert into receipts(name) values (?)', ('Чек покупателя',))
            original = sha256(source)
            root = folder / 'backups'
            with contextlib.redirect_stdout(io.StringIO()):
                permanent = snapshot(source, root, 'manual')
                for i in range(50):
                    last = snapshot(source, root, 'hourly')
                for i in range(16):
                    snapshot(source, root, 'daily')
            self.assertEqual(sha256(source), original)
            self.assertEqual(verify(last), {'receipts': 1})
            self.assertTrue(permanent.exists())
            self.assertEqual(len(list((root / 'hourly').glob('*.sqlite3'))), 48)
            self.assertEqual(len(list((root / 'daily').glob('*.sqlite3'))), 14)
            self.assertEqual(last.stat().st_mode & 0o777, 0o600)

    def test_failure_preserves_prior_copies_and_does_not_create_source(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            source = folder / 'missing.sqlite3'
            with self.assertRaises(FileNotFoundError):
                snapshot(source, folder / 'backups', 'hourly')
            self.assertFalse(source.exists())


if __name__ == '__main__':
    unittest.main()
