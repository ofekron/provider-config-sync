import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  applyRowsToContent,
  buildAlignedDiffRows,
  buildDiffHunks,
  replaceLine,
  type AlignedDiffRow,
} from "@better-agent/provider-config-sync-core/diff";
import {
  parseCommonItemDraft,
  parseMcpServers,
  stringifyCommonItemDraft,
  type CommonItemDraft,
  type McpServerDraft,
} from "@better-agent/provider-config-sync-core/items";
import type {
  ProviderConfigSyncApplyRequest,
  ProviderConfigSyncAutoMode,
  ProviderConfigSyncAutoOperation,
  ProviderConfigSyncAutoOverrideMode,
  ProviderConfigSyncAutoPolicy,
  ProviderConfigSyncAutoRequest,
  ProviderConfigSyncAutoResponse,
  ProviderConfigSyncAutoSettings,
  ProviderConfigSyncAutoSettingsLevel,
  ProviderConfigSyncFile,
  ProviderConfigSyncCapability,
  ProviderConfigSyncCreateCapabilityRequest,
  ProviderConfigSyncDeleteCapabilityRequest,
  ProviderConfigSyncResponse,
  ProviderConfigSyncRestoreRequest,
  ProviderConfigSyncScope,
  ProviderConfigSyncTransferCapabilityRequest,
  ProviderConfigSyncWriteRequest,
} from "@better-agent/provider-config-sync-core";
import { type ProviderConfigSyncApiClient, type ProviderConfigSyncProject } from "./client.js";

export interface ProviderConfigSyncPageProps {
  open: boolean;
  cwd: string | null;
  onClose: () => void;
  client: ProviderConfigSyncApiClient;
  subscribeExternalChanges?: (cb: () => void) => () => void;
  /** Pre-select this capability once data loads. Used when the page is
   *  embedded inline (agent `open_config_panel` tool) so it opens focused
   *  on the relevant capability without a user click. */
  initialCapabilityId?: string;
  /** Embedded (inline/panel) mode: the host provides chrome + close, so
   *  hide the page's own topbar Close button and constrain height. */
  embedded?: boolean;
}

const SCOPES: ProviderConfigSyncScope[] = ["global", "project"];
const CATEGORY_LABELS: Record<string, string> = {
  instructions: "Instructions",
  memory: "Memory",
  config: "Provider settings",
  skill: "Skills",
  agent: "Subagents",
  command: "Commands",
};
const AUTO_OPERATIONS: { id: ProviderConfigSyncAutoOperation; label: string }[] = [
  { id: "additive", label: "Additive" },
  { id: "removal", label: "Removal" },
  { id: "change", label: "Edit" },
];
const AUTO_MODES: { id: ProviderConfigSyncAutoMode; label: string }[] = [
  { id: "off", label: "Off" },
  { id: "auto", label: "Auto" },
  { id: "review", label: "Review per hunk" },
  { id: "llm", label: "LLM review" },
];
const CREATE_CAPABILITY_CATEGORIES = [
  { id: "skill", label: "Skill" },
  { id: "agent", label: "Subagent" },
  { id: "command", label: "Command" },
] as const;
type CreateCapabilityCategory = (typeof CREATE_CAPABILITY_CATEGORIES)[number]["id"];
const COMMON_ITEM_FIELDS: Array<{ field: keyof CommonItemDraft; label: string }> = [
  { field: "name", label: "Name" },
  { field: "description", label: "Description" },
  { field: "instructions", label: "Instructions" },
  { field: "metadata", label: "Provider extensions" },
];
const DEFAULT_AUTO_POLICY: ProviderConfigSyncAutoPolicy = {
  additive: "off",
  removal: "off",
  change: "off",
};
const LLM_AUTO_POLICY: ProviderConfigSyncAutoPolicy = {
  additive: "llm",
  removal: "llm",
  change: "llm",
};

function effectiveAutoPolicy(
  settings: ProviderConfigSyncAutoSettings | undefined,
  cwd: string,
  capabilityId: string | undefined,
): ProviderConfigSyncAutoPolicy {
  const policy = { ...DEFAULT_AUTO_POLICY, ...(settings?.global ?? {}) };
  if (capabilityId) {
    Object.assign(policy, settings?.capabilities?.[capabilityId] ?? {});
  }
  if (cwd) {
    const project = settings?.projects?.[cwd];
    Object.assign(policy, project?.policy ?? {});
    if (capabilityId) {
      Object.assign(policy, project?.capabilities?.[capabilityId] ?? {});
    }
  }
  return policy;
}

function inheritedAutoPolicy(
  policy: Partial<Record<ProviderConfigSyncAutoOperation, ProviderConfigSyncAutoMode>> | undefined,
): Record<ProviderConfigSyncAutoOperation, ProviderConfigSyncAutoOverrideMode> {
  return {
    additive: policy?.additive ?? "inherit",
    removal: policy?.removal ?? "inherit",
    change: policy?.change ?? "inherit",
  };
}

function formatTokens(count: number | undefined): string {
  const value = Math.max(0, count ?? 0);
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M tok`;
  if (value >= 10_000) return `${Math.round(value / 1_000)}K tok`;
  return `${value.toLocaleString()} tok`;
}

function isStructuredCapability(capability: ProviderConfigSyncCapability | undefined): boolean {
  return capability?.capability_id === "mcp" || capability?.category === "agent" || capability?.category === "skill" || capability?.category === "command";
}

function providerSpecificStatus(
  specific: ProviderConfigSyncFile,
  unifiedContent: string,
  specificContent: string,
): "missing" | "diff" | "aligned" {
  if (!specific.exists) return "missing";
  return unifiedContent === specificContent ? "aligned" : "diff";
}

function capabilityStatus(capability: ProviderConfigSyncCapability): "missing" | "diff" | "aligned" {
  if (capability.missing_count > 0) return "missing";
  return capability.has_diffs ? "diff" : "aligned";
}

function collectProviderConfigSyncContents(body: ProviderConfigSyncResponse): Record<string, string> {
  const contents: Record<string, string> = {};
  for (const file of body.files) contents[file.entry_id] = file.content;
  for (const capabilities of Object.values(body.groups)) {
    for (const capability of capabilities) {
      contents[capability.unified.entry_id] = capability.unified.content;
      for (const specific of capability.specifics) contents[specific.entry_id] = specific.content;
    }
  }
  return contents;
}

function mergeFetchedDrafts(
  currentDrafts: Record<string, string>,
  previousContents: Record<string, string>,
  nextContents: Record<string, string>,
): Record<string, string> {
  const merged: Record<string, string> = {};
  for (const [entryId, content] of Object.entries(nextContents)) {
    const current = currentDrafts[entryId];
    const previous = previousContents[entryId];
    merged[entryId] = current !== undefined && previous !== undefined && current !== previous
      ? current
      : content;
  }
  return merged;
}

function providerConfigSyncFileDisplayName(file: ProviderConfigSyncFile): string {
  return file.provider_names.length > 0 ? file.provider_names.join(", ") : file.label;
}

function shouldConfirmApplyTargetOverwrite(target: ProviderConfigSyncFile): boolean {
  return target.exists && target.content.trim().length > 0;
}

function canTransferCapability(capability: ProviderConfigSyncCapability): boolean {
  return capability.category === "skill" || capability.category === "agent" || capability.category === "command";
}

export function ProviderConfigSyncPage({ open, cwd, onClose, client, subscribeExternalChanges, initialCapabilityId, embedded }: ProviderConfigSyncPageProps) {
  const [data, setData] = useState<ProviderConfigSyncResponse | null>(null);
  const [projects, setProjects] = useState<ProviderConfigSyncProject[]>([]);
  const [scope, setScope] = useState<ProviderConfigSyncScope>("project");
  const [capabilityMenuOpen, setCapabilityMenuOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [transferCapabilityId, setTransferCapabilityId] = useState("");
  const [transferMode, setTransferMode] = useState<"copy" | "move">("copy");
  const [transferTargetScope, setTransferTargetScope] = useState<ProviderConfigSyncScope>("project");
  const [transferTargetCwd, setTransferTargetCwd] = useState(cwd ?? "");
  const [collapsedGroups, setCollapsedGroups] = useState<Record<string, boolean>>({});
  const [newCapabilityCategory, setNewCapabilityCategory] = useState<CreateCapabilityCategory>("skill");
  const [newCapabilityProviders, setNewCapabilityProviders] = useState<string[]>([]);
  const [newCapabilityName, setNewCapabilityName] = useState("");
  const [newCapabilityDescription, setNewCapabilityDescription] = useState("");
  const [newCapabilityInstructions, setNewCapabilityInstructions] = useState("");
  const [selectedProjectPath, setSelectedProjectPath] = useState(cwd ?? "");
  const [selectedCapabilityId, setSelectedCapabilityId] = useState("");
  const [selectedSpecificId, setSelectedSpecificId] = useState("");
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [debouncedDrafts, setDebouncedDrafts] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [autoLog, setAutoLog] = useState<ProviderConfigSyncAutoResponse | null>(null);
  const fetchSequence = useRef(0);
  const latestContents = useRef<Record<string, string>>({});

  const projectOptions = useMemo(
    () => projects.filter((project) => (project.node_id ?? "primary") === "primary"),
    [projects],
  );
  const targetCwd = scope === "project" ? selectedProjectPath : "";
  const transferTargetEffectiveCwd = transferTargetScope === "project" ? transferTargetCwd : "";

  useEffect(() => {
    if (!open) return;
    void client.listProjects()
      .then((nextProjects) => {
        const local = nextProjects.filter((project) => (project.node_id ?? "primary") === "primary");
        setProjects(nextProjects);
        setSelectedProjectPath(
          local.some((project) => project.path === cwd) ? cwd ?? "" : local[0]?.path ?? "",
        );
        setTransferTargetCwd(
          local.some((project) => project.path === cwd) ? cwd ?? "" : local[0]?.path ?? "",
        );
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [open, cwd, client]);

  const fetchSync = useCallback(async () => {
    const sequence = ++fetchSequence.current;
    try {
      const body = await client.getState(targetCwd);
      if (sequence !== fetchSequence.current) return;
      setData(body);
      const previousContents = latestContents.current;
      const nextContents = collectProviderConfigSyncContents(body);
      setDrafts((current) => mergeFetchedDrafts(current, previousContents, nextContents));
      setDebouncedDrafts((current) => mergeFetchedDrafts(current, previousContents, nextContents));
      latestContents.current = nextContents;
      setError(null);
    } catch (e) {
      if (sequence !== fetchSequence.current) return;
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [targetCwd, client]);

  useEffect(() => {
    if (!open) return;
    void fetchSync();
    const off = subscribeExternalChanges?.(() => void fetchSync());
    return typeof off === "function" ? off : undefined;
  }, [open, fetchSync]);

  const capabilities = useMemo(
    () => data?.groups?.[scope] ?? [],
    [data, scope],
  );
  const capabilityGroups = useMemo(() => {
    const groups: { category: string; label: string; capabilities: ProviderConfigSyncCapability[] }[] = [];
    const byCategory = new Map<string, ProviderConfigSyncCapability[]>();
    for (const capability of capabilities) {
      const items = byCategory.get(capability.category) ?? [];
      items.push(capability);
      byCategory.set(capability.category, items);
    }
    for (const [category, items] of byCategory.entries()) {
      groups.push({
        category,
        label: CATEGORY_LABELS[category] ?? category,
        capabilities: items,
      });
    }
    return groups;
  }, [capabilities]);
  const scopeTokenTotals = useMemo(() => {
    const byProvider = new Map<string, { providerName: string; tokenCount: number }>();
    let unified = 0;
    let specifics = 0;
    for (const capability of capabilities) {
      unified += capability.unified_token_count ?? 0;
      specifics += capability.specific_token_count ?? 0;
      for (const item of capability.provider_token_counts ?? []) {
        const current = byProvider.get(item.provider_kind) ?? { providerName: item.provider_name, tokenCount: 0 };
        current.tokenCount += item.token_count;
        byProvider.set(item.provider_kind, current);
      }
    }
    return {
      unified,
      specifics,
      allTracked: unified + specifics,
      byProvider: [...byProvider.entries()]
        .map(([providerKind, item]) => ({ providerKind, ...item }))
        .sort((a, b) => a.providerName.localeCompare(b.providerName)),
    };
  }, [capabilities]);
  const providerOptions = useMemo(
    () => (data?.providers ?? []).map((provider) => ({
      providerKind: provider.kind,
      providerName: provider.name,
    })),
    [data?.providers],
  );
  const selectedCapability = capabilities.find((capability) => capability.id === selectedCapabilityId) ?? capabilities[0];

  // Honor an embedded pre-selection: once data loads, jump to the
  // requested capability (no-op if it doesn't exist in this scope/cwd).
  useEffect(() => {
    if (!open || !initialCapabilityId) return;
    if (selectedCapabilityId === initialCapabilityId) return;
    if (capabilities.some((capability) => capability.id === initialCapabilityId)) {
      setSelectedCapabilityId(initialCapabilityId);
    }
  }, [open, initialCapabilityId, capabilities, selectedCapabilityId]);
  const unified = selectedCapability?.unified;
  const selectedSpecific =
    selectedCapability?.specifics.find((specific) => specific.entry_id === selectedSpecificId)
    ?? selectedCapability?.specifics[0];
  const autoPolicy = effectiveAutoPolicy(data?.auto_settings, targetCwd, selectedCapability?.capability_id);

  const saveAutoSettings = useCallback(async (
    level: ProviderConfigSyncAutoSettingsLevel,
    policy: ProviderConfigSyncAutoPolicy | Record<ProviderConfigSyncAutoOperation, ProviderConfigSyncAutoOverrideMode>,
  ) => {
    setBusy(true);
    try {
      const body = {
        level,
        cwd: level === "project" || level === "project_capability" ? targetCwd : "",
        capability_id: level === "capability" || level === "project_capability" ? selectedCapability?.capability_id ?? "" : "",
        policy,
      };
      const settings = await client.updateAutoSettings(body);
      setData((current) => current ? { ...current, auto_settings: settings } : current);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [targetCwd, selectedCapability?.capability_id]);

  useEffect(() => {
    setSelectedCapabilityId("");
    setSelectedSpecificId("");
    setCapabilityMenuOpen(false);
    setAutoLog(null);
    setCreateOpen(false);
    setTransferCapabilityId("");
  }, [scope, targetCwd]);

  useEffect(() => {
    setNewCapabilityProviders((current) => {
      const available = new Set(providerOptions.map((provider) => provider.providerKind));
      const kept = current.filter((kind) => available.has(kind));
      return kept.length > 0 ? kept : providerOptions.map((provider) => provider.providerKind);
    });
  }, [providerOptions]);

  useEffect(() => {
    setSelectedSpecificId("");
    setAutoLog(null);
  }, [selectedCapability?.id]);

  useEffect(() => {
    setAutoLog(null);
  }, [selectedSpecific?.entry_id]);

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedDrafts(drafts), 180);
    return () => window.clearTimeout(timer);
  }, [drafts]);

  const draftFor = useCallback(
    (file: ProviderConfigSyncFile | undefined) => file ? drafts[file.entry_id] ?? file.content : "",
    [drafts],
  );
  const debouncedDraftFor = useCallback(
    (file: ProviderConfigSyncFile | undefined) => file ? debouncedDrafts[file.entry_id] ?? draftFor(file) : "",
    [debouncedDrafts, draftFor],
  );
  const updateDraft = useCallback((file: ProviderConfigSyncFile, content: string) => {
    setDrafts((current) => ({ ...current, [file.entry_id]: content }));
  }, []);
  const isDirty = useCallback((file: ProviderConfigSyncFile | undefined) => {
    if (!file) return false;
    return draftFor(file) !== file.content;
  }, [draftFor]);

  const saveFileContent = useCallback(async (file: ProviderConfigSyncFile, content: string) => {
    setBusy(true);
    try {
      const body: ProviderConfigSyncWriteRequest = {
        cwd: targetCwd,
        entry_id: file.entry_id,
        expected_content: file.exists ? file.content : null,
        content,
      };
      await client.writeFile(body);
      await fetchSync();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [targetCwd, fetchSync]);

  const saveFile = useCallback(async (file: ProviderConfigSyncFile) => {
    await saveFileContent(file, draftFor(file));
  }, [draftFor, saveFileContent]);

  const updateAndSaveFile = useCallback((file: ProviderConfigSyncFile, content: string) => {
    updateDraft(file, content);
    if (content !== file.content) void saveFileContent(file, content);
  }, [saveFileContent, updateDraft]);

  const restoreFile = useCallback(async (file: ProviderConfigSyncFile) => {
    if (!window.confirm(`Restore ${providerConfigSyncFileDisplayName(file)} from its Provider Config Sync backup?`)) return;
    setBusy(true);
    try {
      const body: ProviderConfigSyncRestoreRequest = {
        cwd: targetCwd,
        entry_id: file.entry_id,
        expected_content: file.exists ? file.content : null,
      };
      await client.restoreFile(body);
      await fetchSync();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [targetCwd, fetchSync]);

  const deleteCapability = useCallback(async (capability: ProviderConfigSyncCapability) => {
    const entries = [capability.unified, ...capability.specifics];
    const existingCount = entries.filter((entry) => entry.exists).length;
    const label = `${capability.name} (${existingCount} existing file${existingCount === 1 ? "" : "s"})`;
    if (!window.confirm(`Remove the whole capability ${label} across unified and provider-specific files?`)) return;
    setBusy(true);
    try {
      const expected_contents = Object.fromEntries(
        entries.map((entry) => [entry.entry_id, entry.exists ? entry.content : null]),
      );
      const body: ProviderConfigSyncDeleteCapabilityRequest = {
        cwd: targetCwd,
        scope: capability.scope,
        capability_id: capability.capability_id,
        expected_contents,
      };
      await client.deleteCapability(body);
      setSelectedCapabilityId("");
      setSelectedSpecificId("");
      await fetchSync();
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [targetCwd, fetchSync]);

  const createCapability = useCallback(async () => {
    const name = newCapabilityName.trim();
    if (!name || newCapabilityProviders.length === 0) return;
    setBusy(true);
    try {
      const body: ProviderConfigSyncCreateCapabilityRequest = {
        cwd: targetCwd,
        scope,
        category: newCapabilityCategory,
        provider_kinds: newCapabilityProviders,
        name,
        description: newCapabilityDescription.trim(),
        instructions: newCapabilityInstructions,
        metadata: {},
      };
      const result = await client.createCapability(body);
      setNewCapabilityName("");
      setNewCapabilityDescription("");
      setNewCapabilityInstructions("");
      setCreateOpen(false);
      await fetchSync();
      if (result.capability?.id) setSelectedCapabilityId(result.capability.id);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [
    targetCwd,
    scope,
    newCapabilityCategory,
    newCapabilityProviders,
    newCapabilityName,
    newCapabilityDescription,
    newCapabilityInstructions,
    fetchSync,
  ]);

  const openCreateCapability = useCallback((category: CreateCapabilityCategory = "skill") => {
    setNewCapabilityCategory(category);
    setNewCapabilityProviders(providerOptions.map((provider) => provider.providerKind));
    setCreateOpen(true);
  }, [providerOptions]);

  const transferCapability = useCallback(async (capability: ProviderConfigSyncCapability) => {
    if (!canTransferCapability(capability) || !transferTargetScope) return;
    if (transferTargetScope === "project" && !transferTargetCwd) return;
    const sourceLabel = capability.scope === "global" ? "global" : selectedProjectPath;
    const targetLabel = transferTargetScope === "global" ? "global" : transferTargetCwd;
    if (sourceLabel === targetLabel) return;
    const entries = [capability.unified, ...capability.specifics];
    const dirty = entries.some((entry) => isDirty(entry));
    if (dirty) {
      setError("Save or discard edits before moving or copying a capability.");
      return;
    }
    if (transferMode === "move" && !window.confirm(`Move ${capability.name} to ${targetLabel}?`)) return;
    setBusy(true);
    try {
      const expected_contents = Object.fromEntries(
        entries.map((entry) => [entry.entry_id, entry.exists ? entry.content : null]),
      );
      const body: ProviderConfigSyncTransferCapabilityRequest = {
        cwd: targetCwd,
        scope: capability.scope,
        capability_id: capability.capability_id,
        target_cwd: transferTargetScope === "project" ? transferTargetCwd : "",
        target_scope: transferTargetScope,
        mode: transferMode,
        expected_contents,
      };
      const result = await client.transferCapability(body);
      setTransferCapabilityId("");
      await fetchSync();
      if (result.capability?.id && transferTargetScope === scope && transferTargetEffectiveCwd === targetCwd) {
        setSelectedCapabilityId(result.capability.id);
      } else if (transferMode === "move") {
        setSelectedCapabilityId("");
      }
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [
    transferTargetScope,
    transferTargetCwd,
    selectedProjectPath,
    transferMode,
    isDirty,
    targetCwd,
    client,
    fetchSync,
    scope,
    transferTargetEffectiveCwd,
  ]);

  const apply = useCallback(async (capability: ProviderConfigSyncCapability, source: ProviderConfigSyncFile, target: ProviderConfigSyncFile) => {
    if (
      shouldConfirmApplyTargetOverwrite(target)
      && !window.confirm(`This will overwrite existing content in ${providerConfigSyncFileDisplayName(target)}. Continue?`)
    ) {
      return;
    }
    setBusy(true);
    try {
      const body: ProviderConfigSyncApplyRequest = {
        cwd: targetCwd,
        capability_id: capability.capability_id,
        source_entry_id: source.entry_id,
        target_entry_id: target.entry_id,
        expected_source: source.content,
        expected_target: target.exists ? target.content : null,
      };
      await client.apply(body);
      await fetchSync();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [targetCwd, fetchSync]);

  const runAutoSync = useCallback(async (
    capability: ProviderConfigSyncCapability,
    source: ProviderConfigSyncFile,
    target: ProviderConfigSyncFile,
    options: {
      llmHunkIds?: string[];
      policy?: ProviderConfigSyncAutoPolicy;
      refetch?: boolean;
    } = {},
  ) => {
    setBusy(true);
    try {
      const body: ProviderConfigSyncAutoRequest = {
        cwd: targetCwd,
        capability_id: capability.capability_id,
        source_entry_id: source.entry_id,
        target_entry_id: target.entry_id,
        expected_source: source.content,
        expected_target: target.exists ? target.content : null,
        policy: options.policy ?? autoPolicy,
        approved_hunk_ids: [],
        llm_hunk_ids: options.llmHunkIds ?? [],
      };
      const result = await client.autoSync(body);
      setAutoLog(result);
      if (options.refetch !== false) await fetchSync();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [targetCwd, autoPolicy, fetchSync]);

  const llmFixSelectedProvider = useCallback(async () => {
    if (!selectedCapability || !unified || !selectedSpecific) return;
    await runAutoSync(selectedCapability, unified, selectedSpecific, { policy: LLM_AUTO_POLICY });
  }, [selectedCapability, unified, selectedSpecific, runAutoSync]);

  const llmFixCapability = useCallback(async () => {
    if (!selectedCapability || !unified) return;
    setBusy(true);
    try {
      let latestLog: ProviderConfigSyncAutoResponse | null = null;
      for (const target of selectedCapability.specifics) {
        if (!target.writable || !target.exists || target.disabled || target.entry_id === unified.entry_id) continue;
        const body: ProviderConfigSyncAutoRequest = {
          cwd: targetCwd,
          capability_id: selectedCapability.capability_id,
          source_entry_id: unified.entry_id,
          target_entry_id: target.entry_id,
          expected_source: unified.content,
          expected_target: target.content,
          policy: LLM_AUTO_POLICY,
          approved_hunk_ids: [],
          llm_hunk_ids: [],
        };
        latestLog = await client.autoSync(body);
      }
      setAutoLog(latestLog);
      await fetchSync();
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [selectedCapability, unified, targetCwd, fetchSync]);

  const llmFixHunk = useCallback(async (hunkId: string) => {
    if (!selectedCapability || !unified || !selectedSpecific || !autoLog) return;
    const source = autoLog.source_entry_id === unified.entry_id ? unified : selectedSpecific;
    const target = autoLog.target_entry_id === unified.entry_id ? unified : selectedSpecific;
    await runAutoSync(selectedCapability, source, target, {
      policy: DEFAULT_AUTO_POLICY,
      llmHunkIds: [hunkId],
    });
  }, [selectedCapability, unified, selectedSpecific, autoLog, runAutoSync]);

  if (!open) return null;

  const selectCapability = (capabilityId: string) => {
    setSelectedCapabilityId(capabilityId);
    setCapabilityMenuOpen(false);
    setTransferCapabilityId("");
  };

  return (
    <div className={`provider-config-sync-page${capabilityMenuOpen ? " menu-open" : ""}${embedded ? " embedded" : ""}`} data-testid="provider-config-sync-page">
      {!embedded && (
      <header className="provider-config-sync-topbar">
        <div>
          <h1>Provider Config Sync</h1>
          <div className="provider-config-sync-subtitle">
            {scope}
            {selectedCapability ? ` · ${selectedCapability.name}` : ""}
            {selectedCapability ? ` · ${formatTokens(selectedCapability.total_token_count)} est.` : ""}
          </div>
        </div>
        <div className="provider-config-sync-topbar-actions">
          <button
            type="button"
            className="btn-secondary provider-config-sync-menu-button"
            onClick={() => setCapabilityMenuOpen((current) => !current)}
            aria-expanded={capabilityMenuOpen}
            aria-controls="provider-config-sync-capability-menu"
          >
            Capabilities
          </button>
          <button
            type="button"
            className="btn-secondary"
            onClick={() => setSettingsOpen((current) => !current)}
            disabled={busy}
          >
            Settings
          </button>
          <button type="button" className="btn-secondary" onClick={onClose} disabled={busy} hidden={embedded}>
            Close
          </button>
        </div>
      </header>
      )}

      <div className="provider-config-sync-shell">
        {!embedded && (
        <>
        <button
          type="button"
          className="provider-config-sync-menu-scrim"
          aria-label="Close capabilities menu"
          onClick={() => setCapabilityMenuOpen(false)}
        />
        <aside className="provider-config-sync-sidebar" id="provider-config-sync-capability-menu">
          <div className="provider-config-sync-sidebar-section">
            <div className="provider-config-sync-label">Scope</div>
            <div className="provider-config-sync-segmented">
              {SCOPES.map((item) => (
                <button
                  key={item}
                  type="button"
                  className={item === scope ? "active" : ""}
                  onClick={() => setScope(item)}
                >
                  {item}
                </button>
              ))}
            </div>
          </div>

          {scope === "project" && (
            <div className="provider-config-sync-sidebar-section">
              <div className="provider-config-sync-label">Project</div>
              <select
                aria-label="Provider Config Sync project"
                value={selectedProjectPath}
                onChange={(e) => {
                  setSelectedProjectPath(e.target.value);
                }}
                className="provider-config-sync-select"
              >
                <option value="">select project</option>
                {projectOptions.map((project) => (
                  <option key={`${project.node_id ?? "primary"}:${project.path}`} value={project.path}>
                    {project.name} · {project.path}
                  </option>
                ))}
              </select>
            </div>
          )}

          <div className="provider-config-sync-sidebar-section">
            <div className="provider-config-sync-sidebar-heading">
              <div className="provider-config-sync-label">Capabilities</div>
              <button
                type="button"
                className="btn-secondary provider-config-sync-icon-action"
                aria-label="Add capability"
                title="Add capability"
                disabled={busy || providerOptions.length === 0 || (scope === "project" && !targetCwd)}
                onClick={() => {
                  if (createOpen) {
                    setCreateOpen(false);
                    return;
                  }
                  openCreateCapability();
                }}
              >
                +
              </button>
            </div>
            {createOpen && (
              <div className="provider-config-sync-create-panel">
                <select
                  aria-label="New capability category"
                  className="provider-config-sync-select"
                  value={newCapabilityCategory}
                  onChange={(e) => setNewCapabilityCategory(e.target.value as CreateCapabilityCategory)}
                >
                  {CREATE_CAPABILITY_CATEGORIES.map((category) => (
                    <option key={category.id} value={category.id}>{category.label}</option>
                  ))}
                </select>
                <div className="provider-config-sync-provider-checks" aria-label="New capability providers">
                  {providerOptions.map((provider) => (
                    <label key={provider.providerKind}>
                      <input
                        type="checkbox"
                        checked={newCapabilityProviders.includes(provider.providerKind)}
                        onChange={(e) => setNewCapabilityProviders((current) => (
                          e.target.checked
                            ? [...current, provider.providerKind]
                            : current.filter((kind) => kind !== provider.providerKind)
                        ))}
                      />
                      <span>{provider.providerName}</span>
                    </label>
                  ))}
                </div>
                <input
                  aria-label="New capability name"
                  className="provider-config-sync-input"
                  value={newCapabilityName}
                  onChange={(e) => setNewCapabilityName(e.target.value)}
                  placeholder="name"
                />
                <input
                  aria-label="New capability description"
                  className="provider-config-sync-input"
                  value={newCapabilityDescription}
                  onChange={(e) => setNewCapabilityDescription(e.target.value)}
                  placeholder="description"
                />
                <textarea
                  aria-label="New capability instructions"
                  className="provider-config-sync-textarea provider-config-sync-create-instructions"
                  value={newCapabilityInstructions}
                  onChange={(e) => setNewCapabilityInstructions(e.target.value)}
                  placeholder="instructions"
                />
                <button
                  type="button"
                  className="btn-primary"
                  disabled={busy || !newCapabilityName.trim() || newCapabilityProviders.length === 0}
                  onClick={() => void createCapability()}
                >
                  Add capability
                </button>
              </div>
            )}
            <div className="provider-config-sync-token-summary">
              <div>
                <strong>{formatTokens(scopeTokenTotals.allTracked)}</strong>
                <span>estimated tracked config</span>
              </div>
              <div>
                <span>Unified</span>
                <strong>{formatTokens(scopeTokenTotals.unified)}</strong>
              </div>
              <div>
                <span>Providers</span>
                <strong>{formatTokens(scopeTokenTotals.specifics)}</strong>
              </div>
              {scopeTokenTotals.byProvider.map((item) => (
                <div key={item.providerKind}>
                  <span>{item.providerName}</span>
                  <strong>{formatTokens(item.tokenCount)}</strong>
                </div>
              ))}
            </div>
            <div className="provider-config-sync-file-list">
              {capabilityGroups.map((group) => (
                <section
                  className={`provider-config-sync-capability-group${collapsedGroups[group.category] ? " is-collapsed" : ""}`}
                  key={group.category}
                >
                  <div className="provider-config-sync-capability-group-header">
                    <button
                      type="button"
                      className="provider-config-sync-capability-group-title"
                      aria-expanded={!collapsedGroups[group.category]}
                      onClick={() => setCollapsedGroups((current) => ({
                        ...current,
                        [group.category]: !current[group.category],
                      }))}
                    >
                      <span className="provider-config-sync-capability-group-chevron">{collapsedGroups[group.category] ? ">" : "v"}</span>
                      <span>{group.label}</span>
                      <small>{group.capabilities.length}</small>
                    </button>
                    {CREATE_CAPABILITY_CATEGORIES.some((category) => category.id === group.category) && (
                      <button
                        type="button"
                        className="btn-secondary provider-config-sync-icon-action provider-config-sync-capability-group-add"
                        aria-label={`Add ${group.label} capability`}
                        title={`Add ${group.label} capability`}
                        disabled={busy || providerOptions.length === 0 || (scope === "project" && !targetCwd)}
                        onClick={() => openCreateCapability(group.category as CreateCapabilityCategory)}
                      >
                        +
                      </button>
                    )}
                  </div>
                  {!collapsedGroups[group.category] && (
                    <div className="provider-config-sync-capability-group-items">
                      {group.capabilities.map((capability) => (
                        <div className="provider-config-sync-capability-item" key={capability.id}>
                          <button
                            type="button"
                            className={capability.id === selectedCapability?.id ? "active" : ""}
                            onClick={() => selectCapability(capability.id)}
                            onContextMenu={(e) => {
                              e.preventDefault();
                              selectCapability(capability.id);
                              if (canTransferCapability(capability)) {
                                setTransferCapabilityId((current) => current === capability.id ? "" : capability.id);
                                setTransferTargetScope(capability.scope === "global" ? "project" : "global");
                              }
                            }}
                          >
                            <span className="provider-config-sync-capability-name">
                              <span
                                className={`provider-config-sync-status-dot ${capabilityStatus(capability)}`}
                                aria-label={`${capabilityStatus(capability)} capability`}
                              />
                              <span>{capability.name}</span>
                            </span>
                            <small>
                              {capability.specific_count} specifics
                              {capability.has_diffs ? " · diff" : " · aligned"}
                              {capability.missing_count ? ` · ${capability.missing_count} missing` : ""}
                              {` · ${formatTokens(capability.total_token_count)} est.`}
                            </small>
                          </button>
                          {transferCapabilityId === capability.id && (
                            <div className="provider-config-sync-transfer-panel">
                              {canTransferCapability(capability) ? (
                                <>
                                  <div className="provider-config-sync-segmented">
                                    <button
                                      type="button"
                                      className={transferMode === "copy" ? "active" : ""}
                                      onClick={() => setTransferMode("copy")}
                                    >
                                      Copy
                                    </button>
                                    <button
                                      type="button"
                                      className={transferMode === "move" ? "active" : ""}
                                      onClick={() => setTransferMode("move")}
                                    >
                                      Move
                                    </button>
                                  </div>
                                  <select
                                    aria-label="Transfer target level"
                                    className="provider-config-sync-select"
                                    value={transferTargetScope}
                                    onChange={(e) => setTransferTargetScope(e.target.value as ProviderConfigSyncScope)}
                                  >
                                    {SCOPES.map((item) => (
                                      <option key={item} value={item}>{item}</option>
                                    ))}
                                  </select>
                                  {transferTargetScope === "project" && (
                                    <select
                                      aria-label="Transfer target project"
                                      className="provider-config-sync-select"
                                      value={transferTargetCwd}
                                      onChange={(e) => setTransferTargetCwd(e.target.value)}
                                    >
                                      <option value="">select project</option>
                                      {projectOptions.map((project) => (
                                        <option key={`${project.node_id ?? "primary"}:${project.path}`} value={project.path}>
                                          {project.name} · {project.path}
                                        </option>
                                      ))}
                                    </select>
                                  )}
                                  <button
                                    type="button"
                                    className="btn-primary"
                                    disabled={
                                      busy
                                      || (transferTargetScope === "project" && !transferTargetCwd)
                                      || (capability.scope === transferTargetScope && targetCwd === transferTargetEffectiveCwd)
                                    }
                                    onClick={() => void transferCapability(capability)}
                                  >
                                    {transferMode === "copy" ? "Copy capability" : "Move capability"}
                                  </button>
                                </>
                              ) : (
                                <div className="provider-config-sync-empty">Move/copy supports skills, subagents, and commands.</div>
                              )}
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </section>
              ))}
              {capabilities.length === 0 && (
                <div className="provider-config-sync-empty">
                  {scope === "project" && !targetCwd ? "Select a project." : "No equivalent capabilities found."}
                </div>
              )}
            </div>
          </div>
        </aside>
        </>
        )}

        <main className="provider-config-sync-main">
          {error && <div className="provider-config-sync-error">{error}</div>}
          {settingsOpen && (
            <ProviderConfigSyncPageSettings
              busy={busy}
              settings={data?.auto_settings}
              targetCwd={targetCwd}
              capability={selectedCapability}
              specific={selectedSpecific}
              effectivePolicy={autoPolicy}
              log={autoLog}
              canFixSelectedProvider={
                !!selectedCapability
                && !!unified
                && !!selectedSpecific
                && !isDirty(unified)
                && !isDirty(selectedSpecific)
                && unified.exists
                && !selectedSpecific.disabled
                && selectedSpecific.writable
                && selectedSpecific.exists
              }
              canFixCapability={
                !!selectedCapability
                && !!unified
                && !isDirty(unified)
                && unified.exists
                && selectedCapability.specifics.every((specific) => !isDirty(specific))
                && selectedCapability.specifics.some((specific) => specific.exists && specific.writable && !specific.disabled)
              }
              onSave={saveAutoSettings}
              onFixSelectedProvider={() => void llmFixSelectedProvider()}
              onFixCapability={() => void llmFixCapability()}
              onFixHunk={(hunkId) => void llmFixHunk(hunkId)}
            />
          )}

          <div className="provider-config-sync-editor-grid provider-config-sync-editor-grid-single">
            <section className="provider-config-sync-editor-card provider-config-sync-specifics-card">
              <div className="provider-config-sync-card-header">
                <span>{selectedCapability?.name ?? "Provider Config Sync"}</span>
                <div className="provider-config-sync-card-header-actions">
                  <span>
                    {selectedCapability
                      ? `${selectedCapability.specific_count} provider files · ${formatTokens(selectedCapability.total_token_count)} est.`
                      : "none"}
                  </span>
                  {selectedCapability && (
                    <button
                      type="button"
                      className="btn-secondary provider-config-sync-danger-action"
                      disabled={
                        busy
                        || isDirty(selectedCapability.unified)
                        || selectedCapability.specifics.some((specific) => isDirty(specific))
                      }
                      onClick={() => void deleteCapability(selectedCapability)}
                    >
                      Remove capability
                    </button>
                  )}
                </div>
              </div>
              {selectedCapability && unified ? (
                <div className="provider-config-sync-specifics">
                  {selectedCapability.specifics.length > 0 && (
                    <div className="provider-config-sync-specific-tabs" role="tablist" aria-label="Provider specifics">
                      {selectedCapability.specifics.map((specific) => {
                        const status = providerSpecificStatus(
                          specific,
                          debouncedDraftFor(unified),
                          debouncedDraftFor(specific),
                        );
                        return (
                          <button
                            key={specific.entry_id}
                            type="button"
                            role="tab"
                            aria-selected={specific.entry_id === selectedSpecific?.entry_id}
                            className={specific.entry_id === selectedSpecific?.entry_id ? "active" : ""}
                            onClick={() => setSelectedSpecificId(specific.entry_id)}
                          >
                            <span>{specific.provider_names.join(", ")}</span>
                            <small className={status}>{status}{specific.disabled ? " · disabled" : ""}</small>
                            <small>{formatTokens(specific.token_count)}</small>
                          </button>
                        );
                      })}
                    </div>
                  )}
                  {selectedSpecific ? (
                    <section className="provider-config-sync-specific" key={selectedSpecific.entry_id}>
                      <div className="provider-config-sync-specific-header">
                        <div>
                          <strong>{selectedSpecific.provider_names.join(", ")}</strong>
                          <span>{selectedSpecific.exists ? selectedSpecific.label : `${selectedSpecific.label} (new)`}</span>
                          {selectedSpecific.disabled && <span>disabled</span>}
                          <span>{formatTokens(selectedSpecific.token_count)} est.</span>
                        </div>
                        <small>{selectedSpecific.path}</small>
                      </div>
                      {selectedSpecific.read_error ? (
                        <div className="provider-config-sync-empty">{selectedSpecific.read_error}</div>
                      ) : !selectedSpecific.exists ? (
                        <MissingSpecificColumns
                          unified={unified}
                          specific={selectedSpecific}
                          unifiedContent={debouncedDraftFor(unified)}
                        />
                      ) : isStructuredCapability(selectedCapability) ? (
                        <StructuredSpecificView
                          busy={busy}
                          capability={selectedCapability}
                          unified={unified}
                          specific={{ ...selectedSpecific, content: debouncedDraftFor(selectedSpecific) }}
                          unifiedContent={debouncedDraftFor(unified)}
                          unifiedDirty={isDirty(unified)}
                          specificDirty={isDirty(selectedSpecific)}
                          onUnifiedContentChange={(content) => updateDraft(unified, content)}
                          onSpecificContentChange={(content) => updateDraft(selectedSpecific, content)}
                          onSaveUnified={() => void saveFile(unified)}
                          onSaveSpecific={() => void saveFile(selectedSpecific)}
                        />
                      ) : (
                        <EditableAlignedFileDiff
                          busy={busy}
                          unified={unified}
                          specific={selectedSpecific}
                          debouncedUnifiedContent={debouncedDraftFor(unified)}
                          debouncedSpecificContent={debouncedDraftFor(selectedSpecific)}
                          unifiedDirty={isDirty(unified)}
                          specificDirty={isDirty(selectedSpecific)}
                          onUnifiedChange={(lineNumber, fallbackIndex, content) => {
                            updateDraft(
                              unified,
                              replaceLine(draftFor(unified), lineNumber, fallbackIndex, content),
                            );
                          }}
                          onSpecificChange={(lineNumber, fallbackIndex, content) => {
                            updateDraft(
                              selectedSpecific,
                              replaceLine(draftFor(selectedSpecific), lineNumber, fallbackIndex, content),
                            );
                          }}
                          onApplyUnifiedBlock={() => updateAndSaveFile(unified, draftFor(selectedSpecific))}
                          onApplySpecificBlock={() => updateAndSaveFile(selectedSpecific, draftFor(unified))}
                          onApplyUnifiedRows={(rows) => {
                            updateAndSaveFile(unified, applyRowsToContent(draftFor(unified), rows, "unified"));
                          }}
                          onApplySpecificRows={(rows) => {
                            updateAndSaveFile(
                              selectedSpecific,
                              applyRowsToContent(draftFor(selectedSpecific), rows, "specific"),
                            );
                          }}
                          onSaveUnified={() => void saveFile(unified)}
                          onSaveSpecific={() => void saveFile(selectedSpecific)}
                          unifiedHeaderActions={(
                            <>
                              <button
                                type="button"
                                className="btn-secondary"
                                disabled={
                                  busy
                                  || isDirty(unified)
                                  || isDirty(selectedSpecific)
                                  || !selectedSpecific.exists
                                  || !unified.writable
                                }
                                onClick={() => void apply(selectedCapability, selectedSpecific, unified)}
                              >
                                From {providerConfigSyncFileDisplayName(selectedSpecific)}
                              </button>
                              {unified.backup_exists && (
                                <button
                                  type="button"
                                  className="btn-secondary"
                                  disabled={busy}
                                  onClick={() => void restoreFile(unified)}
                                >
                                  Rollback Unified
                                </button>
                              )}
                            </>
                          )}
                          specificHeaderActions={(
                            <>
                              <button
                                type="button"
                                className="btn-secondary"
                                disabled={
                                  busy
                                  || isDirty(unified)
                                  || isDirty(selectedSpecific)
                                  || !unified.exists
                                  || !selectedSpecific.writable
                                }
                                onClick={() => void apply(selectedCapability, unified, selectedSpecific)}
                              >
                                To {providerConfigSyncFileDisplayName(selectedSpecific)}
                              </button>
                              {selectedSpecific.backup_exists && (
                                <button
                                  type="button"
                                  className="btn-secondary"
                                  disabled={busy}
                                  onClick={() => void restoreFile(selectedSpecific)}
                                >
                                  Rollback {providerConfigSyncFileDisplayName(selectedSpecific)}
                                </button>
                              )}
                            </>
                          )}
                        />
                      )}
                    </section>
                  ) : (
                    <div className="provider-config-sync-empty">No provider-specific files for this capability.</div>
                  )}
                </div>
              ) : (
                <div className="provider-config-sync-empty">Select a capability.</div>
              )}
            </section>
          </div>
        </main>
      </div>
    </div>
  );
}

function StructuredSpecificView({
  busy,
  capability,
  unified,
  unifiedContent,
  specific,
  unifiedDirty,
  specificDirty,
  onUnifiedContentChange,
  onSpecificContentChange,
  onSaveUnified,
  onSaveSpecific,
}: {
  busy: boolean;
  capability: ProviderConfigSyncCapability;
  unified: ProviderConfigSyncFile;
  unifiedContent: string;
  specific: ProviderConfigSyncFile;
  unifiedDirty: boolean;
  specificDirty: boolean;
  onUnifiedContentChange: (content: string) => void;
  onSpecificContentChange: (content: string) => void;
  onSaveUnified: () => void;
  onSaveSpecific: () => void;
}) {
  if (!specific.exists) return <StructuredMissingSpecific specific={specific} />;
  if (capability.capability_id === "mcp") {
    const unifiedServers = parseMcpServers(unifiedContent);
    const specificServers = parseMcpServers(specific.content);
    if (!specificServers) return <StructuredParseError />;
    return (
      <div className="provider-config-sync-structured provider-config-sync-structured-specific">
        <StructuredDiffSummary
          unified={unifiedServers?.map((server) => server.name) ?? []}
          specific={specificServers.map((server) => server.name)}
        />
        {specificServers.map((server, index) => (
          <McpServerFields key={`${server.name}:${index}`} server={server} readOnly />
        ))}
        {specificServers.length === 0 && <div className="provider-config-sync-empty">No MCP servers.</div>}
      </div>
    );
  }
  if (capability.category === "agent" || capability.category === "skill" || capability.category === "command") {
    const unifiedItem = parseCommonItemDraft(unifiedContent);
    const item = parseCommonItemDraft(specific.content);
    if (!unifiedItem || !item) return <StructuredParseError />;
    return (
      <div className="provider-config-sync-structured provider-config-sync-structured-specific">
        <StructuredEditableFieldDiffs
          busy={busy}
          unified={unified}
          specific={specific}
          unifiedItem={unifiedItem}
          specificItem={item}
          unifiedDirty={unifiedDirty}
          specificDirty={specificDirty}
          onUnifiedContentChange={onUnifiedContentChange}
          onSpecificContentChange={onSpecificContentChange}
          onSaveUnified={onSaveUnified}
          onSaveSpecific={onSaveSpecific}
        />
      </div>
    );
  }
  return null;
}

function ProviderConfigSyncPageSettings({
  busy,
  settings,
  targetCwd,
  capability,
  specific,
  effectivePolicy,
  log,
  canFixSelectedProvider,
  canFixCapability,
  onSave,
  onFixSelectedProvider,
  onFixCapability,
  onFixHunk,
}: {
  busy: boolean;
  settings: ProviderConfigSyncAutoSettings | undefined;
  targetCwd: string;
  capability: ProviderConfigSyncCapability | undefined;
  specific: ProviderConfigSyncFile | undefined;
  effectivePolicy: ProviderConfigSyncAutoPolicy;
  log: ProviderConfigSyncAutoResponse | null;
  canFixSelectedProvider: boolean;
  canFixCapability: boolean;
  onSave: (
    level: ProviderConfigSyncAutoSettingsLevel,
    policy: ProviderConfigSyncAutoPolicy | Record<ProviderConfigSyncAutoOperation, ProviderConfigSyncAutoOverrideMode>,
  ) => void;
  onFixSelectedProvider: () => void;
  onFixCapability: () => void;
  onFixHunk: (hunkId: string) => void;
}) {
  const capabilityId = capability?.capability_id ?? "";
  const projectSettings = targetCwd ? settings?.projects?.[targetCwd] : undefined;
  return (
    <section className="provider-config-sync-settings-panel" aria-label="Provider Config Sync settings">
      <div className="provider-config-sync-settings-head">
        <div>
          <strong>Settings</strong>
          <span>{capability?.name ?? "No capability selected"}</span>
        </div>
        <div className="provider-config-sync-settings-effective">
          {AUTO_OPERATIONS.map((operation) => (
            <span key={operation.id}>{operation.label}: {effectivePolicy[operation.id]}</span>
          ))}
        </div>
      </div>
      <div className="provider-config-sync-settings-grid">
        <AutoPolicyEditor
          title="Global"
          policy={{ ...DEFAULT_AUTO_POLICY, ...(settings?.global ?? {}) }}
          disabled={busy}
          onChange={(policy) => onSave("global", policy as ProviderConfigSyncAutoPolicy)}
        />
        <AutoPolicyEditor
          title="Capability"
          policy={inheritedAutoPolicy(capabilityId ? settings?.capabilities?.[capabilityId] : undefined)}
          disabled={busy || !capabilityId}
          allowInherit
          onChange={(policy) => onSave("capability", policy)}
        />
        <AutoPolicyEditor
          title="Project"
          policy={inheritedAutoPolicy(projectSettings?.policy)}
          disabled={busy || !targetCwd}
          allowInherit
          onChange={(policy) => onSave("project", policy)}
        />
        <AutoPolicyEditor
          title="Project capability"
          policy={inheritedAutoPolicy(capabilityId ? projectSettings?.capabilities?.[capabilityId] : undefined)}
          disabled={busy || !targetCwd || !capabilityId}
          allowInherit
          onChange={(policy) => onSave("project_capability", policy)}
        />
      </div>
      <div className="provider-config-sync-settings-actions">
        <button
          type="button"
          className="btn-secondary"
          disabled={busy || !canFixSelectedProvider}
          onClick={onFixSelectedProvider}
        >
          LLM fix selected provider
        </button>
        <button
          type="button"
          className="btn-secondary"
          disabled={busy || !canFixCapability}
          onClick={onFixCapability}
        >
          LLM fix current capability
        </button>
      </div>
      {specific && (
        <div className="provider-config-sync-settings-target">
          <span>Selected provider</span>
          <strong>{specific.provider_names.join(", ")}</strong>
        </div>
      )}
      {log && (
        <div className="provider-config-sync-auto-log">
          <div className="provider-config-sync-auto-log-head">
            <strong>{log.applied_count} applied</strong>
            <strong>{log.pending_count} pending</strong>
            <span>{log.skipped_count} skipped</span>
          </div>
          {log.log_head.map((item) => (
            <div className={`provider-config-sync-auto-log-row ${item.status}`} key={item.hunk_id}>
              <div>
                <strong>{item.operation}</strong>
                <span>{item.status} · {item.row_count} rows</span>
                <small>{item.preview}</small>
              </div>
              {item.status !== "applied" && (
                <button
                  type="button"
                  className="btn-secondary"
                  disabled={busy}
                  onClick={() => onFixHunk(item.hunk_id)}
                >
                  LLM hunk
                </button>
              )}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function AutoPolicyEditor({
  title,
  policy,
  disabled,
  allowInherit = false,
  onChange,
}: {
  title: string;
  policy: Record<ProviderConfigSyncAutoOperation, ProviderConfigSyncAutoOverrideMode>;
  disabled: boolean;
  allowInherit?: boolean;
  onChange: (policy: Record<ProviderConfigSyncAutoOperation, ProviderConfigSyncAutoOverrideMode>) => void;
}) {
  const options = allowInherit ? [{ id: "inherit", label: "Inherit" }, ...AUTO_MODES] : AUTO_MODES;
  return (
    <div className="provider-config-sync-settings-card">
      <strong>{title}</strong>
      {AUTO_OPERATIONS.map((operation) => (
        <label key={operation.id}>
          <span>{operation.label}</span>
          <select
            aria-label={`${title} ${operation.label}`}
            value={policy[operation.id]}
            disabled={disabled}
            onChange={(e) => onChange({ ...policy, [operation.id]: e.target.value as ProviderConfigSyncAutoOverrideMode })}
          >
            {options.map((mode) => (
              <option key={mode.id} value={mode.id}>{mode.label}</option>
            ))}
          </select>
        </label>
      ))}
    </div>
  );
}

function EditableAlignedFileDiff({
  busy,
  unified,
  specific,
  debouncedUnifiedContent,
  debouncedSpecificContent,
  unifiedDirty,
  specificDirty,
  onUnifiedChange,
  onSpecificChange,
  onApplyUnifiedBlock,
  onApplySpecificBlock,
  onApplyUnifiedRows,
  onApplySpecificRows,
  onSaveUnified,
  onSaveSpecific,
  unifiedHeaderActions,
  specificHeaderActions,
}: {
  busy: boolean;
  unified: ProviderConfigSyncFile;
  specific: ProviderConfigSyncFile;
  debouncedUnifiedContent: string;
  debouncedSpecificContent: string;
  unifiedDirty: boolean;
  specificDirty: boolean;
  onUnifiedChange: (lineNumber: number | null, fallbackIndex: number, content: string) => void;
  onSpecificChange: (lineNumber: number | null, fallbackIndex: number, content: string) => void;
  onApplyUnifiedBlock: () => void;
  onApplySpecificBlock: () => void;
  onApplyUnifiedRows: (rows: AlignedDiffRow[]) => void;
  onApplySpecificRows: (rows: AlignedDiffRow[]) => void;
  onSaveUnified: () => void;
  onSaveSpecific: () => void;
  unifiedHeaderActions?: ReactNode;
  specificHeaderActions?: ReactNode;
}) {
  return (
    <AlignedDiffView
      className="provider-config-sync-specific-content"
      leftLabel="Unified"
      rightLabel={specific.provider_names.join(", ")}
      leftPath={unified.path}
      rightPath={specific.path}
      unifiedContent={debouncedUnifiedContent}
      specificContent={debouncedSpecificContent}
      leftHeaderActions={unifiedHeaderActions}
      rightHeaderActions={specificHeaderActions}
      editable={{
        busy,
        leftDirty: unifiedDirty,
        rightDirty: specificDirty,
        leftWritable: unified.writable && !busy,
        rightWritable: specific.writable && !busy,
        onSaveLeft: onSaveUnified,
        onSaveRight: onSaveSpecific,
        onChangeLeft: onUnifiedChange,
        onChangeRight: onSpecificChange,
        onApplyLeftBlock: onApplyUnifiedBlock,
        onApplyRightBlock: onApplySpecificBlock,
        onApplyLeftRows: onApplyUnifiedRows,
        onApplyRightRows: onApplySpecificRows,
      }}
    />
  );
}

interface EditableDiffControls {
  busy: boolean;
  leftDirty: boolean;
  rightDirty: boolean;
  leftWritable: boolean;
  rightWritable: boolean;
  onSaveLeft: () => void;
  onSaveRight: () => void;
  onChangeLeft: (lineNumber: number | null, fallbackIndex: number, content: string) => void;
  onChangeRight: (lineNumber: number | null, fallbackIndex: number, content: string) => void;
  onApplyLeftBlock: () => void;
  onApplyRightBlock: () => void;
  onApplyLeftRows: (rows: AlignedDiffRow[]) => void;
  onApplyRightRows: (rows: AlignedDiffRow[]) => void;
}

function EditableDiffCell({
  editorKey,
  label,
  lineNumber,
  fallbackIndex,
  text,
  cellClassName,
  writable,
  active,
  onActivate,
  onDeactivate,
  onChange,
}: {
  editorKey: string;
  label: string;
  lineNumber: number | null;
  fallbackIndex: number;
  text: string;
  cellClassName: string;
  writable: boolean;
  active: boolean;
  onActivate: (key: string) => void;
  onDeactivate: (key: string) => void;
  onChange: (lineNumber: number | null, fallbackIndex: number, content: string) => void;
}) {
  const editorRef = useRef<HTMLTextAreaElement>(null);
  const rows = Math.max(1, text.split(/\r?\n/).length);
  const resizeEditor = useCallback((editor: HTMLTextAreaElement) => {
    editor.style.height = "auto";
    editor.style.height = `${editor.scrollHeight}px`;
  }, []);

  useLayoutEffect(() => {
    const editor = editorRef.current;
    if (!editor) return;
    if (document.activeElement !== editor && editor.value !== text) {
      editor.value = text;
    }
    resizeEditor(editor);
  }, [resizeEditor, text]);

  useEffect(() => {
    if (!active) return;
    const editor = editorRef.current;
    editor?.focus();
    editor?.setSelectionRange(editor.value.length, editor.value.length);
  }, [active]);

  if (!active) {
    return (
      <div className={cellClassName}>
        <pre
          className="provider-config-sync-aligned-diff-cell-text"
          tabIndex={writable ? 0 : undefined}
          onDoubleClick={() => {
            if (writable) onActivate(editorKey);
          }}
          onKeyDown={(e) => {
            if (!writable || (e.key !== "Enter" && e.key !== "F2")) return;
            e.preventDefault();
            onActivate(editorKey);
          }}
        >
          {text}
        </pre>
      </div>
    );
  }

  return (
    <div className={cellClassName}>
      <textarea
        ref={editorRef}
        aria-label={`${label} line ${lineNumber ?? fallbackIndex + 1}`}
        className="provider-config-sync-aligned-diff-cell-editor"
        defaultValue={text}
        readOnly={!writable}
        rows={rows}
        spellCheck={false}
        onBlur={() => onDeactivate(editorKey)}
        onChange={(e) => {
          resizeEditor(e.target);
          onChange(lineNumber, fallbackIndex, e.target.value);
        }}
      />
    </div>
  );
}

function diffCellTone(row: AlignedDiffRow, side: "left" | "right"): "same" | "changed" | "empty" {
  if (row.kind === "same") return "same";
  if (row.kind === "changed") return "changed";
  if (row.kind === "removed") return side === "left" ? "changed" : "empty";
  return side === "right" ? "changed" : "empty";
}

function diffCellClassName(row: AlignedDiffRow, side: "left" | "right"): string {
  const tone = diffCellTone(row, side);
  return `provider-config-sync-diff-cell provider-config-sync-diff-cell-${side} ${tone}`;
}

function editableDiffRowKey(row: AlignedDiffRow, fallbackIndex: number): string {
  const unifiedKey = row.unifiedLine === null ? `u-new-${fallbackIndex}` : `u-${row.unifiedLine}`;
  const specificKey = row.specificLine === null ? `s-new-${fallbackIndex}` : `s-${row.specificLine}`;
  return `${unifiedKey}:${specificKey}`;
}

function diffCounts(rows: AlignedDiffRow[]) {
  return rows.reduce(
    (counts, row) => {
      if (row.kind === "added") counts.added += 1;
      if (row.kind === "removed") counts.removed += 1;
      if (row.kind === "changed") counts.changed += 1;
      return counts;
    },
    { added: 0, removed: 0, changed: 0 },
  );
}

function diffCountsLabel(counts: ReturnType<typeof diffCounts>): string {
  const parts = [
    counts.changed ? `${counts.changed} changed` : "",
    counts.added ? `${counts.added} added` : "",
    counts.removed ? `${counts.removed} removed` : "",
  ].filter(Boolean);
  return parts.length > 0 ? parts.join(" · ") : "0 diffs";
}

function DiffHeaderSide({
  label,
  path,
  dirty,
  writable,
  busy,
  onSave,
  actions,
}: {
  label: string;
  path?: string;
  dirty: boolean;
  writable: boolean;
  busy: boolean;
  onSave: () => void;
  actions?: ReactNode;
}) {
  return (
    <div className="provider-config-sync-aligned-diff-header-side">
      <div>
        <span>{label}</span>
        {path && <small>{path}</small>}
      </div>
      {(dirty || actions) && (
        <div className="provider-config-sync-aligned-diff-header-actions">
          {actions}
          {dirty && (
            <button
              type="button"
              className="btn-secondary"
              disabled={busy || !writable}
              onClick={onSave}
            >
              Save {label}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

function DiffBlockControls({
  counts,
  editable,
  leftLabel,
  rightLabel,
  onNextDiff,
}: {
  counts: ReturnType<typeof diffCounts>;
  editable?: EditableDiffControls;
  leftLabel: string;
  rightLabel: string;
  onNextDiff: () => void;
}) {
  const changedCount = counts.added + counts.removed + counts.changed;
  return (
    <div className="provider-config-sync-aligned-diff-block-controls">
      <strong>{changedCount === 0 ? "Aligned" : diffCountsLabel(counts)}</strong>
      {editable && changedCount > 0 && (
        <div>
          <button
            type="button"
            className="btn-secondary provider-config-sync-next-diff-button"
            onClick={onNextDiff}
          >
            Next diff
          </button>
          <ArrowApplyButton
            direction="left"
            label={`Apply block to ${leftLabel}`}
            onClick={editable.onApplyLeftBlock}
          />
          <ArrowApplyButton
            direction="right"
            label={`Apply block to ${rightLabel}`}
            onClick={editable.onApplyRightBlock}
          />
        </div>
      )}
    </div>
  );
}

function ArrowApplyButton({
  direction,
  label,
  onClick,
}: {
  direction: "left" | "right";
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      className="btn-secondary provider-config-sync-diff-arrow-button"
      aria-label={label}
      title={label}
      onClick={onClick}
    >
      {direction === "left" ? "←" : "→"}
    </button>
  );
}

function DiffHeaderLabel({ label, path, actions }: { label: string; path?: string; actions?: ReactNode }) {
  return (
    <div className="provider-config-sync-aligned-diff-header-side">
      <div>
        <span>{label}</span>
        {path && <small>{path}</small>}
      </div>
      {actions && <div className="provider-config-sync-aligned-diff-header-actions">{actions}</div>}
    </div>
  );
}

function AlignedDiffView({
  className,
  leftLabel,
  rightLabel,
  leftPath,
  rightPath,
  unifiedContent,
  specificContent,
  leftHeaderActions,
  rightHeaderActions,
  editable,
}: {
  className?: string;
  leftLabel: string;
  rightLabel: string;
  leftPath?: string;
  rightPath?: string;
  unifiedContent: string;
  specificContent: string;
  leftHeaderActions?: ReactNode;
  rightHeaderActions?: ReactNode;
  editable?: EditableDiffControls;
}) {
  const rows = useMemo(
    () => buildAlignedDiffRows(unifiedContent, specificContent),
    [unifiedContent, specificContent],
  );
  const hunks = useMemo(() => buildDiffHunks(rows), [rows]);
  const hunkByFirstRow = useMemo(
    () => new Map(hunks.map((hunk) => [hunk.rows[0]?.key, hunk])),
    [hunks],
  );
  const counts = useMemo(() => diffCounts(rows), [rows]);
  const diffRowKeys = useMemo(() => rows.filter((row) => row.kind !== "same").map((row) => row.key), [rows]);
  const diffRowNodes = useRef(new Map<string, HTMLDivElement>());
  const nextDiffIndex = useRef(0);
  const [highlightedDiffKey, setHighlightedDiffKey] = useState<string | null>(null);
  const [activeEditorKey, setActiveEditorKey] = useState<string | null>(null);

  useEffect(() => {
    nextDiffIndex.current = 0;
    diffRowNodes.current.clear();
    setHighlightedDiffKey(null);
    setActiveEditorKey(null);
  }, [unifiedContent, specificContent]);

  const registerDiffRow = useCallback((key: string, node: HTMLDivElement | null) => {
    if (node) {
      diffRowNodes.current.set(key, node);
      return;
    }
    diffRowNodes.current.delete(key);
  }, []);

  const goToNextDiff = useCallback(() => {
    if (diffRowKeys.length === 0) return;
    const key = diffRowKeys[nextDiffIndex.current % diffRowKeys.length];
    nextDiffIndex.current = (nextDiffIndex.current + 1) % diffRowKeys.length;
    const node = diffRowNodes.current.get(key);
    node?.scrollIntoView({ behavior: "smooth", block: "center", inline: "nearest" });
    setHighlightedDiffKey(key);
    window.setTimeout(() => setHighlightedDiffKey((current) => (current === key ? null : current)), 900);
  }, [diffRowKeys]);

  const deactivateEditor = useCallback((key: string) => {
    setActiveEditorKey((current) => (current === key ? null : current));
  }, []);

  return (
    <div className={`${className ? `${className} ` : ""}provider-config-sync-aligned-diff`}>
      <div className="provider-config-sync-aligned-diff-header">
        {editable ? (
          <DiffHeaderSide
            label={leftLabel}
            path={leftPath}
            dirty={editable.leftDirty}
            writable={editable.leftWritable}
            busy={editable.busy}
            onSave={editable.onSaveLeft}
            actions={leftHeaderActions}
          />
        ) : (
          <DiffHeaderLabel label={leftLabel} path={leftPath} actions={leftHeaderActions} />
        )}
        <DiffBlockControls
          counts={counts}
          editable={editable}
          leftLabel={leftLabel}
          rightLabel={rightLabel}
          onNextDiff={goToNextDiff}
        />
        {editable ? (
          <DiffHeaderSide
            label={rightLabel}
            path={rightPath}
            dirty={editable.rightDirty}
            writable={editable.rightWritable}
            busy={editable.busy}
            onSave={editable.onSaveRight}
            actions={rightHeaderActions}
          />
        ) : (
          <DiffHeaderLabel label={rightLabel} path={rightPath} actions={rightHeaderActions} />
        )}
      </div>
      <div className="provider-config-sync-aligned-diff-body">
        {rows.map((row, index) => {
          const hunk = hunkByFirstRow.get(row.key);
          const changed = row.kind !== "same";
          const renderKey = editable ? editableDiffRowKey(row, index) : row.key;
          return (
            <div
              className={`provider-config-sync-aligned-diff-row-wrap${highlightedDiffKey === row.key ? " highlighted" : ""}`}
              key={renderKey}
              ref={(node) => {
                if (changed) registerDiffRow(row.key, node);
              }}
            >
              {editable && hunk && (
                <div className="provider-config-sync-aligned-diff-hunk-controls">
                  <span>Hunk</span>
                  <ArrowApplyButton
                    direction="left"
                    label={`Apply hunk to ${leftLabel}`}
                    onClick={() => editable.onApplyLeftRows(hunk.rows)}
                  />
                  <ArrowApplyButton
                    direction="right"
                    label={`Apply hunk to ${rightLabel}`}
                    onClick={() => editable.onApplyRightRows(hunk.rows)}
                  />
                </div>
              )}
              <div className={`provider-config-sync-aligned-diff-row ${row.kind}${editable ? " editable" : ""}`}>
                <span className="provider-config-sync-line-number">{row.unifiedLine ?? ""}</span>
                {editable ? (
                  <EditableDiffCell
                    editorKey={`${renderKey}:left`}
                    label={leftLabel}
                    lineNumber={row.unifiedLine}
                    fallbackIndex={index}
                    text={row.unifiedText}
                    cellClassName={diffCellClassName(row, "left")}
                    writable={editable.leftWritable}
                    active={activeEditorKey === `${renderKey}:left`}
                    onActivate={setActiveEditorKey}
                    onDeactivate={deactivateEditor}
                    onChange={editable.onChangeLeft}
                  />
                ) : (
                  <div className={diffCellClassName(row, "left")}>
                    <pre>{row.unifiedText}</pre>
                  </div>
                )}
                {editable && (
                  <div className="provider-config-sync-aligned-diff-line-controls">
                    {row.kind !== "same" && (
                      <>
                        <ArrowApplyButton
                          direction="left"
                          label={`Apply line to ${leftLabel}`}
                          onClick={() => editable.onApplyLeftRows([row])}
                        />
                        <ArrowApplyButton
                          direction="right"
                          label={`Apply line to ${rightLabel}`}
                          onClick={() => editable.onApplyRightRows([row])}
                        />
                      </>
                    )}
                  </div>
                )}
                <span className="provider-config-sync-line-number">{row.specificLine ?? ""}</span>
                {editable ? (
                  <EditableDiffCell
                    editorKey={`${renderKey}:right`}
                    label={rightLabel}
                    lineNumber={row.specificLine}
                    fallbackIndex={index}
                    text={row.specificText}
                    cellClassName={diffCellClassName(row, "right")}
                    writable={editable.rightWritable}
                    active={activeEditorKey === `${renderKey}:right`}
                    onActivate={setActiveEditorKey}
                    onDeactivate={deactivateEditor}
                    onChange={editable.onChangeRight}
                  />
                ) : (
                  <div className={diffCellClassName(row, "right")}>
                    <pre>{row.specificText}</pre>
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function StructuredMissingSpecific({ specific }: { specific: ProviderConfigSyncFile }) {
  return (
    <div className="provider-config-sync-empty">
      <div>Not configured yet.</div>
      <small>Apply unified to create {specific.label}.</small>
    </div>
  );
}

function MissingSpecificColumns({
  unified,
  specific,
  unifiedContent,
}: {
  unified: ProviderConfigSyncFile;
  specific: ProviderConfigSyncFile;
  unifiedContent: string;
}) {
  return (
    <div className="provider-config-sync-editor-grid provider-config-sync-missing-columns">
      <div className="provider-config-sync-editor-card">
        <div className="provider-config-sync-card-header">
          <span>Unified</span>
          <small>{unified.exists ? `${formatTokens(unified.token_count)} est.` : "not configured"}</small>
        </div>
        <div className="provider-config-sync-specific-content provider-config-sync-readonly-pane">
          {unified.exists ? (
            <pre>{unifiedContent || ""}</pre>
          ) : (
            <div className="provider-config-sync-empty">Unified not configured yet.</div>
          )}
        </div>
      </div>
      <div className="provider-config-sync-editor-card">
        <div className="provider-config-sync-card-header">
          <span>{specific.label}</span>
          <small>not on disk</small>
        </div>
        <div className="provider-config-sync-empty">
          <div>{specific.label} not configured yet.</div>
          <small>Apply unified to create this file from the unified source.</small>
        </div>
      </div>
    </div>
  );
}

function McpServerFields({
  server,
  readOnly,
  onChange,
  onRemove,
}: {
  server: McpServerDraft;
  readOnly: boolean;
  onChange?: (server: McpServerDraft) => void;
  onRemove?: () => void;
}) {
  const set = (patch: Partial<McpServerDraft>) => onChange?.({ ...server, ...patch });
  return (
    <div className="provider-config-sync-item-card">
      <div className="provider-config-sync-item-title">
        <input
          value={server.name}
          onChange={(e) => set({ name: e.target.value })}
          readOnly={readOnly}
          placeholder="server name"
        />
        {!readOnly && onRemove && (
          <button type="button" className="btn-secondary" onClick={onRemove}>
            Remove
          </button>
        )}
      </div>
      <label>
        <span>Command</span>
        <input value={server.command} onChange={(e) => set({ command: e.target.value })} readOnly={readOnly} />
      </label>
      <label>
        <span>Arguments</span>
        <textarea value={server.args} onChange={(e) => set({ args: e.target.value })} readOnly={readOnly} />
      </label>
      <label>
        <span>Environment</span>
        <textarea value={server.env} onChange={(e) => set({ env: e.target.value })} readOnly={readOnly} />
      </label>
      <label>
        <span>Extra fields</span>
        <textarea value={server.extra} onChange={(e) => set({ extra: e.target.value })} readOnly={readOnly} />
      </label>
    </div>
  );
}

function StructuredDiffSummary({ unified, specific }: { unified: string[]; specific: string[] }) {
  const added = specific.filter((item) => !unified.includes(item));
  const missing = unified.filter((item) => !specific.includes(item));
  if (added.length === 0 && missing.length === 0) {
    return <div className="provider-config-sync-structured-diff ok">Same item names as unified.</div>;
  }
  return (
    <div className="provider-config-sync-structured-diff">
      {missing.length > 0 && <span>Missing: {missing.join(", ")}</span>}
      {added.length > 0 && <span>Only here: {added.join(", ")}</span>}
    </div>
  );
}

function commonItemContentWithField(
  item: CommonItemDraft,
  field: keyof CommonItemDraft,
  value: string,
): string | null {
  return stringifyCommonItemDraft({ ...item, [field]: value });
}

function StructuredEditableFieldDiffs({
  busy,
  unified,
  specific,
  unifiedItem,
  specificItem,
  unifiedDirty,
  specificDirty,
  onUnifiedContentChange,
  onSpecificContentChange,
  onSaveUnified,
  onSaveSpecific,
}: {
  busy: boolean;
  unified: ProviderConfigSyncFile;
  specific: ProviderConfigSyncFile;
  unifiedItem: CommonItemDraft;
  specificItem: CommonItemDraft;
  unifiedDirty: boolean;
  specificDirty: boolean;
  onUnifiedContentChange: (content: string) => void;
  onSpecificContentChange: (content: string) => void;
  onSaveUnified: () => void;
  onSaveSpecific: () => void;
}) {
  const onUnifiedFieldChange = (
    field: keyof CommonItemDraft,
    lineNumber: number | null,
    fallbackIndex: number,
    content: string,
  ) => {
    const fieldContent = replaceLine(unifiedItem[field], lineNumber, fallbackIndex, content);
    const nextContent = commonItemContentWithField(unifiedItem, field, fieldContent);
    if (nextContent) onUnifiedContentChange(nextContent);
  };
  const onSpecificFieldChange = (
    field: keyof CommonItemDraft,
    lineNumber: number | null,
    fallbackIndex: number,
    content: string,
  ) => {
    const fieldContent = replaceLine(specificItem[field], lineNumber, fallbackIndex, content);
    const nextContent = commonItemContentWithField(specificItem, field, fieldContent);
    if (nextContent) onSpecificContentChange(nextContent);
  };
  const applyUnifiedFieldRows = (field: keyof CommonItemDraft, rows: AlignedDiffRow[]) => {
    const fieldContent = applyRowsToContent(unifiedItem[field], rows, "unified");
    const nextContent = commonItemContentWithField(unifiedItem, field, fieldContent);
    if (nextContent) onUnifiedContentChange(nextContent);
  };
  const applySpecificFieldRows = (field: keyof CommonItemDraft, rows: AlignedDiffRow[]) => {
    const fieldContent = applyRowsToContent(specificItem[field], rows, "specific");
    const nextContent = commonItemContentWithField(specificItem, field, fieldContent);
    if (nextContent) onSpecificContentChange(nextContent);
  };
  const applyUnifiedFieldBlock = (field: keyof CommonItemDraft) => {
    const nextContent = commonItemContentWithField(unifiedItem, field, specificItem[field]);
    if (nextContent) onUnifiedContentChange(nextContent);
  };
  const applySpecificFieldBlock = (field: keyof CommonItemDraft) => {
    const nextContent = commonItemContentWithField(specificItem, field, unifiedItem[field]);
    if (nextContent) onSpecificContentChange(nextContent);
  };

  return (
    <div className="provider-config-sync-structured-field-diffs">
      {COMMON_ITEM_FIELDS.map(({ field, label }) => (
        <section className="provider-config-sync-structured-field-diff" key={label}>
          <div className="provider-config-sync-structured-field-diff-title">{label}</div>
          <AlignedDiffView
            leftLabel="Unified"
            rightLabel={specific.provider_names.join(", ")}
            leftPath={unified.path}
            rightPath={specific.path}
            unifiedContent={unifiedItem[field]}
            specificContent={specificItem[field]}
            editable={{
              busy,
              leftDirty: unifiedDirty,
              rightDirty: specificDirty,
              leftWritable: unified.writable && !busy,
              rightWritable: specific.writable && !busy,
              onSaveLeft: onSaveUnified,
              onSaveRight: onSaveSpecific,
              onChangeLeft: (lineNumber, fallbackIndex, content) => {
                onUnifiedFieldChange(field, lineNumber, fallbackIndex, content);
              },
              onChangeRight: (lineNumber, fallbackIndex, content) => {
                onSpecificFieldChange(field, lineNumber, fallbackIndex, content);
              },
              onApplyLeftBlock: () => applyUnifiedFieldBlock(field),
              onApplyRightBlock: () => applySpecificFieldBlock(field),
              onApplyLeftRows: (rows) => applyUnifiedFieldRows(field, rows),
              onApplyRightRows: (rows) => applySpecificFieldRows(field, rows),
            }}
          />
        </section>
      ))}
    </div>
  );
}

function StructuredParseError() {
  return <div className="provider-config-sync-empty">This item needs a valid converted shape before it can be shown here.</div>;
}
