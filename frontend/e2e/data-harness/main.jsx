import React from "react";
import ReactDOM from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import StoreDataPage from "../../src/pages/StoreDataPage.jsx";
import StoreDataPanel from "../../src/platform/panels/StoreDataPanel.jsx";
import StoreUsagePanel from "../../src/platform/panels/StoreUsagePanel.jsx";
import { HelpProvider } from "../../src/ui/help/index.js";
import "../../src/index.css";
import "../../src/ui/help/help.css";
import "../../src/platform/platform.css";
import "../../src/styles/kc.css";
import "../../src/styles/brand.css";
import { exportsList, offboarding, usage } from "./fixtures.js";

const q = new URLSearchParams(window.location.search);
const view = q.get("view") || "page";
const theme = q.get("theme") || "light";
document.documentElement.setAttribute("data-theme", theme);

const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
let plan = q.get("plan") || "trial";
window.fetch = async (url, opts = {}) => {
  const path = String(url).replace(/^https?:\/\/[^/]+/, "");
  const method = (opts.method || "GET").toUpperCase();
  if (path === "/api/settings/usage") {
    if (q.get("single")) return json({ available: false, message: "Usage is measured on the shared service." });
    const u = usage(plan);
    delete u.plan.plans;
    return json({ available: true, ...u });
  }
  if (path === "/api/settings/exports") {
    if (q.get("single")) return json({ available: false, exports: [] });
    return json({ available: true, store_code: "ALPHA", exports: q.get("empty") ? [] : exportsList, link_hours: 24, keep_days: 7 });
  }
  if (path.startsWith("/api/platform/stores/ALPHA/usage")) return json(usage(plan));
  if (path === "/api/platform/stores/ALPHA/plan" && method === "PUT") {
    plan = JSON.parse(opts.body).plan;
    return json(usage(plan));
  }
  if (path === "/api/platform/stores/ALPHA/exports") return json({ store_code: "ALPHA", exports: exportsList, link_hours: 24, keep_days: 7 });
  if (path === "/api/platform/stores/ALPHA/offboarding") return json(offboarding(q.get("off") || "scheduled"));
  return json({ error: "not_found", message: "Not found." }, 404);
};

function Shell({ children }) {
  return (
    <main style={{ maxWidth: 1180, margin: "0 auto", padding: "16px", boxSizing: "border-box" }}>{children}</main>
  );
}

const body =
  view === "usage" ? <StoreUsagePanel storeCode="ALPHA" /> :
  view === "data" ? <StoreDataPanel storeCode="ALPHA" status="archived" onChanged={() => {}} /> :
  <StoreDataPage />;

ReactDOM.createRoot(document.getElementById("root")).render(
  <HelpProvider>
    <MemoryRouter>
      <Shell>{body}</Shell>
    </MemoryRouter>
  </HelpProvider>,
);
