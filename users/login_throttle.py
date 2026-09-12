"""Shared login attempt budgets for desktop, mobile and Django admin.

Reserve before password hashing, release on the actual user_logged_in signal.
Pair (IP + login) budget prevents one cashier's typos blocking another cashier.
A larger IP budget bounds username spraying. No global account lockout.
Counters are private HMAC digests, never passwords, usernames or raw IPs.
"""
import hashlib
import hmac
import ipaddress
import json
import logging
import math
import os
from pathlib import Path
import sqlite3
import time
import unicodedata

from django.conf import settings
from django.contrib.auth.signals import user_logged_in
from django.shortcuts import render
from django.utils.cache import add_never_cache_headers
from django.utils.deprecation import MiddlewareMixin

logger = logging.getLogger(__name__)


def client_ip(request):
    def normalize(value):
        try:
            ip = ipaddress.ip_address(value)
            return str(ip.ipv4_mapped or ip) if isinstance(ip, ipaddress.IPv6Address) else str(ip)
        except ValueError:
            return 'unknown'
    peer = normalize(request.META.get('REMOTE_ADDR', ''))
    if peer in settings.LOGIN_TRUSTED_PROXY_IPS:
        # Trust exactly the header overwritten by our immediate proxy, not
        # the user-controllable left edge of X-Forwarded-For.
        forwarded = normalize(request.META.get('HTTP_X_REAL_IP', ''))
        if forwarded != 'unknown':
            return forwarded
    return peer


def _digest(parts):
    value = json.dumps(parts, ensure_ascii=False, separators=(',', ':')).encode()
    return hmac.new(settings.SECRET_KEY.encode(), value, hashlib.sha256).hexdigest()


def _connect():
    path = Path(settings.LOGIN_THROTTLE_DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    os.close(fd)
    db = sqlite3.connect(path, timeout=0.25, isolation_level=None)
    db.execute('CREATE TABLE IF NOT EXISTS attempts (key TEXT PRIMARY KEY, count INTEGER NOT NULL, expires REAL NOT NULL)')
    db.execute('CREATE INDEX IF NOT EXISTS attempts_expiry ON attempts(expires)')
    return db


def reserve(ip, username):
    """Return (retry_seconds, ticket). Transaction makes parallel POSTs count."""
    username = unicodedata.normalize('NFKC', username.strip()).casefold()
    pair, address = _digest(['pair', ip, username]), _digest(['ip', ip])
    db = _connect()
    try:
        db.execute('BEGIN IMMEDIATE')
        now = time.time()
        db.execute('DELETE FROM attempts WHERE expires <= ?', (now,))
        rows = {r[0]: (r[1], r[2]) for r in db.execute('SELECT key, count, expires FROM attempts WHERE key IN (?, ?)', (pair, address))}
        retry = 0
        for key, limit in ((pair, settings.LOGIN_THROTTLE_PAIR_LIMIT), (address, settings.LOGIN_THROTTLE_IP_LIMIT)):
            count, expiry = rows.get(key, (0, now + settings.LOGIN_THROTTLE_WINDOW))
            if count >= limit:
                retry = max(retry, max(1, math.ceil(expiry - now)))
        if retry:
            db.execute('COMMIT')
            return retry, None
        # Bound disk growth under distributed abuse. Fail closed, don't clear
        # active budgets to make room (that would permit bypass).
        if db.execute('SELECT count(*) FROM attempts').fetchone()[0] >= 10000:
            raise sqlite3.OperationalError('Login budget capacity exceeded')
        ticket = []
        for key in (pair, address):
            count, expiry = rows.get(key, (0, now + settings.LOGIN_THROTTLE_WINDOW))
            db.execute('INSERT OR REPLACE INTO attempts VALUES (?, ?, ?)', (key, count + 1, expiry))
            ticket.append((key, expiry))
        db.execute('COMMIT')
        return 0, ticket
    finally:
        if db.in_transaction:
            db.execute('ROLLBACK')
        db.close()


def release_success(ticket):
    db = _connect()
    try:
        db.execute('BEGIN IMMEDIATE')
        # Reset successful account/IP pair, but retain other failures sharing IP.
        db.execute('DELETE FROM attempts WHERE key=? AND expires=?', ticket[0])
        db.execute('UPDATE attempts SET count=max(0,count-1) WHERE key=? AND expires=?', ticket[1])
        db.execute('COMMIT')
    finally:
        if db.in_transaction:
            db.execute('ROLLBACK')
        db.close()


def _on_login(sender, request, **kwargs):
    if request is not None:
        request._login_throttle_succeeded = True


class LoginThrottleMiddleware(MiddlewareMixin):
    def __init__(self, get_response):
        super().__init__(get_response)
        user_logged_in.connect(_on_login, dispatch_uid='dacar.login_throttle.success', weak=False)

    def process_view(self, request, view_func, view_args, view_kwargs):
        match = request.resolver_match
        if request.method != 'POST' or not match or match.view_name not in ('login', 'm_login', 'admin:login'):
            return None
        # Desktop/mobile views simply redirect already-authenticated users.
        # Don't apply this bypass to admin login: a cashier can reauthenticate there.
        if match.view_name != 'admin:login' and request.user.is_authenticated:
            return None
        try:
            retry, ticket = reserve(client_ip(request), request.POST.get('username', ''))
        except (OSError, sqlite3.Error):
            logger.error('Login throttle storage unavailable')
            return self._blocked(request, 30, unavailable=True)
        if retry:
            return self._blocked(request, retry)
        request._login_throttle_ticket = ticket
        return None

    def process_response(self, request, response):
        ticket = getattr(request, '_login_throttle_ticket', None)
        if ticket and getattr(request, '_login_throttle_succeeded', False):
            try:
                release_success(ticket)
            except (OSError, sqlite3.Error):
                # Login is already successful. Retain the reservation until TTL.
                logger.error('Could not release successful login reservation')
        return response

    @staticmethod
    def _blocked(request, retry, unavailable=False):
        response = render(request, 'users/login_limited.html', {
            'retry_minutes': max(1, math.ceil(retry / 60)),
            'unavailable': unavailable,
            'login_path': request.path,
        }, status=503 if unavailable else 429)
        response['Retry-After'] = str(retry)
        add_never_cache_headers(response)
        return response
