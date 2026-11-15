(() => {
function createDashboard(root, load, initialConversation) {
const $ = id => root.querySelector("#" + id);
const whole = new Intl.NumberFormat("zh-CN");
const precise = new Intl.NumberFormat("zh-CN", {maximumFractionDigits: 2, minimumFractionDigits: 2});
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
let wanted = initialConversation || "";
let follow = !wanted;
let loading = false;
let pending = false;
let disposed = false;
let selectedDay = "";

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text != null) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function cards(id, items) {
  $(id).replaceChildren(...items.map(([name, value, detail]) => {
    const card = element("div", null, "metric");
    card.append(element("span", name), element("strong", value), element("small", detail));
    return card;
  }));
}

function rows(id, values, columns, empty) {
  const nodes = values.map(list => {
    const row = element("tr");
    row.append(...list.map(value => {
      const cell = element("td");
      cell.append(value);
      return cell;
    }));
    return row;
  });
  if (!nodes.length) {
    const row = element("tr");
    const cell = element("td", empty);
    cell.colSpan = columns;
    row.append(cell);
    nodes.push(row);
  }
  $(id).replaceChildren(...nodes);
}

function chart(days) {
  const frame = $("chart");
  const caption = $("chart-caption");
  if (!days.length) {
    frame.replaceChildren(element("p", "当前日期范围没有用量", "empty"));
    caption.textContent = "—";
    return;
  }
  const max = Math.max(...days.map(day => day.total), 1);
  const grid = element("div", null, "chart-grid");
  const axes = element("div", null, "axis");
  axes.append(...[max, max / 2, 0].map(v => element("span", new Intl.NumberFormat("en", {
    notation: "compact", maximumFractionDigits: 1
  }).format(v))));
  const plot = element("div", null, "plot");
  const detail = day => day.day + " · " + num(day.total) + " Token · " + cny(day.cny);
  const setDay = (bar, day) => {
    selectedDay = day.day;
    const selected = plot.querySelector('[aria-pressed="true"]');
    selected?.setAttribute("aria-pressed", "false");
    bar.setAttribute("aria-pressed", "true");
    caption.textContent = detail(day);
  };
  days.forEach(day => {
    const bar = element("button", null, "chart-bar");
    bar.type = "button";
    bar.style.setProperty("--bar-height", Math.max(0.6, day.total / max * 100) + "%");
    bar.setAttribute("aria-label", detail(day));
    bar.setAttribute("aria-pressed", "false");
    bar.title = detail(day);
    bar.addEventListener("click", () => setDay(bar, day));
    bar.addEventListener("focus", () => setDay(bar, day));
    bar.addEventListener("pointerenter", () => { caption.textContent = detail(day); });
    bar.addEventListener("pointerleave", () => {
      caption.textContent = plot.querySelector('[aria-pressed="true"]')?.title || detail(days.at(-1));
    });
    plot.append(bar);
  });
  const dates = element("div", null, "chart-dates");
  const tickCount = Math.min(days.length, 5);
  for (let i = 0; i < tickCount; i++) {
    const index = tickCount > 1 ? Math.round(i * (days.length - 1) / (tickCount - 1)) : 0;
    dates.append(element("span", days[index].day.slice(5)));
  }
  grid.append(axes, plot, dates);
  frame.replaceChildren(grid);
  const index = days.findIndex(day => day.day === selectedDay);
  if (index >= 0) setDay(plot.children[index], days[index]);
  else setDay(plot.lastElementChild, days.at(-1));
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
  $("session-hint").textContent = current
    ? (follow ? "最近活跃 · " : "已选会话 · ") + current.title
    : "没有读取到该会话的用量。";
  const session = report.session;
  const last = report.latest || {};
  cards("session-metrics", [
    ["会话累计 Token", num(session.total), num(session.calls) + " 次模型调用"],
    ["缓存命中率", pct(session.hit), "缓存读取 " + num(session.cached)],
    ["最近生成速率", speed(ratio(last.output, last.seconds)), "输出 Token ÷ 流式生成耗时"],
    ["模型窗口占用", last.context == null || !last.model_limit ? "—" : "≈" + pct(ratio(last.context, last.model_limit)),
      "已用约 " + num(last.context) + " / 模型窗口 " + num(last.model_limit) + " Token"
        + (last.limit ? " · 调用预算 " + num(last.limit) : "")],
    ["会话参考费用", cny(session.cny), usd(session.usd) + " · 已计价 " + pct(ratio(session.priced_tokens, session.total))]
  ]);
  rows("recent", report.recent.map(row => [
    stamp(row.time), row.label, num(row.uncached), num(row.cached), num(row.output), speed(ratio(row.output, row.seconds))
  ]), 6, "暂无调用记录");

  const sum = report.all;
  $("coverage").textContent = "本机桌面端 · UTC+08:00 · " + num(report.scanned) + " 个会话库"
    + (report.range[0] ? " · " + report.range.join(" 至 ") : "")
    + (report.unreadable ? " · " + report.unreadable + " 个库暂未读到" : "");
  cards("history-metrics", [
    ["累计 Token", num(sum.total), "含缓存和输出 · " + num(sum.calls) + " 次调用"],
    ["缓存命中率", pct(sum.hit), "读取 " + num(sum.cached) + " · 写入 " + num(sum.written)],
    ["输出 Token", num(sum.output), "加权速率 " + speed(sum.speed)],
    ["API 参考费用", usd(sum.usd), "已计价 " + pct(ratio(sum.priced_tokens, sum.total)) + " Token"],
    ["历史汇率折算", cny(sum.cny), "已换汇 " + num(sum.converted) + " / " + num(sum.priced) + " 次已计价调用"]
  ]);
  $("pricing").textContent = !report.fx.available
    ? "暂未获取历史汇率，人民币金额留空。"
    : "参考费用为 API 等值估算，未匹配公开价格的模型仍计入 Token 总量。";
  const latestFx = $("latest-fx");
  latestFx.hidden = !report.latest_fx;
  if (report.latest_fx) {
    latestFx.textContent = "悬停卡片参考汇率 · 1 USD = " + report.latest_fx.rate + " CNY · " + report.latest_fx.date;
  }
  chart(report.days);
  rows("daily", [...report.days].reverse().map(day => [
    day.day, num(day.total), pct(day.hit), usd(day.usd), cny(day.cny),
    day.fx_rate == null ? "—" : String(day.fx_rate) + " · " + day.fx_date
  ]), 6, "当前筛选没有数据");
  rows("models", report.models.map(model => [
    model.model, num(model.calls), num(model.total), num(model.priced_tokens), usd(model.usd), cny(model.cny)
  ]), 6, "当前筛选没有数据");
  const quota = report.quota;
  $("quota-status").textContent = quota.available
    ? "桌面端额度快照 · " + stamp(quota.read_at)
    : "桌面端额度暂不可用，请确认 Antigravity 正在运行并已登录。";
  rows("quota", quota.groups.flatMap(group => group.buckets.map(item => {
    const period = item.window === "5h" ? "5 小时" : "每周";
    const name = element("span", group.name || "模型额度");
    name.title = group.models.join("、");
    if (item.remaining == null) return [name, period, "—", stamp(item.reset)];
    const remaining = element("span", null, "quota-value");
    const meter = element("progress");
    meter.max = 1;
    meter.value = item.remaining;
    meter.setAttribute("aria-label", group.name + " " + period + "剩余额度");
    remaining.append(meter, element("span", pct(item.remaining)));
    return [name, period, remaining, stamp(item.reset)];
  })), 4, "暂无可用额度数据");
}

async function update(showStatus = true) {
  if (loading) {
    pending ||= showStatus;
    return;
  }
  loading = true;
  if (showStatus) $("state").textContent = "正在读取…";
  const filters = {start: $("start").value, end: $("end").value, conversation: follow ? "" : wanted, only: $("only").checked};
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
$("filters").addEventListener("submit", event => { event.preventDefault(); update(); });
$("refresh").addEventListener("click", () => update());
$("only").addEventListener("change", () => update());
root.querySelectorAll("[data-days]").forEach(button => button.addEventListener("click", () => {
  root.querySelectorAll("[data-days]").forEach(control => {
    control.setAttribute("aria-pressed", String(control === button));
  });
  const days = Number(button.dataset.days);
    selectedDay = "";
  const today = localDate.format(new Date());
  const first = new Date(today + "T12:00:00+08:00");
  first.setTime(first.getTime() - (days - 1) * 86400000);
  $("start").value = days ? localDate.format(first) : "";
  $("end").value = days ? today : "";
  update();
}));
const tabs = [...root.querySelectorAll("[role=tab]")];
function selectTab(tab) {
  for (const candidate of tabs) {
    const selected = candidate === tab;
    candidate.setAttribute("aria-selected", String(selected));
    candidate.tabIndex = selected ? 0 : -1;
    $(candidate.getAttribute("aria-controls")).hidden = !selected;
  }
}
tabs.forEach(tab => {
  tab.addEventListener("click", () => selectTab(tab));
  tab.addEventListener("keydown", event => {
    const index = tabs.indexOf(tab);
    const offsets = {ArrowLeft: -1, ArrowRight: 1};
    if (!Object.hasOwn(offsets, event.key)) return;
    event.preventDefault();
    const next = tabs[(index + offsets[event.key] + tabs.length) % tabs.length];
    selectTab(next);
    next.focus();
  });
});
for (const input of [$("start"), $("end")]) {
  input.addEventListener("change", () => root.querySelectorAll("[data-days]").forEach(
    control => control.setAttribute("aria-pressed", "false")));
}
update();
const timer = setInterval(() => { if (!document.hidden) update(false); }, 15000);
return {dispose() { disposed = true; clearInterval(timer); }};
}

if (window.__agyUsageStatus?.version === 11) {
  window.__agyUsageStatus.createDashboard = createDashboard;
} else {
  createDashboard(document, async filters => {
    const query = new URLSearchParams({...filters, only: filters.only ? "1" : "0", fx: "1"});
    const response = await fetch("data?" + query);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error);
    return data;
  }, new URLSearchParams(location.search).get("conversation"));
}
})();
