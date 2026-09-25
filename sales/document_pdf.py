"""Portable PDFs with embedded Cyrillic fonts; no system font dependencies."""
from decimal import Decimal, ROUND_HALF_UP
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak

FONT_DIR = Path(__file__).resolve().parent / 'fonts'
pdfmetrics.registerFont(TTFont('Dacar', str(FONT_DIR / 'DejaVuSans.ttf')))
pdfmetrics.registerFont(TTFont('DacarBold', str(FONT_DIR / 'DejaVuSans-Bold.ttf')))
pdfmetrics.registerFontFamily('Dacar', normal='Dacar', bold='DacarBold')
BODY = ParagraphStyle('body', fontName='Dacar', fontSize=9, leading=13, spaceAfter=6)
TITLE = ParagraphStyle('title', parent=BODY, fontName='DacarBold', fontSize=17, leading=22, spaceAfter=14)
SMALL = ParagraphStyle('small', parent=BODY, fontSize=8, leading=11)
RIGHT = ParagraphStyle('right', parent=BODY, alignment=TA_RIGHT)


def text(value, style=BODY):
    return Paragraph(escape(str(value)).replace('\n', '<br/>'), style)


def amount(value):
    return f'{Decimal(value):,.2f}'.replace(',', ' ').replace('.', ',')


def _plural(value, forms):
    value %= 100
    if 11 <= value <= 14:
        return forms[2]
    value %= 10
    if value == 1:
        return forms[0]
    if 2 <= value <= 4:
        return forms[1]
    return forms[2]


def _triplet_words(value, feminine=False):
    hundreds = ['', 'сто', 'двести', 'триста', 'четыреста', 'пятьсот',
                'шестьсот', 'семьсот', 'восемьсот', 'девятьсот']
    tens = ['', '', 'двадцать', 'тридцать', 'сорок', 'пятьдесят',
            'шестьдесят', 'семьдесят', 'восемьдесят', 'девяносто']
    teens = ['десять', 'одиннадцать', 'двенадцать', 'тринадцать', 'четырнадцать',
             'пятнадцать', 'шестнадцать', 'семнадцать', 'восемнадцать', 'девятнадцать']
    units = ['', 'одна' if feminine else 'один', 'две' if feminine else 'два',
             'три', 'четыре', 'пять', 'шесть', 'семь', 'восемь', 'девять']
    words = []
    if value // 100:
        words.append(hundreds[value // 100])
    remainder = value % 100
    if 10 <= remainder <= 19:
        words.append(teens[remainder - 10])
    else:
        if remainder // 10:
            words.append(tens[remainder // 10])
        if remainder % 10:
            words.append(units[remainder % 10])
    return words


def amount_in_words_ru(value):
    """Return a non-negative monetary amount in Russian words."""
    value = Decimal(value).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
    if value < 0:
        raise ValueError('Сумма не может быть отрицательной.')
    tenge = int(value)
    tiyn = int((value - tenge) * 100)
    words = []
    groups = (
        (1_000_000_000, ('миллиард', 'миллиарда', 'миллиардов'), False),
        (1_000_000, ('миллион', 'миллиона', 'миллионов'), False),
        (1_000, ('тысяча', 'тысячи', 'тысяч'), True),
    )
    remainder = tenge
    for divider, forms, feminine in groups:
        group, remainder = divmod(remainder, divider)
        if group:
            words.extend(_triplet_words(group, feminine))
            words.append(_plural(group, forms))
    if remainder:
        words.extend(_triplet_words(remainder))
    if not words:
        words.append('ноль')
    return f"{' '.join(words).capitalize()} тенге {tiyn:02d} тиын"


def payment_total_text(value):
    value = Decimal(value).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
    tenge = int(value)
    numeric = f'{tenge:,}'.replace(',', ' ')
    words = amount_in_words_ru(value)
    tenge_words, tiyn = words.rsplit(' тенге ', 1)
    return f'Всего к оплате: {numeric} ({tenge_words}) тенге {tiyn}.'


def qty(value):
    return format(Decimal(value).normalize(), 'f').replace('.', ',')


def table(rows, widths, header=True):
    content = [[text(cell, SMALL) for cell in row] for row in rows]
    result = Table(content, colWidths=[width * mm for width in widths], repeatRows=1 if header else 0)
    rules = [('VALIGN', (0, 0), (-1, -1), 'TOP'),
             ('GRID', (0, 0), (-1, -1), .4, colors.HexColor('#cbd5e1')),
             ('LEFTPADDING', (0, 0), (-1, -1), 6), ('RIGHTPADDING', (0, 0), (-1, -1), 6),
             ('TOPPADDING', (0, 0), (-1, -1), 7), ('BOTTOMPADDING', (0, 0), (-1, -1), 7)]
    if header:
        rules.append(('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#eaf0f6')))
    result.setStyle(TableStyle(rules))
    return result


def build(story, title, wide=False):
    output = BytesIO()
    size = landscape(A4) if wide else A4
    doc = SimpleDocTemplate(output, pagesize=size, leftMargin=15*mm, rightMargin=15*mm,
                           topMargin=16*mm, bottomMargin=18*mm, title=title, author='DACAR')

    def footer(canvas, document):
        canvas.saveState()
        canvas.setFont('Dacar', 8)
        canvas.setFillColor(colors.HexColor('#64748b'))
        canvas.drawString(15*mm, 9*mm, 'DACAR MARKET')
        canvas.drawRightString(size[0]-15*mm, 9*mm, f'Страница {document.page}')
        canvas.restoreState()
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return output.getvalue()


def invoice_pdf(invoice):
    seller = invoice.seller
    date = timezone.localtime(invoice.created_at).strftime('%d.%m.%Y')
    story = [text('Реквизиты для оплаты', TITLE), table([
        [f"Получатель: {seller['name']}\nБИН / ИИН: {seller['bin']}", f"ИИК: {seller['account']}\nКБе: {seller['kbe']}"],
        [f"Банк: {seller['bank']}", f"БИК: {seller['bik']}"],
    ], [98, 82], header=False), Spacer(1, 9*mm),
        text(f'Счёт на оплату № {invoice.number} от {date}', TITLE),
        text(f"Поставщик: {seller['name']}, БИН / ИИН {seller['bin']}. {seller['address']}"),
        text(f'Покупатель: {invoice.buyer_name}, БИН / ИИН {invoice.buyer_bin}. {invoice.buyer_address}'),
        Spacer(1, 4*mm)]
    rows = [['№', 'Товар / код', 'Кол-во / ед.', 'Цена, ₸', 'Скидка, ₸', 'Сумма, ₸']]
    for n, line in enumerate(invoice.lines, 1):
        gross = (Decimal(line['price']) * Decimal(line['quantity'])).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
        rows.append([n, f"{line['name']}\n{line['sku']}", f"{qty(line['quantity'])} {line['unit']}",
                     amount(line['price']), amount(gross - Decimal(line['amount'])), amount(line['amount'])])
    story += [table(rows, [9, 64, 24, 27, 27, 29]), Spacer(1, 5*mm),
              text(f'Итого к оплате: {amount(invoice.total_amount)} ₸', RIGHT), text(seller['tax'], RIGHT),
              text(payment_total_text(invoice.total_amount)),
              Spacer(1, 6*mm), text('Счёт на оплату не подтверждает факт оплаты и не является кассовым чеком.', SMALL),
              Spacer(1, 8*mm), text('Исполнитель: _______________________    Подпись: _______________________')]
    return build(story, f'Счёт {invoice.number}')


def shine_pdf(report):
    period = f"{report['start']:%d.%m.%Y} - {report['end']:%d.%m.%Y}"
    story = [text('Отчёт для Маршала · Shine Systems', TITLE),
             text(f'Период: {period}. Конечный понедельник не включён. Часовой пояс: {timezone.get_current_timezone_name()}.'),
             text('Продажи и возвраты учтены по дате операции. Суммы - цены продажи с учётом всех скидок. Возврат прошлой продажи уменьшает текущую неделю.', SMALL)]
    rows = [['Товар / артикул', 'Ед.', 'Продано', 'Возврат', 'Итого кол-во', 'Продажи, ₸', 'Возвраты, ₸', 'Итого, ₸']]
    for row in report['rows']:
        rows.append([f"{row['name']}\n{row['sku']}", row['unit'], qty(row['sold_qty']), qty(row['returned_qty']),
                     qty(row['net_qty']), amount(row['sold_amount']), amount(row['returned_amount']), amount(row['net_amount'])])
    if report['rows']:
        story.append(table(rows, [85, 14, 23, 23, 26, 32, 32, 32]))
    else:
        story.append(text('За выбранную неделю продаж и возвратов Shine Systems не найдено.'))
    story += [Spacer(1, 5*mm), text(f"Продажи: {amount(report['sold_total'])} ₸ · Возвраты: {amount(report['returned_total'])} ₸", RIGHT),
              text(f"Итого после возвратов: {amount(report['net_total'])} ₸", RIGHT),
              text('Это отчёт о розничной реализации. Сумма к перечислению поставщику по закупочным ценам или комиссии здесь не рассчитывается.', SMALL)]
    for warning in report['warnings']:
        story.append(text('Требует проверки: ' + warning, SMALL))
    if report['events']:
        story += [PageBreak(), text('Расшифровка операций', TITLE), text(period)]
        events = [['Дата', 'Чек', 'Операция', 'Товар', 'Кол-во', 'Сумма, ₸']]
        for event in report['events']:
            events.append([timezone.localtime(event['date']).strftime('%d.%m.%Y %H:%M'), event['order'], event['kind'],
                           event['name'], qty(event['quantity']), amount(event['amount'])])
        story.append(table(events, [32, 53, 26, 103, 20, 33]))
    return build(story, 'Shine Systems - недельная реализация', wide=True)
