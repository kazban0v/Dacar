"""Low-cost host telemetry. No shell commands, process scans or business writes.

One shared sample per 10 seconds across workers, at most one history row per
minute for 24 hours. BEGIN IMMEDIATE serializes sampling across Gunicorn workers.
CPU/network deltas use persisted OS counters (not per-worker cpu_percent state).
"""
import json
import logging
import os
from pathlib import Path
import platform
import sqlite3
import time
from datetime import datetime, timezone as dt_timezone
from urllib import parse as urlparse
from urllib import request as urlrequest

from django.conf import settings
from django.db import connections, DatabaseError

logger = logging.getLogger(__name__)

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
    db.execute('''CREATE TABLE IF NOT EXISTS alerts (
        fingerprint TEXT PRIMARY KEY,
        severity TEXT NOT NULL,
        title TEXT NOT NULL,
        message TEXT NOT NULL,
        first_seen INTEGER NOT NULL,
        last_seen INTEGER NOT NULL,
        resolved_at INTEGER,
        active INTEGER NOT NULL DEFAULT 1
    )''')
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
    return {key: sample.get(key) for key in (
        'timestamp', 'cpu_percent', 'memory_percent', 'disk_percent',
        'swap_percent', 'network_rx', 'network_tx', 'database_ok',
    )}


def _alert_conditions(sample):
    """Stable threshold alerts; one event per condition, not one per sample."""
    conditions = []
    cpu = sample.get('cpu_percent')
    memory = sample.get('memory_percent')
    disk = sample.get('disk_percent')
    swap = sample.get('swap_percent')
    if not sample.get('database_ok', True):
        conditions.append(('database_down', 'error', 'База данных недоступна',
            'Проверочный запрос SELECT 1 не получил ответ.'))
    if isinstance(cpu, (int, float)) and cpu >= 98:
        conditions.append(('cpu_critical', 'error', 'Критическая загрузка CPU', f'CPU: {cpu:.1f}%'))
    elif isinstance(cpu, (int, float)) and cpu >= 90:
        conditions.append(('cpu_high', 'warning', 'Высокая загрузка CPU', f'CPU: {cpu:.1f}%'))
    if isinstance(memory, (int, float)) and memory >= 95:
        conditions.append(('memory_critical', 'error', 'Критически мало памяти', f'Память занята на {memory:.1f}%'))
    elif isinstance(memory, (int, float)) and memory >= 85:
        conditions.append(('memory_high', 'warning', 'Высокая загрузка памяти', f'Память занята на {memory:.1f}%'))
    if isinstance(disk, (int, float)) and disk >= 95:
        conditions.append(('disk_critical', 'error', 'Почти закончился диск', f'Диск занят на {disk:.1f}%'))
    elif isinstance(disk, (int, float)) and disk >= 85:
        conditions.append(('disk_high', 'warning', 'Мало свободного места', f'Диск занят на {disk:.1f}%'))
    if isinstance(swap, (int, float)) and swap >= 80:
        conditions.append(('swap_critical', 'error', 'Критически используется Swap', f'Swap занят на {swap:.1f}%'))
    elif isinstance(swap, (int, float)) and swap >= 50:
        conditions.append(('swap_high', 'warning', 'Высокое использование Swap', f'Swap занят на {swap:.1f}%'))
    return conditions


def _update_alerts(db, sample):
    now = int(sample.get('timestamp', time.time()))
    conditions = _alert_conditions(sample)
    active_keys = {item[0] for item in conditions}
    active_before = {
        row[0]: row for row in db.execute(
            'SELECT fingerprint, severity, title, message FROM alerts WHERE active=1'
        ).fetchall()
    }
    events = []
    for key, severity, title, message in conditions:
        if key not in active_before:
            events.append({'kind': 'new', 'fingerprint': key, 'severity': severity,
                           'title': title, 'message': message})
        db.execute('''INSERT INTO alerts
            (fingerprint, severity, title, message, first_seen, last_seen, resolved_at, active)
            VALUES (?, ?, ?, ?, ?, ?, NULL, 1)
            ON CONFLICT(fingerprint) DO UPDATE SET
              severity=excluded.severity, title=excluded.title, message=excluded.message,
              last_seen=excluded.last_seen, resolved_at=NULL, active=1''',
            (key, severity, title, message, now, now))
    # A condition disappearing is a useful recovery event, but does not spam.
    if active_keys:
        placeholders = ','.join('?' for _ in active_keys)
        resolved = db.execute(
            f'''SELECT fingerprint, severity, title, message FROM alerts
                WHERE active=1 AND fingerprint NOT IN ({placeholders})''',
            tuple(active_keys),
        ).fetchall()
        events.extend({'kind': 'resolved', 'fingerprint': row[0], 'severity': row[1],
                       'title': row[2], 'message': row[3]} for row in resolved)
        db.execute(f'''UPDATE alerts SET active=0, resolved_at=?
            WHERE active=1 AND fingerprint NOT IN ({placeholders})''', (now, *active_keys))
    else:
        resolved = db.execute(
            'SELECT fingerprint, severity, title, message FROM alerts WHERE active=1'
        ).fetchall()
        events.extend({'kind': 'resolved', 'fingerprint': row[0], 'severity': row[1],
                       'title': row[2], 'message': row[3]} for row in resolved)
        db.execute('UPDATE alerts SET active=0, resolved_at=? WHERE active=1', (now,))
    db.execute('DELETE FROM alerts WHERE active=0 AND resolved_at < ?', (now - RETENTION,))
    return events


def _send_telegram(events, sample):
    """Send only alert transitions; never send every telemetry sample."""
    enabled = getattr(settings, 'TELEGRAM_ALERTS_ENABLED', None)
    if enabled is None:
        enabled = os.environ.get('TELEGRAM_ALERTS_ENABLED', '').strip().lower() in {
            '1', 'true', 'yes', 'on'
        }
    if not enabled:
        return
    token = getattr(settings, 'TELEGRAM_BOT_TOKEN', '') or os.environ.get('TELEGRAM_BOT_TOKEN', '')
    chat_id = getattr(settings, 'TELEGRAM_CHAT_ID', '') or os.environ.get('TELEGRAM_CHAT_ID', '')
    if not token or not chat_id:
        return
    local_time = datetime.fromtimestamp(
        float(sample.get('timestamp', time.time())), dt_timezone.utc
    ).astimezone().strftime('%d.%m.%Y %H:%M')
    machine = sample.get('machine', 'DACAR')
    endpoint = f'https://api.telegram.org/bot{token}/sendMessage'
    for event in events:
        prefix = '🚨' if event['kind'] == 'new' else '✅'
        state = 'Новое событие' if event['kind'] == 'new' else 'Состояние восстановлено'
        text = (f'{prefix} DACAR — {state}\n{event["title"]}\n'
                f'{event["message"]}\nСервер: {machine}\nВремя: {local_time}')
        payload = urlparse.urlencode({'chat_id': chat_id, 'text': text}).encode()
        try:
            with urlrequest.urlopen(endpoint, data=payload, timeout=4) as response:
                if response.status != 200:
                    logger.warning('Telegram monitor notification returned HTTP %s', response.status)
        except Exception:
            # Monitoring must never fail because Telegram is unavailable.
            logger.warning('Telegram monitor notification failed', exc_info=True)


def _read_alerts(db):
    rows = db.execute('''SELECT fingerprint, severity, title, message,
        first_seen, last_seen, resolved_at, active FROM alerts
        WHERE active=1 OR resolved_at >= ? ORDER BY active DESC, last_seen DESC LIMIT 50''',
        (int(time.time()) - RETENTION,)).fetchall()
    keys = ('fingerprint', 'severity', 'title', 'message', 'first_seen',
            'last_seen', 'resolved_at', 'active')
    return [dict(zip(keys, row)) for row in rows]


def get_metrics(*, history=False):
    db = _connect()
    alert_events = []
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
                        alert_events = _update_alerts(db, sample)
                    db.execute('COMMIT')
                except Exception:
                    db.execute('ROLLBACK')
                    raise
        if alert_events:
            _send_telegram(alert_events, sample)
        payload = {'sample': {k: v for k, v in sample.items() if not k.startswith('_')},
                   'alerts': _read_alerts(db), 'interval': INTERVAL}
        if history:
            since = int((time.time() - RETENTION) // 60)
            payload['history'] = [json.loads(r[0]) for r in db.execute(
                'SELECT payload FROM history WHERE minute > ? ORDER BY minute LIMIT 1440', (since,)
            )]
        return payload
    finally:
        db.close()
