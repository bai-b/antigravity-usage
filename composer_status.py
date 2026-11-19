"""Add a local usage row to the Antigravity desktop composer through its loopback inspector."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import socket
import sqlite3
import time
from urllib.parse import urlsplit

from websocket import WebSocketException, create_connection

try:
    from cdp_guard import heal
except ImportError:  # older installs ship without the self-heal module
    def heal():
        return None

from usage import (APP_DATA, LOOPBACK, ROOT, UUID, Usage, catalog_limit, catalog_selection,
                   count_tokens, latest_usd_cny, model_catalog, model_label, quota_snapshot, quotas_by_model,
                   reference_total)


DESKTOP_DATA = Path(os.environ["APPDATA"]) / "Antigravity"
LOCK_PORT = 43827
IPC_PING = b"agy-usage:ping"
IPC_READY = b"agy-usage:ready"
IPC_WAITING = b"agy-usage:waiting"
IPC_DETACH = b"agy-usage:detach"
WEB = ROOT / "web"
BAR = (WEB / "composer-status.js").read_text(encoding="utf-8")
HTML = (WEB / "dashboard.html").read_text(encoding="utf-8").split("<body>", 1)[1].split("</body>", 1)[0]
ASSETS = json.dumps({"html": HTML, "css": (WEB / "dashboard.css").read_text(encoding="utf-8")})
VIEW = (WEB / "dashboard.js").read_text(encoding="utf-8")
INSTALL = BAR + "\n" + VIEW + "\nwindow.__agyUsageStatus.install(" + ASSETS + ");"
LOG = logging.getLogger("composer-status")
CONNECTION_ERRORS = (OSError, ValueError, WebSocketException)


def desktop_pages():
    port = int((DESKTOP_DATA / "DevToolsActivePort").read_text().splitlines()[0])
    if not 1024 <= port <= 65535:
        return {}
    app_ports = set()
    for config in (APP_DATA / "daemon").glob("hub_*.json"):
        try:
            value = json.loads(config.read_bytes()).get("httpsPort")
            if type(value) is int:
                app_ports.add(value)
        except (OSError, ValueError, AttributeError):
            continue
    with LOOPBACK.open(f"http://127.0.0.1:{port}/json/list", timeout=2) as response:
        targets = json.load(response)
    pages = {}
    for target in targets:
        page = urlsplit(target.get("url", ""))
        debug = urlsplit(target.get("webSocketDebuggerUrl", ""))
        if (target.get("type") == "page" and page.scheme == "https" and page.hostname == "127.0.0.1"
                and page.port in app_ports and debug.scheme == "ws" and debug.hostname == "127.0.0.1"
                and debug.port == port and re.fullmatch(r"/devtools/page/[A-Fa-f0-9]+", debug.path)):
            pages[target["id"]] = target["webSocketDebuggerUrl"], page.netloc
    return pages


def evaluate(ws, expression):
    # A connection has one in-flight request at a time.
    ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate", "params": {
        "expression": expression, "returnByValue": True, "silent": True
    }}))
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        message = json.loads(ws.recv())
        if message.get("id") != 1:
            continue
        result = message.get("result", {})
        if "exceptionDetails" in result or "error" in message:
            raise ValueError("Desktop renderer is not ready")
        return result.get("result", {}).get("value")
    raise TimeoutError("Desktop renderer did not respond")


def renderer_code(authority, script):
    origin = json.dumps("https://" + authority)
    return "if(location.origin===" + origin + "){\n" + script + "\n}"


def active_context(rows, selected, catalog, window_mode=""):
    recent = rows[-1] if rows else {}
    selection = catalog_selection(catalog, selected)
    actual_id = recent.get("model", "")
    actual = catalog.get(actual_id, {})
    actual_label = actual.get("label") or recent.get("label")
    correlated = bool(recent and (actual_id in selection
                      or model_label(recent.get("label", "")) == model_label(selected)))
    if actual_id == "gemini-flash-worker-agy" and any(key.startswith("gemini-pro-orchestrator") for key in selection):
        latest_route = next((row for row in reversed(rows) if row["model"] != actual_id), {})
        correlated = latest_route.get("model") in selection
    use_recent = bool(selected and correlated)
    used = recent.get("context") if use_recent else None
    configured = catalog_limit(selection)
    return {
        "context": round(100 * used / configured, 1) if used is not None and configured else None,
        "used": used, "model_limit": configured,
        "call_limit": recent.get("limit") if use_recent else None,
        "call_model": actual_label if use_recent else None, "window_mode": window_mode,
        "waiting_for_call": bool(selected and used is None),
        "breakdown": recent.get("breakdown", []) if use_recent else [],
    }


def step_statistics(path):
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1) as db:
        from usage import message, seconds
        turns, steps, tool_time, compactions = 0, 0, 0, 0
        for kind, raw in db.execute("SELECT step_type,metadata FROM steps WHERE status=3"):
            steps += 1
            turns += kind == 14
            compactions += kind == 23
            metadata = message(raw, (1, 4, 8, 32))
            if 4 in metadata:
                started = seconds(metadata, 32) or seconds(metadata, 1)
                ended = seconds(metadata, 8)
                if started and ended and ended >= started:
                    tool_time += ended - started
        return {"turns": turns, "steps": steps, "tool_seconds": tool_time, "compactions": compactions}


def session_metrics(usage, conversation, selected="", catalog=None, window_mode=""):
    catalog = catalog or {}
    if not UUID.fullmatch(conversation):
        return {"reference_usd": 0, "compactions": 0, **active_context([], selected, catalog, window_mode)}
    path = APP_DATA / "conversations" / (conversation + ".db")
    try:
        rows = usage.read_conversation(path)
    except FileNotFoundError:
        rows = []
    except (OSError, ValueError, sqlite3.Error, OverflowError):
        return {}
    if not rows:
        return {"total": 0, "reference_usd": 0, "compactions": getattr(usage, "compactions", {}).get(path.stem, 0),
                **active_context(rows, selected, catalog, window_mode)}
    recent = rows[-1]
    total = sum(map(count_tokens, rows))
    input_tokens = total - sum(row["output"] for row in rows)
    cached_stats = getattr(usage, "step_stats", {})
    usage.step_stats = cached_stats
    version = usage.cached[path][0]
    if path not in cached_stats or cached_stats[path][0] != version:
        stats = step_statistics(path)
        stats["reference_usd"] = reference_total(rows)
        cached_stats[path] = (version, stats)
    timings = [row["ttft"] for row in rows if row.get("ttft") is not None]
    duration = sum(row["seconds"] or 0 for row in rows)
    return {
        "total": total,
        **cached_stats[path][1],
        "model_seconds": duration,
        "average_ttft": sum(timings) / len(timings) if timings else None,
        "uncached": sum(row["uncached"] for row in rows),
        "cached": sum(row["cached"] for row in rows),
        "written": sum(row["written"] for row in rows),
        "output": sum(row["output"] for row in rows),
        "hit": round(100 * sum(row["cached"] for row in rows) / input_tokens, 1) if input_tokens else None,
        "speed": round(recent["output"] / recent["seconds"], 1) if recent["seconds"] else None,
        **active_context(rows, selected, catalog, window_mode),
    }


def dashboard_report(usage, filters, snapshot, catalog, fx):
    try:
        report = usage.report(
            filters.get("start", ""), filters.get("end", ""), filters.get("conversation", ""),
            True, filters.get("only") is True, catalog, filters.get("hours") or 0,
            filters.get("day") is True,
        )
        return {"report": report | {"quota": snapshot, "latest_fx": fx}}
    except (OSError, sqlite3.Error, TypeError, ValueError) as error:
        return {"error": str(error) if isinstance(error, ValueError) else "暂时无法读取本机记录"}


def remove_renderers(connections, targets):
    for key, ws in connections.items():
        try:
            if key in targets:
                evaluate(ws, renderer_code(targets[key][1], "window.__agyUsageStatus?.remove();"))
        except CONNECTION_ERRORS:
            pass
        finally:
            ws.close()
    connections.clear()


def poll(guard):
    usage = Usage()
    history = Usage()
    executor = ThreadPoolExecutor(max_workers=1)
    rate_executor = ThreadPoolExecutor(max_workers=1)
    requests = {}
    catalogs = {}
    quota_cache = {}
    connections = {}
    targets = {}
    week_job = executor.submit(history.week_reference_usd)
    executor.submit(history.report, convert=True)
    rate_job = rate_executor.submit(latest_usd_cny)
    rate_due, week_due = 0, 0
    latest_rate, week_usd = None, None
    had_windows, last_rate_request = False, time.monotonic()
    cached_sessions = {}
    attached = False
    detached = False
    try:
        while True:
            try:
                command, sender = guard.recvfrom(65535)
                if command == b"stop":
                    break
                if command == IPC_PING:
                    guard.sendto(IPC_READY if attached else IPC_WAITING, sender)
                if command == IPC_DETACH:
                    remove_renderers(connections, targets)
                    detached, attached = True, False
                    guard.sendto(IPC_DETACH, sender)
            except TimeoutError:
                pass
            if detached:
                continue
            try:
                targets = desktop_pages()
            except (OSError, ValueError, IndexError):
                targets = {}
                try:
                    heal()
                except Exception:
                    LOG.debug("inspector self-heal failed", exc_info=True)
            now = time.monotonic()
            if rate_job is not None and rate_job.done():
                quote = rate_job.result()
                if quote is not None:
                    latest_rate = quote
                rate_job, rate_due = None, now + (3600 if quote else 60)
            if targets and not had_windows and now - last_rate_request >= 10:
                rate_due = 0
            had_windows = bool(targets)
            if rate_job is None and targets and now >= rate_due:
                rate_job, last_rate_request = rate_executor.submit(latest_usd_cny), now
            if week_job is not None and week_job.done():
                try:
                    week_usd = week_job.result()
                except (OSError, ValueError, sqlite3.Error):
                    pass
                week_job, week_due = None, now + 30
            if week_job is None and targets and now >= week_due:
                week_job = executor.submit(history.week_reference_usd)
            for key in list(connections.keys() - targets.keys()):
                connections.pop(key).close()
                request = requests.pop(key, None)
                if request:
                    request[2].cancel()
            if not targets:
                attached = False
                guard.settimeout(1)
                continue
            guard.settimeout(0.15)
            attached = False
            for key, (url, authority) in targets.items():
                try:
                    if key not in connections:
                        connections[key] = create_connection(
                            url, timeout=3, suppress_origin=True, http_no_proxy=["127.0.0.1"]
                        )
                    ws = connections[key]
                    probe = evaluate(ws, renderer_code(authority,
                        "window.__agyUsageStatus?.version === 25 && window.__agyUsageStatus.probe();"))
                    if probe is False:
                        evaluate(ws, renderer_code(authority, INSTALL))
                        probe = evaluate(ws, renderer_code(authority, "window.__agyUsageStatus.probe();"))
                    if not probe:
                        continue
                    port = int(authority.rsplit(":", 1)[-1])
                    expires, catalog = catalogs.get(port, (0, {}))
                    if time.monotonic() >= expires:
                        catalog = model_catalog(port) or catalog
                        catalogs[port] = (time.monotonic() + (60 if catalog else 10), catalog)
                    expires, snapshot = quota_cache.get(port, (0, {}))
                    if time.monotonic() >= expires:
                        snapshot = quota_snapshot(https_port=port)
                        quota_cache[port] = (time.monotonic() + (60 if snapshot["available"] else 10), snapshot)
                    quotas = quotas_by_model(snapshot["groups"], {item["model"] for item in probe["inputs"]})
                    sessions = []
                    for item in probe["inputs"]:
                        cache_key = (port, item["id"], item["model"], item["windowMode"])
                        expires, metrics = cached_sessions.get(cache_key, (0, {}))
                        if time.monotonic() >= expires:
                            metrics = session_metrics(usage, item["id"], item["model"], catalog, item["windowMode"])
                            cached_sessions[cache_key] = (time.monotonic() + 2, metrics)
                        sessions.append({**item, "usage": metrics})
                    pending = probe.get("request")
                    request = requests.get(key)
                    request_key = (pending["epoch"], pending["id"]) if pending else None
                    if request and request[:2] != request_key:
                        request[2].cancel()
                        requests.pop(key)
                        request = None
                    if pending and not request:
                        request = (*request_key, executor.submit(dashboard_report, history, pending["filters"],
                                                                 snapshot, catalog, latest_rate))
                        requests[key] = request
                    completed = None
                    if request and request[2].done():
                        completed = {"epoch": request[0], "id": request[1], **request[2].result()}
                        requests.pop(key)
                    payload = json.dumps({"sessions": sessions, "quotas": quotas, "completed": completed,
                                          "preview": {"week_usd": week_usd, "rate": latest_rate}})
                    evaluate(ws, renderer_code(authority, f"window.__agyUsageStatus.render({payload});"))
                    attached = True
                except CONNECTION_ERRORS:
                    if key in connections:
                        connections.pop(key).close()
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
        rate_executor.shutdown(wait=False, cancel_futures=True)
        remove_renderers(connections, targets)


def run():
    with closing(socket.socket(socket.AF_INET, socket.SOCK_DGRAM)) as guard:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            guard.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            guard.bind(("127.0.0.1", LOCK_PORT))
        except OSError:
            return
        guard.settimeout(0.15)
        poll(guard)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--stop", action="store_true")
    args = parser.parse_args()
    if args.stop:
        with closing(socket.socket(socket.AF_INET, socket.SOCK_DGRAM)) as signal:
            signal.sendto(b"stop", ("127.0.0.1", LOCK_PORT))
    else:
        logs = Path(os.environ["LOCALAPPDATA"]) / "AntigravityUsage"
        logs.mkdir(exist_ok=True)
        LOG.addHandler(RotatingFileHandler(logs / "composer.log", maxBytes=64000, backupCount=1, encoding="utf-8"))
        try:
            run()
        except Exception:
            LOG.exception("Usage status stopped")
            raise
