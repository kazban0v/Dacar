"""Low-cost host telemetry. No shell commands, process scans or business writes.

One shared sample per 10 seconds across workers, at most one history row per
minute for 24 hours. BEGIN IMMEDIATE serializes sampling across Gunicorn workers.
CPU/network deltas use persisted OS counters (not per-worker cpu_percent state).
"""
import json
import os
from pathlib import Path
import platform
import sqlite3
import time

from django.conf import settings
from django.db import connections, DatabaseError

INTERVAL = 10
RETENTION = 24 * 60 * 60


def _connect():
    path = Path(settings.MONITOR_DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Create with private permissions, without truncating an existing file.
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    os.close(fd)
    db = sqlite3.connect(path, timeout=0.2, isolation_level=None)
    db.execute('CREATE TABLE IF NOT EXISTS latest (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS history (minute INTEGER PRIMARY KEY, payload TEXT NOT NULL)')
    return db


def _collect(previous):
    import psutil

    now = time.time()
    boot = psutil.boot_time()
    times = psutil.cpu_times()._asdict()
    # Linux guest counters are already included in user/nice.
    total = sum(v for k, v in times.items() if k not in ('guest', 'guest_nice'))
    idle = times.get('idle', 0) + times.get('iowait', 0)
    net = psutil.net_io_counters(pernic=True)
    net = [v for k, v in net.items() if k not in ('lo', 'lo0')]
    recv, sent = sum(v.bytes_recv for v in net), sum(v.bytes_sent for v in net)
    cpu = rx = tx = None
    elapsed = now - previous.get('timestamp', now)
    old = previous.get('_counters', {})
    valid = old and old.get('boot') == boot and 0 < elapsed <= 120
    if valid:
        delta = total - old['total']
        if delta > 0 and idle >= old['idle']:
            cpu = round(max(0, min(100, 100 * (1 - (idle - old['idle']) / delta))), 1)
        if recv >= old['recv'] and sent >= old['sent']:
            rx, tx = (recv - old['recv']) / elapsed, (sent - old['sent']) / elapsed
    memory, swap = psutil.virtual_memory(), psutil.swap_memory()
    disk = psutil.disk_usage(str(settings.BASE_DIR))
    start = time.monotonic()
    db_ok = True
    try:
        with connections['default'].cursor() as cursor:
            cursor.execute('SELECT 1')
            cursor.fetchone()
    except DatabaseError:
        db_ok = False
    latency = round((time.monotonic() - start) * 1000, 2)
    return {
        'timestamp': now,
        'machine': settings.MONITOR_LABEL or ('Локальный Mac' if platform.system() == 'Darwin' else 'Сервер приложения'),
        'system': platform.system(), 'cpu_count': psutil.cpu_count() or 1,
        'uptime': max(0, int(now - boot)), 'cpu_percent': cpu,
        'memory_total': memory.total, 'memory_available': memory.available,
        'memory_percent': memory.percent,
        'swap_total': swap.total, 'swap_used': swap.used, 'swap_percent': swap.percent,
        'disk_total': disk.total, 'disk_free': disk.free, 'disk_percent': disk.percent,
        'network_rx': rx, 'network_tx': tx, 'database_ok': db_ok,
        'database_ms': latency if db_ok else None,
        '_counters': {'total': total, 'idle': idle, 'recv': recv, 'sent': sent, 'boot': boot},
    }


def _point(sample):
    return {key: sample[key] for key in (
        'timestamp', 'cpu_percent', 'memory_percent', 'network_rx', 'network_tx',
    )}


def get_metrics(*, history=False):
    db = _connect()
    try:
        # Cached reads do not acquire a write lock.
        row = db.execute('SELECT payload FROM latest WHERE id=1').fetchone()
        sample = json.loads(row[0]) if row else {}
        age = time.time() - sample.get('timestamp', 0)
        if not sample or age >= INTERVAL or age < 0:
            try:
                db.execute('BEGIN IMMEDIATE')
            except sqlite3.OperationalError:
                if not sample:
                    raise
                # Another worker is sampling; return last measurement as-is.
            else:
                try:
                    row = db.execute('SELECT payload FROM latest WHERE id=1').fetchone()
                    sample = json.loads(row[0]) if row else {}
                    age = time.time() - sample.get('timestamp', 0)
                    if not sample or age >= INTERVAL or age < 0:
                        sample = _collect(sample)
                        db.execute('INSERT OR REPLACE INTO latest VALUES (1, ?)', (json.dumps(sample),))
                        minute = int(sample['timestamp'] // 60)
                        # First observation of each minute, not a fabricated average.
                        db.execute('INSERT OR IGNORE INTO history VALUES (?, ?)', (minute, json.dumps(_point(sample))))
                        db.execute('DELETE FROM history WHERE minute <= ? OR minute > ?', (minute - 1440, minute))
                    db.execute('COMMIT')
                except Exception:
                    db.execute('ROLLBACK')
                    raise
        payload = {'sample': {k: v for k, v in sample.items() if not k.startswith('_')}, 'interval': INTERVAL}
        if history:
            since = int((time.time() - RETENTION) // 60)
            payload['history'] = [json.loads(r[0]) for r in db.execute(
                'SELECT payload FROM history WHERE minute > ? ORDER BY minute LIMIT 1440', (since,)
            )]
        return payload
    finally:
        db.close()
