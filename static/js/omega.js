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
    ['🛡️', 'Starting SSH (openssh-server)…'],
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
        '<h3>VPS ban raha hai…</h3>' +
        '<p class="oco-step">Starting…</p>' +
        '<div class="oco-bar"><i></i></div>' +
        '<small>Please wait — SSH ready hote hi page khud khul jayega.</small>' +
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
    try { sessionStorage.setItem('omega:pending-create', '1'); } catch (e) {}
    overlay();
  }, true);
  function celebrate(){
    const el = overlay();
    if (timer) { clearInterval(timer); timer = null; }
    el.querySelector('h3').textContent = 'VPS ready 🎉';
    el.querySelector('.oco-step').textContent = 'SSH credentials ban gaye';
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
    let pending = null;
    try { pending = sessionStorage.getItem('omega:pending-create'); } catch (e) {}
    if (pending !== '1') return;
    try { sessionStorage.removeItem('omega:pending-create'); } catch (e) {}
    celebrate();
  });
})();

/* ---- action animation: start / stop / restart / SSH repair -------------- */
(function(){
  if (window.__omegaActionAnim) return;
  window.__omegaActionAnim = true;
  const ACTIONS = [
    {re: /fix-ssh|regen-ssh|ssh/i, title: 'Preparing SSH access…', step: 'Setting the root password and opening a public port…'},
    {re: /kvm-access|nesting|toggle/i, title: 'Applying setting…', step: 'Updating the VPS and restarting it…'},
    {re: /restart/i, title: 'Restarting the VPS…', step: 'Services are starting again…'},
    {re: /start/i, title: 'Starting the VPS…', step: 'The container is booting…'},
    {re: /stop|shutdown/i, title: 'Stopping the VPS…', step: 'Shutting the processes down…'}
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
      el.innerHTML = '<div class="oao-card"><div class="oao-dots"><i></i><i></i><i></i></div>' +
                     '<h4></h4><p></p></div>';
      document.body.appendChild(el);
    }
    el.querySelector('h4').textContent = a.title;
    el.querySelector('p').textContent = a.step;
    requestAnimationFrame(function(){ el.classList.add('show'); });
    return el;
  }
  function hide(){
    const el = document.getElementById('omega-action-overlay');
    if (el) { el.classList.remove('show'); setTimeout(function(){ el.remove(); }, 400); }
  }
  document.addEventListener('submit', function(ev){
    const form = ev.target;
    if (!form || !form.getAttribute) return;
    const a = match(form.getAttribute('action'));
    if (!a) return;
    show(a);
  }, true);
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
