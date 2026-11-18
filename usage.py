"""Read the usage fields from Antigravity desktop 2.32 conversation databases."""

from bisect import bisect_right
from collections import defaultdict
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
import json
import math
from pathlib import Path
import re
import sqlite3
import time
from urllib.error import URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


ROOT = Path(__file__).resolve().parent
APP_DATA = Path.home() / ".gemini" / "antigravity"
TZ = timezone(timedelta(hours=8))
UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", re.I)
PRICES = json.loads((ROOT / "prices.json").read_text(encoding="utf-8"))
ALIASES = {name.casefold(): target for name, target in PRICES.get("aliases", {}).items()}
FX_CACHE = APP_DATA / "agy_usage_fx.json"
FALLBACK_FX = 7.10
DIRECT = build_opener(ProxyHandler({}))
MODEL_IDS = {
    1318: "gemini-3.8-flash", 1319: "gemini-3.8-flash", 1322: "gemini-3.8-flash",
    1418: "gemini-4-argon", 1419: "gemini-4-argon",
    1401: "claude-opus-5-5", 1035: "claude-sonnet-4-6",
}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_):
        return None


LOOPBACK = build_opener(ProxyHandler({}), NoRedirect())


def varint(data, offset):
    value = 0
    for shift in range(0, 70, 7):
        if offset >= len(data):
            break
        byte = data[offset]
        offset += 1
        value |= (byte & 127) << shift
        if byte < 128:
            return value, offset
    raise ValueError("Invalid protobuf varint")


def wire_fields(data):
    offset = 0
    while offset < len(data):
        tag, offset = varint(data, offset)
        number, kind = divmod(tag, 8)
        if not number:
            raise ValueError("Invalid protobuf field")
        if kind == 0:
            value, offset = varint(data, offset)
        else:
            if kind == 2:
                size, offset = varint(data, offset)
            elif kind in (1, 5):
                size = 8 if kind == 1 else 4
            else:
                raise ValueError("Unsupported protobuf wire type")
            end = offset + size
            if end > len(data):
                raise ValueError("Truncated protobuf field")
            value, offset = data[offset:end], end
        yield number, value


def message(data, wanted):
    return {number: value for number, value in wire_fields(data or b"") if number in wanted}


def nested(fields, number, wanted):
    data = fields.get(number)
    return message(data, wanted) if isinstance(data, bytes) else {}


def read_number(fields, number):
    value = fields.get(number)
    return value if isinstance(value, int) and 0 <= value < 2**53 else None


def seconds(fields, number):
    value = nested(fields, number, (1, 2))
    if not value:
        return None
    return (read_number(value, 1) or 0) + (read_number(value, 2) or 0) / 1e9


def model_text(value):
    if not isinstance(value, bytes) or len(value) > 120:
        return ""
    return "".join(c for c in value.decode("utf-8", "replace") if c.isprintable())


def model_label(value):
    return re.sub(r"[()]", "", value).casefold().strip()


def catalog_selection(catalog, label):
    """Match the desktop's visible label, including the selected thinking effort."""
    if not label:
        return {}
    if label in catalog:
        return {label: catalog[label]}
    normalized = model_label(label)
    return {key: entry for key, entry in catalog.items() if model_label(entry["label"]) == normalized}


def catalog_limit(entries):
    limits = {entry.get("limit") for entry in entries.values()}
    if len(limits) == 1:
        limit = next(iter(limits))
        if type(limit) is int and limit > 0:
            return limit
    return None


def generation_details(db):
    result = {}
    for (raw,) in db.execute("SELECT data FROM gen_metadata"):
        indices = []
        chat_data = None
        for number, value in wire_fields(raw):
            if number == 1 and isinstance(value, bytes):
                chat_data = value
            elif number == 2:
                if isinstance(value, int):
                    indices.append(value)
                    continue
                offset = 0
                while offset < len(value):
                    index, offset = varint(value, offset)
                    indices.append(index)
        if not chat_data or not indices:
            continue
        chat = message(chat_data, (4, 9, 11, 12, 19, 21, 22))
        context = nested(nested(chat, 9, (10,)), 10, (1, 3, 4))
        breakdown = []
        for field, raw in wire_fields(context.get(3, b"")):
            if field != 1 or not isinstance(raw, bytes):
                continue
            group = message(raw, (1, 4))
            count = read_number(group, 4)
            if count is not None:
                breakdown.append({"name": model_text(group.get(1)), "tokens": count})
        response = model_text(chat.get(22)) or model_text(chat.get(19))
        details = (
            nested(chat, 4, (1, 7)),
            response,
            model_text(chat.get(21)),
            seconds(chat, 12),
            read_number(context, 1),
            read_number(context, 4),  # ContextWindowMetadata.max_context_tokens for this call.
            seconds(chat, 11),
            breakdown,
        )
        for index in indices:
            result[index] = details
    return result


def readonly(path):
    return closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1))


def conversation_rows(path):
    with readonly(path) as db:
        db.execute("BEGIN")
        details = generation_details(db)
        rows = []
        compactions = 0
        # 15 = planner response, 23 = checkpoint (context compaction), 3 = completed.
        for index, step_type, raw in db.execute(
            "SELECT idx, step_type, metadata FROM steps WHERE step_type IN (15, 23) AND status=3 ORDER BY idx"
        ):
            compactions += step_type == 23
            metadata = message(raw, (1, 9))
            usage = nested(metadata, 9, (1, 2, 3, 4, 5, 7))
            when = seconds(metadata, 1)
            if not usage or not when:
                continue
            tag = read_number(usage, 1)
            uncached, output, written, cached = (read_number(usage, i) or 0 for i in (2, 3, 4, 5))
            if not (uncached + cached + written + output):
                continue
            marker, model, label, duration, context, limit, ttft, breakdown = details.get(
                index, ({}, "", "", None, None, None, None, []))
            if marker.get(7) and usage.get(7) and marker[7] != usage[7]:
                model, label, duration, context, limit = "", "", None, None, None
                ttft, breakdown = None, []
            model = model or MODEL_IDS.get(tag, f"model-{tag}" if tag else "unknown")
            moment = datetime.fromtimestamp(when, TZ)
            rows.append({
                "time": moment.isoformat(timespec="milliseconds"),
                "day": moment.date().isoformat(), "step": index, "model": model, "label": label or model,
                "uncached": uncached, "cached": cached, "written": written, "output": output,
                "seconds": duration if duration and duration > 0 else None, "context": context,
                "limit": limit, "ttft": ttft, "breakdown": breakdown, "_message": usage.get(7),
            })
    return rows, compactions


def priced_model(model):
    """Desktop role models share the price of the equivalent public model."""
    seen = set()
    while True:
        target = ALIASES.get(model.casefold())
        if not target or target in seen:
            return model
        seen.add(model)
        model = target


def row_rate(row):
    model = priced_model(row["model"])
    for base, rates in PRICES["models"].items():
        if model != base and not model.startswith(base + "-"):
            continue
        rate = next((r for r in reversed(rates) if r["from"] <= row["day"]), None)
        if not rate or (row["written"] and "write" not in rate):
            return None
        return rate
    return None


def reference_cost(row):
    rate = row_rate(row)
    if not rate:
        return None
    return (
        row["uncached"] * rate["input"] + row["cached"] * rate["read"]
        + row["written"] * rate.get("write", 0) + row["output"] * rate["output"]
    ) / 1_000_000


def reference_savings(row):
    """Cached tokens are billed at the read rate instead of the input rate."""
    rate = row_rate(row)
    if not rate or rate["input"] <= rate["read"]:
        return None
    return row["cached"] * (rate["input"] - rate["read"]) / 1_000_000


def reference_total(rows):
    amounts = [amount for row in rows if (amount := reference_cost(row)) is not None]
    return math.fsum(amounts) if amounts else None


def read_fx_cache():
    try:
        data = json.loads(FX_CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_fx_cache(data):
    try:
        FX_CACHE.parent.mkdir(parents=True, exist_ok=True)
        FX_CACHE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def latest_usd_cny():
    request = Request(
        "https://api.frankfurter.dev/v2/rate/USD/CNY",
        headers={"User-Agent": "Antigravity-Usage-Dashboard"},
    )
    try:
        with DIRECT.open(request, timeout=6) as response:
            quote = json.load(response)
        rate = quote["rate"]
        day = date.fromisoformat(quote["date"])
        if (quote["base"], quote["quote"]) == ("USD", "CNY") and type(rate) in (int, float) and 0 < rate < 100:
            cache = read_fx_cache()
            cache["latest"] = {"rate": rate, "date": day.isoformat()}
            write_fx_cache(cache)
            return {"rate": rate, "date": day.isoformat()}
    except (OSError, KeyError, ValueError, TypeError):
        pass
    cached = read_fx_cache().get("latest")
    if isinstance(cached, dict) and type(cached.get("rate")) in (int, float) and 0 < cached["rate"] < 100:
        return {"rate": cached["rate"], "date": str(cached.get("date") or ""), "cached": True}
    return None


def count_tokens(row):
    return sum(row[key] for key in ("uncached", "cached", "written", "output"))


def aggregate(rows):
    sums = {key: sum(row[key] for row in rows) for key in ("uncached", "cached", "written", "output")}
    total_input = sums["uncached"] + sums["cached"] + sums["written"]
    timed = [row for row in rows if row["seconds"]]
    priced = [row for row in rows if row["usd"] is not None]
    converted = [row for row in priced if row["cny"] is not None]
    saved = [amount for row in rows if (amount := reference_savings(row)) is not None]
    return {
        **sums, "calls": len(rows), "total": total_input + sums["output"],
        "hit": sums["cached"] / total_input if total_input else None,
        "speed": sum(row["output"] for row in timed) / sum(row["seconds"] for row in timed) if timed else None,
        "timed": len(timed), "usd": math.fsum(row["usd"] for row in priced) if priced else None,
        "cny": math.fsum(row["cny"] for row in converted) if converted else None,
        "priced": len(priced), "converted": len(converted), "unpriced": len(rows) - len(priced),
        "priced_tokens": sum(count_tokens(row) for row in priced),
        "savings": math.fsum(saved) if saved else None,
    }


class Usage:
    def __init__(self, app_data=APP_DATA):
        self.app_data = app_data
        self.cached = {}
        self.compactions = {}
        self.fx_range = None
        self.fx_rates = {}
        self.fx_checked = 0
        self.fx_fallback = False

    def read_conversation(self, path):
        stat = path.stat()
        wal = path.with_suffix(".db-wal")
        try:
            wal_stat = wal.stat()
        except FileNotFoundError:
            wal_stat = None
        version = (stat.st_mtime_ns, stat.st_size, wal_stat.st_mtime_ns if wal_stat else 0,
                   wal_stat.st_size if wal_stat else 0)
        if path not in self.cached or version != self.cached[path][0]:
            rows, compactions = conversation_rows(path)
            self.cached[path] = (version, rows)
            self.compactions[path.stem] = compactions
        return self.cached[path][1]

    def scan(self):
        titles = {}
        try:
            with readonly(self.app_data / "conversation_summaries.db") as db:
                titles = dict(db.execute("SELECT conversation_id, title FROM conversation_summaries"))
        except sqlite3.Error:
            pass
        paths = sorted(p for p in (self.app_data / "conversations").glob("*.db") if UUID.fullmatch(p.stem))
        rows, problems = [], 0
        seen = set()
        for path in paths:
            try:
                entries = self.read_conversation(path)
            except (OSError, sqlite3.Error, ValueError, TypeError, OverflowError):
                problems += 1
                continue
            for entry in entries:
                marker = entry["_message"]
                if marker and marker in seen:
                    continue
                if marker:
                    seen.add(marker)
                rows.append({k: v for k, v in entry.items() if k != "_message"} | {"id": path.stem})
        rows.sort(key=lambda row: (row["time"], row["step"]))
        return rows, titles, len(paths), problems

    def week_reference_usd(self):
        today = datetime.now(TZ).date()
        first = (today - timedelta(days=6)).isoformat()
        last = today.isoformat()
        rows, _, _, problems = self.scan()
        in_range = [row for row in rows if first <= row["day"] <= last]
        if not in_range and not problems:
            return 0
        return reference_total(in_range)

    def load_rates(self, first, last):
        start = (date.fromisoformat(first) - timedelta(days=7)).isoformat()
        bounds = start, last
        if bounds == self.fx_range and time.monotonic() - self.fx_checked < (
                1800 if self.fx_rates and not self.fx_fallback else 60):
            return
        if not self.fx_range or bounds != self.fx_range:
            self.fx_rates = {}
        self.fx_range, self.fx_checked = bounds, time.monotonic()
        request = Request(
            f"https://api.frankfurter.dev/v1/{start}..{last}?base=USD&symbols=CNY",
            headers={"User-Agent": "Antigravity-Usage-Dashboard/1.0"},
        )
        cache = read_fx_cache()
        try:
            with DIRECT.open(request, timeout=6) as response:
                rates = json.load(response)["rates"]
            self.fx_rates = {
                day: rate["CNY"] for day, rate in rates.items()
                if isinstance(rate.get("CNY"), (int, float)) and 0 < rate["CNY"] < 100
            }
        except (OSError, URLError, KeyError, ValueError, TypeError):
            self.fx_rates = {}
        self.fx_fallback = False
        if self.fx_rates:
            latest = max(self.fx_rates.items())
            cache.update(range=list(bounds), rates=self.fx_rates, latest={"date": latest[0], "rate": latest[1]})
            write_fx_cache(cache)
            return
        self.fx_fallback = True
        saved = cache.get("rates")
        if list(bounds) == cache.get("range") and isinstance(saved, dict) and saved:
            self.fx_rates = {day: rate for day, rate in saved.items()
                             if isinstance(rate, (int, float)) and 0 < rate < 100}
            return
        latest = cache.get("latest")
        rate = latest.get("rate") if isinstance(latest, dict) else None
        self.fx_rates = {start: rate if type(rate) in (int, float) and 0 < rate < 100 else FALLBACK_FX}

    def report(self, start="", end="", conversation="", convert=False, only=False, catalog=None,
               hours=0, day=False):
        start = date.fromisoformat(start).isoformat() if start else ""
        end = date.fromisoformat(end).isoformat() if end else ""
        if start and end and start > end:
            raise ValueError("开始日期晚于结束日期")
        if conversation and not UUID.fullmatch(conversation):
            raise ValueError("会话 ID 格式无效")
        hours = max(0, min(int(hours or 0), 168))
        moment_now = datetime.now(TZ)
        if day:
            start = end = moment_now.strftime("%Y-%m-%d")
            cutoff = moment_now.strftime("%Y-%m-%dT00:00:00")
            hours = 24
        else:
            cutoff = ((moment_now - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S")
                      if hours else "")
        rows, titles, scanned, problems = self.scan()
        first, last = (rows[0]["day"], rows[-1]["day"]) if rows else ("", "")
        if convert and first:
            self.load_rates(first, last)
        fx_days = sorted(self.fx_rates) if convert else []
        by_session, daily, by_model = defaultdict(list), defaultdict(list), defaultdict(list)
        hourly = defaultdict(list)
        calendar = defaultdict(list)
        filtered = []
        selected = conversation or (rows[-1]["id"] if rows else "")
        for row in rows:
            amount = reference_cost(row)
            pos = bisect_right(fx_days, row["day"])
            fx_day = fx_days[pos - 1] if pos else None
            if fx_day and (date.fromisoformat(row["day"]) - date.fromisoformat(fx_day)).days > 7:
                fx_day = None
            rate = self.fx_rates[fx_day] if fx_day else None
            row.update(usd=amount, cny=amount * rate if amount is not None and rate else None,
                       fx_date=fx_day, fx_rate=rate)
            by_session[row["id"]].append(row)
            calendar[row["day"]].append(row)
            if ((start and row["day"] < start) or (end and row["day"] > end)
                    or (cutoff and row["time"][:19] < cutoff) or (only and row["id"] != selected)):
                continue
            filtered.append(row)
            daily[row["day"]].append(row)
            hourly[row["time"][:13].replace("T", " ") + ":00"].append(row)
            by_model[row["model"]].append(row)
        daily_filled = []
        if daily or start or end:
            low = start or (min(daily) if daily else "")
            high = end or (max(daily) if daily else "")
            if low and high:
                cursor = datetime.strptime(low, "%Y-%m-%d").date()
                stop = datetime.strptime(high, "%Y-%m-%d").date()
                while cursor <= stop:
                    key = cursor.strftime("%Y-%m-%d")
                    daily_filled.append((key, daily.get(key) or []))
                    cursor += timedelta(days=1)
        sessions = []
        for key, group in by_session.items():
            summary = aggregate(group)
            sessions.append({
                "id": key, "title": titles.get(key) or key[:8], "updated": group[-1]["time"],
                "calls": len(group), "compactions": self.compactions.get(key, 0),
                "total": summary["total"], "usd": summary["usd"],
                "cny": summary["cny"], "unpriced": summary["unpriced"],
            })
        sessions.sort(key=lambda item: item["updated"], reverse=True)
        session_rows = by_session.get(selected, [])
        latest = None
        if session_rows:
            row = session_rows[-1]
            models = catalog or {}
            matches = catalog_selection(models, row["model"]) or catalog_selection(models, row["label"])
            latest = {**row, "model_limit": catalog_limit(matches)}
        hourly_rows = []
        if hours:
            now = moment_now.replace(minute=0, second=0, microsecond=0)
            span = (range(now.hour, -1, -1) if day else range(hours - 1, -1, -1))
            for offset in span:
                moment = now - timedelta(hours=offset)
                key = moment.strftime("%Y-%m-%d %H:00")
                hourly_rows.append({"day": key, "hour": moment.strftime("%H:00"),
                                    **aggregate(hourly.get(key) or [])})
        return {
            "timezone": "UTC+08:00", "range": [first, last], "scanned": scanned, "unreadable": problems,
            "granularity": "hour" if hours else "day", "hours": hours, "day_mode": bool(day),
            "hourly": hourly_rows,
            "calendar": [{"day": day, **aggregate(group)} for day, group in calendar.items()],
            "sessions": sessions, "selected": selected, "all": aggregate(filtered),
            "session": {**aggregate(session_rows), "compactions": self.compactions.get(selected, 0)},
            "latest": latest,
            "recent": list(reversed(session_rows[-20:])),
            "days": [{"day": stamp, **aggregate(group),
                      "fx_date": group[0]["fx_date"] if group else None,
                      "fx_rate": group[0]["fx_rate"] if group else None}
                     for stamp, group in daily_filled],
            "models": sorted(
                [{"model": key, **aggregate(group)} for key, group in by_model.items()],
                key=lambda item: item["total"], reverse=True,
            ),
            "fx": {"enabled": convert, "available": bool(fx_days), "fallback": self.fx_fallback,
                   "rate": self.fx_rates[max(self.fx_rates)] if self.fx_rates else None},
        }


def desktop_services(app_data):
    candidates = sorted((app_data / "daemon").glob("hub_*.json"),
                        key=lambda path: path.stat().st_mtime_ns if path.exists() else 0, reverse=True)
    for path in candidates[:6]:
        try:
            info = json.loads(path.read_bytes())
            if isinstance(info, dict):
                yield info
        except (OSError, ValueError):
            continue


def desktop_request(info, method, payload=None):
    port, token = info.get("httpPort"), info.get("csrfToken")
    if not isinstance(port, int) or not 1024 <= port <= 65535 or not isinstance(token, str) or not token:
        return {}
    request = Request(
        f"http://127.0.0.1:{port}/exa.language_server_pb.LanguageServerService/{method}",
        data=json.dumps(payload or {}).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Codeium-Csrf-Token": token},
    )
    with LOOPBACK.open(request, timeout=0.8) as response:
        return json.load(response)


def desktop_read(info, method):
    return desktop_request(info, method)


def model_catalog(https_port=None, app_data=APP_DATA):
    for info in desktop_services(app_data):
        if https_port is not None and info.get("httpsPort") != https_port:
            continue
        try:
            models = desktop_read(info, "GetAvailableModels").get("response", {}).get("models", {})
            if models:
                return {key: {"label": model.get("displayName") or key, "limit": model.get("maxTokens")}
                        for key, model in models.items() if isinstance(model, dict)}
        except (OSError, ValueError, TypeError, AttributeError):
            continue
    return {}


def parse_quota_groups(summary):
    groups = []
    for group in summary.get("groups", []):
        name = group.get("displayName", "")
        # This desktop API carries the member list in the group's description.
        heading, separator, members = group.get("description", "").partition(":")
        models = [model.strip() for model in members.split(",") if model.strip()] if (
            separator and heading.strip().casefold() == "models within this group"
        ) else []
        windows = {}
        for bucket in group.get("buckets", []):
            window = bucket.get("window")
            if window not in ("5h", "weekly") or bucket.get("disabled"):
                continue
            remaining = bucket.get("remainingFraction")
            windows[window] = {"window": window, "remaining": remaining if (
                type(remaining) in (int, float) and 0 <= remaining <= 1
            ) else None, "reset": bucket.get("resetTime")}
        if windows:
            groups.append({"name": name, "models": models,
                           "buckets": [windows[key] for key in ("5h", "weekly") if key in windows]})
    return groups


def quotas_by_model(groups, labels):
    result = {}
    for label in labels:
        base = re.sub(r"\s+\(?(?:low|medium|high|xhigh|thinking|fast|minimal)\)?$", "", label, flags=re.I)
        matches = [group for group in groups if base.casefold() in {name.casefold() for name in group["models"]}]
        if len(matches) == 1:
            group = matches[0]
            result[label] = {"group": group["name"], "buckets": group["buckets"]}
    return result


def quota_snapshot(app_data=APP_DATA, https_port=None):
    for info in desktop_services(app_data):
        if https_port is not None and info.get("httpsPort") != https_port:
            continue
        try:
            summary = desktop_read(info, "RetrieveUserQuotaSummary").get("response")
            if not isinstance(summary, dict):
                continue
            return {"available": True, "read_at": datetime.now(TZ).isoformat(timespec="seconds"),
                    "groups": parse_quota_groups(summary)}
        except (OSError, ValueError, TypeError, AttributeError):
            continue
    return {"available": False, "groups": []}
