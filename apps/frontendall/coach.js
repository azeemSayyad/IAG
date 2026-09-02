/* Coach Mode — hard suggestions for the Agent role.
 *
 * Carousel-style notification bar above the page title. Cycles through every
 * pending priority every ~6s, with prev/next arrows for manual control.
 * Each card shows: severity pill, task title, waiting-age, impact line,
 * Show me / Mark done actions.
 *
 * Persistence keys (all localStorage):
 *   ebCoachDone        JSON array of completed task ids
 *   ebCoachSnooze      JSON object id -> snooze-until timestamp (ms)
 *   ebCoachStarted     ISO date string; set the first time the queue is touched
 *
 * Agent role only — exits silently for lead / head / admin.
 */
(function(){
  'use strict';

  if(localStorage.getItem('ebRole') !== 'agent') return;

  var __path = (location.pathname.split('/').pop() || '').toLowerCase();
  if(/login(-v1-glass)?\.html$|index\.html$/i.test(location.pathname)) return;
  // Ask the Brain has a two-pane "split" layout that takes over the viewport
  // once the user asks a question. We still want the coach bar there before
  // the split — but we have to hide it once the page splits, since the bar
  // would push the hemispheres off-screen.
  var __isAskBrain = __path === 'ask-the-brain.html';

  function coachOn(){ return (localStorage.getItem('ebCoachEnabled') || 'on') === 'on'; }
  function setCoach(state){ localStorage.setItem('ebCoachEnabled', state ? 'on' : 'off'); }

  // ===== Task queue — populated from real API =====
  var NOW = Date.now();
  var TASKS = [];

  // ===== State =====
  function getDone(){
    try { return JSON.parse(localStorage.getItem('ebCoachDone') || '[]') || []; }
    catch(e){ return []; }
  }
  function getSnoozed(){
    try { return JSON.parse(localStorage.getItem('ebCoachSnooze') || '{}') || {}; }
    catch(e){ return {}; }
  }
  function setDone(arr){ localStorage.setItem('ebCoachDone', JSON.stringify(arr)); }
  function setSnoozed(obj){ localStorage.setItem('ebCoachSnooze', JSON.stringify(obj)); }

  function pickPending(){
    const done = getDone();
    const snoozed = getSnoozed();
    const now = Date.now();
    return TASKS.filter(t => !done.includes(t.id) && (!snoozed[t.id] || snoozed[t.id] < now));
  }

  function fmtAge(ms){
    const h = Math.floor(ms/3600000);
    const m = Math.floor((ms%3600000)/60000);
    if(h < 1) return m + 'm';
    if(h < 24) return h + 'h ' + m + 'm';
    return Math.floor(h/24) + 'd ' + (h%24) + 'h';
  }

  // ===== CSS =====
  function injectCss(){
    if(document.getElementById('ebCoachStyle')) return;
    const css =
      '.eb-coach{position:relative;display:flex;align-items:center;gap:14px;padding:16px 20px;margin:6px 0 20px;background:#FFFFFF;border:1px solid rgba(201,123,58,0.28);border-radius:11px;box-shadow:0 1px 2px rgba(26,31,42,0.04),0 4px 12px rgba(201,123,58,0.08);overflow:hidden;animation:ebCoachIn 320ms cubic-bezier(0.4,0,0.2,1) both}' +
      '.eb-coach.in-hemi{margin:14px 22px 6px}' +
      '.ebc-allclear.in-hemi{margin:14px 22px 6px}' +
      '@keyframes ebCoachIn{from{opacity:0;transform:translateY(-6px)}to{opacity:1;transform:translateY(0)}}' +
      '@keyframes ebCoachCardIn{from{opacity:0;transform:translateX(8px)}to{opacity:1;transform:translateX(0)}}' +
      '.eb-coach.crit{border-color:rgba(163,82,92,0.30);background:#FFFCF6}' +
      '.ebc-ic{width:32px;height:32px;border-radius:9px;background:linear-gradient(135deg,var(--accent),var(--accent-2));color:#fff;display:flex;align-items:center;justify-content:center;flex-shrink:0;box-shadow:0 1px 2px rgba(178,106,45,0.22),inset 0 1px 0 rgba(255,255,255,0.18);transition:background 220ms}' +
      '.eb-coach.crit .ebc-ic{background:linear-gradient(135deg,#A3525C,#B97077);box-shadow:0 1px 2px rgba(163,82,92,0.22),inset 0 1px 0 rgba(255,255,255,0.18)}' +
      '.ebc-ic svg{width:16px;height:16px;stroke-width:2}' +
      '.ebc-body{flex:1;min-width:0;display:flex;align-items:center;gap:10px;flex-wrap:nowrap;animation:ebCoachCardIn 260ms cubic-bezier(0.4,0,0.2,1) both;overflow:hidden}' +
      '.ebc-title{font-size:.8125rem;font-weight:600;color:var(--text-strong);letter-spacing:-0.005em;line-height:1.3;white-space:nowrap;flex-shrink:0}' +
      '.ebc-pill{display:inline-flex;align-items:center;gap:4px;height:18px;padding:0 8px;border-radius:9px;font-size:.5625rem;font-weight:700;letter-spacing:.06em;text-transform:uppercase;white-space:nowrap}' +
      '.ebc-pill.crit{background:rgba(163,82,92,0.14);color:#7B3A45}' +
      '.ebc-pill.high{background:rgba(201,123,58,0.14);color:#A35E26}' +
      '.ebc-pill.med{background:rgba(156,120,66,0.14);color:#7C5E2F}' +
      '.ebc-pill.low{background:rgba(79,130,104,0.12);color:#3D6651}' +
      '.ebc-impact{font-size:.75rem;color:var(--text-muted);line-height:1.4;letter-spacing:-0.002em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex:1;min-width:0}' +
      '.ebc-impact strong{color:var(--text);font-weight:600}' +
      '.ebc-impact .lbl{font-size:.5625rem;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--text-faint);margin-right:6px}' +
      '.ebc-sep{color:var(--text-faint);font-weight:400;opacity:.6;flex-shrink:0}' +
      '.ebc-age{font-size:.6875rem;color:var(--text-faint);font-weight:600;font-variant-numeric:tabular-nums;letter-spacing:.01em}' +
      '.ebc-actions{display:flex;gap:5px;flex-shrink:0;align-items:center}' +
      '.ebc-btn{height:28px;padding:0 11px;border:1px solid var(--border);background:#FFFFFF;border-radius:7px;font-family:inherit;font-size:.6875rem;font-weight:600;color:var(--text);cursor:pointer;white-space:nowrap;transition:background 130ms,border-color 130ms,transform 130ms;display:inline-flex;align-items:center;justify-content:center;gap:6px}' +
      '.ebc-btn-arrow{display:none;width:14px;height:14px;stroke-width:2.2}' +
      '.ebc-btn:hover{background:#F6F4EF;border-color:var(--border-hover)}' +
      '.ebc-btn.primary{background:var(--accent);color:#FFFFFF;border-color:var(--accent);box-shadow:0 1px 2px rgba(178,106,45,0.18)}' +
      '.ebc-btn.primary:hover{background:var(--accent-hover);border-color:var(--accent-hover);transform:translateY(-1px)}' +
      // Carousel navigation
      '.ebc-nav{display:flex;align-items:center;gap:4px;flex-shrink:0;padding-left:8px;margin-left:4px;border-left:1px solid var(--border-soft,rgba(0,0,0,0.06))}' +
      '.ebc-arrow{width:26px;height:26px;display:flex;align-items:center;justify-content:center;border:1px solid var(--border);background:#FFFFFF;border-radius:7px;color:var(--text-muted);cursor:pointer;padding:0;transition:background 130ms,color 130ms,border-color 130ms}' +
      '.ebc-arrow:hover{background:#F6F4EF;color:var(--text);border-color:var(--border-hover)}' +
      '.ebc-arrow:disabled{opacity:.35;cursor:not-allowed}' +
      '.ebc-arrow svg{width:12px;height:12px;stroke-width:2.2}' +
      '.ebc-count{font-size:.6875rem;font-weight:700;font-variant-numeric:tabular-nums;color:var(--text-muted);letter-spacing:.04em;min-width:30px;text-align:center}' +
      // All clear
      '.ebc-allclear{padding:14px 18px;margin-bottom:16px;background:linear-gradient(90deg,rgba(79,130,104,0.10),rgba(79,130,104,0.02));border:1px solid rgba(79,130,104,0.22);border-radius:14px;display:flex;align-items:center;gap:12px;color:var(--text);font-size:.8125rem}' +
      '.ebc-allclear svg{width:18px;height:18px;color:#3D6651;stroke-width:2}' +
      '.ebc-allclear strong{color:var(--text-strong);font-weight:600}' +
      '.ebc-allclear button{margin-left:auto;height:30px;padding:0 12px;border:1px solid var(--border);background:#FFFFFF;border-radius:8px;font-family:inherit;font-size:.75rem;font-weight:600;color:var(--text);cursor:pointer}' +
      // Re-enable pill
      '.ebc-offpill{position:fixed;bottom:90px;right:22px;display:inline-flex;align-items:center;gap:8px;height:34px;padding:0 14px 0 10px;background:#FFFFFF;border:1px solid var(--border);border-radius:999px;font-size:.75rem;font-weight:600;color:var(--text);cursor:pointer;box-shadow:0 4px 14px rgba(26,31,42,0.10);z-index:48;transition:transform 130ms}' +
      '.ebc-offpill:hover{transform:translateY(-1px)}' +
      '.ebc-offpill svg{width:14px;height:14px;color:var(--text-muted);stroke-width:1.8}' +
      // Progress bar (auto-advance indicator)
      '.ebc-prog-bar{position:absolute;left:0;bottom:0;height:2px;background:linear-gradient(90deg,var(--accent),var(--accent-2));width:0;transition:width 120ms linear;opacity:.55}' +
      '.eb-coach.crit .ebc-prog-bar{background:linear-gradient(90deg,#A3525C,#B97077)}' +
      '.eb-coach.paused .ebc-prog-bar{opacity:0}' +
      '@media (max-width:1100px){.ebc-impact{white-space:normal;line-height:1.35}}' +
      // Tablet / narrow: switch the coach bar to an explicit CSS grid so the
      // title is on the left and the nav arrows pin to the top-right of the
      // same row. The body content (pill + title + age + impact) flows
      // inside the body cell and wraps to more rows if it gets long, while
      // the nav arrows stay anchored in their corner. Actions row below.
      '@media (max-width:820px){' +
        '.eb-coach{' +
          'display:grid !important;' +
          'grid-template-columns:auto 1fr auto !important;' +
          'grid-template-areas:"ic body nav" "act act act" !important;' +
          'column-gap:10px !important;row-gap:8px !important;' +
          'align-items:start !important;' +
        '}' +
        '.ebc-ic{grid-area:ic}' +
        '.ebc-body{grid-area:body;display:flex;flex-wrap:wrap;align-items:center;gap:6px 8px;overflow:visible;min-width:0}' +
        '.ebc-body > .ebc-pill{flex:0 0 auto;order:1}' +
        '.ebc-body > .ebc-title{flex:1 1 100%;order:2;white-space:normal;line-height:1.3}' +
        '.ebc-body > .ebc-sep{order:3}' +
        '.ebc-body > .ebc-age{flex:0 0 auto;order:4}' +
        '.ebc-body > .ebc-impact{flex:1 1 100%;order:5;white-space:normal}' +
        // Nav arrows pin to the top-right of the first row, regardless of
        // how many rows the body wraps to.
        '.ebc-nav{grid-area:nav;align-self:start;margin:0;padding:0 0 0 8px;border-left:1px solid var(--border-soft,rgba(0,0,0,0.06));display:flex;gap:2px}' +
        // Actions row beneath everything
        '.ebc-actions{grid-area:act;justify-self:start;display:flex;gap:6px}' +
      '}' +
      '@media (max-width:480px){' +
        // Drop the impact line + its separators on phones. The pill, title,
        // and action buttons carry the message; the impact metric is noise
        // when space is this tight.
        '.ebc-age,.ebc-sep,.ebc-impact{display:none !important}' +
        '.ebc-title{font-size:.875rem;line-height:1.3;flex:1 1 100% !important}' +
        '.ebc-actions .ebc-btn{font-size:.75rem;height:32px;padding:0 14px}' +
        '.ebc-arrow{width:28px;height:28px}' +
        '.ebc-count{font-size:.6875rem;min-width:34px}' +
        // Tighten the body gap now that there are fewer chips on the row.
        '.ebc-body{gap:4px 8px !important}' +
        // Center the Show me / Mark done buttons across the action row.
        '.ebc-actions{justify-self:center !important;justify-content:center !important;width:100%;gap:8px !important}' +
      '}' +
      // On Ask the Brain at narrow widths, keep the coach bar COMPACT so the
      // welcome zone (search + carousel) stays visible above the fold. Drop
      // the impact line and the separator that precedes it.
      '@media (max-width:820px){' +
        '.eb-coach.in-hemi{margin:10px 16px 4px;padding:8px 10px;gap:8px}' +
        '.eb-coach.in-hemi .ebc-ic{width:26px;height:26px}' +
        '.eb-coach.in-hemi .ebc-ic svg{width:13px;height:13px}' +
        // CSS grid: icon + pill + nav arrows on the top row; title and the
        // big action button share the second row. The title wraps within
        // its column when long, but the action button stays anchored on
        // the right of that same row.
        '.eb-coach.in-hemi{' +
          'display:grid !important;' +
          'grid-template-columns:auto 1fr auto !important;' +
          'grid-template-areas:"ic pill nav" "ic title act" !important;' +
          'column-gap:10px !important;row-gap:8px !important;' +
          'align-items:center !important;' +
          'padding:10px 12px !important;' +
        '}' +
        '.eb-coach.in-hemi .ebc-ic{grid-area:ic;align-self:start;width:28px;height:28px}' +
        '.eb-coach.in-hemi .ebc-ic svg{width:14px;height:14px}' +
        // Let the body wrapper dissolve so its children join the parent grid
        '.eb-coach.in-hemi .ebc-body{display:contents !important}' +
        '.eb-coach.in-hemi .ebc-body > .ebc-pill{grid-area:pill;justify-self:start;align-self:center}' +
        '.eb-coach.in-hemi .ebc-body > .ebc-title{grid-area:title;align-self:center;font-size:.875rem;line-height:1.3;white-space:normal;min-width:0}' +
        '.eb-coach.in-hemi .ebc-age,.eb-coach.in-hemi .ebc-sep,.eb-coach.in-hemi .ebc-impact{display:none}' +
        // Prev + next in the top-right corner
        '.eb-coach.in-hemi .ebc-nav{grid-area:nav;align-self:start;display:flex;gap:2px;margin:0;padding:0;border-left:none}' +
        '.eb-coach.in-hemi .ebc-count{display:none}' +
        '.eb-coach.in-hemi .ebc-arrow{width:26px;height:26px;background:transparent;border-color:transparent;color:var(--text-muted)}' +
        '.eb-coach.in-hemi .ebc-arrow:hover{background:#F6F4EF;color:var(--text)}' +
        '.eb-coach.in-hemi .ebc-arrow svg{width:13px;height:13px;stroke-width:2.2}' +
        // Big action arrow: sits in the SAME row as the title, anchored
        // on the right. Vertically centered against the wrapping title.
        '.eb-coach.in-hemi .ebc-actions{grid-area:act;align-self:center;justify-self:end;display:flex;gap:0}' +
        '.eb-coach.in-hemi .ebc-actions [data-act="done"]{display:none}' +
        '.eb-coach.in-hemi .ebc-actions [data-act="do"]{width:46px;height:36px;padding:0;border-radius:10px;display:inline-flex;align-items:center;justify-content:center}' +
        '.eb-coach.in-hemi .ebc-btn-label{display:none}' +
        '.eb-coach.in-hemi .ebc-btn-arrow{display:inline-block;width:18px;height:18px;color:#fff;stroke-width:2.2}' +
        '.eb-coach.in-hemi .ebc-prog-bar{display:none}' +
      '}';
    const st = document.createElement('style');
    st.id = 'ebCoachStyle';
    st.textContent = css;
    document.head.appendChild(st);
  }

  // ===== Toast =====
  function toast(msg){
    let t = document.getElementById('ebCoachToast');
    if(!t){
      t = document.createElement('div');
      t.id = 'ebCoachToast';
      t.style.cssText = 'position:fixed;bottom:24px;left:50%;transform:translateX(-50%) translateY(20px);background:#1A1F2A;color:#fff;padding:12px 18px;border-radius:10px;font-size:.8125rem;font-weight:500;box-shadow:0 10px 30px rgba(26,31,42,0.30);opacity:0;pointer-events:none;transition:opacity 200ms,transform 200ms;z-index:90';
      document.body.appendChild(t);
    }
    t.textContent = msg;
    requestAnimationFrame(()=>{
      t.style.opacity = '1';
      t.style.transform = 'translateX(-50%) translateY(0)';
    });
    setTimeout(()=>{
      t.style.opacity = '0';
      t.style.transform = 'translateX(-50%) translateY(20px)';
    }, 2200);
  }

  // Pick an insertion point + a sibling to slot before, depending on the page.
  // Ask the Brain has a two-column grid <main>; we insert the bar INSIDE the
  // left hemisphere after the hemi-head (so it doesn't break the grid).
  // Other pages have a normal <main> with a .page-hdr — insert above the hdr.
  function pickInsertion(){
    if(__isAskBrain){
      var lh = document.getElementById('leftHemi') || document.querySelector('.left-hemi');
      if(lh){
        // Insert ABOVE the .hemi-head ("Ask the Brain" title row).
        var head = lh.querySelector('.hemi-head');
        return { parent:lh, before: head || lh.firstChild, askBrain:true };
      }
    }
    var main = document.querySelector('main.main');
    if(!main) return null;
    var hdr = main.querySelector('.page-hdr');
    // Keep the greeting + KPI metrics at the very top: if a KPI row exists
    // (dashboard), place the bar BELOW it; otherwise fall back to above the hdr.
    var kpiRow = main.querySelector('.kpi-row-wrap');
    var before = (kpiRow && kpiRow.parentNode === main) ? kpiRow.nextSibling
               : (hdr && hdr.parentNode === main) ? hdr : main.firstChild;
    return { parent:main, before: before, askBrain:false };
  }

  // ===== Build the bar =====
  function buildBar(){
    const pending = pickPending();
    const slot = pickInsertion();
    if(!slot) return;

    if(!pending.length){
      const all = document.createElement('div');
      all.className = 'ebc-allclear' + (slot.askBrain ? ' in-hemi' : '');
      all.innerHTML =
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>' +
        '<span><strong>Coach Mode: you&rsquo;re all caught up.</strong> No priorities right now.</span>' +
        '<button type="button" id="ebCoachReset">Reset queue</button>';
      slot.parent.insertBefore(all, slot.before);
      const reset = document.getElementById('ebCoachReset');
      reset && reset.addEventListener('click', ()=>{
        localStorage.removeItem('ebCoachDone');
        localStorage.removeItem('ebCoachSnooze');
        location.reload();
      });
      return;
    }

    // Shell — content swapped per index
    const bar = document.createElement('div');
    bar.className = 'eb-coach' + (slot.askBrain ? ' in-hemi' : '');
    bar.id = 'ebCoachBar';
    slot.parent.insertBefore(bar, slot.before);

    let idx = 0;
    const ROTATE_MS = 6500;
    let rotateTimer = null;
    let progressTimer = null;
    let progressStart = 0;
    let paused = false;
    let ageTickTimer = null;

    function severityKey(s){
      return s === 'critical' ? 'crit' : s === 'high' ? 'high' : s === 'medium' ? 'med' : 'low';
    }
    function severityLabel(s){
      return s === 'critical' ? 'Critical' : s === 'high' ? 'High' : s === 'medium' ? 'Medium' : 'Low';
    }

    function render(){
      const t = pending[idx];
      const sKey = severityKey(t.severity);
      const sLabel = severityLabel(t.severity);
      bar.className = 'eb-coach ' + sKey + (paused ? ' paused' : '') + (slot.askBrain ? ' in-hemi' : '');
      const ageStr = t.sinceMs
        ? '<span class="ebc-age" data-age="'+t.sinceMs+'">waiting ' + fmtAge(Date.now() - t.sinceMs) + '</span><span class="ebc-sep">·</span>'
        : '';
      bar.innerHTML =
        '<div class="ebc-ic"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M13 2 4 14h7l-1 8 9-12h-7l1-8Z"/></svg></div>' +
        '<div class="ebc-body">' +
          '<span class="ebc-pill ' + sKey + '">Coach Mode · ' + sLabel + '</span>' +
          '<span class="ebc-title">' + t.title + '</span>' +
          '<span class="ebc-sep">·</span>' +
          ageStr +
          '<span class="ebc-impact">' + t.stake + '</span>' +
        '</div>' +
        '<div class="ebc-actions">' +
          '<button class="ebc-btn primary" data-act="do" type="button">' +
            '<span class="ebc-btn-label">Show me</span>' +
            '<svg class="ebc-btn-arrow" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14"/><path d="m13 6 6 6-6 6"/></svg>' +
          '</button>' +
          '<button class="ebc-btn" data-act="done" type="button">Mark done</button>' +
        '</div>' +
        '<div class="ebc-nav">' +
          '<button class="ebc-arrow" data-act="prev" type="button" aria-label="Previous priority"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="m15 18-6-6 6-6"/></svg></button>' +
          '<span class="ebc-count">' + (idx+1) + ' / ' + pending.length + '</span>' +
          '<button class="ebc-arrow" data-act="next" type="button" aria-label="Next priority"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="m9 18 6-6-6-6"/></svg></button>' +
        '</div>' +
        '<div class="ebc-prog-bar" id="ebcProg"></div>';

      wire();
      startProgress();
      scheduleAgeTick();
    }

    function wire(){
      const t = pending[idx];
      bar.querySelector('[data-act="do"]').addEventListener('click', ()=>{
        try { sessionStorage.setItem('ebTour', t.id); } catch(e){}
        window.location.href = t.href;
      });
      bar.querySelector('[data-act="done"]').addEventListener('click', ()=>{
        const done = getDone();
        if(!done.includes(t.id)) done.push(t.id);
        setDone(done);
        toast('Marked done. Next priority coming up.');
        setTimeout(()=>location.reload(), 600);
      });
      bar.querySelector('[data-act="prev"]').addEventListener('click', ()=>{
        idx = (idx - 1 + pending.length) % pending.length;
        render();
      });
      bar.querySelector('[data-act="next"]').addEventListener('click', ()=>{
        idx = (idx + 1) % pending.length;
        render();
      });
      bar.querySelectorAll('.ebc-dot').forEach(d=>{
        d.addEventListener('click', ()=>{
          const i = parseInt(d.getAttribute('data-d'), 10);
          if(!isNaN(i)){ idx = i; render(); }
        });
      });
    }

    function scheduleAgeTick(){
      if(ageTickTimer) clearInterval(ageTickTimer);
      const t = pending[idx];
      if(!t.sinceMs) return;
      const ageEl = bar.querySelector('.ebc-age');
      if(!ageEl) return;
      ageTickTimer = setInterval(()=>{
        ageEl.textContent = 'waiting ' + fmtAge(Date.now() - t.sinceMs);
      }, 60000);
    }

    function startProgress(){
      if(pending.length < 2) return;
      if(rotateTimer) clearTimeout(rotateTimer);
      if(progressTimer) clearInterval(progressTimer);
      progressStart = Date.now();
      const fill = bar.querySelector('#ebcProg');
      if(fill) fill.style.width = '0%';
      progressTimer = setInterval(()=>{
        if(paused) return;
        const pct = Math.min(100, ((Date.now() - progressStart) / ROTATE_MS) * 100);
        if(fill) fill.style.width = pct + '%';
      }, 120);
      rotateTimer = setTimeout(()=>{
        if(!paused){
          idx = (idx + 1) % pending.length;
          render();
        }
      }, ROTATE_MS);
    }

    function pause(){
      paused = true;
      bar.classList.add('paused');
      if(rotateTimer){ clearTimeout(rotateTimer); rotateTimer = null; }
      if(progressTimer){ clearInterval(progressTimer); progressTimer = null; }
    }
    function resume(){
      if(!paused) return;
      paused = false;
      bar.classList.remove('paused');
      startProgress();
    }

    bar.addEventListener('mouseenter', pause);
    bar.addEventListener('mouseleave', resume);
    bar.addEventListener('focusin', pause);
    bar.addEventListener('focusout', (e)=>{
      if(!bar.contains(e.relatedTarget)) resume();
    });

    render();
  }

  function buildOffPill(){
    if(document.getElementById('ebCoachOffPill')) return;
    const pill = document.createElement('button');
    pill.type = 'button';
    pill.id = 'ebCoachOffPill';
    pill.className = 'ebc-offpill';
    pill.innerHTML =
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M13 2 4 14h7l-1 8 9-12h-7l1-8Z"/></svg>' +
      '<span>Coach mode off — enable</span>';
    pill.addEventListener('click', ()=>{
      setCoach(true);
      location.reload();
    });
    document.body.appendChild(pill);
  }

  function syncSplitVisibility(){
    if(!__isAskBrain) return;
    var main = document.querySelector('main.main');
    var bar = document.getElementById('ebCoachBar') || document.querySelector('.ebc-allclear');
    if(!main || !bar) return;
    var split = main.classList.contains('split');
    bar.style.display = split ? 'none' : '';
  }

  function watchAskBrainSplit(){
    if(!__isAskBrain) return;
    var main = document.querySelector('main.main');
    if(!main) return;
    syncSplitVisibility();
    if(typeof MutationObserver === 'undefined') return;
    var mo = new MutationObserver(syncSplitVisibility);
    mo.observe(main, { attributes:true, attributeFilter:['class'] });
  }

  async function init(){
    injectCss();
    if(coachOn()){
      try {
        var userId = localStorage.getItem('ebUserId') || '';
        var insightsRes = await window.__ebAPI.get('/coaching/insights/' + (userId || 'me'), { days: 7 }).catch(function(){ return null; });
        if (insightsRes && insightsRes.insights && insightsRes.insights.length > 0) {
          TASKS = insightsRes.insights.map(function(insight, idx){
            var sev = insight.priority || 'medium';
            if (sev === 'critical' || sev === 'high' || sev === 'medium' || sev === 'low') {
              // valid
            } else {
              sev = 'medium';
            }
            return {
              id: insight.id || ('task-' + idx),
              title: insight.title || 'Coaching insight',
              desc: insight.description || '',
              stake: insight.impact || '',
              cta: insight.action_label || 'View details',
              href: insight.action_url || 'inbox.html',
              severity: sev,
              sinceMs: insight.created_at ? (Date.now() - new Date(insight.created_at).getTime()) : Date.now() - (idx * 3600000),
            };
          });
        }
      } catch(err) {
        console.warn('Coach mode: failed to load tasks from API', err);
      }
      buildBar();
      watchAskBrainSplit();
    } else {
      buildOffPill();
    }
  }
  function ready(fn){
    if(document.readyState === 'loading'){
      document.addEventListener('DOMContentLoaded', fn);
    } else {
      fn();
    }
  }
  ready(function(){ init(); });
})();
