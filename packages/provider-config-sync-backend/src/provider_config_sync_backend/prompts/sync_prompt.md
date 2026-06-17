Use Provider Config Sync for this provider capability change.

Workflow:
1. List provider config capabilities for the current project.
2. Find or create the matching unified capability.
3. Apply the unified capability to every configured provider that has an equivalent native config.
4. If a provider-specific config already has the better version, pull it into the unified capability first, then apply it outward.
5. Preserve provider-specific extensions instead of flattening them away.

Never edit only one provider-native config when the capability has equivalents in Claude, Codex, or Gemini.
