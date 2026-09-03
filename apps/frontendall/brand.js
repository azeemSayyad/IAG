/* ============================================================================
   brand.js — SINGLE SOURCE OF TRUTH for every brand colour in the portal.

   >>> TO REBRAND THE WHOLE PORTAL, EDIT `BRAND` BELOW. NOTHING ELSE. <<<

   Everything the eye reads as "our colour" — the accent, its hover/gradient
   partners, every alpha tint, the page backdrop, the corner glow and the whole
   off-white surface ramp — is DERIVED from those three hexes at runtime and
   written onto <html> as CSS custom properties. No page, stylesheet or script
   anywhere in apps/frontendall (or apps/sms-ui) may hardcode a brand colour;
   they all consume the variables listed under "Exported variables" below.

   Loaded as a blocking <script> in every page <head>, BEFORE the page's own
   <style>, so the first paint is already branded (no flash of the old colour).

   Exported variables
   ------------------
     --accent --accent-2 --accent-hover      the gradient pair + hover
     --accent-ink                            accent-coloured text on a tint
     --accent-glow                           the standard 32% accent shadow
     --accent-rgb --accent-2-rgb
     --accent-hover-rgb --accent-tint-rgb    "r,g,b" triplets, for
                                             rgba(var(--accent-rgb),0.10)
     --bg --bg-grad-1 --bg-grad-2            page canvas
     --page-gradient --page-glow             body::before / body::after
     --n99 … --n68                           neutral surface ramp; the number
                                             IS the HSL lightness, so --n95 is
                                             lighter than --n88
     --a98 … --a92                           accent-tinted "cream" ramp
     --n95-rgb / --a98-rgb / …               "r,g,b" of every ramp step, for
                                             rgba(var(--n98-rgb),0.5)
     --field-bg --field-bg-hover             form field surfaces

   Exported JS
   -----------
     window.EB_BRAND.theme()   active theme object {a,a2,ah,b1,b2,b3,g,gl}
     window.EB_BRAND.THEMES    the theme map (settings.html swatches)
     window.__ebApplyTheme(v)  switch accent theme  (kept for settings.html)
     window.__ebApplyFont(v)   switch base font size
   ========================================================================= */
(function () {
  'use strict';

  /* ------------------------------------------------------------------ */
  /*  THE BRAND.  Change these three hexes to rebrand the whole portal.  */
  /* ------------------------------------------------------------------ */
  var BRAND = {
    accent:      '#1E3A8A',   /* navy  — primary: buttons, links, active nav  */
    accent2:     '#60A5FA',   /* sky   — the light end of every brand gradient */
    accentHover: '#172D6E'    /* deeper navy — hover / pressed / shadow tint   */
  };

  /* Alternate accent themes offered in Settings → Personalization. The first
     entry is the brand itself and the default; the rest are opt-in only. */
  var THEMES = {
    brand:  { a: BRAND.accent, a2: BRAND.accent2, ah: BRAND.accentHover },
    forest: { a: '#4F8268', a2: '#6B9F86', ah: '#3D6651' },
    indigo: { a: '#5E7BA8', a2: '#7B96BD', ah: '#4A6488' },
    rose:   { a: '#A3525C', a2: '#B97077', ah: '#82414B' },
    slate:  { a: '#2D3340', a2: '#5A6275', ah: '#1A1F2A' },
    amber:  { a: '#9C7842', a2: '#B89058', ah: '#7C5E2F' }
  };
  /* Legacy: 'warm' was the old orange default and is still in users'
     localStorage. Resolve it to the current brand rather than stranding them. */
  var LEGACY = { warm: 'brand' };

  var DEFAULT_THEME = 'brand';

  /* Neutral surface ramp, as [saturation, lightness] pairs. The HUE comes from
     the active accent, so a rebrand recolours every card/panel/divider too,
     while these S/L values preserve the original contrast steps exactly. */
  var NEUTRAL_SAT = 0.8;   /* blue reads more saturated than beige — damp it */
  var NEUTRALS = {
    99: [14, 99], 98: [33, 98], 97: [30, 97], 96: [40, 96], 95: [28, 95],
    94: [31, 94], 93: [24, 93], 92: [22, 92], 91: [27, 91], 89: [27, 89],
    88: [24, 88], 85: [24, 85], 84: [18, 84], 83: [16, 83], 81: [17, 81],
    79: [15, 79], 68: [15, 68]
  };

  /* Accent-tinted "cream" ramp — the warm-white washes behind hero panels and
     the page backdrop. Same idea, but saturated enough to read as the brand. */
  var ACCENT_TINTS = { 98: [100, 98], 97: [100, 97], 96: [100, 96], 93: [100, 93], 92: [54, 92] };

  var FONTS = { sm: '14px', md: '16px', lg: '18px', xl: '20px' };

  /* ---------------------------- colour maths ---------------------------- */
  function toRgb(hex) {
    var h = String(hex || '').trim().replace('#', '');
    if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
    var n = parseInt(h, 16);
    if (h.length !== 6 || isNaN(n)) return [0, 0, 0];
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }
  function rgbStr(hex) { return toRgb(hex).join(','); }

  function hueOf(hex) {
    var c = toRgb(hex), r = c[0] / 255, g = c[1] / 255, b = c[2] / 255;
    var mx = Math.max(r, g, b), mn = Math.min(r, g, b), d = mx - mn;
    if (!d) return 0;
    var h = mx === r ? ((g - b) / d + (g < b ? 6 : 0)) : mx === g ? ((b - r) / d + 2) : ((r - g) / d + 4);
    return h * 60;
  }

  function hsl(h, s, l) {
    s /= 100; l /= 100;
    var c = (1 - Math.abs(2 * l - 1)) * s, x = c * (1 - Math.abs(((h / 60) % 2) - 1)), m = l - c / 2;
    var t = h < 60 ? [c, x, 0] : h < 120 ? [x, c, 0] : h < 180 ? [0, c, x]
          : h < 240 ? [0, x, c] : h < 300 ? [x, 0, c] : [c, 0, x];
    return '#' + t.map(function (v) {
      return ('0' + Math.round((v + m) * 255).toString(16)).slice(-2);
    }).join('').toUpperCase();
  }

  /* Lighten a hex toward white — used for the --accent-tint alpha family. */
  function lighten(hex, amount) {
    var c = toRgb(hex);
    return '#' + c.map(function (v) {
      return ('0' + Math.round(v + (255 - v) * amount).toString(16)).slice(-2);
    }).join('').toUpperCase();
  }

  /* ------------------------------- apply -------------------------------- */
  var D = document.documentElement;
  var current = DEFAULT_THEME;

  function set(name, value) { D.style.setProperty(name, value); }

  function normalize(v) {
    v = LEGACY[v] || v;
    return THEMES[v] ? v : DEFAULT_THEME;
  }

  function applyTheme(v) {
    current = normalize(v);
    var t = THEMES[current];

    var hue  = hueOf(t.a);
    var tint = lighten(t.a2, 0.45);          /* the pale end of the glow trio */

    set('--accent', t.a);
    set('--accent-2', t.a2);
    set('--accent-hover', t.ah);
    set('--accent-ink', t.ah);
    set('--accent-rgb', rgbStr(t.a));
    set('--accent-2-rgb', rgbStr(t.a2));
    set('--accent-hover-rgb', rgbStr(t.ah));
    set('--accent-tint-rgb', rgbStr(tint));
    set('--accent-glow', 'rgba(' + rgbStr(t.a) + ',0.32)');

    var n = {}, k;
    for (k in NEUTRALS) if (NEUTRALS.hasOwnProperty(k)) {
      n[k] = hsl(hue, NEUTRALS[k][0] * NEUTRAL_SAT, NEUTRALS[k][1]);
      set('--n' + k, n[k]);
      set('--n' + k + '-rgb', rgbStr(n[k]));   /* for rgba(var(--n98-rgb),.5) */
    }
    var a = {};
    for (k in ACCENT_TINTS) if (ACCENT_TINTS.hasOwnProperty(k)) {
      a[k] = hsl(hue, ACCENT_TINTS[k][0], ACCENT_TINTS[k][1]);
      set('--a' + k, a[k]);
      set('--a' + k + '-rgb', rgbStr(a[k]));
    }

    /* The three per-theme values the old inline head script exposed as
       t.g / t.gl / t.b1-b3, now derived instead of hand-listed per theme. */
    t.g  = 'linear-gradient(135deg,' + a[98] + ' 0%,' + a[93] + ' 50%,' + a[96] + ' 100%)';
    t.gl = 'rgba(' + rgbStr(t.ah) + ',.08)';
    t.b1 = 'rgba(' + rgbStr(t.a2) + ',.40)';
    t.b2 = 'rgba(' + rgbStr(t.a) + ',.32)';
    t.b3 = 'rgba(' + rgbStr(tint) + ',.22)';
    set('--page-gradient', t.g);
    set('--page-glow', t.gl);

    /* body::before/::after and the few gradient chrome bits that can't read a
       variable through a pseudo-element shorthand. */
    var st = document.getElementById('ebPrefsTheme');
    if (!st) { st = document.createElement('style'); st.id = 'ebPrefsTheme'; (document.head || D).appendChild(st); }
    st.textContent =
      /* Page/field surfaces as :root DEFAULTS, not inline properties — this
         <style> is injected before the page's own <style> is parsed, so a page
         that wants a different canvas (mobile-gate, login) still wins. */
      ':root{--bg:' + n[84] + ';--bg-grad-1:' + n[85] + ';--bg-grad-2:' + n[79] +
      ';--field-bg:' + n[93] + ';--field-bg-hover:' + n[89] + '}' +
      'body::before{background:var(--page-gradient) !important}' +
      'body::after{background:radial-gradient(ellipse 95% 85% at 50% 50%,transparent 55%,var(--page-glow) 100%) !important}' +
      '.sb-brand-dot{background:linear-gradient(135deg,var(--accent),var(--accent-2)) !important}' +
      '.usage-fill{background:linear-gradient(90deg,var(--accent),var(--accent-2)) !important}' +
      '.av-circle{background:linear-gradient(135deg,var(--accent),var(--accent-2)) !important}';
  }

  function applyFont(v) { D.style.fontSize = FONTS[v] || FONTS.md; }

  /* ------------------------------- boot --------------------------------- */
  var savedTheme = DEFAULT_THEME, savedFont = 'md';
  try {
    savedTheme = localStorage.getItem('ebTheme') || DEFAULT_THEME;
    savedFont  = localStorage.getItem('ebFontSize') || 'md';
    if (localStorage.getItem('ebMode') === 'dark') D.setAttribute('data-mode', 'dark');
  } catch (e) { /* private mode — fall back to the defaults */ }

  applyFont(savedFont);
  applyTheme(savedTheme);

  window.EB_BRAND = {
    BRAND: BRAND,
    THEMES: THEMES,
    DEFAULT: DEFAULT_THEME,
    normalize: normalize,
    applyFont: applyFont,
    /* Computed value of any brand variable — for SVG presentation attributes
       and <canvas>, neither of which resolves var(). */
    css: function (name) {
      return getComputedStyle(D).getPropertyValue(name).trim();
    },
    theme: function () { return THEMES[current] || THEMES[DEFAULT_THEME]; },
    /* Accent at an arbitrary alpha, as a real colour string — for <canvas>
       contexts, which cannot resolve var(). */
    rgba: function (alpha) {
      return 'rgba(' + rgbStr((THEMES[current] || THEMES[DEFAULT_THEME]).a) + ',' + alpha + ')';
    },
    apply: applyTheme
  };
  window.__ebApplyTheme = applyTheme;
  window.__ebApplyFont = applyFont;
})();
