"""
Management command: import_product_images

Массовая привязка фотографий к товарам по SKU или штрихкоду.

Использование:
    python manage.py import_product_images --folder /path/to/Image_Tovar_webp
    python manage.py import_product_images --folder /path  --dry-run
    python manage.py import_product_images --folder /path  --overwrite

Правила именования файлов (приоритет по убыванию):
    1. <SKU>.webp              — точный артикул, например DAC-123456.webp
    2. <BARCODE>.webp          — штрихкод, например 2009876543.webp
    3. IMG_XXXX_NN_<name>.webp — пробный формат с вашего iPhone (IMG_9814.webp и т.д.)
       Эти файлы не привяжутся автоматически — будут выведены как "не найдено".

Что команда НЕ делает:
    - Не меняет остатки, цены, чеки, продажи
    - Не удаляет существующие фотографии (если не передан --overwrite)
    - Не трогает никакие бизнес-данные
"""
import zipfile
import tempfile
import shutil
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.core.files import File
from catalog.models import Product


SUPPORTED_EXTS = {'.webp', '.jpg', '.jpeg', '.png'}


def _find_product(stem: str) -> Product | None:
    """
    Пытается найти товар по имени файла (без расширения).
    Порядок:
      1. Точный SKU (DAC-XXXXXX)
      2. Точный штрихкод (цифры)
      3. Регистронезависимый SKU
      4. Поиск по части имени из filename (IMG_XXXX_NN_<name> → <name>)
    """
    qs = Product.objects.filter(is_active=True)

    # 1. Точный SKU
    p = qs.filter(sku__iexact=stem).first()
    if p:
        return p

    # 2. Точный штрихкод
    p = qs.filter(barcode=stem).first()
    if p:
        return p

    # 3. Имя содержит подстроку после последнего _ (для IMG_9819_01_ProtectorWax)
    # Ищем последний сегмент после _ если он не число
    parts = stem.split('_')
    name_parts = [p for p in parts if not p.isdigit() and not p.upper().startswith('IMG')]
    if name_parts:
        # Берём последние значимые слова и ищем в названии товара
        search_term = ' '.join(name_parts).replace('_', ' ')
        p = qs.filter(name__icontains=search_term).first()
        if p:
            return p
        # Пробуем каждое слово по отдельности
        for word in name_parts:
            if len(word) > 3:  # игнорируем короткие слова
                p = qs.filter(name__icontains=word).first()
                if p:
                    return p

    return None


class Command(BaseCommand):
    help = "Массовая загрузка фотографий товаров из папки или ZIP-архива"

    def add_arguments(self, parser):
        parser.add_argument(
            '--folder', '-f',
            type=str,
            required=False,
            help="Путь к папке с изображениями",
        )
        parser.add_argument(
            '--zip',
            type=str,
            required=False,
            help="Путь к ZIP-архиву с изображениями",
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            default=False,
            help="Только показать что будет сделано, ничего не менять",
        )
        parser.add_argument(
            '--overwrite',
            action='store_true',
            default=False,
            help="Перезаписать существующие фото (по умолчанию пропускает товары с фото)",
        )

    def handle(self, *args, **options):
        folder  = options.get('folder')
        zip_path = options.get('zip')
        dry_run  = options['dry_run']
        overwrite = options['overwrite']

        if not folder and not zip_path:
            raise CommandError("Укажите --folder или --zip")

        tmp_dir = None
        try:
            if zip_path:
                tmp_dir = tempfile.mkdtemp(prefix='dacar_img_')
                self.stdout.write(f"Распаковываю ZIP в {tmp_dir}...")
                with zipfile.ZipFile(zip_path, 'r') as z:
                    z.extractall(tmp_dir)
                image_dir = Path(tmp_dir)
            else:
                image_dir = Path(folder)

            if not image_dir.exists():
                raise CommandError(f"Папка не найдена: {image_dir}")

            files = [
                f for f in image_dir.iterdir()
                if f.is_file() and f.suffix.lower() in SUPPORTED_EXTS
            ]

            if not files:
                self.stdout.write(self.style.WARNING(
                    f"В {image_dir} нет поддерживаемых изображений {SUPPORTED_EXTS}"
                ))
                return

            self.stdout.write(f"\nНайдено файлов: {len(files)}")
            self.stdout.write(f"Режим: {'DRY-RUN (ничего не меняем)' if dry_run else 'РЕАЛЬНАЯ ЗАГРУЗКА'}")
            self.stdout.write(f"Перезапись: {'да' if overwrite else 'нет (пропускаем товары с фото)'}")
            self.stdout.write("-" * 70)

            ok = skipped = not_found = errors = 0
            not_found_files = []
            error_files = []

            for img_path in sorted(files):
                stem = img_path.stem
                product = _find_product(stem)

                if product is None:
                    not_found += 1
                    not_found_files.append(img_path.name)
                    self.stdout.write(
                        self.style.WARNING(f"  [НЕ НАЙДЕН]  {img_path.name}")
                    )
                    continue

                if product.image and not overwrite:
                    skipped += 1
                    self.stdout.write(
                        f"  [ПРОПУЩЕН]   {img_path.name}  →  {product.sku} (фото уже есть)"
                    )
                    continue

                if dry_run:
                    ok += 1
                    action = "заменит" if product.image else "добавит"
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"  [DRY-RUN OK] {img_path.name}  →  [{product.sku}] {product.name}"
                        )
                    )
                    continue

                try:
                    with img_path.open('rb') as f:
                        if product.image and overwrite:
                            product.image.delete(save=False)
                        product.image.save(img_path.name, File(f), save=True)
                    ok += 1
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"  [OK]         {img_path.name}  →  [{product.sku}] {product.name}"
                        )
                    )
                except Exception as exc:
                    errors += 1
                    error_files.append(f"{img_path.name}: {exc}")
                    self.stdout.write(
                        self.style.ERROR(f"  [ОШИБКА]     {img_path.name}: {exc}")
                    )

            # Итог
            self.stdout.write("\n" + "=" * 70)
            self.stdout.write(f"Загружено:       {ok}")
            self.stdout.write(f"Пропущено:       {skipped}")
            self.stdout.write(f"Товар не найден: {not_found}")
            self.stdout.write(f"Ошибки:          {errors}")

            if not_found_files:
                self.stdout.write(self.style.WARNING(
                    "\nФайлы, для которых не найден товар (нужно переименовать в SKU.webp или BARCODE.webp):"
                ))
                for name in not_found_files:
                    self.stdout.write(f"  - {name}")

            if error_files:
                self.stdout.write(self.style.ERROR("\nОшибки при загрузке:"))
                for err in error_files:
                    self.stdout.write(f"  - {err}")

        finally:
            if tmp_dir:
                shutil.rmtree(tmp_dir, ignore_errors=True)
