"""Generate clearly labelled sample PDFs, without touching the database.

Run with: .venv/bin/python3.11 scripts/qa_documents.py
"""
import os
import sys
from pathlib import Path
from datetime import date
from decimal import Decimal

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()
from django.utils import timezone
from sales.models import CompanyInvoice
from sales.documents import SELLER
from sales.document_pdf import invoice_pdf, shine_pdf

folder = ROOT / 'output' / 'pdf'
folder.mkdir(parents=True, exist_ok=True)
invoice = CompanyInvoice(pk=0, buyer_name='ОБРАЗЕЦ — ТОО «Тестовая компания»',
    buyer_bin='000000000000', buyer_address='Тестовый адрес, г. Актобе', seller=SELLER,
    created_at=timezone.now(), total_amount=Decimal('12200'), lines=[
        dict(name='ОБРАЗЕЦ: Shine Systems InteriorCleaner — очиститель интерьера, 1 л', sku='DEMO-01',
             quantity='2', unit='шт', price='3500', amount='7000'),
        dict(name='ОБРАЗЕЦ: Shine Systems GlassCleaner — очиститель стекол, 750 мл', sku='DEMO-02',
             quantity='2', unit='шт', price='3000', amount='5200'),
    ])
(folder / 'invoice-sample.pdf').write_bytes(invoice_pdf(invoice))
row = dict(name='ОБРАЗЕЦ: Shine Systems InteriorCleaner — очиститель интерьера, 1 л', sku='DEMO-01',
           unit='шт', sold_qty=Decimal('5'), returned_qty=Decimal('1'), net_qty=Decimal('4'),
           sold_amount=Decimal('17500'), returned_amount=Decimal('3500'), net_amount=Decimal('14000'))
report = dict(start=date(2026,9,14), end=date(2026,9,21), rows=[row], sold_total=17500,
              returned_total=3500, net_total=14000, warnings=['ОБРАЗЕЦ. Вымышленные данные, не для расчётов.'], events=[
                  dict(date=timezone.now(), order='DEMO-0001', kind='Продажа', name=row['name'], quantity=5, amount=17500),
                  dict(date=timezone.now(), order='DEMO-0002', kind='Возврат', name=row['name'], quantity=1, amount=-3500),
              ])
(folder / 'shine-sample.pdf').write_bytes(shine_pdf(report))
print('Created two sample PDFs; database unchanged.')
