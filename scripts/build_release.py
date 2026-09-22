#!/usr/bin/env python3
"""Build the public, reproducible Simulator Manager Tool ZIP and checksum."""
from pathlib import Path
import hashlib
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
ARCHIVE = DIST / "simulator-manager-tool.zip"
EPOCH = (2026, 1, 1, 0, 0, 0)


def main() -> None:
    files = subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True).splitlines()
    files = [name for name in files if not name.startswith("dist/") and not name.startswith(".github/")]
    DIST.mkdir(exist_ok=True)
    with zipfile.ZipFile(ARCHIVE, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as output:
        for name in sorted(files):
            path = ROOT / name
            if not path.is_file():
                continue
            info = zipfile.ZipInfo(f"simulator-manager/{name}", EPOCH)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = ((0o755 if path.stat().st_mode & 0o111 else 0o644) & 0xFFFF) << 16
            output.writestr(info, path.read_bytes())
    digest = hashlib.sha256(ARCHIVE.read_bytes()).hexdigest()
    (DIST / f"{ARCHIVE.name}.sha256").write_text(f"{digest}  {ARCHIVE.name}\n")
    print(ARCHIVE)
    print(digest)


if __name__ == "__main__":
    main()
