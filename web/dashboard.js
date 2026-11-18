(() => {
const VERSION = 20;
function createDashboard(root, load, initialConversation) {
const $ = id => root.querySelector("#" + id);
const NS = "http://www.w3.org/2000/svg";
const whole = new Intl.NumberFormat("zh-CN");
const precise = new Intl.NumberFormat("zh-CN", {maximumFractionDigits: 2, minimumFractionDigits: 2});
const compact = new Intl.NumberFormat("en", {notation: "compact", maximumFractionDigits: 1});
const localDate = new Intl.DateTimeFormat("sv-SE", {timeZone: "Asia/Shanghai"});
const localTime = new Intl.DateTimeFormat("sv-SE", {
  timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit",
  hour: "2-digit", minute: "2-digit", second: "2-digit"
});
const num = v => v == null ? "—" : whole.format(v);
const money = (v, mark) => v == null ? "—" : mark + precise.format(v);
const pct = v => v == null ? "—" : precise.format(v * 100) + "%";
const speed = v => v == null ? "—" : precise.format(v) + " tok/s";
const stamp = v => v && !Number.isNaN(Date.parse(v)) ? localTime.format(new Date(v)) : "—";
const usd = v => money(v, "$");
const cny = v => money(v, "¥");
const ratio = (a, b) => a != null && b ? a / b : null;
const short = v => v == null ? "—" : v >= 1e8 ? precise.format(v / 1e8) + " 亿" : v >= 1e4 ? precise.format(v / 1e4) + " 万" : num(v);
let wanted = initialConversation || "";
let follow = !wanted;
let loading = false;
let pending = false;
let disposed = false;
let metric = "usd";
let view = "daily";
let lastReport = null;
let windowHours = 0;
let windowDay = false;

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text != null) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function shape(tag, attrs) {
  const node = document.createElementNS(NS, tag);
  for (const key in attrs) node.setAttribute(key, attrs[key]);
  return node;
}

function tiles(items) {
  $("tiles").replaceChildren(...items.map(([name, value, note]) => {
    const cell = element("div", null, "metric");
    cell.append(element("p", name, "label"), element("p", value, "value"), element("p", note, "note"));
    return cell;
  }));
}

function table(headers, rows, empty) {
  const head = element("thead");
  const headRow = element("tr");
  headRow.append(...headers.map(name => element("th", name)));
  head.append(headRow);
  const body = element("tbody");
  if (!rows.length) {
    const row = element("tr");
    const cell = element("td", empty, "empty");
    cell.colSpan = headers.length;
    row.append(cell);
    body.append(row);
  } else {
    for (const values of rows) {
      const row = element("tr");
      row.append(...values.map(value => {
        const cell = element("td");
        cell.append(value);
        return cell;
      }));
      body.append(row);
    }
  }
  const node = element("table");
  node.append(head, body);
  return node;
}

function pick(day) {
  return metric === "usd" ? (day.usd || 0) : metric === "calls" ? day.calls : day.total;
}

function label(value) {
  return metric === "usd" ? usd(value) : metric === "calls" ? num(value) + " 次" : num(value) + " Token";
}

function detail(day) {
  return day.day.slice(5) + " · " + label(pick(day)) + " · " + num(day.calls) + " 次 · " + cny(day.cny);
}

function axisText(value) {
  if (metric === "usd") return "$" + (value >= 1000 ? precise.format(value / 1000) + "k" : compact.format(value));
  if (metric === "calls") return value >= 1000 ? precise.format(value / 1000) + "k" : whole.format(Math.round(value));
  return value >= 1e8 ? precise.format(value / 1e8) + "亿" : value >= 1e4 ? Math.round(value / 1e4) + "万" : whole.format(Math.round(value));
}

function seriesOf(report) {
  return report.granularity === "hour" && report.hourly ? report.hourly : report.days;
}

function chart(days) {
  const svg = $("chart-svg");
  const caption = $("chart-caption");
  const axis = $("yaxis");
  const ticks = $("xaxis");
  if (!days.length) {
    svg.replaceChildren();
    axis.replaceChildren();
    ticks.replaceChildren();
    caption.textContent = "当前日期范围没有用量";
    return;
  }
  const W = 1000;
  const H = 240;
  const top = 10;
  const values = days.map(pick);
  const max = Math.max(...values, 1);
  const slot = W / days.length;
  const width = Math.max(3, Math.min(56, slot * 0.62));
  const title = $("chart-title");
  if (title) {
    title.textContent = (metric === "usd" ? "费用" : metric === "calls" ? "请求" : "Token")
      + " · 按" + (days[0].hour ? "小时" : "天");
  }
  const gridGroup = shape("g", {class: "grid"});
  for (let step = 0; step <= 3; step++) {
    const gy = top + (H - top) * step / 3;
    gridGroup.append(shape("line", {x1: 0, x2: W, y1: gy, y2: gy, "vector-effect": "non-scaling-stroke"}));
  }
  const group = shape("g", {class: "bars"});
  const bars = days.map((day, index) => {
    const share = Math.max(0, Math.min(1, pick(day) / max));
    const height = share > 0 ? Math.max(1.5, (H - top) * share) : 0;
    const bar = shape("rect", {
      x: (slot * index + (slot - width) / 2).toFixed(1),
      y: (H - height).toFixed(1),
      width: width.toFixed(1),
      height: height.toFixed(1),
      rx: 2,
      class: "bar"
    });
    group.append(bar);
    return bar;
  });
  svg.replaceChildren(gridGroup, group);
  axis.replaceChildren(...[0, 1, 2, 3].map(step => element("span", axisText(max * (3 - step) / 3))));
  const step = Math.max(1, Math.ceil(days.length / 8));
  const last = days.length - 1;
  const marks = [];
  for (let index = 0; index <= last; index += step) marks.push(index);
  if (marks[marks.length - 1] !== last) {
    if (last - marks[marks.length - 1] < Math.max(1, step / 2)) marks.pop();
    marks.push(last);
  }
  const marked = new Set(marks);
  ticks.replaceChildren(...days.map((day, index) =>
    element("span", marked.has(index) ? (day.hour || day.day.slice(5)) : "")));
  let active = -1;
  const mark = index => {
    if (active === index) return;
    if (active >= 0 && bars[active]) bars[active].classList.remove("hot");
    active = index;
    if (index >= 0 && bars[index]) bars[index].classList.add("hot");
  };
  const plot = $("plot");
  plot.onpointermove = event => {
    const box = event.currentTarget.getBoundingClientRect();
    if (!box.width) return;
    const position = Math.min(0.9999, Math.max(0, (event.clientX - box.left) / box.width));
    const index = Math.floor(position * days.length);
    mark(index);
    caption.textContent = detail(days[index]);
  };
  plot.onpointerleave = () => {
    mark(-1);
    caption.textContent = detail(days[days.length - 1]);
  };
  mark(-1);
  caption.textContent = detail(days[days.length - 1]);
}

function shares(models) {
  const rows = [...models].sort((a, b) => (b.usd || 0) - (a.usd || 0) || b.total - a.total).slice(0, 8);
  const note = $("share-note");
  if (!rows.length) {
    $("shares").replaceChildren(element("p", "当前筛选没有模型记录", "empty"));
    note.textContent = "—";
    return;
  }
  const max = Math.max(...rows.map(item => item.usd || 0), 0) || 1;
  const top = rows.filter(item => item.usd).length;
  note.textContent = "按参考费用排序 · 前 " + rows.length + " 个模型"
    + (top < rows.length ? " · 未计价模型仅计 Token" : "");
  $("shares").replaceChildren(...rows.map(item => {
    const row = element("div", null, "share");
    const name = element("span", item.model, "share-name");
    name.title = item.model + " · " + num(item.calls) + " 次 · " + num(item.total) + " Token";
    const track = element("span", null, "share-track");
    const bar = shape("svg", {viewBox: "0 0 100 8", preserveAspectRatio: "none", "aria-hidden": "true"});
    const value = item.usd || 0;
    const tone = item.usd ? "share-fill" : "share-fill muted";
    bar.append(shape("rect", {x: 0, y: 0, height: 8, class: "share-track-bg"}),
      shape("rect", {x: 0, y: 0, height: 8, rx: 2, class: tone,
        width: (value ? Math.max(1.5, value / max * 100) : 1.5).toFixed(1)}));
    track.append(bar);
    const share = value ? value / max : 0;
    const amount = element("span", item.usd ? usd(item.usd) : num(item.total) + " tok", "share-value");
    amount.title = (item.usd ? pct(share) + " · " : "未计价 · ") + num(item.calls) + " 次调用";
    row.append(name, track, amount);
    return row;
  }));
}

function renderTable(report, rows) {
  rows = rows || seriesOf(report);
  const area = $("table-area");
  const note = $("view-note");
  if (view === "models") {
    note.textContent = "未计价 = 公开价格表里没有匹配项，只计 Token。";
    area.replaceChildren(table(
      ["模型", "调用", "Token", "已计价 Token", "参考费用", "平均每次", "未计价"],
      report.models.map(model => [
        model.model, num(model.calls), num(model.total), num(model.priced_tokens), usd(model.usd),
        model.priced ? usd(model.usd / model.priced) : "—",
        model.unpriced ? element("span", num(model.unpriced), "tag warn") : "0"
      ]), "当前筛选没有数据"));
    return;
  }
  if (view === "recent") {
    note.textContent = "按时间倒序的最近模型调用。";
    area.replaceChildren(table(
      ["时间", "模型", "未缓存输入", "缓存读取", "输出", "生成速率"],
      report.recent.map(row => [stamp(row.time), row.label, num(row.uncached), num(row.cached),
        num(row.output), speed(ratio(row.output, row.seconds))]), "暂无调用记录"));
    return;
  }
  if (view === "sessions") {
    note.textContent = "压缩 = 该会话发生过的上下文压缩（checkpoint）次数。";
    area.replaceChildren(table(
      ["会话", "调用", "Token", "压缩", "参考费用", "人民币"],
      report.sessions.map(item => [
        item.title, num(item.calls), num(item.total), num(item.compactions ?? 0), usd(item.usd), cny(item.cny)
      ]), "还没有读到会话记录"));
    return;
  }
  if (view === "quota") {
    const quota = report.quota || { available: false, groups: [], read_at: null };
    note.textContent = quota.available ? "桌面端额度快照 · " + stamp(quota.read_at)
      : "桌面端额度暂不可用，请确认 Antigravity 正在运行并已登录。";
    area.replaceChildren(table(
      ["额度组", "周期", "剩余额度", "重置时间（UTC+08:00）"],
      (quota.groups || []).flatMap(group => (group.buckets || []).map(item => {
        const period = item.window === "5h" ? "5 小时" : "每周";
        const name = element("span", group.name || "模型额度");
        name.title = group.models.join("、");
        if (item.remaining == null) return [name, period, "—", stamp(item.reset)];
        const cell = element("span", null, "quota-cell");
        const meter = element("progress");
        meter.max = 1;
        meter.value = item.remaining;
        meter.setAttribute("aria-label", (group.name || "模型额度") + " " + period + "剩余额度");
        cell.append(meter, element("span", pct(item.remaining)));
        return [name, period, cell, stamp(item.reset)];
      })), "暂无可用额度数据"));
    return;
  }
  note.textContent = windowDay
    ? "当天按小时聚合，最新一小时在最上方。"
    : windowHours
    ? "按小时聚合，最新一小时在最上方。"
    : "汇率按用量发生日期取历史值，接口不可用时用兜底汇率。";
  area.replaceChildren(table(
    [windowHours ? "时刻" : "日期", "Token", "命中率", "参考费用", "人民币", "汇率"],
    [...rows].reverse().map(day => [
      day.day, num(day.total), pct(day.hit), usd(day.usd), cny(day.cny),
      day.fx_rate == null ? "—" : String(day.fx_rate)
    ]), "当前筛选没有数据"));
}

function draw(report) {
  const selection = $("session");
  const current = report.sessions.find(item => item.id === report.selected);
  const currentKey = follow ? "" : wanted;
  const options = [new Option("最近活跃会话", ""), ...report.sessions.map(item => new Option(item.title, item.id))];
  if (currentKey && !options.some(option => option.value === currentKey)) {
    options.push(new Option("该会话暂无用量记录", currentKey));
  }
  selection.replaceChildren(...options);
  selection.value = currentKey;
  lastReport = report;
  const sum = report.all;
  $("range-note").textContent = (report.range[0] ? report.range.join(" 至 ") : "全部记录")
    + (current ? " · " + (follow ? "最近活跃 · " : "已选 · ") + current.title : "");
  const avg = sum.calls ? sum.usd / sum.calls : null;
  tiles([
    ["API 参考费用", usd(sum.usd),
      cny(sum.cny) + " · 已计价 " + pct(ratio(sum.priced_tokens, sum.total))
        + (sum.unpriced ? " · " + num(sum.unpriced) + " 次未计入" : ""), true],
    ["请求数", num(sum.calls), "平均每次 " + usd(avg) + " · " + num(sum.calls) + " 次模型调用"],
    ["Token 总量", num(sum.total), "读取 " + short(sum.cached) + " · 写入 " + short(sum.written) + " · 输出 " + short(sum.output)],
    ["缓存命中率", pct(sum.hit), "缓存节省 " + usd(sum.savings) + " · 按读取价与输入价差额"]
  ]);
  $("coverage").textContent = "本机桌面端 · UTC+08:00 · " + num(report.scanned) + " 个会话库"
    + (report.range[0] ? " · " + report.range.join(" 至 ") : "")
    + (report.unreadable ? " · " + num(report.unreadable) + " 个库暂未读到" : "");
  $("pricing").textContent = (!report.fx.available
    ? "暂未获取历史汇率，人民币金额留空。"
    : (report.fx.fallback ? "汇率接口不可用，已按离线兜底汇率折算；" : "")
      + "参考费用为 API 等值估算，未匹配公开价格的模型仍计入 Token 总量。");
  const latestFx = $("latest-fx");
  latestFx.hidden = !report.latest_fx;
  if (report.latest_fx) {
    latestFx.textContent = "悬停卡片参考汇率 · 1 USD = " + report.latest_fx.rate + " CNY · " + report.latest_fx.date;
  }
  const rows = seriesOf(report);
  $("count-daily").textContent = num(rows.length);
  $("count-models").textContent = num(report.models.length);
  $("count-recent").textContent = num(report.recent.length);
  $("count-sessions").textContent = num(report.sessions.length);
  $("count-quota").textContent = report.quota && report.quota.available
    ? num((report.quota.groups || []).length) : "";
  chart(rows);
  shares(report.models);
  renderTable(report, rows);
}

async function update(showStatus = true) {
  if (loading) {
    pending ||= showStatus;
    return;
  }
  loading = true;
  if (showStatus) $("state").textContent = "正在读取…";
  const filters = {start: $("start").value, end: $("end").value, conversation: follow ? "" : wanted,
    only: $("only").checked, hours: windowHours, day: windowDay};
  try {
    const result = await load(filters);
    if (disposed) return;
    draw(result);
    $("state").textContent = "更新于 " + stamp(new Date().toISOString());
  } catch (error) {
    if (!disposed) $("state").textContent = "读取失败 · " + error.message;
  } finally {
    loading = false;
    if (pending && !disposed) {
      pending = false;
      update();
    }
  }
}

$("session").addEventListener("change", () => {
  wanted = $("session").value;
  follow = !wanted;
  update();
});
$("filter").addEventListener("click", () => update());
$("refresh").addEventListener("click", () => update());
$("only").addEventListener("change", () => update());
$("fold").addEventListener("click", () => {
  const button = $("fold");
  const open = button.getAttribute("aria-expanded") === "true";
  button.setAttribute("aria-expanded", String(!open));
  button.textContent = open ? "⌃" : "⌄";
  $("chart-area").classList.toggle("collapsed", open);
});
root.querySelectorAll("[data-metric]").forEach(button => button.addEventListener("click", () => {
  metric = button.dataset.metric;
  root.querySelectorAll("[data-metric]").forEach(control =>
    control.setAttribute("aria-pressed", String(control === button)));
  if (lastReport) chart(seriesOf(lastReport));
}));
function setView(next) {
  view = next;
  root.querySelectorAll("[data-view]").forEach(control =>
    control.setAttribute("aria-pressed", String(control.dataset.view === view)));
}
root.querySelectorAll("[data-view]").forEach(button => button.addEventListener("click", () => {
  setView(button.dataset.view);
  if (lastReport) renderTable(lastReport, seriesOf(lastReport));
}));
root.querySelectorAll("[data-days], [data-hours]").forEach(button => button.addEventListener("click", () => {
  root.querySelectorAll("[data-days], [data-hours]").forEach(control =>
    control.setAttribute("aria-pressed", String(control === button)));
  const hours = Number(button.dataset.hours || 0);
  const days = Number(button.dataset.days || 0);
  const today = localDate.format(new Date());
  const first = new Date(today + "T12:00:00+08:00");
  first.setTime(first.getTime() - (days - 1) * 86400000);
  if (view !== "daily") setView("daily");
  windowDay = !hours && days === 1;
  windowHours = hours || (windowDay ? 24 : 0);
  $("start").value = !windowHours && days ? localDate.format(first) : "";
  $("end").value = !windowHours && days ? today : "";
  update();
}));
for (const input of [$("start"), $("end")]) {
  input.addEventListener("change", () => {
    windowHours = 0;
    windowDay = false;
    setView("daily");
    root.querySelectorAll("[data-days], [data-hours]").forEach(
      control => control.setAttribute("aria-pressed", "false"));
  });
}
update();
const timer = setInterval(() => { if (!document.hidden) update(false); }, 15000);
return {dispose() { disposed = true; clearInterval(timer); }};
}

if (window.__agyUsageStatus?.version === VERSION) {
  window.__agyUsageStatus.createDashboard = createDashboard;
} else {
  createDashboard(document, async filters => {
    const query = new URLSearchParams({...filters, only: filters.only ? "1" : "0",
      day: filters.day ? "1" : "0", fx: "1"});
    const response = await fetch("data?" + query);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error);
    return data;
  }, new URLSearchParams(location.search).get("conversation"));
}
})();
