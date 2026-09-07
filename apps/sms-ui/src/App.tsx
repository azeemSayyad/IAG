import { Routes, Route, Navigate } from "react-router-dom";
import PortalShell from "./components/PortalShell";
import SmsQueue from "./pages/SmsQueue";
import SmsManager from "./pages/SmsManager";
import SmsMonitoring from "./pages/SmsMonitoring";
import SalesDashboard from "./pages/SalesDashboard";
import Expenses from "./pages/Expenses";
import Contacts from "./pages/Contacts";
import Training from "./pages/Training";
import {
  isAdmin,
  isAdminClass,
  isOwner,
  canSeeQueue,
  canSeeManager,
  canSeeMonitoring,
  canSeeTraining,
  smsDefaultRoute,
} from "./lib/auth";
import { useI18n } from "./lib/useI18n";

export default function App() {
  useI18n();
  const admin = isAdmin();          // strict — gates Contacts only
  const adminClass = isAdminClass(); // includes Head Manager
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
        {/* Sales Dashboard: admin-class, Head Manager included. */}
        <Route
          path="/sales-dashboard"
          element={adminClass ? <SalesDashboard /> : <Navigate to={home} replace />}
        />
        {/* Expenses: OWNER only (super_admin/dev) — payroll is not admin-visible. */}
        <Route
          path="/expenses"
          element={owner ? <Expenses /> : <Navigate to={home} replace />}
        />
        {/* Contacts: strict admin — a Head Manager is deliberately excluded. */}
        <Route
          path="/contacts"
          element={admin ? <Contacts /> : <Navigate to={home} replace />}
        />
        {/* Training: agents (its audience) + admin-class (who edit it) + dev. */}
        <Route
          path="/training"
          element={canSeeTraining() ? <Training /> : <Navigate to={home} replace />}
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
