import { createRoot } from "react-dom/client";
import { ProviderConfigSyncPage } from "./ProviderConfigSyncPage.js";
import { createFetchProviderConfigSyncClient } from "./client.js";
import "./styles.css";

const rootElement = document.getElementById("root");
if (!rootElement) {
  throw new Error("Standalone app root element is missing");
}

createRoot(rootElement).render(
  <ProviderConfigSyncPage
    open
    cwd={null}
    onClose={() => undefined}
    client={createFetchProviderConfigSyncClient({ baseUrl: window.location.origin })}
  />,
);
