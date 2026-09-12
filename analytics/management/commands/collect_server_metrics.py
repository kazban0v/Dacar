from django.core.management.base import BaseCommand
from config.monitoring import get_metrics


class Command(BaseCommand):
    help = 'Collect one host sample into private 24-hour monitoring history (no business writes).'

    def handle(self, *args, **options):
        sample = get_metrics()['sample']
        self.stdout.write(f"Metrics collected at {sample['timestamp']:.0f}")
