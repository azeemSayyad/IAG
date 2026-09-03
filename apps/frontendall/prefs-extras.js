/* prefs-extras.js — applies Mode / Layout / Sidebar / Language preferences.
 *
 * Loaded synchronously in <head> AFTER the existing inline prefs script. It
 * exposes four globals and injects a single <style> block with the CSS
 * overrides for dark mode, compact density, and icons-only sidebar.
 *
 *   window.__ebApplyMode      'light' | 'dark' | 'auto'
 *   window.__ebApplyLayout    'compact' | 'spacious'
 *   window.__ebApplySidebar   'icons' | 'labels'
 *   window.__ebApplyLanguage  'English (US)' | 'English (UK)' | 'Spanish' | 'French' | 'Portuguese'
 *
 * Persistence keys:
 *   ebMode, ebLayout, ebSidebar, ebLanguage
 *
 * Hooks: when mode is 'auto', the script subscribes to prefers-color-scheme
 * and re-applies. CSS overrides use `html[data-...]` selectors so they win
 * against the page's base stylesheet without needing !important everywhere.
 */
(function(){
  'use strict';

  // Load the mobile hard gate FIRST (on phones it replaces the whole UI with a
  // compulsory "install the app" screen). One shared file, served at the root.
  if(!document.getElementById('appGateScript')){
    var _gate = document.createElement('script');
    _gate.id = 'appGateScript'; _gate.src = '/app-gate.js?v=2'; _gate.defer = true;
    (document.head || document.documentElement).appendChild(_gate);
  }

  // Load the announcement gate (blocking blurred popup for unacknowledged admin
  // announcements) on every portal page. One shared file, served at the root.
  if(!document.getElementById('annScript')){
    var _ann = document.createElement('script');
    _ann.id = 'annScript'; _ann.src = '/announcements.js?v=2'; _ann.defer = true;
    (document.head || document.documentElement).appendChild(_ann);
  }

  try {
    var D = document.documentElement;
    // Apply the saved font-size on EVERY page. Many pages have no inline applier,
    // so the preference reset to default when navigating to them — set it here
    // (synchronously, before paint) so the choice sticks across all pages/POVs.
    try { D.style.fontSize = ({sm:'14px',md:'16px',lg:'18px',xl:'20px'})[localStorage.getItem('ebFontSize')||'md'] || '16px'; } catch(_){}
    // Mark the nav "pending" synchronously (before first paint) so the Workspaces
    // list stays hidden until its order is normalized; revealed right after
    // normalizeWorkspaceOrder(). Fallback timer guarantees it never sticks hidden.
    D.classList.add('eb-nav-pending');
    setTimeout(function(){ D.classList.remove('eb-nav-pending'); }, 1500);

    // ===== CSS overrides (single injected style block) =====
    var css =
      // Anti-stutter: hide the Workspaces nav list until prefs-extras finalizes
      // its order (see normalizeWorkspaceOrder), so the one-frame DOM reorder
      // isn't visible as a flicker when navigating between pages.
      'html.eb-nav-pending #sbWorkspaces .sb-group-body{visibility:hidden}' +
      // --- Dark mode ---
      'html[data-mode="dark"]{color-scheme:dark;--text-strong:#F0F2F5;--text:#D5DAE2;--text-muted:rgba(255,255,255,0.62);--text-faint:rgba(255,255,255,0.42);--border:rgba(255,255,255,0.10);--border-hover:rgba(255,255,255,0.20);--border-soft:rgba(255,255,255,0.06);--strong:#F0F2F5;--muted:rgba(255,255,255,0.62);--faint:rgba(255,255,255,0.42);--card:rgba(24,28,36,0.78);--bg:#0F1216}' +
      'html[data-mode="dark"] body{background:#0F1216;color:#D5DAE2}' +
      'html[data-mode="dark"] body::before{background:linear-gradient(135deg,#0F1216 0%,#161A22 50%,#0F1216 100%) !important;animation:none !important}' +
      'html[data-mode="dark"] body::after{background:radial-gradient(ellipse 95% 85% at 50% 50%,transparent 55%,rgba(0,0,0,0.35) 100%) !important;animation:none !important}' +
      'html[data-mode="dark"] .card,html[data-mode="dark"] .sidebar,html[data-mode="dark"] .topbar,html[data-mode="dark"] .panel,html[data-mode="dark"] .right-hemi,html[data-mode="dark"] .login-card{background:rgba(24,28,36,0.78) !important;color:#D5DAE2;border-color:rgba(255,255,255,0.08) !important}' +
      // Generic content surfaces used across portal pages (stat tiles, agent cards,
      // tables) that aren't .card/.panel — map them so dark mode is consistent.
      'html[data-mode="dark"] .tile,html[data-mode="dark"] .acard{background:rgba(24,28,36,0.78) !important;border-color:rgba(255,255,255,0.08) !important;color:#D5DAE2}' +
      'html[data-mode="dark"] .acard:hover{background:rgba(255,255,255,0.06) !important}' +
      // All Deals / My Deals bespoke surfaces (summary cards, filter toolbar, date-range
      // pill, dropdowns, search, date popover, plain buttons) that default to LIGHT
      // backgrounds and weren't dark-mapped — so they showed as white chips in dark mode.
      'html[data-mode="dark"] .ds-card,html[data-mode="dark"] .toolbar{background:rgba(24,28,36,0.72) !important;border-color:rgba(255,255,255,0.08) !important;color:#D5DAE2}' +
      'html[data-mode="dark"] .seg-group,html[data-mode="dark"] .tabs{background:rgba(255,255,255,0.05) !important;border-color:rgba(255,255,255,0.12) !important}' +
      'html[data-mode="dark"] .seg-btn:hover,html[data-mode="dark"] .tab:hover{background:rgba(255,255,255,0.08) !important;color:#E8EAEE}' +
      'html[data-mode="dark"] .filter-sel,html[data-mode="dark"] .tb-search,html[data-mode="dark"] .cp-field input,html[data-mode="dark"] .more-btn{background:rgba(255,255,255,0.06) !important;border-color:rgba(255,255,255,0.14) !important;color:#E8EAEE !important}' +
      'html[data-mode="dark"] .custom-pop,html[data-mode="dark"] .more-pop{background:rgba(28,32,42,0.98) !important;border-color:rgba(255,255,255,0.12) !important;color:#D5DAE2}' +
      'html[data-mode="dark"] .btn:not(.primary){background:rgba(255,255,255,0.08) !important;border-color:rgba(255,255,255,0.14) !important;color:#E8EAEE !important}' +
      'html[data-mode="dark"] .stat,html[data-mode="dark"] .acard .stat{background:rgba(255,255,255,0.05) !important}' +
      'html[data-mode="dark"] .panel-head{border-bottom-color:rgba(255,255,255,0.08) !important}' +
      'html[data-mode="dark"] th{background:rgba(255,255,255,0.04) !important;color:var(--text-muted) !important}' +
      'html[data-mode="dark"] td{border-bottom-color:rgba(255,255,255,0.06) !important}' +
      'html[data-mode="dark"] .sb-brand{color:#F0F2F5}' +
      'html[data-mode="dark"] .sb-item{color:rgba(255,255,255,0.60)}' +
      'html[data-mode="dark"] .sb-item:hover{background:rgba(255,255,255,0.06);color:#F0F2F5}' +
      'html[data-mode="dark"] .sb-item.active{background:rgba(var(--accent-rgb),0.16);color:#F0F2F5}' +
      'html[data-mode="dark"] .sb-featured{background:linear-gradient(135deg,rgba(var(--accent-rgb),0.18),rgba(var(--accent-2-rgb),0.06)) !important;border-color:rgba(var(--accent-rgb),0.34) !important}' +
      'html[data-mode="dark"] input,html[data-mode="dark"] select,html[data-mode="dark"] textarea{background:rgba(255,255,255,0.04) !important;color:#D5DAE2 !important;border-color:rgba(255,255,255,0.10) !important}' +
      'html[data-mode="dark"] input::placeholder{color:rgba(255,255,255,0.40)}' +
      'html[data-mode="dark"] .btn,html[data-mode="dark"] .ebc-btn,html[data-mode="dark"] .bulk-btn,html[data-mode="dark"] .btn-preview,html[data-mode="dark"] .top-btn,html[data-mode="dark"] .tab,html[data-mode="dark"] .na-btn,html[data-mode="dark"] .icon-btn,html[data-mode="dark"] .demo-chip{background:rgba(255,255,255,0.06) !important;color:#D5DAE2 !important;border-color:rgba(255,255,255,0.10) !important}' +
      'html[data-mode="dark"] .btn.primary,html[data-mode="dark"] .ebc-btn.primary,html[data-mode="dark"] .top-btn.primary,html[data-mode="dark"] .na-btn.primary,html[data-mode="dark"] .btn-primary{background:var(--accent) !important;color:#fff !important;border-color:var(--accent) !important}' +
      'html[data-mode="dark"] .btn.danger,html[data-mode="dark"] .na-btn.danger{color:#E59AA0 !important}' +
      'html[data-mode="dark"] .field-row-static,html[data-mode="dark"] .av-block,html[data-mode="dark"] .dg-tile,html[data-mode="dark"] .bill-tile,html[data-mode="dark"] .list-row,html[data-mode="dark"] .notif-pref,html[data-mode="dark"] .acc-tile,html[data-mode="dark"] .master,html[data-mode="dark"] .mate,html[data-mode="dark"] .team-block,html[data-mode="dark"] .lead-card,html[data-mode="dark"] .sugg-card{background:rgba(255,255,255,0.03) !important;border-color:rgba(255,255,255,0.08) !important}' +
      'html[data-mode="dark"] .notif{background:rgba(255,255,255,0.03) !important;border-color:rgba(255,255,255,0.08) !important;color:#D5DAE2 !important}' +
      'html[data-mode="dark"] .notif.unread{background:rgba(var(--accent-rgb),0.10) !important;border-color:rgba(var(--accent-rgb),0.25) !important}' +
      'html[data-mode="dark"] .search,html[data-mode="dark"] .lt-search{background:rgba(255,255,255,0.05) !important}' +
      // Icon chips / the search shortcut badge keep light fills in dark mode (their
      // base bg was never overridden) — show them as a WHITE OUTLINE instead of a
      // white fill: transparent background + white border.
      'html[data-mode="dark"] .notif-icon,html[data-mode="dark"] .kpi-icon,html[data-mode="dark"] .activity-icon{background:transparent !important;border:1px solid rgba(255,255,255,0.85) !important}' +
      'html[data-mode="dark"] .search kbd{background:transparent !important;border:1px solid rgba(255,255,255,0.85) !important}' +
      // background-COLOR only (not the shorthand) so a set profile photo
      // (inline background-image) is never wiped in dark mode.
      'html[data-mode="dark"] .avatar{background-color:#2A2F3A !important}' +
      'html[data-mode="dark"] .toast{background:#FFFFFF !important;color:#1A1F2A !important}' +
      'html[data-mode="dark"] .car-card{background:rgba(255,255,255,0.06) !important;color:#D5DAE2 !important}' +
      'html[data-mode="dark"] .composer{background:rgba(24,28,36,0.92) !important;border-color:rgba(255,255,255,0.10) !important}' +
      'html[data-mode="dark"] .eb-coach{background:linear-gradient(90deg,rgba(var(--accent-rgb),0.18),rgba(var(--accent-rgb),0.06)) !important}' +
      'html[data-mode="dark"] .ebc-btn{background:rgba(255,255,255,0.06) !important;color:#D5DAE2 !important}' +
      // --- Dark mode: extra coverage (segmented, settings fields, custom selects, modals, cards) ---
      // Segmented controls (Font size, Mode, Layout, Sidebar)
      'html[data-mode="dark"] .seg{background:rgba(255,255,255,0.04) !important;border-color:rgba(255,255,255,0.10) !important}' +
      'html[data-mode="dark"] .seg button{color:rgba(255,255,255,0.55) !important}' +
      'html[data-mode="dark"] .seg button:hover{color:rgba(255,255,255,0.85) !important}' +
      'html[data-mode="dark"] .seg button.active{background:rgba(255,255,255,0.10) !important;color:#F0F2F5 !important;box-shadow:0 1px 2px rgba(0,0,0,0.35) !important}' +
      // Settings field inputs / selects override the base .field rule
      'html[data-mode="dark"] .field input[type=text],html[data-mode="dark"] .field input[type=email],html[data-mode="dark"] .field input[type=tel],html[data-mode="dark"] .field input[type=password],html[data-mode="dark"] .field select{background:rgba(255,255,255,0.04) !important;color:#F0F2F5 !important;border-color:rgba(255,255,255,0.10) !important}' +
      'html[data-mode="dark"] .field input:focus,html[data-mode="dark"] .field select:focus{background:rgba(255,255,255,0.08) !important;border-color:var(--accent) !important}' +
      // Custom dropdown wrappers / selects added in this session
      'html[data-mode="dark"] .ctl-field,html[data-mode="dark"] .ctl{background:rgba(255,255,255,0.04) !important;border-color:rgba(255,255,255,0.10) !important;color:#D5DAE2 !important}' +
      'html[data-mode="dark"] .ctl-sel,html[data-mode="dark"] .tb-sel,html[data-mode="dark"] .lt-sort{background-color:rgba(255,255,255,0.04) !important;color:#D5DAE2 !important;border-color:rgba(255,255,255,0.10) !important}' +
      'html[data-mode="dark"] .ctl-field .ctl-cap{color:rgba(255,255,255,0.55) !important}' +
      // Settings left nav
      'html[data-mode="dark"] .set-nav-item{color:rgba(255,255,255,0.60) !important}' +
      'html[data-mode="dark"] .set-nav-item:hover{background:rgba(255,255,255,0.06) !important;color:#F0F2F5 !important}' +
      'html[data-mode="dark"] .set-nav-item.active{background:rgba(var(--accent-rgb),0.16) !important;color:#F0F2F5 !important}' +
      // Modals (reschedule, override, etc.)
      'html[data-mode="dark"] .modal{background:#1A1F2A !important;color:#D5DAE2 !important;border-color:rgba(255,255,255,0.10) !important}' +
      'html[data-mode="dark"] .modal-head,html[data-mode="dark"] .modal-foot{border-color:rgba(255,255,255,0.08) !important}' +
      'html[data-mode="dark"] .modal-foot{background:rgba(255,255,255,0.03) !important}' +
      'html[data-mode="dark"] .modal-row input,html[data-mode="dark"] .modal-row select{background:rgba(255,255,255,0.04) !important;color:#F0F2F5 !important;border-color:rgba(255,255,255,0.10) !important}' +
      'html[data-mode="dark"] .modal-close{background:rgba(255,255,255,0.06) !important;color:#F0F2F5 !important;border-color:rgba(255,255,255,0.10) !important}' +
      'html[data-mode="dark"] .modal-btn{background:rgba(255,255,255,0.06) !important;color:#F0F2F5 !important;border-color:rgba(255,255,255,0.10) !important}' +
      'html[data-mode="dark"] .modal-btn.primary{background:var(--accent) !important;color:#fff !important;border-color:var(--accent) !important}' +
      // Copilot / visualization / scaffold cards
      'html[data-mode="dark"] .cp-card{background:rgba(255,255,255,0.04) !important;border-color:rgba(255,255,255,0.08) !important;color:#D5DAE2 !important}' +
      'html[data-mode="dark"] .viz-card{background:rgba(255,255,255,0.04) !important;border-color:rgba(255,255,255,0.08) !important;color:#D5DAE2 !important}' +
      'html[data-mode="dark"] .viz-card-title{color:#F0F2F5 !important}' +
      'html[data-mode="dark"] .gs-row{background:rgba(var(--accent-rgb),0.10) !important;border-color:rgba(var(--accent-rgb),0.24) !important}' +
      // Welcome composer + biz cards on Ask the Brain
      'html[data-mode="dark"] .welcome-composer{background:rgba(24,28,36,0.78) !important;border-color:rgba(255,255,255,0.10) !important}' +
      'html[data-mode="dark"] .welcome-composer input{color:#F0F2F5 !important}' +
      'html[data-mode="dark"] .biz-card{background:rgba(255,255,255,0.04) !important;border-color:rgba(255,255,255,0.10) !important;color:#D5DAE2 !important}' +
      'html[data-mode="dark"] .biz-card:hover{background:rgba(255,255,255,0.07) !important;border-color:rgba(var(--accent-rgb),0.40) !important}' +
      'html[data-mode="dark"] .biz-card-q{color:#F0F2F5 !important}' +
      // Misc: chips, agenda chip bar, view-toggle, today-btn, cal heads
      'html[data-mode="dark"] .chip{background:rgba(255,255,255,0.04) !important;color:rgba(255,255,255,0.65) !important;border-color:rgba(255,255,255,0.10) !important}' +
      'html[data-mode="dark"] .chip.active{background:rgba(var(--accent-rgb),0.18) !important;color:#F0F2F5 !important;border-color:rgba(var(--accent-rgb),0.45) !important}' +
      'html[data-mode="dark"] .chips{background:rgba(255,255,255,0.02) !important;border-color:rgba(255,255,255,0.08) !important}' +
      'html[data-mode="dark"] .view-toggle{background:rgba(255,255,255,0.04) !important;border-color:rgba(255,255,255,0.10) !important}' +
      'html[data-mode="dark"] .vt-btn{color:rgba(255,255,255,0.55) !important}' +
      'html[data-mode="dark"] .vt-btn.active{background:rgba(255,255,255,0.10) !important;color:#F0F2F5 !important}' +
      'html[data-mode="dark"] .today-btn{background:rgba(255,255,255,0.06) !important;color:#F0F2F5 !important;border-color:rgba(255,255,255,0.10) !important}' +
      'html[data-mode="dark"] .cal-day-head,html[data-mode="dark"] .cal-corner,html[data-mode="dark"] .cal-time-cell{background:rgba(255,255,255,0.02) !important;border-color:rgba(255,255,255,0.06) !important;color:#D5DAE2 !important}' +
      'html[data-mode="dark"] .cal-cell{border-color:rgba(255,255,255,0.05) !important}' +
      'html[data-mode="dark"] .cal-cell:hover,html[data-mode="dark"] .cal-cell.weekend{background:rgba(255,255,255,0.02) !important}' +
      // Resize grip
      'html[data-mode="dark"] .col-resize .cr-grip{background:rgba(255,255,255,0.06) !important;border-color:rgba(255,255,255,0.14) !important;color:rgba(255,255,255,0.55) !important}' +
      // Login page: composer + form bg
      'html[data-mode="dark"] .login-card,html[data-mode="dark"] .login-panel{background:rgba(24,28,36,0.85) !important;border-color:rgba(255,255,255,0.10) !important;color:#D5DAE2 !important}' +
      'html[data-mode="dark"] .demo-chip{background:rgba(255,255,255,0.05) !important;border-color:rgba(255,255,255,0.10) !important;color:#D5DAE2 !important}' +
      'html[data-mode="dark"] .demo-chip:hover{background:rgba(255,255,255,0.10) !important}' +
      // Tour spotlight card
      'html[data-mode="dark"] .eb-tour-card,html[data-mode="dark"] .tour-card{background:#1A1F2A !important;color:#F0F2F5 !important;border-color:rgba(255,255,255,0.10) !important}' +

      // --- Dark mode: portal-page audit fixes (compliance, inbox, dashboard) ---
      // Compliance KPI cards: compliance.html uses .metric (background:#fff);
      // dashboard.html embeds inline-styled divs in #complianceDashboardGrid.
      'html[data-mode="dark"] .metric{background:rgba(24,28,36,0.78) !important;border-color:rgba(255,255,255,0.08) !important;color:#D5DAE2}' +
      'html[data-mode="dark"] .metric .mk{color:rgba(255,255,255,0.62) !important}' +
      'html[data-mode="dark"] .metric .mv{color:#F0F2F5 !important}' +
      'html[data-mode="dark"] #complianceDashboardGrid > div{background:rgba(255,255,255,0.05) !important;border-color:rgba(255,255,255,0.08) !important}' +
      // Light wrapper boxes (search box, reply textarea wrap, form panels, nested tiles)
      'html[data-mode="dark"] .lead-search,html[data-mode="dark"] .composer-input-wrap,html[data-mode="dark"] .form,html[data-mode="dark"] .deal-fields,html[data-mode="dark"] .sugg,html[data-mode="dark"] .chart-tabs,html[data-mode="dark"] .uc-stat,html[data-mode="dark"] .kpi-icon,html[data-mode="dark"] .aiperf-row,html[data-mode="dark"] .activity-icon,html[data-mode="dark"] .date-pick{background:rgba(255,255,255,0.05) !important;border-color:rgba(255,255,255,0.10) !important}' +
      // Light pill/chip/button controls (composer tools, mini buttons, pagination, stage pills)
      'html[data-mode="dark"] .tool,html[data-mode="dark"] .ai-mini,html[data-mode="dark"] .conv-ic,html[data-mode="dark"] .deal-mini,html[data-mode="dark"] .page-btn,html[data-mode="dark"] .stage-pill,html[data-mode="dark"] .chip-count{background:rgba(255,255,255,0.06) !important;color:#D5DAE2 !important;border-color:rgba(255,255,255,0.10) !important}' +
      'html[data-mode="dark"] .tool:hover,html[data-mode="dark"] .conv-ic:hover,html[data-mode="dark"] .deal-mini:hover,html[data-mode="dark"] .la-btn:hover,html[data-mode="dark"] .sugg:hover{background:rgba(255,255,255,0.10) !important}' +
      // Inbox: hover-action popover (opaque), conversation rows, inbound bubble
      'html[data-mode="dark"] .lead-actions{background:#1A1F2A !important;border-color:rgba(255,255,255,0.12) !important}' +
      'html[data-mode="dark"] .lead:hover{background:rgba(255,255,255,0.05) !important}' +
      'html[data-mode="dark"] .lead.active{background:rgba(var(--accent-rgb),0.16) !important;border-color:rgba(var(--accent-rgb),0.30) !important}' +
      'html[data-mode="dark"] .msg.in .bubble{background:rgba(255,255,255,0.07) !important;color:#D5DAE2 !important;border-color:rgba(255,255,255,0.10) !important}' +
      // Inbox: lead-tag chips (SCORE white chip + status tints with hardcoded dark text)
      'html[data-mode="dark"] .lt.score{background:rgba(255,255,255,0.08) !important;color:rgba(255,255,255,0.62) !important}' +
      'html[data-mode="dark"] .lt.dead{background:rgba(255,255,255,0.06) !important}' +
      'html[data-mode="dark"] .lt.hot{background:rgba(216,85,46,0.22) !important;color:#F0A88E !important}' +
      'html[data-mode="dark"] .lt.appt{background:rgba(79,130,104,0.22) !important;color:#8FC9AB !important}' +
      'html[data-mode="dark"] .lt.dnc{background:rgba(163,82,92,0.22) !important;color:#E59AA0 !important}' +
      // Score progress track + compliance icons + signal/warning text
      'html[data-mode="dark"] .snap-bar{background:rgba(255,255,255,0.10) !important}' +
      'html[data-mode="dark"] .comp-item.ok .comp-ic{color:#8FC9AB !important}' +
      'html[data-mode="dark"] .comp-item.risk .comp-ic{color:#E0B070 !important}' +
      'html[data-mode="dark"] .comp-item.dnc .comp-ic{color:#E59AA0 !important}' +
      'html[data-mode="dark"] .warning{color:#E0B070 !important}' +
      'html[data-mode="dark"] .signal{color:#8FC9AB !important}' +
      // Dashboard analytics toggle active tab + critical coach banner
      'html[data-mode="dark"] .ctab.active{background:rgba(255,255,255,0.10) !important;color:#F0F2F5 !important}' +
      'html[data-mode="dark"] .eb-coach.crit{background:rgba(163,82,92,0.12) !important}' +
      // Compliance page: info banner (inline styles), active tab, status pills, delete button
      'html[data-mode="dark"] #agentLicHint{background:rgba(255,255,255,0.03) !important;border-bottom-color:rgba(255,255,255,0.08) !important;color:#D5DAE2 !important}' +
      'html[data-mode="dark"] .tab.active{background:rgba(var(--accent-rgb),0.18) !important;color:#F0F2F5 !important;border-color:rgba(var(--accent-rgb),0.45) !important}' +
      'html[data-mode="dark"] .pill.ok{background:rgba(79,130,104,0.18) !important;color:#9FD3B6 !important;border-color:rgba(79,130,104,0.35) !important}' +
      'html[data-mode="dark"] .pill.bad{background:rgba(163,82,92,0.18) !important;color:#E59AA0 !important;border-color:rgba(163,82,92,0.35) !important}' +
      'html[data-mode="dark"] .pill.mid{background:rgba(156,120,66,0.18) !important;color:#E9C98A !important;border-color:rgba(156,120,66,0.35) !important}' +
      'html[data-mode="dark"] .row-del{background:rgba(163,82,92,0.16) !important;color:#E59AA0 !important;border-color:rgba(163,82,92,0.35) !important}' +
      // Appointments page: day-group cards, day-view appointment rows, mini-cal,
      // header bands, and calendar action buttons (all hardcoded #FFFFFF/var(--n98)).
      'html[data-mode="dark"] .agent-day,html[data-mode="dark"] .ap-card{background:rgba(24,28,36,0.78) !important;border-color:rgba(255,255,255,0.08) !important;color:#D5DAE2}' +
      'html[data-mode="dark"] .cal-day-appt,html[data-mode="dark"] .cal-mini-day{background:rgba(255,255,255,0.05) !important;border-color:rgba(255,255,255,0.08) !important}' +
      'html[data-mode="dark"] .cal-day-appt:hover,html[data-mode="dark"] .cal-mini-day:hover{background:rgba(255,255,255,0.09) !important}' +
      'html[data-mode="dark"] .cal-mini-strip,html[data-mode="dark"] .cal-day-head-row{background:rgba(255,255,255,0.03) !important;border-color:rgba(255,255,255,0.08) !important}' +
      'html[data-mode="dark"] .cal-min-btn,html[data-mode="dark"] .cal-exp-btn,html[data-mode="dark"] .ar-call,html[data-mode="dark"] .ar-btn{background:rgba(255,255,255,0.06) !important;color:#D5DAE2 !important;border-color:rgba(255,255,255,0.10) !important}' +

      // --- Dark mode: remaining white-filled surfaces → dark fill + light frame ---
      // These keep light fills in dark mode (hardcoded var(--n98)/#fff/#F8FAFC or a
      // semi-white glass header), so they read as solid white panes. Recolor the
      // surface to the dark theme and let the (light) border be the visible frame.
      // Table pagination footer bar (deals, dashboard, agent-performance, upload-leads).
      'html[data-mode="dark"] .pagination{background:rgba(255,255,255,0.03) !important;border-top-color:rgba(255,255,255,0.10) !important}' +
      // Upload-leads Campaigns card + campaign rows (white cards w/ near-black text).
      'html[data-mode="dark"] #campaignMgr,html[data-mode="dark"] .camp-card{background:rgba(255,255,255,0.04) !important;border-color:rgba(255,255,255,0.10) !important;color:#D5DAE2 !important}' +
      'html[data-mode="dark"] #campaignMgr [style*="0F172A"],html[data-mode="dark"] .camp-card [style*="0F172A"]{color:#F0F2F5 !important}' +
      // Ask-the-Brain hemisphere header (semi-white glass bar w/ History / New).
      'html[data-mode="dark"] .hemi-head{background:rgba(255,255,255,0.04) !important;border-bottom-color:rgba(255,255,255,0.10) !important}' +
      // Decorative orange radial glow behind summary KPIs (agent-performance,
      // deals, appointments) — reads as an out-of-place orange smear in dark
      // mode and most pages don't have it. Hide it so they match.
      'html[data-mode="dark"] .summary-blob{display:none !important}' +
      // Coach "all caught up" Reset queue button (white pill).
      'html[data-mode="dark"] .ebc-allclear button{background:rgba(255,255,255,0.06) !important;color:#D5DAE2 !important;border-color:rgba(255,255,255,0.10) !important}' +
      // Appointments split Upcoming view: Scheduled pane (white) + Call now pane
      // (cream/orange), their count badges and the call-now header text.
      'html[data-mode="dark"] .agenda-pane{background:rgba(255,255,255,0.03) !important;border-color:rgba(255,255,255,0.08) !important}' +
      'html[data-mode="dark"] .agenda-pane.callnow{background:rgba(var(--accent-rgb),0.10) !important;border-color:rgba(var(--accent-rgb),0.30) !important}' +
      'html[data-mode="dark"] .agenda-pane.callnow .pane-h{color:var(--accent-2) !important}' +
      'html[data-mode="dark"] .pane-n{background:rgba(255,255,255,0.10) !important;color:rgba(255,255,255,0.62) !important}' +

      // --- Dark mode: full-portal white-surface audit (consistency sweep) ---
      // Floating "quote" pill (most pages; dashboard already fixed locally).
      'html[data-mode="dark"] .fab-quote{background:rgba(24,28,36,0.92) !important;border-color:rgba(255,255,255,0.10) !important;color:#D5DAE2 !important}' +
      // Wizard shell (auto-1..4 / dv-1..3 via wizard.css): card, choice cards, footer.
      'html[data-mode="dark"] .wiz-card{background:rgba(24,28,36,0.78) !important;border-color:rgba(255,255,255,0.08) !important;color:#D5DAE2}' +
      'html[data-mode="dark"] .cs-card{background:rgba(255,255,255,0.05) !important;border-color:rgba(255,255,255,0.08) !important;color:#F0F2F5}' +
      'html[data-mode="dark"] .wiz-foot{background:rgba(24,28,36,0.85) !important;border-top-color:rgba(255,255,255,0.10) !important}' +
      // Deal/lead filter dropdowns (all-deals, my-deals; leaderboard fixed locally).
      'html[data-mode="dark"] .filter-sel,html[data-mode="dark"] .filter-date{background:rgba(24,28,36,0.78) !important;border-color:rgba(255,255,255,0.12) !important;color:#D5DAE2 !important}' +
      // Team Performance + CEO dashboard: section/KPI/team cards, leaderboard rows,
      // range toggle, export/recommendation buttons, section pills.
      'html[data-mode="dark"] .sec,html[data-mode="dark"] .kpi-card,html[data-mode="dark"] .team-card{background:rgba(24,28,36,0.78) !important;border-color:rgba(255,255,255,0.08) !important;color:#D5DAE2}' +
      'html[data-mode="dark"] .lb-row{background:rgba(255,255,255,0.03) !important;border-color:rgba(255,255,255,0.08) !important}' +
      'html[data-mode="dark"] .range-toggle,html[data-mode="dark"] .export-btn,html[data-mode="dark"] .rec-btn,html[data-mode="dark"] .sec-pill{background:rgba(255,255,255,0.06) !important;border-color:rgba(255,255,255,0.10) !important;color:#D5DAE2 !important}' +
      // Ask the Brain bespoke cards / tiles / lists.
      'html[data-mode="dark"] .agc-card{background:rgba(24,28,36,0.78) !important;border-color:rgba(255,255,255,0.08) !important;color:#D5DAE2}' +
      'html[data-mode="dark"] .rec-list,html[data-mode="dark"] .ai-block{background:rgba(255,255,255,0.05) !important;border-color:rgba(255,255,255,0.08) !important}' +
      'html[data-mode="dark"] .kpi-tile,html[data-mode="dark"] .det-row{background:rgba(255,255,255,0.03) !important;border-color:rgba(255,255,255,0.08) !important}' +
      // Notifications detail drawer footer + mobile back button.
      'html[data-mode="dark"] .detail-foot{background:rgba(255,255,255,0.03) !important;border-top-color:rgba(255,255,255,0.08) !important}' +
      'html[data-mode="dark"] .detail-back{background:rgba(255,255,255,0.06) !important;border-color:rgba(255,255,255,0.10) !important;color:#D5DAE2 !important}' +
      // Upload-leads stat tiles (drop-shell/map-sel already fixed locally).
      'html[data-mode="dark"] .stat-tile{background:rgba(24,28,36,0.78) !important;border-color:rgba(255,255,255,0.08) !important;color:#D5DAE2}' +
      // My Team locked-state card + its icon chip.
      'html[data-mode="dark"] .locked-card{background:rgba(24,28,36,0.78) !important;border-color:rgba(255,255,255,0.08) !important;color:#D5DAE2}' +
      'html[data-mode="dark"] .lk-icon{background:rgba(255,255,255,0.05) !important;color:rgba(255,255,255,0.62) !important}' +
      // Add-deal address autocomplete dropdown (opaque popover + hover row).
      'html[data-mode="dark"] .addr-ac{background:#1A1F2A !important;border-color:rgba(255,255,255,0.12) !important;color:#D5DAE2}' +
      'html[data-mode="dark"] .addr-ac-item:hover,html[data-mode="dark"] .addr-ac-item.active{background:rgba(255,255,255,0.08) !important}' +
      // Search-pill inputs: the wrapper provides the (dark) pill fill, but the
      // global dark `input{}` rule above also forces a fill onto the input — a
      // lighter inner rectangle. Reset to transparent so the pill reads as one
      // uniform field with just light text. (Higher specificity beats `input`.)
      'html[data-mode="dark"] .search input,html[data-mode="dark"] .tb-search input,html[data-mode="dark"] .lead-search input,html[data-mode="dark"] .lt-search input,html[data-mode="dark"] .qa-search input{background:transparent !important}' +
      // The two search-pill wrappers that still carried light fills in dark mode.
      'html[data-mode="dark"] .tb-search,html[data-mode="dark"] .qa-search{background:rgba(255,255,255,0.05) !important;border-color:rgba(255,255,255,0.10) !important}' +
      // Coach carousel prev/next arrows (the white "1 / 2" nav squares) were
      // solid white fills in dark mode — show them as a white outline instead.
      'html[data-mode="dark"] .ebc-arrow{background:transparent !important;border-color:rgba(255,255,255,0.85) !important;color:#F0F2F5 !important}' +
      'html[data-mode="dark"] .ebc-arrow:hover{background:rgba(255,255,255,0.10) !important}' +

      // --- Compact layout ---
      'html[data-layout="compact"] .main{padding-top:calc(var(--topbar-h) + 12px) !important;padding-left:14px !important;padding-right:14px !important}' +
      'html[data-layout="compact"] .page-hdr{margin-bottom:10px !important}' +
      'html[data-layout="compact"] .page-hdr h1{font-size:1.375rem !important}' +
      'html[data-layout="compact"] .card{border-radius:12px}' +
      'html[data-layout="compact"] .card-body,html[data-layout="compact"] .panel{padding:12px 14px !important}' +
      'html[data-layout="compact"] .kpi{padding:10px 12px !important}' +
      'html[data-layout="compact"] .k-v{font-size:1.125rem !important}' +
      'html[data-layout="compact"] .field-grid{gap:12px 16px !important}' +
      'html[data-layout="compact"] .field{gap:4px}' +
      'html[data-layout="compact"] .panel-section.active{gap:14px}' +

      // --- Sidebar: icons only ---
      // Override the CSS variable too — some pages (Inbox, QA Review) use
      // var(--sidebar-w) directly on their fixed inner shells (.inbox-shell,
      // .qa-shell). Without this, those shells stay anchored at 220px and
      // a ~156px gap appears next to the 64px icon-rail sidebar. Guarded
      // by a min-width media query so the mobile slide-out layout (which
      // wants --sidebar-w:0) still wins on small screens.
      '@media (min-width:821px){html[data-sidebar="icons"]{--sidebar-w:64px !important}}' +
      'html[data-sidebar="icons"]:not([data-mobile]) .sidebar{width:64px !important}' +
      'html[data-sidebar="icons"]:not([data-mobile]) .sb-tip,html[data-sidebar="icons"]:not([data-mobile]) .sb-brand>span:nth-of-type(2),html[data-sidebar="icons"]:not([data-mobile]) .sb-group-head,html[data-sidebar="icons"]:not([data-mobile]) .sb-badge,html[data-sidebar="icons"]:not([data-mobile]) .sb-lock{display:none !important}' +
      'html[data-sidebar="icons"]:not([data-mobile]) .sb-item,html[data-sidebar="icons"]:not([data-mobile]) .sb-brand{justify-content:center !important;padding:0 !important;gap:0 !important}' +
      'html[data-sidebar="icons"]:not([data-mobile]) .sb-group-body{max-height:600px !important}' +
      'html[data-sidebar="icons"]:not([data-mobile]) .main{margin-left:64px !important}' +
      'html[data-sidebar="icons"]:not([data-mobile]) .topbar{left:64px !important}' +
      // Restore the brand logo to a proper size in icons mode. The base
      // injectLogoutCss() shrinks .sb-brand-dot to 17px so the wordmark
      // "Insurance Alliance Group" aligns with the nav icons below; with the wordmark
      // hidden in icons mode there's nothing to align to and the tiny
      // 17px tile looked lost. Bump it back up to a real logo.
      'html[data-sidebar="icons"]:not([data-mobile]) .sb-brand-dot{width:28px !important;height:28px !important;border-radius:8px !important}' +
      'html[data-sidebar="icons"]:not([data-mobile]) .sb-brand-dot svg{width:16px !important;height:16px !important}' +
      // ----- Collapse toggle button (chevron handle on the sidebar edge) -----
      '.sb-collapse-btn{position:absolute;top:18px;right:-12px;width:24px;height:24px;border-radius:50%;background:#FFFFFF;border:1px solid var(--border);color:var(--text-muted);display:flex;align-items:center;justify-content:center;cursor:pointer;padding:0;box-shadow:0 1px 2px rgba(26,31,42,0.06),0 2px 8px rgba(26,31,42,0.08);z-index:50;transition:color 140ms var(--ease),background 140ms var(--ease),border-color 140ms var(--ease),transform 220ms var(--ease)}' +
      '.sb-collapse-btn:hover{color:var(--text);background:var(--n95);border-color:var(--border-hover)}' +
      // Dark mode: the button hardcodes a white bg + muted chevron, which is a
      // white circle with an invisible light chevron on the dark canvas. Flip it.
      'html[data-mode="dark"] .sb-collapse-btn{background:#1A1F2A !important;border-color:rgba(255,255,255,0.14) !important;color:#D5DAE2 !important}' +
      'html[data-mode="dark"] .sb-collapse-btn:hover{background:#252B36 !important;color:#FFFFFF !important;border-color:rgba(255,255,255,0.24) !important}' +
      // Hirees detail modal — keep its bespoke surfaces in sync with dark mode.
      'html[data-mode="dark"] .modal-card{background:#12151B !important;border-color:rgba(255,255,255,0.10) !important}' +
      'html[data-mode="dark"] .modal-card .mh{background:linear-gradient(180deg,rgba(var(--accent-rgb),0.16),transparent) !important;border-bottom-color:rgba(255,255,255,0.08) !important}' +
      'html[data-mode="dark"] .modal-card .mactions{border-top-color:rgba(255,255,255,0.08) !important}' +
      'html[data-mode="dark"] .modal-card .mh-sub{color:rgba(255,255,255,0.62) !important}' +
      'html[data-mode="dark"] .modal-card .dcard,html[data-mode="dark"] .modal-card .doc-card{background:rgba(255,255,255,0.04) !important;border-color:rgba(255,255,255,0.08) !important}' +
      'html[data-mode="dark"] .modal-card .doc-thumb{background:rgba(255,255,255,0.05) !important}' +
      'html[data-mode="dark"] .modal-card .doc-meta{border-top-color:rgba(255,255,255,0.06) !important}' +
      'html[data-mode="dark"] .modal-card .x{background:rgba(255,255,255,0.06) !important;color:#D5DAE2 !important}' +
      'html[data-mode="dark"] .modal-card .x:hover{background:rgba(255,255,255,0.12) !important;color:#F0F2F5 !important}' +
      'html[data-mode="dark"] .modal-card .btn{background:rgba(255,255,255,0.06) !important;color:#D5DAE2 !important;border-color:rgba(255,255,255,0.10) !important}' +
      'html[data-mode="dark"] .modal-card .btn:hover{background:rgba(255,255,255,0.12) !important}' +
      'html[data-mode="dark"] .modal-card .btn-approve{background:var(--success) !important;color:#fff !important;border-color:transparent !important}' +
      'html[data-mode="dark"] .modal-card .btn-reject{background:transparent !important;color:#E59AA0 !important;border-color:rgba(229,154,160,0.40) !important}' +
      'html[data-mode="dark"] .modal-card .btn-delete{background:transparent !important;color:#E59AA0 !important;border-color:rgba(229,154,160,0.40) !important}' +
      'html[data-mode="dark"] .modal-card .cbtn{background:rgba(255,255,255,0.06) !important;color:#D5DAE2 !important;border-color:rgba(255,255,255,0.10) !important}' +
      'html[data-mode="dark"] .modal-card .cbtn.pri{background:var(--warn) !important;color:#fff !important;border-color:transparent !important}' +
      'html[data-mode="dark"] .modal-card .callout-ta{background:rgba(255,255,255,0.04) !important;color:#F0F2F5 !important}' +
      'html[data-mode="dark"] .modal-card .callout .c-b{color:#F0F2F5 !important}' +
      'html[data-mode="dark"] .modal-card .callout.pending{background:rgba(176,122,46,0.20) !important;color:var(--accent-2) !important}' +
      'html[data-mode="dark"] .modal-card .callout.rejected{background:rgba(163,82,92,0.22) !important;color:#E59AA0 !important}' +
      'html[data-mode="dark"] .modal-card .callout.approved{background:rgba(79,130,104,0.22) !important;color:#7FC59E !important}' +
      'html[data-mode="dark"] .modal-card .chip{background:rgba(var(--accent-rgb),0.18) !important;color:var(--accent-2) !important}' +
      'html[data-mode="dark"] .modal-card .chip.subtle{background:rgba(255,255,255,0.06) !important;color:#D5DAE2 !important}' +
      'html[data-mode="dark"] .modal-card .pill.new{background:rgba(63,110,158,0.22) !important;color:#9DC2E8 !important}' +
      'html[data-mode="dark"] .modal-card .pill.pending{background:rgba(176,122,46,0.22) !important;color:var(--accent-2) !important}' +
      'html[data-mode="dark"] .modal-card .pill.approved{background:rgba(79,130,104,0.22) !important;color:#7FC59E !important}' +
      'html[data-mode="dark"] .modal-card .pill.rejected{background:rgba(163,82,92,0.22) !important;color:#E59AA0 !important}' +
      'html[data-mode="dark"] .modal-card .chk-row.done .chk-ic{background:rgba(79,130,104,0.22) !important;color:#7FC59E !important}' +
      'html[data-mode="dark"] .modal-card .chk-row.todo .chk-ic{background:rgba(176,122,46,0.22) !important;color:var(--accent-2) !important}' +
      'html[data-mode="dark"] .modal-card .chk-row.todo .chk-note{color:var(--accent-2) !important}' +
      '.sb-collapse-btn svg{width:13px;height:13px;stroke-width:2.2;transition:transform 220ms var(--ease)}' +
      'html[data-sidebar="icons"]:not([data-mobile]) .sb-collapse-btn svg{transform:rotate(180deg)}' +
      '@media (max-width:820px){.sb-collapse-btn{display:none !important}}';

    var st = document.createElement('style');
    st.id = 'ebPrefsExtras';
    st.textContent = css;
    document.head.appendChild(st);

    // ===== Mobile responsive overrides =====
    // Goal: when a multi-column section doesn't fit on phone/tablet, stack
    // its children vertically instead of cropping, scrolling sideways, or
    // hiding one pane behind a tab. We use !important to win over the
    // per-page <style> blocks, and we keep desktop layouts untouched.
    var mobileCss =
      // ----- 1100px and below: Ask the Brain hemispheres stack -----
      '@media (max-width:1100px){' +
        '.main.split{grid-template-columns:1fr !important;height:auto !important;min-height:calc(100vh - var(--topbar-h, 64px))}' +
        '.main.split .right-hemi{display:flex !important;min-height:60vh;height:auto}' +
        '.main.split .left-hemi.hide-mobile{display:flex !important}' +
        '.main.split .right-hemi.show-mobile,.main.split .right-hemi{display:flex !important}' +
        '.viz-tabs{display:none !important}' +
        '.main:not(.split) .right-hemi{display:none !important}' +
        '.main{height:auto !important;min-height:calc(100vh - var(--topbar-h, 64px))}' +
        '.hemi,.left-hemi,.right-hemi{height:auto !important}' +
        '.thread-wrap{max-height:none;overflow:visible}' +
        // Composer flows under the thread instead of floating absolutely
        '.composer-wrap{position:static !important;background:transparent !important;padding:12px 14px 18px}' +
        // Right hemi viz wrap allowed to grow with content
        '.viz-wrap{max-height:none;overflow:visible}' +
      '}' +
      // ----- 900px and below: workspace pages drop multi-col grids -----
      '@media (max-width:900px){' +
        // Phone topbar: normalize spacing so every search-bar page looks IDENTICAL
        // (pages had different padding / margins — e.g. 22px vs 14px — making some look
        // congested) and so the search + notifications bell + profile avatar (.tb-right)
        // all stay on-screen. Uniform 14px padding + a fixed menu gap pulls the search a
        // little further left for breathing room; the search width is capped (min() keeps
        // the 520px desktop cap) so .tb-right is never pushed off the right edge.
        '.topbar{padding-left:14px !important;padding-right:14px !important;gap:8px !important}' +
        '.topbar .menu-toggle{margin:0 !important}' +
        '.topbar .search{margin:0 !important;max-width:min(520px,calc(100vw - 172px)) !important;min-width:0 !important}' +
        '.topbar .tb-right{flex:0 0 auto !important;margin-left:auto !important}' +
        // Phone sidebar height: 100vh sits behind the mobile browser toolbar (e.g. iOS
        // Safari's bottom search bar), hiding the bottom item (Settings). Use the DYNAMIC
        // viewport height so it fits the visible area, pad for the home-indicator safe
        // area, and let the nav scroll if the list is long so Settings is always reachable.
        '.sidebar{height:100dvh !important;padding-bottom:calc(12px + env(safe-area-inset-bottom,0px)) !important}' +
        '.sidebar .sb-nav{overflow-y:auto;min-height:0}' +
        '.summary,.kpis{grid-template-columns:repeat(2,minmax(0,1fr)) !important}' +
        '.row-mid,.row-bot,.ai-grid,.field-grid,.drawer-grid,.snap-grid,.deal-fields,.detail-grid,.dr-grid,.kpi-grid{grid-template-columns:1fr !important}' +
        // Inbox + QA: pin the shell to the viewport so the topbar stays
        // put, then turn the SHELL itself into the scrollable column. We
        // switch the shell from grid to flex-column so panels reliably
        // stack one after another, each at its natural content height,
        // and the user scrolls the shell vertically to reach the rest.
        '.inbox-shell,.qa-shell{position:fixed !important;top:var(--topbar-h, 64px) !important;left:0 !important;right:0 !important;bottom:0 !important;overflow-y:auto !important;overflow-x:hidden !important;-webkit-overflow-scrolling:touch !important;display:flex !important;flex-direction:column !important;gap:14px !important;padding:14px !important;height:auto !important;min-height:0 !important}' +
        '.inbox-shell > *,.qa-shell > *{flex:0 0 auto !important;width:100% !important;min-width:0 !important;min-height:0 !important;max-height:none !important;overflow:visible !important}' +
        // Drag handles between panes only make sense at desktop widths
        '.col-resize{display:none !important}' +
        '.inbox-shell .panel,.qa-shell .panel{overflow:visible !important;height:auto !important;max-height:none !important;min-height:0 !important}' +
        '.inbox-shell .lead-list,.inbox-shell .conv-thread,.inbox-shell .copilot-scroll,.qa-shell .review-list,.qa-shell .thread,.qa-shell .review-body,.qa-shell .detail-scroll{overflow:visible !important;max-height:none !important;flex:0 0 auto !important;height:auto !important}' +
        '.inbox-shell .panel.leads{max-height:none !important}' +
        // Make sure the copilot panel (hidden on tablet by the page) shows
        // on phones so the user can still reach the suggestions by scrolling.
        '.inbox-shell .copilot{display:flex !important}' +
        // Two-column dashboards — flatten to a single readable column
        '.dash-grid,.split-2,.two-col{grid-template-columns:1fr !important}' +
        // Cards / lists in dashboards — single column for the bottom rows
        '.row-bot{grid-template-columns:1fr !important}' +
        // Settings field grid
        '.field-row,.field-row-static{flex-direction:column;align-items:stretch !important}' +
        // Composer + topbar
        '.composer-row{flex-wrap:wrap}' +
      '}' +
      // ----- 600px and below: phone — collapse everything to a single column -----
      // Coach bar mobile layout is handled inside coach.js (<=820px) so it
      // can put body / actions / nav in the exact stacked order it needs.
      '@media (max-width:600px){' +
        '.summary,.kpis{grid-template-columns:1fr !important}' +
        '.stat-row{grid-template-columns:1fr auto !important;gap:6px}' +
        '.stat-row .stat-bar{grid-column:1/-1}' +
        '.inbox-shell{padding:8px !important;gap:8px !important}' +
        '.kpi{padding:10px 12px !important}' +
        '.page-hdr h1{font-size:1.25rem !important}' +
        // Hemisphere on phone: give each panel a minimum viewport so they read
        // as proper sections rather than tiny strips
        '.main.split .left-hemi,.main.split .right-hemi{min-height:auto}' +
        // Hide collapse arrows on tiny screens
        '.hemi-collapse,.hr-expand{display:none !important}' +
      '}' +
      // ----- 480px and below: tighten spacing and chrome -----
      '@media (max-width:480px){' +
        '.topbar{padding:0 12px;gap:10px}' +
        '.icon-btn,.avatar{width:32px;height:32px}' +
        '.search{display:none}' +
        '.composer{padding:8px 10px}' +
        '.welcome h2,#welcomeTitle{font-size:1.15rem !important;line-height:1.3}' +
        // 2x2 biz suggestion grid stays 2x2 even on phones, but tighten so
        // each card still fits a question at 320px viewport widths.
        '.biz-grid{gap:8px !important}' +
        '.biz-card{padding:10px 11px !important;border-radius:10px !important}' +
        '.biz-card-q{font-size:.75rem !important;line-height:1.3 !important}' +
        '.biz-card-eyebrow{font-size:.5625rem !important;letter-spacing:.06em !important}' +
        // Toolbar dropdowns: ensure they fill the row width when wrapped
        '.deals-toolbar .tb-sel,.perf-toolbar .tb-sel{flex:1 1 calc(50% - 6px);min-width:0}' +
        '.deals-toolbar .tb-search,.perf-toolbar .tb-search{flex:1 1 100%}' +
        // Custom select chevron stays readable on narrow widths
        '.ctl-sel,.tb-sel,.lt-sort{background-size:10px 10px !important}' +
      '}';
    var mst = document.createElement('style');
    mst.id = 'ebMobileExtras';
    mst.textContent = mobileCss;
    document.head.appendChild(mst);

    // ===== Apply helpers =====
    // Color theme (accent) — brand.js owns the palette AND writes every custom
    // property (accent family, neutral ramp, page gradient) onto <html>. This
    // only forwards the user's choice to it and then paints the couple of
    // surfaces that can't read a variable through a pseudo-element shorthand.
    function applyTheme(v){
      if(!window.EB_BRAND) return;              // brand.js failed to load
      window.EB_BRAND.apply(v);
      var t = window.EB_BRAND.theme();
      // Phone sidebar background: on phones the sidebar slides in as a panel over the
      // page, so the desktop frosted-glass look (translucent white + blur) reads as a
      // wrong, inconsistent grey. Paint the SAME themed page gradient (t.g) on it so it
      // matches the page on every page and in every theme — background-attachment:fixed
      // aligns it with the page's fixed ::before gradient. (The SMS Manager is the React
      // SPA and never loads this script — it's handled in portal-shell.css.) Dark mode
      // gets its own OPAQUE rule below — the default dark .sidebar is translucent
      // (rgba .78), which over the page content reads as a see-through sidebar on phones.
      var ebSbBg = document.getElementById('ebSidebarBgTheme');
      if(!ebSbBg){ ebSbBg = document.createElement('style'); ebSbBg.id = 'ebSidebarBgTheme'; document.head.appendChild(ebSbBg); }
      ebSbBg.textContent =
        '@media (max-width:900px){.sidebar{background-color:var(--bg) !important;background-image:' + t.g + ' !important;' +
        'background-attachment:fixed !important;background-repeat:no-repeat !important;' +
        'backdrop-filter:none !important;-webkit-backdrop-filter:none !important}' +
        // Dark mode: solid dark gradient (matches the dark page ::before) so the phone
        // sidebar is fully OPAQUE, not the translucent frosted panel you can see through.
        'html[data-mode="dark"] .sidebar{background-color:#0F1216 !important;' +
        'background-image:linear-gradient(135deg,#0F1216 0%,#161A22 50%,#0F1216 100%) !important;' +
        'background-attachment:fixed !important;backdrop-filter:none !important;-webkit-backdrop-filter:none !important}}';
    }
    function applyMode(v){
      var m = v || 'light';
      if(m === 'auto'){
        m = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
      }
      D.setAttribute('data-mode', m);
    }
    function applyLayout(v){
      D.setAttribute('data-layout', v || 'spacious');
    }
    function applySidebar(v){
      D.setAttribute('data-sidebar', v || 'labels');
    }
    // ---- Runtime UI translation -----------------------------------------
    // No build-time i18n exists, so translate by walking text nodes and swapping
    // any whose trimmed text EXACTLY matches a known UI string. Only dictionary
    // hits are changed — names, numbers, phone #s and other dynamic/backend data
    // never match, so they're left untouched (backend is never involved). The
    // English original is cached per node so switching languages live always
    // translates from the source. Dynamic content is caught by a MutationObserver.
    var I18N = {
      es: {
        "Ask the Brain":"Consulta al cerebro","Sales Dashboard":"Panel de ventas","Notifications":"Notificaciones",
        "SMS Queue":"Cola de SMS","SMS Manager":"Gestor de SMS","Workspaces":"Espacios de trabajo",
        "Upload Leads":"Cargar prospectos","All Deals":"Todas las ventas","My Deals":"Mis ventas",
        "Leaderboard":"Clasificación","Inbox":"Bandeja de entrada","Appointments":"Citas",
        "Agent performance":"Rendimiento del agente","My Team":"Mi equipo","Analytics":"Analíticas",
        "Settings":"Configuración","Log out":"Cerrar sesión","My Licenses":"Mis licencias","Compliance":"Cumplimiento",
        "Save":"Guardar","Cancel":"Cancelar","Apply":"Aplicar","Today":"Hoy","This month":"Este mes","This week":"Esta semana",
        "Save message":"Guardar mensaje","Reset to default":"Restablecer","Add Deal":"Añadir venta","Add appointment":"Añadir cita",
        "Add license":"Añadir licencia","+ Add carrier":"+ Añadir aseguradora","Remove this carrier":"Eliminar esta aseguradora",
        "Save appointment":"Guardar cita","Save license":"Guardar licencia","View Details":"Ver detalles","Escalate":"Escalar",
        "Mark all read":"Marcar todo como leído","Mark done":"Marcar como hecho","Show me":"Mostrar","Reset queue":"Reiniciar cola",
        "Join queue":"Unirse a la cola","Pause sending":"Pausar envío","Newest first":"Más recientes primero","All":"Todos",
        "Carrier":"Aseguradora","State":"Estado","States":"Estados","Expiration date":"Fecha de vencimiento","Effective date":"Fecha de vigencia",
        "Status":"Estado","unread":"sin leer","critical":"críticas","Profile":"Perfil","Personalization":"Personalización",
        "Security":"Seguridad","Licenses & Appointments":"Licencias y nombramientos","Team & Permissions":"Equipo y permisos",
        "Font size":"Tamaño de fuente","Language":"Idioma","Mode":"Modo","Layout":"Diseño","Sidebar":"Barra lateral"
      },
      fr: {
        "Ask the Brain":"Demander au cerveau","Sales Dashboard":"Tableau des ventes","Notifications":"Notifications",
        "SMS Queue":"File SMS","SMS Manager":"Gestionnaire SMS","Workspaces":"Espaces de travail",
        "Upload Leads":"Importer des prospects","All Deals":"Toutes les ventes","My Deals":"Mes ventes",
        "Leaderboard":"Classement","Inbox":"Boîte de réception","Appointments":"Rendez-vous",
        "Agent performance":"Performance de l'agent","My Team":"Mon équipe","Analytics":"Analytique",
        "Settings":"Paramètres","Log out":"Se déconnecter","My Licenses":"Mes licences","Compliance":"Conformité",
        "Save":"Enregistrer","Cancel":"Annuler","Apply":"Appliquer","Today":"Aujourd'hui","This month":"Ce mois-ci","This week":"Cette semaine",
        "Save message":"Enregistrer le message","Reset to default":"Réinitialiser","Add Deal":"Ajouter une vente","Add appointment":"Ajouter un rendez-vous",
        "Add license":"Ajouter une licence","+ Add carrier":"+ Ajouter un assureur","Remove this carrier":"Supprimer cet assureur",
        "Save appointment":"Enregistrer le rendez-vous","Save license":"Enregistrer la licence","View Details":"Voir les détails","Escalate":"Escalader",
        "Mark all read":"Tout marquer comme lu","Mark done":"Marquer comme terminé","Show me":"Afficher","Reset queue":"Réinitialiser la file",
        "Join queue":"Rejoindre la file","Pause sending":"Suspendre l'envoi","Newest first":"Plus récents d'abord","All":"Tous",
        "Carrier":"Assureur","State":"État","States":"États","Expiration date":"Date d'expiration","Effective date":"Date d'effet",
        "Status":"Statut","unread":"non lues","critical":"critiques","Profile":"Profil","Personalization":"Personnalisation",
        "Security":"Sécurité","Licenses & Appointments":"Licences et nominations","Team & Permissions":"Équipe et autorisations",
        "Font size":"Taille de police","Language":"Langue","Mode":"Mode","Layout":"Disposition","Sidebar":"Barre latérale"
      },
      pt: {
        "Ask the Brain":"Pergunte ao cérebro","Sales Dashboard":"Painel de vendas","Notifications":"Notificações",
        "SMS Queue":"Fila de SMS","SMS Manager":"Gerenciador de SMS","Workspaces":"Áreas de trabalho",
        "Upload Leads":"Carregar leads","All Deals":"Todas as vendas","My Deals":"Minhas vendas",
        "Leaderboard":"Classificação","Inbox":"Caixa de entrada","Appointments":"Compromissos",
        "Agent performance":"Desempenho do agente","My Team":"Minha equipe","Analytics":"Análises",
        "Settings":"Configurações","Log out":"Sair","My Licenses":"Minhas licenças","Compliance":"Conformidade",
        "Save":"Salvar","Cancel":"Cancelar","Apply":"Aplicar","Today":"Hoje","This month":"Este mês","This week":"Esta semana",
        "Save message":"Salvar mensagem","Reset to default":"Redefinir","Add Deal":"Adicionar venda","Add appointment":"Adicionar compromisso",
        "Add license":"Adicionar licença","+ Add carrier":"+ Adicionar operadora","Remove this carrier":"Remover esta operadora",
        "Save appointment":"Salvar compromisso","Save license":"Salvar licença","View Details":"Ver detalhes","Escalate":"Escalar",
        "Mark all read":"Marcar tudo como lido","Mark done":"Marcar como concluído","Show me":"Mostrar","Reset queue":"Reiniciar fila",
        "Join queue":"Entrar na fila","Pause sending":"Pausar envio","Newest first":"Mais recentes primeiro","All":"Todos",
        "Carrier":"Operadora","State":"Estado","States":"Estados","Expiration date":"Data de expiração","Effective date":"Data de vigência",
        "Status":"Status","unread":"não lidas","critical":"críticas","Profile":"Perfil","Personalization":"Personalização",
        "Security":"Segurança","Licenses & Appointments":"Licenças e nomeações","Team & Permissions":"Equipe e permissões",
        "Font size":"Tamanho da fonte","Language":"Idioma","Mode":"Modo","Layout":"Layout","Sidebar":"Barra lateral"
      }
    };
    // Merge in the full dictionary from i18n-dict.js (loaded before this script).
    // Format there is { "English": {es,fr,pt} }; transpose into our {lang:{en:tr}}.
    try {
      var EX = window.__EB_I18N;
      if (EX) { for (var _k in EX) { var _e = EX[_k] || {}; if(_e.es){(I18N.es||(I18N.es={}))[_k]=_e.es;} if(_e.fr){(I18N.fr||(I18N.fr={}))[_k]=_e.fr;} if(_e.pt){(I18N.pt||(I18N.pt={}))[_k]=_e.pt;} } }
    } catch(_){}
    var LANG_CODE = { 'Spanish':'es','French':'fr','Portuguese':'pt','English (US)':null,'English (UK)':null };
    var _i18nOrig = (typeof WeakMap !== 'undefined') ? new WeakMap() : null;
    var _i18nSkip = { SCRIPT:1, STYLE:1, NOSCRIPT:1, TEXTAREA:1, CODE:1, OPTION:0 };
    function _i18nApply(root, dict){
      if(!root || !document.body) return;
      var w = document.createTreeWalker(root.nodeType===3?root.parentNode:root, NodeFilter.SHOW_TEXT, null);
      var nodes=[], n;
      if(root.nodeType===3){ nodes.push(root); }
      else { while((n=w.nextNode())) nodes.push(n); }
      for(var i=0;i<nodes.length;i++){
        n=nodes[i]; var p=n.parentNode; if(!p) continue;
        if(_i18nSkip[p.nodeName]) continue;
        if(p.closest && p.closest('[data-no-i18n]')) continue;
        var orig = _i18nOrig ? _i18nOrig.get(n) : undefined;
        if(orig===undefined) orig = n.nodeValue;
        var key = (orig||'').trim();
        if(!key) continue;
        if(!dict){ if(_i18nOrig && _i18nOrig.has(n) && n.nodeValue!==orig) n.nodeValue = orig; continue; }
        var tr = dict[key];
        if(tr && tr!==key){ if(_i18nOrig && !_i18nOrig.has(n)) _i18nOrig.set(n, orig); n.nodeValue = orig.replace(key, tr); }
      }
    }
    var _i18nObs = null;
    function translatePage(){
      var dict = (function(){ var v=localStorage.getItem('ebLanguage')||'English (US)'; var c=LANG_CODE[v]; return (c && I18N[c]) ? I18N[c] : null; })();
      if(!document.body) return;
      _i18nApply(document.body, dict);
      // Translate content rendered later (queues, tables, modals) — debounced.
      if(!_i18nObs && typeof MutationObserver !== 'undefined'){
        var pend=false;
        _i18nObs = new MutationObserver(function(){ if(pend) return; pend=true; setTimeout(function(){ pend=false; var d=(function(){ var v=localStorage.getItem('ebLanguage')||'English (US)'; var c=LANG_CODE[v]; return (c && I18N[c])?I18N[c]:null; })(); if(d) _i18nApply(document.body, d); }, 120); });
        try { _i18nObs.observe(document.body, { childList:true, subtree:true }); } catch(_){}
      }
    }
    function applyLanguage(v){
      var map = {
        'English (US)':'en-US',
        'English (UK)':'en-GB',
        'Spanish':'es',
        'French':'fr',
        'Portuguese':'pt'
      };
      D.setAttribute('lang', map[v] || 'en-US');
      localStorage.setItem('ebLanguage', v);
      // Disconnect observer to stop catching mutations while translating
      if(_i18nObs && _i18nObs.disconnect) _i18nObs.disconnect();
      _i18nObs = null;
      // Clear cached originals to force full retranslation
      _i18nOrig = (typeof WeakMap !== 'undefined') ? new WeakMap() : null;
      try { translatePage(); } catch(e){ console.error('Translation error:', e); }
      // Force complete re-translation of all content
      setTimeout(function(){
        try {
          _i18nOrig = (typeof WeakMap !== 'undefined') ? new WeakMap() : null;
          translatePage();
        } catch(e){ console.error('Delayed translation error:', e); }
      }, 100);
    }

    // Don't clobber a page's own (richer, live-updating) theme applier
    // (e.g. settings.html defines its own for instant swatch preview).
    window.__ebApplyTheme = applyTheme;
    window.__ebApplyMode     = applyMode;
    window.__ebApplyLayout   = applyLayout;
    window.__ebApplySidebar  = applySidebar;
    window.__ebApplyLanguage = applyLanguage;

    // ===== Initial apply from localStorage =====
    applyTheme(localStorage.getItem('ebTheme') || window.EB_BRAND.DEFAULT);
    applyMode(localStorage.getItem('ebMode') || 'light');
    applyLayout(localStorage.getItem('ebLayout') || 'spacious');
    applySidebar(localStorage.getItem('ebSidebar') || 'labels');
    applyLanguage(localStorage.getItem('ebLanguage') || 'English (US)');
    // applyLanguage above runs in <head> (sets <html lang>), but the body
    // doesn't exist yet — so run the text translation once the DOM is ready
    // (it also starts the MutationObserver for dynamically-rendered content).
    if(document.readyState === 'loading'){ document.addEventListener('DOMContentLoaded', function(){ try { translatePage(); } catch(_){} }); } else { try { translatePage(); } catch(_){} }

    // ===== Phone: shorten the topbar search placeholder so it doesn't cut off =====
    // The narrow phone topbar squeezes the search box, truncating long placeholders
    // mid-word (e.g. "Search agent, customer, phc…"). Swap to a short "Search…" on
    // phones and restore the page's own (longer, informative) placeholder on desktop.
    // The Leads-section pages (SMS Manager / Sales Dashboard / DID Fleet) are the React
    // SPA and don't load this script, so they're left out as requested.
    function ebFixSearchPlaceholder(){
      if(!window.matchMedia) return;
      var phone = window.matchMedia('(max-width:600px)').matches;
      var inputs = document.querySelectorAll('.search input, .tb-search input');
      for(var i=0;i<inputs.length;i++){
        var inp = inputs[i];
        if(inp.getAttribute('data-eb-ph') === null){ inp.setAttribute('data-eb-ph', inp.getAttribute('placeholder') || ''); }
        inp.setAttribute('placeholder', phone ? 'Search…' : (inp.getAttribute('data-eb-ph') || ''));
      }
    }
    if(document.readyState === 'loading'){ document.addEventListener('DOMContentLoaded', ebFixSearchPlaceholder); } else { ebFixSearchPlaceholder(); }
    if(window.matchMedia){ var ebPhoneMq = window.matchMedia('(max-width:600px)'); if(ebPhoneMq.addEventListener){ ebPhoneMq.addEventListener('change', ebFixSearchPlaceholder); } else if(ebPhoneMq.addListener){ ebPhoneMq.addListener(ebFixSearchPlaceholder); } }

    // React to system theme change when in Auto mode
    if(window.matchMedia){
      var mq = window.matchMedia('(prefers-color-scheme: dark)');
      if(mq.addEventListener){
        mq.addEventListener('change', function(){
          if((localStorage.getItem('ebMode') || 'light') === 'auto') applyMode('auto');
        });
      }
    }

    // ===== Sidebar: inject a Log-out icon button right beside Settings =====
    // Settings exists as <a class="sb-item" href="settings.html"> inside .sb-bottom.
    // We add a 38x38 icon-only logout button on the right side of the same row.
    // Eager role attribute on <html> so role-based CSS rules apply on first
    // paint (no flash of My Team / QA Review for agents).
    try {
      var _role = localStorage.getItem('ebRole') || 'agent';
      D.setAttribute('data-role', _role);
    } catch(e){}

    function injectLogoutCss(){
      if(document.getElementById('ebLogoutCss')) return;
      var css =
        // Hide the floating "Ask the Brain" assistant FAB sitewide.
        '.fab-wrap{display:none !important}' +
        // Deals has been merged into the Analytics page — hide its sidebar
        // link for every role (the deals.html page itself is now a redirect
        // stub).
        'a[href="deals.html"]{display:none !important}' +
        // Export (CSV/PDF) is not a feature — hide every export button sitewide.
        '#exportBtn,#exportCsv,#exportPdf,.tb-export,.export-btn{display:none !important}' +
        // Upload Leads is hardcoded as a top-level sidebar item in every
        // page; on DOMContentLoaded we move it into the Workspaces group.
        // Hide it at the original top-level position so the user never
        // sees it flash there before the move. The selector uses `>` so
        // it ONLY matches the link while it's a direct child of .sb-nav —
        // once moved inside .sb-group-body the rule no longer applies
        // and the link becomes visible in its final spot.
        '.sb-nav > #navUpload{display:none !important}' +
        // CEO Dashboard is admin-only (matches dashboard.html's ROLE gate).
        // Gate it here so EVERY page hides it for non-admins identically —
        // pages like Dispositions used to leak the link because the gate
        // previously lived only in dashboard.html's page script.
        // (dev sees everything, so it keeps the CEO Dashboard link too.)
        'html:not([data-role="admin"]):not([data-role="dev"]) #navCeo{display:none !important}' +
        // Role gating (first-paint, no flash):
        //   Agents      → hide My Team, QA Review, Agent Performance, Analytics
        //   Team leads  → hide Dashboard
        //   Head mgrs   → hide Appointments
        // QA Review is now AI-driven — remove the human QA Review tab for ALL roles.
        'a[href="qa-review.html"]{display:none !important}' +
        // Agents: hide My Team, Agent Performance, Analytics, Dispositions.
        'html[data-role="agent"] a[href="my-team.html"],' +
        'html[data-role="agent"] a[href="agent-performance.html"],' +
        'html[data-role="agent"] a[href="analytics.html"],' +
        'html[data-role="agent"] a[href="dispositions.html"],' +
        // Team leader (lead): Agent Performance merges into My Team; no Dashboard.
        'html[data-role="lead"]  a[href="agent-performance.html"],' +
        'html[data-role="lead"]  a[href="dashboard.html"],' +
        // Manager: no Dispositions, no Upload Leads, no Dashboard.
        'html[data-role="manager"] a[href="dispositions.html"],' +
        'html[data-role="manager"] #navUpload,' +
        'html[data-role="manager"] a[href="dashboard.html"],' +
        // Head manager: no Appointments (operates above the appointment level).
        'html[data-role="head"]  a[href="appointments.html"],' +
        // Admin: no Appointments, no Inbox, no My Team; Analytics merges into
        // Dashboard. Admin works at the org level (Team Performance, not the
        // operator-level My Team / Inbox / Appointments surfaces).
        'html[data-role="tenant_admin"] a[href="appointments.html"],' +
        'html[data-role="tenant_admin"] a[href="inbox.html"],' +
        'html[data-role="tenant_admin"] a[href="my-team.html"],' +
        'html[data-role="tenant_admin"] a[href="analytics.html"],' +
        'html[data-role="super_admin"]  a[href="appointments.html"],' +
        'html[data-role="super_admin"]  a[href="inbox.html"],' +
        'html[data-role="super_admin"]  a[href="my-team.html"],' +
        'html[data-role="super_admin"]  a[href="analytics.html"],' +
        'html[data-role="admin"] a[href="appointments.html"],' +
        'html[data-role="admin"] a[href="inbox.html"],' +
        'html[data-role="admin"] a[href="my-team.html"],' +
        'html[data-role="admin"] a[href="analytics.html"]{display:none !important}' +
        // Applicant Inbox (admin↔hiree SMS) is admin/dev ONLY: hide it for the
        // operator roles. admin/tenant_admin/super_admin + dev keep it.
        'html[data-role="agent"] a[href="applicant-inbox.html"],' +
        'html[data-role="lead"] a[href="applicant-inbox.html"],' +
        'html[data-role="manager"] a[href="applicant-inbox.html"],' +
        'html[data-role="head"] a[href="applicant-inbox.html"]{display:none !important}' +
        // Admin Inbox (agent↔admin in-app chat) is AGENT/dev only: hide it for
        // everyone else (the admin side reaches agents from their own Inbox).
        'html[data-role="lead"] a[href="admin-inbox.html"],' +
        'html[data-role="manager"] a[href="admin-inbox.html"],' +
        'html[data-role="head"] a[href="admin-inbox.html"],' +
        'html[data-role="tenant_admin"] a[href="admin-inbox.html"],' +
        'html[data-role="super_admin"] a[href="admin-inbox.html"],' +
        'html[data-role="admin"] a[href="admin-inbox.html"]{display:none !important}' +
        // Admin dashboard: the operator-level content (Inbox / Recent deals /
        // Upcoming appointments / Active leads table) isn't relevant to an admin —
        // they get the org KPI overview only. Hide those cards for admin roles.
        'html[data-role="tenant_admin"] body[data-page="dashboard"] .row-bot,' +
        'html[data-role="tenant_admin"] body[data-page="dashboard"] .leads-card,' +
        'html[data-role="super_admin"] body[data-page="dashboard"] .row-bot,' +
        'html[data-role="super_admin"] body[data-page="dashboard"] .leads-card,' +
        'html[data-role="admin"] body[data-page="dashboard"] .row-bot,' +
        'html[data-role="admin"] body[data-page="dashboard"] .leads-card{display:none !important}' +
        // Brand row: drop the separator and the negative side margin so it
        // sits inside the sidebar inset like .sb-item. Shrink the orange
        // logo block to 17px (the same width as the nav icons) so the
        // "Insurance Alliance Group" wordmark lines up exactly with each nav label below.
        '.sb-brand{border-bottom:none !important;margin-left:0 !important;margin-right:0 !important}' +
        '.sb-brand-dot{width:17px !important;height:17px !important;border-radius:5px !important}' +
        '.sb-brand-dot svg{width:11px !important;height:11px !important}' +
        // The wordmark is the full company name ("Insurance Alliance Group"),
        // which is far too long for one line in a 220px sidebar — it wraps to
        // two and used to spill out of the fixed topbar-height brand row. Let
        // the row grow to fit and tighten the type so both lines sit inside it.
        '.sb-brand{height:auto !important;min-height:var(--topbar-h);padding-top:12px !important;padding-bottom:12px !important}' +
        '.sb-brand>span:nth-of-type(2){font-size:0.9375rem;line-height:1.2;letter-spacing:-0.02em}' +
        '.sb-bottom{flex-direction:row !important;align-items:center;gap:6px}' +
        '.sb-bottom .sb-item[href="settings.html"]{flex:0 1 auto;min-width:0}' +
        '.sb-bottom .sb-logout{flex:0 0 38px;width:38px;padding:0;justify-content:center;color:var(--text-muted);transition:color 150ms,background 150ms}' +
        '.sb-bottom .sb-logout .sb-tip{display:none !important;visibility:hidden !important;width:0 !important;height:0 !important;overflow:hidden !important}' +
        '.sb-bottom .sb-logout svg{width:17px;height:17px;stroke-width:1.7}' +
        '.sb-bottom .sb-logout:hover{color:#A3525C;background:rgba(163,82,92,0.08)}' +
        // Icons-only sidebar: keep both items visible, side by side, centered
        'html[data-sidebar="icons"]:not([data-mobile]) .sb-bottom{flex-direction:column !important;gap:2px}' +
        'html[data-sidebar="icons"]:not([data-mobile]) .sb-bottom .sb-item[href="settings.html"]{flex:0 0 auto}' +
        'html[data-sidebar="icons"]:not([data-mobile]) .sb-bottom .sb-logout{width:100%;flex:0 0 auto}' +
        // Sidebar layout: brand pinned at the top, Settings + Logout sticky
        // at the bottom, nav list scrolls in the middle when items would
        // overflow. Scrollbar hidden so the sidebar doesn't look crunched.
        '.sb-nav{min-height:0 !important;overflow-y:auto !important;scrollbar-width:none !important;-ms-overflow-style:none !important;-webkit-overflow-scrolling:touch}' +
        '.sb-nav::-webkit-scrollbar{width:0 !important;height:0 !important;display:none}' +
        '.sb-bottom{flex-shrink:0 !important}' +
        // Stop the top-level nav items from being squashed when the list
        // overflows the sidebar height. Without flex-shrink:0 the flexbox
        // shrinks each item below its declared height, crunching the top
        // three (Ask the Brain / CEO Dashboard / Notifications).
        '.sb-nav > .sb-item,.sb-nav > .sb-featured{flex-shrink:0 !important}' +
        '.sb-nav > .sb-group{flex-shrink:0 !important}' +
        '.sb-group-head{flex-shrink:0 !important}' +
        '.sb-group-body > .sb-item{flex-shrink:0 !important}' +
        // === Topbar avatar dropdown menu (injected by wireTopbarIcons) ===
        '#ebAvatarMenu{position:fixed;z-index:90;background:#FFFFFF;border:1px solid var(--border, rgba(26,31,42,0.07));border-radius:12px;box-shadow:0 1px 2px rgba(26,31,42,0.06),0 12px 32px rgba(26,31,42,0.18);min-width:220px;padding:6px;opacity:0;transform:translateY(-6px);pointer-events:none;transition:opacity 160ms ease,transform 160ms ease;font-family:inherit}' +
        '#ebAvatarMenu.open{opacity:1;transform:translateY(0);pointer-events:auto}' +
        '.eb-am-head{padding:10px 12px 10px;border-bottom:1px solid rgba(26,31,42,0.06);margin-bottom:4px;display:flex;flex-direction:column;gap:3px}' +
        '.eb-am-cap{font-size:.625rem;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:rgba(26,31,42,0.60)}' +
        '.eb-am-name{font-size:.875rem;font-weight:600;color:#1A1F2A;letter-spacing:-0.005em}' +
        '.eb-am-role{font-size:.7rem;font-weight:500;color:rgba(26,31,42,0.55)}' +
        '.eb-am-item{display:flex;align-items:center;gap:10px;padding:9px 12px;border-radius:8px;background:transparent;border:none;width:100%;text-align:left;font-family:inherit;font-size:.8125rem;font-weight:500;color:#2D3340;cursor:pointer;text-decoration:none;transition:background 140ms ease,color 140ms ease}' +
        '.eb-am-item:hover{background:rgba(26,31,42,0.05);color:#1A1F2A}' +
        '.eb-am-item svg{width:14px;height:14px;stroke-width:1.7;color:rgba(26,31,42,0.60);flex-shrink:0}' +
        '.eb-am-item:hover svg{color:#1A1F2A}' +
        '.eb-am-item.danger{color:#A3525C}' +
        '.eb-am-item.danger svg{color:#A3525C}' +
        '.eb-am-item.danger:hover{background:rgba(163,82,92,0.08);color:#A3525C}' +
        '.eb-am-item.danger:hover svg{color:#A3525C}' +
        '.eb-am-divider{height:1px;background:rgba(26,31,42,0.06);margin:4px 6px}' +
        // Notification badge on the bell icon (we add it via JS so the dot
        // is meaningful, not just decorative)
        '.topbar .icon-btn[aria-label="Notifications"]{cursor:pointer;position:relative}' +
        '.eb-bell-badge{position:absolute;top:3px;right:3px;min-width:15px;height:15px;padding:0 4px;border-radius:8px;background:var(--accent);color:#fff;font-size:9px;font-weight:700;line-height:15px;text-align:center;display:none;box-shadow:0 0 0 2px #FFFFFF}' +
        '.eb-bell-menu{position:fixed;top:0;right:0;width:344px;max-width:92vw;background:#FFFFFF;border:1px solid rgba(26,31,42,0.10);border-radius:14px;box-shadow:0 18px 50px rgba(26,31,42,0.22);z-index:120;opacity:0;transform:translateY(-6px);pointer-events:none;transition:opacity .16s ease,transform .16s ease;overflow:hidden}' +
        '.eb-bell-menu.open{opacity:1;transform:none;pointer-events:auto}' +
        '.eb-bm-head{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:13px 15px;border-bottom:1px solid rgba(26,31,42,0.06);font-weight:700;font-size:13px;color:#1A1F2A}' +
        '.eb-bm-head .eb-bm-count{font-size:11px;font-weight:600;color:#9A9A9A}' +
        '.eb-bm-actions{display:flex;align-items:center;gap:8px}' +
        '.eb-bm-readall{background:transparent;border:none;color:var(--accent);font:600 12px Inter,system-ui,sans-serif;cursor:pointer;padding:2px 2px;border-radius:6px;white-space:nowrap}' +
        '.eb-bm-readall:hover{text-decoration:underline}' +
        '.eb-bm-act{display:inline-flex;align-items:center;justify-content:center;width:26px;height:26px;border-radius:7px;border:1px solid rgba(26,31,42,0.10);background:#FFFFFF;color:#5B6270;cursor:pointer;padding:0}' +
        '.eb-bm-act:hover{background:var(--n95);color:#1A1F2A}' +
        '.eb-bm-act svg{width:14px;height:14px}' +
        'html[data-mode="dark"] .eb-bm-act{background:rgba(255,255,255,0.06);border-color:rgba(255,255,255,0.12);color:#A8AEB8}' +
        'html[data-mode="dark"] .eb-bm-act:hover{background:rgba(255,255,255,0.10);color:#E8EAEE}' +
        '.eb-bm-list{max-height:320px;overflow-y:auto}' +
        '.eb-bm-item{display:block;padding:12px 15px;border-bottom:1px solid rgba(26,31,42,0.05);text-decoration:none;color:inherit}' +
        '.eb-bm-item:last-child{border-bottom:none}' +
        '.eb-bm-item:hover{background:var(--n95)}' +
        '.eb-bm-title{font-size:13px;font-weight:600;color:#1A1F2A;margin-bottom:2px}' +
        '.eb-bm-body{font-size:12px;color:#5B6270;line-height:1.45;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}' +
        '.eb-bm-time{font-size:11px;color:#9A9A9A;margin-top:4px}' +
        '.eb-bm-empty{padding:26px 15px;text-align:center;font-size:12.5px;color:#8A8A8A}' +
        '.eb-bm-foot{padding:10px 15px;text-align:center;border-top:1px solid rgba(26,31,42,0.06)}' +
        '.eb-bm-foot a{font-size:12.5px;font-weight:600;color:var(--accent);text-decoration:none}' +
        'html[data-mode="dark"] .eb-bell-menu{background:#161A22;border-color:rgba(255,255,255,0.10)}' +
        'html[data-mode="dark"] .eb-bm-head{color:#E8EAEE;border-bottom-color:rgba(255,255,255,0.08)}' +
        'html[data-mode="dark"] .eb-bm-item{border-bottom-color:rgba(255,255,255,0.06)}' +
        'html[data-mode="dark"] .eb-bm-item:hover{background:rgba(255,255,255,0.05)}' +
        'html[data-mode="dark"] .eb-bm-title{color:#E8EAEE}' +
        'html[data-mode="dark"] .eb-bm-body{color:#A8AEB8}' +
        'html[data-mode="dark"] .eb-bm-foot{border-top-color:rgba(255,255,255,0.08)}' +
        'html[data-mode="dark"] .eb-bell-badge{box-shadow:0 0 0 2px #1A1F2A}' +
        '.topbar .avatar{cursor:pointer}';
      var st = document.createElement('style');
      st.id = 'ebLogoutCss';
      st.textContent = css;
      document.head.appendChild(st);
    }

    function addLogoutBtn(){
      var settings = document.querySelector('.sb-bottom a.sb-item[href="settings.html"]');
      if(!settings) return;
      var existing = settings.parentNode.querySelector('.sb-logout');
      if(existing){
        // Hardcoded version present in the page — just wire the click
        // handler so ebRole gets cleared when the user logs out.
        if(!existing._ebWired){
          existing._ebWired = true;
          existing.addEventListener('click', function(){
            try { localStorage.removeItem('ebRole'); } catch(e){}
          });
        }
        return;
      }
      var a = document.createElement('a');
      a.className = 'sb-item sb-logout';
      a.href = 'login.html';
      a.setAttribute('aria-label', 'Log out');
      a.title = 'Log out';
      a.innerHTML =
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round">' +
          '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/>' +
          '<path d="m16 17 5-5-5-5"/>' +
          '<path d="M21 12H9"/>' +
        '</svg>' +
        '<span class="sb-tip" style="display:none!important">Log out</span>';
      a._ebWired = true;
      a.addEventListener('click', function(){
        try { localStorage.removeItem('ebRole'); } catch(e){}
      });
      settings.parentNode.insertBefore(a, settings.nextSibling);
    }

    // ===== Role gating: hide My Team for agents =====
    function hideMyTeamForAgents(){
      if((localStorage.getItem('ebRole') || 'agent') !== 'agent') return;
      // Sidebar links
      document.querySelectorAll('a[href="my-team.html"]').forEach(function(a){
        // Hide the immediate link and the my-team workspace card if any
        a.style.display = 'none';
      });
      // If the page IS my-team.html, redirect the agent away.
      var here = (location.pathname.split('/').pop() || '').toLowerCase();
      if(here === 'my-team.html'){
        location.replace('ask-the-brain.html');
      }
    }

    // ===== Role gating: hide QA Review for agents =====
    function hideQAForAgents(){
      if((localStorage.getItem('ebRole') || 'agent') !== 'agent') return;
      document.querySelectorAll('a[href="qa-review.html"]').forEach(function(a){
        a.style.display = 'none';
      });
      var here = (location.pathname.split('/').pop() || '').toLowerCase();
      if(here === 'qa-review.html'){
        location.replace('ask-the-brain.html');
      }
    }

    // ===== Upload Leads nav (admin + head only) =====
    // The link is hardcoded in every page's sidebar; this just hides it
    // for roles that shouldn't see it, marks the active state on the
    // upload-leads page, and guards direct URL access for lower roles.
    function gateUploadLeadsLink(){
      // Upload Leads is HIDDEN from the sidebar for everyone — its controls (Pause,
      // First message, Campaigns, drip) now live on the SMS Manager page. The page
      // itself stays reachable by direct URL for admins (no redirect added below).
      // Reversible: delete this style-injection block to restore the nav link.
      if(!document.getElementById('hideUploadNavCss')){
        var _st=document.createElement('style'); _st.id='hideUploadNavCss';
        _st.textContent='#navUpload,a[href="upload-leads.html"],a[href="/upload-leads.html"]{display:none!important}';
        (document.head||document.documentElement).appendChild(_st);
      }
      var role = localStorage.getItem('ebRole') || 'agent';
      var here = (location.pathname.split('/').pop() || '').toLowerCase();
      // Admin-tier roles allowed to upload leads. Backend emits tenant_admin /
      // super_admin; legacy UI roles admin / head are kept for compatibility.
      var canUpload = (role === 'admin' || role === 'tenant_admin' || role === 'super_admin' || role === 'head' || role === 'dev');

      // Page-level guard: lower roles can't view the upload-leads page.
      if(here === 'upload-leads.html' && !canUpload){
        location.replace('ask-the-brain.html');
        return;
      }
      var link = document.querySelector('#navUpload');
      if(!link) return;
      if(!canUpload){
        link.style.display = 'none';
        return;
      }
      // Mark active on the upload-leads page (sidebar HTML is the same
      // across pages, so the active class is applied here).
      if(here === 'upload-leads.html') link.classList.add('active');
    }

    // ===== Role gating: hide Dashboard for team leaders =====
    // Team leaders don't need a separate Dashboard page (Analytics covers it).
    function hideDashboardForLeads(){
      if((localStorage.getItem('ebRole') || 'agent') !== 'lead') return;
      document.querySelectorAll('a[href="dashboard.html"]').forEach(function(a){
        a.style.display = 'none';
      });
      var here = (location.pathname.split('/').pop() || '').toLowerCase();
      if(here === 'dashboard.html'){
        location.replace('analytics.html');
      }
    }

    // ===== Sitewide: Deals merges into Analytics =====
    // The standalone Deals page has been folded into the Analytics page for
    // ALL roles. The sidebar link is hidden via the eager CSS in
    // injectLogoutCss(); here we also redirect any direct visit to
    // deals.html so the merger is enforced at the route level too.
    function redirectDealsToAnalytics(){
      // Belt-and-suspenders: even if a hardcoded sidebar link slipped past
      // the eager CSS, hide it via JS too.
      document.querySelectorAll('a[href="deals.html"]').forEach(function(a){
        a.style.display = 'none';
      });
      var here = (location.pathname.split('/').pop() || '').toLowerCase();
      if(here === 'deals.html'){
        location.replace('analytics.html');
      }
    }

    // ===== Role gating: hide Agent Performance + Analytics for agents =====
    // Agents don't get the dashboard/analytics surfaces; their workflow lives
    // in Ask the Brain + Inbox + Appointments + Deals(in-Analytics? no — only
    // higher roles see Analytics). Hide both sidebar links and bounce direct
    // visits to ask-the-brain.html.
    function hidePerfAndAnalyticsForAgents(){
      if((localStorage.getItem('ebRole') || 'agent') !== 'agent') return;
      document.querySelectorAll('a[href="agent-performance.html"], a[href="analytics.html"]').forEach(function(a){
        a.style.display = 'none';
      });
      var here = (location.pathname.split('/').pop() || '').toLowerCase();
      if(here === 'agent-performance.html' || here === 'analytics.html'){
        location.replace('ask-the-brain.html');
      }
    }

    // ===== Role gating: hide Appointments for head managers =====
    // Head managers operate above the appointment level.
    function hideAppointmentsForHeads(){
      if((localStorage.getItem('ebRole') || 'agent') !== 'head') return;
      document.querySelectorAll('a[href="appointments.html"]').forEach(function(a){
        a.style.display = 'none';
      });
      var here = (location.pathname.split('/').pop() || '').toLowerCase();
      if(here === 'appointments.html'){
        location.replace('ask-the-brain.html');
      }
    }

    // ===== Role gating: Dispositions report is ADMIN-ONLY =====
    // Only admin roles (admin / tenant_admin / super_admin) may view the
    // Dispositions report. For everyone else we hide the sidebar link (belt
    // and suspenders alongside the eager CSS) and bounce direct visits.
    function gateDispositionsForNonAdmins(){
      var role = localStorage.getItem('ebRole') || 'agent';
      // Dispositions are visible to admin, head manager AND team leader (they
      // need their team's dispositions). Hidden for agent + manager.
      var canSee = (role === 'admin' || role === 'tenant_admin' || role === 'super_admin' || role === 'head' || role === 'lead' || role === 'dev');
      if(canSee) return;
      document.querySelectorAll('a[href="dispositions.html"]').forEach(function(a){
        a.style.display = 'none';
      });
      var here = (location.pathname.split('/').pop() || '').toLowerCase();
      if(here === 'dispositions.html'){
        location.replace('ask-the-brain.html');
      }
    }

    // ===== Role gating: admin tab set =====
    // Admin doesn't use Appointments or Inbox, and Analytics is merged into the
    // Dashboard. Hide those links and bounce direct visits to the dashboard.
    function gateAdminTabs(){
      var role = localStorage.getItem('ebRole') || 'agent';
      if(role !== 'admin' && role !== 'tenant_admin' && role !== 'super_admin') return;
      document.querySelectorAll('a[href="appointments.html"], a[href="inbox.html"], a[href="analytics.html"], a[href="my-team.html"]').forEach(function(a){
        a.style.display = 'none';
      });
      var here = (location.pathname.split('/').pop() || '').toLowerCase();
      if(here === 'appointments.html' || here === 'inbox.html' || here === 'analytics.html' || here === 'my-team.html'){
        location.replace('dashboard.html');
      }
    }

    // ===== Role gating: team leader — Agent Performance merges into My Team =====
    function gateLeadPerformance(){
      if((localStorage.getItem('ebRole') || 'agent') !== 'lead') return;
      document.querySelectorAll('a[href="agent-performance.html"]').forEach(function(a){
        a.style.display = 'none';
      });
      var here = (location.pathname.split('/').pop() || '').toLowerCase();
      if(here === 'agent-performance.html'){
        location.replace('my-team.html');
      }
    }

    // ===== QA Review is AI-driven — no human QA page for anyone =====
    function gateQAReviewForAll(){
      document.querySelectorAll('a[href="qa-review.html"]').forEach(function(a){
        a.style.display = 'none';
      });
      var here = (location.pathname.split('/').pop() || '').toLowerCase();
      if(here === 'qa-review.html'){
        var role = localStorage.getItem('ebRole') || 'agent';
        location.replace(role === 'agent' ? 'appointments.html' : 'ask-the-brain.html');
      }
    }

    // ===== Sidebar layout: move Upload Leads into the Workspaces group =====
    // Currently the link sits as a top-level nav item; the product wants it
    // grouped with the other workspace tools AND positioned at the top of
    // that group. We move + reposition at runtime so we don't have to
    // touch every page's HTML.
    function moveUploadIntoWorkspaces(){
      var upload = document.querySelector('#navUpload');
      var wsBody = document.querySelector('#sbWorkspaces .sb-group-body');
      if(!upload || !wsBody) return;
      // Always place Upload Leads as the FIRST child of the workspaces body
      // (works whether the link was originally top-level or already inside).
      if(wsBody.firstChild === upload) return;
      wsBody.insertBefore(upload, wsBody.firstChild);
    }

    // ===== Inject "Team Performance" link into the Workspaces group =====
    // The page is head/admin-only. We inject the sidebar entry at runtime
    // so every page picks it up without touching their HTML.
    function injectTeamPerfLink(){
      var role = localStorage.getItem('ebRole') || 'agent';
      if(role !== 'head' && role !== 'admin') return;
      var wsBody = document.querySelector('#sbWorkspaces .sb-group-body');
      if(!wsBody) return;
      // Skip if already present (hardcoded on the page itself, or already injected)
      if(wsBody.querySelector('a[href="team-performance.html"]')) return;

      var here = (location.pathname.split('/').pop() || '').toLowerCase();
      var a = document.createElement('a');
      a.className = 'sb-item' + (here === 'team-performance.html' ? ' active' : '');
      a.href = 'team-performance.html';
      a.id = 'navTeamPerf';
      a.setAttribute('aria-label', 'Team Performance');
      a.innerHTML =
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round">' +
          '<path d="M3 3v18h18"/>' +
          '<path d="M7 17V10M12 17V6M17 17V13"/>' +
        '</svg>' +
        '<span class="sb-tip">Team Performance</span>';
      // Insert near the other team/agent views — right after "My Team" if it
      // exists, otherwise at the top of the workspaces body.
      var anchor = wsBody.querySelector('a[href="my-team.html"]');
      if(anchor && anchor.nextSibling) wsBody.insertBefore(a, anchor.nextSibling);
      else if(anchor) wsBody.appendChild(a);
      else wsBody.insertBefore(a, wsBody.firstChild);
    }

    // ===== Inject "Dispositions" link for admin / head / team leader =====
    // Dispositions appears only in some pages' hardcoded sidebars, so it was
    // "impossible to find" and vanished on pages like Upload Leads. Inject it
    // on every page for the roles allowed to see it (admin / head / lead).
    function injectDispositionsLink(){
      // Dispositions was merged into Agent Performance (Performance/Dispositions
      // toggle → agent-performance.html#dispView). The standalone sidebar link is
      // intentionally removed for all roles, so this no longer injects anything.
    }

    // ===== Inject "Compliance" / "My Licenses" link for every role =====
    // The compliance page (state licenses, carrier appointments) had NO sidebar
    // entry — it was only reachable via a small "Manage" link on the dashboard.
    // Everyone has compliance:read; agents can add their OWN licenses here, so
    // inject a proper nav tab on every page. Agents see it labelled "My Licenses".
    function injectComplianceLink(){
      // HIDDEN (not removed): the standalone Compliance page is consolidated into
      // Settings → Licenses & Appointments, which manages NPN, state licenses and
      // carrier appointments (+ CSV import) per agent and drives deal approval.
      // The page file and route still exist; we just don't surface a sidebar tab.
      // To restore the nav entry, delete the `return;` line below.
      return;
      var role = localStorage.getItem('ebRole') || 'agent';
      var wsBody = document.querySelector('#sbWorkspaces .sb-group-body');
      if(!wsBody) return;
      if(wsBody.querySelector('a[href="compliance.html"]')) return;
      var here = (location.pathname.split('/').pop() || '').toLowerCase();
      var label = (role === 'agent') ? 'My Licenses' : 'Compliance';
      var a = document.createElement('a');
      a.className = 'sb-item' + (here === 'compliance.html' ? ' active' : '');
      a.href = 'compliance.html';
      a.id = 'navCompliance';
      a.setAttribute('aria-label', label);
      a.innerHTML =
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round">' +
          '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z"/><path d="m9 12 2 2 4-4"/>' +
        '</svg>' +
        '<span class="sb-tip">' + label + '</span>';
      wsBody.appendChild(a);
    }

    // ===== Inject "My Deals" link for agents (positioned above Inbox) =====
    // Agent-only page: the agent's own deals logged today + ACA/Dental/Vision
    // totals. The sidebar is hardcoded per page, so inject the link at runtime
    // right before the Inbox entry.
    function injectMyDealsLink(){
      var role = localStorage.getItem('ebRole') || 'agent';
      if(role !== 'agent' && role !== 'dev') return;
      var wsBody = document.querySelector('#sbWorkspaces .sb-group-body');
      if(!wsBody) return;
      if(wsBody.querySelector('a[href="my-deals.html"]')) return;
      var here = (location.pathname.split('/').pop() || '').toLowerCase();
      var a = document.createElement('a');
      a.className = 'sb-item' + (here === 'my-deals.html' ? ' active' : '');
      a.href = 'my-deals.html';
      a.id = 'navMyDeals';
      a.setAttribute('aria-label', 'My Deals');
      a.innerHTML =
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round">' +
          '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/><path d="M9 13h6M9 17h4"/>' +
        '</svg>' +
        '<span class="sb-tip">My Deals</span>';
      var inbox = wsBody.querySelector('a[href="inbox.html"]');
      if(inbox) wsBody.insertBefore(a, inbox);
      else wsBody.appendChild(a);
    }

    // ===== Page-level guard: My Deals is agent-only =====
    function gateMyDealsPage(){
      var here = (location.pathname.split('/').pop() || '').toLowerCase();
      if(here !== 'my-deals.html') return;
      var role = localStorage.getItem('ebRole') || 'agent';
      if(role !== 'agent' && role !== 'dev') location.replace('ask-the-brain.html');
    }

    // ===== Inject "All Deals" link for admins (org-wide, all agents) =====
    // The admin counterpart to the agent's My Deals: every agent's deals today
    // with org-wide totals. Inserted right after Dashboard in Workspaces.
    function injectAllDealsLink(){
      var role = localStorage.getItem('ebRole') || 'agent';
      if(role !== 'admin' && role !== 'tenant_admin' && role !== 'super_admin' && role !== 'dev') return;
      var wsBody = document.querySelector('#sbWorkspaces .sb-group-body');
      if(!wsBody) return;
      if(wsBody.querySelector('a[href="all-deals.html"]')) return;
      var here = (location.pathname.split('/').pop() || '').toLowerCase();
      var a = document.createElement('a');
      a.className = 'sb-item' + (here === 'all-deals.html' ? ' active' : '');
      a.href = 'all-deals.html';
      a.id = 'navAllDeals';
      a.setAttribute('aria-label', 'All Deals');
      a.innerHTML =
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round">' +
          '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/><path d="M9 13h6M9 17h4"/>' +
        '</svg>' +
        '<span class="sb-tip">All Deals</span>';
      var dash = wsBody.querySelector('a[href="dashboard.html"]');
      if(dash && dash.nextSibling) wsBody.insertBefore(a, dash.nextSibling);
      else if(dash) wsBody.appendChild(a);
      else wsBody.insertBefore(a, wsBody.firstChild);
    }

    // NOTE: "DID Fleet" (admin/dev only) lives in the SMS section — the #sbSms group
    // injected by services/error-boundary.js, right under Sales Dashboard — NOT in
    // Workspaces. Its page guard is gateDidFleetPage() below.

    // ===== Page-level guard: All Deals is admin-only =====
    function gateAllDealsPage(){
      var here = (location.pathname.split('/').pop() || '').toLowerCase();
      if(here !== 'all-deals.html') return;
      var role = localStorage.getItem('ebRole') || 'agent';
      if(role !== 'admin' && role !== 'tenant_admin' && role !== 'super_admin' && role !== 'dev') location.replace('ask-the-brain.html');
    }

    // ===== Page-level guard: DID Fleet is admin/dev only =====
    // Non-admins who open did-fleet.html directly are redirected out. did-fleet.html
    // also carries its own early inline guard (redirects before any data fetch);
    // this is the shared second layer, mirroring gateAllDealsPage().
    function gateDidFleetPage(){
      var here = (location.pathname.split('/').pop() || '').toLowerCase();
      if(here !== 'did-fleet.html') return;
      var role = localStorage.getItem('ebRole') || 'agent';
      if(role !== 'admin' && role !== 'tenant_admin' && role !== 'super_admin' && role !== 'dev') location.replace('ask-the-brain.html');
    }

    // ===== dev-only: ensure CEO Dashboard + Upload Leads appear on EVERY page =====
    // dev sees everything (matches the React SMS shell). Some pages' hardcoded
    // sidebars (e.g. agent-oriented ones like my-team.html) omit the CEO and
    // Upload links, so for dev we inject them when absent — otherwise they'd
    // appear on some pages and vanish on others ("dancing").
    function injectCeoForDev(){
      if((localStorage.getItem('ebRole') || '') !== 'dev') return;
      // DID Fleet intentionally omits CEO Dashboard from its sidebar — don't
      // re-inject it here, even for dev.
      if((location.pathname.split('/').pop() || '').toLowerCase() === 'did-fleet.html') return;
      var nav = document.querySelector('aside.sidebar nav.sb-nav');
      if(!nav || document.getElementById('navCeo') || nav.querySelector('a[href="ceo-dashboard.html"]')) return;
      var here = (location.pathname.split('/').pop() || '').toLowerCase();
      var a = document.createElement('a');
      a.className = 'sb-item' + (here === 'ceo-dashboard.html' ? ' active' : '');
      a.id = 'navCeo';
      a.href = 'ceo-dashboard.html';
      a.setAttribute('aria-label', 'CEO Dashboard');
      a.innerHTML =
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round">' +
          '<path d="M3 12a9 9 0 1 1 18 0"/><path d="M12 12l4-2"/><circle cx="12" cy="12" r="1.6"/>' +
        '</svg>' +
        '<span class="sb-tip">CEO Dashboard</span>';
      // Place just after Sales Dashboard if present, else after the featured item, else at top.
      var anchor = document.getElementById('navSalesDash') || nav.querySelector('.sb-featured');
      if(anchor && anchor.nextSibling) nav.insertBefore(a, anchor.nextSibling);
      else if(anchor) nav.appendChild(a);
      else nav.insertBefore(a, nav.firstChild);
    }
    function injectUploadForDev(){
      if((localStorage.getItem('ebRole') || '') !== 'dev') return;
      if(document.getElementById('navUpload') || document.querySelector('a[href="upload-leads.html"]')) return;
      var wsBody = document.querySelector('#sbWorkspaces .sb-group-body');
      if(!wsBody) return;
      var here = (location.pathname.split('/').pop() || '').toLowerCase();
      var a = document.createElement('a');
      a.className = 'sb-item' + (here === 'upload-leads.html' ? ' active' : '');
      a.id = 'navUpload';
      a.href = 'upload-leads.html';
      a.setAttribute('aria-label', 'Upload Leads');
      a.innerHTML =
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round">' +
          '<path d="M12 3v12"/><path d="m7 8 5-5 5 5"/><path d="M5 17v2a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-2"/>' +
        '</svg>' +
        '<span class="sb-tip">Upload Leads</span>';
      wsBody.insertBefore(a, wsBody.firstChild);  // normalizeWorkspaceOrder() will sort it
    }

    // ===== Inject "Leaderboard" link for EVERYONE (global) =====
    // Team leaderboard ranked by today's deals — visible to admins and agents
    // alike. Placed right after the role's deal page (My Deals / All Deals),
    // otherwise after Dashboard.
    function injectLeaderboardLink(){
      var wsBody = document.querySelector('#sbWorkspaces .sb-group-body');
      if(!wsBody) return;
      if(wsBody.querySelector('a[href="leaderboard.html"]')) return;
      var here = (location.pathname.split('/').pop() || '').toLowerCase();
      var a = document.createElement('a');
      a.className = 'sb-item' + (here === 'leaderboard.html' ? ' active' : '');
      a.href = 'leaderboard.html';
      a.id = 'navLeaderboard';
      a.setAttribute('aria-label', 'Leaderboard');
      a.innerHTML =
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round">' +
          '<path d="M6 9H4.5a2.5 2.5 0 0 1 0-5H6"/><path d="M18 9h1.5a2.5 2.5 0 0 0 0-5H18"/><path d="M4 22h16"/><path d="M10 14.66V17c0 .55-.47.98-.97 1.21C7.85 18.75 7 20.24 7 22"/><path d="M14 14.66V17c0 .55.47.98.97 1.21C16.15 18.75 17 20.24 17 22"/><path d="M18 2H6v7a6 6 0 0 0 12 0V2Z"/>' +
        '</svg>' +
        '<span class="sb-tip">Leaderboard</span>';
      var anchor = wsBody.querySelector('a[href="my-deals.html"], a[href="all-deals.html"]')
                || wsBody.querySelector('a[href="dashboard.html"]');
      if(anchor && anchor.nextSibling) wsBody.insertBefore(a, anchor.nextSibling);
      else if(anchor) wsBody.appendChild(a);
      else wsBody.insertBefore(a, wsBody.firstChild);
    }

    // Shared filter: notifications about what the AI DID (replied / sent / drafted /
    // auto-handled). The portal intentionally hides these — both here in the bell
    // and on notifications.html (which references this same global).
    window.__ebIsAiActivity = window.__ebIsAiActivity || function(n){
      if(!n) return false;
      var d = n.details || {};
      var s = [n.action, n.resource_type, n.resourceType, n.title, n.body, n.desc, n.message, d.message, d.action]
                .join(' ').toLowerCase();
      if(/autopil|auto[\s-]?repl/.test(s)) return true;
      var aiTok = /(^|[^a-z])ai([^a-z]|_|$)/;
      var verb = /(repl|message|\bmsg\b|draft|hand(le|led)|respond|\bsent\b|\bsend\b|\bwrote\b|follow[\s-]?up|outreach|texted|messaged)/;
      return aiTok.test(s) && verb.test(s);
    };

    // ===== Topbar: enforce ONE consistent layout on every page =====
    // The per-page topbars drifted: some had only a search box (no bell/avatar),
    // avatars differed, ordering varied. Normalise every `.topbar` to the SAME
    // shape — search on the LEFT, then a right-hand cluster with the
    // notification bell followed by the account avatar. Missing pieces are
    // injected; existing ones are reordered into place. Runs BEFORE
    // wireTopbarIcons()/wireGlobalSearch() so the injected elements get wired.
    function normalizeTopbar(){
      var bar = document.querySelector('.topbar');
      if(!bar) return;

      // Canonical layout CSS injected once. Scoped to `.topbar …` so it wins
      // over plain `.topbar`/`.search`/`.avatar` page rules and looks identical
      // everywhere (incl. leaderboard/compliance, which never styled the cluster).
      if(!document.getElementById('ebTopbarNormCss')){
        var st = document.createElement('style');
        st.id = 'ebTopbarNormCss';
        st.textContent =
          '.topbar{display:flex;align-items:center;gap:18px}' +
          '.topbar .search{flex:1 1 auto;max-width:520px}' +
          '.topbar .tb-right{display:flex;align-items:center;gap:10px;margin-left:auto}' +
          '.topbar .tb-right .icon-btn{width:36px;height:36px;border-radius:9px;background:rgba(255,255,255,0.6);border:none;color:var(--text-muted,#5b6472);display:inline-flex;align-items:center;justify-content:center;cursor:pointer;position:relative;transition:color .15s ease,background .15s ease}' +
          '.topbar .tb-right .icon-btn:hover{color:var(--text,#1A1F2A);background:rgba(255,255,255,0.9)}' +
          '.topbar .tb-right .icon-btn svg{width:16px;height:16px;stroke-width:1.6}' +
          '.topbar .tb-right .avatar{width:36px;height:36px;border-radius:50%;background:rgba(255,255,255,0.6);display:inline-flex;align-items:center;justify-content:center;color:var(--text-muted,#5b6472);border:1px solid var(--border,rgba(26,31,42,0.08));cursor:pointer;font-weight:700;font-size:13px;overflow:hidden;padding:0}' +
          '.topbar .tb-right .avatar svg{width:18px;height:18px}';
        document.head.appendChild(st);
      }

      // Right-hand cluster — create if absent, and keep it last (rightmost).
      var right = bar.querySelector('.tb-right');
      if(!right){ right = document.createElement('div'); right.className = 'tb-right'; }
      bar.appendChild(right);

      // Notification bell — reuse an existing one (preserving its dot) or build it.
      var bell = bar.querySelector('.icon-btn[aria-label="Notifications"]');
      if(!bell){
        bell = document.createElement('button');
        bell.className = 'icon-btn';
        bell.type = 'button';
        bell.setAttribute('aria-label', 'Notifications');
        bell.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M6 8a6 6 0 1 1 12 0c0 7 3 7 3 9H3c0-2 3-2 3-9Z"/><path d="M10 21a2 2 0 0 0 4 0"/></svg><span class="dot"></span>';
      }

      // Account avatar — reuse an existing one or build a default person glyph
      // (prefs-extras' avatar wiring later fills in the real photo/initials).
      var avatar = bar.querySelector('.avatar');
      if(!avatar){
        avatar = document.createElement('button');
        avatar.className = 'avatar';
        avatar.type = 'button';
        avatar.setAttribute('aria-label', 'Account');
        avatar.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="8" r="4"/><path d="M4 21c0-4.4 3.6-8 8-8s8 3.6 8 8"/></svg>';
      }

      // Final order inside the cluster: bell, then avatar.
      right.appendChild(bell);
      right.appendChild(avatar);
    }

    // ===== Topbar: notifications bell + avatar dropdown =====
    // Wire the bell to navigate to notifications.html, and the avatar to
    // open a small dropdown menu (role label + Settings + Notifications +
    // Log out).
    function wireTopbarIcons(){
      // Bell icon → dropdown preview of the 3 latest notifications + unread
      // badge. Opening it resets the count (marks all read).
      var bells = document.querySelectorAll('.topbar .icon-btn[aria-label="Notifications"]');
      if(bells.length){
        var bellMenu = document.getElementById('ebBellMenu');
        if(!bellMenu){
          bellMenu = document.createElement('div');
          bellMenu.id = 'ebBellMenu';
          bellMenu.className = 'eb-bell-menu';
          bellMenu.innerHTML =
            '<div class="eb-bm-head"><span>Notifications</span>' +
              '<div class="eb-bm-actions">' +
                '<span class="eb-bm-count" id="ebBmCount"></span>' +
                '<button class="eb-bm-readall" id="ebBmReadAll" type="button">Mark all read</button>' +
              '</div>' +
            '</div>' +
            '<div class="eb-bm-list" id="ebBmList"></div>' +
            '<div class="eb-bm-foot"><a href="notifications.html">View all notifications</a></div>';
          document.body.appendChild(bellMenu);
          // "Mark all read" — clear the unread badge/count and flag every row read.
          var ebBmReadAllBtn = bellMenu.querySelector('#ebBmReadAll');
          if(ebBmReadAllBtn) ebBmReadAllBtn.addEventListener('click', function(e){
            e.stopPropagation();
            ebBellUnread = 0;
            document.querySelectorAll('.topbar .icon-btn[aria-label="Notifications"] .eb-bell-badge').forEach(function(x){ x.style.display='none'; });
            var c=document.getElementById('ebBmCount'); if(c) c.textContent='';
            var lst=document.getElementById('ebBmList'); if(lst) lst.querySelectorAll('.eb-bm-item').forEach(function(it){ it.setAttribute('data-unread','0'); });
            if(window.__ebAPI && window.__ebAPI.post){ window.__ebAPI.post('/notifications/read-all', {}).catch(function(){}); }
          });
        }
        // Clicking a notification opens its detail popup — inline on the
        // notifications page, otherwise it deep-links there (?notif=<id>).
        var ebBmListClickEl = document.getElementById('ebBmList');
        if(ebBmListClickEl && !ebBmListClickEl._ebOpenWired){ ebBmListClickEl._ebOpenWired = true;
          ebBmListClickEl.addEventListener('click', function(e){
            var a=e.target.closest('.eb-bm-item'); if(!a) return;
            var nid=a.getAttribute('data-id');
            if(nid && typeof window.__ebOpenNotifDetail === 'function'){
              e.preventDefault();
              var m=document.getElementById('ebBellMenu'); if(m) m.classList.remove('open');
              window.__ebOpenNotifDetail(nid);
            }
          });
        }
        var ebEsc = function(s){ return String(s==null?'':s).replace(/[&<>"]/g,function(c){ return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]; }); };
        var ebTimeAgo = function(s){ if(!s) return ''; var diff=Date.now()-new Date(s).getTime(); var m=Math.floor(diff/60000); if(m<1) return 'now'; if(m<60) return m+'m ago'; var h=Math.floor(m/60); if(h<24) return h+'h ago'; return Math.floor(h/24)+'d ago'; };
        var ebBellUnread = 0;
        var ebBellSetBadge = function(n){
          bells.forEach(function(b){
            var dot=b.querySelector('.dot'); if(dot) dot.style.display='none';   // replace decorative dot with a real count
            var bd=b.querySelector('.eb-bell-badge');
            if(!bd){ bd=document.createElement('span'); bd.className='eb-bell-badge'; b.appendChild(bd); }
            if(n>0){ bd.textContent=(n>9?'9+':String(n)); bd.style.display='block'; } else { bd.style.display='none'; }
          });
        };
        var ebBellRender = function(items){
          var list=document.getElementById('ebBmList'); if(!list) return;
          if(!items || !items.length){ list.innerHTML='<div class="eb-bm-empty">You\'re all caught up.</div>'; return; }
          list.innerHTML = items.slice(0,8).map(function(n){
            return '<a class="eb-bm-item" href="notifications.html?notif='+encodeURIComponent(n.id||'')+'" data-id="'+ebEsc(n.id||'')+'">' +
              '<div class="eb-bm-title">'+ebEsc(n.title||'Notification')+'</div>' +
              (n.body ? '<div class="eb-bm-body">'+ebEsc(n.body)+'</div>' : '') +
              '<div class="eb-bm-time">'+ebTimeAgo(n.created_at)+'</div>' +
            '</a>';
          }).join('');
        };
        // Mirror notifications.html: per-user notifications for everyone, plus the
        // system audit feed for admins — minus anything the AI did.
        var ebBellLoad = function(cb){
          if(!(window.__ebAPI && window.__ebAPI.get)){ if(cb) cb(); return; }
          var isAi = window.__ebIsAiActivity || function(){ return false; };
          var role=''; try{ role=localStorage.getItem('ebRole')||''; }catch(e){}
          var canAudit = (role==='tenant_admin'||role==='super_admin'||role==='admin');
          var pN = window.__ebAPI.get('/notifications', {page:1, size:20}).catch(function(){ return null; });
          var pA = canAudit ? window.__ebAPI.get('/audit', {page:1, size:20}).catch(function(){ return null; }) : Promise.resolve(null);
          Promise.all([pN, pA]).then(function(rs){
            var nr=rs[0], ar=rs[1];
            var nitems=(nr&&nr.items)||[];
            var items=[];
            nitems.forEach(function(n){
              var it={ id:n.id, unread:!n.read, title:n.title||'Notification', body:n.body||'', created_at:n.created_at, read:!!n.read,
                       action:n.action||'', resource_type:n.resource_type||n.type||'' };
              if(!isAi(it)) items.push(it);
            });
            if(canAudit){
              ((ar&&ar.items)||[]).forEach(function(a){
                var act=(a.action||'').toLowerCase();
                if(act.indexOf('login')>=0||act.indexOf('logout')>=0||act.indexOf('refresh')>=0) return;
                var it={ id:a.id, unread:false, title:(a.details&&a.details.message)||a.action||'System notification', body:'',
                         created_at:a.created_at, read:true, action:a.action||'', resource_type:a.resource_type||'' };
                if(!isAi(it)) items.push(it);
              });
            }
            items.sort(function(x,y){ return new Date(y.created_at||0)-new Date(x.created_at||0); });
            ebBellUnread = nitems.filter(function(n){
              return !n.read && !isAi({ action:n.action, resource_type:n.resource_type||n.type, title:n.title, body:n.body });
            }).length;
            var cnt=document.getElementById('ebBmCount'); if(cnt) cnt.textContent = ebBellUnread>0 ? (ebBellUnread+' unread') : '';
            ebBellSetBadge(ebBellUnread); ebBellRender(items); if(cb) cb();
          }).catch(function(){ if(cb) cb(); });
        };
        var ebBellPosition = function(btn){ var r=btn.getBoundingClientRect(); bellMenu.style.top=(r.bottom+8)+'px';
          // Right-align to the bell, but never let the menu run off the LEFT edge (on
          // phones the avatar sits right of the bell, so bell-relative alignment pushed
          // the 344px menu partly off-screen). Cap so the left edge stays >= 8px.
          var mw=Math.min(344, window.innerWidth*0.92);
          var right=Math.max(8,(window.innerWidth-r.right));
          right=Math.min(right, window.innerWidth-mw-8);
          if(right<8) right=8;
          bellMenu.style.right=right+'px'; };
        var ebBellClose = function(){ bellMenu.classList.remove('open'); };
        bells.forEach(function(b){
          if(b._ebBellWired) return; b._ebBellWired=true;
          b.addEventListener('click', function(e){
            e.stopPropagation();
            if(bellMenu.classList.contains('open')){ ebBellClose(); return; }
            ebBellPosition(b);
            ebBellLoad(function(){
              // Opening previews the latest AND resets the unread count.
              // Record a "read up to" timestamp so the notifications PAGE and the
              // sidebar badge (which also surface non-/notifications rows like the
              // /audit feed that have no server read-state) treat everything up to
              // now as read too — keeping the bell, the page and the sidebar in sync.
              try { localStorage.setItem('ebNotifsReadAt', String(Date.now())); } catch(e){}
              ebBellSetBadge(0); var c0=document.getElementById('ebBmCount'); if(c0) c0.textContent='';
              try { if(window.__ebRefreshNotifBadges) window.__ebRefreshNotifBadges(); } catch(e){}
              if(window.__ebAPI && window.__ebAPI.post){
                window.__ebAPI.post('/notifications/read-all', {}).then(function(){
                  ebBellSetBadge(0); var c=document.getElementById('ebBmCount'); if(c) c.textContent='';
                }).catch(function(){});
              }
            });
            bellMenu.classList.add('open');
          });
        });
        // Let other code (e.g. the notifications page's own "Mark all read")
        // clear the topbar bell badge live, without a reload.
        window.__ebResetBell = function(){ ebBellSetBadge(0); var c=document.getElementById('ebBmCount'); if(c) c.textContent=''; ebBellClose(); };
        ebBellLoad();   // initial unread badge
        if(!window._ebBellDocBound){
          window._ebBellDocBound = true;
          document.addEventListener('click', function(e){
            if(bellMenu.classList.contains('open') && !bellMenu.contains(e.target) && !e.target.closest('.topbar .icon-btn[aria-label="Notifications"]')) ebBellClose();
          });
          document.addEventListener('keydown', function(e){ if(e.key==='Escape') ebBellClose(); });
          window.addEventListener('resize', ebBellClose);
          window.addEventListener('scroll', ebBellClose, true);
        }
      }

      // Avatar → toggle dropdown
      var avatars = document.querySelectorAll('.topbar .avatar');
      if(!avatars.length) return;

      // Show the uploaded profile photo on the topbar avatar (cached for an
      // instant, flash-free paint; refreshed from /auth/me below).
      function applyAvatar(url){
        avatars.forEach(function(a){
          if(url){ a.style.backgroundImage='url('+url+')'; a.style.backgroundSize='cover'; a.style.backgroundPosition='center'; a.style.color='transparent'; }
          else { a.style.backgroundImage=''; a.style.color=''; }
        });
        var head = document.querySelector('#ebAvatarMenu .eb-am-head');
        // (name/role text already in the menu head; avatar shown on the button)
      }
      try { var cachedAv = localStorage.getItem('ebAvatar'); if(cachedAv) applyAvatar(cachedAv); } catch(e){ }

      // Inject the dropdown once (lives on document.body, positioned per-click)
      var menu = document.getElementById('ebAvatarMenu');
      if(!menu){
        var role = localStorage.getItem('ebRole') || 'agent';
        var roleLabel = role === 'agent'   ? 'Agent'
                      : role === 'lead'    ? 'Team Leader'
                      : role === 'head'    ? 'Head Manager'
                      : role === 'manager' ? 'Manager'
                      : 'Admin';
        // Show the signed-in person's NAME (with the role as a sub-line). Use a
        // cached name for an instant, flash-free first paint, then refresh from
        // /auth/me below.
        var cachedName = '';
        try { cachedName = localStorage.getItem('ebName') || ''; } catch(e){}
        menu = document.createElement('div');
        menu.id = 'ebAvatarMenu';
        menu.setAttribute('role', 'menu');
        menu.innerHTML =
          '<div class="eb-am-head">' +
            '<span class="eb-am-cap">Signed in as</span>' +
            '<span class="eb-am-name" id="ebAmName">' + (cachedName || roleLabel) + '</span>' +
            '<span class="eb-am-role" id="ebAmRole">' + roleLabel + '</span>' +
          '</div>' +
          '<a class="eb-am-item" href="settings.html" role="menuitem">' +
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.6 1.6 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.6 1.6 0 0 0-1.8-.3 1.6 1.6 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1A1.6 1.6 0 0 0 9 19.4a1.6 1.6 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.6 1.6 0 0 0 .3-1.8 1.6 1.6 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1A1.6 1.6 0 0 0 4.6 9a1.6 1.6 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.6 1.6 0 0 0 1.8.3H9a1.6 1.6 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1c0 .6.4 1.2 1 1.5a1.6 1.6 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.6 1.6 0 0 0-.3 1.8V9c.3.6.9 1 1.5 1H21a2 2 0 1 1 0 4h-.1c-.6 0-1.2.4-1.5 1Z"/></svg>' +
            'Settings' +
          '</a>' +
          '<a class="eb-am-item" href="notifications.html" role="menuitem">' +
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M6 8a6 6 0 1 1 12 0c0 7 3 7 3 9H3c0-2 3-2 3-9Z"/><path d="M10 21a2 2 0 0 0 4 0"/></svg>' +
            'Notifications' +
          '</a>' +
          '<div class="eb-am-divider"></div>' +
          '<button class="eb-am-item danger" type="button" id="ebAvatarLogout" role="menuitem">' +
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="m16 17 5-5-5-5"/><path d="M21 12H9"/></svg>' +
            'Log out' +
          '</button>';
        document.body.appendChild(menu);

        // Wire the logout item: clear role + go to login
        var logoutBtn = menu.querySelector('#ebAvatarLogout');
        if(logoutBtn){
          logoutBtn.addEventListener('click', function(){
            try { localStorage.removeItem('ebRole'); } catch(e){ }
            try { localStorage.removeItem('ebName'); localStorage.removeItem('ebAvatar'); } catch(e){ }
            window.location.href = 'login.html';
          });
        }

        // Fill the real signed-in name from the profile (and cache it so the
        // next page paints it instantly). Falls back silently to the role label.
        try {
          if (window.__ebAPI && window.__ebAPI.get) {
            window.__ebAPI.get('/auth/me').then(function(u){
              if(!u) return;
              var full = ((u.first_name||'') + ' ' + (u.last_name||'')).trim()
                         || u.full_name || u.name || u.email || roleLabel;
              var nm = document.getElementById('ebAmName');
              if(nm) nm.textContent = full;
              try { localStorage.setItem('ebName', full); } catch(e){ }
              // Profile photo: reflect on the topbar avatar + cache it.
              if(u.avatar_url){ applyAvatar(u.avatar_url); try { localStorage.setItem('ebAvatar', u.avatar_url); } catch(e){ } }
              else { try { localStorage.removeItem('ebAvatar'); } catch(e){ } applyAvatar(''); }
            }).catch(function(){ });
          }
        } catch(e){ }
      }

      function positionMenu(av){
        var rect = av.getBoundingClientRect();
        menu.style.top = (rect.bottom + 8) + 'px';
        menu.style.right = Math.max(8, (window.innerWidth - rect.right)) + 'px';
      }
      function closeMenu(){ menu.classList.remove('open'); }

      avatars.forEach(function(av){
        if(av._ebWired) return;
        av._ebWired = true;
        av.addEventListener('click', function(e){
          e.stopPropagation();
          if(menu.classList.contains('open')){ closeMenu(); return; }
          positionMenu(av);
          menu.classList.add('open');
        });
      });

      // Outside-click + Escape close the menu
      if(!window._ebAvatarMenuDocBound){
        window._ebAvatarMenuDocBound = true;
        document.addEventListener('click', function(e){
          if(menu.classList.contains('open') &&
             !menu.contains(e.target) &&
             !e.target.closest('.topbar .avatar')){
            closeMenu();
          }
        });
        document.addEventListener('keydown', function(e){
          if(e.key === 'Escape') closeMenu();
        });
        window.addEventListener('resize', closeMenu);
        window.addEventListener('scroll', closeMenu, true);
      }
    }

    // ===== Sidebar collapse/expand toggle =====
    // Injects a small chevron handle on the right edge of the sidebar that
    // toggles between "labels" (full width with text) and "icons" (64px
    // icon-only). Persists via the existing ebSidebar localStorage key so
    // the choice survives across pages and matches the Settings → Personalize
    // option.
    function injectSidebarCollapseBtn(){
      var sidebar = document.querySelector('.sidebar');
      if(!sidebar || sidebar.querySelector('.sb-collapse-btn')) return;
      var btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'sb-collapse-btn';
      btn.setAttribute('aria-label', 'Collapse sidebar');
      btn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="m15 18-6-6 6-6"/></svg>';
      sidebar.appendChild(btn);
      btn.addEventListener('click', function(e){
        e.stopPropagation();
        var current = localStorage.getItem('ebSidebar') || 'labels';
        var next = current === 'icons' ? 'labels' : 'icons';
        try { localStorage.setItem('ebSidebar', next); } catch(err){}
        if(typeof window.__ebApplySidebar === 'function'){
          window.__ebApplySidebar(next);
        } else {
          document.documentElement.setAttribute('data-sidebar', next);
        }
        btn.setAttribute('aria-label', next === 'icons' ? 'Expand sidebar' : 'Collapse sidebar');
      });
      // Make sure the initial aria-label matches the current state
      var current = localStorage.getItem('ebSidebar') || 'labels';
      btn.setAttribute('aria-label', current === 'icons' ? 'Expand sidebar' : 'Collapse sidebar');
    }

    // ===== Page-level gate: only head + admin can view team-performance.html
    function gateTeamPerfPage(){
      var role = localStorage.getItem('ebRole') || 'agent';
      var here = (location.pathname.split('/').pop() || '').toLowerCase();
      if(here === 'team-performance.html' && role !== 'head' && role !== 'admin'){
        location.replace('ask-the-brain.html');
      }
    }

    // ===== Sidebar layout: make Appointments the first item in Workspaces =====
    // Order requirement: the Appointments link should sit at the top of the
    // Workspaces group for all roles that can see it.
    function appointmentsFirstInWorkspaces(){
      var wsBody = document.querySelector('#sbWorkspaces .sb-group-body');
      if(!wsBody) return;
      var appt = wsBody.querySelector('a[href="appointments.html"]');
      if(!appt) return;
      if(wsBody.firstChild === appt) return;
      wsBody.insertBefore(appt, wsBody.firstChild);
    }

    // ===== Canonical Workspaces order (consistent on EVERY page) =====
    // Each page hardcodes its own sidebar order and links are injected relative
    // to it, so the nav reordered as the user navigated. Enforce one fixed order
    // here, last, after all injects/moves: reorder whatever links are present
    // into this sequence (hidden-by-role links are reordered too but stay
    // hidden). Items not in the list keep their relative spot at the front.
    // ===== Inject "Hirees" link for admins on EVERY page =====
    // Hirees was hardcoded in only a few pages' sidebars, so it vanished when
    // you navigated elsewhere. Inject it on every page (admin roles only) so
    // it's always present and in the same spot. Mirrors injectAllDealsLink.
    function injectHireesLink(){
      var role = localStorage.getItem('ebRole') || 'agent';
      if(role !== 'admin' && role !== 'tenant_admin' && role !== 'super_admin' && role !== 'dev') return;
      var wsBody = document.querySelector('#sbWorkspaces .sb-group-body');
      if(!wsBody) return;
      if(wsBody.querySelector('a[href="hirees.html"]')) return;
      var here = (location.pathname.split('/').pop() || '').toLowerCase();
      var a = document.createElement('a');
      a.className = 'sb-item' + (here === 'hirees.html' ? ' active' : '');
      a.href = 'hirees.html';
      a.id = 'navHirees';
      a.setAttribute('aria-label', 'Hirees');
      a.innerHTML =
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round">' +
          '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M19 8v6M22 11h-6"/>' +
        '</svg>' +
        '<span class="sb-tip">Hirees</span>';
      // Place after All Deals (admin views cluster); normalizeWorkspaceOrder
      // gives it its canonical slot regardless.
      var anchor = wsBody.querySelector('a[href="all-deals.html"]') || wsBody.querySelector('a[href="leaderboard.html"]');
      if(anchor && anchor.nextSibling) wsBody.insertBefore(a, anchor.nextSibling);
      else wsBody.appendChild(a);
    }

    // ===== Inject "Inbox" (applicant SMS) link for admins/dev =====
    // Admin/dev-only surface to text job applicants (hirees). The sidebar is
    // hardcoded per page, so inject at runtime; normalizeWorkspaceOrder() gives
    // it its canonical slot (right after Hirees) regardless.
    function injectApplicantInboxLink(){
      var role = localStorage.getItem('ebRole') || 'agent';
      if(role !== 'admin' && role !== 'tenant_admin' && role !== 'super_admin' && role !== 'dev') return;
      var wsBody = document.querySelector('#sbWorkspaces .sb-group-body');
      if(!wsBody) return;
      if(wsBody.querySelector('a[href="applicant-inbox.html"]')) return;
      var here = (location.pathname.split('/').pop() || '').toLowerCase();
      var a = document.createElement('a');
      a.className = 'sb-item' + (here === 'applicant-inbox.html' ? ' active' : '');
      a.href = 'applicant-inbox.html';
      a.id = 'navApplicantInbox';
      a.setAttribute('aria-label', 'Inbox');
      a.innerHTML =
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round">' +
          '<path d="M3 12h6l2 3h2l2-3h6"/><path d="M3 7l2 12a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2l2-12"/><path d="M5 7l2-3h10l2 3"/>' +
        '</svg>' +
        '<span class="sb-tip">Inbox</span>';
      var anchor = wsBody.querySelector('a[href="hirees.html"]');
      if(anchor && anchor.nextSibling) wsBody.insertBefore(a, anchor.nextSibling);
      else wsBody.appendChild(a);
    }

    // ===== Page-level guard: applicant Inbox is admin/dev only =====
    function gateApplicantInboxPage(){
      var here = (location.pathname.split('/').pop() || '').toLowerCase();
      if(here !== 'applicant-inbox.html') return;
      var role = localStorage.getItem('ebRole') || 'agent';
      if(role !== 'admin' && role !== 'tenant_admin' && role !== 'super_admin' && role !== 'dev') location.replace('ask-the-brain.html');
    }

    // ===== Inject "Admin Inbox" (agent↔admin in-app chat) for AGENTS/dev =====
    // The agent's channel to message admins in-app. Placed right after the
    // customer "Inbox". normalizeWorkspaceOrder() gives it its canonical slot.
    function injectAdminInboxLink(){
      var role = localStorage.getItem('ebRole') || 'agent';
      if(role !== 'agent' && role !== 'dev') return;
      var wsBody = document.querySelector('#sbWorkspaces .sb-group-body');
      if(!wsBody) return;
      if(wsBody.querySelector('a[href="admin-inbox.html"]')) return;
      var here = (location.pathname.split('/').pop() || '').toLowerCase();
      var a = document.createElement('a');
      a.className = 'sb-item' + (here === 'admin-inbox.html' ? ' active' : '');
      a.href = 'admin-inbox.html';
      a.id = 'navAdminInbox';
      a.setAttribute('aria-label', 'Admin Inbox');
      a.innerHTML =
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round">' +
          '<path d="M3 12h6l2 3h2l2-3h6"/><path d="M3 7l2 12a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2l2-12"/><path d="M5 7l2-3h10l2 3"/>' +
        '</svg>' +
        '<span class="sb-tip">Admin Inbox</span>';
      var inbox = wsBody.querySelector('a[href="inbox.html"]');
      if(inbox && inbox.nextSibling) wsBody.insertBefore(a, inbox.nextSibling);
      else wsBody.appendChild(a);
    }

    // ===== Page-level guard: Admin Inbox is agent/dev only =====
    function gateAdminInboxPage(){
      var here = (location.pathname.split('/').pop() || '').toLowerCase();
      if(here !== 'admin-inbox.html') return;
      var role = localStorage.getItem('ebRole') || 'agent';
      if(role !== 'agent' && role !== 'dev') location.replace('ask-the-brain.html');
    }

    function normalizeWorkspaceOrder(){
      var wsBody = document.querySelector('#sbWorkspaces .sb-group-body');
      if(!wsBody) return;
      // Some pages hardcode their OWN nav item as href="#" (active state), which
      // left it unsortable and stuck at the front (e.g. Dashboard on dashboard
      // .html). Give that self-link the real href so it sorts into its canonical
      // slot like every other page.
      var here = (location.pathname.split('/').pop() || '').toLowerCase();
      if(here){
        var self = wsBody.querySelector('a.sb-item[href="#"]');
        if(self && !wsBody.querySelector('a.sb-item[href="' + here + '"]')){
          self.setAttribute('href', here);
          self.classList.add('active');
        }
      }
      var ORDER = [
        'upload-leads.html', 'appointments.html', 'dashboard.html',
        'my-deals.html', 'all-deals.html', 'leaderboard.html', 'hirees.html',
        'applicant-inbox.html',
        'inbox.html', 'admin-inbox.html', 'my-team.html', 'team-performance.html',
        'dispositions.html', 'agent-performance.html', 'analytics.html',
        'deals.html', 'compliance.html'
      ];
      ORDER.forEach(function(href){
        var el = wsBody.querySelector('a[href="' + href + '"]');
        if(el) wsBody.appendChild(el);   // move to end → ends up in ORDER sequence
      });
    }

    // Inject the sidebar-bottom CSS immediately (the script lives in <head>,
    // so we can append to document.head before the body parses). This stops
    // the brief flash where the hardcoded Log out link renders as a full-
    // width text row before the row/icon layout CSS kicks in.
    try { injectLogoutCss(); } catch(e){}

    // Keep the sidebar notification badge truthful on EVERY page: it was a
    // hardcoded "7" baked into each page's markup, which mismatched the real
    // (often 0) count shown inside notifications.html. Derive it from the same
    // source the notifications page uses (/audit) and hide it when there's
    // nothing to show.
    function updateNotifBadges(){
      var badges = document.querySelectorAll('.sidebar a.sb-item[href="notifications.html"] .sb-badge');
      if(!badges.length) return;
      var role = '';
      try { role = localStorage.getItem('ebRole') || ''; } catch(_){}
      // Only admin-level roles can read the audit feed; for everyone else don't
      // call it (avoids a console 403) and just hide the badge.
      var canAudit = (role === 'tenant_admin' || role === 'super_admin' || role === 'admin');
      if(!window.__ebAPI || !canAudit){ badges.forEach(function(b){ b.style.display='none'; }); return; }
      // Honour a "mark all read" (from the bell or the notifications page):
      // anything at/older than that moment counts as read, so the sidebar badge
      // clears in lock-step with the bell and the page.
      var readAt = 0; try { readAt = parseInt(localStorage.getItem('ebNotifsReadAt') || '0', 10) || 0; } catch(_){}
      window.__ebAPI.get('/audit', { page: 1, size: 50 }).then(function(res){
        var its = (res && res.items) ? res.items.filter(function(n){
          var a = (n.action || '').toLowerCase();
          if(a.indexOf('login') >= 0 || a.indexOf('logout') >= 0 || a.indexOf('refresh') >= 0) return false;
          if(readAt && n.created_at && new Date(n.created_at).getTime() <= readAt) return false;
          return true;
        }) : [];
        var count = its.length;
        badges.forEach(function(b){
          if(count > 0){ b.textContent = count > 99 ? '99+' : String(count); b.style.display = ''; }
          else { b.style.display = 'none'; }
        });
      }).catch(function(){
        // No access / error → don't show a misleading number.
        badges.forEach(function(b){ b.style.display = 'none'; });
      });
    }
    window.__ebRefreshNotifBadges = updateNotifBadges;

    // Keep the sidebar INBOX badge truthful on every page. It was a hardcoded
    // "4" baked into each page's markup that never matched the real inbox (e.g.
    // the inbox showed 3 conversations / 0 unread). Derive the real unread count
    // (conversations where the customer messaged last) from /conversations and
    // hide the badge when it's zero.
    // Shared inbox read-state (persisted) so the sidebar badge and the inbox
    // page agree on "unread". A conversation is unread only when the CUSTOMER
    // messaged last AND the agent hasn't opened it since that message. Opening
    // a conversation in the inbox records its last_message_at here, so the badge
    // clears instead of getting stuck at a stale count.
    window.__ebInbox = window.__ebInbox || {
      _read: function(){ try { return JSON.parse(localStorage.getItem('ebInboxRead') || '{}'); } catch(_){ return {}; } },
      markRead: function(convId, lastAt){
        if(!convId) return;
        var m = this._read(); m[String(convId)] = lastAt || new Date().toISOString();
        try { localStorage.setItem('ebInboxRead', JSON.stringify(m)); } catch(_){}
      },
      isInbound: function(c){
        var lmf = ((c && c.last_message_from) || '').toLowerCase();
        return lmf === 'lead' || lmf === 'customer' || lmf === 'inbound' || lmf === 'user' || lmf === 'them';
      },
      isUnread: function(c){
        if(!c || !this.isInbound(c)) return false;
        var seenAt = this._read()[String(c.id)];
        if(!seenAt) return true;
        var last = c.last_message_at ? new Date(c.last_message_at).getTime() : 0;
        return last > new Date(seenAt).getTime();
      }
    };

    function updateInboxBadge(){
      var badges = document.querySelectorAll('.sidebar a.sb-item[href="inbox.html"] .sb-badge');
      if(!badges.length) return;
      function hide(){ badges.forEach(function(b){ b.style.display='none'; }); }
      if(!window.__ebAPI){ hide(); return; }
      // Inbox unread = unread customer conversations + (for agents) unread in-app
      // admin DMs, which are pinned inside this same Inbox.
      Promise.all([
        window.__ebAPI.get('/conversations', { page: 1, size: 50 }).catch(function(){ return null; }),
        window.__ebAPI.get('/inbox/dm/unread-count').catch(function(){ return null; })
      ]).then(function(rs){
        var conv = rs[0], dm = rs[1];
        var items = (conv && conv.items) ? conv.items : [];
        var unread = items.filter(function(c){ return window.__ebInbox.isUnread(c); }).length;
        unread += (dm && dm.unread) ? dm.unread : 0;
        badges.forEach(function(b){
          if(unread > 0){ b.textContent = unread > 99 ? '99+' : String(unread); b.style.display = ''; }
          else { b.style.display = 'none'; }
        });
      }).catch(hide);
    }
    window.__ebUpdateInboxBadge = updateInboxBadge;

    // Live updates: refresh the inbox badge the moment a message arrives or a
    // reply is sent — no page reload. Listens to the realtime events dispatched
    // by services/api.js (Socket.IO). Debounced so a burst of events triggers
    // one refresh. Bound once per page.
    var _inboxBadgeT = null;
    function refreshInboxBadgeSoon(){
      if(_inboxBadgeT) clearTimeout(_inboxBadgeT);
      _inboxBadgeT = setTimeout(updateInboxBadge, 400);
    }
    function bindInboxBadgeRealtime(){
      if(window._ebInboxBadgeBound) return;
      window._ebInboxBadgeBound = true;
      ['engage_cloud_inbound_processed','conversation_message_created','lead_replied','message_delivery_updated','socket_connected','inapp_message']
        .forEach(function(ev){
          window.addEventListener('launchpad:realtime:' + ev, refreshInboxBadgeSoon);
        });
    }

    // ===== Sidebar badge for IN-APP admin↔agent messages =====
    // Separate from the customer Inbox badge above. The admin side badges the
    // Inbox (applicant-inbox.html); an agent badges the Admin Inbox
    // (admin-inbox.html). Count = unread DMs addressed to me (/inbox/dm).
    function dmBadgeHref(){
      var role = ''; try { role = localStorage.getItem('ebRole') || ''; } catch(_){}
      // Agents: admin DMs are pinned INSIDE the Inbox now → folded into the
      // inbox.html badge by updateInboxBadge. Admin-side roles badge their Inbox
      // (applicant-inbox.html).
      if(role === 'tenant_admin' || role === 'super_admin' || role === 'admin' || role === 'dev') return 'applicant-inbox.html';
      return null;
    }
    function updateDmBadge(){
      var href = dmBadgeHref();
      if(!href || !window.__ebAPI || !window.__ebAPI.get) return;
      var link = document.querySelector('.sidebar a.sb-item[href="' + href + '"]');
      if(!link) return;
      window.__ebAPI.get('/inbox/dm/unread-count').then(function(r){
        var n = (r && r.unread) || 0;
        var b = link.querySelector('.sb-badge');
        if(!b){ b = document.createElement('span'); b.className = 'sb-badge'; link.appendChild(b); }
        if(n > 0){ b.textContent = n > 99 ? '99+' : String(n); b.style.display = ''; }
        else { b.style.display = 'none'; }
      }).catch(function(){});
    }
    window.__ebUpdateDmBadge = updateDmBadge;   // inbox pages call this after marking a thread read
    var _dmBadgeT = null;
    function refreshDmBadgeSoon(){ if(_dmBadgeT) clearTimeout(_dmBadgeT); _dmBadgeT = setTimeout(updateDmBadge, 400); }
    function bindDmBadgeRealtime(){
      if(window._ebDmBadgeBound) return;
      window._ebDmBadgeBound = true;
      ['inapp_message','socket_connected'].forEach(function(ev){
        window.addEventListener('launchpad:realtime:' + ev, refreshDmBadgeSoon);
      });
      window.addEventListener('focus', refreshDmBadgeSoon);
      setInterval(updateDmBadge, 30000);
      // Ensure the socket is connected even on pages that don't open it themselves.
      try { window.__ebRealtime && window.__ebRealtime.connect(); } catch(_){}
    }

    // ===== Universal topbar search =====
    // The topbar "Search…" box (.topbar .search input) was a dead no-op on most
    // pages. Wire it everywhere: ⌘K / Ctrl+K focuses it, and typing filters the
    // page's primary list rows (leads, teammates, notifications, table rows,
    // etc.) live. Pages that already wire their own topbar search (inbox via
    // #topSearch, settings via its own handler) are skipped.
    function wireGlobalSearch(){
      var input = document.querySelector('.topbar .search input');
      if(!input || input._ebSearchWired) return;
      if(input.id === 'topSearch') return; // inbox handles its own
      var here = (location.pathname.split('/').pop() || '').toLowerCase();
      if(here === 'settings.html') return;  // settings has its own search filter
      input._ebSearchWired = true;

      // ⌘K / Ctrl+K focuses the search.
      document.addEventListener('keydown', function(e){
        if((e.metaKey || e.ctrlKey) && (e.key === 'k' || e.key === 'K')){
          e.preventDefault(); input.focus(); input.select();
        }
      });

      // Common "row" containers across the pages. We hide any that don't match
      // the query (and restore all when the box is cleared). Sidebar/topbar
      // elements are never touched.
      var SEL = ['.lead', '.mate', '.notif', '.rec-item', '.lb-row', '.appt-block',
                 '.team-card', '.list-row', '.comp-item', '.coach-item', '.risk-item',
                 '.deal-row', 'table tbody tr'].join(', ');
      function applyFilter(){
        var q = (input.value || '').trim().toLowerCase();
        var els = document.querySelectorAll(SEL);
        els.forEach(function(el){
          if(el.closest('.sidebar') || el.closest('.topbar')) return;
          var t = (el.textContent || '').toLowerCase();
          el.style.display = (!q || t.indexOf(q) >= 0) ? '' : 'none';
        });
      }
      input.addEventListener('input', applyFilter);
      // Re-apply after async data loads so filtering survives re-renders.
      window.addEventListener('launchpad:realtime', function(){ if(input.value) applyFilter(); });
    }

    function wireLogout(){
      // CSS already in place; wire DOM bits once the sidebar exists.
      injectLogoutCss();
      addLogoutBtn();
      updateNotifBadges();
      updateInboxBadge();
      bindInboxBadgeRealtime();
      // Role gates
      hideMyTeamForAgents();
      hideQAForAgents();
      hideDashboardForLeads();
      redirectDealsToAnalytics();
      hideAppointmentsForHeads();
      hidePerfAndAnalyticsForAgents();
      gateUploadLeadsLink();
      gateDispositionsForNonAdmins();
      gateAdminTabs();
      gateLeadPerformance();
      gateQAReviewForAll();
      gateTeamPerfPage();
      // Sidebar layout adjustments (run after gating so we don't move hidden
      // links). Order matters: appointmentsFirstInWorkspaces() places
      // Appointments at index 0, then moveUploadIntoWorkspaces() runs last
      // and slides Upload Leads in ahead of it, claiming the first slot.
      injectTeamPerfLink();
      injectDispositionsLink();
      injectComplianceLink();
      injectMyDealsLink();
      gateMyDealsPage();
      injectAllDealsLink();
      gateAllDealsPage();
      gateDidFleetPage();          // admin/dev only page guard (redirect non-admins)
      injectHireesLink();
      injectApplicantInboxLink();  // admin/dev: applicant SMS inbox
      gateApplicantInboxPage();    // admin/dev only page guard
      // NOTE: the agent "Admin Inbox" was retired — admin DMs are now pinned at
      // the top of the agent's regular Inbox (inbox.html). admin-inbox.html is a
      // redirect stub to inbox.html.
      injectCeoForDev();           // dev: ensure CEO Dashboard on every page
      injectUploadForDev();        // dev: ensure Upload Leads on every page
      injectLeaderboardLink();
      appointmentsFirstInWorkspaces();
      moveUploadIntoWorkspaces();
      normalizeWorkspaceOrder();   // final authority on the Workspaces order — same on every page
      D.classList.remove('eb-nav-pending'); // order finalized — reveal the nav (no visible reorder)
      updateDmBadge();             // in-app unread badge on Inbox / Admin Inbox (after injects)
      bindDmBadgeRealtime();
      // Topbar: enforce one consistent layout, THEN wire its interactivity.
      normalizeTopbar();
      // Topbar interactivity (bell + avatar dropdown)
      wireTopbarIcons();
      // Sidebar collapse/expand toggle
      injectSidebarCollapseBtn();
      // Universal topbar search (was a dead no-op input on most pages)
      wireGlobalSearch();
    }
    if(document.readyState === 'loading'){
      document.addEventListener('DOMContentLoaded', wireLogout);
    } else {
      wireLogout();
    }
  } catch(e){
    // Silent — preferences are best-effort
  }
})();
