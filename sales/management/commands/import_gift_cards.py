"""Import the exact printed registry; dry run unless --apply is given."""
import csv
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from rest_framework.exceptions import ValidationError

from sales.gift_cards import code_digest, normalize_code
from sales.models import GiftCard


class Command(BaseCommand):
    help = 'Check or import printed gift-card codes. Existing cards and balances are never changed.'

    def add_arguments(self, parser):
        parser.add_argument('registry', type=Path)
        parser.add_argument('--apply', action='store_true', help='Write new cards to the database')

    def handle(self, *args, **options):
        registry = options['registry']
        if not registry.is_file():
            raise CommandError('Файл реестра не найден.')
        try:
            with registry.open(encoding='utf-8-sig', newline='') as source:
                rows = list(csv.DictReader(source))
        except (OSError, UnicodeError, csv.Error) as exc:
            raise CommandError('Реестр не читается.') from exc
        if not rows:
            raise CommandError('Реестр пуст.')

        parsed = []
        seen = set()
        for number, row in enumerate(rows, start=2):
            try:
                code = normalize_code(row['barcode_data'])
                nominal = Decimal(row['nominal_kzt'])
            except (KeyError, InvalidOperation, ValueError) as exc:
                raise CommandError(f'Строка {number}: неверные данные.') from exc
            except ValidationError as exc:
                raise CommandError(f'Строка {number}: неверный штрихкод.') from exc
            if nominal <= 0 or nominal != nominal.quantize(Decimal('0.01')):
                raise CommandError(f'Строка {number}: неверный номинал.')
            digest = code_digest(code)
            if digest in seen:
                raise CommandError(f'Строка {number}: дублирующийся штрихкод.')
            seen.add(digest)
            parsed.append((digest, code[-4:], nominal))

        existing = GiftCard.objects.filter(code_hash__in=seen).in_bulk(field_name='code_hash')
        for digest, _, nominal in parsed:
            if digest in existing and existing[digest].nominal != nominal:
                raise CommandError('Номинал уже зарегистрированной карты отличается от реестра.')
        new_cards = [GiftCard(code_hash=digest, code_last4=last4, nominal=nominal)
            for digest, last4, nominal in parsed if digest not in existing]
        if options['apply']:
            with transaction.atomic():
                GiftCard.objects.bulk_create(new_cards)
        self.stdout.write(f'Проверено: {len(parsed)}. Уже в базе: {len(existing)}. '
                          f'{"Добавлено" if options["apply"] else "К добавлению"}: {len(new_cards)}.')
