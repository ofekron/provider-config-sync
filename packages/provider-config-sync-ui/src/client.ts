import type {
  ProviderSyncApplyRequest,
  ProviderSyncAutoPolicy,
  ProviderSyncAutoOperation,
  ProviderSyncAutoOverrideMode,
  ProviderSyncAutoRequest,
  ProviderSyncAutoResponse,
  ProviderSyncAutoSettings,
  ProviderSyncAutoSettingsLevel,
  ProviderSyncCapabilityPickerResponse,
  ProviderSyncCapability,
  ProviderSyncCreateCapabilityRequest,
  ProviderSyncDeleteCapabilityRequest,
  ProviderSyncResponse,
  ProviderSyncRestoreRequest,
  ProviderSyncTransferCapabilityRequest,
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
  transferCapability(body: ProviderSyncTransferCapabilityRequest): Promise<ProviderSyncCreateCapabilityResponse>;
  apply(body: ProviderSyncApplyRequest): Promise<void>;
  autoSync(body: ProviderSyncAutoRequest): Promise<ProviderSyncAutoResponse>;
  listCapabilityPickerSources(cwd: string): Promise<ProviderSyncCapabilityPickerResponse>;
}

export interface FetchProviderSyncClientOptions {
  baseUrl: string;
  credentials?: RequestCredentials;
  routes?: ProviderSyncFetchRoutes;
}

export interface ProviderSyncFetchRoutes {
  projects: string;
  state: string;
  settings: string;
  file: string;
  restoreFile: string;
  capability: string;
  transferCapability: string;
  apply: string;
  autoSync: string;
  capabilityPicker: string;
}

export const PROVIDER_CONFIG_SYNC_ROUTES: ProviderSyncFetchRoutes = {
  projects: "/api/provider-config-sync/projects",
  state: "/api/provider-config-sync",
  settings: "/api/provider-config-sync/settings",
  file: "/api/provider-config-sync/file",
  restoreFile: "/api/provider-config-sync/file/restore",
  capability: "/api/provider-config-sync/capability",
  transferCapability: "/api/provider-config-sync/capability/transfer",
  apply: "/api/provider-config-sync/apply",
  autoSync: "/api/provider-config-sync/auto-sync",
  capabilityPicker: "/api/provider-config-sync/capability-picker",
};

export const BETTER_CLAUDE_PROVIDER_SYNC_ROUTES: ProviderSyncFetchRoutes = {
  projects: "/api/projects",
  state: "/api/provider-sync",
  settings: "/api/provider-sync/settings",
  file: "/api/provider-sync/file",
  restoreFile: "/api/provider-sync/file/restore",
  capability: "/api/provider-sync/capability",
  transferCapability: "/api/provider-sync/capability/transfer",
  apply: "/api/provider-sync/apply",
  autoSync: "/api/provider-sync/auto-sync",
  capabilityPicker: "/api/provider-sync/capability-picker",
};

function pathWithParams(path: string, params: URLSearchParams): string {
  const query = params.toString();
  return query ? `${path}?${query}` : path;
}

// Generic fetch client for the standalone provider-config-sync backend. Hosts
// with different route ownership inject a route map or their own client.
export function createFetchProviderSyncClient(
  options: FetchProviderSyncClientOptions,
): ProviderSyncApiClient {
  const { baseUrl, credentials = "include", routes = PROVIDER_CONFIG_SYNC_ROUTES } = options;

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
      request<{ projects?: ProviderSyncProject[] }>(routes.projects).then((b) => b.projects ?? []),
    getState: (cwd) => {
      const params = new URLSearchParams();
      if (cwd) params.set("cwd", cwd);
      return request<ProviderSyncResponse>(pathWithParams(routes.state, params));
    },
    updateAutoSettings: (body) =>
      request<ProviderSyncAutoSettings>(routes.settings, {
        method: "PATCH",
        body: json(body),
      }),
    writeFile: (body) =>
      request<void>(routes.file, { method: "PUT", body: json(body) }),
    restoreFile: (body) =>
      request<void>(routes.restoreFile, { method: "POST", body: json(body) }),
    deleteCapability: (body) =>
      request<void>(routes.capability, { method: "DELETE", body: json(body) }),
    createCapability: (body) =>
      request<ProviderSyncCreateCapabilityResponse>(routes.capability, {
        method: "POST",
        body: json(body),
      }),
    transferCapability: (body) =>
      request<ProviderSyncCreateCapabilityResponse>(routes.transferCapability, {
        method: "POST",
        body: json(body),
      }),
    apply: (body) =>
      request<void>(routes.apply, { method: "POST", body: json(body) }),
    autoSync: (body) =>
      request<ProviderSyncAutoResponse>(routes.autoSync, {
        method: "POST",
        body: json(body),
      }),
    listCapabilityPickerSources: (cwd) => {
      const params = new URLSearchParams();
      if (cwd) params.set("cwd", cwd);
      return request<ProviderSyncCapabilityPickerResponse>(pathWithParams(routes.capabilityPicker, params));
    },
  };
}

export function createBetterClaudeProviderSyncClient(
  options: Omit<FetchProviderSyncClientOptions, "routes">,
): ProviderSyncApiClient {
  return createFetchProviderSyncClient({
    ...options,
    routes: BETTER_CLAUDE_PROVIDER_SYNC_ROUTES,
  });
}
