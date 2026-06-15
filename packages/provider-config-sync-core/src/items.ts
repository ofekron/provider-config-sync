export interface McpServerDraft {
  name: string;
  command: string;
  args: string;
  env: string;
  extra: string;
}

export interface CommonItemDraft {
  name: string;
  description: string;
  instructions: string;
  metadata: string;
}

export function parseJsonObject(content: string): Record<string, unknown> | null {
  try {
    const value = JSON.parse(content) as unknown;
    return value && typeof value === "object" && !Array.isArray(value)
      ? (value as Record<string, unknown>)
      : null;
  } catch {
    return null;
  }
}

export function stringifyJson(value: unknown): string {
  return JSON.stringify(value, null, 2) + "\n";
}

export function parseMcpServers(content: string): McpServerDraft[] | null {
  const root = parseJsonObject(content);
  const servers = root?.mcpServers;
  if (!servers || typeof servers !== "object" || Array.isArray(servers)) return null;
  return Object.entries(servers as Record<string, unknown>).map(([name, raw]) => {
    const server = raw && typeof raw === "object" && !Array.isArray(raw)
      ? (raw as Record<string, unknown>)
      : {};
    const { command, args, env, ...extra } = server;
    return {
      name,
      command: typeof command === "string" ? command : "",
      args: Array.isArray(args) ? args.map(String).join("\n") : "",
      env: env && typeof env === "object" && !Array.isArray(env) ? stringifyJson(env).trimEnd() : "",
      extra: Object.keys(extra).length ? stringifyJson(extra).trimEnd() : "",
    };
  });
}

export function parseCommonItemDraft(content: string): CommonItemDraft | null {
  const root = parseJsonObject(content);
  if (!root) return null;
  return {
    name: typeof root.name === "string" ? root.name : "",
    description: typeof root.description === "string" ? root.description : "",
    instructions: typeof root.instructions === "string" ? root.instructions : "",
    metadata: root.metadata && typeof root.metadata === "object" && !Array.isArray(root.metadata)
      ? stringifyJson(root.metadata).trimEnd()
      : "{}",
  };
}

export function stringifyCommonItemDraft(item: CommonItemDraft): string | null {
  const metadata = parseJsonObject(item.metadata);
  if (!metadata) return null;
  return stringifyJson({
    name: item.name,
    description: item.description,
    instructions: item.instructions,
    metadata,
  });
}
