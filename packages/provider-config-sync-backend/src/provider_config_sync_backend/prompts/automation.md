Use the Provider Config Sync MCP tools to automatically reconcile known agent provider configs.

First call list_provider_config_worklist once. Do not enumerate projects or capabilities yourself. Use that returned worklist as the complete reconciliation plan for global config and configured projects.

For each listed capability with status diff or missing, inspect the listed entries, choose the best source content, update the unified capability first, then apply it to every provider-specific target that has an equivalent native config. Preserve provider-specific extensions when they are not equivalent common fields. Do not edit unrelated files. Use expected_content, expected_source, and expected_target from the latest reads before every write/apply. Report what changed and what was already aligned.$extra
