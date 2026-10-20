import React, { lazy, Suspense } from "react";
import ReactDOM from "react-dom/client";
import App from "./App.jsx";
import { installTableStacking } from "./ui/stackTables.js";
import { storeFromPath } from "./login/loginModel.js";
import { StoreProvider } from "./store/StoreContext.jsx";
import { HelpProvider } from "./ui/help/index.js";
import "./index.css";
import "./ui/help/help.css";

// /c/{slug}-{token} is the logged-out public checklist page (served by the
// backend's /c/ route): it renders ONLY that checklist's form - never the
// app, its login check or its navigation.
const isPublicChecklist = window.location.pathname.startsWith("/c/");
const PublicChecklistPage = lazy(() => import("./pages/PublicChecklistPage.jsx"));

// Wide tables (class kc-table--stack) stack as cards on phones (ui/stackTables.js).
installTableStacking();

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    {/* Help content and the Help panel's state, for every page (ui/help). */}
    <HelpProvider>
      {isPublicChecklist ? (
        <StoreProvider storeCode={storeFromPath(window.location.pathname)}>
          <Suspense fallback={null}>
            <PublicChecklistPage />
          </Suspense>
        </StoreProvider>
      ) : (
        <App />
      )}
    </HelpProvider>
  </React.StrictMode>
);
