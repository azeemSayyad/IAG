/* Shared portal date-range presets — ONE source of truth for leaderboard, all-deals,
 * and my-deals (and mirrored exactly in the Sales Dashboard SPA's presetRange). Every
 * range is computed in America/New_York (Eastern) with CALENDAR month/year, so the same
 * preset resolves to the identical window on every page.
 *   window.EBDates.presetRange(key) -> {from, to}   (YYYY-MM-DD, Eastern)
 *   keys: today · yesterday · this_week · this_month · this_year · all · custom
 */
(function () {
  var BIZ_TZ = 'America/New_York';
  function todayET() { return new Intl.DateTimeFormat('en-CA', { timeZone: BIZ_TZ }).format(new Date()); }
  function shiftISO(iso, days) {
    var p = iso.split('-').map(Number);
    var dt = new Date(Date.UTC(p[0], p[1] - 1, p[2]));
    dt.setUTCDate(dt.getUTCDate() + days);
    return dt.toISOString().slice(0, 10);
  }
  function prettyDate(iso) {
    if (!iso) return '';
    var p = iso.split('-').map(Number);
    return new Date(p[0], p[1] - 1, p[2]).toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' });
  }
  function presetRange(key) {
    var t = todayET(), p = t.split('-').map(Number), Y = p[0], M = ('0' + p[1]).slice(-2);
    switch (key) {
      case 'yesterday': { var y = shiftISO(t, -1); return { from: y, to: y }; }
      case 'this_week': { var off = (new Date(Date.UTC(p[0], p[1] - 1, p[2])).getUTCDay() + 6) % 7; return { from: shiftISO(t, -off), to: t }; } // Mon -> today
      case 'this_month': return { from: Y + '-' + M + '-01', to: t };  // CALENDAR month-to-date
      case 'this_year': return { from: Y + '-01-01', to: t };          // calendar year-to-date
      case 'all': return { from: '2000-01-01', to: t };                // all time — same on every page
      case 'today':
      default: return { from: t, to: t };
    }
  }
  window.EBDates = { BIZ_TZ: BIZ_TZ, todayET: todayET, shiftISO: shiftISO, prettyDate: prettyDate, presetRange: presetRange };
})();
