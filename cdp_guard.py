"""Keep the Antigravity desktop inspector reachable for the composer row.

The desktop build only writes %APPDATA%\\Antigravity\\DevToolsActivePort when it is
started with --remote-debugging-port. Two things lose that flag: the updater
rewrites the shortcuts, and any launch that bypasses them. This module patches
the shortcuts on disk and, while the app is still freshly started, relaunches it
once with the flag so the usage row comes back by itself. A session that has
been running for a while is never interrupted.
"""

import base64
import ctypes
import json
import logging
import os
import subprocess
import time
from pathlib import Path

LOG = logging.getLogger("composer-status")

DESKTOP_DATA = Path(os.environ.get("APPDATA") or Path.home()) / "Antigravity"
MARKER = DESKTOP_DATA / "DevToolsActivePort"
STATE_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "AntigravityUsage"
SETTINGS = STATE_DIR / "settings.json"
STATE = STATE_DIR / "cdp-heal.json"
SWITCH = "--remote-debugging-port=0"
CHECK_INTERVAL = 30.0
PATCH_INTERVAL = 3600.0
RESTART_COOLDOWN = 1800.0
FRESH_SECONDS = 300.0
NO_WINDOW = 0x08000000

PROBE = """$ErrorActionPreference = 'SilentlyContinue'
$p = Get-CimInstance Win32_Process | Where-Object { ($_.Name -match 'ANTIGR|ntigravity') -and ($_.CommandLine -notmatch '--type=') } | Sort-Object CreationDate | Select-Object -First 1
if (-not $p) { Write-Output '{"running":false}'; exit 0 }
$file = Join-Path $env:APPDATA 'Antigravity\\DevToolsActivePort'
$port = 0
if (Test-Path $file) { $port = [int]([DateTimeOffset](Get-Item $file).LastWriteTime).ToUnixTimeSeconds() }
$started = [int]([DateTimeOffset]$p.CreationDate).ToUnixTimeSeconds()
$flag = 0
if ($p.CommandLine -match 'remote-debugging-port') { $flag = 1 }
$exe = ''
if ($p.ExecutablePath) { $exe = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($p.ExecutablePath)) }
@{ running = $true; exe64 = $exe; started = $started; port = $port; flag = $flag } | ConvertTo-Json -Compress
"""

PATCH = """$ErrorActionPreference = 'SilentlyContinue'
$sh = New-Object -ComObject WScript.Shell
$items = @(
  (Join-Path $env:USERPROFILE 'Desktop\\Antigravity.lnk'),
  (Join-Path $env:APPDATA 'Microsoft\\Windows\\Start Menu\\Programs\\Antigravity.lnk'),
  (Join-Path $env:APPDATA 'Microsoft\\Internet Explorer\\Quick Launch\\User Pinned\\TaskBar\\Antigravity.lnk')
)
foreach ($item in $items) {
  if (-not (Test-Path $item)) { continue }
  $shortcut = $sh.CreateShortcut($item)
  if ($shortcut.Arguments -notmatch 'remote-debugging-port') {
    $shortcut.Arguments = '--remote-debugging-port=0'
    $shortcut.Save()
  }
}
"""

RELAUNCH = """$ErrorActionPreference = 'SilentlyContinue'
"$(Get-Date -Format o) begin" | Out-File -Encoding ascii '{log}' -Append
Start-Sleep -Milliseconds 600
Get-Process | Where-Object {{ $_.ProcessName -match 'ANTIGR|ntigravity' }} | Stop-Process -Force
Start-Sleep -Seconds 3
Remove-Item Env:ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue
$exe = Join-Path $env:LOCALAPPDATA 'Programs/antigravity/Antigravity.exe'
if (-not (Test-Path $exe)) {{ $exe = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{exe64}')) }}
Start-Process -FilePath $exe -ArgumentList '--remote-debugging-port=0' -WorkingDirectory (Split-Path -Parent $exe)
"$(Get-Date -Format o) launched $exe" | Out-File -Encoding ascii '{log}' -Append
"""


def _load(path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return fallback


def _save(path, value):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except OSError:
        pass


def _short(path):
    """8.3 path so the generated scripts stay pure ASCII."""
    if os.name != "nt":
        return str(path)
    try:
        buffer = ctypes.create_unicode_buffer(512)
        size = ctypes.windll.kernel32.GetShortPathNameW(str(path), buffer, 512)
        return buffer.value if size else str(path)
    except (OSError, AttributeError):
        return str(path)


def _script_file(script):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    path = STATE_DIR / ("cdp-%d-%d.ps1" % (os.getpid(), int(time.time() * 1000) % 100000))
    try:
        path.write_bytes(script.encode("ascii"))
    except UnicodeEncodeError:
        path.write_bytes(b"\xef\xbb\xbf" + script.encode("utf-8"))
    return path


def _powershell(script, timeout=25):
    """Run an ASCII-only PowerShell snippet in a temp script file."""
    if os.name != "nt":
        return None
    try:
        path = _script_file(script)
    except OSError:
        return None
    command = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
               "-WindowStyle", "Hidden", "-File", str(path)]
    env = dict(os.environ)
    env.pop("ELECTRON_RUN_AS_NODE", None)
    try:
        done = subprocess.run(command, capture_output=True, timeout=timeout,
                              creationflags=NO_WINDOW, env=env)
        return (done.stdout or b"").decode("utf-8", "replace").strip()
    except (OSError, subprocess.SubprocessError):
        return None
    finally:
        try:
            path.unlink()
        except OSError:
            pass


def _spawn(script):
    """Start a detached script through WMI so it survives this process."""
    if os.name != "nt":
        return False
    try:
        path = _script_file(script)
    except OSError:
        return False
    target = 'powershell -NoProfile -ExecutionPolicy Bypass -File "%s"' % _short(path)
    call = ("Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments "
            "@{CommandLine='%s'} | Out-Null" % target.replace("'", "''"))
    try:
        output = _powershell(call, timeout=40)
    finally:
        pass
    if output is None:
        try:
            path.unlink()
        except OSError:
            pass
        return False
    return True


def probe():
    raw = _powershell(PROBE)
    if not raw:
        return None
    try:
        value = json.loads(raw.splitlines()[-1])
    except (ValueError, IndexError):
        return None
    if not isinstance(value, dict):
        return None
    encoded = value.pop("exe64", "")
    try:
        value["exe"] = base64.b64decode(encoded).decode("utf-8") if encoded else ""
    except (ValueError, UnicodeDecodeError):
        value["exe"] = ""
    return value


def patch_shortcuts():
    return _powershell(PATCH, timeout=40) is not None


def _auto_restart():
    settings = _load(SETTINGS, {})
    return not (isinstance(settings, dict) and settings.get("auto_restart") is False)


def _fresh_marker():
    try:
        return time.time() - MARKER.stat().st_mtime < 3600
    except OSError:
        return False


def heal():
    """Restore the inspector when the desktop lost the launch flag."""
    if os.name != "nt":
        return
    now = time.time()
    state = _load(STATE, {})
    if not isinstance(state, dict):
        state = {}
    try:
        checked = float(state.get("checked") or 0)
    except (TypeError, ValueError):
        checked = 0.0
    if now - checked < CHECK_INTERVAL:
        return
    state["checked"] = now
    _save(STATE, state)
    if _fresh_marker():
        return
    info = probe()
    if not info or not info.get("running"):
        return
    try:
        patched = float(state.get("patched") or 0)
    except (TypeError, ValueError):
        patched = 0.0
    if now - patched > PATCH_INTERVAL:
        state["patched"] = now
        _save(STATE, state)
        patch_shortcuts()
    if info.get("flag"):
        return
    if not _auto_restart():
        LOG.info("desktop has no inspector and auto restart is off")
        return
    try:
        started = float(info.get("started") or 0)
        port = float(info.get("port") or 0)
        restarted = float(state.get("restarted") or 0)
    except (TypeError, ValueError):
        return
    if not started or now - started > FRESH_SECONDS:
        LOG.info("desktop is running without the inspector; keeping the session")
        return
    if port and port >= started:
        return
    if now - restarted < RESTART_COOLDOWN:
        return
    exe = info.get("exe") or ""
    if not exe or not Path(exe).exists():
        return
    state["restarted"] = now
    _save(STATE, state)
    LOG.info("relaunching the desktop with the inspector enabled")
    script = RELAUNCH.format(log=_short(STATE_DIR / "relaunch.log"),
                             exe64=base64.b64encode(exe.encode("utf-8")).decode("ascii"))
    _spawn(script)
