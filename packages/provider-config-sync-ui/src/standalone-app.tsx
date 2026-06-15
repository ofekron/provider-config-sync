import { createRoot } from "react-dom/client";
import { ProviderSyncPage } from "./ProviderSyncPage.js";
import { createFetchProviderSyncClient } from "./client.js";
import "./styles.css";

const rootElement = document.getElementById("root");
if (!rootElement) {
  throw new Error("Standalone app root element is missing");
}

createRoot(rootElement).render(
  <ProviderSyncPage
    open
    cwd={null}
    onClose={() => undefined}
    client={createFetchProviderSyncClient({ baseUrl: window.location.origin })}
  />,
);
