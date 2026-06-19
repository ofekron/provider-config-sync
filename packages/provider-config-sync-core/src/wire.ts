// Wire DTOs for the provider-config-sync HTTP API.
// Single source of truth for the request/response shapes shared by the
// backend, the UI package, and any host consumer.

export type ProviderConfigSyncScope = "global" | "project";
export type ProviderConfigSyncCategory = "instructions" | "memory" | "config" | "skill" | "agent" | "command";
export type ProviderConfigSyncRole = "unified" | "specific";

export interface ProviderConfigSyncFile {
  entry_id: string;
  path: string;
  content_kind: string;
  scope: ProviderConfigSyncScope;
  category: ProviderConfigSyncCategory;
  capability_id: string;
  capability_key: string;
  capability_name: string;
  role: ProviderConfigSyncRole;
  label: string;
  language: string;
  content: string;
  token_count: number;
  exists: boolean;
  read_error: string | null;
  writable: boolean;
  backup_exists: boolean;
  disabled: boolean;
  provider_names: string[];
  provider_kinds: string[];
}

export interface ProviderConfigSyncProviderTokenCount {
  provider_kind: string;
  provider_name: string;
  token_count: number;
}

export interface ProviderConfigSyncTokenTotals {
  unified: number;
  specifics: number;
  all_tracked: number;
  by_provider: ProviderConfigSyncProviderTokenCount[];
}

export interface ProviderConfigSyncCapability {
  id: string;
  capability_id: string;
  name: string;
  scope: ProviderConfigSyncScope;
  category: ProviderConfigSyncCategory;
  language: string;
  unified: ProviderConfigSyncFile;
  specifics: ProviderConfigSyncFile[];
  unified_token_count: number;
  specific_token_count: number;
  total_token_count: number;
  provider_token_counts: ProviderConfigSyncProviderTokenCount[];
  has_diffs: boolean;
  specific_count: number;
  missing_count: number;
}

export interface ProviderConfigSyncResponse {
  files: ProviderConfigSyncFile[];
  capabilities: ProviderConfigSyncCapability[];
  providers: { kind: string; name: string }[];
  token_totals: ProviderConfigSyncTokenTotals;
  groups: Record<ProviderConfigSyncScope, ProviderConfigSyncCapability[]>;
  auto_settings?: ProviderConfigSyncAutoSettings;
}

export interface ProviderConfigSyncWriteRequest {
  cwd: string;
  entry_id: string;
  path?: string;
  expected_content: string | null;
  content: string;
}

export interface ProviderConfigSyncRestoreRequest {
  cwd: string;
  entry_id: string;
  path?: string;
  expected_content: string | null;
}

export interface ProviderConfigSyncDeleteCapabilityRequest {
  cwd: string;
  scope: ProviderConfigSyncScope;
  capability_id: string;
  expected_contents: Record<string, string | null>;
}

export interface ProviderConfigSyncCreateCapabilityRequest {
  cwd: string;
  scope: ProviderConfigSyncScope;
  category: "skill" | "agent" | "command";
  provider_kinds: string[];
  name: string;
  description: string;
  instructions: string;
  metadata: Record<string, unknown>;
}

export interface ProviderConfigSyncTransferCapabilityRequest {
  cwd: string;
  scope: ProviderConfigSyncScope;
  capability_id: string;
  target_cwd: string;
  target_scope: ProviderConfigSyncScope;
  mode: "copy" | "move";
  expected_contents: Record<string, string | null>;
}

export interface ProviderConfigSyncApplyRequest {
  cwd: string;
  capability_id: string;
  source_entry_id: string;
  target_entry_id: string;
  source_path?: string;
  target_path?: string;
  expected_source: string;
  expected_target: string | null;
}

export type ProviderConfigSyncAutoMode = "off" | "auto" | "review" | "llm";
export type ProviderConfigSyncAutoOperation = "additive" | "removal" | "change";

export interface ProviderConfigSyncAutoPolicy {
  additive: ProviderConfigSyncAutoMode;
  removal: ProviderConfigSyncAutoMode;
  change: ProviderConfigSyncAutoMode;
}

export type ProviderConfigSyncAutoOverrideMode = ProviderConfigSyncAutoMode | "inherit";
export type ProviderConfigSyncAutoSettingsLevel = "global" | "capability" | "project" | "project_capability";
export type ProviderConfigSyncAutoOverridePolicy = Partial<Record<ProviderConfigSyncAutoOperation, ProviderConfigSyncAutoMode>>;

export interface ProviderConfigSyncAutoProjectSettings {
  policy?: ProviderConfigSyncAutoOverridePolicy;
  capabilities?: Record<string, ProviderConfigSyncAutoOverridePolicy>;
}

export interface ProviderConfigSyncAutoSettings {
  global: ProviderConfigSyncAutoPolicy;
  capabilities: Record<string, ProviderConfigSyncAutoOverridePolicy>;
  projects: Record<string, ProviderConfigSyncAutoProjectSettings>;
  effective: ProviderConfigSyncAutoPolicy;
}

export interface ProviderConfigSyncAutoRequest {
  cwd: string;
  capability_id: string;
  source_entry_id: string;
  target_entry_id: string;
  expected_source: string;
  expected_target: string | null;
  policy: ProviderConfigSyncAutoPolicy;
  approved_hunk_ids?: string[];
  llm_hunk_ids?: string[];
}

export interface ProviderConfigSyncAutoLogItem {
  hunk_id: string;
  operation: ProviderConfigSyncAutoOperation;
  mode: ProviderConfigSyncAutoMode;
  status: "applied" | "pending" | "skipped";
  row_count: number;
  preview: string;
}

export interface ProviderConfigSyncAutoResponse {
  ok: boolean;
  source_entry_id: string;
  target_entry_id: string;
  source_path: string;
  target_path: string;
  target_side: "unified" | "specific";
  applied_count: number;
  pending_count: number;
  skipped_count: number;
  log_head: ProviderConfigSyncAutoLogItem[];
}

export interface ProviderConfigSyncCapabilityPickerSource {
  source_id: string;
  source_scope: ProviderConfigSyncScope;
  source_cwd: string;
  source_label: string;
  capability: ProviderConfigSyncCapability;
  preferred_entry: ProviderConfigSyncFile | null;
  outputs: ProviderConfigSyncCapabilityPickerOutput[];
}

export interface ProviderConfigSyncCapabilityPickerOutput {
  provider_kind: string;
  provider_name: string;
  entry_id: string;
  path: string;
  label: string;
  content_kind: string;
  language: string;
  content: string;
  token_count: number;
  render_error: string | null;
}

export interface ProviderConfigSyncCapabilityPickerResponse {
  sources: ProviderConfigSyncCapabilityPickerSource[];
}

export interface ProviderConfigSyncRepositoryStatus {
  enabled: boolean;
  auto_apply: boolean;
  remote_url: string;
  checkout_path: string;
  checkout_exists: boolean;
  last_synced_at?: string;
  last_error?: string;
  apply?: { updated: number; considered: number };
}

export interface ProviderConfigSyncRepositoryRequest {
  remote_url: string;
  auto_apply: boolean;
}
