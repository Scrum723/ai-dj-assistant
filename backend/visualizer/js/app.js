(() => {
  const $=id=>document.getElementById(id), cv=$('c'), ctx=cv.getContext('2d');
  const Q=new URLSearchParams(location.search), A=AudioEngine.A;
  let W=0,H=0, mode='story', cur=null, prev=null, xf=1, flashA=0, lastFlash=-9, uiTimer=0, now=0, last=performance.now()/1000;
  const opts={safe:true,titles:true,proj:false,debug:Q.get('debug')==='1'};
  const num=(k,d)=>{ const v=parseFloat(Q.get(k)); return isFinite(v)?v:d; };
  $('sceneMin').value=Math.max(0.5,Math.min(15,num('sceneMinutes',4))); $('react').value=Math.max(0,Math.min(3,num('reactivity',1.2))); $('sens').value=num('sens',1);
  if(Q.get('autogain')==='0') $('agc').checked=false;
  const SD=def=>def.dur<1e9? (+$('sceneMin').value)*60 : def.dur; // effective story length
  const fx={sp:[], rings:[], shock:0, hue:0};
  function resize(){ const dpr=Math.min(window.devicePixelRatio||1,2); let w=innerWidth*dpr, h=innerHeight*dpr; const k=Math.min(1,Math.sqrt(1920*1080/(w*h))); // cap render ~1080p
    W=cv.width=Math.round(w*k); H=cv.height=Math.round(h*k); }
  addEventListener('resize',resize); resize();
  const fmt=s=>`${Math.floor(s/60)}:${String(Math.round(s%60)).padStart(2,'0')}`;
  SCENES.forEach((s,i)=>{ const o=document.createElement('option'); o.value=i; o.textContent=`${i+1}. ${s.name}`; $('scene').appendChild(o); });
  const ro=document.createElement('option'); ro.value='R'; ro.textContent='★ RANDO-TRON (∞)'; $('scene').appendChild(ro);

  function makeInst(def,idx){ const c=document.createElement('canvas'); c.width=W; c.height=H; const g=c.getContext('2d'); g.fillStyle='#000'; g.fillRect(0,0,W,H);
    const S={def,idx,c,g,t:0,dt:0,p:0,act:0,ap:0,W,H,a:A,flash}; def.init(S); return S; }
  function flash(x){ if(opts.safe){ if(now-lastFlash<0.34) return; x=Math.min(x,.3);} lastFlash=now; flashA=Math.max(flashA,x); }
  function go(idx, instant){ const def= idx==='R'?RandoTron:SCENES[(idx+SCENES.length)%SCENES.length]; if(idx!=='R') idx=(idx+SCENES.length)%SCENES.length;
    prev= instant?null:cur; cur=makeInst(def,idx); xf= prev?0:1; $('scene').value=idx; showTitle(def); }
  function showTitle(def){ const t=$('title'); if(!opts.titles) return; t.querySelector('.tt').textContent=def.name; t.querySelector('.ts').textContent=def.sub+(def.dur<1e9?`  ·  ${fmt(SD(def))}`:'');
    t.classList.remove('hidden'); clearTimeout(showTitle.h); showTitle.h=setTimeout(()=>t.classList.add('hidden'),4000); }
  function nextIdx(dir=1){ if(!cur||cur.idx==='R') return dir>0?0:SCENES.length-1;
    if(mode==='shuffle'){ let n; do n=(Math.random()*SCENES.length)|0; while(n===cur.idx); return n; }
    if(mode==='story'){ const k=STORY_ORDER.indexOf(cur.idx); return STORY_ORDER[(k+dir+SCENES.length)%SCENES.length]; }
    return cur.idx+dir; }
  function setMode(m){ mode=m; $('mode').value=m; if(m==='rando') go('R'); else if(cur&&cur.idx==='R') go(m==='story'?STORY_ORDER[0]:0); }

  function step(S,dt){ S.dt=dt; S.t+=dt; S.W=W; S.H=H; if(S.c.width!==W||S.c.height!==H){ S.c.width=W; S.c.height=H; }
    if(S.def.dur<1e9){ // story progress: time + beats push forward; a drop jumps to the climax
      S.p += dt/SD(S.def) + (A.beat?0.0004*A.sens:0); if(A.drop && S.p<.5) S.p=.5;
      const B=[0,.25,.5,.8,1.0001]; S.act=B.findIndex((b,i)=>S.p>=b&&S.p<B[i+1]); if(S.act<0) S.act=3; S.ap=clamp((S.p-B[S.act])/(B[S.act+1]-B[S.act])); S.phase=Math.min(2,Math.floor(S.ap*3)); }
    S.def.draw(S.g,S); }

  function frame(){ const tn=performance.now()/1000, dt=Math.min(.05,tn-last); last=tn; now+=dt; A.sens=+$('sens').value; A.react=+$('react').value; A.autoGain=$('agc').checked; AudioEngine.update(dt,now);
    step(cur,dt); if(prev){ xf+=dt/2.5; step(prev,dt); if(xf>=1){ prev=null; xf=1; } }
    if(cur.def.dur<1e9 && cur.p>=1){ if(mode==='loop') go(cur.idx); else go(nextIdx(1)); }
    ctx.globalAlpha=1; ctx.fillStyle='#000'; ctx.fillRect(0,0,W,H);
    composite(dt);
    if(flashA>0.01){ ctx.fillStyle=`rgba(235,240,255,${flashA})`; ctx.fillRect(0,0,W,H); flashA*= opts.safe?.8:.7; }
    // HUD
    $('mb').style.transform=`scaleX(${A.bass})`; $('mm').style.transform=`scaleX(${A.mid})`; $('mh').style.transform=`scaleX(${A.high})`; $('bpm').textContent=(A.bpm||'—')+' BPM';
    $('act').textContent= cur.def.dur<1e9?`Act ${cur.act+1}/4: ${cur.def.acts[cur.act]} · part ${cur.phase+1}/3 · ${fmt(cur.p*SD(cur.def))} / ${fmt(SD(cur.def))}`:'';
    if(opts.debug) debugMeter();
    $('badge').textContent= cur.idx==='R'?'◉ RANDO-TRON  '+(RandoTron.label||''):''; $('badge').style.opacity=$('ui').classList.contains('off')&&cur.idx!=='R'?0:1;
    if((uiTimer+=dt)>4 && !$('ui').matches(':hover')) $('ui').classList.add('off');
    requestAnimationFrame(frame); }

  // ---- v2: camera / palette / FX layer applied over every scene ----
  function composite(dt){ const S=cur, Rk=A.react, prog=S.def.dur<1e9?S.p:(S.t/240)%1, T=now;
    if(A.drop){ fx.shock=1; flash(.9); for(let i=0;i<120;i++) fx.sp.push({x:W/2,y:H/2,vx:(Math.random()-.5)*W*1.2,vy:(Math.random()-.5)*H*1.2,l:1,h:Math.random()*360}); }
    fx.shock*=Math.pow(.25,dt);
    // slow drift that evolves across the whole scene (direction changes every sub-phase)
    const ph=(S.act||0)*3+(S.phase||0), dx=Math.sin(T*.05+ph*1.7)*W*.025, dy=Math.cos(T*.037+ph*2.3)*H*.02;
    const zoom=1.06+.05*Math.sin(T*.03+ph)+prog*.04+A.buildup*.05*Rk+A.punch*.045+fx.shock*.12;
    const rot=Math.sin(T*.021+ph)*.012+ (A.buildup*.01*Math.sin(T*3))*Rk;
    const shake=(A.punch*.006+fx.shock*.02)*Rk*Math.min(W,H)*(opts.safe?.6:1), sx=(Math.random()-.5)*shake, sy=(Math.random()-.5)*shake;
    // palette evolution: slow hue wander + per-act offset + energy saturation
    fx.hue += ((Math.sin(T*.013+ph*.9)*28 + (S.act===2?-12:0)) - fx.hue)*Math.min(1,dt*.4);
    const filt=`hue-rotate(${fx.hue.toFixed(1)}deg) saturate(${(1+A.energy*.6*Rk+A.buildup*.4).toFixed(2)}) brightness(${(1+A.punch*.18+fx.shock*.4).toFixed(2)}) contrast(${(1+A.snap*.12).toFixed(2)})`;
    const draw=(c,al)=>{ ctx.save(); ctx.globalAlpha=al; ctx.translate(W/2+dx+sx,H/2+dy+sy); ctx.rotate(rot); ctx.scale(zoom,zoom); ctx.drawImage(c,-W/2,-H/2); ctx.restore(); };
    ctx.filter=filt; if(prev) draw(prev.c,1-xf); draw(S.c,prev?xf:1); ctx.filter='none';
    // kick rings
    if(A.kick && Rk>0) fx.rings.push({r:Math.min(W,H)*.05,l:.5*Rk});
    ctx.save(); ctx.globalCompositeOperation='lighter';
    fx.rings=fx.rings.filter(r=>{ r.r+=dt*Math.max(W,H)*.9; r.l-=dt*1.2; if(r.l<=0) return false; ctx.strokeStyle=`rgba(255,200,140,${Math.min(.25,r.l*.35)})`; ctx.lineWidth=3+r.l*10; ctx.beginPath(); ctx.arc(W/2,H/2,r.r,0,Math.PI*2); ctx.stroke(); return true; });
    // highs -> shimmer sparkles; hats -> bursts
    const nSp=(A.high*6+(A.hat?18:0))*Rk*dt*60/4; for(let i=0;i<nSp;i++) fx.sp.push({x:Math.random()*W,y:Math.random()*H,vx:0,vy:-H*.03,l:.6+Math.random()*.4,h:180+Math.random()*80});
    if(fx.sp.length>900) fx.sp.splice(0,fx.sp.length-900);
    fx.sp=fx.sp.filter(p=>{ p.x+=p.vx*dt; p.y+=p.vy*dt; p.vx*=.96; p.vy*=.96; p.l-=dt*.9; if(p.l<=0) return false; const s=1+p.l*2.5*(1+A.air);
      ctx.fillStyle=`hsla(${p.h},90%,75%,${p.l*.8})`; ctx.fillRect(p.x-s/2,p.y-s/2,s,s); return true; });
    // buildup: rising streaks (weather intensifying)
    const nR=Math.floor(A.buildup*Rk*80 + (S.act===2?20:0)*Rk*A.level); if(nR>0){ ctx.strokeStyle=`rgba(190,215,255,${.15+A.buildup*.25})`; ctx.lineWidth=1; ctx.beginPath();
      for(let i=0;i<nR;i++){ const x=((i*137.5+T*(300+i*7))%(W+200))-100, y=((i*91.3+T*H*(1.2+A.buildup)))%H; ctx.moveTo(x,y); ctx.lineTo(x-12,y-30-A.buildup*40); } ctx.stroke(); }
    ctx.restore();
    // snares -> lightning flicker (goes through strobe-safe limiter)
    if(A.snare && Rk>0.3 && (S.act>=1||A.energy>.3)) flash(.12*Rk);
    // vignette deepens with buildup, opens on drop
    const vg=ctx.createRadialGradient(W/2,H/2,Math.min(W,H)*(.35-A.buildup*.12),W/2,H/2,Math.max(W,H)*.75);
    vg.addColorStop(0,'rgba(0,0,0,0)'); vg.addColorStop(1,`rgba(0,0,0,${Math.max(0,.35+A.buildup*.35-fx.shock*.4)})`); ctx.fillStyle=vg; ctx.fillRect(0,0,W,H);
    if(fx.shock>.02){ ctx.strokeStyle=`rgba(255,255,255,${fx.shock*.6})`; ctx.lineWidth=20*fx.shock; ctx.beginPath(); ctx.arc(W/2,H/2,(1-fx.shock)*Math.max(W,H),0,Math.PI*2); ctx.stroke(); }
  }
  function debugMeter(){ const s=Math.max(1,H/1080), x0=W-330*s, y0=16*s, w=314*s, h=170*s; ctx.save(); ctx.fillStyle='rgba(0,0,0,.72)'; ctx.fillRect(x0,y0,w,h);
    ctx.font=`${11*s}px monospace`; ctx.fillStyle='#cfe'; const names=['sub','bass','lowmid','mid','high','air'], cols=['#f33','#f80','#fd0','#4f6','#3cf','#c6f'];
    names.forEach((n,i)=>{ const v=A[n==='bass'?'bass':n]??0, pv=A.peaks[n]||0, bx=x0+10*s+i*38*s, bh=90*s; ctx.fillStyle='#223'; ctx.fillRect(bx,y0+14*s,28*s,bh);
      ctx.fillStyle=cols[i]; ctx.fillRect(bx,y0+14*s+bh*(1-Math.min(1,v)),28*s,bh*Math.min(1,v)); ctx.fillStyle='#fff'; ctx.fillRect(bx,y0+14*s+bh*(1-Math.min(1,pv)),28*s,2*s);
      ctx.fillStyle='#cfe'; ctx.fillText(n,bx,y0+118*s); });
    const lx=x0+240*s; [['KICK',A.kickEnv,'#f80'],['SNARE',A.snareEnv,'#4f6'],['HAT',A.hatEnv,'#3cf'],['BEAT',A.sinceBeat<.12?1:0,'#fff']].forEach(([n,v,c],i)=>{ ctx.fillStyle=v>.3?c:'#333'; ctx.fillRect(lx,y0+(14+i*22)*s,14*s,14*s); ctx.fillStyle='#cfe'; ctx.fillText(n,lx+18*s,y0+(25+i*22)*s); });
    ctx.fillStyle='#cfe'; ctx.fillText(`BPM ${A.bpm||'—'}  phase`,x0+10*s,y0+138*s); ctx.fillStyle='#333'; ctx.fillRect(x0+120*s,y0+130*s,60*s,8*s); ctx.fillStyle='#ff0'; ctx.fillRect(x0+120*s+A.beatPhase*56*s,y0+130*s,4*s,8*s);
    ctx.fillStyle='#cfe'; ctx.fillText(`build ${(A.buildup*100|0)}%  flux ${A.flux.toFixed(2)}  gain ${A.gain.toFixed(1)}x  ${fx.shock>.1?'DROP!':''}`,x0+10*s,y0+158*s); ctx.restore(); }

  // UI wiring
  const poke=()=>{ uiTimer=0; if(!hideLock) $('ui').classList.remove('off'); }; let hideLock=false;
  addEventListener('mousemove',poke);
  $('mode').onchange=e=>setMode(e.target.value); $('scene').onchange=e=>{ const v=e.target.value; v==='R'?setMode('rando'):(mode==='rando'&&setMode('story'),go(+v)); };
  $('prev').onclick=()=>go(nextIdx(-1)); $('next').onclick=()=>go(nextIdx(1));
  $('fs').onclick=()=>document.fullscreenElement?document.exitFullscreen():document.documentElement.requestFullscreen();
  $('helpb').onclick=()=>$('help').classList.toggle('hidden');
  $('safe').onchange=e=>opts.safe=e.target.checked; $('titles').onchange=e=>opts.titles=e.target.checked;
  $('proj').onchange=e=>{ opts.proj=e.target.checked; cv.style.filter=opts.proj?'contrast(1.7) saturate(1.5) brightness(.85)':''; };
  const status=s=>$('status').textContent=s;
  async function fillDevices(){ const d=await AudioEngine.listDevices(), sel=$('dev'), v=sel.value; sel.length=1; d.forEach((x,i)=>{ const o=document.createElement('option'); o.value=x.deviceId; o.textContent=x.label||`Input ${i+1}`; sel.appendChild(o); }); sel.value=v; }
  fillDevices(); navigator.mediaDevices&&navigator.mediaDevices.addEventListener&&navigator.mediaDevices.addEventListener('devicechange',fillDevices);
  $('mic').onclick=async()=>{ try{ await AudioEngine.useInput($('dev').value); status('live input ✓'); fillDevices(); }catch(e){ status('input error: '+e.message); } };
  $('dev').onchange=()=>$('mic').onclick();
  const loadFile=async f=>{ try{ await AudioEngine.useFile(f); status('▶ '+f.name); }catch(e){ status('file error: '+e.message); } };
  $('file').onchange=e=>e.target.files[0]&&loadFile(e.target.files[0]);
  $('demo').onclick=async()=>{ await AudioEngine.useDemo(); status('demo 128 BPM tone'); };
  addEventListener('dragover',e=>{e.preventDefault(); $('drop').classList.remove('hidden');}); addEventListener('dragleave',e=>{ if(!e.relatedTarget) $('drop').classList.add('hidden'); });
  addEventListener('drop',e=>{ e.preventDefault(); $('drop').classList.add('hidden'); const f=e.dataTransfer.files[0]; f&&loadFile(f); });
  addEventListener('keydown',e=>{ if(e.target.tagName==='SELECT'||e.target.tagName==='INPUT'&&e.target.type!=='range'&&e.target.type!=='checkbox') return; const k=e.key.toLowerCase(), c=e.code;
    if(/^Digit\d$/.test(c)){ let n=+c[5]; n= n===0?9:n-1; if(e.shiftKey) n+=10; if(n<SCENES.length){ if(mode==='rando') setMode('story'); go(n);} return; }
    const tog=id=>{ $(id).checked=!$(id).checked; $(id).onchange({target:$(id)}); };
    ({ f:()=>$('fs').onclick(), h:()=>{ hideLock=!hideLock; $('ui').classList.toggle('off',hideLock); }, arrowright:()=>go(nextIdx(1)), arrowleft:()=>go(nextIdx(-1)),
       r:()=>setMode('rando'), s:()=>setMode('story'), x:()=>setMode('shuffle'), l:()=>setMode('loop'), p:()=>tog('proj'), t:()=>tog('titles'), '?':()=>$('helpb').onclick(), '/':()=>$('helpb').onclick(),
       d:()=>{ opts.debug=!opts.debug; }, a:()=>{ $('agc').checked=!$('agc').checked; }, '[':()=>{ $('react').value=+$('react').value-.1; }, ']':()=>{ $('react').value=+$('react').value+.1; }, ',':()=>{ $('sceneMin').value=+$('sceneMin').value-.5; }, '.':()=>{ $('sceneMin').value=+$('sceneMin').value+.5; },
       arrowup:()=>{ $('sens').value=+$('sens').value+.1; }, arrowdown:()=>{ $('sens').value=+$('sens').value-.1; } }[k]||(()=>{}))(); poke(); });

  // test hooks: ?scene=N|R&t=seconds&demo=1&ui=0
  if(Q.get('ui')==='0'){ hideLock=true; $('ui').classList.add('off'); }
  if(Q.get('notitle')) { opts.titles=false; $('titles').checked=false; }
  const qs=Q.get('scene'); if(qs==='R') { mode='rando'; $('mode').value='rando'; go('R',true); } else go(qs!=null?+qs:STORY_ORDER[0],true);
  if(Q.get('t')){ const T=+Q.get('t'), dt=1/30; for(let i=0;i<T/dt && i<9000;i++){ now+=dt; AudioEngine.update(dt,now); step(cur,dt); } }
  if(Q.get('demo')) $('demo').onclick();
  requestAnimationFrame(frame);
  window.__vis={get cur(){return cur;}, go, setMode, A, opts};
})();
setInterval(()=>{ const e=document.getElementById('smv'); e.textContent=document.getElementById('sceneMin').value+' min'; const k=document.getElementById('kick'); k.style.color=AudioEngine.A.kickEnv>.3?'#f80':'#333'; },100);
