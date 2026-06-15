// Wire DTOs for the provider-config-sync HTTP API.
// Single source of truth for the request/response shapes shared by the
// backend, the UI package, and any host consumer.

export type ProviderSyncScope = "global" | "project";
export type ProviderSyncCategory = "instructions" | "memory" | "config" | "skill" | "agent" | "command";
export type ProviderSyncRole = "unified" | "specific";

export interface ProviderSyncFile {
  entry_id: string;
  path: string;
  content_kind: string;
  scope: ProviderSyncScope;
  category: ProviderSyncCategory;
  capability_id: string;
  capability_key: string;
  capability_name: string;
  role: ProviderSyncRole;
  label: string;
  language: string;
  content: string;
  token_count: number;
  exists: boolean;
  read_error: string | null;
  writable: boolean;
  backup_exists: boolean;
  provider_names: string[];
  provider_kinds: string[];
}

export interface ProviderSyncProviderTokenCount {
  provider_kind: string;
  provider_name: string;
  token_count: number;
}

export interface ProviderSyncTokenTotals {
  unified: number;
  specifics: number;
  all_tracked: number;
  by_provider: ProviderSyncProviderTokenCount[];
}

export interface ProviderSyncCapability {
  id: string;
  capability_id: string;
  name: string;
  scope: ProviderSyncScope;
  category: ProviderSyncCategory;
  language: string;
  unified: ProviderSyncFile;
  specifics: ProviderSyncFile[];
  unified_token_count: number;
  specific_token_count: number;
  total_token_count: number;
  provider_token_counts: ProviderSyncProviderTokenCount[];
  has_diffs: boolean;
  specific_count: number;
  missing_count: number;
}

export interface ProviderSyncResponse {
  files: ProviderSyncFile[];
  capabilities: ProviderSyncCapability[];
  providers: { kind: string; name: string }[];
  token_totals: ProviderSyncTokenTotals;
  groups: Record<ProviderSyncScope, ProviderSyncCapability[]>;
  auto_settings?: ProviderSyncAutoSettings;
}

export interface ProviderSyncWriteRequest {
  cwd: string;
  entry_id: string;
  path?: string;
  expected_content: string | null;
  content: string;
}

export interface ProviderSyncRestoreRequest {
  cwd: string;
  entry_id: string;
  path?: string;
  expected_content: string | null;
}

export interface ProviderSyncDeleteCapabilityRequest {
  cwd: string;
  scope: ProviderSyncScope;
  capability_id: string;
  expected_contents: Record<string, string | null>;
}

export interface ProviderSyncCreateCapabilityRequest {
  cwd: string;
  scope: ProviderSyncScope;
  category: "skill" | "agent" | "command";
  provider_kinds: string[];
  name: string;
  description: string;
  instructions: string;
  metadata: Record<string, unknown>;
}

export interface ProviderSyncTransferCapabilityRequest {
  cwd: string;
  scope: ProviderSyncScope;
  capability_id: string;
  target_cwd: string;
  target_scope: ProviderSyncScope;
  mode: "copy" | "move";
  expected_contents: Record<string, string | null>;
}

export interface ProviderSyncApplyRequest {
  cwd: string;
  capability_id: string;
  source_entry_id: string;
  target_entry_id: string;
  source_path?: string;
  target_path?: string;
  expected_source: string;
  expected_target: string | null;
}

export type ProviderSyncAutoMode = "off" | "auto" | "review" | "llm";
export type ProviderSyncAutoOperation = "additive" | "removal" | "change";

export interface ProviderSyncAutoPolicy {
  additive: ProviderSyncAutoMode;
  removal: ProviderSyncAutoMode;
  change: ProviderSyncAutoMode;
}

export type ProviderSyncAutoOverrideMode = ProviderSyncAutoMode | "inherit";
export type ProviderSyncAutoSettingsLevel = "global" | "capability" | "project" | "project_capability";
export type ProviderSyncAutoOverridePolicy = Partial<Record<ProviderSyncAutoOperation, ProviderSyncAutoMode>>;

export interface ProviderSyncAutoProjectSettings {
  policy?: ProviderSyncAutoOverridePolicy;
  capabilities?: Record<string, ProviderSyncAutoOverridePolicy>;
}

export interface ProviderSyncAutoSettings {
  global: ProviderSyncAutoPolicy;
  capabilities: Record<string, ProviderSyncAutoOverridePolicy>;
  projects: Record<string, ProviderSyncAutoProjectSettings>;
  effective: ProviderSyncAutoPolicy;
}

export interface ProviderSyncAutoRequest {
  cwd: string;
  capability_id: string;
  source_entry_id: string;
  target_entry_id: string;
  expected_source: string;
  expected_target: string | null;
  policy: ProviderSyncAutoPolicy;
  approved_hunk_ids?: string[];
  llm_hunk_ids?: string[];
}

export interface ProviderSyncAutoLogItem {
  hunk_id: string;
  operation: ProviderSyncAutoOperation;
  mode: ProviderSyncAutoMode;
  status: "applied" | "pending" | "skipped";
  row_count: number;
  preview: string;
}

export interface ProviderSyncAutoResponse {
  ok: boolean;
  source_entry_id: string;
  target_entry_id: string;
  source_path: string;
  target_path: string;
  target_side: "unified" | "specific";
  applied_count: number;
  pending_count: number;
  skipped_count: number;
  log_head: ProviderSyncAutoLogItem[];
}

export interface ProviderSyncCapabilityPickerSource {
  source_id: string;
  source_scope: ProviderSyncScope;
  source_cwd: string;
  source_label: string;
  capability: ProviderSyncCapability;
  preferred_entry: ProviderSyncFile | null;
}

export interface ProviderSyncCapabilityPickerResponse {
  sources: ProviderSyncCapabilityPickerSource[];
}
