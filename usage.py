"""Read the usage fields from Antigravity desktop 2.32 conversation databases."""

from bisect import bisect_right
from collections import defaultdict
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from functools import partial
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import sqlite3
import time
from urllib.error import URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from pricing import DEFAULT_BOOK, current_at, load_book, title_key

ROOT = Path(__file__).resolve().parent
APP_DATA = Path.home() / ".gemini" / "antigravity"
TZ = timezone(timedelta(hours=8))
UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", re.I)
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
    cleaned = " ".join(re.sub(r"[()]", " ", value).casefold().split())
    return re.sub(r"\s+extra(?: high)?$", " xhigh", cleaned)


def model_family(value):
    return re.sub(r"\s+(?:low|medium|high|xhigh|thinking|fast|minimal)$", "", model_label(value))


def catalog_selection(catalog, label):
    """Match the desktop's visible label, including the selected thinking effort."""
    if not label:
        return {}
    if label in catalog:
        return {label: catalog[label]}
    normalized = model_label(label)
    exact = {key: entry for key, entry in catalog.items() if model_label(entry["label"]) == normalized}
    if exact:
        return exact
    family = model_family(label)
    return {key: entry for key, entry in catalog.items()
            if family in entry.get("aliases", ()) or model_label(entry.get("group", {}).get("label", "")) == normalized}


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


def call_errors(db, details):
    """Keep error records separate from billing. Attribute only by exact generation index."""
    errors = []
    matches = defaultdict(list)
    for index, detail in details.items():
        matches[id(detail)].append(index)
    assigned = set()
    for index, raw in db.execute(
        "SELECT idx, metadata FROM steps WHERE step_type=17 AND status=3 ORDER BY idx"
    ):
        try:
            when = seconds(message(raw, (1,)), 1)
            moment = datetime.fromtimestamp(when, TZ) if when else None
        except (OSError, TypeError, ValueError, OverflowError):
            continue
        if not moment:
            continue
        detail = details.get(index)
        model = detail[1] if detail else ""
        marker = detail[0].get(7) if model else None
        key = id(detail) if model else None
        if key is not None and key in assigned:
            continue
        if key is not None:
            assigned.add(key)
        errors.append({
            "time": moment.isoformat(timespec="milliseconds"), "day": moment.date().isoformat(),
            "model": model, "label": detail[2] if model else "", "_message": marker,
            "_digest": sha256(str(index).encode("ascii") + b":" + raw).digest(),
            "_steps": matches[key] if key is not None else [],
        })
    return errors


def conversation_rows(path, *, health=False, context=False):
    with readonly(path) as db:
        db.execute("BEGIN")
        details = generation_details(db)
        rows = []
        snapshots = []
        compactions = 0
        # 15 = planner response, 23 = checkpoint (context compaction), 3 = completed.
        condition = "(status=3 OR step_type=15)" if context else "status=3"
        for index, step_type, status, raw in db.execute(
            f"SELECT idx, step_type, status, metadata FROM steps WHERE step_type IN (15, 23) AND {condition} ORDER BY idx"
        ):
            compactions += step_type == 23 and status == 3
            metadata = message(raw, (1, 9))
            usage = nested(metadata, 9, (1, 2, 3, 4, 5, 7))
            when = seconds(metadata, 1)
            if not when:
                continue
            tag = read_number(usage, 1)
            uncached, output, written, cached = (read_number(usage, i) or 0 for i in (2, 3, 4, 5))
            marker, model, label, duration, used, limit, ttft, breakdown = details.get(
                index, ({}, "", "", None, None, None, None, []))
            if marker.get(7) and usage.get(7) and marker[7] != usage[7]:
                model, label, duration, used, limit = "", "", None, None, None
                ttft, breakdown = None, []
            model = model or MODEL_IDS.get(tag, f"model-{tag}" if tag else "unknown")
            moment = datetime.fromtimestamp(when, TZ)
            identity = {
                "time": moment.isoformat(timespec="milliseconds"),
                "day": moment.date().isoformat(), "step": index, "model": model, "label": label or model,
            }
            if context and step_type == 15 and (status == 3 or used is not None):
                snapshots.append({**identity, "context": used, "limit": limit, "breakdown": breakdown})
            if status != 3 or not (uncached + cached + written + output):
                continue
            rows.append({
                **identity,
                "uncached": uncached, "cached": cached, "written": written, "output": output,
                "seconds": duration if duration and duration > 0 else None, "context": used,
                "limit": limit, "ttft": ttft, "breakdown": breakdown, "_message": usage.get(7),
                "_model_tag": tag,
            })
        result = (rows, compactions, call_errors(db, details)) if health else (rows, compactions)
        return (*result, snapshots) if context else result


def reference_cost(row, book=DEFAULT_BOOK):
    rate = book.rate(row)
    if not rate:
        return None
    return (
        row["uncached"] * (rate.get("input") or 0) + row["cached"] * (rate.get("read") or 0)
        + row["written"] * (rate.get("write") or 0) + row["output"] * (rate.get("output") or 0)
    ) / 1_000_000


def reference_savings(row, book=DEFAULT_BOOK):
    """Cached tokens are billed at the read rate instead of the input rate."""
    rate = book.rate(row)
    if not rate or rate.get("input") is None or rate.get("read") is None or rate["input"] <= rate["read"]:
        return None
    return row["cached"] * (rate["input"] - rate["read"]) / 1_000_000


def reference_total(rows, book=DEFAULT_BOOK):
    amounts = [amount for row in rows if (amount := reference_cost(row, book)) is not None]
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


def aggregate(rows, book):
    sums = {key: sum(row[key] for row in rows) for key in ("uncached", "cached", "written", "output")}
    total_input = sums["uncached"] + sums["cached"] + sums["written"]
    timed = [row for row in rows if row["seconds"]]
    priced = [row for row in rows if row["usd"] is not None]
    converted = [row for row in priced if row["cny"] is not None]
    saved = [amount for row in rows if (amount := reference_savings(row, book)) is not None]
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


def heatmap_key(row, hourly):
    return row["time"][:13].replace("T", " ") + ":00" if hourly else row["day"]


def day_keys(first, last):
    begin, end = date.fromisoformat(first), date.fromisoformat(last)
    return [(begin + timedelta(days=offset)).isoformat() for offset in range((end - begin).days + 1)]


def health_period(now, window):
    if window == "24h":
        begin = now - timedelta(hours=24)
        first = begin.replace(minute=0, second=0, microsecond=0)
        size = int((now - first) // timedelta(hours=1)) + 1
        buckets = [(first + timedelta(hours=idx)).strftime("%Y-%m-%d %H:00") for idx in range(size)]
    else:
        begin = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        next_month = (begin + timedelta(days=31)).replace(day=1)
        buckets = [(begin + timedelta(days=idx)).date().isoformat() for idx in range((next_month - begin).days)]
    return begin.isoformat(timespec="seconds"), now.isoformat(timespec="seconds"), buckets


def dashboard_model_names(catalog, rows):
    """Use picker group names and order for the dashboard's presentation groups."""
    catalog = catalog or {}
    groups = {entry["label"]: entry["group"] for entry in catalog.values() if "group" in entry}
    by_id, by_label, order = {}, {}, {}
    for key, entry in catalog.items():
        label = entry["label"]
        group = groups.get(label)
        name = group["label"] if group else label
        if name == key:
            continue
        by_id[key] = name
        by_label[model_label(label)] = name
        by_label[model_label(name)] = name
        if group:
            by_id[group["id"]] = name
        order.setdefault(name, len(order))

    def picker_name(row):
        return (by_id.get(row["model"]) or by_label.get(model_label(row.get("label") or ""))
                or by_id.get(MODEL_IDS.get(row.get("_model_tag"))))

    observed = defaultdict(set)
    for row in rows:
        name = picker_name(row)
        if name:
            observed[row["model"]].add(name)
            if row.get("_model_tag") is not None:
                observed[f'model-{row["_model_tag"]}'].add(name)
    aliases = {key: next(iter(names)) for key, names in observed.items() if len(names) == 1}

    def name_of(row):
        model = row["model"]
        if not model:
            return ""
        return picker_name(row) or aliases.get(model) or row.get("label") or model

    return name_of, order


def call_health(rows, errors, buckets, hourly, included, name_of, order):
    by_model = defaultdict(dict)
    identities = defaultdict(set)
    failures = [error for error in errors if error["model"]]
    # A generation can link a completed planner step and an error step together.
    # Count it once as an error in the heatmap while preserving logged usage.
    failed_steps = {(error["id"], step) for error in failures for step in error["_steps"]}
    failed_messages = {error["_message"] for error in failures if error["_message"]}

    def cell(row):
        model = name_of(row)
        if row["model"]:
            identities[model].add(row["model"])
        key = heatmap_key(row, hourly)
        return by_model[model].setdefault(key, {"done": 0, "errors": 0, "slow": 0, "ttft": []})

    for row in rows:
        if ((row["id"], row["step"]) in failed_steps
                or row.get("_health_message") in failed_messages):
            continue
        target = cell(row)
        target["done"] += 1
        first_token = row.get("ttft")
        if first_token is not None:
            target["ttft"].append(first_token)
            target["slow"] += first_token >= 10
    for error in errors:
        if included(error):
            cell(error)["errors"] += 1

    models = []
    for label, cells in by_model.items():
        for point in cells.values():
            samples = sorted(point.pop("ttft"))
            middle = len(samples) // 2
            point["timed"] = len(samples)
            point["median_ttft"] = round((samples[middle] + samples[(len(samples) - 1) // 2]) / 2, 2) if samples else None
        models.append({"label": label, "ids": sorted(identities[label]), "cells": cells,
                       "count": sum(cell["done"] + cell["errors"] for cell in cells.values())})
    models.sort(key=lambda model: (not model["label"], order.get(model["label"], len(order)),
                                   -model["count"], model["label"]))
    used_buckets = {key for model in models for key in model["cells"]}
    columns = sorted(set(buckets) | used_buckets) if models else []
    return {"buckets": columns, "models": models}


def usage_heatmap(by_model, models, buckets, hourly):
    cards = []
    for model in models:
        label = model["model"]
        groups = defaultdict(list)
        for row in by_model[label]:
            groups[heatmap_key(row, hourly)].append(row)
        cells = {}
        for stamp, rows in groups.items():
            dollars = [row["usd"] for row in rows if row["usd"] is not None]
            yuan = [row["cny"] for row in rows if row["cny"] is not None]
            cells[stamp] = {
                "total": sum(map(count_tokens, rows)),
                "calls": len(rows),
                "usd": math.fsum(dollars) if dollars else None,
                "cny": math.fsum(yuan) if yuan else None,
                "unpriced": len(rows) - len(dollars),
            }
        cards.append({"label": label, "ids": model["ids"], "cells": cells})
    observed = {stamp for card in cards for stamp in card["cells"]}
    return {"buckets": sorted(set(buckets) | observed), "models": cards,
            "granularity": "hour" if hourly else "day"}


class Usage:
    def __init__(self, app_data=APP_DATA):
        self.app_data = app_data
        self.cached = {}
        self.compactions = {}
        self.errors = {}
        self.contexts = {}
        self.fx_range = None
        self.fx_rates = {}
        self.fx_checked = 0
        self.fx_fallback = False

    def read_conversation(self, path, *, context=False):
        stat = path.stat()
        wal = path.with_suffix(".db-wal")
        try:
            wal_stat = wal.stat()
        except FileNotFoundError:
            wal_stat = None
        version = (stat.st_mtime_ns, stat.st_size, wal_stat.st_mtime_ns if wal_stat else 0,
                   wal_stat.st_size if wal_stat else 0)
        if (path not in self.cached or version != self.cached[path][0] or context and path not in self.contexts):
            rows, compactions, errors, *snapshots = conversation_rows(path, health=True, context=context)
            self.cached[path] = (version, rows)
            self.compactions[path.stem] = compactions
            self.errors[path] = errors
            if context:
                self.contexts[path] = snapshots[0]
            else:
                self.contexts.pop(path, None)
        return self.cached[path][1]

    def scan(self, *, health=False):
        titles = {}
        try:
            with readonly(self.app_data / "conversation_summaries.db") as db:
                titles = dict(db.execute("SELECT conversation_id, title FROM conversation_summaries"))
        except sqlite3.Error:
            pass
        paths = sorted(p for p in (self.app_data / "conversations").glob("*.db") if UUID.fullmatch(p.stem))
        rows, problems = [], 0
        seen = set()
        errors, seen_errors, seen_failed_messages = [], set(), set()
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
                row = {k: v for k, v in entry.items() if k not in ("_message", "_model_tag")} | {"id": path.stem}
                if health:
                    row["_health_message"] = marker
                    row["_model_tag"] = entry.get("_model_tag")
                rows.append(row)
            if health:
                for error in self.errors.get(path, ()):
                    marker = error["_message"]
                    digest = error["_digest"]
                    if digest in seen_errors or marker and marker in seen_failed_messages:
                        continue
                    seen_errors.add(digest)
                    if marker:
                        seen_failed_messages.add(marker)
                    errors.append({**error, "id": path.stem})
        rows.sort(key=lambda row: (row["time"], row["step"]))
        result = rows, titles, len(paths), problems
        return (*result, errors) if health else result

    def week_reference_usd(self):
        today = datetime.now(TZ).date()
        first = (today - timedelta(days=6)).isoformat()
        last = today.isoformat()
        rows, _, _, problems = self.scan()
        in_range = [row for row in rows if first <= row["day"] <= last]
        if not in_range and not problems:
            return 0
        return reference_total(in_range, load_book(self.app_data))

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
               hours=0, day=False, *, health=False, health_window=""):
        start = date.fromisoformat(start).isoformat() if start else ""
        end = date.fromisoformat(end).isoformat() if end else ""
        if start and end and start > end:
            raise ValueError("开始日期晚于结束日期")
        if conversation and not UUID.fullmatch(conversation):
            raise ValueError("会话 ID 格式无效")
        hours = max(0, min(int(hours or 0), 168))
        moment_now = datetime.now(TZ)
        prices = load_book(self.app_data)
        summarize = partial(aggregate, book=prices)
        if day:
            start = end = moment_now.strftime("%Y-%m-%d")
            cutoff = moment_now.strftime("%Y-%m-%dT00:00:00")
            hours = 24
        else:
            cutoff = ((moment_now - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S")
                      if hours else "")
        rows, titles, scanned, problems, *error_data = self.scan(health=health)
        first, last = (rows[0]["day"], rows[-1]["day"]) if rows else ("", "")
        if convert and first:
            self.load_rates(first, last)
        fx_days = sorted(self.fx_rates) if convert else []
        by_session, daily, by_model = defaultdict(list), defaultdict(list), defaultdict(list)
        hourly = defaultdict(list)
        calendar = defaultdict(list)
        filtered = []
        selected = conversation or (rows[-1]["id"] if rows else "")
        def included(row):
            return not ((start and row["day"] < start) or (end and row["day"] > end)
                        or (cutoff and row["time"][:19] < cutoff) or (only and row["id"] != selected))

        if health:
            name_of, model_order = dashboard_model_names(catalog, rows)

        for row in rows:
            amount = reference_cost(row, prices)
            pos = bisect_right(fx_days, row["day"])
            fx_day = fx_days[pos - 1] if pos else None
            if fx_day and (date.fromisoformat(row["day"]) - date.fromisoformat(fx_day)).days > 7:
                fx_day = None
            rate = self.fx_rates[fx_day] if fx_day else None
            row.update(usd=amount, cny=amount * rate if amount is not None and rate else None,
                       fx_date=fx_day, fx_rate=rate)
            by_session[row["id"]].append(row)
            calendar[row["day"]].append(row)
            if not included(row):
                continue
            filtered.append(row)
            daily[row["day"]].append(row)
            hourly[row["time"][:13].replace("T", " ") + ":00"].append(row)
            by_model[name_of(row) if health else row["model"]].append(row)
        daily_filled = []
        if daily or start or end:
            low = start or (min(daily) if daily else "")
            high = end or (max(daily) if daily else "")
            if low and high:
                daily_filled = [(key, daily.get(key) or []) for key in day_keys(low, high)]
        sessions = []
        for key, group in by_session.items():
            summary = summarize(group)
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
            # A rolling window ends partway through the current hour and can
            # include records in the partial hour at its other end.
            span = range(now.hour if day else hours, -1, -1)
            for offset in span:
                moment = now - timedelta(hours=offset)
                key = moment.strftime("%Y-%m-%d %H:00")
                hourly_rows.append({"day": key, "hour": moment.strftime("%H:00"),
                                    **summarize(hourly.get(key) or [])})
        health_data = None
        if health:
            if health_window in ("24h", "month"):
                low, high, buckets = health_period(moment_now, health_window)

                def in_health_window(row):
                    return low[:19] <= row["time"][:19] <= high[:19] and (not only or row["id"] == selected)

                health_data = call_health(
                    [row for row in rows if in_health_window(row)], error_data[0], buckets, health_window == "24h",
                    in_health_window, name_of, model_order,
                )
                health_data.update(window=health_window, buckets=buckets)
            elif health_window == "all":
                def in_health_session(row):
                    return not only or row["id"] == selected

                health_data = call_health(
                    [row for row in rows if in_health_session(row)], error_data[0], [], False,
                    in_health_session, name_of, model_order,
                )
                health_buckets = health_data["buckets"]
                if health_buckets:
                    health_data["buckets"] = day_keys(health_buckets[0], health_buckets[-1])
                health_data["window"] = health_window
            else:
                buckets = [row["day"] for row in hourly_rows] if hours else [stamp for stamp, _ in daily_filled]
                health_data = call_health(filtered, error_data[0], buckets, bool(hours), included, name_of, model_order)
            model_rows = [
                {"model": label, "ids": sorted({row["model"] for row in group}),
                 "billing_models": sorted({prices.model(row) for row in group if row["usd"] is not None}),
                 **summarize(group)}
                for label, group in by_model.items()
            ]
            model_rows.sort(key=lambda item: (model_order.get(item["model"], len(model_order)), -item["total"]))
            usage_buckets = [row["day"] for row in hourly_rows] if hours else [stamp for stamp, _ in daily_filled]
            usage_data = usage_heatmap(by_model, model_rows, usage_buckets, bool(hours))
            for row in rows:
                row.pop("_health_message", None)
                row.pop("_model_tag", None)
            if latest is not None:
                latest.pop("_health_message", None)
                latest.pop("_model_tag", None)
        else:
            model_rows = sorted(
                [{"model": key, **summarize(group)} for key, group in by_model.items()],
                key=lambda item: item["total"], reverse=True,
            )
        return {
            "timezone": "UTC+08:00", "range": [first, last], "scanned": scanned, "unreadable": problems,
            "granularity": "hour" if hours else "day", "hours": hours, "day_mode": bool(day),
            "hourly": hourly_rows,
            "calendar": [{"day": day, **summarize(group)} for day, group in calendar.items()],
            "sessions": sessions, "selected": selected, "all": summarize(filtered),
            "session": {**summarize(session_rows), "compactions": self.compactions.get(selected, 0)},
            "latest": latest,
            "recent": [{**row, "billing_model": prices.model(row) if row["usd"] is not None else None}
                       for row in reversed(session_rows[-20:])],
            "days": [{"day": stamp, **summarize(group),
                      "fx_date": group[0]["fx_date"] if group else None,
                      "fx_rate": group[0]["fx_rate"] if group else None}
                     for stamp, group in daily_filled],
            "models": model_rows,
            "price_sources": prices.status,
            "fx": {"enabled": convert, "available": bool(fx_days), "fallback": self.fx_fallback,
                   "rate": self.fx_rates[max(self.fx_rates)] if self.fx_rates else None},
        } | ({"health": health_data, "usage_heatmap": usage_data} if health else {})


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
            response = desktop_read(info, "GetAvailableModels").get("response", {})
            models = response.get("models", {})
            if models:
                groups = {}
                for group_id, group in response.get("groupedModels", {}).items():
                    name = group.get("displayName")
                    if not name:
                        continue
                    for level in group.get("supportedThinkingLevels", []):
                        key = group_id + "-" + level["thinkingSuffix"]
                        groups[key] = {"id": group_id, "label": name}
                sorts = response.get("agentModelSorts") or []
                recommended = [key for group in sorts[0].get("groups", []) for key in group.get("modelIds", [])] if sorts else []
                catalog = {}
                for key in dict.fromkeys([*recommended, *models]):
                    model = models.get(key)
                    if isinstance(model, dict):
                        catalog[key] = {"label": model.get("displayName") or key, "limit": model.get("maxTokens")}
                        if key in groups:
                            catalog[key]["group"] = groups[key]
                names = {key: entry["label"] for key, entry in catalog.items()}
                names.update({group["id"]: group["label"] for group in groups.values()})
                today = datetime.now(TZ).date().isoformat()
                routes = current_at(load_book(app_data).routes, today) or {}
                for entry in catalog.values():
                    route = routes.get(title_key(entry["label"]))
                    if route and (planner := names.get(route[0])):
                        entry["aliases"] = [model_family("Orchestrator " + planner)]
                return catalog
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


def quotas_by_model(groups, labels, catalog=None):
    memberships = [(group, {model_family(name) for name in group["models"]}) for group in groups]
    result = {}
    for label in labels:
        models = catalog_selection(catalog or {}, label)
        labels_for_model = {model_family(entry["label"]) for entry in models.values()}
        labels_for_model.add(model_family(label))
        matches = [group for group, names in memberships if labels_for_model & names]
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
