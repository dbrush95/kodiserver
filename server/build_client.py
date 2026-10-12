#!/usr/bin/env python3
"""Build the installable companion ZIP into a selected output directory."""
from pathlib import Path
import sys
import zipfile

source = Path(__file__).resolve().parents[1] / "client/service.kodiserver.client"
target = Path(sys.argv[1])
target.mkdir(parents=True, exist_ok=True)
archive = target / "service.kodiserver.client-0.2.0.zip"
with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as out:
    for path in sorted(source.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
            out.write(path, path.relative_to(source.parent))
print(archive)
