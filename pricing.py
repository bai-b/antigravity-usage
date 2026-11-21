"""Read and retain dated reference prices and routing from official public pages."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from html.parser import HTMLParser
from http.client import HTTPException, HTTPSConnection
import json
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from urllib.parse import urljoin, urlsplit
from urllib.request import getproxies


TZ = timezone(timedelta(hours=8))
SEED = json.loads(Path(__file__).with_name("prices.json").read_text(encoding="utf-8"))
SOURCES = {
    "google": ("Google 模型", SEED["google_source"] + "?hl=en"),
    "partner": ("合作模型", SEED["partner_source"]),
    "routes": ("模型分工", "https://antigravity.google/docs/models"),
}
MONTH_DATE = r"[A-Z][a-z]+\s+\d{1,2},\s+\d{4}"
DATE_NOTE = re.compile(rf"\b(starting|through)\s+({MONTH_DATE})", re.I)
MODEL_NAME = re.compile(r"(?:gemini\s+\d+(?:\.\d+)?\s+|(?:claude\s+)?(?:opus|sonnet|haiku|fable)\s+).+", re.I)


def official_day(text):
    return datetime.strptime(" ".join(text.split()), "%B %d, %Y").date()


def model_id(text):
    label = text.strip().lower()
    if not MODEL_NAME.fullmatch(label):
        return ""
    if not label.startswith(("gemini ", "claude ")):
        label = "claude " + label
    if label.startswith("claude "):
        label = label.replace(".", "-")
    return re.sub(r"[^a-z0-9.]+", "-", label).strip("-")


def title_key(label):
    return " ".join(label.casefold().split())


class OfficialTables(HTMLParser):
    """Read table cells and the tab labels identifying Standard and Global prices."""

    void = frozenset("area base br col embed hr img input link meta param source track wbr".split())

    def __init__(self, html):
        super().__init__()
        self.stack = []
        self.tables = []
        self.titles = {}
        self.write_day = None
        self.table = self.row = self.cell = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        role = attrs.get("role")
        panel, tab = self.stack[-1][1:] if self.stack else (None, None)
        if role == "tabpanel":
            panel = attrs.get("aria-labelledby")
        if role == "tab" and attrs.get("id"):
            tab = attrs["id"]
            self.titles.setdefault(tab, [])
        if tag not in self.void:
            self.stack.append((tag, panel, tab))
        if tag == "table":
            self.table = []
            self.tables.append((panel, self.table))
        elif self.table is not None and tag == "tr":
            self.row = []
            self.table.append(self.row)
        elif self.row is not None and tag in ("th", "td"):
            self.cell = []

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break
        if tag in ("th", "td") and self.cell is not None:
            self.row.append(" ".join(" ".join(self.cell).split()))
            self.cell = None
        if tag == "tr":
            self.row = None
        if tag == "table":
            self.table = self.row = self.cell = None

    def handle_data(self, text):
        if self.cell is not None:
            self.cell.append(text)
        if self.stack and self.stack[-1][2]:
            self.titles[self.stack[-1][2]].append(text)
        if "pricing for cache write effective" in text.lower():
            found = re.search(rf"pricing for cache write effective\s+({MONTH_DATE})", text, re.I)
            if found:
                self.write_day = official_day(found[1])

    def in_tab(self, name):
        return [rows for panel, rows in self.tables
                if rows and " ".join(" ".join(self.titles.get(panel, [])).split()) == name]


def grouped_rows(rows):
    groups = []
    for row in rows:
        if len(row) != len(rows[0]):
            continue
        if row[0]:
            groups.append((row[0], []))
        if groups:
            groups[-1][1].append(row)
    return groups


def dollars(text):
    match = re.fullmatch(r"\$([\d,]+(?:\.\d+)?)\s*\**", text)
    return float(match[1].replace(",", "")) if match else None


def google_rates(rows, tiered):
    rows = [row for row in rows if row[2].lower() == "global"]
    input_row = next((r for r in rows if r[1].lower() == "input (text, image, video, audio)"), None)
    output_row = next((r for r in rows if r[1].lower() == "text output (response and reasoning)"), None)
    if input_row is None or output_row is None:
        return None
    low = {"input": dollars(input_row[3]), "output": dollars(output_row[3]),
           "read": dollars(input_row[5 if tiered else 4])}
    if tiered:
        high = {"input": dollars(input_row[4]), "output": dollars(output_row[4]), "read": dollars(input_row[6])}
        if high != low:
            low.update(threshold=200_000, higher=high)
    elif dollars(input_row[5]) is not None:
        low["write"] = dollars(input_row[5])
    return low


def partner_rates(rows):
    fields = {"input": "input", "output": "output", "cache hit": "read", "5m cache write": "write"}
    values = [{fields[r[1].lower()]: dollars(r[column]) for r in rows if r[1].lower() in fields}
              for column in range(2, 6)]
    first = 0 if values[0].get("input") is not None else 2
    low, high = values[first:first + 2]
    if low != high:
        # The public table separates the long-context tier. Blank prices stay unknown.
        low.update(threshold=200_000 if first == 0 else 100_000, inclusive=True, higher=high)
    return low


def parse_public(html, source, today):
    document = OfficialTables(html)
    if source == "routes":
        options = {}
        for _, rows in document.tables:
            if not rows or rows[0][:3] not in (["Model selector option", "Planner", "Implementation"],
                                               ["Model selector option", "Planner", "Executor"]):
                continue
            for option, planner, executor, *_ in rows[1:]:
                if key := title_key(option):
                    ids = [model_id(planner), model_id(executor)]
                    if not all(ids):
                        raise ValueError("Unrecognized model in Orchestrator table")
                    options[key] = ids
        if not options:
            raise ValueError("Orchestrator table not found")
        return options

    prices = {}
    for rows in document.in_tab("Standard Model" if source == "google" else "Global"):
        header = " ".join(rows[0]).casefold()
        tiered = len(rows[0]) == 7 and "<= 200k input tokens" in header
        if source == "google":
            if rows[0][:3] != ["Model", "Type", "Region"] or not (
                tiered or len(rows[0]) == 6 and "cache read" in header and "cache write" in header
            ):
                continue
        elif len(rows[0]) != 6 or "< 200k input tokens" not in header or "< 100k input tokens" not in header:
            # Cloud currently spells these headings "=<"; accept standard "<=" as well.
            if len(rows[0]) != 6 or not all(f"<= {size}k input tokens" in header for size in (200, 100)):
                continue
        for raw_name, block in grouped_rows(rows[1:]):
            found = DATE_NOTE.search(raw_name)
            name = raw_name[:found.start()] if found else raw_name
            name = re.split(r"[*,(]", name, maxsplit=1)[0].strip()
            key = model_id(name)
            if not key or (source == "google" and not key.startswith("gemini-")):
                continue
            rates = google_rates(block, tiered) if source == "google" else partner_rates(block)
            if rates is None or rates.get("input") is None or rates.get("output") is None:
                continue
            starts, ends = today, None
            if found:
                boundary = official_day(found[2])
                if found[1].casefold() == "starting":
                    starts = max(today, boundary)
                else:
                    if boundary < today:
                        continue
                    ends = boundary
            events = prices.setdefault(key, {})
            write_day = document.write_day if "***" in " ".join(block[0]) else None
            if source == "google" and write_day and write_day > starts and "write" in rates:
                if ends is None or write_day <= ends:
                    events[write_day.isoformat()] = rates.copy()
                rates.pop("write")
            events[starts.isoformat()] = rates
            if ends:
                events.setdefault((ends + timedelta(days=1)).isoformat(), {})
    if len(prices) < 2:
        raise ValueError("Published USD price tables not found")
    return prices


def fetch_html(url):
    """Use the existing system HTTPS proxy when configured, then try direct."""
    proxies = getproxies()
    raw_proxy = proxies.get("https") or proxies.get("http")
    proxy = urlsplit(raw_proxy if "://" in raw_proxy else "http://" + raw_proxy) if raw_proxy else None
    failure = None
    for endpoint in (proxy, None) if proxy else (None,):
        location = url
        try:
            for _ in range(3):
                parts = urlsplit(location)
                if parts.scheme != "https" or parts.hostname not in {"cloud.google.com", "antigravity.google",
                                                                      "docs.cloud.google.com"}:
                    raise ValueError("Unexpected pricing source redirect")
                host = parts.hostname
                connection = HTTPSConnection(endpoint.hostname if endpoint else host,
                                             endpoint.port if endpoint else 443, timeout=8)
                if endpoint:
                    connection.set_tunnel(host, 443)
                try:
                    path = parts.path + ("?" + parts.query if parts.query else "")
                    connection.request("GET", path, headers={"Accept-Language": "en-US", "Cache-Control": "no-cache"})
                    response = connection.getresponse()
                    if response.status in (301, 302, 303, 307, 308):
                        location = urljoin(location, response.getheader("Location", ""))
                        continue
                    if response.status != 200:
                        raise ValueError(f"Official source HTTP {response.status}")
                    limit = 8_000_000
                    body = response.read(limit + 1)
                    if len(body) > limit:
                        raise ValueError("Official source response exceeds size limit")
                    return body.decode("utf-8")
                finally:
                    connection.close()
            raise ValueError("Too many official source redirects")
        except (OSError, ValueError, HTTPException) as error:
            failure = error
    raise failure


def cache_file(app_data, source):
    return app_data / "agy_usage_pricing" / (source + ".json")


def read_cache(path):
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(saved, dict) or ("data" in saved and not isinstance(saved["data"], dict)):
        return {}
    return saved


def write_cache(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))
            stream.close()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def seed_prices(source):
    prefix = "gemini-" if source == "google" else "claude-"
    return {name: {row["from"]: {k: v for k, v in row.items() if k != "from"} for row in rates}
            for name, rates in SEED["models"].items() if name.startswith(prefix)}


def current_at(versions, day):
    return versions.get(max((key for key in versions if key <= day), default=""))


def merge_prices(previous, today, current):
    result = {}
    for model in previous.keys() | current.keys():
        old = previous.get(model, {})
        updates = current.get(model, {})
        versions = {day: rates for day, rates in old.items() if day < today}
        for day, rates in sorted({today: {}, **updates}.items()):
            if (current_at(versions, day) or {}) != rates:
                versions[day] = rates
        if versions:
            result[model] = versions
    return result


def refresh_sources(app_data, *, now=None, fetch=fetch_html):
    now = now or datetime.now(TZ)
    today = now.astimezone(TZ).date()
    day = today.isoformat()

    def refresh(source):
        path = cache_file(app_data, source)
        old = read_cache(path)
        timestamp = now.timestamp()
        if old.get("checked") == day or 0 <= timestamp - old.get("attempt", 0) < 3600:
            return False
        try:
            data = parse_public(fetch(SOURCES[source][1]), source, today)
        except (OSError, ValueError, HTTPException):
            write_cache(path, {**old, "attempt": timestamp, "failed": True})
            return False
        if source == "routes":
            old_routes = old.get("data") or SEED["routes"]
            history = {key: value for key, value in old_routes.items() if key < day}
            if current_at(history, day) != data:
                history[day] = data
        else:
            history = merge_prices(old.get("data") or seed_prices(source), day, data)
        write_cache(path, {"checked": day, "attempt": timestamp, "failed": False, "data": history})
        return True

    with ThreadPoolExecutor(max_workers=len(SOURCES)) as executor:
        return any(list(executor.map(refresh, SOURCES)))


class PriceBook:
    def __init__(self, data=None):
        data = data or {}
        self.models = {}
        self.status = {}
        for source, (name, _) in SOURCES.items():
            cache = data.get(source, {})
            self.status[source] = {"name": name, "checked": cache.get("checked"),
                                   "bundled": SEED["checked"], "failed": bool(cache.get("failed"))}
            if source == "routes":
                self.routes = cache.get("data") or SEED["routes"]
            else:
                self.models.update(cache.get("data") or seed_prices(source))

    def model(self, row):
        raw = row["model"].casefold()
        label = row.get("label") or ""
        if raw in self.models:
            return raw
        direct = model_id(label.split(" (", 1)[0])
        if direct in self.models:
            return direct
        routes = current_at(self.routes, row["day"]) or {}
        route = routes.get(title_key(label)) or routes.get(SEED["route_ids"].get(raw))
        if route and "worker" not in raw:
            return route[0]
        for suffix in ("-high", "-medium", "-low", "-xhigh", "-tiered", "-a", "-n", "-p", "-trial"):
            if raw.endswith(suffix) and raw[:-len(suffix)] in self.models:
                return raw[:-len(suffix)]
        return None

    def route_phase(self, row):
        raw = row["model"].casefold()
        label = title_key(row.get("label") or "")
        routes = current_at(self.routes, row["day"]) or {}
        if "worker" not in raw and (label in routes or SEED["route_ids"].get(raw) in routes):
            return "planning"
        model = self.model(row)
        if model and ("worker" in raw or label.endswith(" (orchestrator)")):
            return "implementing"
        return ""

    def rate(self, row):
        model = self.model(row)
        rate = current_at(self.models.get(model, {}), row["day"])
        if not rate:
            return None
        if threshold := rate.get("threshold"):
            context = row["uncached"] + row["cached"] + row["written"]
            if context > threshold or context == threshold and rate.get("inclusive"):
                rate = rate["higher"]
        if any(rate.get(name) is None and row[field] for name, field in
               (("input", "uncached"), ("read", "cached"), ("write", "written"), ("output", "output"))):
            return None
        return rate


DEFAULT_BOOK = PriceBook()


@lru_cache(maxsize=16)
def cached_book(app_data, stamps):
    return PriceBook({source: read_cache(cache_file(app_data, source)) for source in SOURCES})


def load_book(app_data):
    stamps = []
    for source in SOURCES:
        try:
            stamps.append(cache_file(app_data, source).stat().st_mtime_ns)
        except OSError:
            stamps.append(0)
    return cached_book(app_data, tuple(stamps))
