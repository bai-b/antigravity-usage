"""Install or remove the per-user Antigravity plugin from the Windows release ZIP."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
from tempfile import NamedTemporaryFile, TemporaryDirectory
import time

from usage import APP_DATA, desktop_read, desktop_request, desktop_services


SOURCE = Path(__file__).resolve().parent
CONFIG = (Path.home() / ".gemini" / "config").resolve()
SETTINGS = CONFIG / "config.json"
PLUGINS = CONFIG / "plugins"
DESTINATION = PLUGINS / "agy-usage"
SIDECAR_ID = "agy-usage/composer"
MARKER = ".agy-usage-release"
PORT = 43827
PAYLOAD = ("plugin.json", "composer_status.py", "usage.py", "dashboard.py", "prices.json",
           "mcp_config.json", "web", "skills", "runtime")


def save_settings(enabled):
    CONFIG.mkdir(parents=True, exist_ok=True)
    for _ in range(3):
        original = SETTINGS.read_bytes() if SETTINGS.exists() else None
        config = json.loads(original.decode("utf-8-sig")) if original is not None else {}
        sidecars = config.setdefault("sidecars", {})
        if not isinstance(sidecars, dict):
            raise ValueError("Antigravity 的 sidecars 配置不是对象，请检查 config.json")
        if enabled:
            settings = sidecars.setdefault(SIDECAR_ID, {})
            if settings.get("enabled") is True:
                return
            settings["enabled"] = True
        elif SIDECAR_ID in sidecars:
            del sidecars[SIDECAR_ID]
        else:
            return
        with NamedTemporaryFile(dir=CONFIG, prefix=".agy-usage-config-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write((json.dumps(config, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
        try:
            current = SETTINGS.read_bytes() if SETTINGS.exists() else None
            if current != original:
                continue
            temporary.replace(SETTINGS)
            return
        finally:
            temporary.unlink(missing_ok=True)
    raise OSError("Antigravity 正在修改配置。请退出 Antigravity 后重新运行安装程序。")


def manage_sidecar(action):
    for info in desktop_services(APP_DATA):
        try:
            desktop_read(info, "GetSidecars")
        except (OSError, ValueError):
            continue
        try:
            desktop_request(info, "ManageSidecar", {"sidecarId": SIDECAR_ID, "action": "SIDECAR_ACTION_" + action})
        except (OSError, ValueError):
            return False
        return True
    return False


def bridge_command(message, timeout=0.6):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as channel:
        channel.settimeout(timeout)
        channel.connect(("127.0.0.1", PORT))
        try:
            channel.send(message)
            return channel.recv(64)
        except OSError:
            return None


def stop_bridge():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as channel:
        channel.sendto(b"stop", ("127.0.0.1", PORT))
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as channel:
            channel.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            try:
                channel.bind(("127.0.0.1", PORT))
            except OSError:
                pass
            else:
                return
        time.sleep(0.2)
    raise OSError("用量插件仍在运行。请完全退出 Antigravity，等它关闭后重试。")


def check_destination():
    if PLUGINS.is_symlink() or PLUGINS.is_junction():
        raise OSError(f"插件目录是链接，安装程序无法写入 {PLUGINS}")
    if DESTINATION.is_symlink() or DESTINATION.is_junction():
        raise OSError(f"插件位置是链接，安装程序无法写入 {DESTINATION}")
    if DESTINATION.exists() and (not DESTINATION.is_dir() or not (DESTINATION / MARKER).is_file()):
        raise OSError(f"发现未由发布包安装的同名插件，请先移走 {DESTINATION}")


def replace_plugin_directory(source, destination):
    deadline = time.monotonic() + 12
    while True:
        try:
            source.replace(destination)
            return
        except OSError as error:
            # Windows can hold the old Python runtime open briefly after its socket is closed.
            if getattr(error, "winerror", None) not in {5, 32, 33}:
                raise
            if time.monotonic() >= deadline:
                raise OSError("插件文件还在使用。请退出 Antigravity 后重新运行 Install.cmd。") from error
            time.sleep(0.25)


def same_runtime(installed, incoming):
    def fingerprints(folder):
        entries = {}
        if not folder.is_dir():
            return entries
        for file in folder.rglob("*"):
            if file.is_file():
                with file.open("rb") as stream:
                    entries[file.relative_to(folder)] = hashlib.file_digest(stream, "sha256").digest()
        return entries

    return fingerprints(installed) == fingerprints(incoming)


def update_plugin_files(incoming, backup, updated):
    files = sorted(file for file in incoming.rglob("*") if file.is_file()
                   and file.relative_to(incoming).parts[0] not in {"runtime", MARKER})
    for source in [*files, incoming / MARKER]:
        relative = source.relative_to(incoming)
        target = DESTINATION / relative
        if target.is_symlink() or not target.resolve().is_relative_to(DESTINATION.resolve()):
            raise OSError(f"插件文件位置与预期不符：{relative}")
        previous = backup / relative if target.exists() else None
        if previous is not None:
            if source.read_bytes() == target.read_bytes():
                continue
            previous.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(target, previous)
        target.parent.mkdir(parents=True, exist_ok=True)
        replace_plugin_directory(source, target)
        updated.append((target, previous))


def stage_plugin(stage, version):
    for name in PAYLOAD:
        origin = SOURCE / name
        if origin.is_dir():
            shutil.copytree(origin, stage / name)
        else:
            shutil.copyfile(origin, stage / name)
    (stage / MARKER).write_text(version, encoding="ascii")
    interpreter = str(DESTINATION / "runtime" / "pythonw.exe")
    sidecar = stage / "sidecars" / "composer" / "sidecar.json"
    sidecar.parent.mkdir(parents=True)
    sidecar.write_text(json.dumps({
        "display_name": "Antigravity 用量看板",
        "description": "将本机用量与额度显示在桌面会话中",
        "command": interpreter,
        "args": ["-B", str(DESTINATION / "composer_status.py")],
        "restart_policy": "always",
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    settings = stage / "mcp_config.json"
    mcp = json.loads(settings.read_text(encoding="utf-8"))
    mcp["mcpServers"]["usage"]["command"] = str(DESTINATION / "runtime" / "python.exe")
    settings.write_text(json.dumps(mcp, indent=2) + "\n", encoding="utf-8")


def install():
    version = json.loads((SOURCE / "plugin.json").read_text(encoding="utf-8"))["version"]
    check_destination()
    if (DESTINATION / MARKER).exists() and (DESTINATION / MARKER).read_text(encoding="ascii") == version:
        save_settings(True)
        manage_sidecar("ENABLE")
        return
    PLUGINS.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".agy-usage-setup-", dir=CONFIG) as directory:
        scratch = Path(directory).resolve()
        incoming = scratch / "new"
        previous = scratch / "previous"
        incoming.mkdir()
        stage_plugin(incoming, version)
        had_existing = DESTINATION.exists()
        reuse_runtime = had_existing and same_runtime(DESTINATION / "runtime", incoming / "runtime")
        updated = []
        moved_incoming = False
        try:
            if had_existing:
                bridge_command(b"agy-usage:detach", timeout=2)
                manage_sidecar("DISABLE")
            stop_bridge()
            if reuse_runtime:
                update_plugin_files(incoming, previous, updated)
            else:
                if had_existing:
                    replace_plugin_directory(DESTINATION, previous)
                replace_plugin_directory(incoming, DESTINATION)
                moved_incoming = True
            save_settings(True)
        except (OSError, ValueError, TypeError):
            if reuse_runtime:
                for target, backup in reversed(updated):
                    if backup is None:
                        target.unlink(missing_ok=True)
                    else:
                        replace_plugin_directory(backup, target)
            else:
                if moved_incoming:
                    replace_plugin_directory(DESTINATION, incoming)
                if previous.exists():
                    replace_plugin_directory(previous, DESTINATION)
            if had_existing:
                save_settings(True)
                manage_sidecar("ENABLE")
            raise
    manage_sidecar("ENABLE")


def uninstall():
    check_destination()
    if not DESTINATION.exists():
        print("当前用户没有通过发布包安装用量看板。")
        return
    bridge_command(b"agy-usage:detach", timeout=2)
    manage_sidecar("DISABLE")
    save_settings(False)
    stop_bridge()
    if DESTINATION.resolve().parent != PLUGINS.resolve():
        raise OSError("插件移除路径与预期不符")
    shutil.rmtree(DESTINATION)
    print("已从 Antigravity 移除用量看板。")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("install", "uninstall"))
    args = parser.parse_args()
    if os.name != "nt" or not (SOURCE / "runtime" / "pythonw.exe").is_file():
        parser.exit(1, "请在 Windows 上从解压后的发布包运行。\n")
    try:
        if args.action == "uninstall":
            uninstall()
            return
        install()
        for _ in range(8):
            if bridge_command(b"agy-usage:ping") == b"agy-usage:ready":
                print("安装完成。当前 Antigravity 窗口已连接，以后打开 Antigravity 会自动加载。")
                break
            time.sleep(0.25)
        else:
            print("安装完成。打开 Antigravity 后会自动加载；如果已打开，请退出后重开一次。")
    except (OSError, ValueError, TypeError) as error:
        parser.exit(1, f"操作失败：{error}\n")


if __name__ == "__main__":
    main()
