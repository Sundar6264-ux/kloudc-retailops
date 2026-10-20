import { lazy, Suspense, useEffect, useState } from "react";
import { HashRouter, Routes, Route, Navigate, useLocation } from "react-router-dom";
import { AuthProvider, useAuth } from "./AuthContext.jsx";
import { FEATURE_ROUTES, firstAccessiblePath, insightsHomePath, insightsLandingPath, routeForPath } from "./featureRoutes.js";
import { StoreProvider } from "./store/StoreContext.jsx";
import { featureAvailable, routeAvailable } from "./store/gating.js";
const NotAvailablePage = lazy(() => import("./pages/NotAvailablePage.jsx"));
import Sidebar, { readCollapsed, writeCollapsed } from "./components/Sidebar.jsx";
import TopBar from "./components/TopBar.jsx";
import HelpDrawer from "./components/HelpDrawer.jsx";
const ChatWidget = lazy(() => import("./components/ChatWidget.jsx"));
import { HelpCenter, HelpPanel } from "./ui/helpBridge.jsx";
import { readRemembered } from "./ui/useRemembered.js";
import { INSIGHTS_TAB_KEY } from "./components/insights/InsightsHub.jsx";
const LoginPage = lazy(() => import("./pages/LoginPage.jsx"));
const Overview = lazy(() => import("./pages/Overview.jsx"));
const InvoiceSubmissionPage = lazy(() => import("./pages/InvoiceSubmissionPage.jsx"));
const DailyChecklistsPage = lazy(() => import("./pages/DailyChecklistsPage.jsx"));
const ChecklistAdminPage = lazy(() => import("./pages/ChecklistAdminPage.jsx"));
const OpenOrdersPage = lazy(() => import("./pages/OpenOrdersPage.jsx"));
const ItemsSoldPage = lazy(() => import("./pages/ItemsSoldPage.jsx"));
const ReorderPage = lazy(() => import("./pages/ReorderPage.jsx"));
const SupplierProjectionPage = lazy(() => import("./pages/SupplierProjectionPage.jsx"));
const VendorCalendarPage = lazy(() => import("./pages/VendorCalendarPage.jsx"));
const PurchaseOrderPage = lazy(() => import("./pages/PurchaseOrderPage.jsx"));
const PlacedOrdersPage = lazy(() => import("./pages/PlacedOrdersPage.jsx"));
const VendorContactsPage = lazy(() => import("./pages/VendorContactsPage.jsx"));
const DeliveryReviewPage = lazy(() => import("./pages/DeliveryReviewPage.jsx"));
const InventoryReportsPage = lazy(() => import("./pages/InventoryReportsPage.jsx"));
const FixIssuesPage = lazy(() => import("./pages/FixIssuesPage.jsx"));
const ReconciliationPage = lazy(() => import("./pages/ReconciliationPage.jsx"));
const PoSectionsPage = lazy(() => import("./pages/PoSectionsPage.jsx"));
const HelpPage = lazy(() => import("./pages/HelpPage.jsx"));
const UsersPage = lazy(() => import("./pages/UsersPage.jsx"));
const TeamsPage = lazy(() => import("./pages/TeamsPage.jsx"));
const TeamTasksPage = lazy(() => import("./pages/TeamTasksPage.jsx"));
const TeamTemplatesPage = lazy(() => import("./pages/TeamTemplatesPage.jsx"));
const TeamAnnouncementsPage = lazy(() => import("./pages/TeamAnnouncementsPage.jsx"));
const TeamModerationPage = lazy(() => import("./pages/TeamModerationPage.jsx"));
const TeamChatPage = lazy(() => import("./pages/TeamChatPage.jsx"));
const ChangePasswordPage = lazy(() => import("./pages/ChangePasswordPage.jsx"));
const NoAccessPage = lazy(() => import("./pages/NoAccessPage.jsx"));
const SignupPage = lazy(() => import("./pages/SignupPage.jsx"));
const SignupPendingPage = lazy(() => import("./pages/SignupPendingPage.jsx"));
const OwnerSetupPage = lazy(() => import("./pages/OwnerSetupPage.jsx"));
const PlatformConsolePage = lazy(() => import("./pages/PlatformConsolePage.jsx"));
const IntegrationsPage = lazy(() => import("./pages/IntegrationsPage.jsx"));
const FlyersPage = lazy(() => import("./pages/FlyersPage.jsx"));
const InventoryApiPage = lazy(() => import("./pages/InventoryApiPage.jsx"));
const IssueLeadsPage = lazy(() => import("./pages/IssueLeadsPage.jsx"));
// Website ready: Setup > Data & usage (track B), Item vendors (track D) and
// Store settings (A5) - each in its own chunk like every page.
const StoreDataPage = lazy(() => import("./pages/StoreDataPage.jsx"));
const ItemVendorsPage = lazy(() => import("./pages/ItemVendorsPage.jsx"));
const StoreSettingsPage = lazy(() => import("./pages/StoreSettingsPage.jsx"));
const DailyComparePage = lazy(() => import("./pages/insights/DailyComparePage.jsx"));
const HeatmapPage = lazy(() => import("./pages/insights/HeatmapPage.jsx"));
const ChannelsPage = lazy(() => import("./pages/insights/ChannelsPage.jsx"));
const CheckTrendsPage = lazy(() => import("./pages/insights/CheckTrendsPage.jsx"));
const MenuPerformancePage = lazy(() => import("./pages/insights/MenuPerformancePage.jsx"));
const AddonsPage = lazy(() => import("./pages/insights/AddonsPage.jsx"));
const PrepForecastPage = lazy(() => import("./pages/insights/PrepForecastPage.jsx"));
const ReturningCustomersPage = lazy(() => import("./pages/insights/ReturningCustomersPage.jsx"));
const LossWatchPage = lazy(() => import("./pages/insights/LossWatchPage.jsx"));
const OddChecksPage = lazy(() => import("./pages/insights/OddChecksPage.jsx"));
import { menuPerformanceRedirect } from "./components/insights/insightsModel.js";
import PageSkeleton from "./ui/PageSkeleton.jsx";
import { GlossaryProvider } from "./glossary.jsx";
import { isPlatformUser } from "./login/loginModel.js";
// Styles last, so the design layer (styles/kc.css) wins over older rules:
// index.css (tokens + legacy rules), then the KloudC layer. main.jsx's own
// import of index.css is the same module (loaded once, here).
import "./index.css";
import "./styles/kc.css";
import "./styles/brand.css";

// Wraps a page element: 403-equivalent redirect (to the user's first
// accessible tab, or the no-access page) if they haven't been granted this
// feature - mirrors the backend's own per-feature 403s (app/features.py),
// just resolved client-side so it doesn't even flash the wrong page.
//
// Before the grant check: a page THIS STORE can't have (its POS lacks a
// capability, or a store feature is off - store/gating.js) goes to "Not
// available for this store" for everyone, admins included. `routeKey` is
// the featureRoutes.js key when it differs from the feature.
function Protected({ feature, routeKey, children }) {
  const { hasFeature, store } = useAuth();
  const key = routeKey || feature;
  if (!storeHasPage(key, feature, store)) return <NotAvailableRedirect page={key} />;
  if (!hasFeature(feature)) {
    return <Navigate to={firstAccessiblePath(hasFeature, store)} replace />;
  }
  return children;
}

function storeHasPage(routeKey, feature, store) {
  return routeAvailable(routeKey, store) && (!feature || featureAvailable(feature, store));
}

function NotAvailableRedirect({ page }) {
  return <Navigate to={`/not-available?page=${encodeURIComponent(page || "")}`} replace />;
}

// Admin-only setup pages (no grant): the store check, then admin.
function AdminPage({ routeKey, fallback = "/", children }) {
  const { user, store } = useAuth();
  if (routeKey && !routeAvailable(routeKey, store)) return <NotAvailableRedirect page={routeKey} />;
  return user?.is_admin ? children : <Navigate to={fallback} replace />;
}

const ROUTE_KEY_BY_PATH = Object.fromEntries(FEATURE_ROUTES.map((r) => [r.path, r.key || r.feature]));

// Insights pages (docs/INSIGHTS_CONTRACT.md): the `sales_insights` grant;
// admin-only pages send anyone else, and sales-only pages send a "Hide sales
// numbers" user, to the first main-section page they can open.
const INSIGHTS_PAGES = [
  { path: "/insights/daily", Page: DailyComparePage, salesOnly: true },
  { path: "/insights/loss-watch", Page: LossWatchPage, adminOnly: true },
  { path: "/insights/odd-checks", Page: OddChecksPage, adminOnly: true },
  { path: "/insights/prep-forecast", Page: PrepForecastPage },
  { path: "/insights/menu-trends", Page: MenuPerformancePage, props: { view: "trends" }, salesOnly: true },
  { path: "/insights/add-ons", Page: AddonsPage, salesOnly: true },
  { path: "/insights/returning-customers", Page: ReturningCustomersPage },
  // "Also in <pos>" (same numbers as the POS's own reports).
  { path: "/insights/channels", Page: ChannelsPage, salesOnly: true },
  { path: "/insights/check-trends", Page: CheckTrendsPage, salesOnly: true },
  { path: "/insights/busy-hours", Page: HeatmapPage },
  { path: "/insights/best-sellers", Page: MenuPerformancePage, props: { view: "sellers" }, salesOnly: true },
];

function InsightsRoute({ path, adminOnly, salesOnly, children }) {
  const { user, canSeeSales, store } = useAuth();
  const key = ROUTE_KEY_BY_PATH[path];
  if (!storeHasPage(key, "sales_insights", store)) return <NotAvailableRedirect page={key} />;
  if (adminOnly && !user?.is_admin) return <Navigate to={insightsHomePath(canSeeSales)} replace />;
  // "Hide sales numbers" users: these pages are nothing but sales (the API 403s).
  if (salesOnly && !canSeeSales) return <Navigate to={insightsHomePath(false)} replace />;
  return <Protected feature="sales_insights">{children}</Protected>;
}

// /insights (the menu's one Insights link): the tile this user opened last
// on the hub (kc.tab.insights), else the first main-section page.
function InsightsHome() {
  const { user, hasFeature, canSeeSales, store } = useAuth();
  if (!featureAvailable("sales_insights", store)) return <NotAvailableRedirect page="insights_compare" />;
  const to = insightsLandingPath(readRemembered(INSIGHTS_TAB_KEY), { user, hasFeature, canSeeSales, store }) || insightsHomePath(canSeeSales);
  return <Navigate to={to} replace />;
}

// Help Center: track H's (ui/help) when it is in this build, else the
// older Help page. /glossary is its Glossary tab.
function HelpRoute({ tab }) {
  return HelpCenter ? <HelpCenter tab={tab} /> : <HelpPage tab={tab} />;
}

// Menu performance became Menu trends + Best & worst sellers: an old link
// (?tab=top etc.) opens the page and tab that list now lives on.
function OldMenuPerformance() {
  const { search } = useLocation();
  return <Navigate to={menuPerformanceRedirect(search)} replace />;
}

function AppShell() {
  const { user, loading, hasFeature } = useAuth();
  const location = useLocation();
  // Team chat has its own composer at the bottom - the floating Inventory
  // Bot bubble would sit on top of its Send button on a phone.
  const onTeamChat = location.pathname.startsWith("/team/chat");
  // Phone / tablet drawer (TopBar's ☰) and the desktop icon rail (remembered).
  const [navOpen, setNavOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(readCollapsed);
  useEffect(() => setNavOpen(false), [location.pathname]);
  const area = routeForPath(location.pathname)?.area;

  if (loading) {
    return (
      <div
        style={{
          minHeight: "100vh",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          background: "var(--bg)",
        }}
      >
        <p className="muted">Loading…</p>
      </div>
    );
  }

  // Logged out: the one login page (docs/PLATFORM_CONTRACT.md 14) for
  // every address, plus the public sign-up pages.
  if (!user) {
    return (
      <Suspense fallback={<PageSkeleton bare />}>
        <Routes>
          <Route path="/signup" element={<SignupPage />} />
          <Route path="/signup/pending" element={<SignupPendingPage />} />
          <Route path="/setup" element={<OwnerSetupPage />} />
          <Route path="*" element={<LoginPage />} />
        </Routes>
      </Suspense>
    );
  }

  // A master (Store ID PLATFORM) sees only the platform console.
  if (isPlatformUser(user)) {
    return (
      <Suspense fallback={<PageSkeleton bare />}>
        <Routes>
          <Route path="/platform" element={<PlatformConsolePage />} />
          <Route path="*" element={<Navigate to="/platform" replace />} />
        </Routes>
      </Suspense>
    );
  }

  return (
    <div className={`app${collapsed ? " app-nav-collapsed" : ""}`}>
      <a className="kc-skip-link" href="#main" onClick={(e) => { e.preventDefault(); document.getElementById("main")?.focus(); }}>
        Skip to content
      </a>
      <Sidebar
        open={navOpen}
        onClose={() => setNavOpen(false)}
        collapsed={collapsed}
        onToggleCollapsed={() =>
          setCollapsed((v) => {
            writeCollapsed(!v);
            return !v;
          })
        }
      />
      <div className="app-body">
        <TopBar navOpen={navOpen} onToggleNav={() => setNavOpen((v) => !v)} />
      <main className="main" id="main" tabIndex={-1} data-area={area || undefined}>
        <Suspense fallback={<PageSkeleton />}>
        <Routes>
          <Route
            path="/"
            element={
              <Protected feature="overview">
                <Overview />
              </Protected>
            }
          />
          <Route
            path="/invoices"
            element={
              <Protected feature="invoice_submission">
                <InvoiceSubmissionPage />
              </Protected>
            }
          />
          <Route
            path="/daily-checklists"
            element={
              <Protected feature="daily_checklists">
                <DailyChecklistsPage />
              </Protected>
            }
          />
          {/* Checklist builder: admins only (the admin API is require_admin). */}
          <Route
            path="/checklists/admin"
            element={user.is_admin ? <ChecklistAdminPage /> : <Navigate to="/daily-checklists" replace />}
          />
          <Route
            path="/checklists/admin/:type"
            element={user.is_admin ? <ChecklistAdminPage /> : <Navigate to="/daily-checklists" replace />}
          />
          <Route
            path="/open-orders"
            element={
              <Protected feature="open_orders">
                <OpenOrdersPage />
              </Protected>
            }
          />
          <Route
            path="/items-sold"
            element={
              <Protected feature="items_sold">
                <ItemsSoldPage />
              </Protected>
            }
          />
          <Route
            path="/reorder"
            element={
              <Protected feature="reorder_candidates">
                <ReorderPage />
              </Protected>
            }
          />
          <Route
            path="/supplier-projection"
            element={
              <Protected feature="supplier_projection">
                <SupplierProjectionPage />
              </Protected>
            }
          />
          <Route
            path="/vendor-calendar"
            element={
              <Protected feature="vendor_calendar">
                <VendorCalendarPage />
              </Protected>
            }
          />
          <Route
            path="/purchase-order"
            element={
              <Protected feature="purchase_order_cart">
                <PurchaseOrderPage />
              </Protected>
            }
          />
          <Route
            path="/placed-orders"
            element={
              <Protected feature="purchase_order_cart" routeKey="placed_orders">
                <PlacedOrdersPage />
              </Protected>
            }
          />
          {/* Send PO's vendor contacts: same grant as the cart; the page tells a demo login it's off. */}
          <Route
            path="/vendor-contacts"
            element={
              <Protected feature="purchase_order_cart" routeKey="vendor_contacts">
                <VendorContactsPage />
              </Protected>
            }
          />
          <Route
            path="/delivery-review"
            element={
              <Protected feature="delivery_review">
                <DeliveryReviewPage />
              </Protected>
            }
          />
          {/* Transfer Review is retired (automation batch item 9): an old
              link or bookmark lands on Delivery Review. The API stays for old
              app builds. */}
          <Route path="/transfer-review" element={<Navigate to="/delivery-review" replace />} />
          <Route
            path="/inventory-reports"
            element={
              <Protected feature="inventory_reports">
                <InventoryReportsPage />
              </Protected>
            }
          />
          <Route
            path="/fix-issues"
            element={
              <Protected feature="inventory_reports" routeKey="fix_issues">
                <FixIssuesPage />
              </Protected>
            }
          />
          <Route
            path="/reconciliation"
            element={
              <Protected feature="reconciliation">
                <ReconciliationPage />
              </Protected>
            }
          />
          <Route
            path="/po-sections"
            element={
              <Protected feature="category_edits">
                <PoSectionsPage />
              </Protected>
            }
          />
          {/* Old address of the same page (was Product categories). */}
          <Route
            path="/categories"
            element={
              <Protected feature="category_edits">
                <PoSectionsPage />
              </Protected>
            }
          />
          <Route
            path="/team/tasks"
            element={
              <Protected feature="team_tasks">
                <TeamTasksPage />
              </Protected>
            }
          />
          <Route
            path="/team/templates"
            element={
              user.is_admin ? (
                <Protected feature="team_tasks">
                  <TeamTemplatesPage />
                </Protected>
              ) : (
                <Navigate to="/team/tasks" replace />
              )
            }
          />
          <Route
            path="/team/chat"
            element={
              <Protected feature="team_chat">
                <TeamChatPage />
              </Protected>
            }
          />
          {/* Everyone gets the announcements sent to them - no feature grant. */}
          <Route path="/team/announcements" element={<TeamAnnouncementsPage />} />
          <Route
            path="/team/moderation"
            element={user.is_admin ? <TeamModerationPage /> : <Navigate to="/team/announcements" replace />}
          />
          {/* Marketing flyers: the page itself tells a demo login it's off. */}
          <Route
            path="/flyers"
            element={
              <Protected feature="marketing_flyers">
                <FlyersPage />
              </Protected>
            }
          />
          {INSIGHTS_PAGES.map(({ path, Page, props, adminOnly, salesOnly }) => (
            <Route
              key={path}
              path={path}
              element={
                <InsightsRoute path={path} adminOnly={adminOnly} salesOnly={salesOnly}>
                  <Page key={path} {...props} />
                </InsightsRoute>
              }
            />
          ))}
          <Route path="/insights" element={<InsightsHome />} />
          <Route path="/insights/menu" element={<OldMenuPerformance />} />
          {/* old paths, kept as redirects so a bookmark/old link doesn't just 404 */}
          <Route path="/price-changes" element={<Navigate to="/inventory-reports" replace />} />
          <Route path="/barcode-reports" element={<Navigate to="/inventory-reports" replace />} />
          <Route
            path="/help"
            element={
              <Protected feature="help">
                <HelpRoute />
              </Protected>
            }
          />
          {/* Glossary is a tab of Help; /glossary opens that tab (and is the
              whole page for anyone without Help). Same address as before, so
              every "All definitions" link and bookmark still works. */}
          <Route path="/glossary" element={<HelpRoute tab="glossary" />} />
          {/* The Notifications page is gone from the website (alerts are
              chosen in the app; the API stays for it): an old link or
              bookmark lands on the dashboard. */}
          <Route path="/notifications" element={<Navigate to="/" replace />} />
          {/* Settings -> Integrations: store admins (the API is require_admin). */}
          <Route
            path="/settings/integrations"
            element={user.is_admin ? <IntegrationsPage /> : <Navigate to="/" replace />}
          />
          {/* Signed in already: the sign-up and platform pages go home. */}
          <Route path="/signup/*" element={<Navigate to="/" replace />} />
          <Route path="/platform" element={<Navigate to="/" replace />} />
          <Route path="/login" element={<Navigate to="/" replace />} />
          <Route path="/change-password" element={<ChangePasswordPage />} />
          <Route path="/no-access" element={<NoAccessPage />} />
          <Route path="/not-available" element={<NotAvailablePage />} />
          <Route path="/users" element={user.is_admin ? <UsersPage /> : <Navigate to="/" replace />} />
          {/* Inventory from the POS: admins only (the API is require_admin), and
              only when the POS has a catalog and stock levels. */}
          <Route
            path="/inventory-api"
            element={
              <AdminPage routeKey="setup_inventory_api">
                <InventoryApiPage />
              </AdminPage>
            }
          />
          {/* Fix Issues category & vendor leads (W4): admins only (the API is require_admin). */}
          <Route path="/issue-leads" element={user.is_admin ? <IssueLeadsPage /> : <Navigate to="/" replace />} />
          <Route path="/teams" element={user.is_admin ? <TeamsPage /> : <Navigate to="/" replace />} />
          {/* Website ready: Setup > Data & usage, Item vendors and Store settings (admins; their APIs are admin-only). */}
          <Route
            path="/settings/data"
            element={
              <AdminPage routeKey="setup_data">
                <StoreDataPage />
              </AdminPage>
            }
          />
          <Route
            path="/setup/item-vendors"
            element={
              <AdminPage routeKey="setup_item_vendors">
                <ItemVendorsPage />
              </AdminPage>
            }
          />
          <Route
            path="/settings/store"
            element={
              <AdminPage routeKey="setup_store_settings">
                <StoreSettingsPage />
              </AdminPage>
            }
          />
        </Routes>
        </Suspense>
      </main>
      </div>
      {HelpPanel ? <HelpPanel /> : <HelpDrawer />}
      {hasFeature("ask_bot") && !onTeamChat && (
        <Suspense fallback={null}>
          <ChatWidget />
        </Suspense>
      )}
    </div>
  );
}

export default function App() {
  return (
    <StoreProvider>
      <AuthProvider>
        <GlossaryProvider>
          <HashRouter>
            <AppShell />
          </HashRouter>
        </GlossaryProvider>
      </AuthProvider>
    </StoreProvider>
  );
}
