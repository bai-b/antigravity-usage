(() => {
  const key = "__agyUsageStatus";
  if (window[key]?.version === 21) return;
  window[key]?.remove();
  const state = window[key] = { version: 21, request: null };
  const dashboardCache = new Map();
  const bars = new Map();
  const launchers = new Map();
  const selector = '[data-testid="agent-input-box"]';
  const attr = "data-agy-usage-status";
  const launcherAttr = "data-agy-usage-launcher";
  const number = new Intl.NumberFormat("zh-CN");
  const money = new Intl.NumberFormat("zh-CN", {minimumFractionDigits: 2, maximumFractionDigits: 2});
  const value = (v, suffix = "") => v == null ? "—" : number.format(v) + suffix;
  const compact = new Intl.NumberFormat("en", {notation: "compact", maximumFractionDigits: 1});
  const capacity = tokens => tokens >= 1024 ** 2 && tokens % (1024 ** 2) === 0 ? tokens / (1024 ** 2) + "M"
    : tokens >= 1024 && tokens % 1024 === 0 ? tokens / 1024 + "K" : compact.format(tokens);
  const time = new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai", month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit"
  });

  function inputs() {
    return [...document.querySelectorAll(selector)].filter(element => element.getClientRects().length);
  }

  function conversation(input) {
    return input.closest("[data-cascade-id]")?.getAttribute("data-cascade-id") || "";
  }

  function modelTrigger(input) {
    return input.querySelector('[data-testid="model-selector-trigger"]');
  }

  function model(input) {
    const trigger = modelTrigger(input);
    const label = trigger?.firstElementChild?.textContent.trim() || "";
    const mode = trigger?.dataset.contextWindow;
    const suffix = mode ? " · " + mode : "";
    const name = mode && label.endsWith(suffix) ? label.slice(0, -suffix.length) : label;
    return /^(No Model Selected|未选择模型|未选模型)$/i.test(name) ? "" : name;
  }

  function style(element, properties) {
    Object.assign(element.style, properties);
  }

  const styles = document.createElement("style");
  styles.textContent = `
    [data-agy-usage-slot] {flex:1 1 0px;min-width:0;height:24px;align-self:center;container:agy-usage-space / inline-size}
    [data-agy-usage-status] {display:flex;justify-content:space-evenly;align-items:center;height:100%;margin-inline:4px;overflow:clip;visibility:hidden;--agy-usage-density:hidden}
    [data-agy-usage-status] button {font:inherit;color:inherit;white-space:nowrap;border:0;background:none;padding:2px 4px;border-radius:5px;cursor:pointer}
    [data-agy-usage-status] button:hover,[data-agy-usage-status] button:focus-visible {background:var(--secondary,#8882)}
    [data-agy-usage-status] button:focus-visible {outline:1px solid var(--foreground,#888);outline-offset:-1px}
    [data-agy-usage-status] .agy-usage-pill {position:relative;display:inline-flex;align-items:center;gap:4px;flex:none}
    [data-agy-usage-status] .agy-usage-icon {display:grid;place-items:center;flex:none}
    [data-agy-usage-status] .agy-usage-reading {position:absolute;left:0;visibility:hidden;width:max-content;pointer-events:none}
    [data-agy-usage-launcher] {display:inline-flex;align-items:center;gap:6px;flex:none;height:28px;padding:0 8px;border:0;border-radius:6px;background:none;color:var(--foreground,CanvasText);cursor:pointer;font-size:12px;white-space:nowrap;-webkit-app-region:no-drag}
    [data-agy-usage-launcher]:hover,[data-agy-usage-launcher]:focus-visible {background:var(--secondary,#8882)}
    [data-agy-usage-launcher]:focus-visible {outline:1px solid var(--foreground,CanvasText);outline-offset:-1px}
    .agy-usage-launcher-icon {height:12px;display:flex;align-items:end;gap:2px;color:var(--muted-foreground,#777)}
    .agy-usage-launcher-icon i {width:2px;border-radius:1px;background:currentColor}
    .agy-usage-launcher-icon i:nth-child(1) {height:5px}
    .agy-usage-launcher-icon i:nth-child(2) {height:8px}
    .agy-usage-launcher-icon i:nth-child(3) {height:11px}
    [data-agy-usage-popup] {position:fixed;inset:auto;margin:0;padding:14px 16px;width:300px;max-width:calc(100vw - 24px);border:1px solid var(--border,#8884);border-radius:12px;background:var(--background,Canvas);color:var(--foreground,CanvasText);box-shadow:0 8px 28px #0002;font:12px/1.5 Inter,"Microsoft YaHei UI",system-ui,sans-serif;font-variant-numeric:tabular-nums;z-index:100}
    [data-agy-usage-popup] header {display:flex;justify-content:space-between;align-items:center;gap:12px;padding-bottom:10px;margin-bottom:6px;border-bottom:1px solid var(--border,#8883);font-weight:500}
    [data-agy-usage-popup] dl {display:grid;grid-template-columns:minmax(0,1fr) auto;gap:9px 16px;margin:10px 0 0}
    [data-agy-usage-popup] dt {color:var(--muted-foreground,#888);overflow-wrap:anywhere}
    [data-agy-usage-popup] dd {margin:0;text-align:right;max-width:170px;overflow-wrap:anywhere}
    [data-agy-usage-popup] progress {display:block;width:100%;height:5px;margin:10px 0;accent-color:light-dark(#3c63b4,#9eb7f3)}
    [data-agy-usage-popup] small {display:block;color:var(--muted-foreground,#888);margin-top:10px;font:inherit;font-size:11px}
    [data-agy-usage-popup] .usage-dot {width:7px;height:7px;display:inline-block;border-radius:2px;background:var(--primary,#6489bf);margin-right:7px}
    [data-agy-usage-preview] {width:292px;padding:12px;box-shadow:0 8px 22px #0002}
    [data-agy-usage-preview] header {padding:0 0 8px;margin:0 0 8px;font-size:13px}
    [data-agy-usage-preview] dl {margin:0;gap:8px 12px;align-items:center}
    [data-agy-usage-preview] dd {display:flex;justify-content:flex-end;gap:8px;flex-wrap:wrap;font-weight:500}
    [data-agy-usage-preview] dd span {white-space:nowrap}
  `;
  document.head.append(styles);
  const decimal = v => v == null ? "暂无数据" : value(Math.round(v * 10) / 10);
  const duration = seconds => seconds == null ? "暂无数据" : seconds < 60
    ? decimal(seconds) + " 秒" : Math.floor(seconds / 60) + " 分 " + Math.round(seconds % 60) + " 秒";

  function makeBar() {
    const slot = document.createElement("div");
    slot.setAttribute("data-agy-usage-slot", "");
    slot.id = "agy-usage-space-" + crypto.randomUUID();
    const bar = document.createElement("div");
    bar.setAttribute(attr, "");
    bar.setAttribute("aria-label", "Antigravity 会话用量");
    style(bar, {fontSize:"11px",lineHeight:"16px",fontVariantNumeric:"tabular-nums",
      color:"var(--muted-foreground,#71717a)"});
    slot.append(bar);
    const popup = document.createElement("div");
    popup.setAttribute("data-agy-usage-popup","");
    popup.setAttribute("popover","manual");
    popup.id = "agy-usage-popup-" + crypto.randomUUID();
    document.body.append(popup);
    let active;
    let pinned = false;
    let closeTimer;
    let frame = 0;
    let painted = "";
    const hide = () => {
      clearTimeout(closeTimer);
      cancelAnimationFrame(frame);
      frame = 0;
      if (popup.matches(":popover-open")) popup.hidePopover();
      active?.setAttribute("aria-expanded","false");
      active = undefined;
      pinned = false;
      painted = "";
    };
    const closeSoon = () => {
      if (!pinned) closeTimer = setTimeout(hide,140);
    };
    const position = () => {
      if (!active) return;
      const box=active.getBoundingClientRect();
      if (!box.width || !box.height || (bar.contains(active) && getComputedStyle(bar).visibility === "hidden")) { hide();return; }
      const bounds=popup.getBoundingClientRect();
      const left=Math.max(12,Math.min(box.left,innerWidth-bounds.width-12))+"px";
      const above=box.top-bounds.height-8;
      const top=(above>=12 ? above : Math.max(12,Math.min(innerHeight-bounds.height-12,box.bottom+8)))+"px";
      if (popup.style.left !== left) popup.style.left=left;
      if (popup.style.top !== top) popup.style.top=top;
    };
    const follow = () => {
      frame = 0;
      position();
      if (active) frame = requestAnimationFrame(follow);
    };
    const paint = () => {
      if (!active) return;
      const content=active.detail;
      if (!content) return;
      const reading = JSON.stringify(content);
      if (reading === painted) return;
      painted = reading;
      const header=document.createElement("header");
      const title=document.createElement("span");title.textContent=content.title;
      const value=document.createElement("span");value.textContent=content.value;
      header.append(title,value);
      const list=document.createElement("dl");
      content.rows.forEach(([name,reading])=>{
        const dt=document.createElement("dt");dt.textContent=name;
        const dd=document.createElement("dd");dd.textContent=reading;
        list.append(dt,dd);
      });
      popup.replaceChildren(header);
      if (content.progress != null) {
        const meter=document.createElement("progress");meter.max=100;
        meter.value=Math.max(0,Math.min(100,content.progress));
        meter.setAttribute("aria-label","上下文已用");
        popup.append(meter);
      }
      popup.append(list);
      if(content.note){const note=document.createElement("small");note.textContent=content.note;popup.append(note);}
      position();
    };
    function show(button) {
      clearTimeout(closeTimer);
      if (pinned && active!==button) return;
      active?.setAttribute("aria-expanded","false");
      active=button;
      button.setAttribute("aria-expanded","true");
      paint();
      if (!popup.matches(":popover-open")) popup.showPopover();
      position();
      if (active && !frame) frame = requestAnimationFrame(follow);
    }
    const pills=["会话统计","Token 用量","上下文","额度"].map((name,index)=>{
      const button=document.createElement("button");
      button.type="button";button.className="agy-usage-pill";
      button.setAttribute("aria-label",name);
      button.setAttribute("aria-controls",popup.id);
      button.setAttribute("aria-expanded","false");
      const icon=document.createElement("span");
      icon.className="agy-usage-icon";
      icon.setAttribute("aria-hidden","true");
      style(icon,{fontSize:"13px",lineHeight:"14px"});
      if (index < 2) {
        const svg=document.createElementNS("http://www.w3.org/2000/svg","svg");
        for (const [key,value] of Object.entries({viewBox:"0 0 24 24",width:"13",height:"13",
          fill:"none",stroke:"currentColor","stroke-width":"1.6","stroke-linecap":"round","stroke-linejoin":"round"})) svg.setAttribute(key,value);
        const path=document.createElementNS(svg.namespaceURI,"path");
        path.setAttribute("d",index===0 ? "M4 17a9 9 0 1 1 16 0M12 13l4-5M12 17h.01"
          : "M20 5c0 2-4 3-8 3S4 7 4 5s4-3 8-3 8 1 8 3ZM4 5v14c0 2 4 3 8 3s8-1 8-3V5M4 12c0 2 4 3 8 3s8-1 8-3");
        svg.append(path);icon.append(svg);
      }
      const reading=name=>{
        const span=document.createElement("span");
        span.className="agy-usage-reading agy-usage-"+name;
        span.textContent="—";
        return span;
      };
      const text=reading("full"), short=reading("short"), mini=reading("mini");
      if(index===3) button.append(text);
      else {
        for (const span of [text, short, mini]) span.setAttribute("aria-hidden", "true");
        button.append(icon,text,short,mini);
      }
      button.addEventListener("pointerenter",()=>show(button));
      button.addEventListener("pointerleave",closeSoon);
      button.addEventListener("focus",()=>show(button));
      button.addEventListener("blur",closeSoon);
      button.addEventListener("click",event=>{
        event.stopPropagation();
        if(pinned&&active===button) hide();
        else {pinned=false;show(button);pinned=true;}
      });
      if(index<3) bar.append(button);
      return {button,text,short,mini,icon,name};
    });
    const ring=pills[2].icon;
    style(ring,{width:"12px",height:"12px",flexShrink:"0",borderRadius:"50%",position:"relative",
      background:"var(--border,#8884)"});
    const hole=document.createElement("span");
    style(hole,{position:"absolute",inset:"2px",borderRadius:"50%",background:"var(--background,Canvas)"});
    ring.append(hole);
    popup.addEventListener("pointerenter",()=>clearTimeout(closeTimer));
    popup.addEventListener("pointerleave",closeSoon);
    const escape=e=>{if(e.key==="Escape"&&active){hide();e.stopPropagation();}};
    const outside=e=>{if(!bar.contains(e.target)&&!popup.contains(e.target)&&!pills.some(p=>p.button.contains(e.target)))hide();};
    document.addEventListener("pointerdown",outside,true);
    document.addEventListener("keydown",escape,true);
    const summaries = pills.slice(0, 3);
    const responsiveRules = document.createElement("style");
    document.head.append(responsiveRules);
    function layout(readingsChanged = false) {
      if (!slot.isConnected || (!readingsChanged && responsiveRules.textContent)) return;
      const widths = {full:20, short:20, mini:20, icons:20}; // 8px outer margin plus space between buttons.
      for (const {button, icon, text, short, mini} of summaries) {
        const css = getComputedStyle(button);
        const padding = parseFloat(css.paddingLeft) + parseFloat(css.paddingRight);
        const gap = parseFloat(css.columnGap) || 0;
        const base = icon.getBoundingClientRect().width + padding;
        const withLabel = base + gap;
        widths.full += withLabel + text.getBoundingClientRect().width;
        widths.short += withLabel + short.getBoundingClientRect().width;
        widths.mini += withLabel + mini.getBoundingClientRect().width;
        widths.icons += base;
      }
      const selector = `#${slot.id} > [${attr}]`;
      const icons = Math.ceil(widths.icons);
      const mini = Math.ceil(Math.max(widths.mini, icons + 1));
      const short = Math.ceil(Math.max(widths.short, mini + 1));
      const full = Math.ceil(Math.max(widths.full, widths.short, short + 1));
      // Let the browser apply the measured breakpoints in the same layout pass as resizing.
      responsiveRules.textContent = `
        @container agy-usage-space (width >= ${icons}px) {
          ${selector} { visibility:visible; --agy-usage-density:icons; justify-content:flex-end; gap:6px }
        }
        @container agy-usage-space (width >= ${mini}px) {
          ${selector} { --agy-usage-density:mini; justify-content:space-evenly; gap:0 }
          ${selector} .agy-usage-mini { position:static; visibility:visible }
        }
        @container agy-usage-space (width >= ${short}px) {
          ${selector} { --agy-usage-density:short }
          ${selector} .agy-usage-mini { position:absolute; visibility:hidden }
          ${selector} .agy-usage-short { position:static; visibility:visible }
        }
        @container agy-usage-space (width >= ${full}px) {
          ${selector} { --agy-usage-density:full }
          ${selector} .agy-usage-short { position:absolute; visibility:hidden }
          ${selector} .agy-usage-full { position:static; visibility:visible }
        }`;
    }
    slot.usage={pills,ring,paint,layout,destroy(){responsiveRules.remove();hide();popup.remove();pills[3].button.remove();
      document.removeEventListener("pointerdown",outside,true);document.removeEventListener("keydown",escape,true);}};
    return slot;
  }

  function conversationFor(toolbar) {
    const bounds = toolbar.getBoundingClientRect();
    for (let node = toolbar.parentElement; node; node = node.parentElement) {
      const pane = [...node.querySelectorAll('[data-testid="conversation-view"][data-cascade-id]')].find(view => {
        const box = view.getBoundingClientRect();
        const anchor=bounds.left+Math.min(bounds.width/2,16);
        return box.height && anchor > box.left && anchor <= box.right + 2
          && box.top >= bounds.bottom && box.top - bounds.bottom <= 20;
      });
      if (pane) return pane.dataset.cascadeId;
    }
    return "";
  }

  function makeLauncher(toolbar) {
    const history = document.createElement("button");
    history.type = "button";
    history.setAttribute(launcherAttr, "");
    history.setAttribute("aria-haspopup", "dialog");
    const icon = document.createElement("span");
    icon.className = "agy-usage-launcher-icon";
    icon.setAttribute("aria-hidden", "true");
    icon.append(...Array.from({length:3}, () => document.createElement("i")));
    history.append(icon, document.createTextNode("用量看板"));
    const preview = document.createElement("div");
    preview.id = "agy-usage-preview-" + crypto.randomUUID();
    preview.setAttribute("data-agy-usage-popup", "");
    preview.setAttribute("data-agy-usage-preview", "");
    preview.setAttribute("popover", "manual");
    preview.setAttribute("role", "tooltip");
    document.body.append(preview);
    history.setAttribute("aria-describedby", preview.id);
    const header = document.createElement("header");
    header.textContent = "API 参考费用";
    const details = document.createElement("dl");
    const readings = ["当前会话", "近 7 天合计"].map(label => {
      const term = document.createElement("dt");
      term.textContent = label;
      const detail = document.createElement("dd");
      const usd = document.createElement("span");
      const cny = document.createElement("span");
      detail.append(usd, cny);
      details.append(term, detail);
      return {usd, cny};
    });
    preview.append(header, details);
    let timer;
    const opened = () => preview.matches(":popover-open");
    function close() {
      clearTimeout(timer);
      if (opened()) preview.hidePopover();
    }
    function position() {
      const bounds = history.getBoundingClientRect();
      const left = Math.max(12, Math.min(bounds.right - preview.offsetWidth, innerWidth - preview.offsetWidth - 12));
      preview.style.left = left + "px";
      preview.style.top = Math.max(10, Math.min(bounds.bottom + 8, innerHeight - preview.offsetHeight - 12)) + "px";
    }
    function schedule() {
      clearTimeout(timer);
      if (opened()) return;
      timer = setTimeout(() => {
        if (!history.isConnected || state.dismiss) return;
        preview.showPopover();
        position();
      }, 180);
    }
    function closeSoon() {
      clearTimeout(timer);
      timer = setTimeout(close, 140);
    }
    history.addEventListener("pointerenter", schedule);
    history.addEventListener("pointerleave", closeSoon);
    history.addEventListener("focus", () => { if (history.matches(":focus-visible")) schedule(); });
    history.addEventListener("blur", closeSoon);
    preview.addEventListener("pointerenter", () => clearTimeout(timer));
    preview.addEventListener("pointerleave", closeSoon);
    function escape(event) {
      if (event.key === "Escape" && opened()) {
        close();
        event.preventDefault();
        event.stopPropagation();
      }
    }
    function dismissOutside(event) {
      if (!history.contains(event.target) && !preview.contains(event.target)) close();
    }
    document.addEventListener("keydown", escape, true);
    document.addEventListener("pointerdown", dismissOutside, true);
    history.addEventListener("click", event => {
      event.stopPropagation();
      close();
      state.open(conversationFor(toolbar), history);
    });
    history.updatePreview = (session, week, rate) => {
      [session, week].forEach((amount, index) => {
        const {usd, cny} = readings[index];
        const dollar = amount == null ? "US$ —" : "US$" + money.format(amount);
        const yuan = amount == null || rate == null ? "¥ —" : "¥" + money.format(amount * rate);
        if (usd.textContent !== dollar) usd.textContent = dollar;
        if (cny.textContent !== yuan) cny.textContent = yuan;
      });
      if (opened()) position();
    };
    history.destroy = () => {
      close();
      preview.remove();
      history.remove();
      document.removeEventListener("keydown", escape, true);
      document.removeEventListener("pointerdown", dismissOutside, true);
    };
    return history;
  }

  function placeLaunchers() {
    for (const [toolbar, button] of launchers) {
      if (toolbar.isConnected) continue;
      button.destroy();
      launchers.delete(toolbar);
    }
    for (const toolbar of document.querySelectorAll('[data-testid="pane-titlebar-toolbar"]')) {
      const more = toolbar.querySelector('[data-testid="titlebar-more-actions"]');
      const anchor = more?.parentElement || toolbar.firstElementChild || toolbar;
      let button = launchers.get(toolbar);
      if (!button) {
        button = makeLauncher(toolbar);
        launchers.set(toolbar, button);
      }
      if (button.parentElement !== anchor) anchor.insertBefore(button, more || anchor.firstChild);
    }
  }

  function updateHeader(sessions, preview) {
    for (const [toolbar, launcher] of launchers) {
      const id=conversationFor(toolbar);
      const reading = sessions.find(session => session.id === id);
      launcher.updatePreview(reading?.usage?.reference_usd, preview.week_usd, preview.rate?.rate);
      const entry=[...bars.entries()].find(([input])=>conversation(input)===id);
      const previous = launcher.quotaButton;
      const button = entry?.[1].usage.pills[3].button;
      if (previous && previous !== button && previous.parentElement === launcher.parentElement) previous.remove();
      launcher.quotaButton = button;
      if (!entry) continue;
      button.className="rounded-md hover:bg-secondary";
      style(button,{fontSize:"11px",height:"28px",padding:"0 8px",border:"0",background:"none",
        cursor:"pointer",color:"var(--muted-foreground,#71717a)",whiteSpace:"nowrap",webkitAppRegion:"no-drag"});
      if(button.parentElement!==launcher.parentElement)launcher.before(button);
    }
  }

  function placeBar(input, slot) {
    const trigger = modelTrigger(input);
    const group = trigger?.closest(".agy-composer-footer")?.firstElementChild;
    if (!group?.contains(trigger)) {
      slot.remove();
      return;
    }
    if (group.lastElementChild === slot) return;
    if (getComputedStyle(group).display !== "flex") return;
    // A zero-basis flex item occupies only the current spare width.
    group.append(slot);
  }

  state.probe = () => ({
    inputs: inputs().map(input => ({
      id: conversation(input), model: model(input), windowMode: modelTrigger(input)?.dataset.contextWindow || ""
    })),
    request: state.request
  });

  state.install = ({ html, css }) => {
    if (state.open) return;
    state.open = (id, opener) => {
      let serial = 0;
      const epoch = crypto.randomUUID();
      let pending;
      let timeout;
      const dialog = document.createElement("dialog");
      dialog.setAttribute("data-agy-usage-dashboard", "");
      dialog.setAttribute("aria-label", "Antigravity 用量看板");
      style(dialog, {
        width: "min(1160px, calc(100vw - 40px))", maxWidth: "none",
        maxHeight: "min(86vh, 900px)", padding: "0", overflow: "auto", margin: "auto",
        border: "1px solid var(--border, #8884)", borderRadius: "16px",
        background: "var(--background, Canvas)", color: "var(--foreground, CanvasText)",
        boxShadow: "0 20px 64px #0003", overscrollBehavior: "contain"
      });
      const host = document.createElement("div");
      host.style.colorScheme = getComputedStyle(document.documentElement).colorScheme;
      const root = host.attachShadow({mode: "open"});
      const sheet = new CSSStyleSheet();
      sheet.replaceSync(css);
      root.adoptedStyleSheets = [sheet];
      root.innerHTML = html;
      root.querySelector("#close-dashboard").hidden = false;
      root.querySelector("#close-dashboard").addEventListener("click", () => dialog.close());
      dialog.addEventListener("click", event => {
        if (event.target === dialog) dialog.close();
      });
      dialog.append(host);
      document.body.append(dialog);
      const view = state.createDashboard(root, filters => new Promise((resolve, reject) => {
        const cacheKey=JSON.stringify(filters);
        const cached=dashboardCache.get(cacheKey);
        if(cached&&performance.now()-cached.time<5000){resolve(cached.report);return;}
        pending = { resolve, reject, cacheKey };
        state.request = { epoch, id: ++serial, filters };
        timeout = setTimeout(() => {
          pending = undefined;
          state.request = null;
          reject(new Error("本机用量服务未响应，请刷新重试"));
        }, 25000);
      }), id);
      state.finish = data => {
        if (!pending || data.epoch !== epoch || data.id !== serial) return;
        const current = pending;
        pending = undefined;
        clearTimeout(timeout);
        state.request = null;
        if (data.error) current.reject(new Error(data.error));
        else {
          dashboardCache.set(current.cacheKey,{time:performance.now(),report:data.report});
          if(dashboardCache.size>20) dashboardCache.delete(dashboardCache.keys().next().value);
          current.resolve(data.report);
        }
      };
      state.dismiss = () => dialog.close();
      dialog.addEventListener("close", () => {
        view.dispose();
        clearTimeout(timeout);
        state.request = null;
        state.finish = null;
        state.dismiss = null;
        pending?.reject(new Error("已关闭"));
        dialog.remove();
        if (opener.isConnected) opener.focus();
      }, {once: true});
      dialog.showModal();
      root.querySelector("#close-dashboard").focus();
    };
  };

  state.render = ({ sessions, quotas, completed, preview = {} }) => {
    if (completed) state.finish?.(completed);
    placeLaunchers();
    const visible = inputs();
    for (const [input, bar] of bars) {
      if (visible.includes(input)) continue;
      bar.usage.destroy();
      bar.remove();
      bars.delete(input);
    }
    visible.forEach((input, index) => {
      const currentModel = model(input);
      const windowMode = modelTrigger(input)?.dataset.contextWindow || "";
      const reading = sessions[index];
      const usage = reading?.id === conversation(input) && reading?.model === currentModel
        && reading.windowMode === windowMode ? reading.usage : {};
      const quota = quotas[currentModel];
      let bar = bars.get(input);
      if (!bar) {
        bar = makeBar();
        bars.set(input, bar);
      }
      placeBar(input, bar);
      const callLimit = usage.call_limit ? value(usage.call_limit) + " Token" : "—";
      const modelLimit = usage.model_limit ? value(usage.model_limit) + " Token" : "—";
      const windowLimit = usage.model_limit ? capacity(usage.model_limit) : "—";
      const context = usage.used != null
        ? compact.format(usage.used) + "/" + windowLimit : windowLimit;
      const contextPercent = usage.context != null ? decimal(usage.context) + "%" : "";
      const period = (window, label) => {
        const bucket = quota?.buckets.find(item => item.window === window);
        if (!bucket) return null;
        const remaining = bucket.remaining == null ? "—" : value(Math.round(bucket.remaining * 1000) / 10, "%");
        const reset = bucket.reset && !Number.isNaN(Date.parse(bucket.reset))
          ? " · 北京时间 " + time.format(new Date(bucket.reset)) + " 重置" : "";
        return [label + " " + remaining, quota.group + " · " + label + "剩余 " + remaining + reset];
      };
      const stats=bar.usage.pills;
      let readingsChanged = false;
      const assign=(index,text,detail,short=text,mini=short)=>{
        const pill=stats[index];
        if (pill.text.textContent!==text) {
          pill.text.textContent=text;
          pill.button.setAttribute("aria-label",text&&text!=="—" ? pill.name+"："+text : pill.name);
          if (index < 3) readingsChanged = true;
        }
        if (index < 3 && pill.short.textContent!==short) {
          pill.short.textContent=short;
          readingsChanged = true;
        }
        if (index < 3 && pill.mini.textContent!==mini) {
          pill.mini.textContent=mini;
          readingsChanged = true;
        }
        pill.button.detail=detail;
      };
      const roundsMeta = usage.compactions == null ? "" : " · 压缩 "+value(usage.compactions)+" 次";
      const rounds=usage.turns == null ? "暂无数据" : value(usage.turns)+" 轮 "+value(usage.steps)+" 步"+roundsMeta;
      assign(0, (usage.turns == null ? "会话 —" : value(usage.turns)+" 轮")
        +(usage.compactions ? " · 压缩 "+value(usage.compactions)+" 次" : "")
        +(usage.speed == null ? "" : " · "+decimal(usage.speed)+" tok/s"), {
        title:"会话统计",value:rounds,rows:[
          ["对话轮数 / 步骤",usage.turns == null ? "暂无数据" : value(usage.turns)+" 轮 "+value(usage.steps)+" 步"],
          ["上下文压缩",usage.compactions == null ? "暂无数据" : value(usage.compactions)+" 次"],
          ["模型生成用时",duration(usage.model_seconds)],["工具调用用时",duration(usage.tool_seconds)],
          ["平均首 Token（TTFT）",usage.average_ttft == null ? "暂无数据" : decimal(usage.average_ttft)+" 秒"],
          ["最近输出速率",usage.speed == null ? "暂无数据" : decimal(usage.speed)+" tok/s"]
        ]}, [usage.turns == null ? "" : compact.format(usage.turns)+"轮",
          usage.compactions ? "压缩"+compact.format(usage.compactions) : "",
          usage.speed == null ? "" : decimal(usage.speed)+"t/s"].filter(Boolean).join("·") || "—",
          usage.turns == null ? "—" : compact.format(usage.turns)+"轮");
      assign(1, (usage.total == null ? "Token —" : compact.format(usage.total)+" tok")
        +(usage.hit == null ? "" : " · 命中 "+decimal(usage.hit)+"%"), {
        title:"Token 用量",value:value(usage.total)+" tok",rows:[
          ["缓存命中",usage.hit == null ? "暂无数据" : decimal(usage.hit)+"%"],
          ["未缓存输入",value(usage.uncached)+" tok"],["缓存读取",value(usage.cached)+" tok"],
          ["缓存写入",value(usage.written)+" tok"],["输出",value(usage.output)+" tok"]
        ]}, [usage.total == null ? "" : compact.format(usage.total),
          usage.hit == null ? "" : decimal(usage.hit)+"%"].filter(Boolean).join("·") || "—",
          usage.total == null ? "—" : compact.format(usage.total));
      const contextReading = [context, contextPercent].filter(Boolean).join(" · ");
      assign(2,contextReading,{
        title:"上下文",value:contextPercent || windowLimit,progress:usage.context,
        rows:[["已用（调用估算）",usage.used == null ? "—" : value(usage.used)+" Token"],
          ["所选模型窗口",modelLimit],
          ...(windowMode ? [["所选上下文模式",windowMode]] : []),
          ...(usage.call_limit ? [["调用记录的上下文预算",callLimit]] : []),
          ...(usage.breakdown||[]).map(group=>[({"System Prompt":"系统提示词","Tools":"工具定义","Chat Messages":"对话消息"})[group.name]||group.name||"上下文分组","≈"+compact.format(group.tokens)])],
        note:usage.used == null ? (usage.waiting_for_call ? "等待该模型产生上下文记录" : "暂无上下文记录")
          : (usage.call_model ? "最近一次调用 · "+usage.call_model : "") + (usage.model_limit ? "" : " · 暂未读取到模型窗口")
      },[context, contextPercent].filter(Boolean).join("·"),
        usage.context != null ? Math.round(usage.context)+"%" : windowLimit);
      const periods=[period("5h","5h"),period("weekly","周")].filter(Boolean);
      stats[3].button.hidden=!periods.length;
      assign(3,periods.map(item=>item[0]).join(" · "),{
        title:"剩余额度",value:quota?.group||"",rows:periods.map(item=>[item[0],item[1].split("北京时间 ")[1]||"暂无重置时间"])
      });
      bar.usage.layout(readingsChanged);
      bar.usage.paint();
      const fraction = Math.max(0, Math.min(100, usage.context || 0));
      const ink = usage.context >= 100 ? "#d47743" : "light-dark(#3c63b4,#9eb7f3)";
      bar.usage.ring.style.background = `conic-gradient(${ink} ${fraction}%, var(--border, #8884) 0)`;
    });
    updateHeader(sessions, preview);
  };
  state.remove = () => {
    state.dismiss?.();
    for (const bar of bars.values()) { bar.usage.destroy(); bar.remove(); }
    styles.remove();
    for (const button of launchers.values()) button.destroy();
    delete window[key];
  };
})();
