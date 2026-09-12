"""Optional ESC/POS transport for explicitly configured, compatible printers.

EPT371U's installed TSPL2 driver must use the browser/OS print path instead.
Queue acceptance is not proof of a physical print.
"""
import os
import shutil
import subprocess
import textwrap
from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.utils import timezone

try:
    import win32print
    HAS_WIN32PRINT = True
except ImportError:
    win32print = None
    HAS_WIN32PRINT = False

ESC_INIT = b'\x1b\x40'
ESC_ALIGN_LEFT = b'\x1b\x61\x00'
ESC_ALIGN_CENTER = b'\x1b\x61\x01'
ESC_BOLD_ON = b'\x1b\x45\x01'
ESC_BOLD_OFF = b'\x1b\x45\x00'
GS_TEXT_NORMAL = b'\x1d\x21\x00'
GS_TEXT_DOUBLE_HEIGHT = b'\x1d\x21\x01'
GS_CUT_FULL = b'\x1d\x56\x41\x00'


def _cups(args):
    return subprocess.run(args, capture_output=True, text=True, timeout=10,
                          env={**os.environ, 'LC_ALL': 'C', 'LANG': 'C'})


def get_target_printer_name():
    configured = getattr(settings, 'THERMAL_PRINTER_NAME', None)
    if configured:
        return configured
    if HAS_WIN32PRINT:
        try:
            return win32print.GetDefaultPrinter()
        except Exception as exc:
            raise RuntimeError('Принтер по умолчанию не настроен.') from exc
    if shutil.which('lpstat'):
        result = _cups(['lpstat', '-d'])
        if result.returncode == 0 and ':' in result.stdout:
            return result.stdout.split(':', 1)[1].strip()
    raise RuntimeError('Принтер на сервере не настроен. Используйте печать через браузер.')


def build_escpos_bytes_for_order(order, shop_info=None):
    width = 42
    shop = shop_info or {
        'name': settings.SHOP_NAME, 'tagline': settings.SHOP_TAGLINE,
        'address': settings.SHOP_ADDRESS, 'phone': settings.SHOP_PHONE,
    }
    raw = bytearray(ESC_INIT)
    # Single, configurable Cyrillic table. No conflicting table selection commands.
    raw.extend(b'\x1bt' + bytes([getattr(settings, 'THERMAL_CODEPAGE', 17)]))
    # Set movement units to 203/inch, then feed 10 mm before the first printed line.
    raw.extend(b'\x1dP\xcb\xcb')
    raw.extend(b'\x1bJ\x50')

    def clean(value):
        return ''.join(c if c.isprintable() else ' ' for c in str(value))

    def line(value='', center=False, bold=False, large=False):
        raw.extend(ESC_ALIGN_CENTER if center else ESC_ALIGN_LEFT)
        raw.extend(GS_TEXT_DOUBLE_HEIGHT if large else GS_TEXT_NORMAL)
        raw.extend(ESC_BOLD_ON if bold else ESC_BOLD_OFF)
        for part in textwrap.wrap(clean(value), width) or ['']:
            raw.extend((part + '\n').encode('cp866', errors='replace'))

    def pair(label, value, bold=False, large=False):
        left, right = clean(label), clean(value)
        if len(left) + len(right) + 1 > width:
            line(left, bold=bold)
            line(right.rjust(width), bold=bold, large=large)
        else:
            line(left + ' ' * (width - len(left) - len(right)) + right, bold=bold, large=large)

    def money(value):
        number = Decimal(str(value)).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
        return f'{number:,.2f}'.replace(',', ' ').replace('.', ',') + ' тг'

    def date(value):
        if value is None:
            return '—'
        return (timezone.localtime(value) if timezone.is_aware(value) else value).strftime('%d.%m.%Y %H:%M')

    def person(user):
        return user.get_full_name() or user.username if user else 'Удалённый сотрудник'

    line(shop['name'], center=True, bold=True, large=True)
    if shop.get('tagline'):
        line(shop['tagline'], center=True)
    line(shop['address'], center=True)
    line('Тел: ' + shop['phone'], center=True)
    refunded = order.status == 'REFUNDED'
    cancelled = order.status == 'CANCELLED'
    line('ВОЗВРАТ' if refunded else ('ЧЕК ОТМЕНЕН' if cancelled else 'ЧЕК ПРОДАЖИ'), center=True, bold=True)
    pair('Чек №', order.order_number)
    pair('Дата продажи', date(order.created_at))
    pair('Кассир', person(order.cashier))
    if refunded:
        pair('Дата возврата', date(order.refunded_at))
        pair('Оформил', person(order.refunded_by))
    line('-' * width)
    for index, item in enumerate(order.items.all(), 1):
        line(f'{index}. {item.product.name if item.product else "Удалённый товар"}', bold=True)
        qty = format(item.quantity, 'f').rstrip('0').rstrip('.') if '.' in str(item.quantity) else str(item.quantity)
        line((qty + ' x ' + money(item.unit_price)).rjust(width))
        if item.discount_amount:
            pair('Скидка на позицию', money(item.discount_amount))
        line(('= ' + money(item.total_amount)).rjust(width), bold=True)
        line()
    line('-' * width)
    pair('Без скидки', money(order.subtotal_amount))
    if order.discount_amount:
        pair('Скидка на чек', money(order.discount_amount))
    pair('ВОЗВРАТ' if refunded else 'ИТОГО', money(order.total_amount), bold=True, large=True)
    pair('Оплата продажи' if refunded else 'Оплата', order.get_payment_method_display())
    if order.status == 'COMPLETED' and order.payment_method == 'CASH':
        pair('Получено', money(order.paid_amount))
        pair('Сдача', money(order.change_amount))
    if refunded:
        line('Причина возврата: ' + (order.refund_reason or '—'))
    line('-' * width)
    line('ВОЗВРАТ ОФОРМЛЕН' if refunded else ('ОПЕРАЦИЯ ОТМЕНЕНА' if cancelled else 'СПАСИБО ЗА ПОКУПКУ!'), center=True, bold=True)
    line('Сохраните чек', center=True)
    raw.extend(GS_TEXT_NORMAL)
    # 15 mm of explicit paper feed, then advance to the cutter/tear position.
    raw.extend(b'\x1bJ\x78')
    raw.extend(GS_CUT_FULL)
    return bytes(raw)


def send_raw_bytes_to_printer(raw_data, printer_name=None):
    if getattr(settings, 'THERMAL_PRINT_TRANSPORT', 'browser') != 'escpos':
        raise RuntimeError('Прямая ESC/POS-печать не настроена. Используйте печать через драйвер принтера.')
    name = printer_name or get_target_printer_name()
    if HAS_WIN32PRINT:
        handle = win32print.OpenPrinter(name)
        try:
            info = win32print.GetPrinter(handle, 2)
            if info['Status'] & (win32print.PRINTER_STATUS_OFFLINE | win32print.PRINTER_STATUS_ERROR):
                raise RuntimeError('Принтер офлайн или сообщает об ошибке.')
            win32print.StartDocPrinter(handle, 1, ('DACAR Receipt', None, 'RAW'))
            win32print.StartPagePrinter(handle)
            written = win32print.WritePrinter(handle, raw_data)
            win32print.EndPagePrinter(handle)
            win32print.EndDocPrinter(handle)
            if written != len(raw_data):
                raise RuntimeError('Данные чека переданы не полностью.')
        finally:
            win32print.ClosePrinter(handle)
        return True

    if not shutil.which('lpstat') or not shutil.which('lpr'):
        raise RuntimeError('Служба печати недоступна на сервере.')
    status = _cups(['lpstat', '-p', name])
    if status.returncode or any(word in status.stdout.lower() for word in ('offline', 'disabled', 'not connected')):
        raise RuntimeError('Принтер недоступен. Проверьте USB, питание и очередь печати.')
    result = subprocess.run(['lpr', '-P', name, '-o', 'raw'], input=raw_data,
                            capture_output=True, timeout=15)
    if result.returncode:
        raise RuntimeError('Драйвер не принял чек. Проверьте очередь печати.')
    return True


def print_order_direct(order, printer_name=None):
    return send_raw_bytes_to_printer(build_escpos_bytes_for_order(order), printer_name)
