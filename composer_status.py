"""Add a local usage row to the Antigravity desktop composer through its loopback inspector."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime
from functools import lru_cache
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import socket
import sqlite3
from stat import S_ISREG
import time
from urllib.parse import urlsplit

from websocket import WebSocketException, create_connection

from pricing import current_at, load_book, refresh_sources, title_key

try:
    from cdp_guard import heal
except ImportError:  # older installs ship without the self-heal module
    def heal():
        return None

from usage import (APP_DATA, LOOPBACK, ROOT, UUID, Usage, catalog_limit, catalog_selection,
                   count_tokens, latest_usd_cny, message, model_catalog, model_label, quota_snapshot, quotas_by_model,
                   readonly, reference_total, seconds)


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
INSTALL = (
    BAR
    + "\nwindow.__agyUsageStatus.install(" + ASSETS + ");"
    + "\ntry {" + VIEW + "\n} catch (error) { console.error('[agy-usage] dashboard bundle failed', error); }"
)
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


def active_context(rows, selected, catalog, window_mode="", *, prices=None, since=None):
    recent = rows[-1] if rows else {}
    selection = catalog_selection(catalog, selected)
    actual_id = recent.get("model", "")
    actual = catalog.get(actual_id, {})
    actual_label = actual.get("label") or recent.get("label")
    actual_selection = (catalog_selection(catalog, recent.get("label", "")) or catalog_selection(catalog, actual_id))
    correlated = bool(recent and (actual_id in selection
                      or model_label(recent.get("label", "")) == model_label(selected)
                      or selection.keys() & actual_selection.keys()))
    if actual_id == "gemini-flash-worker-agy" and any(key.startswith("gemini-pro-orchestrator") for key in selection):
        latest_route = next((row for row in reversed(rows) if row["model"] != actual_id), {})
        correlated = latest_route.get("model") in selection

    if not correlated and recent and prices and since is not None:
        routes = current_at(prices.routes, recent["day"]) or {}
        selected_routes = {tuple(routes[title_key(entry["label"])]) for entry in selection.values()
                           if title_key(entry["label"]) in routes}
        if len(selected_routes) == 1 and datetime.fromisoformat(recent["time"]).timestamp() >= since:
            planner, executor = next(iter(selected_routes))
            executed = prices.model(recent) or actual_id
            if executed == planner:
                correlated = True
            elif executed == executor:
                # A direct worker call belongs to the route only after the
                # selected planner has appeared in the same user turn.
                for row in reversed(rows[:-1]):
                    if datetime.fromisoformat(row["time"]).timestamp() < since:
                        break
                    if row["model"] in selection or (prices.model(row) or row["model"]) == planner:
                        correlated = True
                        break
    use_recent = bool(selected and correlated)
    used = recent.get("context") if use_recent else None
    configured = catalog_limit(selection)
    call_limit = recent.get("limit") if use_recent else None
    context_limit = (call_limit or catalog_limit(actual_selection) or configured) if used is not None else configured
    return {
        "context": round(100 * used / context_limit, 1) if used is not None and context_limit else None,
        "used": used, "model_limit": configured, "context_limit": context_limit,
        "call_limit": call_limit,
        "call_model": actual_label if use_recent else None, "window_mode": window_mode,
        "waiting_for_call": bool(selected and used is None),
        "breakdown": recent.get("breakdown", []) if use_recent else [],
    }


def step_statistics(path):
    with readonly(path) as db:
        db.execute("BEGIN")
        last_input = db.execute(
            "SELECT metadata FROM steps WHERE step_type=14 ORDER BY idx DESC LIMIT 1"
        ).fetchone()
        raw_input = last_input[0] if last_input else None
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
        return {"turns": turns, "steps": steps, "tool_seconds": tool_time, "compactions": compactions,
                "_since": seconds(message(raw_input, (1,)), 1)}


CHECKBOX = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+\[([ xX/])\]\s+\S")
FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
PLAN_FILE = re.compile(r"(?:.*[_-])?(?:task|tasks|plan)\.md(?:\.resolved(?:\.\d+)?)?", re.I)


@lru_cache(maxsize=64)
def parsed_checklist(path, _stamp):
    with path.open("rb") as stream:
        content = stream.read(256_001)
    if len(content) > 256_000:
        return {}
    marks = []
    fence = ""
    for line in content.decode("utf-8-sig", errors="replace").splitlines():
        boundary = FENCE.match(line)
        if boundary and (not fence or boundary[1][0] == fence[0] and len(boundary[1]) >= len(fence)):
            fence = "" if fence else boundary[1]
        elif not fence and (box := CHECKBOX.match(line)):
            marks.append(box[1].lower())
    return {"done": marks.count("x"), "total": len(marks), "in_progress": marks.count("/")} if marks else {}


def task_checklist(base, since):
    if since is None:
        return {}
    candidates = []
    for folder in (base, base / ".system_generated"):
        try:
            for path in folder.iterdir():
                if not PLAN_FILE.fullmatch(path.name):
                    continue
                info = path.stat()
                if info.st_mtime >= since and info.st_size <= 256_000 and S_ISREG(info.st_mode):
                    candidates.append((info.st_mtime_ns, path))
        except OSError:
            continue
    for stamp, path in sorted(candidates, reverse=True):
        try:
            progress = parsed_checklist(path, stamp)
        except OSError:
            continue
        if progress:
            return progress
    return {}


def task_progress(app_data, conversation, stats, recent=None, prices=None):
    status = ""
    background = False
    try:
        with readonly(app_data / "conversation_summaries.db") as db:
            row = db.execute(
                "SELECT status,not_fully_idle FROM conversation_summaries WHERE conversation_id=?", (conversation,)
            ).fetchone()
            if row:
                runtime, background = row
                status = {
                    "CASCADE_RUN_STATUS_RUNNING": "running",
                    "CASCADE_RUN_STATUS_WAITING_FOR_USER": "waiting",
                    "CASCADE_RUN_STATUS_IDLE": "idle",
                }.get(runtime, "")
    except (OSError, sqlite3.Error):
        pass
    since = stats.get("_since")
    checklist = task_checklist(app_data / "brain" / conversation, since)
    phase = ""
    if checklist and recent and prices:
        try:
            if datetime.fromisoformat(recent["time"]).timestamp() >= since:
                phase = prices.route_phase(recent)
        except ValueError:
            pass
    return {"state": status, "background": bool(background) and status == "idle", "last_phase": phase, **checklist}


def session_metrics(usage, conversation, selected="", catalog=None, window_mode=""):
    catalog = catalog or {}
    if not UUID.fullmatch(conversation):
        return {"reference_usd": 0, "compactions": 0, **active_context([], selected, catalog, window_mode)}
    path = usage.app_data / "conversations" / (conversation + ".db")
    try:
        rows = usage.read_conversation(path)
    except FileNotFoundError:
        rows = []
    except (OSError, ValueError, sqlite3.Error, OverflowError):
        return {}
    cached_stats = getattr(usage, "step_stats", {})
    usage.step_stats = cached_stats
    stats = {}
    prices = load_book(usage.app_data)
    if path in usage.cached:
        version = (usage.cached[path][0], prices)
        if path not in cached_stats or cached_stats[path][0] != version:
            try:
                stats = step_statistics(path)
            except (OSError, sqlite3.Error, TypeError, ValueError, OverflowError):
                pass
            else:
                stats["reference_usd"] = reference_total(rows, prices)
                cached_stats[path] = (version, stats)
        else:
            stats = cached_stats[path][1]
    progress = task_progress(usage.app_data, conversation, stats, rows[-1] if rows else None, prices)
    display_stats = {key: value for key, value in stats.items() if not key.startswith("_")}
    if not rows:
        return {"total": 0, "reference_usd": 0, "compactions": usage.compactions.get(path.stem, 0),
                **display_stats, "task_progress": progress, **active_context(rows, selected, catalog, window_mode)}
    recent = rows[-1]
    total = sum(map(count_tokens, rows))
    input_tokens = total - sum(row["output"] for row in rows)
    timings = [row["ttft"] for row in rows if row.get("ttft") is not None]
    duration = sum(row["seconds"] or 0 for row in rows)
    return {
        "total": total,
        **display_stats,
        "task_progress": progress,
        "model_seconds": duration,
        "average_ttft": sum(timings) / len(timings) if timings else None,
        "uncached": sum(row["uncached"] for row in rows),
        "cached": sum(row["cached"] for row in rows),
        "written": sum(row["written"] for row in rows),
        "output": sum(row["output"] for row in rows),
        "hit": round(100 * sum(row["cached"] for row in rows) / input_tokens, 1) if input_tokens else None,
        "speed": round(recent["output"] / recent["seconds"], 1) if recent["seconds"] else None,
        **active_context(rows, selected, catalog, window_mode, prices=prices, since=stats.get("_since")),
    }


def dashboard_report(usage, filters, snapshot, catalog, fx):
    try:
        report = usage.report(
            filters.get("start", ""), filters.get("end", ""), filters.get("conversation", ""),
            True, filters.get("only") is True, catalog, filters.get("hours") or 0,
            filters.get("day") is True, health=True, health_window=filters.get("health_window", "24h"),
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
    prices_job, prices_due = None, 0
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
            if prices_job is not None and prices_job.done():
                try:
                    if prices_job.result():
                        cached_sessions.clear()
                        week_due = 0
                except (OSError, ValueError):
                    LOG.debug("Could not refresh public pricing sources", exc_info=True)
                prices_job = None
            if prices_job is None and targets and now >= prices_due:
                prices_job = rate_executor.submit(refresh_sources, APP_DATA)
                prices_due = now + 300
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
                        "window.__agyUsageStatus?.version === 40 && window.__agyUsageStatus.probe();"))
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
                    quotas = quotas_by_model(snapshot["groups"], {item["model"] for item in probe["inputs"]}, catalog)
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
