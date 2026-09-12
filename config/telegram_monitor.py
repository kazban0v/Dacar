"""Small, read-only Telegram interface for host monitoring."""
import html
import json
import os
import time
from datetime import datetime, timezone as dt_timezone
from hmac import compare_digest
from urllib import parse as urlparse
from urllib import request as urlrequest

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from config.monitoring import get_metrics


def _setting(name, env_name):
    value = getattr(settings, name, None)
    return value if value is not None else os.environ.get(env_name, '')


def _token():
    return str(_setting('TELEGRAM_BOT_TOKEN', 'TELEGRAM_BOT_TOKEN') or '').strip()


def _chat_id():
    return str(_setting('TELEGRAM_CHAT_ID', 'TELEGRAM_CHAT_ID') or '').strip()


def _webhook_secret():
    return str(_setting('TELEGRAM_WEBHOOK_SECRET', 'TELEGRAM_WEBHOOK_SECRET') or '').strip()


def _api(method, data):
    token = _token()
    if not token:
        return False
    endpoint = f'https://api.telegram.org/bot{token}/{method}'
    payload = urlparse.urlencode(data).encode()
    try:
        with urlrequest.urlopen(endpoint, data=payload, timeout=4) as response:
            return response.status == 200
    except Exception:
        return False


def _keyboard():
    return json.dumps({
        'inline_keyboard': [
            [{'text': '📊 Статус', 'callback_data': 'status'},
             {'text': '⚠️ Проблемы', 'callback_data': 'alerts'}],
            [{'text': '📈 История 24ч', 'callback_data': 'history'},
             {'text': '❓ Помощь', 'callback_data': 'help'}],
        ]
    })


def _bar(value):
    if not isinstance(value, (int, float)):
        return 'нет данных'
    filled = max(0, min(10, round(value / 10)))
    return f'{value:.1f}% ' + ('█' * filled) + ('░' * (10 - filled))


def _local_time(timestamp):
    return datetime.fromtimestamp(float(timestamp), dt_timezone.utc).astimezone().strftime('%d.%m.%Y %H:%M')


def _metric(value, suffix='%'):
    return 'нет данных' if value is None else f'{value:.1f}{suffix}'


def _status_text(payload):
    sample = payload.get('sample') or {}
    alerts = [item for item in payload.get('alerts', []) if item.get('active')]
    db = '✅ OK' if sample.get('database_ok', True) else '❌ недоступна'
    return (
        '<b>📊 DACAR · Состояние сервера</b>\n\n'
        f'<b>CPU</b>  {_bar(sample.get("cpu_percent"))}\n'
        f'<b>RAM</b>  {_bar(sample.get("memory_percent"))}\n'
        f'<b>Диск</b> {_bar(sample.get("disk_percent"))}\n'
        f'<b>Swap</b> {_bar(sample.get("swap_percent"))}\n\n'
        f'<b>База:</b> {db} · {(_metric(sample.get("database_ms"), " мс"))}\n'
        f'<b>Uptime:</b> {_uptime(sample.get("uptime"))}\n'
        f'<b>Обновлено:</b> {_local_time(sample.get("timestamp", time.time()))}\n\n'
        f'<b>Активных проблем:</b> {len(alerts)}'
    )


def _uptime(seconds):
    if not isinstance(seconds, (int, float)):
        return 'нет данных'
    days, remainder = divmod(int(seconds), 86400)
    hours, minutes = divmod(remainder, 3600)[0], divmod(remainder, 3600)[1] // 60
    return f'{days} дн. {hours} ч. {minutes} мин.'


def _alerts_text(payload):
    active = [item for item in payload.get('alerts', []) if item.get('active')]
    if not active:
        return '<b>⚠️ DACAR · Проблемы</b>\n\n✅ Активных проблем нет.'
    lines = ['<b>⚠️ DACAR · Активные проблемы</b>', '']
    for item in active:
        icon = '🚨' if item.get('severity') == 'error' else '⚠️'
        lines.extend([f'{icon} <b>{html.escape(item.get("title", "Проблема"))}</b>',
                      html.escape(item.get('message', '')), ''])
    return '\n'.join(lines).strip()


def _history_text(payload):
    points = payload.get('history') or []
    cpu = [item['cpu_percent'] for item in points if isinstance(item.get('cpu_percent'), (int, float))]
    memory = [item['memory_percent'] for item in points if isinstance(item.get('memory_percent'), (int, float))]
    disk = [item['disk_percent'] for item in points if isinstance(item.get('disk_percent'), (int, float))]
    if not points:
        return '<b>📈 DACAR · История 24ч</b>\n\nПока нет накопленной истории.'
    def range_text(values):
        return 'нет данных' if not values else f'{min(values):.1f}% — {max(values):.1f}%'
    return (
        '<b>📈 DACAR · История 24 часа</b>\n\n'
        f'<b>Точек:</b> {len(points)}\n'
        f'<b>CPU:</b> {range_text(cpu)}\n'
        f'<b>RAM:</b> {range_text(memory)}\n'
        f'<b>Диск:</b> {range_text(disk)}\n\n'
        'Данные берутся из лёгкого локального хранилища мониторинга.'
    )


def _help_text():
    return (
        '<b>❓ DACAR · Команды</b>\n\n'
        '/status — текущее состояние\n'
        '/alerts — активные проблемы\n'
        '/history — история за 24 часа\n'
        '/help — список команд\n\n'
        'Бот только показывает состояние и ничего не изменяет на сервере.'
    )


def _send_response(chat_id, text):
    return _api('sendMessage', {
        'chat_id': chat_id,
        'text': text,
        'parse_mode': 'HTML',
        'reply_markup': _keyboard(),
        'disable_web_page_preview': 'true',
    })


def _handle_command(chat_id, command):
    command = (command or '').strip().lower().split('@', 1)[0]
    if command in ('/start', '/help', 'help', '❓ помощь'):
        text = _help_text() if command != '/start' else (
            '<b>👋 DACAR · Мониторинг подключён</b>\n\nВыберите действие ниже или используйте /help.'
        )
    elif command in ('/status', 'status', '📊 статус'):
        text = _status_text(get_metrics())
    elif command in ('/alerts', 'alerts', '⚠️ проблемы'):
        text = _alerts_text(get_metrics())
    elif command in ('/history', 'history', '📈 история 24ч'):
        text = _history_text(get_metrics(history=True))
    else:
        text = _help_text()
    return _send_response(chat_id, text)


@csrf_exempt
@require_POST
def telegram_monitor_webhook(request, secret):
    expected = _webhook_secret()
    provided_header = request.headers.get('X-Telegram-Bot-Api-Secret-Token', '')
    if not expected or not compare_digest(str(secret), expected) or not compare_digest(provided_header, expected):
        return JsonResponse({'ok': False}, status=404)
    try:
        update = json.loads(request.body.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return JsonResponse({'ok': False}, status=400)

    callback = update.get('callback_query')
    message = callback.get('message') if callback else update.get('message')
    chat = (message or {}).get('chat') or {}
    chat_id = str(chat.get('id', ''))
    if not chat_id or chat_id != _chat_id():
        return JsonResponse({'ok': True})
    if callback:
        _api('answerCallbackQuery', {'callback_query_id': callback.get('id', '')})
        command = callback.get('data', '')
    else:
        command = (message or {}).get('text', '').split()[0] if (message or {}).get('text') else '/help'
    _handle_command(chat_id, command)
    return JsonResponse({'ok': True})
