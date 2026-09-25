#!/bin/zsh
set -euo pipefail

destination='/Users/beibitkazbanov/Library/Application Support/DACAR Backup/archive'
key='/Users/beibitkazbanov/.ssh/dacar_backup_ed25519'
staging="$(mktemp -d '/tmp/dacar-backup-sync.XXXXXX')"
archive="${staging}/backups.tar.gz"
extracted="${staging}/extracted"
trap 'rm -rf -- "$staging"' EXIT

mkdir -p -- "$destination" "$extracted"
/usr/bin/ssh -T -i "$key" -o BatchMode=yes -o StrictHostKeyChecking=yes root@dacar-market.kz > "$archive"
/usr/bin/tar -xzf "$archive" -C "$extracted"

/usr/bin/python3 - "$extracted" <<'PY'
import hashlib, json, sqlite3, sys
from pathlib import Path
root = Path(sys.argv[1]).resolve(strict=True)
copies = list(root.glob('*/*.sqlite3'))
if not copies:
    raise SystemExit('No database snapshots received')
for path in copies:
    manifest_path = path.with_suffix('.json')
    manifest = json.loads(manifest_path.read_text())
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != manifest.get('sha256'):
        raise SystemExit(f'Checksum mismatch: {path.name}')
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as db:
        if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise SystemExit(f'Integrity check failed: {path.name}')
PY

# Copies only verified files. Existing off-server copies are never deleted automatically.
/usr/bin/rsync -r "$extracted/" "$destination/"
/usr/bin/touch '/Users/beibitkazbanov/Library/Application Support/DACAR Backup/last-success.txt'
