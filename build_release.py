"""Build the portable Windows installation ZIP with verified runtime dependencies."""

import hashlib
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
from urllib.request import ProxyHandler, build_opener
from zipfile import ZipFile, ZIP_DEFLATED


ROOT = Path(__file__).resolve().parent
PYTHON_URL = "https://www.python.org/ftp/python/3.13.17/python-3.13.17-embed-amd64.zip"
PYTHON_SHA256 = "9e35319bffedab8b36ad44d8f377cb4f1733bcec257a0b6d04d94f01486ddb38"
WHEEL_URL = (
    "https://files.pythonhosted.org/packages/34/db/b10e48aa8fff7407e67470363eac595018441cf32d5e1001567a7aeba5d2/"
    "websocket_client-1.9.0-py3-none-any.whl"
)
WHEEL_SHA256 = "af248a825037ef591efbf6ed20cc5faa03d3b47b9e5a2230a529eeee1c1fc3ef"
FILES = ("README.md", "LICENSE", "THIRD_PARTY.md", "Install.cmd", "Uninstall.cmd", "install.py",
         "composer_status.py", "cdp_guard.py", "usage.py", "dashboard.py", "plugin.json", "prices.json", "mcp_config.json")


def fetch(url, expected, target):
    opener = build_opener(ProxyHandler({}))
    with opener.open(url, timeout=45) as response, target.open("wb") as output:
        shutil.copyfileobj(response, output)
    actual = hashlib.sha256(target.read_bytes()).hexdigest()
    if actual != expected:
        raise ValueError(f"SHA-256 mismatch for {url}")


def build():
    version = json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))["version"]
    name = f"antigravity-usage-{version}-win-x64"
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    output = dist / f"{name}.zip"
    temporary = output.with_suffix(".zip.tmp")
    with TemporaryDirectory(prefix="agy-usage-build-") as directory:
        stage = Path(directory)
        portable = stage / name
        runtime = portable / "runtime"
        runtime.mkdir(parents=True)
        fetch(PYTHON_URL, PYTHON_SHA256, stage / "python.zip")
        fetch(WHEEL_URL, WHEEL_SHA256, stage / "websocket.whl")
        with ZipFile(stage / "python.zip") as archive:
            archive.extractall(runtime)
        with ZipFile(stage / "websocket.whl") as archive:
            archive.extractall(runtime / "site-packages")
        # Keep the embedded interpreter isolated from any installed Python.
        (runtime / "python313._pth").write_text("python313.zip\n.\n..\nsite-packages\n", encoding="ascii")
        for file in FILES:
            shutil.copyfile(ROOT / file, portable / file)
        for folder in ("web", "skills"):
            shutil.copytree(ROOT / folder, portable / folder)
        with ZipFile(temporary, "w", ZIP_DEFLATED, compresslevel=6) as archive:
            for path in sorted(portable.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(stage))
        temporary.replace(output)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(".zip.sha256").write_text(f"{digest}  {output.name}\n", encoding="ascii")
    print(output)


if __name__ == "__main__":
    build()
