"""Upload coordinated backups with the VM identity and no stored cloud keys."""
from __future__ import annotations

import http.client
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen


class UploadRejected(RuntimeError):
    pass


def upload(account: str, directory: Path) -> None:
    if not re.fullmatch(r"[a-z0-9]{3,24}", account) or not directory.is_dir():
        raise ValueError("Invalid backup destination or directory")
    request = Request(
        "http://169.254.169.254/metadata/identity/oauth2/token?"
        "api-version=2018-02-01&resource=https%3A%2F%2Fstorage.azure.com%2F",
        headers={"Metadata": "true"},
    )
    with urlopen(request, timeout=15) as response:
        token = json.load(response)["access_token"]
    for path in sorted(directory.iterdir()):
        if not path.is_file() or path.is_symlink():
            raise ValueError("Backup directory must contain regular files")
        connection = http.client.HTTPSConnection(f"{account}.blob.core.windows.net", timeout=120)
        try:
            with path.open("rb") as body:
                connection.request(
                    "PUT", f"/backups/{quote(directory.name)}/{quote(path.name)}", body=body,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "x-ms-version": "2023-11-03",
                        "x-ms-date": datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT"),
                        "x-ms-blob-type": "BlockBlob",
                        "Content-Length": str(path.stat().st_size),
                        "Content-Type": "application/octet-stream",
                    },
                )
                response = connection.getresponse()
                response.read()
                if response.status != 201:
                    raise UploadRejected(f"HTTP {response.status}")
        finally:
            connection.close()


if __name__ == "__main__":
    try:
        upload(sys.argv[1], Path(sys.argv[2]))
    except Exception as exc:
        detail = str(exc) if isinstance(exc, UploadRejected) else type(exc).__name__
        print(f"Backup upload failed: {detail}", file=sys.stderr)
        raise SystemExit(1) from None
