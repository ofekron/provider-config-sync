import type {
  ProviderSyncApplyRequest,
  ProviderSyncAutoPolicy,
  ProviderSyncAutoOperation,
  ProviderSyncAutoOverrideMode,
  ProviderSyncAutoRequest,
  ProviderSyncAutoResponse,
  ProviderSyncAutoSettings,
  ProviderSyncAutoSettingsLevel,
  ProviderSyncCapability,
  ProviderSyncCreateCapabilityRequest,
  ProviderSyncDeleteCapabilityRequest,
  ProviderSyncResponse,
  ProviderSyncRestoreRequest,
  ProviderSyncWriteRequest,
} from "@better-agent/provider-config-sync-core";

// Minimal project shape the picker needs. Hosts with richer project objects
// can pass them in; only these fields are read.
export interface ProviderSyncProject {
  path: string;
  node_id?: string;
  name: string;
}

export interface ProviderSyncUpdateSettingsRequest {
  level: ProviderSyncAutoSettingsLevel;
  cwd: string;
  capability_id: string;
  policy: ProviderSyncAutoPolicy | Record<ProviderSyncAutoOperation, ProviderSyncAutoOverrideMode>;
}

export interface ProviderSyncCreateCapabilityResponse {
  capability?: ProviderSyncCapability;
}

// One method per provider-config-sync HTTP endpoint. The host injects an
// implementation; the UI never calls fetch directly.
export interface ProviderSyncApiClient {
  listProjects(): Promise<ProviderSyncProject[]>;
  getState(cwd: string): Promise<ProviderSyncResponse>;
  updateAutoSettings(body: ProviderSyncUpdateSettingsRequest): Promise<ProviderSyncAutoSettings>;
  writeFile(body: ProviderSyncWriteRequest): Promise<void>;
  restoreFile(body: ProviderSyncRestoreRequest): Promise<void>;
  deleteCapability(body: ProviderSyncDeleteCapabilityRequest): Promise<void>;
  createCapability(body: ProviderSyncCreateCapabilityRequest): Promise<ProviderSyncCreateCapabilityResponse>;
  apply(body: ProviderSyncApplyRequest): Promise<void>;
  autoSync(body: ProviderSyncAutoRequest): Promise<ProviderSyncAutoResponse>;
}

export interface FetchProviderSyncClientOptions {
  baseUrl: string;
  credentials?: RequestCredentials;
}

// Default client reproducing the original better-claude behavior: cookie auth,
// JSON bodies, errors surfaced as `detail` (or `HTTP {status}`).
export function createFetchProviderSyncClient(
  options: FetchProviderSyncClientOptions,
): ProviderSyncApiClient {
  const { baseUrl, credentials = "include" } = options;

  async function request<T>(path: string, init?: RequestInit): Promise<T> {
    const response = await fetch(`${baseUrl}${path}`, {
      credentials,
      headers: { "Content-Type": "application/json" },
      ...init,
    });
    if (!response.ok) {
      const body = await response.json().catch(() => null);
      throw new Error(body?.detail ?? `HTTP ${response.status}`);
    }
    const text = await response.text();
    return (text ? (JSON.parse(text) as T) : (undefined as T));
  }

  const json = (body: unknown) => JSON.stringify(body);

  return {
    listProjects: () =>
      request<{ projects?: ProviderSyncProject[] }>("/api/projects").then((b) => b.projects ?? []),
    getState: (cwd) => {
      const params = new URLSearchParams();
      if (cwd) params.set("cwd", cwd);
      return request<ProviderSyncResponse>(`/api/provider-sync?${params.toString()}`);
    },
    updateAutoSettings: (body) =>
      request<ProviderSyncAutoSettings>("/api/provider-sync/settings", {
        method: "PATCH",
        body: json(body),
      }),
    writeFile: (body) =>
      request<void>("/api/provider-sync/file", { method: "PUT", body: json(body) }),
    restoreFile: (body) =>
      request<void>("/api/provider-sync/file/restore", { method: "POST", body: json(body) }),
    deleteCapability: (body) =>
      request<void>("/api/provider-sync/capability", { method: "DELETE", body: json(body) }),
    createCapability: (body) =>
      request<ProviderSyncCreateCapabilityResponse>("/api/provider-sync/capability", {
        method: "POST",
        body: json(body),
      }),
    apply: (body) =>
      request<void>("/api/provider-sync/apply", { method: "POST", body: json(body) }),
    autoSync: (body) =>
      request<ProviderSyncAutoResponse>("/api/provider-sync/auto-sync", {
        method: "POST",
        body: json(body),
      }),
  };
}
