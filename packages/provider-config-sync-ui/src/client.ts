import type {
  ProviderConfigSyncApplyRequest,
  ProviderConfigSyncAutoPolicy,
  ProviderConfigSyncAutoOperation,
  ProviderConfigSyncAutoOverrideMode,
  ProviderConfigSyncAutoRequest,
  ProviderConfigSyncAutoResponse,
  ProviderConfigSyncAutoSettings,
  ProviderConfigSyncAutoSettingsLevel,
  ProviderConfigSyncCapabilityPickerResponse,
  ProviderConfigSyncCapability,
  ProviderConfigSyncCreateCapabilityRequest,
  ProviderConfigSyncDeleteCapabilityRequest,
  ProviderConfigSyncResponse,
  ProviderConfigSyncRestoreRequest,
  ProviderConfigSyncRepositoryRequest,
  ProviderConfigSyncRepositoryStatus,
  ProviderConfigSyncTransferCapabilityRequest,
  ProviderConfigSyncWriteRequest,
} from "@better-agent/provider-config-sync-core";

// Minimal project shape the picker needs. Hosts with richer project objects
// can pass them in; only these fields are read.
export interface ProviderConfigSyncProject {
  path: string;
  node_id?: string;
  name: string;
}

export interface ProviderConfigSyncUpdateSettingsRequest {
  level: ProviderConfigSyncAutoSettingsLevel;
  cwd: string;
  capability_id: string;
  policy: ProviderConfigSyncAutoPolicy | Record<ProviderConfigSyncAutoOperation, ProviderConfigSyncAutoOverrideMode>;
}

export interface ProviderConfigSyncCreateCapabilityResponse {
  capability?: ProviderConfigSyncCapability;
}

// One method per provider-config-sync HTTP endpoint. The host injects an
// implementation; the UI never calls fetch directly.
export interface ProviderConfigSyncApiClient {
  listProjects(): Promise<ProviderConfigSyncProject[]>;
  getState(cwd: string): Promise<ProviderConfigSyncResponse>;
  updateAutoSettings(body: ProviderConfigSyncUpdateSettingsRequest): Promise<ProviderConfigSyncAutoSettings>;
  writeFile(body: ProviderConfigSyncWriteRequest): Promise<void>;
  restoreFile(body: ProviderConfigSyncRestoreRequest): Promise<void>;
  deleteCapability(body: ProviderConfigSyncDeleteCapabilityRequest): Promise<void>;
  createCapability(body: ProviderConfigSyncCreateCapabilityRequest): Promise<ProviderConfigSyncCreateCapabilityResponse>;
  transferCapability(body: ProviderConfigSyncTransferCapabilityRequest): Promise<ProviderConfigSyncCreateCapabilityResponse>;
  apply(body: ProviderConfigSyncApplyRequest): Promise<void>;
  autoSync(body: ProviderConfigSyncAutoRequest): Promise<ProviderConfigSyncAutoResponse>;
  listCapabilityPickerSources(cwd: string): Promise<ProviderConfigSyncCapabilityPickerResponse>;
  getRepositoryStatus(): Promise<ProviderConfigSyncRepositoryStatus>;
  initRepository(body: ProviderConfigSyncRepositoryRequest): Promise<ProviderConfigSyncRepositoryStatus>;
  loadRepository(body: ProviderConfigSyncRepositoryRequest): Promise<ProviderConfigSyncRepositoryStatus>;
  syncRepository(): Promise<ProviderConfigSyncRepositoryStatus>;
}

export interface FetchProviderConfigSyncClientOptions {
  baseUrl: string;
  credentials?: RequestCredentials;
  routes?: ProviderConfigSyncFetchRoutes;
}

export interface ProviderConfigSyncFetchRoutes {
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
  repository: string;
  repositoryInit: string;
  repositoryLoad: string;
  repositorySync: string;
}

export const PROVIDER_CONFIG_SYNC_ROUTES: ProviderConfigSyncFetchRoutes = {
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
  repository: "/api/provider-config-sync/repository",
  repositoryInit: "/api/provider-config-sync/repository/init",
  repositoryLoad: "/api/provider-config-sync/repository/load",
  repositorySync: "/api/provider-config-sync/repository/sync",
};

export const BETTER_AGENT_PROVIDER_CONFIG_SYNC_ROUTES: ProviderConfigSyncFetchRoutes = {
  projects: "/api/projects",
  state: "/api/provider-config-sync",
  settings: "/api/provider-config-sync/settings",
  file: "/api/provider-config-sync/file",
  restoreFile: "/api/provider-config-sync/file/restore",
  capability: "/api/provider-config-sync/capability",
  transferCapability: "/api/provider-config-sync/capability/transfer",
  apply: "/api/provider-config-sync/apply",
  autoSync: "/api/provider-config-sync/auto-sync",
  capabilityPicker: "/api/provider-config-sync/capability-picker",
  repository: "/api/provider-config-sync/repository",
  repositoryInit: "/api/provider-config-sync/repository/init",
  repositoryLoad: "/api/provider-config-sync/repository/load",
  repositorySync: "/api/provider-config-sync/repository/sync",
};

function pathWithParams(path: string, params: URLSearchParams): string {
  const query = params.toString();
  return query ? `${path}?${query}` : path;
}

// Generic fetch client for the standalone provider-config-sync backend. Hosts
// with different route ownership inject a route map or their own client.
export function createFetchProviderConfigSyncClient(
  options: FetchProviderConfigSyncClientOptions,
): ProviderConfigSyncApiClient {
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
      request<{ projects?: ProviderConfigSyncProject[] }>(routes.projects).then((b) => b.projects ?? []),
    getState: (cwd) => {
      const params = new URLSearchParams();
      if (cwd) params.set("cwd", cwd);
      return request<ProviderConfigSyncResponse>(pathWithParams(routes.state, params));
    },
    updateAutoSettings: (body) =>
      request<ProviderConfigSyncAutoSettings>(routes.settings, {
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
      request<ProviderConfigSyncCreateCapabilityResponse>(routes.capability, {
        method: "POST",
        body: json(body),
      }),
    transferCapability: (body) =>
      request<ProviderConfigSyncCreateCapabilityResponse>(routes.transferCapability, {
        method: "POST",
        body: json(body),
      }),
    apply: (body) =>
      request<void>(routes.apply, { method: "POST", body: json(body) }),
    autoSync: (body) =>
      request<ProviderConfigSyncAutoResponse>(routes.autoSync, {
        method: "POST",
        body: json(body),
      }),
    listCapabilityPickerSources: (cwd) => {
      const params = new URLSearchParams();
      if (cwd) params.set("cwd", cwd);
      return request<ProviderConfigSyncCapabilityPickerResponse>(pathWithParams(routes.capabilityPicker, params));
    },
    getRepositoryStatus: () =>
      request<ProviderConfigSyncRepositoryStatus>(routes.repository),
    initRepository: (body) =>
      request<ProviderConfigSyncRepositoryStatus>(routes.repositoryInit, {
        method: "POST",
        body: json(body),
      }),
    loadRepository: (body) =>
      request<ProviderConfigSyncRepositoryStatus>(routes.repositoryLoad, {
        method: "POST",
        body: json(body),
      }),
    syncRepository: () =>
      request<ProviderConfigSyncRepositoryStatus>(routes.repositorySync, {
        method: "POST",
      }),
  };
}

export function createBetterAgentProviderConfigSyncClient(
  options: Omit<FetchProviderConfigSyncClientOptions, "routes">,
): ProviderConfigSyncApiClient {
  return createFetchProviderConfigSyncClient({
    ...options,
    routes: BETTER_AGENT_PROVIDER_CONFIG_SYNC_ROUTES,
  });
}
