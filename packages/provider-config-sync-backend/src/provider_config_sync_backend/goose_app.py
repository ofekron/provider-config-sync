from __future__ import annotations

GOOSE_APP_URI = "ui://provider-config-sync/main"
GOOSE_APP_MIME_TYPE = "text/html;profile=mcp-app"


def goose_app_html() -> str:
    return r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Provider Config Sync</title>
  <style>
    :root {
      color-scheme: dark light;
      --bg: #111318;
      --panel: #181b22;
      --panel-2: #20242d;
      --line: #343a46;
      --text: #eef1f6;
      --muted: #9aa3b2;
      --accent: #6aa8ff;
      --good: #56d364;
      --warn: #f2cc60;
      --bad: #ff7b72;
    }
    * { box-sizing: border-box; }
    html, body { min-height: 100%; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font: 13px/1.4 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      overflow: hidden;
    }
    body.light {
      --bg: #f6f7f9;
      --panel: #ffffff;
      --panel-2: #edf0f5;
      --line: #d8dde6;
      --text: #161b22;
      --muted: #59636f;
      --accent: #0969da;
    }
    .shell {
      display: grid;
      grid-template-rows: auto minmax(0, 1fr);
      height: 100dvh;
      min-height: 520px;
    }
    header {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 10px;
      align-items: end;
      padding: 12px;
      border-bottom: 1px solid var(--line);
      background: var(--panel);
    }
    h1 { margin: 0 0 6px; font-size: 16px; font-weight: 650; }
    label { display: block; margin-bottom: 4px; color: var(--muted); font-size: 11px; text-transform: uppercase; }
    input, textarea, select, button {
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--panel-2);
      color: var(--text);
      font: inherit;
      min-width: 0;
    }
    input, textarea, select { width: 100%; padding: 8px; }
    textarea {
      min-height: 260px;
      resize: vertical;
      font: 12px/1.45 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      white-space: pre;
    }
    button {
      padding: 8px 10px;
      cursor: pointer;
      white-space: nowrap;
    }
    button.primary { background: var(--accent); border-color: var(--accent); color: white; }
    button:disabled { cursor: not-allowed; opacity: .5; }
    button.icon {
      display: inline-grid;
      place-items: center;
      width: 34px;
      height: 34px;
      padding: 0;
      font-size: 16px;
      line-height: 1;
    }
    .main {
      display: grid;
      grid-template-columns: 310px minmax(0, 1fr);
      min-height: 0;
      overflow: hidden;
    }
    aside {
      min-height: 0;
      overflow: auto;
      border-right: 1px solid var(--line);
      background: var(--panel);
    }
    .content {
      min-width: 0;
      min-height: 0;
      overflow: auto;
      padding: 12px;
    }
    .stats {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 8px;
      padding: 10px;
      border-bottom: 1px solid var(--line);
    }
    .stat { padding: 8px; border: 1px solid var(--line); border-radius: 6px; background: var(--panel-2); }
    .stat b { display: block; font-size: 16px; }
    .list { padding: 8px; display: grid; gap: 6px; }
    .cap-group { display: grid; gap: 6px; }
    .cap-group-head {
      display: grid;
      grid-template-columns: 18px minmax(0, 1fr) auto;
      gap: 6px;
      align-items: center;
      width: 100%;
      padding: 6px 8px;
      border: 0;
      background: transparent;
      color: var(--muted);
      text-align: left;
      text-transform: uppercase;
      font-size: 11px;
    }
    .cap-group-head b {
      color: var(--text);
      font-size: 12px;
      text-transform: none;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .cap-group-count {
      min-width: 20px;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 1px 6px;
      text-align: center;
      color: var(--muted);
    }
    .cap-group-items { display: grid; gap: 6px; }
    .cap-group.collapsed .cap-group-items { display: none; }
    .cap {
      display: grid;
      gap: 4px;
      width: 100%;
      text-align: left;
      padding: 9px;
      border: 1px solid var(--line);
      background: var(--panel-2);
    }
    .cap.active { border-color: var(--accent); }
    .meta { color: var(--muted); font-size: 12px; overflow-wrap: anywhere; }
    .pill { display: inline-block; margin-right: 5px; color: var(--muted); }
    .pill.diff { color: var(--warn); }
    .pill.ok { color: var(--good); }
    .grid { display: grid; grid-template-columns: minmax(0, 1fr) minmax(260px, 360px); gap: 12px; align-items: start; }
    .create-grid { display: grid; grid-template-columns: repeat(4, minmax(120px, 1fr)); gap: 8px; align-items: end; }
    .create-grid .wide { grid-column: span 2; }
    .create-grid .full { grid-column: 1 / -1; }
    .create-grid textarea { min-height: 92px; }
    .provider-checks {
      display: grid;
      gap: 6px;
      padding: 8px;
      border: 1px solid var(--border);
      border-radius: 8px;
      background: rgba(8, 13, 24, .35);
    }
    .provider-checks label {
      min-width: 0;
      display: flex;
      align-items: center;
      gap: 8px;
      color: var(--muted);
      font-size: 12px;
    }
    .provider-checks input { width: auto; margin: 0; }
    .panel { border: 1px solid var(--line); border-radius: 8px; background: var(--panel); overflow: hidden; }
    .panel-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      padding: 10px;
      border-bottom: 1px solid var(--line);
    }
    .panel-body { padding: 10px; display: grid; gap: 10px; }
    .entries { display: grid; gap: 6px; }
    .entry {
      display: grid;
      gap: 4px;
      padding: 8px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--panel-2);
      text-align: left;
    }
    .entry.active { border-color: var(--accent); }
    .entry.missing { opacity: .75; }
    .row { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
    .toolbar { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; justify-content: flex-end; }
    .status { min-height: 20px; color: var(--muted); }
    .error { color: var(--bad); }
    .compare { margin-top: 12px; display: grid; gap: 12px; }
    .compare-card { border: 1px solid var(--line); border-radius: 8px; background: var(--panel); overflow: hidden; }
    .compare-head {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 10px;
      align-items: center;
      padding: 10px;
      border-bottom: 1px solid var(--line);
    }
    .diff-grid {
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
      min-width: 720px;
      overflow: hidden;
    }
    .diff-side { min-width: 0; border-right: 1px solid var(--line); }
    .diff-side:last-child { border-right: 0; }
    .diff-title {
      display: flex;
      justify-content: space-between;
      gap: 8px;
      padding: 8px 10px;
      border-bottom: 1px solid var(--line);
      background: var(--panel-2);
      color: var(--muted);
      font-size: 12px;
    }
    .diff-line {
      display: grid;
      grid-template-columns: 44px minmax(0, 1fr);
      min-height: 22px;
      font: 12px/1.45 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      border-bottom: 1px solid color-mix(in srgb, var(--line), transparent 45%);
    }
    .diff-line:last-child { border-bottom: 0; }
    .empty-state {
      min-height: 58px;
      display: grid;
      place-items: center;
      padding: 12px;
      color: var(--muted);
      border-bottom: 1px solid var(--line);
      background: color-mix(in srgb, var(--panel-2), transparent 35%);
      font-size: 12px;
    }
    .ln { padding: 2px 8px; color: var(--muted); text-align: right; user-select: none; }
    .code { padding: 2px 8px; white-space: pre-wrap; overflow-wrap: anywhere; }
    .same .code { color: var(--muted); }
    .add .code { background: color-mix(in srgb, var(--good), transparent 82%); }
    .remove .code { background: color-mix(in srgb, var(--bad), transparent 84%); }
    @media (max-width: 760px) {
      body { overflow: auto; }
      .shell {
        display: block;
        height: auto;
        min-height: 100dvh;
      }
      header {
        grid-template-columns: 1fr;
        gap: 8px;
        padding: 10px;
      }
      header button { width: 100%; }
      .main {
        display: block;
        overflow: visible;
      }
      aside {
        border-right: 0;
        border-bottom: 1px solid var(--line);
        overflow: visible;
      }
      .stats {
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 6px;
        padding: 8px;
      }
      .stat { padding: 7px; }
      .stat b { font-size: 14px; }
      .list {
        display: flex;
        gap: 8px;
        overflow-x: auto;
        padding: 8px;
        scroll-snap-type: x proximity;
        -webkit-overflow-scrolling: touch;
      }
      .cap {
        min-width: min(78vw, 280px);
        scroll-snap-align: start;
      }
      .cap-group {
        min-width: min(78vw, 280px);
        scroll-snap-align: start;
      }
      .cap-group .cap { min-width: 0; }
      .content {
        overflow: visible;
        padding: 10px;
      }
      .grid {
        grid-template-columns: 1fr;
        gap: 10px;
      }
      .create-grid { grid-template-columns: 1fr; }
      .create-grid .wide,
      .create-grid .full { grid-column: auto; }
      .panel-head {
        align-items: flex-start;
        flex-wrap: wrap;
      }
      .panel-head > div { min-width: 0; }
      .panel-head button { width: 100%; }
      textarea {
        min-height: 220px;
        max-height: 52dvh;
      }
      .entries { gap: 8px; }
      .row {
        display: grid;
        grid-template-columns: 1fr 1fr;
      }
      .row button { width: 100%; }
      .toolbar { justify-content: flex-start; }
      .toolbar button { width: 34px; }
      .compare-head { grid-template-columns: 1fr; }
      .compare { overflow-x: auto; }
    }
    @media (max-width: 420px) {
      h1 { font-size: 15px; }
      .cap { min-width: 86vw; }
      .row { grid-template-columns: 1fr; }
      button { white-space: normal; }
    }
    @media (max-width: 340px) {
      .stats { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <div class="shell">
    <header>
      <div>
        <h1>Provider Config Sync</h1>
        <label for="cwd">Project path</label>
        <input id="cwd" placeholder="/absolute/path/to/project">
      </div>
      <button class="primary" id="load">Load</button>
    </header>
    <div class="main">
      <aside>
        <div class="stats">
          <div class="stat"><span class="meta">Unified</span><b id="unifiedTokens">0</b></div>
          <div class="stat"><span class="meta">Specific</span><b id="specificTokens">0</b></div>
          <div class="stat"><span class="meta">Total</span><b id="totalTokens">0</b></div>
        </div>
        <div class="list" id="capabilities"></div>
      </aside>
      <main class="content">
        <section class="panel">
          <div class="panel-head">
            <strong>Add capability</strong>
          </div>
          <div class="panel-body">
            <div class="create-grid">
              <div>
                <label for="newScope">Scope</label>
                <select id="newScope">
                  <option value="project">Project</option>
                  <option value="global">Global</option>
                </select>
              </div>
              <div>
                <label for="newCategory">Type</label>
                <select id="newCategory">
                  <option value="command">Command</option>
                  <option value="skill">Skill</option>
                  <option value="agent">Agent</option>
                </select>
              </div>
              <div>
                <label>Providers</label>
                <div class="provider-checks" id="newProviders"></div>
              </div>
              <div>
                <button class="primary" id="createCapability">Add</button>
              </div>
              <div class="wide">
                <label for="newName">Name</label>
                <input id="newName" placeholder="review">
              </div>
              <div class="wide">
                <label for="newDescription">Description</label>
                <input id="newDescription" placeholder="Review code">
              </div>
              <div class="full">
                <label for="newInstructions">Instructions</label>
                <textarea id="newInstructions" spellcheck="false" placeholder="Write the capability instructions here."></textarea>
              </div>
              <div class="full">
                <label for="newMetadata">Metadata JSON</label>
                <textarea id="newMetadata" spellcheck="false">{}</textarea>
              </div>
            </div>
          </div>
        </section>
        <div class="grid">
          <section class="panel">
            <div class="panel-head">
              <div>
                <strong id="selectedName">No capability selected</strong>
                <div class="meta" id="selectedMeta"></div>
              </div>
              <button id="reload">Reload</button>
            </div>
            <div class="panel-body">
              <label for="entrySelect">File</label>
              <select id="entrySelect"></select>
              <textarea id="content" spellcheck="false"></textarea>
              <div class="row">
                <button class="primary" id="save">Save file</button>
                <button class="icon" id="reset" title="Reset edits" aria-label="Reset edits">↩</button>
              </div>
              <div class="status" id="status"></div>
            </div>
          </section>
          <section class="panel">
            <div class="panel-head">
              <strong>Apply</strong>
              <span class="meta" id="sourceLabel"></span>
            </div>
            <div class="panel-body">
              <div class="entries" id="targets"></div>
            </div>
          </section>
        </div>
        <section class="compare" id="compare"></section>
      </main>
    </div>
  </div>
  <script>
    class McpApp {
      constructor() {
        this.nextId = 1;
        this.pending = new Map();
        window.addEventListener("message", event => this.onMessage(event));
      }
      async init() {
        try {
          const result = await this.request("ui/initialize", {
            appCapabilities: {},
            clientInfo: { name: "provider-config-sync-goose-app", version: "1.0.0" },
            protocolVersion: "2026-01-26"
          });
          const theme = result && result.hostContext && result.hostContext.theme;
          if (theme) document.body.className = theme;
          this.notify("ui/notifications/initialized", {});
          this.resize();
        } catch (_) {
          this.resize();
        }
      }
      onMessage(event) {
        const data = event.data;
        if (!data || typeof data !== "object") return;
        if (Object.prototype.hasOwnProperty.call(data, "id") && this.pending.has(data.id)) {
          const pending = this.pending.get(data.id);
          this.pending.delete(data.id);
          if (data.error) pending.reject(new Error(data.error.message || String(data.error)));
          else pending.resolve(data.result);
          return;
        }
        if (data.method === "ui/notifications/host-context-changed" && data.params && data.params.theme) {
          document.body.className = data.params.theme;
        }
      }
      request(method, params) {
        const id = this.nextId++;
        window.parent.postMessage({ jsonrpc: "2.0", id, method, params }, "*");
        return new Promise((resolve, reject) => {
          this.pending.set(id, { resolve, reject });
          setTimeout(() => {
            if (!this.pending.has(id)) return;
            this.pending.delete(id);
            reject(new Error(method + " timed out"));
          }, 30000);
        });
      }
      notify(method, params) {
        window.parent.postMessage({ jsonrpc: "2.0", method, params }, "*");
      }
      resize() {
        this.notify("ui/notifications/size-changed", { height: document.documentElement.scrollHeight });
      }
      async callTool(name, args) {
        const result = await this.request("tools/call", { name, arguments: args || {} });
        if (result && result.structuredContent) return result.structuredContent;
        const text = result && result.content && result.content[0] && result.content[0].text;
        if (!text) return result;
        return JSON.parse(text);
      }
    }

    const app = new McpApp();
    const state = { cwd: "", payload: null, capability: null, entries: [], entry: null, original: "", entryContents: new Map(), collapsedGroups: new Set() };
    const $ = id => document.getElementById(id);

    function setStatus(message, isError) {
      $("status").textContent = message || "";
      $("status").className = isError ? "status error" : "status";
      app.resize();
    }

    function tokens(value) {
      return Number(value || 0).toLocaleString();
    }

    function entryTitle(entry) {
      const providers = (entry.provider_names || []).join(", ");
      return (providers || entry.role) + " - " + entry.label;
    }

    function groupTitle(capability) {
      return capability.scope + " / " + capability.category;
    }

    function iconButton(symbol, label, onClick, disabled) {
      const button = document.createElement("button");
      button.className = "icon";
      button.title = label;
      button.setAttribute("aria-label", label);
      button.textContent = symbol;
      button.disabled = !!disabled;
      button.onclick = onClick;
      return button;
    }

    async function readEntry(entry) {
      if (state.entryContents.has(entry.entry_id)) return state.entryContents.get(entry.entry_id);
      if (!entry.exists) {
        state.entryContents.set(entry.entry_id, "");
        return "";
      }
      const full = await app.callTool("read_provider_config_entry", { cwd: state.cwd, entry_id: entry.entry_id });
      state.entryContents.set(entry.entry_id, full.content || "");
      if (state.entry && state.entry.entry_id === full.entry_id) state.entry = full;
      return full.content || "";
    }

    function splitLines(content) {
      if (!content) return [];
      const lines = content.split("\n");
      if (lines.length && lines[lines.length - 1] === "") lines.pop();
      return lines;
    }

    function diffRows(unifiedContent, specificContent) {
      const unified = splitLines(unifiedContent);
      const specific = splitLines(specificContent);
      const dp = Array.from({ length: unified.length + 1 }, () => Array(specific.length + 1).fill(0));
      for (let i = unified.length - 1; i >= 0; i -= 1) {
        for (let j = specific.length - 1; j >= 0; j -= 1) {
          dp[i][j] = unified[i] === specific[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
        }
      }
      const rows = [];
      let i = 0;
      let j = 0;
      while (i < unified.length || j < specific.length) {
        if (i < unified.length && j < specific.length && unified[i] === specific[j]) {
          rows.push({ kind: "same", unifiedLine: i + 1, specificLine: j + 1, unifiedText: unified[i], specificText: specific[j] });
          i += 1;
          j += 1;
        } else if (j >= specific.length || (i < unified.length && dp[i + 1][j] >= dp[i][j + 1])) {
          rows.push({ kind: "remove", unifiedLine: i + 1, specificLine: "", unifiedText: unified[i], specificText: "" });
          i += 1;
        } else {
          rows.push({ kind: "add", unifiedLine: "", specificLine: j + 1, unifiedText: "", specificText: specific[j] });
          j += 1;
        }
      }
      return rows.length ? rows : [{ kind: "same", unifiedLine: "", specificLine: "", unifiedText: "", specificText: "" }];
    }

    function renderDiffSide(rows, side) {
      const fragment = document.createDocumentFragment();
      for (const row of rows) {
        const line = document.createElement("div");
        const text = side === "unified" ? row.unifiedText : row.specificText;
        const lineNumber = side === "unified" ? row.unifiedLine : row.specificLine;
        line.className = "diff-line " + (text ? row.kind : row.kind === "same" ? "same" : "remove");
        line.innerHTML = "<span class='ln'></span><span class='code'></span>";
        line.children[0].textContent = lineNumber;
        line.children[1].textContent = text;
        fragment.appendChild(line);
      }
      return fragment;
    }

    function emptyState(message) {
      const element = document.createElement("div");
      element.className = "empty-state";
      element.textContent = message;
      return element;
    }

    async function renderCompare() {
      const container = $("compare");
      container.innerHTML = "";
      if (!state.capability) return;
      const unified = state.capability.unified;
      const specifics = state.capability.specifics || [];
      const unifiedContent = await readEntry(unified);
      for (const specific of specifics) {
        const specificContent = await readEntry(specific);
        const rows = diffRows(unifiedContent, specificContent);
        const card = document.createElement("article");
        card.className = "compare-card";
        card.innerHTML =
          "<div class='compare-head'><div><strong></strong><div class='meta'></div></div><div class='toolbar'></div></div>" +
          "<div class='diff-grid'><div class='diff-side'><div class='diff-title'><span>Unified</span><span></span></div></div>" +
          "<div class='diff-side'><div class='diff-title'><span>Specific</span><span></span></div></div></div>";
        card.querySelector("strong").textContent = entryTitle(specific);
        card.querySelector(".meta").textContent = specific.path;
        const toolbar = card.querySelector(".toolbar");
        const dirty = state.entry && $("content").value !== state.original;
        toolbar.appendChild(iconButton("→", "Apply unified to specific", () => applyPair(unified, specific), dirty || !specific.writable || !unified.exists));
        toolbar.appendChild(iconButton("←", "Apply specific to unified", () => applyPair(specific, unified), dirty || !unified.writable || !specific.exists));
        toolbar.appendChild(iconButton("✦", "AI auto-merge into specific", () => aiMergePair(unified, specific), dirty || !specific.writable || !unified.exists));
        const sides = card.querySelectorAll(".diff-side");
        card.querySelectorAll(".diff-title span")[1].textContent = unified.exists ? tokens(unified.token_count) + " tokens" : "missing";
        card.querySelectorAll(".diff-title span")[3].textContent = specific.exists ? tokens(specific.token_count) + " tokens" : "missing";
        if (!unifiedContent) sides[0].appendChild(emptyState(unified.exists ? "Unified is empty" : "Unified is missing"));
        if (!specificContent) sides[1].appendChild(emptyState(specific.exists ? "Specific is empty" : "Specific is missing"));
        sides[0].appendChild(renderDiffSide(rows, "unified"));
        sides[1].appendChild(renderDiffSide(rows, "specific"));
        container.appendChild(card);
      }
      app.resize();
    }

    function renderList() {
      const capabilities = (state.payload && state.payload.capabilities) || [];
      $("capabilities").innerHTML = "";
      const groups = new Map();
      for (const capability of capabilities) {
        const key = groupTitle(capability);
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(capability);
      }
      for (const [key, items] of groups) {
        const group = document.createElement("section");
        const collapsed = state.collapsedGroups.has(key);
        group.className = "cap-group" + (collapsed ? " collapsed" : "");
        group.innerHTML = "<button class='cap-group-head'><span></span><b></b><span class='cap-group-count'></span></button><div class='cap-group-items'></div>";
        group.querySelector(".cap-group-head span").textContent = collapsed ? "▸" : "▾";
        group.querySelector("b").textContent = key;
        group.querySelector(".cap-group-count").textContent = items.length;
        group.querySelector(".cap-group-head").onclick = () => {
          if (state.collapsedGroups.has(key)) state.collapsedGroups.delete(key);
          else state.collapsedGroups.add(key);
          renderList();
        };
        const body = group.querySelector(".cap-group-items");
        for (const capability of items) {
          const button = document.createElement("button");
          button.className = "cap" + (state.capability && state.capability.id === capability.id ? " active" : "");
          button.innerHTML = "<strong></strong><span class='meta'></span><span></span>";
          button.children[0].textContent = capability.name;
          button.children[1].textContent = capability.scope + " / " + capability.category;
          button.children[2].innerHTML =
            "<span class='pill " + (capability.has_diffs ? "diff" : "ok") + "'>" + (capability.has_diffs ? "diff" : "aligned") + "</span>" +
            "<span class='pill'>" + tokens(capability.total_token_count) + " tokens</span>" +
            "<span class='pill'>" + capability.specific_count + " files</span>";
          button.onclick = () => selectCapability(capability.id);
          body.appendChild(button);
        }
        $("capabilities").appendChild(group);
      }
      app.resize();
    }

    function renderProviders() {
      const providers = (state.payload && state.payload.providers) || [];
      const selected = new Set([...$("newProviders").querySelectorAll("input:checked")].map(input => input.value));
      $("newProviders").innerHTML = "";
      for (const provider of providers) {
        const label = document.createElement("label");
        const input = document.createElement("input");
        input.type = "checkbox";
        input.value = provider.kind;
        input.checked = selected.size === 0 || selected.has(provider.kind);
        const text = document.createElement("span");
        text.textContent = provider.name + " (" + provider.kind + ")";
        label.appendChild(input);
        label.appendChild(text);
        $("newProviders").appendChild(label);
      }
    }

    function renderCapability() {
      const capability = state.capability;
      $("entrySelect").innerHTML = "";
      $("targets").innerHTML = "";
      if (!capability) return;
      $("selectedName").textContent = capability.name;
      $("selectedMeta").textContent = capability.scope + " / " + capability.category + " / " + tokens(capability.total_token_count) + " tokens";
      state.entries = [capability.unified].concat(capability.specifics || []);
      state.entryContents = new Map();
      for (const entry of state.entries) {
        const option = document.createElement("option");
        option.value = entry.entry_id;
        option.textContent = entryTitle(entry) + " (" + tokens(entry.token_count) + " tokens)";
        $("entrySelect").appendChild(option);
      }
      selectEntry(state.entry && state.entry.entry_id || state.entries[0].entry_id);
      renderCompare().catch(error => setStatus(error.message, true));
    }

    function renderTargets() {
      const source = state.entry;
      const dirty = source && $("content").value !== state.original;
      $("sourceLabel").textContent = source ? entryTitle(source) : "";
      $("targets").innerHTML = "";
      if (!source || !state.capability) return;
      for (const target of state.entries) {
        if (target.entry_id === source.entry_id) continue;
        const item = document.createElement("div");
        item.className = "entry" + (target.exists ? "" : " missing");
        item.innerHTML = "<strong></strong><span class='meta'></span><span class='meta'></span><div class='toolbar'></div>";
        item.children[0].textContent = entryTitle(target);
        item.children[1].textContent = target.path;
        item.children[2].textContent = dirty ? "Save source before applying" : target.exists ? tokens(target.token_count) + " tokens" : "new file";
        item.querySelector(".toolbar").appendChild(iconButton("→", "Apply selected file to target", () => applyPair(source, target), dirty || !target.writable || !source.exists));
        if (source.role !== "unified" && target.role === "unified") {
          item.querySelector(".toolbar").appendChild(iconButton("✦", "AI auto-merge selected file into unified", () => aiMergePair(source, target), dirty || !target.writable || !source.exists));
        }
        $("targets").appendChild(item);
      }
      renderCompare().catch(error => setStatus(error.message, true));
      app.resize();
    }

    async function load() {
      state.cwd = $("cwd").value.trim();
      setStatus("Loading...", false);
      try {
        state.payload = await app.callTool("list_provider_config_capabilities", { cwd: state.cwd });
        state.entryContents = new Map();
        const totals = state.payload.token_totals || {};
        $("unifiedTokens").textContent = tokens(totals.unified);
        $("specificTokens").textContent = tokens(totals.specifics);
        $("totalTokens").textContent = tokens(totals.all_tracked);
        state.capability = (state.payload.capabilities || [])[0] || null;
        state.entry = null;
        renderProviders();
        renderList();
        renderCapability();
        setStatus(state.capability ? "" : "No capabilities found.", false);
      } catch (error) {
        setStatus(error.message, true);
      }
    }

    function selectCapability(id) {
      state.capability = (state.payload.capabilities || []).find(item => item.id === id);
      state.entry = null;
      renderList();
      renderCapability();
    }

    async function selectEntry(entryId) {
      const summary = state.entries.find(entry => entry.entry_id === entryId);
      if (!summary) return;
      $("entrySelect").value = entryId;
      setStatus("Reading...", false);
      try {
        state.entry = await app.callTool("read_provider_config_entry", { cwd: state.cwd, entry_id: entryId });
        state.original = state.entry.content || "";
        state.entryContents.set(entryId, state.original);
        $("content").value = state.original;
        renderTargets();
        setStatus("", false);
      } catch (error) {
        state.entry = summary;
        state.original = "";
        $("content").value = "";
        renderTargets();
        setStatus(error.message, true);
      }
    }

    async function save() {
      if (!state.entry) return;
      setStatus("Saving...", false);
      try {
        await app.callTool("write_provider_config_entry", {
          cwd: state.cwd,
          entry_id: state.entry.entry_id,
          expected_content: state.original,
          content: $("content").value
        });
        await load();
        setStatus("Saved.", false);
      } catch (error) {
        setStatus(error.message, true);
      }
    }

    async function applyPair(source, target) {
      if (!state.capability || !source || !target) return;
      setStatus("Applying...", false);
      try {
        const sourceContent = state.entry && source.entry_id === state.entry.entry_id ? $("content").value : await readEntry(source);
        const targetContent = target.exists ? await readEntry(target) : null;
        await app.callTool("apply_provider_config_entry", {
          cwd: state.cwd,
          capability_id: state.capability.capability_id,
          source_entry_id: source.entry_id,
          target_entry_id: target.entry_id,
          expected_source: sourceContent,
          expected_target: targetContent
        });
        await load();
        setStatus("Applied.", false);
      } catch (error) {
        setStatus(error.message, true);
      }
    }

    async function aiMergePair(source, target) {
      if (!state.capability || !source || !target) return;
      setStatus("AI merging...", false);
      try {
        const sourceContent = state.entry && source.entry_id === state.entry.entry_id ? $("content").value : await readEntry(source);
        const targetContent = target.exists ? await readEntry(target) : null;
        const result = await app.callTool("auto_sync_provider_config_entry", {
          cwd: state.cwd,
          capability_id: state.capability.capability_id,
          source_entry_id: source.entry_id,
          target_entry_id: target.entry_id,
          expected_source: sourceContent,
          expected_target: targetContent,
          policy: { additive: "llm", removal: "llm", change: "llm" }
        });
        await load();
        setStatus("AI merged " + result.applied_count + " hunks; " + result.pending_count + " pending.", false);
      } catch (error) {
        setStatus(error.message, true);
      }
    }

    async function createCapability() {
      setStatus("Creating capability...", false);
      try {
        const metadataText = $("newMetadata").value.trim() || "{}";
        const metadata = JSON.parse(metadataText);
        if (!metadata || Array.isArray(metadata) || typeof metadata !== "object") throw new Error("Metadata JSON must be an object.");
        const result = await app.callTool("create_provider_config_capability", {
          cwd: state.cwd,
          scope: $("newScope").value,
          category: $("newCategory").value,
          provider_kinds: [...$("newProviders").querySelectorAll("input:checked")].map(input => input.value),
          name: $("newName").value.trim(),
          description: $("newDescription").value,
          instructions: $("newInstructions").value,
          metadata
        });
        await load();
        if (result && result.capability) selectCapability(result.capability.id);
        $("newName").value = "";
        $("newDescription").value = "";
        $("newInstructions").value = "";
        $("newMetadata").value = "{}";
        setStatus("Capability added.", false);
      } catch (error) {
        setStatus(error.message, true);
      }
    }

    $("load").onclick = load;
    $("reload").onclick = load;
    $("save").onclick = save;
    $("createCapability").onclick = createCapability;
    $("reset").onclick = () => { $("content").value = state.original; if (state.entry) state.entryContents.set(state.entry.entry_id, state.original); renderTargets(); };
    $("content").oninput = renderTargets;
    $("entrySelect").onchange = event => selectEntry(event.target.value);
    window.addEventListener("resize", () => app.resize());
    app.init().then(load);
  </script>
</body>
</html>"""
