(() => {
  const state = window.__agyUsageStatus;
  const native = window.electronNative;
  if (!state || state.removeFileMenu || typeof native?.showContextMenu !== "function") return;

  const events = new AbortController();
  const drivePath = /^[A-Za-z]:[\\/]/;
  const linkSelector = 'a[href], button[data-file-uri], [role="link"][data-file-uri]';
  const referenceSelector = 'button[data-uri], [role="button"][data-uri]';
  const scopeSelector = '[data-testid="conversation-view"], [data-testid="overview-panel-host"]';
  let pending;
  let actionId = 0;
  let menuId = 0;
  let toast;
  let toastTimer;

  function fileLink(href) {
    if (!drivePath.test(href) && !/^file:\/\//i.test(href)) return null;
    try {
      const url = new URL(drivePath.test(href) ? "file:///" + href.replaceAll("\\", "/") : href);
      if (url.host) return null;
      let path = decodeURIComponent(url.pathname).replace(/^\/([a-z]:\/)/i, "$1");
      if (drivePath.test(path)) {
        const line = path.match(/:(\d+)(?::\d+)?$/);
        if (line) path = path.slice(0, line.index);
        return {path, fragment: url.hash.slice(1) || (line ? "L" + line[1] : "")};
      }
    } catch {
      return null;
    }
    return null;
  }

  function showError(message) {
    clearTimeout(toastTimer);
    if (!toast) {
      toast = document.createElement("div");
      toast.setAttribute("role", "status");
      toast.style.cssText = "position:fixed;inset:auto 20px 20px auto;z-index:2000;padding:10px 14px;"
        + "border:1px solid var(--agy-usage-line);border-radius:8px;color:var(--agy-usage-ink);"
        + "background:var(--agy-usage-panel);box-shadow:var(--agy-usage-shadow)";
    }
    if (!toast.isConnected) document.body.append(toast);
    toast.textContent = message;
    toastTimer = setTimeout(() => toast.remove(), 4500);
  }

  function request(action, file) {
    if (pending) return Promise.resolve({error: "请先完成上一个文件操作。"});
    return new Promise(resolve => {
      const id = ++actionId;
      pending = {id, resolve};
      state.fileAction = {id, action, ...file};
    });
  }

  state.finishFileAction = result => {
    if (result.id !== pending?.id) return;
    const {resolve} = pending;
    pending = undefined;
    state.fileAction = null;
    resolve(result);
  };

  async function copyText(text) {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      showError("无法写入剪贴板，请确认 Antigravity 窗口处于前台。");
    }
  }

  async function run(action, file) {
    const result = await request(action, file);
    if (events.signal.aborted) return;
    if (result.error) showError(result.error);
    else if (action === "copy") await copyText(result.text);
  }

  async function openMenu(event) {
    if (!event.isTrusted || !(event.target instanceof Element)) return;
    const element = event.target.closest(`${linkSelector}, ${referenceSelector}`);
    if (!element || element.closest('[contenteditable="true"]')) return;
    const scope = element.closest(scopeSelector);
    const href = element.getAttribute("data-file-uri") || element.getAttribute("href") ||
      (scope && element.getAttribute("data-uri")) || "";
    const file = fileLink(href);
    if (file ? window.sshRemote?.kind === "ssh" : !scope || !/^https?:\/\//i.test(href) || !URL.canParse(href)) return;
    if (!file) {
      const url = new URL(href);
      if (url.origin === location.origin || url.username || url.password) return;
    }
    event.preventDefault();
    event.stopImmediatePropagation();
    const id = ++menuId;
    const actions = new Map();
    const item = (name, label, select) => {
      actions.set(name, select);
      return {id: name, type: "normal", label};
    };
    const separator = name => ({id: name, type: "separator"});
    const preview = item("preview", "在 Antigravity 中打开", () => { if (element.isConnected) element.click(); });
    let items;
    try {
      if (file) {
        const info = await request("inspect", file);
        if (id !== menuId || !element.isConnected || events.signal.aborted) return;
        const openWith = [
          info.open && item("default", "默认应用", () => run("open", file)),
          info.choose && item("choose", "选择其他应用…", () => run("choose", file)),
        ].filter(Boolean);
        const path = info.path || file.path.replaceAll("/", "\\");
        items = [
          preview,
          openWith.length && {id: "open-with", type: "submenu", label: "打开方式", submenu: openWith},
          info.exists && typeof native.revealInFilePicker === "function" &&
            item("reveal", "在文件资源管理器中显示", () =>
              native.revealInFilePicker(new URL("file:///" + path.replaceAll("\\", "/").split("/")
                .map((part, i) => i ? encodeURIComponent(part) : part).join("/")).href)),
          info.github && typeof native.openExternal === "function" &&
            item("github", "在 GitHub 中打开", () => native.openExternal(info.github)),
          separator("file-actions"),
          info.save && item("save", "另存为…", () => run("save", file)),
          item("copy-path", "复制路径", () => copyText(path)),
          info.copy && item("copy-contents", "复制文件内容", () => run("copy", file)),
        ].filter(Boolean);
      } else {
        items = [
          preview,
          typeof native.openExternal === "function" &&
            item("external", "在外部浏览器中打开", () => native.openExternal(href)),
          separator("web-actions"),
          item("copy-link", "复制链接地址", () => copyText(href)),
        ].filter(Boolean);
      }
      const selected = await native.showContextMenu(items);
      if (!events.signal.aborted && id === menuId) await actions.get(selected)?.();
    } catch {
      if (!events.signal.aborted) showError("无法打开右键菜单或完成所选操作。");
    }
  }

  state.removeFileMenu = () => {
    events.abort();
    if (pending) state.finishFileAction({id: pending.id, error: "文件操作已结束。"});
    clearTimeout(toastTimer);
    toast?.remove();
  };
  window.addEventListener("contextmenu", openMenu, {capture: true, signal: events.signal});
})();
