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
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font: 13px/1.4 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
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
    .shell { display: grid; grid-template-rows: auto 1fr; min-height: 520px; }
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
    .main {
      display: grid;
      grid-template-columns: 310px minmax(0, 1fr);
      min-height: 0;
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
    .status { min-height: 20px; color: var(--muted); }
    .error { color: var(--bad); }
    @media (max-width: 760px) {
      header { grid-template-columns: 1fr; }
      .main, .grid { grid-template-columns: 1fr; }
      aside { border-right: 0; border-bottom: 1px solid var(--line); max-height: 320px; }
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
                <button id="reset">Reset edits</button>
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
    const state = { cwd: "", payload: null, capability: null, entries: [], entry: null, original: "" };
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

    function renderList() {
      const capabilities = (state.payload && state.payload.capabilities) || [];
      $("capabilities").innerHTML = "";
      for (const capability of capabilities) {
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
        $("capabilities").appendChild(button);
      }
      app.resize();
    }

    function renderCapability() {
      const capability = state.capability;
      $("entrySelect").innerHTML = "";
      $("targets").innerHTML = "";
      if (!capability) return;
      $("selectedName").textContent = capability.name;
      $("selectedMeta").textContent = capability.scope + " / " + capability.category + " / " + tokens(capability.total_token_count) + " tokens";
      state.entries = [capability.unified].concat(capability.specifics || []);
      for (const entry of state.entries) {
        const option = document.createElement("option");
        option.value = entry.entry_id;
        option.textContent = entryTitle(entry) + " (" + tokens(entry.token_count) + " tokens)";
        $("entrySelect").appendChild(option);
      }
      selectEntry(state.entry && state.entry.entry_id || state.entries[0].entry_id);
    }

    function renderTargets() {
      const source = state.entry;
      const dirty = source && $("content").value !== state.original;
      $("sourceLabel").textContent = source ? entryTitle(source) : "";
      $("targets").innerHTML = "";
      if (!source || !state.capability) return;
      for (const target of state.entries) {
        if (target.entry_id === source.entry_id) continue;
        const button = document.createElement("button");
        button.className = "entry" + (target.exists ? "" : " missing");
        button.innerHTML = "<strong></strong><span class='meta'></span><span class='meta'></span>";
        button.children[0].textContent = "Apply to " + entryTitle(target);
        button.children[1].textContent = target.path;
        button.children[2].textContent = dirty ? "Save source before applying" : target.exists ? tokens(target.token_count) + " tokens" : "new file";
        button.disabled = dirty || !target.writable || !source.exists;
        button.onclick = () => applyTo(target);
        $("targets").appendChild(button);
      }
      app.resize();
    }

    async function load() {
      state.cwd = $("cwd").value.trim();
      setStatus("Loading...", false);
      try {
        state.payload = await app.callTool("list_provider_config_capabilities", { cwd: state.cwd });
        const totals = state.payload.token_totals || {};
        $("unifiedTokens").textContent = tokens(totals.unified);
        $("specificTokens").textContent = tokens(totals.specifics);
        $("totalTokens").textContent = tokens(totals.total);
        state.capability = (state.payload.capabilities || [])[0] || null;
        state.entry = null;
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

    async function applyTo(target) {
      if (!state.capability || !state.entry) return;
      setStatus("Applying...", false);
      try {
        const targetFull = target.exists
          ? await app.callTool("read_provider_config_entry", { cwd: state.cwd, entry_id: target.entry_id })
          : null;
        await app.callTool("apply_provider_config_entry", {
          cwd: state.cwd,
          capability_id: state.capability.capability_id,
          source_entry_id: state.entry.entry_id,
          target_entry_id: target.entry_id,
          expected_source: $("content").value,
          expected_target: targetFull ? targetFull.content : null
        });
        await load();
        setStatus("Applied.", false);
      } catch (error) {
        setStatus(error.message, true);
      }
    }

    $("load").onclick = load;
    $("reload").onclick = load;
    $("save").onclick = save;
    $("reset").onclick = () => { $("content").value = state.original; renderTargets(); };
    $("content").oninput = renderTargets;
    $("entrySelect").onchange = event => selectEntry(event.target.value);
    app.init().then(load);
  </script>
</body>
</html>"""
