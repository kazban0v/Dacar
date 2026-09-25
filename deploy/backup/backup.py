#!/usr/bin/env python3
"""Online SQLite snapshots. Source is always opened read-only; no restore code."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import time
from datetime import datetime, timezone
from urllib.parse import urlencode
from urllib.request import urlopen
from uuid import uuid4

DEFAULT_SOURCE = Path('/var/www/dacar/db.sqlite3')
DEFAULT_ROOT = Path('/var/backups/dacar')
KINDS = {'hourly': 48, 'daily': 14, 'pre-update': 20, 'manual': None}
NAME = re.compile(r'^dacar-(hourly|daily|pre-update|manual)-\d{8}T\d{12}Z-[0-9a-f]{8}\.sqlite3$')


def verify(path):
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as db:
        if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise RuntimeError('Snapshot integrity check failed')
        tables = [row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        return {table: db.execute('SELECT COUNT(*) FROM "' + table.replace('"', '""') + '"').fetchone()[0]
                for table in tables}


def sha256(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def prune(directory, kind):
    keep = KINDS[kind]
    if keep is None:
        return
    # Only exact names created by this utility, inside this kind's directory.
    candidates = sorted((p for p in directory.iterdir() if p.is_file() and not p.is_symlink()
                         and NAME.fullmatch(p.name) and p.name.startswith('dacar-' + kind + '-')), reverse=True)
    for path in candidates[keep:]:
        path.unlink()
        manifest = path.with_suffix('.json')
        if manifest.is_file() and not manifest.is_symlink():
            manifest.unlink()


def snapshot(source, root, kind):
    os.umask(0o077)
    source = source.resolve(strict=True)
    root = root.resolve()
    if source == root or root in source.parents:
        raise ValueError('Source must not be inside the backup directory')
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (root / '.lock').open('a') as lock:
        deadline = time.monotonic() + 120
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() > deadline:
                    raise TimeoutError('Another backup is still running')
                time.sleep(.2)
        directory = root / kind
        if directory.is_symlink():
            raise ValueError('Backup directory must not be a symlink')
        directory.mkdir(exist_ok=True, mode=0o700)
        if shutil.disk_usage(root).free < max(source.stat().st_size * 3, 20 * 1024 * 1024):
            raise RuntimeError('Not enough free disk space for a verified backup')
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        final = directory / f'dacar-{kind}-{stamp}-{uuid4().hex[:8]}.sqlite3'
        partial = final.with_suffix('.partial')
        started = time.monotonic()

        def progress(status, remaining, total):
            if time.monotonic() - started > 120:
                raise TimeoutError('Database backup exceeded 120 seconds')

        try:
            with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True, timeout=5) as src:
                with sqlite3.connect(partial) as dst:
                    src.backup(dst, pages=64, progress=progress, sleep=.1)
            counts = verify(partial)
            with partial.open('rb') as stream:
                os.fsync(stream.fileno())
            manifest = dict(format=1, created_utc=datetime.now(timezone.utc).isoformat(),
                kind=kind, source=str(source), filename=final.name, bytes=partial.stat().st_size,
                sha256=sha256(partial), integrity='ok', table_counts=counts,
                duration_seconds=round(time.monotonic()-started, 3))
            manifest_path = final.with_suffix('.json')
            with manifest_path.open('x') as stream:
                json.dump(manifest, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            partial.rename(final)
            fd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
            # Retention is applied only after a new, verified snapshot is durable.
            prune(directory, kind)
            print(json.dumps(manifest, ensure_ascii=False))
            return final
        finally:
            if partial.exists():
                partial.unlink()  # Incomplete destination only; never the source.


def notify(test=False):
    from dotenv import dotenv_values
    config = dotenv_values('/var/www/dacar/.env')
    token = config.get('TELEGRAM_BOT_TOKEN') or os.environ.get('TELEGRAM_BOT_TOKEN')
    chat = config.get('TELEGRAM_CHAT_ID') or os.environ.get('TELEGRAM_CHAT_ID')
    if not token or not chat:
        raise RuntimeError('Telegram is not configured')
    message = ('✅ DACAR: резервные копии настроены. Это проверка канала уведомлений. Рабочая база не заменялась.'
               if test else '⚠️ DACAR: ошибка резервного копирования. Проверьте журнал dacar-backup. Рабочая база не заменялась; предыдущие успешные копии сохранены.')
    payload = urlencode({'chat_id': chat, 'text': message}).encode()
    for attempt in range(3):
        try:
            with urlopen(f'https://api.telegram.org/bot{token}/sendMessage', data=payload, timeout=10) as response:
                if json.load(response).get('ok'):
                    print('Telegram notification delivered')
                    return
        except Exception:
            pass  # Never expose a token-bearing URL in logs.
        time.sleep(1)
    raise RuntimeError('Telegram delivery failed')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('kind', choices=[*KINDS, 'safe-pre-update', 'notify-failure', 'notify-test'])
    parser.add_argument('--source', type=Path, default=DEFAULT_SOURCE)
    parser.add_argument('--root', type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    try:
        if args.kind == 'safe-pre-update':
            try:
                snapshot(args.source, args.root, 'pre-update')
            except Exception as exc:
                print(f'PRE-UPDATE BACKUP FAILED: {type(exc).__name__}: {exc}', file=sys.stderr)
                try:
                    notify()
                except Exception as notify_exc:
                    print(f'NOTIFICATION FAILED: {notify_exc}', file=sys.stderr)
            return 0  # A backup problem must never keep the store offline.
        if args.kind.startswith('notify-'):
            notify(test=args.kind == 'notify-test')
        else:
            snapshot(args.source, args.root, args.kind)
    except Exception as exc:
        print(f'BACKUP FAILED: {type(exc).__name__}: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
