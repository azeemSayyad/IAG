/* tour.js — Guided popup tours for Coach Mode hand-offs.
 *
 * When the Coach bar's "Do it now" button is clicked, coach.js writes the task
 * id into sessionStorage.ebTour and navigates to the destination page. This
 * script reads that flag on load and walks the user through the relevant
 * steps with spotlight + popup callouts.
 *
 * Each step targets a selector on the current page and shows a numbered
 * popup with title, body, and a "Next" / "Got it" CTA. The final step marks
 * the coach task as done.
 *
 * Agent role only — exits silently otherwise.
 */
(function(){
  'use strict';

  if(localStorage.getItem('ebRole') !== 'agent') return;

  // What page are we on?
  var path = (location.pathname.split('/').pop() || '').toLowerCase();
  if(!path) return;

  // Read the active tour from sessionStorage
  var tourId = '';
  try { tourId = sessionStorage.getItem('ebTour') || ''; } catch(e){}
  if(!tourId) return;

  // ===== Tour definitions =====
  // Each tour is keyed by the coach task id, then by page filename.
  // Steps use CSS selectors that will be queried on DOMContentLoaded.
  // If no selectors resolve on this page, the tour exits silently.
  var TOURS = {
    'whitney-reply': {
      'inbox.html': [
        { sel:'.lead-list, .leads', title:'1. Whitney Marsh is at the top', body:'She replied 14 hours ago. The brain pinned her to the top of your Hot lane.', pos:'right' },
        { sel:'.cp-card-accent, .conv', title:'2. The brain drafted a reply', body:'Open the conversation and you\'ll see a draft ready in the AI suggestions panel. Tweak it if needed, then send.', pos:'right' },
        { sel:'.fab-wrap', title:'3. Need help? Just ask', body:'If anything looks off, ask the brain to revise the tone, shorten it, or surface the bound rate.', pos:'left' }
      ]
    },
    'daniel-call': {
      'deals.html': [
        { sel:'.deal-list, .kanban, .deals-grid, .panel', title:'1. Daniel Reyes is in the Pending Sig column', body:'His Auto policy has been sitting unsigned for 3 days. Carrier B drops the quote Friday.', pos:'right' },
        { sel:'.deal-detail, .right-pane, .panel', title:'2. Talking points are ready', body:'The brain has drafted what to say on the call. Open his deal to see the script and the deadline language.', pos:'left' },
        { sel:'.fab-wrap', title:'3. Coach you mid-call', body:'During the call, ask the brain "give me the rebuttal for price" or similar. It\'ll pull the right script.', pos:'left' }
      ]
    },
    'priya-rate-fix': {
      'qa-review.html': [
        { sel:'.reviews, .panel', title:'1. Deal #4419 is flagged', body:'Quote sent at $84/mo, true rate is $96/mo. Compliance window is closing.', pos:'right' },
        { sel:'.qa-search, .panel-head', title:'2. Use the rate-correction script', body:'The brain has a tested rate-correction message ready. Click the review to open it.', pos:'bottom' },
        { sel:'.fab-wrap', title:'3. Send within 24h', body:'You have ~6h left in the compliance window. Send the corrected quote and notify the customer upfront.', pos:'left' }
      ]
    },
    'inbox-triage': {
      'inbox.html': [
        { sel:'.tabs, .lane-tabs, .panel-head', title:'1. Switch to the Hot lane', body:'Three unread conversations. Your response time slipped to 4h 12m — let\'s get it back under 1h.', pos:'bottom' },
        { sel:'.lead-list, .leads', title:'2. Work top to bottom', body:'The brain ranked them by close-likelihood. Start at the top and don\'t skip ahead.', pos:'right' },
        { sel:'.fab-wrap', title:'3. Need a draft? Ask', body:'For any reply, ask the brain "draft a friendly check-in" or "draft the binding question". You stay in flow.', pos:'left' }
      ]
    },
    'prep-whitney-wed': {
      'appointments.html': [
        { sel:'.cal, .calendar, .appt-list, .panel', title:'1. Wed 2:30 PM with Whitney', body:'Health quote walkthrough — 30 minutes blocked. The deck is drafted and waiting.', pos:'right' },
        { sel:'.appt-detail, .right-pane, .panel', title:'2. Review the brain\'s prep', body:'Three talking points and a personalised opener. Skim it, add your touch, mark prep-complete.', pos:'left' },
        { sel:'.fab-wrap', title:'3. Live coaching during the call', body:'Pin the prep on the brain page during the call — it\'ll surface answers as questions come up.', pos:'left' }
      ]
    },
    'coaching-discovery': {
      'agent-performance.html': [
        { sel:'.coaching, .modules, .panel', title:'1. Discovery questions module', body:'Due Friday with Sarah. 14 transcript patterns flagged across your recent calls.', pos:'right' },
        { sel:'.kpis, .perf-stats, .panel', title:'2. Why this matters', body:'It\'s the #1 close-rate predictor in your role. Top performers ask 3+ discovery questions per call.', pos:'bottom' },
        { sel:'.fab-wrap', title:'3. Practice with the brain', body:'Ask "quiz me on discovery questions for Auto" and the brain runs a 5-min drill before your next call.', pos:'left' }
      ]
    }
  };

  var pageTour = TOURS[tourId] && TOURS[tourId][path];
  if(!pageTour) return; // Wrong page, nothing to do

  // ===== Inject CSS =====
  function injectCss(){
    if(document.getElementById('ebTourStyle')) return;
    var css =
      '.ebt-back{position:fixed;inset:0;background:rgba(15,17,23,0.45);z-index:9990;animation:ebtFade 280ms ease both;pointer-events:auto}' +
      '@keyframes ebtFade{from{opacity:0}to{opacity:1}}' +
      '.ebt-spot{position:absolute;border-radius:14px;box-shadow:0 0 0 9999px rgba(15,17,23,0.45),0 0 0 4px rgba(var(--accent-rgb),0.65),0 10px 30px rgba(var(--accent-rgb),0.40);pointer-events:none;transition:all 320ms cubic-bezier(0.4,0,0.2,1)}' +
      '.ebt-card{position:fixed;width:min(360px,calc(100vw - 32px));background:#FFFFFF;border:1px solid rgba(26,31,42,0.10);border-radius:14px;box-shadow:0 24px 60px rgba(15,17,23,0.30);padding:18px 18px 16px;z-index:9992;animation:ebtPop 320ms cubic-bezier(0.34,1.56,0.64,1) both}' +
      '@keyframes ebtPop{from{opacity:0;transform:translateY(8px) scale(0.96)}to{opacity:1;transform:translateY(0) scale(1)}}' +
      '.ebt-card-head{display:flex;align-items:center;gap:10px;margin-bottom:8px}' +
      '.ebt-num{width:24px;height:24px;border-radius:7px;background:linear-gradient(135deg,var(--accent),var(--accent-2));color:#fff;display:flex;align-items:center;justify-content:center;font-size:.75rem;font-weight:700;flex-shrink:0;box-shadow:0 1px 2px rgba(var(--accent-hover-rgb),0.30)}' +
      '.ebt-card-cap{font-size:.625rem;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--accent)}' +
      '.ebt-card-title{font-size:1rem;font-weight:600;color:#1A1F2A;letter-spacing:-0.005em;line-height:1.3;margin-bottom:6px}' +
      '.ebt-card-body{font-size:.8125rem;color:rgba(26,31,42,0.70);line-height:1.55}' +
      '.ebt-card-foot{display:flex;align-items:center;gap:8px;margin-top:14px}' +
      '.ebt-prog{font-size:.6875rem;color:rgba(26,31,42,0.42);font-weight:600;letter-spacing:.04em;text-transform:uppercase}' +
      '.ebt-actions{margin-left:auto;display:flex;gap:6px}' +
      '.ebt-btn{height:32px;padding:0 14px;border:1px solid rgba(26,31,42,0.10);background:#FFFFFF;border-radius:8px;font-family:inherit;font-size:.8125rem;font-weight:600;color:#2D3340;cursor:pointer;transition:background 130ms}' +
      '.ebt-btn:hover{background:var(--n95)}' +
      '.ebt-btn.primary{background:var(--accent);color:#fff;border-color:var(--accent);box-shadow:0 1px 2px rgba(var(--accent-hover-rgb),0.18)}' +
      '.ebt-btn.primary:hover{background:var(--accent-hover);border-color:var(--accent-hover)}' +
      // Connector line from spotlight to card
      '.ebt-line{position:fixed;width:2px;background:linear-gradient(180deg,rgba(var(--accent-rgb),0.80),rgba(var(--accent-rgb),0.10));z-index:9991;pointer-events:none}' +
      '.ebt-arrow{position:fixed;width:0;height:0;border-style:solid;z-index:9993;pointer-events:none}' +
      // Dark mode
      'html[data-mode="dark"] .ebt-card{background:#1A1F2A;border-color:rgba(255,255,255,0.10)}' +
      'html[data-mode="dark"] .ebt-card-title{color:#F0F2F5}' +
      'html[data-mode="dark"] .ebt-card-body{color:rgba(255,255,255,0.72)}' +
      'html[data-mode="dark"] .ebt-btn{background:rgba(255,255,255,0.06);color:#D5DAE2;border-color:rgba(255,255,255,0.10)}';
    var st = document.createElement('style');
    st.id = 'ebTourStyle';
    st.textContent = css;
    document.head.appendChild(st);
  }

  // ===== State =====
  var idx = 0;
  var backdrop, spot, card;

  function findEl(sel){
    if(!sel) return null;
    var parts = sel.split(',').map(function(s){return s.trim();});
    for(var i=0;i<parts.length;i++){
      var el = document.querySelector(parts[i]);
      if(el) return el;
    }
    return null;
  }

  function positionSpot(rect){
    if(!spot) return;
    var pad = 8;
    spot.style.top    = (rect.top + window.scrollY - pad) + 'px';
    spot.style.left   = (rect.left + window.scrollX - pad) + 'px';
    spot.style.width  = (rect.width + pad*2) + 'px';
    spot.style.height = (rect.height + pad*2) + 'px';
  }

  function positionCard(rect, pos){
    if(!card) return;
    var cardW = card.offsetWidth;
    var cardH = card.offsetHeight;
    var vw = window.innerWidth, vh = window.innerHeight;
    var x, y;
    var gap = 18;
    if(pos === 'right'){
      x = rect.right + gap;
      y = rect.top + rect.height/2 - cardH/2;
      if(x + cardW > vw - 16){ pos = 'left'; }
    }
    if(pos === 'left'){
      x = rect.left - cardW - gap;
      y = rect.top + rect.height/2 - cardH/2;
      if(x < 16){ pos = 'bottom'; }
    }
    if(pos === 'bottom'){
      x = rect.left + rect.width/2 - cardW/2;
      y = rect.bottom + gap;
      if(y + cardH > vh - 16){ pos = 'top'; }
    }
    if(pos === 'top'){
      x = rect.left + rect.width/2 - cardW/2;
      y = rect.top - cardH - gap;
    }
    // Clamp into viewport
    x = Math.max(16, Math.min(x, vw - cardW - 16));
    y = Math.max(16, Math.min(y, vh - cardH - 16));
    card.style.left = x + 'px';
    card.style.top  = y + 'px';
  }

  function showStep(i){
    if(i >= pageTour.length){ finish(); return; }
    idx = i;
    var step = pageTour[i];
    var target = findEl(step.sel) || document.querySelector('main') || document.body;

    // Scroll target into view if needed
    try { target.scrollIntoView({behavior:'smooth', block:'center'}); } catch(e){}

    // Position the spotlight and the card on next frame so layout settles
    setTimeout(function(){
      var rect = target.getBoundingClientRect();
      positionSpot(rect);

      var isLast = (i === pageTour.length - 1);
      card.innerHTML =
        '<div class="ebt-card-head"><span class="ebt-num">' + (i+1) + '</span><span class="ebt-card-cap">Coach · step ' + (i+1) + ' of ' + pageTour.length + '</span></div>' +
        '<div class="ebt-card-title">' + step.title.replace(/^\d+\.\s*/, '') + '</div>' +
        '<div class="ebt-card-body">' + step.body + '</div>' +
        '<div class="ebt-card-foot">' +
          '<span class="ebt-prog">Step ' + (i+1) + '/' + pageTour.length + '</span>' +
          '<span class="ebt-actions">' +
            (i > 0 ? '<button class="ebt-btn" data-act="back" type="button">Back</button>' : '') +
            '<button class="ebt-btn" data-act="skip" type="button">Skip</button>' +
            '<button class="ebt-btn primary" data-act="next" type="button">' + (isLast ? 'Got it' : 'Next') + '</button>' +
          '</span>' +
        '</div>';

      // Wait one frame so dimensions are accurate
      requestAnimationFrame(function(){
        positionCard(rect, step.pos || 'right');
      });

      card.querySelector('[data-act="next"]').addEventListener('click', function(){ showStep(idx+1); });
      var back = card.querySelector('[data-act="back"]');
      if(back) back.addEventListener('click', function(){ showStep(idx-1); });
      card.querySelector('[data-act="skip"]').addEventListener('click', finish);
    }, 300);
  }

  function finish(){
    // Mark the coach task done (so the queue advances)
    try {
      var doneArr = JSON.parse(localStorage.getItem('ebCoachDone') || '[]') || [];
      if(!doneArr.includes(tourId)) doneArr.push(tourId);
      localStorage.setItem('ebCoachDone', JSON.stringify(doneArr));
      sessionStorage.removeItem('ebTour');
    } catch(e){}

    // Animate out
    if(backdrop) backdrop.style.opacity = '0';
    if(card)     card.style.opacity = '0';
    setTimeout(function(){
      backdrop && backdrop.remove();
      spot     && spot.remove();
      card     && card.remove();
    }, 240);
  }

  function start(){
    injectCss();
    backdrop = document.createElement('div');
    backdrop.className = 'ebt-back';
    backdrop.addEventListener('click', function(e){
      // Clicking the dark area moves forward (the spotlit target is pointer-events:none)
      if(e.target === backdrop) showStep(idx+1);
    });

    spot = document.createElement('div');
    spot.className = 'ebt-spot';

    card = document.createElement('div');
    card.className = 'ebt-card';
    card.style.opacity = '0';
    // Fade-in the card after first positioning
    setTimeout(function(){ if(card) card.style.opacity = '1'; }, 320);

    document.body.appendChild(backdrop);
    document.body.appendChild(spot);
    document.body.appendChild(card);

    showStep(0);

    // Re-position on resize / scroll
    var resync = function(){
      var step = pageTour[idx];
      var target = findEl(step.sel) || document.body;
      var rect = target.getBoundingClientRect();
      positionSpot(rect);
      positionCard(rect, step.pos || 'right');
    };
    window.addEventListener('resize', resync);
    window.addEventListener('scroll', resync, { passive:true });
  }

  // Wait a beat for the page to settle (coach bar inserts, role scripts run)
  function ready(){ setTimeout(start, 500); }

  if(document.readyState === 'loading'){
    document.addEventListener('DOMContentLoaded', ready);
  } else {
    ready();
  }
})();
