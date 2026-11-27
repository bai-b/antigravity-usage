"""Local Windows actions requested from the conversation link menu."""

import os
from pathlib import Path, PureWindowsPath
import re
import shutil
import subprocess
from urllib.parse import quote


MAX_COPY_BYTES = 2 * 1024 * 1024
UNSAFE_OPEN = set(
    ".appref-ms .bat .cmd .com .cpl .exe .hta .ins .isp .jar .js .jse .lnk .msi .msix "
    ".msp .pif .ps1 .py .reg .scr .url .vb .vbe .vbs .ws .wsc .wsf".split()
)
GITHUB_ORIGIN = re.compile(
    r"(?:https://github\.com/|ssh://git@github\.com/|git@github\.com:)"
    r"([a-zA-Z0-9_.-]+)/([a-zA-Z0-9_.-]+?)(?:\.git)?/?$"
)
SYSTEM32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
SAVE_DIALOG = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
$file = Get-Item -LiteralPath $env:AGY_SAVE_SOURCE
$dialog = New-Object System.Windows.Forms.SaveFileDialog
try {
    $dialog.FileName = $file.Name
    $dialog.InitialDirectory = $file.DirectoryName
    $dialog.OverwritePrompt = $true
    if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
        [System.IO.File]::Copy($file.FullName, $dialog.FileName, $true)
    }
} finally {
    $dialog.Dispose()
}
"""


def local_path(value):
    # Windows drive paths only. Reject network shares, device paths and ADS.
    if not isinstance(value, str) or len(value) > 4096 or not re.fullmatch(r"[A-Za-z]:[\\/][^:\x00-\x1f]*", value):
        return None
    name = PureWindowsPath(value)
    if any(part.rstrip(" .") != part or PureWindowsPath(part).is_reserved()
           for part in name.parts[1:] if part != ".."):
        return None
    resolved = Path(name).resolve(strict=False)
    return resolved if re.fullmatch(r"[A-Za-z]:\\", resolved.anchor) else None


def system_dialog_program():
    return SYSTEM32 / "WindowsPowerShell" / "v1.0" / "powershell.exe"


def can_open_default(path):
    return path.is_dir() or path.suffix.lower() not in UNSAFE_OPEN


def github_link(path, fragment):
    git = shutil.which("git.exe")
    if not git or not path.is_file():
        return None

    def run(*args):
        return subprocess.check_output([git, "-C", str(path.parent), *args], stderr=subprocess.DEVNULL,
                                       encoding="utf-8", timeout=1, creationflags=subprocess.CREATE_NO_WINDOW).strip()

    try:
        root = Path(run("rev-parse", "--show-toplevel"))
        remote = GITHUB_ORIGIN.fullmatch(run("config", "--local", "--get", "remote.origin.url"))
        if not remote:
            return None
        relative = path.relative_to(root).as_posix()
        head = run("rev-parse", "--verify", "HEAD")
        if not re.fullmatch(r"[a-f0-9]{40,64}", head):
            return None
        run("cat-file", "-e", head + ":" + relative)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    lines = fragment if isinstance(fragment, str) and re.fullmatch(r"L\d+(?:-L?\d+)?", fragment) else ""
    return f"https://github.com/{remote[1]}/{remote[2]}/blob/{head}/{quote(relative)}" + ("#" + lines if lines else "")


def copy_contents(path):
    with path.open("rb") as stream:
        data = stream.read(MAX_COPY_BYTES + 1)
    if len(data) > MAX_COPY_BYTES:
        return {"error": "文件超过 2 MB，无法直接复制内容。"}
    try:
        text = data.decode("utf-16" if data.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig")
        if "\0" not in text:
            return {"text": text}
    except UnicodeError:
        pass
    return {"error": "此文件无法按文本复制。"}


def file_action(request):
    try:
        action = request.get("action")
        path = local_path(request.get("path"))
        if not path:
            return {"error": "无法识别本机文件路径。"}
        if action == "inspect":
            is_file = path.is_file()
            exists = is_file or path.is_dir()
            return {"exists": exists, "path": str(path), "open": exists and can_open_default(path),
                    "choose": is_file and (SYSTEM32 / "OpenWith.exe").is_file(),
                    "save": is_file and system_dialog_program().is_file(),
                    "copy": is_file and path.stat().st_size <= MAX_COPY_BYTES,
                    "github": github_link(path, request.get("fragment")) if is_file else None}
        if action == "open" and (path.is_file() or path.is_dir()) and can_open_default(path):
            os.startfile(path)
        elif action == "choose" and path.is_file():
            subprocess.Popen([str(SYSTEM32 / "OpenWith.exe"), str(path)])
        elif action == "copy" and path.is_file():
            return copy_contents(path)
        elif action == "save" and path.is_file():
            env = {**os.environ, "AGY_SAVE_SOURCE": str(path)}
            subprocess.run([str(system_dialog_program()), "-NoProfile", "-STA", "-Command", SAVE_DIALOG],
                           env=env, creationflags=subprocess.CREATE_NO_WINDOW, check=True)
        else:
            return {"error": "该操作不适用于此文件。"}
        return {}
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
        return {"error": "无法完成文件操作，请检查文件是否存在或被占用。"}
