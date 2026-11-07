(function(){
  async function poll(id){
    try{
      const r=await fetch('/api/jobs/'+id,{headers:{'Accept':'application/json'}}); if(!r.ok)return;
      const j=await r.json(); const row=document.querySelector('[data-job="'+id+'"]');
      if(row){const p=row.querySelector('.progress i'),m=row.querySelector('.job-message'),pct=row.querySelector('.job-pct'); p.style.width=(j.progress||0)+'%';m.textContent=j.message||'';pct.textContent=(j.progress||0)+'%';}
      if(!['done','error'].includes(j.status)) setTimeout(()=>poll(id),1800); else setTimeout(()=>location.reload(),900);
    }catch(e){setTimeout(()=>poll(id),3000)}
  }
  (window.OMEGA_JOBS||[]).forEach(poll);
})();

/* ---- VPS create / SSH provisioning animation ---------------------------- */
(function(){
  if (window.__omegaCreateAnim) return;
  window.__omegaCreateAnim = true;
  const STEPS = [
    ['🧱', 'Allocating CPU, RAM and disk…'],
    ['🐧', 'Creating the container…'],
    ['🌐', 'Bringing networking up…'],
    ['🔐', 'Setting the root password…'],
    ['🛡️', 'Starting SSHX (openssh-server)…'],
    ['🚀', 'Almost ready — collecting credentials…']
  ];
  let timer = null;
  function overlay(){
    let el = document.getElementById('omega-create-overlay');
    if (el) return el;
    el = document.createElement('div');
    el.id = 'omega-create-overlay';
    el.innerHTML =
      '<div class="oco-card">' +
        '<div class="oco-ring"><span></span><span></span><span></span><b>⚡</b></div>' +
        '<h3>Creating your VPS…</h3>' +
        '<p class="oco-step">Starting…</p>' +
        '<div class="oco-bar"><i></i></div>' +
        '<small>Please wait — the page opens automatically once SSHX is ready.</small>' +
      '</div>';
    document.body.appendChild(el);
    requestAnimationFrame(function(){ el.classList.add('show'); });
    let i = 0;
    const step = el.querySelector('.oco-step'), bar = el.querySelector('.oco-bar i');
    step.textContent = STEPS[0][0] + ' ' + STEPS[0][1];
    timer = setInterval(function(){
      if (i < STEPS.length - 1) i++;
      step.textContent = STEPS[i][0] + ' ' + STEPS[i][1];
      bar.style.width = Math.min(92, 14 + i * 15) + '%';
    }, 2000);
    return el;
  }
  function isCreate(form){
    const a = (form.getAttribute('action') || '').toLowerCase();
    return a.indexOf('create-vps') >= 0 || a.indexOf('create_vps') >= 0;
  }
  document.addEventListener('submit', function(ev){
    const form = ev.target;
    if (!form || !isCreate(form)) return;
    // The panel ships its own job-driven progress dialog for VPS creation.
    if (window.__omegaCreateAsync) return;
    try { sessionStorage.setItem('omega:pending-create', '1'); } catch (e) {}
    overlay();
  }, true);
  function celebrate(){
    const el = overlay();
    if (timer) { clearInterval(timer); timer = null; }
    el.querySelector('h3').textContent = 'VPS ready 🎉';
    el.querySelector('.oco-step').textContent = 'SSHX link is ready';
    el.querySelector('.oco-bar i').style.width = '100%';
    el.classList.add('done');
    const colors = ['#22d3ee', '#a78bfa', '#34d399', '#f59e0b', '#fb7185'];
    for (let i = 0; i < 44; i++) {
      const c = document.createElement('i');
      c.className = 'oco-confetti';
      c.style.left = (50 + (Math.random() * 70 - 35)) + '%';
      c.style.background = colors[i % colors.length];
      c.style.animationDelay = (Math.random() * 0.6) + 's';
      el.appendChild(c);
    }
    setTimeout(function(){ el.classList.remove('show'); setTimeout(function(){ el.remove(); }, 700); }, 2800);
  }
  window.addEventListener('load', function(){
    if (window.__omegaCreateAsync) return;
    let pending = null;
    try { pending = sessionStorage.getItem('omega:pending-create'); } catch (e) {}
    if (pending !== '1') return;
    try { sessionStorage.removeItem('omega:pending-create'); } catch (e) {}
    celebrate();
  });
})();

/* ---- action animation: start / stop / restart / SSHX repair ------------- */
(function(){
  if (window.__omegaActionAnim) return;
  window.__omegaActionAnim = true;
  const ACTIONS = [
    {re: /regen-ssh|fix-ssh|sshx|ssh/i, title: 'Creating SSHX access\u2026',
     steps: ['Installing sshx inside the VPS\u2026',
             'Starting the sshx agent\u2026',
             'Waiting for the sshx.io share link\u2026',
             'Saving the link to this VPS\u2026']},
    {re: /reinstall|rebuild|re-install/, title: 'Reinstalling the operating system\u2026',
     steps: ['Wiping the current disk\u2026',
             'Deploying the operating system image\u2026',
             'Configuring networking and SSH\u2026',
             'Starting the fresh system\u2026']},
    {re: /\/vps\/\d+\/features/, title: 'Switching the backend\u2026',
     steps: ['Preparing the new backend\u2026', 'Rebuilding the VPS from its image\u2026']},
    {re: /kvm-access|nesting|toggle/i, title: 'Applying setting\u2026',
     steps: ['Updating the VPS configuration\u2026', 'Restarting to apply it\u2026']},
    {re: /restart/i, title: 'Restarting the VPS\u2026',
     steps: ['Stopping the services\u2026', 'Booting the VPS again\u2026', 'Waiting for SSH to answer\u2026']},
    {re: /start/i, title: 'Starting the VPS\u2026',
     steps: ['Allocating resources\u2026', 'Booting the container\u2026', 'Waiting for networking\u2026']},
    {re: /stop|shutdown/i, title: 'Stopping the VPS\u2026',
     steps: ['Sending the shutdown signal\u2026', 'Shutting the processes down\u2026']}
  ];
  function match(url){
    url = (url || '').toLowerCase();
    if (!url) return null;
    for (const a of ACTIONS) { if (a.re.test(url)) return a; }
    return null;
  }
  function show(a){
    let el = document.getElementById('omega-action-overlay');
    if (!el) {
      el = document.createElement('div');
      el.id = 'omega-action-overlay';
      el.className = 'omega-action-overlay';
      el.innerHTML = '<div class="oao-card"><div class="oao-spin"></div>' +
                     '<h4></h4><p></p><div class="oao-bar"><i></i></div>' +
                     '<div class="oao-dots"><i></i><i></i><i></i></div></div>';
      document.body.appendChild(el);
    }
    el.querySelector('h4').textContent = a.title;
    const label = el.querySelector('p');
    const bar = el.querySelector('.oao-bar i');
    const steps = (a.steps && a.steps.length) ? a.steps : [a.step || ''];
    let at = 0;
    label.textContent = steps[0];
    bar.style.width = '9%';
    clearInterval(el.__omegaSteps);
    if (steps.length > 1) {
      // Long actions (SSHX install, reinstall) take a while: walk the steps so
      // the card keeps telling the user what the panel is doing.
      el.__omegaSteps = setInterval(function(){
        if (at < steps.length - 1) {
          at++;
          label.textContent = steps[at];
        }
        bar.style.width = Math.min(92, 9 + ((at + 1) / steps.length) * 80) + '%';
      }, 2600);
    }
    requestAnimationFrame(function(){ el.classList.add('show'); });
    return el;
  }
  function hide(){
    const el = document.getElementById('omega-action-overlay');
    if (!el) return;
    clearInterval(el.__omegaSteps);
    el.classList.remove('show');
    setTimeout(function(){ el.remove(); }, 400);
  }
  document.addEventListener('submit', function(ev){
    const form = ev.target;
    if (!form || !form.getAttribute) return;
    // VPS creation has its own job-driven progress dialog.
    if (window.__omegaCreateAsync && /create-vps|vps-types/.test(form.getAttribute('action') || '')) return;
    const a = match(form.getAttribute('action'));
    if (!a) return;
    // A blocking POST keeps this page on screen until the node answers, so this
    // overlay is what the user watches while start / stop / reinstall runs.
    show(a);
  }, true);
  function miniSpin(){
    let el = document.getElementById('omega-mini-spin');
    if (!el) {
      el = document.createElement('div');
      el.id = 'omega-mini-spin';
      document.body.appendChild(el);
    }
    requestAnimationFrame(function(){ el.classList.add('show'); });
  }
  document.addEventListener('submit', function(ev){
    const form = ev.target;
    if (!form || !form.getAttribute) return;
    if (match(form.getAttribute('action'))) return;
    if ((form.getAttribute('method') || 'get').toLowerCase() === 'get') return;
    miniSpin();
  }, true);
  // Some pages drive actions through XMLHttpRequest instead of a form.
  const nativeXhrOpen = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function(method, url){
    try {
      const a = match(url);
      if (a && String(method || 'GET').toUpperCase() !== 'GET') {
        show(a);
        this.addEventListener('loadend', function(){ setTimeout(hide, 600); });
      }
    } catch (e) {}
    return nativeXhrOpen.apply(this, arguments);
  };
  // <panel-url>?omega-anim=start|stop|restart|reinstall previews the overlay
  // without touching a VPS, so it can be checked right after an update.
  try {
    const wanted = (new URLSearchParams(location.search).get('omega-anim') || '').toLowerCase();
    if (wanted) {
      const a = match('/' + wanted);
      if (a) { show(a); setTimeout(hide, 6000); }
    }
  } catch (e) {}
  const nativeFetch = window.fetch;
  if (nativeFetch) {
    window.fetch = function(input, init){
      const url = typeof input === 'string' ? input : (input && input.url) || '';
      const a = match(url);
      if (a) { show(a); }
      return nativeFetch.apply(this, arguments).then(function(res){
        if (a) { setTimeout(hide, 900); }
        return res;
      }, function(err){ if (a) hide(); throw err; });
    };
  }
  document.addEventListener('click', function(ev){
    const btn = ev.target && ev.target.closest ? ev.target.closest('button, a') : null;
    if (!btn) return;
    const a = match((btn.getAttribute('formaction') || '') + ' ' + (btn.getAttribute('href') || '') + ' ' + (btn.textContent || ''));
    if (!a) return;
    if (btn.closest('form')) return;  // submit handler covers it
    show(a); setTimeout(hide, 4000);
  }, true);
})();
