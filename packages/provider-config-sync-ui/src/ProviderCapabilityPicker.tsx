import { useEffect, useMemo, useState } from "react";
import type {
  ProviderConfigSyncCapabilityPickerOutput,
  ProviderConfigSyncCapabilityPickerSource,
  ProviderConfigSyncScope,
} from "@better-agent/provider-config-sync-core";
import { type ProviderConfigSyncApiClient } from "./client.js";

export interface ProviderCapabilityPickerProps {
  open: boolean;
  cwd?: string;
  client: Pick<ProviderConfigSyncApiClient, "listCapabilityPickerSources">;
  onSelect: (source: ProviderConfigSyncCapabilityPickerSource, output?: ProviderConfigSyncCapabilityPickerOutput) => void;
  onClose?: () => void;
}

const SCOPE_LABELS: Record<ProviderConfigSyncScope, string> = {
  global: "Global",
  project: "Project",
};

const CATEGORY_LABELS: Record<string, string> = {
  instructions: "Instructions",
  memory: "Memory",
  config: "Provider settings",
  skill: "Skills",
  agent: "Subagents",
  command: "Commands",
};

function sourceSearchText(source: ProviderConfigSyncCapabilityPickerSource): string {
  return [
    source.source_label,
    source.source_cwd,
    source.capability.name,
    source.capability.category,
    source.capability.capability_id,
    source.preferred_entry?.content ?? "",
  ].join(" ").toLowerCase();
}

function formatTokens(count: number): string {
  if (count >= 1_000_000) return `${(count / 1_000_000).toFixed(1)}M tok`;
  if (count >= 10_000) return `${Math.round(count / 1_000)}K tok`;
  return `${count.toLocaleString()} tok`;
}

export function ProviderCapabilityPicker({ open, cwd = "", client, onSelect, onClose }: ProviderCapabilityPickerProps) {
  const [sources, setSources] = useState<ProviderConfigSyncCapabilityPickerSource[]>([]);
  const [query, setQuery] = useState("");
  const [scope, setScope] = useState<ProviderConfigSyncScope | "all">("all");
  const [providerKind, setProviderKind] = useState("all");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    setBusy(true);
    setError(null);
    void client.listCapabilityPickerSources(cwd)
      .then((body) => setSources(body.sources))
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setBusy(false));
  }, [open, cwd, client]);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return sources.filter((source) => {
      if (scope !== "all" && source.source_scope !== scope) return false;
      if (providerKind !== "all" && !source.outputs.some((output) => output.provider_kind === providerKind)) return false;
      if (!needle) return true;
      return sourceSearchText(source).includes(needle);
    });
  }, [sources, query, scope, providerKind]);

  const providerOptions = useMemo(() => {
    const byKind = new Map<string, string>();
    for (const source of sources) {
      for (const output of source.outputs) {
        if (output.provider_kind && !byKind.has(output.provider_kind)) {
          byKind.set(output.provider_kind, output.provider_name || output.provider_kind);
        }
      }
    }
    return [...byKind.entries()].map(([kind, name]) => ({ kind, name }));
  }, [sources]);

  const selectSource = (source: ProviderConfigSyncCapabilityPickerSource) => {
    if (providerKind === "all") {
      onSelect(source);
      return;
    }
    onSelect(source, source.outputs.find((output) => output.provider_kind === providerKind));
  };

  if (!open) return null;

  return (
    <div className="provider-capability-picker" data-testid="provider-capability-picker">
      <div className="provider-capability-picker-head">
        <div>
          <h2>Capabilities</h2>
          <span>{filtered.length.toLocaleString()} available</span>
        </div>
        {onClose && <button className="btn-secondary" onClick={onClose}>Close</button>}
      </div>

      <div className="provider-capability-picker-toolbar">
        <input
          className="provider-config-sync-input"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Search capabilities"
        />
        <select
          className="provider-config-sync-select"
          value={scope}
          onChange={(event) => setScope(event.target.value as ProviderConfigSyncScope | "all")}
        >
          <option value="all">All scopes</option>
          <option value="global">Global</option>
          <option value="project">Project</option>
        </select>
        <select
          className="provider-config-sync-select"
          value={providerKind}
          onChange={(event) => setProviderKind(event.target.value)}
        >
          <option value="all">All provider forms</option>
          {providerOptions.map((provider) => (
            <option key={provider.kind} value={provider.kind}>{provider.name}</option>
          ))}
        </select>
      </div>

      {error && <div className="provider-config-sync-error">{error}</div>}
      {busy && <div className="provider-config-sync-loading">Loading capabilities...</div>}
      {!busy && filtered.length === 0 && <div className="provider-config-sync-empty">No capabilities found.</div>}

      <div className="provider-capability-picker-list">
        {filtered.map((source) => (
          <button
            key={source.source_id}
            className="provider-capability-picker-item"
            onClick={() => selectSource(source)}
          >
            <span>
              <strong>{source.capability.name}</strong>
              <small>{CATEGORY_LABELS[source.capability.category] ?? source.capability.category}</small>
            </span>
            <span>
              <em>{SCOPE_LABELS[source.source_scope as ProviderConfigSyncScope]} · {source.source_label}</em>
              <small>
                {providerKind === "all"
                  ? `${source.outputs.length} provider forms`
                  : source.outputs.find((output) => output.provider_kind === providerKind)?.provider_name ?? "Unavailable"}
                {" · "}
                {formatTokens(source.capability.total_token_count)}
              </small>
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}
