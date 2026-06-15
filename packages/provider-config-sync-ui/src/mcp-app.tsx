import { useEffect } from "react";
import { createRoot } from "react-dom/client";
import { ProviderSyncPage } from "./ProviderSyncPage.js";
import type {
  ProviderSyncApiClient,
  ProviderSyncProject,
  ProviderSyncUpdateSettingsRequest,
} from "./client.js";
import "./styles.css";

type PendingRequest = {
  resolve: (value: unknown) => void;
  reject: (error: Error) => void;
};

class McpHostBridge {
  private nextId = 1;
  private readonly pending = new Map<number, PendingRequest>();

  constructor() {
    window.addEventListener("message", (event) => this.onMessage(event));
  }

  request(method: string, params: unknown): Promise<unknown> {
    const id = this.nextId++;
    window.parent.postMessage({ jsonrpc: "2.0", id, method, params }, "*");
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      window.setTimeout(() => {
        if (!this.pending.has(id)) return;
        this.pending.delete(id);
        reject(new Error(`${method} timed out`));
      }, 30000);
    });
  }

  notify(method: string, params: unknown): void {
    window.parent.postMessage({ jsonrpc: "2.0", method, params }, "*");
  }

  resize(): void {
    this.notify("ui/notifications/size-changed", {
      height: document.documentElement.scrollHeight,
    });
  }

  async callTool<T>(name: string, args: Record<string, unknown> = {}): Promise<T> {
    const result = await this.request("tools/call", { name, arguments: args });
    if (isRecord(result) && "structuredContent" in result) {
      return result.structuredContent as T;
    }
    if (!isRecord(result) || !Array.isArray(result.content)) {
      return result as T;
    }
    const first = result.content[0];
    if (!isRecord(first) || typeof first.text !== "string") {
      return result as T;
    }
    return JSON.parse(first.text) as T;
  }

  private onMessage(event: MessageEvent): void {
    const data = event.data;
    if (!isRecord(data)) return;
    if (typeof data.id === "number" && this.pending.has(data.id)) {
      const pending = this.pending.get(data.id);
      this.pending.delete(data.id);
      if (!pending) return;
      if (data.error) {
        const message = isRecord(data.error) && typeof data.error.message === "string"
          ? data.error.message
          : String(data.error);
        pending.reject(new Error(message));
        return;
      }
      pending.resolve(data.result);
    }
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function createMcpProviderSyncClient(host: McpHostBridge): ProviderSyncApiClient {
  return {
    listProjects: () =>
      host.callTool<{ projects?: ProviderSyncProject[] }>("list_provider_config_projects")
        .then((body) => body.projects ?? []),
    getState: (cwd) => host.callTool("get_provider_config_state", { cwd }),
    updateAutoSettings: (body: ProviderSyncUpdateSettingsRequest) =>
      host.callTool("update_provider_config_auto_settings", body as unknown as Record<string, unknown>),
    writeFile: (body) =>
      host.callTool("write_provider_config_entry", body as unknown as Record<string, unknown>).then(() => undefined),
    restoreFile: (body) =>
      host.callTool("restore_provider_config_entry", body as unknown as Record<string, unknown>).then(() => undefined),
    deleteCapability: (body) =>
      host.callTool("delete_provider_config_capability", body as unknown as Record<string, unknown>).then(() => undefined),
    createCapability: (body) =>
      host.callTool("create_provider_config_capability", body as unknown as Record<string, unknown>),
    transferCapability: (body) =>
      host.callTool("transfer_provider_config_capability", body as unknown as Record<string, unknown>),
    apply: (body) =>
      host.callTool("apply_provider_config_entry", body as unknown as Record<string, unknown>).then(() => undefined),
    autoSync: (body) =>
      host.callTool("auto_sync_provider_config_entry", body as unknown as Record<string, unknown>),
    listCapabilityPickerSources: (cwd) =>
      host.callTool("list_provider_config_capability_picker", { cwd }),
  };
}

function McpProviderSyncApp({ host }: { host: McpHostBridge }) {
  useEffect(() => {
    const observer = new MutationObserver(() => host.resize());
    observer.observe(document.body, { childList: true, subtree: true, attributes: true });
    window.addEventListener("resize", () => host.resize());
    host.resize();
    return () => observer.disconnect();
  }, [host]);

  return (
    <ProviderSyncPage
      open
      cwd={null}
      onClose={() => undefined}
      client={createMcpProviderSyncClient(host)}
    />
  );
}

const rootElement = document.getElementById("root");
if (!rootElement) {
  throw new Error("MCP app root element is missing");
}

const host = new McpHostBridge();
createRoot(rootElement).render(<McpProviderSyncApp host={host} />);
