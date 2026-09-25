#!/usr/bin/env python3
"""Stream only verified DACAR backup files to a restricted SSH key."""
from pathlib import Path
import re
import sys
import tarfile

ROOT = Path('/var/backups/dacar').resolve(strict=True)
KINDS = {'hourly', 'daily', 'pre-update', 'manual'}
NAME = re.compile(r'^dacar-(hourly|daily|pre-update|manual)-\d{8}T\d{12}Z-[0-9a-f]{8}\.(sqlite3|json)$')


def main():
    with tarfile.open(fileobj=sys.stdout.buffer, mode='w|gz', format=tarfile.PAX_FORMAT) as archive:
        for kind in sorted(KINDS):
            directory = ROOT / kind
            if not directory.is_dir() or directory.is_symlink():
                continue
            for path in sorted(directory.iterdir()):
                if path.is_file() and not path.is_symlink() and NAME.fullmatch(path.name):
                    archive.add(path, arcname=f'{kind}/{path.name}', recursive=False)


if __name__ == '__main__':
    main()
