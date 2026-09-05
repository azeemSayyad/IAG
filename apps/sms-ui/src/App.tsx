import { Routes, Route, Navigate } from "react-router-dom";
import PortalShell from "./components/PortalShell";
import SmsQueue from "./pages/SmsQueue";
import SmsManager from "./pages/SmsManager";
import SmsMonitoring from "./pages/SmsMonitoring";
import SalesDashboard from "./pages/SalesDashboard";
import Expenses from "./pages/Expenses";
import Contacts from "./pages/Contacts";
import {
  isAdmin,
  isOwner,
  canSeeQueue,
  canSeeManager,
  canSeeMonitoring,
  smsDefaultRoute,
} from "./lib/auth";
import { useI18n } from "./lib/useI18n";

export default function App() {
  useI18n();
  const admin = isAdmin();
  const owner = isOwner();
  const home = smsDefaultRoute();
  return (
    <Routes>
      <Route element={<PortalShell />}>
        <Route index element={<Navigate to={home} replace />} />
        {/* SMS Queue: agents + dev only (admin/manager-class are redirected). */}
        <Route
          path="/queue"
          element={canSeeQueue() ? <SmsQueue /> : <Navigate to={home} replace />}
        />
        {/* Sales Dashboard is admin-only; everyone else goes to their home page. */}
        <Route
          path="/sales-dashboard"
          element={admin ? <SalesDashboard /> : <Navigate to={home} replace />}
        />
        {/* Expenses: OWNER only (super_admin/dev) — payroll is not admin-visible. */}
        <Route
          path="/expenses"
          element={owner ? <Expenses /> : <Navigate to={home} replace />}
        />
        {/* Contacts: the company phone book — admin-class (not owner-only). */}
        <Route
          path="/contacts"
          element={admin ? <Contacts /> : <Navigate to={home} replace />}
        />
        {/* SMS Manager: manager-class + admin + dev. */}
        <Route
          path="/manager"
          element={canSeeManager() ? <SmsManager /> : <Navigate to={home} replace />}
        />
        {/* SMS Monitoring: dev only. */}
        <Route
          path="/monitoring"
          element={canSeeMonitoring() ? <SmsMonitoring /> : <Navigate to={home} replace />}
        />
        <Route path="*" element={<Navigate to={home} replace />} />
      </Route>
    </Routes>
  );
}
