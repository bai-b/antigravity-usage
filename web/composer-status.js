(() => {
  const key = "__agyUsageStatus";
  if (window[key]?.version === 59) return;
  window[key]?.remove();
  const state = window[key] = { version: 59, request: null };
  const dashboardCache = new Map();
  const bars = new Map();
  const launchers = new Map();
  const viewedCompletions = new Map();
  const selector = '[data-testid="agent-input-box"]';
  const attr = "data-agy-usage-status";
  const launcherAttr = "data-agy-usage-launcher";
  let overlay;

  function claimOverlay(owner, close, fixed = false) {
    if (state.dismiss) return false;
    if (overlay?.owner === owner) {
      overlay.fixed ||= fixed;
      return true;
    }
    if (overlay?.fixed && !fixed) return false;
    overlay?.close();
    overlay = {owner, close, fixed};
    return true;
  }

  function releaseOverlay(owner) {
    if (overlay?.owner === owner) overlay = undefined;
  }
  const number = new Intl.NumberFormat("zh-CN");
  const money = new Intl.NumberFormat("zh-CN", {minimumFractionDigits: 2, maximumFractionDigits: 2});
  const value = (v, suffix = "") => v == null ? "—" : number.format(v) + suffix;
  const compact = new Intl.NumberFormat("en", {notation: "compact", maximumFractionDigits: 1});
  const capacity = tokens => tokens % 1000 === 0 ? compact.format(tokens)
    : tokens >= 1024 ** 2 && tokens % (1024 ** 2) === 0 ? tokens / (1024 ** 2) + "M"
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

  function modelParts(trigger) {
    return [...(trigger?.children || [])].filter(part => part instanceof HTMLElement
      && !part.matches('[hidden], [aria-hidden="true"], [data-icon], .google-symbols')
      && part.textContent.trim());
  }

  function model(input) {
    const trigger = modelTrigger(input);
    const label = modelParts(trigger).map(part => part.textContent.trim()).join(" ");
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
    [data-agy-usage-slot] {min-inline-size:0;align-self:stretch;padding-inline:4px}
    .agy-composer-footer:has(> [data-agy-usage-slot]) {display:grid;grid-template-columns:minmax(0,auto) minmax(0,1fr) max-content;grid-template-areas:"native usage actions"}
    .agy-composer-footer:has(> [data-agy-usage-slot]) > :first-child {grid-area:native}
    .agy-composer-footer:has(> [data-agy-usage-slot]) > :last-child {grid-area:actions}
    .agy-composer-footer > [data-agy-usage-slot] {grid-area:usage;align-self:center}
    .agy-composer-footer:has(> [data-agy-usage-slot][data-layout="summary"]) {grid-template-columns:minmax(0,1fr) max-content max-content}
    [data-agy-usage-status] {display:flex;align-items:center;justify-content:center;gap:1px 7px;min-block-size:22px;white-space:nowrap;overflow:clip;color:var(--agy-usage-dim);font-size:11px;line-height:16px;font-variant-numeric:tabular-nums}
    [data-agy-usage-status] button {font:inherit;color:inherit;text-align:start;border:0;background:none;padding:2px 4px;border-radius:var(--agy-usage-radius-sm);cursor:pointer}
    [data-agy-usage-status] .agy-usage-pill {display:inline-flex;align-items:center;gap:4px;flex:none;min-inline-size:0}
    [data-agy-usage-status] .agy-usage-icon {display:grid;place-items:center;flex:none}
    [data-agy-usage-status] .agy-usage-reading {min-inline-size:0}
    [data-agy-usage-status] .agy-usage-compact {display:none}
    [data-agy-usage-status] [data-reading="short"] .agy-usage-full {display:none}
    [data-agy-usage-status] [data-reading="short"] .agy-usage-compact {display:inline}
    [data-agy-usage-status] .agy-usage-summary {padding-inline:6px}
    [data-agy-usage-status] .agy-usage-pill[hidden] {display:none}
    [data-agy-usage-status][data-measuring] .agy-usage-pill {display:inline-flex}
    [data-agy-usage-status][data-measuring] .agy-usage-reading {display:inline}
    .agy-composer-footer:has(> [data-agy-usage-slot]) [data-testid="model-selector-trigger"] > span:not([aria-hidden="true"],[data-icon],.google-symbols) {white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
    [data-agy-usage-launcher], .agy-usage-header-pill {display:inline-flex;align-items:center;gap:5px;flex:none;height:28px;padding:0 8px;border:0;border-radius:var(--agy-usage-radius-control);background:none;color:var(--agy-usage-dim);cursor:pointer;font:inherit;font-size:11px;font-variant-numeric:tabular-nums;white-space:nowrap;-webkit-app-region:no-drag}
    [data-agy-usage-launcher] {color:var(--agy-usage-ink);font-size:12px;font-weight:550}
    :is([data-agy-usage-status] button,[data-agy-usage-launcher],.agy-usage-header-pill):hover {background:var(--agy-usage-hover);color:var(--agy-usage-ink)}
    :is([data-agy-usage-status] button,[data-agy-usage-launcher],.agy-usage-header-pill):focus-visible {outline:2px solid var(--agy-usage-accent);outline-offset:-2px}
    .agy-usage-header-pill.agy-usage-task {font-weight:600}
    .agy-usage-task[data-measured] {--agy-meter:var(--agy-usage-accent);--agy-task-surface:var(--agy-usage-accent-soft);color:var(--agy-meter);background:var(--agy-task-surface)}
    .agy-usage-task[data-complete] {--agy-meter:var(--agy-usage-good);--agy-task-surface:var(--agy-usage-good-soft)}
    .agy-usage-task[data-measured]:hover {color:var(--agy-meter);background:color-mix(in oklab,var(--agy-meter) 10%,var(--agy-task-surface))}
    .agy-usage-ring {display:inline-block;width:12px;height:12px;flex:none;border-radius:50%;background:conic-gradient(var(--agy-meter,var(--agy-usage-accent)) var(--agy-usage-fraction,0%),var(--agy-usage-track) 0);mask-image:radial-gradient(transparent 42%,black 48%)}
    .agy-usage-ring[data-warning], [data-agy-usage-popup] progress[data-tone="warn"] {--agy-meter:var(--agy-usage-warn)}
    [data-agy-usage-popup] progress[data-tone="good"] {--agy-meter:var(--agy-usage-good)}
    @media (forced-colors: active) {
      .agy-usage-ring {forced-color-adjust:none;background:conic-gradient(CanvasText var(--agy-usage-fraction,0%),GrayText 0)}
    }
    .agy-usage-launcher-icon {height:12px;display:flex;align-items:end;gap:2px;color:var(--agy-usage-dim)}
    .agy-usage-launcher-icon i {width:2px;border-radius:1px;background:currentColor}
    .agy-usage-launcher-icon i:nth-child(1) {height:5px}
    .agy-usage-launcher-icon i:nth-child(2) {height:8px}
    .agy-usage-launcher-icon i:nth-child(3) {height:11px}
    [data-agy-usage-popup] {position:fixed;inset:auto;margin:0;padding:14px 16px;width:300px;max-width:calc(100vw - 24px);border:1px solid var(--agy-usage-line);border-radius:var(--agy-usage-radius-card);background:var(--agy-usage-panel);color:var(--agy-usage-ink);box-shadow:var(--agy-usage-shadow);font:12px/1.5 var(--agy-usage-font);font-variant-numeric:tabular-nums;z-index:100}
    [data-agy-usage-popup] header {display:flex;justify-content:space-between;align-items:center;gap:12px;padding-bottom:10px;margin-bottom:6px;border-bottom:1px solid var(--agy-usage-line);font-weight:600}
    [data-agy-usage-popup] dl {display:grid;grid-template-columns:minmax(0,1fr) auto;gap:9px 16px;margin:10px 0 0}
    [data-agy-usage-popup] dt {color:var(--agy-usage-dim);overflow-wrap:anywhere}
    [data-agy-usage-popup] dd {margin:0;text-align:right;max-width:170px;overflow-wrap:anywhere}
    [data-agy-usage-popup] progress {display:block;width:100%;height:5px;margin:10px 0;accent-color:var(--agy-meter,var(--agy-usage-accent))}
    [data-agy-usage-popup] small {display:block;color:var(--agy-usage-dim);margin-top:10px;font:inherit;font-size:11px}
    [data-agy-usage-preview] {width:292px;padding:12px}
    [data-agy-usage-preview] header {padding:0 0 8px;margin:0 0 8px;font-size:13px}
    [data-agy-usage-preview] dl {margin:0;gap:8px 12px;align-items:center}
    [data-agy-usage-preview] dd {display:flex;justify-content:flex-end;gap:8px;flex-wrap:wrap;font-weight:500}
    [data-agy-usage-preview] dd span {white-space:nowrap}
    .agy-usage-running-control {display:inline-flex;flex:none}
    .agy-usage-running-control[hidden] {display:none}
    .agy-usage-header-pill.agy-usage-running[data-active] {background:var(--agy-usage-accent-soft);color:var(--agy-usage-accent);font-weight:600}
    .agy-usage-header-pill.agy-usage-running[data-active][data-completed] {background:var(--agy-usage-good-soft);color:var(--agy-usage-good)}
    .agy-usage-header-pill.agy-usage-running[data-attention] {background:var(--agy-usage-warn-soft);color:var(--agy-usage-warn)}
    [data-agy-usage-running-list] {width:320px;padding:10px;max-height:min(420px,calc(100vh - 24px));overflow:auto;overscroll-behavior:contain}
    [data-agy-usage-running-list] header {padding:2px 6px 8px;margin-bottom:4px}
    [data-agy-usage-running-list] section:not([hidden]) + section:not([hidden]) {border-block-start:1px solid var(--agy-usage-line);margin-block-start:6px;padding-block-start:6px}
    [data-agy-usage-running-list] h3 {margin:0;padding:4px 6px;color:var(--agy-usage-dim);font:inherit;font-weight:600}
    [data-agy-usage-running-list] ul {list-style:none;margin:0;padding:0}
    [data-agy-usage-running-list] a {display:flex;align-items:center;justify-content:space-between;gap:8px;min-height:32px;padding:5px 6px;border-radius:var(--agy-usage-radius-sm);text-decoration:none;color:inherit}
    [data-agy-usage-running-list] a:hover {background:var(--agy-usage-hover)}
    [data-agy-usage-running-list] a:focus-visible {outline:2px solid var(--agy-usage-accent);outline-offset:-2px}
    [data-agy-usage-running-list] a[aria-current="page"] {color:var(--agy-usage-accent);background:var(--agy-usage-accent-soft)}
    [data-agy-usage-running-list] a span:first-child {min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
    [data-agy-usage-running-list] a span:nth-child(2), [data-agy-usage-running-list] p {color:var(--agy-usage-dim);font-size:11px}
    [data-agy-usage-running-list] a span:nth-child(2) {white-space:nowrap}
    [data-agy-usage-running-list] a span.agy-usage-activity-outcome {color:var(--agy-usage-warn)}
    [data-agy-usage-running-list] p {margin:0;padding:5px 6px}
  `;
  // 旧版注入留下的同款基础样式一并清掉，重复注入不再累积。
  for (const stale of document.querySelectorAll("style")) {
    if (stale !== styles && stale.textContent.includes("[data-agy-usage-slot] {flex:1 1 0px")) stale.remove();
  }
  document.head.append(styles);
  const decimal = v => v == null ? "暂无数据" : value(Math.round(v * 10) / 10);
  const duration = seconds => seconds == null ? "暂无数据" : seconds < 60
    ? decimal(seconds) + " 秒" : Math.floor(seconds / 60) + " 分 " + Math.round(seconds % 60) + " 秒";

  function makeBar() {
    const slot = document.createElement("div");
    slot.setAttribute("data-agy-usage-slot", "");
    slot.dataset.layout = "summary";
    const bar = document.createElement("div");
    bar.setAttribute(attr, "");
    bar.setAttribute("role", "group");
    bar.setAttribute("aria-label", "Antigravity 会话用量");
    slot.append(bar);
    const popup = document.createElement("div");
    popup.setAttribute("data-agy-usage-popup","");
    popup.setAttribute("popover","manual");
    popup.id = "agy-usage-popup-" + crypto.randomUUID();
    document.body.append(popup);
    let active;
    let closeTimer;
    let frame = 0;
    let painted = "";
    const hide = () => {
      clearTimeout(closeTimer);
      cancelAnimationFrame(frame);
      frame = 0;
      if (popup.matches(":popover-open")) popup.hidePopover();
      releaseOverlay(slot);
      active?.setAttribute("aria-expanded","false");
      active = undefined;
      painted = "";
    };
    const pinned = () => overlay?.owner === slot && overlay.fixed;
    const closeSoon = () => {
      clearTimeout(closeTimer);
      if (!pinned()) closeTimer = setTimeout(hide,140);
    };
    const position = () => {
      if (!active) return;
      const box=active.getBoundingClientRect();
      if (!box.width || !box.height) { hide();return; }
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
        meter.setAttribute("aria-label",content.progressLabel || "上下文已用");
        if (content.progressTone) meter.dataset.tone = content.progressTone;
        popup.append(meter);
      }
      popup.append(list);
      if(content.note){const note=document.createElement("small");note.textContent=content.note;popup.append(note);}
      position();
    };
    function show(button, fixed = false) {
      if (pinned() && active !== button && !fixed) return;
      if (!claimOverlay(slot, hide, fixed)) return;
      clearTimeout(closeTimer);
      active?.setAttribute("aria-expanded","false");
      active=button;
      button.setAttribute("aria-expanded","true");
      paint();
      if (!popup.matches(":popover-open")) popup.showPopover();
      position();
      if (active && !frame) frame = requestAnimationFrame(follow);
    }
    const pills=["会话统计","Token 用量","上下文","额度","任务进度","会话用量"].map((name,index)=>{
      const button=document.createElement("button");
      button.type="button";button.className="agy-usage-pill";
      button.setAttribute("aria-label",name);
      button.setAttribute("aria-controls",popup.id);
      button.setAttribute("aria-expanded","false");
      const icon=document.createElement("span");
      icon.className="agy-usage-icon";
      if(index===2 || index===4) icon.classList.add("agy-usage-ring");
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
      const text=document.createElement("span");
      text.className="agy-usage-reading agy-usage-full";
      text.textContent="—";
      const short=document.createElement("span");
      short.className="agy-usage-reading agy-usage-compact";
      if(index===5) {
        button.classList.add("agy-usage-summary");
        const summaryIcon=document.createElement("span");
        summaryIcon.className="agy-usage-launcher-icon";
        summaryIcon.setAttribute("aria-hidden","true");
        summaryIcon.append(...Array.from({length:3}, () => document.createElement("i")));
        button.append(summaryIcon);
      } else if(index>=3) {
        button.className="agy-usage-header-pill";
        if (index===4) {button.classList.add("agy-usage-task");button.append(icon);}
        button.append(text);
      }
      else {
        text.setAttribute("aria-hidden", "true");
        short.setAttribute("aria-hidden", "true");
        button.append(icon,text,short);
      }
      button.addEventListener("pointerenter",()=>show(button));
      button.addEventListener("pointerleave",()=>{if(active===button)closeSoon();});
      button.addEventListener("focus",()=>show(button));
      button.addEventListener("blur",()=>{if(active===button)closeSoon();});
      button.addEventListener("click",event=>{
        event.stopPropagation();
        if(pinned()&&active===button) hide();
        else show(button,true);
      });
      if(index<3 || index===5) bar.append(button);
      return {button,text,short,icon,name};
    });
    const ring=pills[2].icon;
    popup.addEventListener("pointerenter",()=>clearTimeout(closeTimer));
    popup.addEventListener("pointerleave",closeSoon);
    const escape=e=>{if(e.key==="Escape"&&active){hide();e.stopPropagation();}};
    const outside=e=>{if(!bar.contains(e.target)&&!popup.contains(e.target)&&!pills.some(p=>p.button.contains(e.target)))hide();};
    document.addEventListener("pointerdown",outside,true);
    document.addEventListener("keydown",escape,true);
    let measuredFooter;
    let measuredActions;
    let measuredControls;
    let layoutKey;
    let titledModel;
    let addedTitle;
    const readings = pills.slice(0, 3);
    const overview = pills[5].button;
    // Prefer context, then Token, then conversation statistics when the same
    // number of readings can fit in more than one combination.
    const [context, tokens, conversationStats] = [...readings].reverse();
    const choices = [[context, tokens, conversationStats], [context, tokens], [context, conversationStats],
      [tokens, conversationStats], [context], [tokens], [conversationStats]];
    function clearModelTitle() {
      if (titledModel?.getAttribute("title") === addedTitle) titledModel?.removeAttribute("title");
      titledModel = addedTitle = undefined;
    }
    function layout() {
      const footer = slot.parentElement;
      const trigger = footer?.querySelector('[data-testid="model-selector-trigger"]');
      if (!footer?.classList.contains("agy-composer-footer") || !trigger || !footer.clientWidth) {
        clearModelTitle();
        return;
      }
      const controls = footer.firstElementChild;
      const parts = modelParts(trigger).filter(part => part.getClientRects().length);
      const actions = slot.nextElementSibling;
      if (!parts.length || !actions) return;
      if (measuredFooter !== footer || measuredActions !== actions || measuredControls !== controls) {
        observer.disconnect();
        observer.observe(footer);
        observer.observe(actions);
        // 模型名变长会挤窄用量列，但 footer 宽度不变 —— 必须也盯住 slot 自己。
        observer.observe(slot);
        modelObserver.disconnect();
        modelObserver.observe(controls, {subtree:true,childList:true,characterData:true,attributes:true,
          attributeFilter:["class","style","hidden","aria-hidden","data-icon","data-context-window"]});
        measuredFooter = footer;
        measuredActions = actions;
        measuredControls = controls;
        layoutKey = undefined;
      }
      const leadingWidth = [...controls.children]
        .filter(control => !control.contains(trigger)).reduce((width, control) => width + control.offsetWidth, 0);
      const next = JSON.stringify([footer.clientWidth, slot.clientWidth, bar.clientWidth, actions.offsetWidth, leadingWidth, ...parts.map(part => part.textContent),
        ...readings.map(pill => [pill.text.textContent, pill.short.textContent])]);
      if (next === layoutKey) return;
      layoutKey = next;
      clearModelTitle();
      // 每次重排先清掉上一轮均分出来的内边距，测量必须在原始宽度下进行。
      for (const pill of readings) pill.button.style.paddingInline = "";
      slot.dataset.layout = "full";
      bar.setAttribute("data-measuring", "");
      const available = bar.getBoundingClientRect().width - 1;
      const gap = parseFloat(getComputedStyle(bar).columnGap) || 0;
      const overviewWasHidden = overview.hidden;
      overview.hidden = false;
      const overviewWidth = overview.getBoundingClientRect().width;
      const modelFits = parts.every(part => part.clientWidth > 0 && part.scrollWidth <= part.clientWidth + 1);
      bar.removeAttribute("data-measuring");
      // 逐档实测：整颗胶囊在「完整读数」「缩写读数」两种状态下的真实宽度。
      // 原来用 combined 反推两个宽度，一旦估算偏差就会选中放不下的组合、把文字裁掉。
      const sizes = new Map(readings.map(pill => {
        pill.button.hidden = false;
        delete pill.button.dataset.reading;
        const full = pill.button.getBoundingClientRect().width;
        pill.button.dataset.reading = "short";
        const short = pill.button.getBoundingClientRect().width;
        delete pill.button.dataset.reading;
        return [pill, {full, short: Math.min(full, short)}];
      }));
      const widthOf = choice => {
        const partial = choice.length < readings.length;
        return choice.reduce((width, pill) => {
          const {full, short} = sizes.get(pill);
          return width + Math.min(full, short);
        }, partial ? overviewWidth : 0) + (choice.length - (partial ? 0 : 1)) * gap;
      };
      const choice = (modelFits && choices.find(item => widthOf(item) <= available)) || [];
      for (const pill of readings) pill.button.hidden = !choice.includes(pill);
      overview.hidden = choice.length === readings.length;
      if (!choice.length) {
        slot.dataset.layout = "summary";
        const clipped = parts.find(part => part.scrollWidth > part.clientWidth + 1);
        if (clipped && !clipped.hasAttribute("title")) {
          titledModel = clipped;
          addedTitle = parts.map(part => part.textContent.trim()).join(" ");
          clipped.setAttribute("title", addedTitle);
        }
        return;
      }
      // 空白均分：以每颗胶囊的缩写宽度为起点，把剩余空间在还没到满宽的胶囊之间等量注水，
      // 谁先到满宽谁退出，剩下的继续平分 —— 不再按数组顺序让前面的胶囊先吃满。
      const partial = choice.length < readings.length;
      const budget = available - (choice.length - (partial ? 0 : 1)) * gap - (partial ? overviewWidth : 0);
      const alloc = new Map(choice.map(pill => [pill, sizes.get(pill).short]));
      let spare = budget - choice.reduce((width, pill) => width + alloc.get(pill), 0);
      for (let pass = 0; pass < 4 && spare > 0.5; pass++) {
        const open = choice.filter(pill => alloc.get(pill) < sizes.get(pill).full - 0.5);
        if (!open.length) break;
        const step = spare / open.length;
        let used = 0;
        for (const pill of open) {
          const add = Math.min(step, sizes.get(pill).full - alloc.get(pill));
          alloc.set(pill, alloc.get(pill) + add);
          used += add;
        }
        if (used < 0.5) break;
        spare -= used;
      }
      const tier = new Map(choice.map(pill => [pill, alloc.get(pill) >= sizes.get(pill).full - 0.5 ? "full" : "short"]));
      // 取整到档位会让出一点空间，用这点余量按 readings 的优先级（上下文 → Token → 会话）
      // 把还能升到完整读数的胶囊补满，同级再挑升级代价最小的。
      let left = budget - choice.reduce((width, pill) => width + sizes.get(pill)[tier.get(pill)], 0);
      const rank = new Map(choices[0].map((pill, index) => [pill, index]));
      const pending = choice.filter(pill => tier.get(pill) === "short")
        .sort((a, b) => (rank.get(a) ?? 9) - (rank.get(b) ?? 9)
          || (sizes.get(a).full - sizes.get(a).short) - (sizes.get(b).full - sizes.get(b).short));
      for (const pill of pending) {
        const need = sizes.get(pill).full - sizes.get(pill).short;
        if (need > left + 0.5) continue;
        left -= need;
        tier.set(pill, "full");
      }
      // 档位取整后剩下的空白，均分成每颗胶囊的内边距，让整行均匀铺满而不是左侧挤成一团。
      const filled = choice.reduce((width, pill) => width + sizes.get(pill)[tier.get(pill)], 0);
      const spread = Math.max(0, Math.min(budget - filled, 20 * choice.length)) / (choice.length * 2);
      for (const pill of choice) pill.button.style.paddingInline = (4 + spread).toFixed(2) + "px";
      let shortened = false;
      for (const pill of choice) {
        if (tier.get(pill) === "full") delete pill.button.dataset.reading;
        else { pill.button.dataset.reading = "short"; shortened = true; }
      }
      slot.dataset.layout = overview.hidden ? (shortened ? "compact" : "full") : "partial";
    }
    const observer = new ResizeObserver(layout);
    const modelObserver = new MutationObserver(() => { layoutKey = undefined; layout(); });
    document.fonts?.ready.then(() => { layoutKey = undefined; if (slot.isConnected) layout(); });
    slot.usage={pills,ring,paint,layout,destroy(){observer.disconnect();modelObserver.disconnect();clearModelTitle();hide();popup.remove();pills.slice(3,5).forEach(pill=>pill.button.remove());
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

  function positionBelow(anchor, popup) {
    const bounds = anchor.getBoundingClientRect();
    popup.style.left = Math.max(12, Math.min(bounds.right - popup.offsetWidth, innerWidth - popup.offsetWidth - 12)) + "px";
    popup.style.top = Math.max(10, Math.min(bounds.bottom + 8, innerHeight - popup.offsetHeight - 12)) + "px";
  }

  function makeActivityList() {
    const control = document.createElement("span");
    control.className = "agy-usage-running-control";
    control.hidden = true;
    const trigger = document.createElement("button");
    trigger.type = "button";
    trigger.className = "agy-usage-header-pill agy-usage-running";
    const popup = document.createElement("nav");
    popup.id = "agy-usage-running-" + crypto.randomUUID();
    popup.setAttribute("data-agy-usage-popup", "");
    popup.setAttribute("data-agy-usage-running-list", "");
    popup.setAttribute("popover", "manual");
    trigger.setAttribute("aria-controls", popup.id);
    trigger.setAttribute("aria-expanded", "false");
    const title = document.createElement("header");
    title.id = popup.id + "-title";
    popup.setAttribute("aria-labelledby", title.id);
    const groups = Array.from({length: 2}, () => {
      const section = document.createElement("section");
      const heading = document.createElement("h3");
      const list = document.createElement("ul");
      section.append(heading, list);
      return {section, heading, list};
    });
    const empty = document.createElement("p");
    empty.textContent = "没有待查看的已完成会话";
    popup.append(title, ...groups.map(group => group.section), empty);
    control.append(trigger, popup);
    const events = new AbortController();
    const listen = {signal: events.signal};
    let timer;
    let previous = "";
    const opened = () => popup.matches(":popover-open");
    const pinned = () => overlay?.owner === trigger && overlay.fixed;

    function close() {
      clearTimeout(timer);
      if (opened()) popup.hidePopover();
      trigger.setAttribute("aria-expanded", "false");
      releaseOverlay(trigger);
    }
    function open(fixed = false) {
      clearTimeout(timer);
      if (!control.isConnected || control.hidden || !claimOverlay(trigger, close, fixed)) return;
      if (!opened()) popup.showPopover();
      trigger.setAttribute("aria-expanded", "true");
      positionBelow(trigger, popup);
    }
    function closeSoon() {
      clearTimeout(timer);
      timer = setTimeout(() => {
        if (!control.matches(":hover, :focus-within")) close();
      }, 230);
    }
    trigger.addEventListener("pointerenter", event => {
      if (event.pointerType === "touch") return;
      clearTimeout(timer);
      if (!opened()) timer = setTimeout(open, 180);
    });
    trigger.addEventListener("focus", () => {
      if (!opened() && trigger.matches(":focus-visible")) open();
    });
    trigger.addEventListener("click", () => { if (pinned()) close(); else open(true); });
    control.addEventListener("pointerleave", closeSoon);
    popup.addEventListener("pointerenter", () => clearTimeout(timer));
    control.addEventListener("focusout", closeSoon);
    document.addEventListener("pointerdown", event => {
      if (!control.contains(event.target)) close();
    }, {...listen, capture: true});
    document.addEventListener("keydown", event => {
      if (event.key !== "Escape" || !opened()) return;
      if (popup.contains(document.activeElement)) trigger.focus();
      close();
      event.preventDefault();
      event.stopPropagation();
    }, {...listen, capture: true});
    window.addEventListener("resize", () => { if (opened()) positionBelow(trigger, popup); }, listen);
    popup.addEventListener("click", event => {
      const link = event.target.closest("a[data-conversation]");
      if (!link || event.button !== 0 || event.metaKey || event.ctrlKey || event.altKey || event.shiftKey) return;
      const native = document.querySelector('[data-testid="conversation-row-sidebar"] a[href="' + link.getAttribute("href") + '"]');
      const router = window.__TSR_ROUTER__;
      close();
      if (native) {
        event.preventDefault();
        native.click();
      } else if (typeof router?.navigate === "function" && router.routesByPath?.["/c/$cascadeId"]) {
        event.preventDefault();
        router.navigate({to:"/c/$cascadeId",params:{cascadeId:link.dataset.conversation}});
      }
    });

    control.update = (activity, current) => {
      control.hidden = !activity;
      if (control.hidden) {
        close();
        return;
      }
      const {running, completed} = activity;
      const key = JSON.stringify([running, completed, current]);
      if (key === previous) return;
      previous = key;
      const hasRunning = running.length > 0;
      const hasCompleted = completed.length > 0;
      const mixed = hasRunning && hasCompleted;
      const attention = completed.some(item => item.outcome !== "success");
      const completionLabel = attention ? "已结束 · 未查看" : "已完成 · 未查看";
      const label = hasRunning ? "运行中" : attention ? "待查看" : "已完成";
      trigger.textContent = label + " " + value(hasRunning ? running.length : completed.length);
      if (hasRunning) trigger.removeAttribute("aria-description");
      else trigger.setAttribute("aria-description", completionLabel);
      trigger.toggleAttribute("data-active", hasRunning || hasCompleted);
      trigger.toggleAttribute("data-completed", !hasRunning && !attention);
      trigger.toggleAttribute("data-attention", !hasRunning && attention);
      title.textContent = mixed ? "会话状态" : hasRunning ? "运行中的会话" : completionLabel;
      empty.hidden = hasRunning || hasCompleted;
      const focusedId = popup.contains(document.activeElement) && document.activeElement.dataset.conversation;
      let focusedLink;
      [running, completed].forEach((items, index) => {
        const {section, heading, list} = groups[index];
        section.hidden = !items.length;
        heading.hidden = !mixed;
        heading.textContent = (index === 0 ? "运行中" : completionLabel) + " " + value(items.length);
        list.replaceChildren(...items.map(({id, title, background, outcome}) => {
          const row = document.createElement("li");
          const link = document.createElement("a");
          link.href = "/c/" + id;
          link.dataset.conversation = id;
          if (id === current) link.setAttribute("aria-current", "page");
          const name = document.createElement("span");
          name.textContent = title;
          name.title = title;
          link.append(name);
          const outcomeLabel = {error:"运行异常", canceled:"已取消", interrupted:"已中断", limit:"达到限制"}[outcome];
          if (background || outcomeLabel) {
            const status = document.createElement("span");
            status.textContent = outcomeLabel || "后台任务";
            if (outcomeLabel) status.className = "agy-usage-activity-outcome";
            link.append(status);
          }
          if (id === focusedId) focusedLink = link;
          row.append(link);
          return row;
        }));
      });
      if (focusedId && opened()) (focusedLink || trigger).focus({preventScroll: true});
      if (opened()) positionBelow(trigger, popup);
    };
    control.destroy = () => { close(); events.abort(); control.remove(); };
    return control;
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
      releaseOverlay(history);
    }
    function position() {
      positionBelow(history, preview);
    }
    function schedule() {
      clearTimeout(timer);
      if (opened() || overlay?.fixed || state.dismiss) return;
      timer = setTimeout(() => {
        if (!history.isConnected || !claimOverlay(history, close)) return;
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
      history.activityList?.destroy();
      (history.headerPills || []).forEach(button => {
        if (button.parentElement === history.parentElement) button.remove();
      });
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
        button.activityList = makeActivityList();
        launchers.set(toolbar, button);
      }
      if (button.parentElement !== anchor) anchor.insertBefore(button, more || anchor.firstChild);
    }
  }

  function unreadCompletions(completed) {
    const currentRevisions = new Map(completed.map(({id, revision}) => [id, revision]));
    for (const [id, revision] of viewedCompletions) {
      if (currentRevisions.get(id) !== revision) viewedCompletions.delete(id);
    }
    // The host also treats a conversation as viewed while its window has focus.
    if (completed.length && document.visibilityState === "visible" && document.hasFocus()) {
      for (const view of document.querySelectorAll('[data-testid="conversation-view"][data-cascade-id]')) {
        const id = view.dataset.cascadeId;
        if (currentRevisions.has(id) && view.getClientRects().length && getComputedStyle(view).visibility === "visible") {
          viewedCompletions.set(id, currentRevisions.get(id));
        }
      }
    }
    return completed.filter(({id, revision}) => viewedCompletions.get(id) !== revision);
  }

  function updateHeader(sessions, preview, activity) {
    const readingActivity = activity && {...activity, completed: unreadCompletions(activity.completed)};
    const usedInputs = new Set();
    for (const [toolbar, launcher] of launchers) {
      const id=conversationFor(toolbar);
      const reading = sessions.find(session => session.id === id);
      launcher.updatePreview(reading?.usage?.reference_usd, preview.week_usd, preview.rate?.rate);
      const entry=[...bars.entries()].find(([input])=>conversation(input)===id && !usedInputs.has(input));
      if (entry) usedInputs.add(entry[0]);
      const buttons = entry?.[1].usage.pills.slice(3, 5).map(pill => pill.button).filter(button => !button.hidden) || [];
      for (const previous of launcher.headerPills || []) {
        if (!buttons.includes(previous) && previous.parentElement === launcher.parentElement) previous.remove();
      }
      launcher.headerPills = buttons;
      if (buttons.some((button, index) => button.nextElementSibling !== (buttons[index + 1] || launcher))) {
        launcher.before(...buttons);
      }
      const first = buttons[0] || launcher;
      if (launcher.activityList.nextElementSibling !== first) first.before(launcher.activityList);
      launcher.activityList.update(readingActivity, id);
    }
  }

  function placeBar(input, slot) {
    const trigger = modelTrigger(input);
    const footer = trigger?.closest(".agy-composer-footer");
    if (!footer || !input.contains(footer)) {
      slot.remove();
      return;
    }
    const groups = [...footer.children].filter(element => element !== slot);
    if (groups.length === 2 && groups[0].contains(trigger)) {
      if (slot.parentElement !== footer || slot.nextElementSibling !== groups[1]) groups[1].before(slot);
    } else {
      slot.remove();
    }
  }

  state.probe = () => ({
    inputs: inputs().map(input => ({
      id: conversation(input), model: model(input), windowMode: modelTrigger(input)?.dataset.contextWindow || ""
    })),
    request: state.request,
    fileAction: state.fileAction || null
  });

  state.install = ({ html, css, theme }) => {
    if (state.open) return;
    styles.prepend(document.createTextNode(theme));
    state.open = (id, opener) => {
      overlay?.close();
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
        position: "fixed", inset: "0", zIndex: "1001",
        border: "1px solid var(--agy-usage-line)", borderRadius: "16px",
        background: "var(--agy-usage-bg)", color: "var(--agy-usage-ink)",
        boxShadow: "0 20px 64px #0003", overscrollBehavior: "contain"
      });
      const host = document.createElement("div");
      const root = host.attachShadow({mode: "open"});
      const sheet = new CSSStyleSheet();
      sheet.replaceSync(css);
      root.adoptedStyleSheets = [sheet];
      root.innerHTML = html;
      root.querySelector("#close-dashboard").hidden = false;
      root.querySelector("#close-dashboard").addEventListener("click", () => dialog.close());
      const onKey = event => { if (event.key === "Escape") dialog.close(); };
      document.addEventListener("keydown", onKey);
      dialog.append(host);
      const scrim = document.createElement("div");
      style(scrim, { position: "fixed", inset: "0", zIndex: "1000", background: "#00000052" });
      scrim.addEventListener("click", () => dialog.close());
      document.body.append(scrim, dialog);
      let view = null;
      const mount = () => {
        view = state.createDashboard(root, (filters, showCached) => {
          const cacheKey = JSON.stringify(filters);
          const cached = dashboardCache.get(cacheKey);
          const age = cached ? performance.now() - cached.time : Infinity;
          if (age < 5000 && !cached.report.fx.pending) return Promise.resolve(cached.report);
          if (age < 300000) showCached?.(cached.report);
          return new Promise((resolve, reject) => {
            pending = { resolve, reject, cacheKey };
            state.request = { epoch, id: ++serial, filters };
            timeout = setTimeout(() => {
              pending = undefined;
              state.request = null;
              reject(new Error("本机用量服务未响应，请刷新重试"));
            }, 25000);
          });
        }, id);
      };
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
        view?.dispose();
        clearTimeout(timeout);
        state.request = null;
        state.finish = null;
        state.dismiss = null;
        pending?.reject(new Error("已关闭"));
        document.removeEventListener("keydown", onKey);
        scrim.remove();
        dialog.remove();
        if (opener.isConnected) opener.focus();
      }, {once: true});
      dialog.show();
      mount();
    };
  };

  state.render = ({ sessions, quotas, completed, preview = {}, activity = null, fileResult = null }) => {
    if (completed) state.finish?.(completed);
    if (fileResult) state.finishFileAction?.(fileResult);
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
      const modelLimit = usage.model_limit ? value(usage.model_limit) + " Token" : "—";
      const limit = usage.context_limit || usage.model_limit;
      const windowLimit = limit ? capacity(limit) : "—";
      const context = usage.used != null
        ? compact.format(usage.used) + "/" + windowLimit
        : (usage.waiting_for_call ? (limit ? windowLimit + " · " : "") + "待读数" : windowLimit);
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
      const assign=(index,text,detail,short=text)=>{
        const pill=stats[index];
        if (pill.text.textContent!==text) {
          pill.text.textContent=text;
          const label = index === 4 ? detail.value : text;
          pill.button.setAttribute("aria-label",label&&label!=="—" ? pill.name+"："+label : pill.name);
        }
        if (index < 3 && pill.short.textContent !== short) pill.short.textContent = short;
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
        ]}, usage.turns == null ? "会话 —" : value(usage.turns)+"轮");
      assign(1, (usage.total == null ? "Token —" : compact.format(usage.total)+" tok")
        +(usage.hit == null ? "" : " · 命中 "+decimal(usage.hit)+"%"), {
        title:"Token 用量",value:value(usage.total)+" tok",rows:[
          ["缓存命中",usage.hit == null ? "暂无数据" : decimal(usage.hit)+"%"],
          ["未缓存输入",value(usage.uncached)+" tok"],["缓存读取",value(usage.cached)+" tok"],
          ["缓存写入",value(usage.written)+" tok"],["输出",value(usage.output)+" tok"]
        ]}, usage.total == null ? "Token —" : compact.format(usage.total));
      const contextReading = [context, contextPercent].filter(Boolean).join(" · ");
      assign(2,contextReading,{
        title:"上下文",value:contextPercent || windowLimit,progress:usage.context,progressTone:usage.context >= 100 ? "warn" : "",
        rows:[["已用（调用估算）",usage.used == null ? "—" : value(usage.used)+" Token"],
          [usage.used == null ? "所选模型窗口" : "计算采用的窗口",limit ? value(limit)+" Token" : "—"],
          ...(usage.clamped && usage.call_limit ? [["本次调用记录上限",value(usage.call_limit)+" Token"]] : []),
          ...(usage.model_limit && limit !== usage.model_limit ? [["模型目录上限",modelLimit]] : []),
          ...(windowMode ? [["所选上下文模式",windowMode]] : []),
          ...(usage.breakdown||[]).map(group=>[({"System Prompt":"系统提示词","Tools":"工具定义","Chat Messages":"对话消息"})[group.name]||group.name||"上下文分组","≈"+compact.format(group.tokens)])],
        note:usage.used == null ? (usage.waiting_for_call ? "等待该模型产生上下文记录" : "暂无上下文记录")
          : (usage.call_model ? "最近一次调用 · "+usage.call_model : "")
            + (usage.before_compaction ? " · 压缩前读数，等待压缩后的调用记录"
              : usage.stale ? " · 上一条有效读数，下一条记录到达后更新" : "")
            + (usage.clamped ? " · 超出记录上限，按已用量计" : "")
            + (usage.model_limit ? "" : " · 暂未读取到模型窗口")
      }, contextPercent || (usage.waiting_for_call ? "待读数" : context));
      const periods=[period("5h","5h"),period("weekly","周")].filter(Boolean);
      stats[3].button.hidden=!periods.length;
      assign(3,periods.map(item=>item[0]).join(" · "),{
        title:"剩余额度",value:quota?.group||"",rows:periods.map(item=>[item[0],item[1].split("北京时间 ")[1]||"暂无重置时间"])
      });
      const task = (reading?.id === conversation(input) && reading.usage?.task_progress) || {};
      const taskState = task.background ? "有后台活动"
        : {running:"进行中",waiting:"等待操作",idle:"会话空闲"}[task.state] || "状态未记录";
      const planned = task.total > 0;
      const progress = planned ? 100 * task.done / task.total : null;
      const percent = planned ? value(Math.floor(progress), "%") : null;
      const badge = stats[4].button;
      badge.hidden = !planned;
      badge.toggleAttribute("data-measured", planned);
      badge.toggleAttribute("data-complete", progress === 100);
      badge.style.setProperty("--agy-usage-fraction", planned ? progress + "%" : "0%");
      const phase = {planning:"规划或复核",implementing:"实施"}[task.last_phase];
      assign(4,percent ? "任务 " + percent : taskState,{
        title:"任务进度",value:percent || taskState,progress,progressLabel:"任务清单进度",progressTone:progress === 100 ? "good" : "",
        rows:[["会话状态",taskState],
          ...(phase ? [["最近一次模型调用",phase]] : []),
          ...(planned ? [["已完成",value(task.done)+" / "+value(task.total)+" 项"],["进行中",value(task.in_progress)+" 项"]] : [])],
        note:task.source === "native" ? "按本轮 Antigravity 原生任务清单统计。"
          : "按本轮计划文件中的任务清单统计。"
      });
      stats[5].button.detail = {
        title:"会话用量",value:"",rows:[
          ["会话统计",rounds],["最近输出速率",usage.speed == null ? "—" : decimal(usage.speed)+" tok/s"],
          ["Token 用量",value(usage.total)+" tok"],["缓存命中",usage.hit == null ? "—" : decimal(usage.hit)+"%"],
          ["上下文",contextReading || "—"]
        ]
      };
      bar.usage.layout();
      bar.usage.paint();
      const fraction = Math.max(0, Math.min(100, usage.context || 0));
      bar.usage.ring.toggleAttribute("data-warning", usage.context >= 100);
      bar.usage.ring.style.setProperty("--agy-usage-fraction", fraction + "%");
    });
    updateHeader(sessions, preview, activity);
  };
  state.remove = () => {
    state.dismiss?.();
    state.removeFileMenu?.();
    for (const bar of bars.values()) { bar.usage.destroy(); bar.remove(); }
    styles.remove();
    for (const button of launchers.values()) button.destroy();
    delete window[key];
  };
})();
