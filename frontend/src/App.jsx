import { HashRouter, Routes, Route, Navigate, useLocation } from "react-router-dom";
import { AuthProvider, useAuth } from "./AuthContext.jsx";
import { firstAccessiblePath, insightsHomePath } from "./featureRoutes.js";
import Sidebar from "./components/Sidebar.jsx";
import ChatWidget from "./components/ChatWidget.jsx";
import CartButton from "./components/CartButton.jsx";
import LoginPage from "./pages/LoginPage.jsx";
import Overview from "./pages/Overview.jsx";
import InvoiceSubmissionPage from "./pages/InvoiceSubmissionPage.jsx";
import DailyChecklistsPage from "./pages/DailyChecklistsPage.jsx";
import ChecklistAdminPage from "./pages/ChecklistAdminPage.jsx";
import OpenOrdersPage from "./pages/OpenOrdersPage.jsx";
import ItemsSoldPage from "./pages/ItemsSoldPage.jsx";
import ReorderPage from "./pages/ReorderPage.jsx";
import SupplierProjectionPage from "./pages/SupplierProjectionPage.jsx";
import VendorCalendarPage from "./pages/VendorCalendarPage.jsx";
import PurchaseOrderPage from "./pages/PurchaseOrderPage.jsx";
import PlacedOrdersPage from "./pages/PlacedOrdersPage.jsx";
import VendorContactsPage from "./pages/VendorContactsPage.jsx";
import DeliveryReviewPage from "./pages/DeliveryReviewPage.jsx";
import InventoryReportsPage from "./pages/InventoryReportsPage.jsx";
import FixIssuesPage from "./pages/FixIssuesPage.jsx";
import ReconciliationPage from "./pages/ReconciliationPage.jsx";
import PoSectionsPage from "./pages/PoSectionsPage.jsx";
import HelpPage from "./pages/HelpPage.jsx";
import UsersPage from "./pages/UsersPage.jsx";
import TeamsPage from "./pages/TeamsPage.jsx";
import TeamTasksPage from "./pages/TeamTasksPage.jsx";
import TeamTemplatesPage from "./pages/TeamTemplatesPage.jsx";
import TeamAnnouncementsPage from "./pages/TeamAnnouncementsPage.jsx";
import TeamModerationPage from "./pages/TeamModerationPage.jsx";
import TeamChatPage from "./pages/TeamChatPage.jsx";
import ChangePasswordPage from "./pages/ChangePasswordPage.jsx";
import NoAccessPage from "./pages/NoAccessPage.jsx";
import NotificationSettingsPage from "./pages/NotificationSettingsPage.jsx";
import FlyersPage from "./pages/FlyersPage.jsx";
import InventoryApiPage from "./pages/InventoryApiPage.jsx";
import IssueLeadsPage from "./pages/IssueLeadsPage.jsx";
import DailyComparePage from "./pages/insights/DailyComparePage.jsx";
import HeatmapPage from "./pages/insights/HeatmapPage.jsx";
import ChannelsPage from "./pages/insights/ChannelsPage.jsx";
import CheckTrendsPage from "./pages/insights/CheckTrendsPage.jsx";
import MenuPerformancePage from "./pages/insights/MenuPerformancePage.jsx";
import AddonsPage from "./pages/insights/AddonsPage.jsx";
import PrepForecastPage from "./pages/insights/PrepForecastPage.jsx";
import ReturningCustomersPage from "./pages/insights/ReturningCustomersPage.jsx";
import LossWatchPage from "./pages/insights/LossWatchPage.jsx";
import OddChecksPage from "./pages/insights/OddChecksPage.jsx";
import { menuPerformanceRedirect } from "./components/insights/insightsModel.js";
import { GlossaryProvider } from "./glossary.jsx";

// Wraps a page element: 403-equivalent redirect (to the user's first
// accessible tab, or the no-access page) if they haven't been granted this
// feature - mirrors the backend's own per-feature 403s (app/features.py),
// just resolved client-side so it doesn't even flash the wrong page.
function Protected({ feature, children }) {
  const { hasFeature } = useAuth();
  if (!hasFeature(feature)) {
    return <Navigate to={firstAccessiblePath(hasFeature)} replace />;
  }
  return children;
}

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
  // "Also in Toast" (same numbers as Toast's own reports).
  { path: "/insights/channels", Page: ChannelsPage, salesOnly: true },
  { path: "/insights/check-trends", Page: CheckTrendsPage, salesOnly: true },
  { path: "/insights/busy-hours", Page: HeatmapPage },
  { path: "/insights/best-sellers", Page: MenuPerformancePage, props: { view: "sellers" }, salesOnly: true },
];

function InsightsRoute({ adminOnly, salesOnly, children }) {
  const { user, canSeeSales } = useAuth();
  if (adminOnly && !user?.is_admin) return <Navigate to={insightsHomePath(canSeeSales)} replace />;
  // "Hide sales numbers" users: these pages are nothing but sales (the API 403s).
  if (salesOnly && !canSeeSales) return <Navigate to={insightsHomePath(false)} replace />;
  return <Protected feature="sales_insights">{children}</Protected>;
}

function InsightsHome() {
  const { canSeeSales } = useAuth();
  return <Navigate to={insightsHomePath(canSeeSales)} replace />;
}

// Menu performance became Menu trends + Best & worst sellers: an old link
// (?tab=top etc.) opens the page and tab that list now lives on.
function OldMenuPerformance() {
  const { search } = useLocation();
  return <Navigate to={menuPerformanceRedirect(search)} replace />;
}

function AppShell() {
  const { user, loading, hasFeature } = useAuth();
  // Team chat has its own composer at the bottom - the floating Inventory
  // Bot bubble would sit on top of its Send button on a phone.
  const onTeamChat = useLocation().pathname.startsWith("/team/chat");

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

  if (!user) {
    return <LoginPage />;
  }

  return (
    <div className="app">
      <Sidebar />
      <main className="main">
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
              <Protected feature="purchase_order_cart">
                <PlacedOrdersPage />
              </Protected>
            }
          />
          {/* Send PO's vendor contacts: same grant as the cart; the page tells a demo login it's off. */}
          <Route
            path="/vendor-contacts"
            element={
              <Protected feature="purchase_order_cart">
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
              <Protected feature="inventory_reports">
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
                <InsightsRoute adminOnly={adminOnly} salesOnly={salesOnly}>
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
                <HelpPage />
              </Protected>
            }
          />
          {/* Glossary is a tab of Help; /glossary opens that tab (and is the
              whole page for anyone without Help). Same address as before, so
              every "All definitions" link and bookmark still works. */}
          <Route path="/glossary" element={<HelpPage tab="glossary" />} />
          {/* Everyone's own push alerts (+ the store defaults for admins). */}
          <Route path="/notifications" element={<NotificationSettingsPage />} />
          <Route path="/change-password" element={<ChangePasswordPage />} />
          <Route path="/no-access" element={<NoAccessPage />} />
          <Route path="/users" element={user.is_admin ? <UsersPage /> : <Navigate to="/" replace />} />
          {/* Inventory from Toast: admins only (the API is require_admin). */}
          <Route path="/inventory-api" element={user.is_admin ? <InventoryApiPage /> : <Navigate to="/" replace />} />
          {/* Fix Issues category & vendor leads (W4): admins only (the API is require_admin). */}
          <Route path="/issue-leads" element={user.is_admin ? <IssueLeadsPage /> : <Navigate to="/" replace />} />
          <Route path="/teams" element={user.is_admin ? <TeamsPage /> : <Navigate to="/" replace />} />
        </Routes>
      </main>
      {hasFeature("purchase_order_cart") && <CartButton />}
      {hasFeature("ask_bot") && !onTeamChat && <ChatWidget />}
    </div>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <GlossaryProvider>
        <HashRouter>
          <AppShell />
        </HashRouter>
      </GlossaryProvider>
    </AuthProvider>
  );
}
