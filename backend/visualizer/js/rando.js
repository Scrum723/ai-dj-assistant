// RANDO-TRON: procedural generator. Every patch = random (geometry × palette × motion × symmetry × feedback × glitch).
// Small mutations on beats, full re-roll every 4–16 bars (random) or on drops. Seeded from Math.random → never the same twice.
const RandoTron = (() => {
  const GEOS=['polyrings','swarm','spiro','scope','tunnel','lissajous','grid','shards'];
  const MOTIONS=['spin','pulse','orbit','wobble','zoom'];
  let P, fb, fbx, bars=0, nextRoll=8, gen=0;
  const pick=a=>a[(Math.random()*a.length)|0];
  function roll(){ gen++; const h=rnd(360), sch=pick(['analog','triad','mono','split','neon']);
    const hues= sch=='analog'?[h,h+30,h+60]:sch=='triad'?[h,h+120,h+240]:sch=='mono'?[h,h,h+10]:sch=='split'?[h,h+150,h+210]:[h,(h+180)%360,(h+90)%360];
    const layers=Array.from({length:1+(Math.random()*3|0)},()=>({geo:pick(GEOS),mot:pick(MOTIONS),n:3+(Math.random()*9|0),sp:rnd(-1.5,1.5),sz:rnd(.15,.5),hue:pick(hues),lw:rnd(1,4),ph:rnd(TAU)}));
    P={gen,hues,sch,layers,sym:pick([1,2,3,4,5,6,8,12]),mirror:Math.random()<.5,trail:rnd(.04,.35),fbZoom:rnd(.97,1.04),fbRot:rnd(-.03,.03),glitch:Math.random()<.45,blend:pick(['lighter','source-over','screen']),bg:Math.random()<.2?hues[1]:null,sat:rnd(60,100)};
    P.label=`GEN #${gen} · ${P.layers.map(l=>l.geo).join('+')} · ${P.sym}x${P.mirror?' mirror':''} · ${P.sch}${P.glitch?' · glitch':''}`; }
  function mutate(){ const l=pick(P.layers); const k=pick(['n','sp','sz','hue','mot','geo']);
    if(k=='n') l.n=3+(Math.random()*9|0); else if(k=='sp') l.sp=rnd(-1.5,1.5); else if(k=='sz') l.sz=rnd(.15,.5); else if(k=='hue') l.hue=(l.hue+rnd(30,180))%360; else if(k=='mot') l.mot=pick(MOTIONS); else l.geo=pick(GEOS);
    if(Math.random()<.2) P.sym=pick([1,2,3,4,5,6,8,12]); }
  function layer(g,L,S,R){ const {a,t}=S, b=a.bass, m=a.mid, hi=a.high; let s=R*L.sz*(1+b*.5);
    const mt= L.mot=='spin'?t*L.sp: L.mot=='orbit'?Math.sin(t*L.sp)*2: L.mot=='wobble'?Math.sin(t*3)*.3: L.mot=='zoom'?0:t*.2; if(L.mot=='pulse') s*=1+Math.sin(t*4)*.2+b*.4; if(L.mot=='zoom') s*=((t*.3*L.sp)%1+1)%1*1.5+.2;
    g.strokeStyle=hsl(L.hue+t*10,P.sat,55+hi*30,.8); g.fillStyle=hsl(L.hue+60,P.sat,60,.6); g.lineWidth=L.lw*(1+b);
    g.save(); g.rotate(mt);
    switch(L.geo){
      case 'polyrings': for(let r=1;r<=5;r++){ g.beginPath(); for(let i=0;i<=L.n;i++){ const an=i/L.n*TAU+r*.3; g.lineTo(Math.cos(an)*s*r/3,Math.sin(an)*s*r/3);} g.stroke(); } break;
      case 'swarm': for(let i=0;i<120;i++){ const an=i*2.399+t*L.sp*.3, r=Math.sqrt(i/120)*s*1.8*(1+m*.5); g.fillRect(Math.cos(an)*r,Math.sin(an)*r,3+b*5,3+b*5);} break;
      case 'spiro': g.beginPath(); for(let i=0;i<=400;i++){ const u=i/400*TAU*L.n; const r1=s, r2=s*.37*(1+m); g.lineTo(Math.cos(u)*r1*.6+Math.cos(u*(L.n+1)/2+t)*r2, Math.sin(u)*r1*.6+Math.sin(u*(L.n+1)/2+t)*r2);} g.stroke(); break;
      case 'scope': { const w=a.wave; g.beginPath(); for(let i=0;i<=128;i++){ const an=i/128*TAU, v=w?(w[i*8%w.length]-128)/128:Math.sin(i*.3+t*5)*.2; const r=s*(1+v*a.sens*1.5); g.lineTo(Math.cos(an)*r,Math.sin(an)*r);} g.stroke(); } break;
      case 'tunnel': for(let i=0;i<12;i++){ const z=((i/12)+t*.3*(1+b))%1; const r=z*z*R*1.6; g.globalAlpha=1-z*.5; g.beginPath(); for(let k=0;k<=L.n;k++){const an=k/L.n*TAU+z*L.sp*3; g.lineTo(Math.cos(an)*r,Math.sin(an)*r);} g.stroke(); } g.globalAlpha=1; break;
      case 'lissajous': g.beginPath(); for(let i=0;i<=300;i++){ const u=i/300*TAU; g.lineTo(Math.sin(u*L.n+t)*s*1.4, Math.sin(u*(L.n+1)+t*.7)*s*1.4);} g.stroke(); break;
      case 'grid': for(let i=-4;i<=4;i++){ const o=i*s*.35+Math.sin(t*L.sp)*s*.1; g.beginPath(); g.moveTo(o,-R); g.lineTo(o*(1+b),R); g.moveTo(-R,o); g.lineTo(R,o*(1+m)); g.stroke(); } break;
      case 'shards': for(let i=0;i<L.n*2;i++){ const an=i/(L.n*2)*TAU, r=s*(.5+NOISE(i+t)*1.2); g.beginPath(); g.moveTo(0,0); g.lineTo(Math.cos(an)*r,Math.sin(an)*r); g.lineTo(Math.cos(an+.15)*r*.7,Math.sin(an+.15)*r*.7); g.closePath(); g.fill(); } break; }
    g.restore(); }
  return {
    name:'RANDO-TRON', sub:'100% randomized — never the same twice', dur:Infinity, acts:['Generating…','Mutating','Re-rolled','Mutating'], get label(){return P&&P.label;},
    init(S){ roll(); fb=document.createElement('canvas'); fbx=fb.getContext('2d'); bars=0; },
    draw(g,S){ const {W,H,a}=S; if(fb.width!==W||fb.height!==H){fb.width=W;fb.height=H;}
      if(a.beat){ bars+=.25; if(Math.random()<.5) mutate(); S.act=1; }
      if(!a.active && Math.random()<S.dt*.5){ bars+=.25; mutate(); }
      if(bars>=nextRoll || a.drop){ roll(); bars=0; nextRoll=pick([4,8,8,16]); S.flash(.3); }
      // feedback trails: previous frame zoomed/rotated back onto itself
      fbx.clearRect(0,0,W,H); fbx.drawImage(g.canvas,0,0);
      g.fillStyle=P.bg!=null?hsl(P.bg,50,6,1):'#000'; g.fillRect(0,0,W,H);
      g.save(); g.globalAlpha=1-P.trail; g.translate(W/2,H/2); g.rotate(P.fbRot*(1+a.bass)); g.scale(P.fbZoom,P.fbZoom); g.drawImage(fb,-W/2,-H/2); g.restore(); g.globalAlpha=1;
      const R=Math.min(W,H)*.45; g.save(); g.translate(W/2,H/2); g.globalCompositeOperation=P.blend;
      for(let s=0;s<P.sym;s++){ g.save(); g.rotate(s/P.sym*TAU); if(P.mirror&&s%2) g.scale(1,-1); for(const L of P.layers) layer(g,L,S,R); g.restore(); }
      g.restore(); g.globalCompositeOperation='source-over';
      if(P.glitch && (a.beat||Math.random()<.03)){ for(let i=0;i<6;i++){ const y=rnd(H), h=rnd(4,H*.06); g.drawImage(g.canvas,0,y,W,h,rnd(-40,40),y,W,h);} }
    } };
})();
