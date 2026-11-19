r"""One-command release for the Antigravity usage plugin.

Build artifacts -> stage them -> commit + tag -> push -> create GitHub Release -> upload assets.

    python release.py                # full flow
    python release.py --no-build     # reuse existing dist/*.zip
    python release.py --dry-run      # print the plan, change nothing
    python release.py --force-upload # re-upload assets even when name+size match
    python release.py --notes FILE   # custom release body (default: CHANGELOG section or template)

Auth: GITHUB_TOKEN / GH_TOKEN, else a cached token in
%LOCALAPPDATA%\agy-release\config.json, else the GitHub device flow (one browser prompt).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
API = "https://api.github.com"
UPLOADS = "https://uploads.github.com"
CLIENT_ID = "178c6fc778ccc68e1d6a"
CONFIG = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "agy-release" / "config.json"
BEGIN = "# >>> agy-release managed >>>"
END = "# <<< agy-release managed <<<"
SOURCE_FILES = ("README.md", "install.py", "plugin.json", "composer_status.py", "cdp_guard.py", "usage.py",
                "dashboard.py", "prices.json")


def say(message=""):
    print(message, flush=True)


def step(title):
    say("")
    say("== " + title)


def run(args, cwd=ROOT, check=True):
    result = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise SystemExit("command failed: " + " ".join(args) + "\n" + detail)
    return result


def git(*args, check=True):
    return run(["git", *args], check=check).stdout.strip()


def read_version():
    return json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))["version"]


def repo_slug():
    url = git("remote", "get-url", "origin")
    match = re.search(r"github\.com[:/]+([^/]+)/([^/\s]+?)(?:\.git)?$", url)
    if not match:
        raise SystemExit("无法解析 origin 地址：" + url)
    return match.group(1), match.group(2)


def build_portable():
    say("构建便携安装包（下载 embedded python，约 10 MB）...")
    sys.path.insert(0, str(ROOT))
    import build_release
    build_release.build()


def build_source_zip(version):
    import shutil
    import zipfile
    name = "antigravity-usage-" + version + "-source-update"
    folder = DIST / name
    if folder.exists():
        shutil.rmtree(folder)
    files = [ROOT / item for item in SOURCE_FILES if (ROOT / item).exists()]
    files += sorted(p for p in (ROOT / "web").glob("*") if p.is_file())
    for item in files:
        target = folder / item.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(item.read_bytes())
    archive = DIST / (name + ".zip")
    if archive.exists():
        archive.unlink()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for item in sorted(folder.rglob("*")):
            if item.is_file():
                bundle.write(item, name + "/" + item.relative_to(folder).as_posix())
    return archive


def artifacts(version):
    base = "antigravity-usage-" + version
    return [DIST / (base + "-win-x64.zip"), DIST / (base + "-source-update.zip")]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sync_gitignore(version, dry):
    path = ROOT / ".gitignore"
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    block = "\n".join([
        BEGIN,
        "dist/*/",
        "dist/*.zip",
        "dist/*.sha256",
        "!dist/antigravity-usage-" + version + "-win-x64.zip",
        "!dist/antigravity-usage-" + version + "-win-x64.zip.sha256",
        "!dist/antigravity-usage-" + version + "-source-update.zip",
        END,
    ])
    if BEGIN in text and END in text:
        head = text[: text.index(BEGIN)]
        tail = text[text.index(END) + len(END):].lstrip("\n")
        updated = head + block + ("\n\n" + tail if tail.strip() else "\n")
    else:
        base = text.rstrip("\n")
        updated = (base + "\n\n" if base else "") + block + "\n"
    if updated != text:
        say("更新 .gitignore 放行清单 -> v" + version)
        if not dry:
            path.write_text(updated, encoding="utf-8", newline="")


def prune_old_artifacts(version, dry):
    keep = {"dist/antigravity-usage-" + version + "-win-x64.zip",
            "dist/antigravity-usage-" + version + "-win-x64.zip.sha256",
            "dist/antigravity-usage-" + version + "-source-update.zip"}
    tracked = [line for line in git("ls-files", "dist").splitlines() if line.strip()]
    stale = [item for item in tracked if item not in keep]
    if stale:
        say("从索引移除 " + str(len(stale)) + " 个旧版本文件")
        if not dry:
            git("rm", "--cached", "-q", *stale)


def commit_all(version, message, dry):
    git("add", "-A")
    staged = git("diff", "--cached", "--name-only")
    if not staged:
        say("没有需要提交的改动")
        return False
    files = staged.splitlines()
    say("暂存 " + str(len(files)) + " 个文件：")
    for item in files[:12]:
        say("  + " + item)
    if len(files) > 12:
        say("  ... 另有 " + str(len(files) - 12) + " 个")
    if dry:
        return True
    git("commit", "-q", "-m", message)
    say("commit " + git("rev-parse", "--short", "HEAD") + "  " + message)
    return True


def ensure_tag(version, dry):
    tag = "V" + version
    if git("tag", "--list", tag):
        say("tag " + tag + " 已存在")
        return tag
    if not dry:
        git("tag", "-a", tag, "-m", "v" + version)
    say("创建 tag " + tag)
    return tag


def api(method, path, body=None, raw=None, content_type="application/json", host=API, token=None):
    data = None
    headers = {"User-Agent": "agy-release", "Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    if raw is not None:
        data = raw
        headers["Content-Type"] = content_type
    elif body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = content_type
    request = Request(host + path, data=data, method=method, headers=headers)
    try:
        with urlopen(request, timeout=180) as response:
            payload = response.read()
            return json.loads(payload) if payload else {}
    except HTTPError as error:
        text = error.read().decode("utf-8", "replace")
        raise HTTPError(error.url, error.code, str(error.reason) + ": " + text[:400], error.headers, None)


def device_login():
    say("首次发布需要一次 GitHub 授权（之后 token 缓存在本地）。")
    start = api("POST", "/login/device/code", host="https://github.com",
                raw=("client_id=" + CLIENT_ID + "&scope=repo").encode(),
                content_type="application/x-www-form-urlencoded")
    say("")
    say("  打开 " + start["verification_uri"] + "  输入代码  " + start["user_code"])
    say("")
    interval = max(5, int(start.get("interval", 5)))
    deadline = time.time() + int(start.get("expires_in", 900))
    payload = ("client_id=" + CLIENT_ID + "&device_code=" + start["device_code"]
               + "&grant_type=urn:ietf:params:oauth:grant-type:device_code")
    while time.time() < deadline:
        time.sleep(interval)
        try:
            result = api("POST", "/login/oauth/access_token", host="https://github.com",
                         raw=payload.encode(), content_type="application/x-www-form-urlencoded")
        except HTTPError:
            continue
        if result.get("access_token"):
            return result["access_token"]
        if result.get("error") not in ("authorization_pending", "slow_down"):
            raise SystemExit("授权失败：" + str(result.get("error")) + " " + str(result.get("error_description", "")))
    raise SystemExit("授权超时，请重新运行。")


def cached_token():
    if not CONFIG.exists():
        return ""
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8")).get("token", "")
    except Exception:
        return ""


def store_token(token, login):
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps({"token": token, "login": login,
                                  "saved": time.strftime("%Y-%m-%d %H:%M:%S")},
                                 ensure_ascii=False, indent=1), encoding="utf-8")
    if os.name == "nt":
        subprocess.run(["icacls", str(CONFIG), "/inheritance:r",
                        "/grant:r", os.environ.get("USERNAME", "user") + ":F"], capture_output=True)


def get_token(force=False):
    for name in ("GITHUB_TOKEN", "GH_TOKEN"):
        value = os.environ.get(name, "").strip()
        if value:
            return value, "环境变量 " + name
    if not force:
        cached = cached_token()
        if cached:
            return cached, "本地缓存"
    token = device_login()
    login = api("GET", "/user", token=token).get("login", "?")
    store_token(token, login)
    say("已授权账号 " + login + "，token 缓存到 " + str(CONFIG))
    return token, "device flow（" + login + "）"


def release_body(version, notes_file, files):
    if notes_file:
        return Path(notes_file).read_text(encoding="utf-8")
    changelog = ROOT / "CHANGELOG.md"
    if changelog.exists():
        text = changelog.read_text(encoding="utf-8")
        match = re.search(r"^##\s*v?" + re.escape(version) + r".*?(?=^##\s|\Z)", text, re.S | re.M)
        if match:
            return match.group(0).replace("## v" + version, "", 1).strip()
    lines = ["### v" + version, "", "artifacts:", ""]
    for item in files:
        lines.append("- " + item.name + " — " + format(item.stat().st_size, ",") + " bytes, sha256 " + digest(item))
    return "\n".join(lines)


def find_release(owner, repo, tag, token):
    try:
        return api("GET", "/repos/" + owner + "/" + repo + "/releases/tags/" + tag, token=token)
    except HTTPError as error:
        if error.code == 404:
            return None
        raise


def publish(owner, repo, tag, version, files, notes_file, token, force_upload, dry):
    existing = find_release(owner, repo, tag, token)
    body = release_body(version, notes_file, files)
    name = "v" + version
    if existing:
        say("Release 已存在：" + existing["html_url"])
        if not dry:
            api("PATCH", "/repos/" + owner + "/" + repo + "/releases/" + str(existing["id"]),
                {"body": body, "name": name}, token=token)
        release = existing
    else:
        if dry:
            say("[dry-run] 将创建 Release " + tag)
            return None
        release = api("POST", "/repos/" + owner + "/" + repo + "/releases",
                      {"tag_name": tag, "name": name, "body": body,
                       "draft": False, "prerelease": False}, token=token)
        say("创建 Release " + release["html_url"])
    current = {asset["name"]: asset for asset in release.get("assets", [])}
    for item in files:
        asset = current.get(item.name)
        if asset and asset["size"] == item.stat().st_size and not force_upload:
            say("跳过 " + item.name + "（已上传，大小一致）")
            continue
        if dry:
            say("[dry-run] 将上传 " + item.name)
            continue
        if asset:
            api("DELETE", "/repos/" + owner + "/" + repo + "/releases/assets/" + str(asset["id"]), token=token)
        request = Request(UPLOADS + "/repos/" + owner + "/" + repo + "/releases/" + str(release["id"])
                          + "/assets?name=" + item.name,
                          data=item.read_bytes(), method="POST",
                          headers={"Authorization": "Bearer " + token, "Content-Type": "application/zip",
                                   "User-Agent": "agy-release", "Accept": "application/vnd.github+json"})
        with urlopen(request, timeout=300) as response:
            uploaded = json.loads(response.read())
        say("上传 " + item.name + "  " + format(uploaded["size"], ",") + " 字节  sha256 " + digest(item)[:16] + "...")
        release = api("GET", "/repos/" + owner + "/" + repo + "/releases/" + str(release["id"]), token=token)
    return release


def main():
    parser = argparse.ArgumentParser(description="publish antigravity-usage: build, commit, tag, push, GitHub Release")
    parser.add_argument("--no-build", action="store_true", help="skip building, reuse dist artifacts")
    parser.add_argument("--no-push", action="store_true", help="commit only, do not push")
    parser.add_argument("--dry-run", action="store_true", help="print the plan, write nothing")
    parser.add_argument("--force-upload", action="store_true", help="re-upload assets even if size matches")
    parser.add_argument("--notes", default="", help="release body file")
    parser.add_argument("--login", action="store_true", help="authorize once and cache the token, then exit")
    parser.add_argument("--message", default="", help="commit message, default: release v<version>")
    parser.add_argument("--actions", action="store_true", help="push the tag only, let GitHub Actions publish")
    args = parser.parse_args()

    if args.login:
        token, origin = get_token(force=True)
        say("token 来源：" + origin)
        say("缓存位置 " + str(CONFIG))
        return

    version = read_version()
    tag = "V" + version
    owner, repo = repo_slug()
    step("publish " + owner + "/" + repo + "  v" + version + " (tag " + tag + ")")
    if args.dry_run:
        say("模式：dry-run")

    if args.no_build:
        say("跳过构建（--no-build）")
    elif args.dry_run:
        say("[dry-run] 将构建便携包与源码包")
    else:
        step("build")
        build_portable()

    if args.dry_run:
        say("[dry-run] 将生成源码更新包")
    else:
        step("source package")
        source_zip = build_source_zip(version)
        say(source_zip.name + "  " + format(source_zip.stat().st_size, ",") + " bytes")

    files = artifacts(version)
    missing = [item for item in files if not item.exists()]
    if missing and not args.dry_run:
        raise SystemExit("缺少构建产物：" + ", ".join(item.name for item in missing) + "（去掉 --no-build 重试）")

    step("stage and commit")
    sync_gitignore(version, args.dry_run)
    prune_old_artifacts(version, args.dry_run)
    commit_all(version, args.message or ("release v" + version), args.dry_run)
    ensure_tag(version, args.dry_run)

    if not args.no_push:
        step("push")
        if args.dry_run:
            say("[dry-run] git push origin main --follow-tags")
        else:
            result = run(["git", "push", "origin", "main", "--follow-tags"], check=False)
            output = (result.stdout + result.stderr).strip()
            if result.returncode != 0:
                raise SystemExit("推送失败：\n" + output)
            say(output.splitlines()[-1] if output else "已推送")

    if args.actions:
        say("")
        say("tag 已推送，由 GitHub Actions 构建并发布：https://github.com/" + owner + "/" + repo + "/actions")
        return

    step("github release")
    if args.dry_run:
        if os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN"):
            source = "environment"
        elif cached_token():
            source = "local cache"
        else:
            source = "needs one device-flow authorization"
        say("[dry-run] token source: " + source)
        publish(owner, repo, tag, version, files, args.notes, "", args.force_upload, True)
        say("")
        say("dry-run 结束。")
        return

    token, origin = get_token()
    say("token 来源：" + origin)
    release = publish(owner, repo, tag, version, files, args.notes, token, args.force_upload, False)

    step("done")
    if release:
        say(release["html_url"])
        for item in release.get("assets", []):
            say("  " + item["name"] + "  " + format(item["size"], ",") + " bytes  " + (item.get("digest") or ""))
    for item in files:
        say("  " + item.name + "  sha256 " + digest(item))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        raise SystemExit(130)
