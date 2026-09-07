import re

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


VERSION_PATTERN = re.compile(r'^\d+\.\d+(?:\.\d+)?$')


class Command(BaseCommand):
    help = 'Повышает версию DACAR POS в едином файле VERSION.'

    def add_arguments(self, parser):
        parser.add_argument(
            'level',
            nargs='?',
            choices=('major', 'minor', 'patch'),
            help='Тип обновления: major, minor или patch.',
        )
        parser.add_argument(
            '--set',
            dest='explicit_version',
            help='Установить точную версию, например --set 1.2.0.',
        )

    def handle(self, *args, **options):
        level = options.get('level')
        explicit_version = (options.get('explicit_version') or '').strip()
        if bool(level) == bool(explicit_version):
            raise CommandError('Укажите либо уровень major/minor/patch, либо --set VERSION.')

        version_file = settings.BASE_DIR / 'VERSION'
        try:
            current_version = version_file.read_text(encoding='utf-8').strip()
        except OSError as exc:
            raise CommandError(f'Не удалось прочитать {version_file}: {exc}') from exc

        if not VERSION_PATTERN.fullmatch(current_version):
            raise CommandError(f'Некорректная текущая версия: {current_version!r}')

        if explicit_version:
            if not VERSION_PATTERN.fullmatch(explicit_version):
                raise CommandError('Версия должна иметь формат 1.2 или 1.2.3.')
            next_version = explicit_version
        else:
            parts = [int(part) for part in current_version.split('.')]
            while len(parts) < 3:
                parts.append(0)
            major, minor, patch = parts
            if level == 'major':
                next_version = f'{major + 1}.0'
            elif level == 'minor':
                next_version = f'{major}.{minor + 1}'
            else:
                next_version = f'{major}.{minor}.{patch + 1}'

        version_file.write_text(f'{next_version}\n', encoding='utf-8')
        self.stdout.write(self.style.SUCCESS(f'DACAR POS: v{current_version} → v{next_version}'))
