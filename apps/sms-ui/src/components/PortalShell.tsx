import { NavLink, Outlet } from "react-router-dom";
import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { getRole, isAdmin, canSeeQueue, canSeeManager, canSeeMonitoring, logout } from "../lib/auth";
import { getSocket } from "../lib/socket";
import { leadOfferedSound } from "../lib/sound";
import LeadOfferOverlay from "./LeadOfferOverlay";

const ROLE_LABEL: Record<string, string> = {
  agent: "Agent",
  lead: "Team Leader",
  head: "Head Manager",
  manager: "Manager",
  tenant_admin: "Admin",
  super_admin: "Admin",
  admin: "Admin",
  dev: "Dev",
};

/* The native portal sidebar links (point back to the static portal pages).
   `hideRoles` mirrors the portal's own per-role filter (prefs-extras.js) so the
   /sms sidebar matches each role's portal pages exactly (no "dancing"). */
type PortalLink = { href: string; label: string; agentLabel?: string; icon: string; hideRoles?: string[] };

const PORTAL_LINKS: PortalLink[] = [
  // Sales Dashboard is an INTERNAL route (admin-only) — rendered separately as a
  // NavLink below, not here, since it lives inside this SPA (not a .html page).
  // CEO Dashboard is admin-only (#navCeo shows only for role "admin").
  { href: "/ceo-dashboard.html", label: "CEO Dashboard", icon: "ceo",
    hideRoles: ["agent", "lead", "manager", "head", "tenant_admin", "super_admin"] },
  { href: "/notifications.html", label: "Notifications", icon: "bell" },
];
const WORKSPACE_LINKS: PortalLink[] = [
  // Order mirrors the portal's runtime nav order (prefs-extras.js
  // normalizeWorkspaceOrder) so the sidebar list matches the static pages and
  // doesn't "dance" when moving between the SMS workspace and the portal.
  // Upload Leads hidden from the sidebar for ALL roles — its controls now live on
  // the SMS Manager page. Reversible: restore hideRoles to ["agent", "manager"].
  { href: "/upload-leads.html", label: "Upload Leads", icon: "upload", hideRoles: ["agent", "lead", "manager", "head", "tenant_admin", "super_admin", "admin"] },
  // All Deals (admins) / My Deals (agents) — only one shows per role.
  { href: "/my-deals.html", label: "My Deals", icon: "deals", hideRoles: ["lead", "manager", "head", "tenant_admin", "super_admin", "admin"] },
  { href: "/all-deals.html", label: "All Deals", icon: "deals", hideRoles: ["agent", "lead", "manager", "head"] },
  { href: "/leaderboard.html", label: "Leaderboard", icon: "trophy" },
  // Hirees (agent onboarding review) — admin-class only, mirrors the static
  // portal's injectHireesLink gating (admin/tenant_admin/super_admin/dev).
  { href: "/hirees.html", label: "Hirees", icon: "user-plus", hideRoles: ["agent", "lead", "manager", "head"] },
  // Applicant Inbox (admin↔hiree SMS) — admin/dev ONLY (mirrors prefs-extras
  // gating: shown to admin/tenant_admin/super_admin/dev). Distinct from the
  // agent-facing inbox.html below, which is hidden from admins.
  { href: "/applicant-inbox.html", label: "Inbox", icon: "inbox", hideRoles: ["agent", "lead", "manager", "head"] },
  { href: "/inbox.html", label: "Inbox", icon: "inbox", hideRoles: ["tenant_admin", "super_admin", "admin"] },
  { href: "/my-team.html", label: "My Team", icon: "users", hideRoles: ["agent", "tenant_admin", "super_admin", "admin"] },
  // Agent performance is reached via the switch on the Sales Dashboard (opens the
  // full page), so it's intentionally not a sidebar item.
  { href: "/analytics.html", label: "Analytics", icon: "chart", hideRoles: ["agent", "tenant_admin", "super_admin", "admin"] },
  // Compliance page HIDDEN (not removed): consolidated into Settings → Licenses &
  // Appointments. Uncomment to restore the nav entry.
  // { href: "/compliance.html", label: "Compliance", agentLabel: "My Licenses", icon: "shield" },
];

// Appointments lives in the "Leads" section (matches the static sidebar, where
// error-boundary.js moves it into the #sbSms group). Hidden for admin-class roles.
const APPOINTMENTS_LINK: PortalLink = { href: "/appointments.html", label: "Appointments", icon: "calendar", hideRoles: ["head", "tenant_admin", "super_admin", "admin"] };
// Each SMS link carries its own visibility check (see lib/auth):
//   Queue → agents + dev · Manager → manager-class + admin + dev · Monitoring → dev only.
const SMS_LINKS: { to: string; label: string; icon: string; show: () => boolean }[] = [
  { to: "/queue", label: "Lead Manager", icon: "queue", show: canSeeQueue },
  { to: "/manager", label: "SMS Manager", icon: "users", show: canSeeManager },
  { to: "/monitoring", label: "SMS Monitoring", icon: "activity", show: canSeeMonitoring },
];

function Icon({ name }: { name: string }) {
  const common = {
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
  };
  switch (name) {
    case "broadcast":
      // DID Fleet — broadcast/signal tower; matches the static sidebar icon (error-boundary.js).
      return (
        <svg {...common}>
          <path d="M4.9 16.1a9 9 0 0 1 0-8.2" />
          <path d="M19.1 7.9a9 9 0 0 1 0 8.2" />
          <path d="M7.8 13.4a5 5 0 0 1 0-2.8" />
          <path d="M16.2 10.6a5 5 0 0 1 0 2.8" />
          <circle cx="12" cy="12" r="1.6" />
          <path d="M12 13.6V21" />
        </svg>
      );
    case "brain":
      return (
        <svg {...common}>
          <path d="M12 5a3 3 0 1 0-5.997.125 4 4 0 0 0-2.526 5.77 4 4 0 0 0 .556 6.588A4 4 0 1 0 12 18Z" />
          <path d="M12 5a3 3 0 1 1 5.997.125 4 4 0 0 1 2.526 5.77 4 4 0 0 1-.556 6.588A4 4 0 1 1 12 18Z" />
        </svg>
      );
    case "ceo":
      return (
        <svg {...common}>
          <path d="M3 12a9 9 0 1 1 18 0" />
          <path d="M12 12l4-2" />
          <circle cx="12" cy="12" r="1.6" />
        </svg>
      );
    case "upload":
      return (
        <svg {...common}>
          <path d="M12 3v12" />
          <path d="m7 8 5-5 5 5" />
          <path d="M5 17v2a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-2" />
        </svg>
      );
    case "bell":
      return (
        <svg {...common}>
          <path d="M6 8a6 6 0 1 1 12 0c0 7 3 7 3 9H3c0-2 3-2 3-9Z" />
          <path d="M10 21a2 2 0 0 0 4 0" />
        </svg>
      );
    case "grid":
      return (
        <svg {...common}>
          <rect x="3" y="3" width="7" height="9" rx="2" />
          <rect x="14" y="3" width="7" height="5" rx="2" />
          <rect x="14" y="12" width="7" height="9" rx="2" />
          <rect x="3" y="16" width="7" height="5" rx="2" />
        </svg>
      );
    case "users":
      return (
        <svg {...common}>
          <circle cx="9" cy="7" r="4" />
          <path d="M3 21c0-3.5 3-6 6-6s6 2.5 6 6" />
          <path d="M16 3.5a4 4 0 0 1 0 7.5" />
          <path d="M22 21c0-3-2-5-5-5.5" />
        </svg>
      );
    case "user-plus":
      return (
        <svg {...common}>
          <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" />
          <circle cx="9" cy="7" r="4" />
          <path d="M19 8v6M22 11h-6" />
        </svg>
      );
    case "inbox":
      return (
        <svg {...common}>
          <path d="M3 12h6l2 3h2l2-3h6" />
          <path d="M3 7l2 12a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2l2-12" />
          <path d="M5 7l2-3h10l2 3" />
        </svg>
      );
    case "shield":
      return (
        <svg {...common}>
          <path d="M12 2 3 7v6c0 5 4 8 9 9 5-1 9-4 9-9V7Z" />
          <path d="M9 12l2 2 4-4" />
        </svg>
      );
    case "calendar":
      return (
        <svg {...common}>
          <rect x="3" y="5" width="18" height="16" rx="2" />
          <path d="M16 3v4M8 3v4M3 11h18" />
        </svg>
      );
    case "chart":
      return (
        <svg {...common}>
          <path d="M3 3v18h18" />
          <path d="M7 15l4-6 4 4 5-7" />
        </svg>
      );
    case "queue":
      return (
        <svg {...common}>
          <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
          <path d="M8 9h8M8 13h5" />
        </svg>
      );
    case "activity":
      return (
        <svg {...common}>
          <path d="M22 12h-4l-3 9L9 3l-3 9H2" />
        </svg>
      );
    case "check":
      return (
        <svg {...common}>
          <path d="M9 11l3 3L22 4" />
          <path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" />
        </svg>
      );
    case "deals":
      return (
        <svg {...common}>
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
          <path d="M14 2v6h6" />
          <path d="M9 13h6M9 17h4" />
        </svg>
      );
    case "trophy":
      return (
        <svg {...common}>
          <path d="M6 9H4.5a2.5 2.5 0 0 1 0-5H6" />
          <path d="M18 9h1.5a2.5 2.5 0 0 0 0-5H18" />
          <path d="M4 22h16" />
          <path d="M10 14.66V17c0 .55-.47.98-.97 1.21C7.85 18.75 7 20.24 7 22" />
          <path d="M14 14.66V17c0 .55.47.98.97 1.21C16.15 18.75 17 20.24 17 22" />
          <path d="M18 2H6v7a6 6 0 0 0 12 0V2Z" />
        </svg>
      );
    default:
      return null;
  }
}

export default function PortalShell() {
  const role = getRole();
  const initials = (role || "AW")
    .replace(/_/g, " ")
    .split(" ")
    .map((w) => w[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();

  const [, setConnected] = useState(false);
  const [navOpen, setNavOpen] = useState(false);
  // Sidebar collapse (icons-only). Shares the portal's `ebSidebar` key + the
  // `data-sidebar` html attribute so the choice persists across the portal and
  // the SMS workspace. The portal injects this toggle via prefs-extras.js, which
  // doesn't run on the React shell — so we render it here.
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    try { return localStorage.getItem("ebSidebar") === "icons"; } catch { return false; }
  });
  // Profile photo: instant paint from the portal's cached value, then refresh
  // from /auth/me so the uploaded avatar shows on the SMS pages too (matching
  // the rest of the portal, which renders it via prefs-extras.js).
  const [avatar, setAvatar] = useState<string | null>(() => {
    try { return localStorage.getItem("ebAvatar"); } catch { return null; }
  });
  const [name, setName] = useState<string>(() => {
    try { return localStorage.getItem("ebName") || ""; } catch { return ""; }
  });
  const [menuOpen, setMenuOpen] = useState(false);
  const [ping, setPing] = useState<string | null>(null);
  // Unread notification count for the topbar bell (matches the static portal's
  // bell, which prefs-extras.js renders elsewhere but doesn't run in this shell).
  const [unread, setUnread] = useState(0);
  const menuRef = useRef<HTMLDivElement>(null);
  const admin = isAdmin();
  const roleKey = (role || "agent").toLowerCase();
  const roleLabel = ROLE_LABEL[roleKey] || "Admin";
  const agentView = roleKey === "agent";
  const smsLinks = SMS_LINKS.filter((l) => l.show());
  // Per-role filter that mirrors the portal pages exactly.
  const showLink = (l: PortalLink) => !(l.hideRoles || []).includes(roleKey);
  const portalLinks = PORTAL_LINKS.filter(showLink);
  const workspaceLinks = WORKSPACE_LINKS.filter(showLink);

  // Reflect the collapse choice on <html data-sidebar> (the CSS keys off it) and
  // persist it so the portal pages pick up the same state.
  useEffect(() => {
    const v = collapsed ? "icons" : "labels";
    document.documentElement.setAttribute("data-sidebar", v);
    try { localStorage.setItem("ebSidebar", v); } catch { /* ignore */ }
  }, [collapsed]);

  useEffect(() => {
    const s = getSocket();
    setConnected(s.connected);
    const on = () => setConnected(true);
    const off = () => setConnected(false);
    const onPing = (p: { message?: string; phone_number?: string }) => {
      setPing(`${p?.message || "A manager pinged you about a lead."}${p?.phone_number ? ` (${p.phone_number})` : ""}`);
      leadOfferedSound();
      window.setTimeout(() => setPing(null), 7000);
    };
    s.on("connect", on);
    s.on("disconnect", off);
    s.on("sms:ping", onPing);
    return () => {
      s.off("connect", on);
      s.off("disconnect", off);
      s.off("sms:ping", onPing);
    };
  }, []);

  useEffect(() => {
    let alive = true;
    api<{ avatar_url?: string | null; first_name?: string; last_name?: string; email?: string }>("/auth/me")
      .then((u) => {
        if (!alive) return;
        const url = u?.avatar_url || null;
        setAvatar(url);
        const full = `${u?.first_name || ""} ${u?.last_name || ""}`.trim() || u?.email || "";
        if (full) {
          setName(full);
          try { localStorage.setItem("ebName", full); } catch { /* ignore */ }
        }
        try {
          if (url) localStorage.setItem("ebAvatar", url);
          else localStorage.removeItem("ebAvatar");
        } catch { /* ignore */ }
      })
      .catch(() => { /* keep cached avatar / name / initials */ });
    return () => { alive = false; };
  }, []);

  // Topbar bell unread count — same source the static portal bell uses.
  useEffect(() => {
    let alive = true;
    api<{ unread_count?: number }>("/notifications?page=1&size=1")
      .then((r) => { if (alive) setUnread(r?.unread_count || 0); })
      .catch(() => { /* no badge on error */ });
    return () => { alive = false; };
  }, []);

  // Close the account menu on outside-click / Escape.
  useEffect(() => {
    if (!menuOpen) return;
    const onClick = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenuOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setMenuOpen(false); };
    document.addEventListener("mousedown", onClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [menuOpen]);

  const onLogout = () => {
    try {
      localStorage.removeItem("ebRole");
      localStorage.removeItem("ebName");
      localStorage.removeItem("ebAvatar");
    } catch { /* ignore */ }
    logout();
  };

  return (
    <>
      <aside className={`sidebar${navOpen ? " open" : ""}`}>
        <a className="sb-brand" href="/dashboard.html">
          <span className="sb-brand-dot">
            <Icon name="brain" />
          </span>
          <span>Insurance Alliance Group</span>
        </a>
        <nav className="sb-nav">
          <a className="sb-item sb-featured" href="/ask-the-brain.html">
            <Icon name="brain" />
            <span className="sb-tip">Ask the Brain</span>
          </a>
          {portalLinks.map((l) => (
            <a key={l.href} className="sb-item" href={l.href}>
              <Icon name={l.icon} />
              <span className="sb-tip">{agentView && l.agentLabel ? l.agentLabel : l.label}</span>
            </a>
          ))}

          {/* Leads — the active section (Lead Manager + Appointments), internal hash routes */}
          <div className="sb-group open">
            <button className="sb-group-head" type="button">
              Leads
              <svg
                className="sb-chev"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="m9 6 6 6-6 6" />
              </svg>
            </button>
            <div className="sb-group-body">
              {/* Lead Manager + SMS Manager first; SMS Monitoring is rendered AFTER the
                  admin dashboards below, to match the static sidebar order. */}
              {smsLinks.filter((l) => l.to !== "/monitoring").map((l) => (
                <NavLink
                  key={l.to}
                  to={l.to}
                  className={({ isActive }) =>
                    `sb-item${isActive ? " active" : ""}`
                  }
                  onClick={() => setNavOpen(false)}
                >
                  <Icon name={l.icon} />
                  <span className="sb-tip">{l.label}</span>
                </NavLink>
              ))}
              {/* Sales Dashboard (admin-only) is bundled into the SMS section, under SMS Manager. */}
              {admin && (
                <NavLink
                  to="/sales-dashboard"
                  className={({ isActive }) => `sb-item${isActive ? " active" : ""}`}
                  onClick={() => setNavOpen(false)}
                >
                  <Icon name="chart" />
                  <span className="sb-tip">Sales Dashboard</span>
                </NavLink>
              )}
              {/* DID Fleet (admin-only) — static portal page (did-fleet.html), in the SMS
                  section under Sales Dashboard, mirroring its placement + admin gating so the
                  SPA sidebar stays consistent with the static one (error-boundary.js #sbSms). */}
              {admin && (
                <a className="sb-item" href="/did-fleet.html" aria-label="DID Fleet">
                  <Icon name="broadcast" />
                  <span className="sb-tip">DID Fleet</span>
                </a>
              )}
              {/* SMS Monitoring (dev-only) — placed AFTER the admin dashboards so the SPA
                  order matches the static sidebar (Lead Manager, SMS Manager, Sales
                  Dashboard, DID Fleet, SMS Monitoring). */}
              {canSeeMonitoring() && (
                <NavLink
                  to="/monitoring"
                  className={({ isActive }) => `sb-item${isActive ? " active" : ""}`}
                  onClick={() => setNavOpen(false)}
                >
                  <Icon name="activity" />
                  <span className="sb-tip">SMS Monitoring</span>
                </NavLink>
              )}
              {/* Appointments — in the Leads section (matches the static sidebar). */}
              {showLink(APPOINTMENTS_LINK) && (
                <a className="sb-item" href={APPOINTMENTS_LINK.href}>
                  <Icon name={APPOINTMENTS_LINK.icon} />
                  <span className="sb-tip">{APPOINTMENTS_LINK.label}</span>
                </a>
              )}
            </div>
          </div>

          <div className="sb-group open">
            <button className="sb-group-head" type="button">
              Workspaces
              <svg
                className="sb-chev"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="m9 6 6 6-6 6" />
              </svg>
            </button>
            <div className="sb-group-body">
              {workspaceLinks.map((l) => (
                <a key={l.href} className="sb-item" href={l.href}>
                  <Icon name={l.icon} />
                  <span className="sb-tip">{agentView && l.agentLabel ? l.agentLabel : l.label}</span>
                </a>
              ))}
            </div>
          </div>
        </nav>
        <div className="sb-bottom">
          <a className="sb-item" href="/settings.html" style={{flex: "0 1 auto"}}>
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <circle cx="12" cy="12" r="3" />
              <path d="M19.4 15a1.6 1.6 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.6 1.6 0 0 0-1.8-.3 1.6 1.6 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1A1.6 1.6 0 0 0 9 19.4a1.6 1.6 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.6 1.6 0 0 0 .3-1.8 1.6 1.6 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1A1.6 1.6 0 0 0 4.6 9a1.6 1.6 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.6 1.6 0 0 0 1.8.3H9a1.6 1.6 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1c0 .6.4 1.2 1 1.5a1.6 1.6 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.6 1.6 0 0 0-.3 1.8V9c.3.6.9 1 1.5 1H21a2 2 0 1 1 0 4h-.1c-.6 0-1.2.4-1.5 1Z" />
            </svg>
            <span className="sb-tip">Settings</span>
          </a>
          <button
            className="sb-item"
            type="button"
            onClick={logout}
            style={{ flex: "0 0 38px", width: "38px", padding: 0, justifyContent: "center" }}
          >
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
              <path d="m16 17 5-5-5-5" />
              <path d="M21 12H9" />
            </svg>
            <span className="sb-tip" style={{display:"none"}}>Log out</span>
          </button>
        </div>
        <button
          type="button"
          className="sb-collapse-btn"
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          onClick={(e) => { e.stopPropagation(); setCollapsed((c) => !c); }}
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round">
            <path d="m15 18-6-6 6-6" />
          </svg>
        </button>
      </aside>

      <div
        className={`sb-overlay${navOpen ? " open" : ""}`}
        onClick={() => setNavOpen(false)}
      />

      <header className="topbar">
        <button
          className="menu-toggle"
          type="button"
          aria-label="Open menu"
          onClick={() => setNavOpen((v) => !v)}
        >
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth={1.8}
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M3 6h18M3 12h18M3 18h18" />
          </svg>
        </button>
        <div className="search">
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <circle cx="11" cy="11" r="7" />
            <path d="m21 21-4.3-4.3" />
          </svg>
          <input placeholder="Search SMS leads, agents…" />
          <kbd className="flex-none rounded bg-black/5 px-1.5 py-0.5 text-[0.6875rem] font-semibold text-ink-muted">⌘K</kbd>
        </div>
        <div className="tb-right">
          <a className="icon-btn" href="/notifications.html" aria-label="Notifications">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round">
              <path d="M6 8a6 6 0 1 1 12 0c0 7 3 7 3 9H3c0-2 3-2 3-9Z" />
              <path d="M10 21a2 2 0 0 0 4 0" />
            </svg>
            {unread > 0 && <span className="nb-badge">{unread > 9 ? "9+" : unread}</span>}
          </a>
          <div className="relative" ref={menuRef}>
            <button
              className="avatar"
              type="button"
              aria-label="Account"
              aria-haspopup="menu"
              aria-expanded={menuOpen}
              onClick={() => setMenuOpen((v) => !v)}
              style={
                avatar
                  ? {
                      backgroundImage: `url(${avatar})`,
                      backgroundSize: "cover",
                      backgroundPosition: "center",
                      color: "transparent",
                    }
                  : undefined
              }
            >
              {initials}
            </button>
            {menuOpen && (
              <div
                role="menu"
                className="absolute right-0 z-50 mt-2 w-64 overflow-hidden rounded-2xl border border-hairline bg-white shadow-xl ring-1 ring-black/5"
              >
                <div className="border-b border-hairline-soft px-4 py-3">
                  <div className="text-[11px] font-semibold uppercase tracking-wide text-ink-faint">
                    Signed in as
                  </div>
                  <div className="mt-0.5 text-base font-bold text-ink">{name || roleLabel}</div>
                  <div className="text-sm text-ink-muted">{roleLabel}</div>
                </div>
                <a
                  href="/settings.html"
                  role="menuitem"
                  className="flex items-center gap-2.5 px-4 py-2.5 text-sm font-medium text-ink hover:bg-black/5"
                >
                  <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round">
                    <circle cx="12" cy="12" r="3" />
                    <path d="M19.4 15a1.6 1.6 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.6 1.6 0 0 0-1.8-.3 1.6 1.6 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1A1.6 1.6 0 0 0 9 19.4a1.6 1.6 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.6 1.6 0 0 0 .3-1.8 1.6 1.6 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1A1.6 1.6 0 0 0 4.6 9a1.6 1.6 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.6 1.6 0 0 0 1.8.3H9a1.6 1.6 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1c0 .6.4 1.2 1 1.5a1.6 1.6 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.6 1.6 0 0 0-.3 1.8V9c.3.6.9 1 1.5 1H21a2 2 0 1 1 0 4h-.1c-.6 0-1.2.4-1.5 1Z" />
                  </svg>
                  Settings
                </a>
                <a
                  href="/notifications.html"
                  role="menuitem"
                  className="flex items-center gap-2.5 px-4 py-2.5 text-sm font-medium text-ink hover:bg-black/5"
                >
                  <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M6 8a6 6 0 1 1 12 0c0 7 3 7 3 9H3c0-2 3-2 3-9Z" />
                    <path d="M10 21a2 2 0 0 0 4 0" />
                  </svg>
                  Notifications
                </a>
                <div className="border-t border-hairline-soft" />
                <button
                  type="button"
                  role="menuitem"
                  onClick={onLogout}
                  className="flex w-full items-center gap-2.5 px-4 py-2.5 text-sm font-semibold text-danger hover:bg-danger/10"
                >
                  <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
                    <path d="m16 17 5-5-5-5" />
                    <path d="M21 12H9" />
                  </svg>
                  Log out
                </button>
              </div>
            )}
          </div>
        </div>
      </header>

      <main className="main">
        <Outlet />
      </main>

      {/* Global blocking lead-offer modal — covers every SMS route */}
      <LeadOfferOverlay />

      {/* Manager ping toast */}
      {ping && (
        <div className="fixed right-4 top-4 z-[9600] max-w-sm rounded-xl border border-pending/40 bg-white px-4 py-3 text-sm font-medium text-ink shadow-xl">
          🔔 {ping}
        </div>
      )}
    </>
  );
}
