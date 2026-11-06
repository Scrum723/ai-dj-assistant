// 15 story scenes. Each: name, sub, dur (s), acts[4], init(S), draw(g,S)
// S: {t (scene time), dt, p (story progress 0..1), act (0-3), ap (0..1 within act), a (audio), W, H, flash(x), safe}
const TAU = Math.PI*2, rnd=(a=1,b)=>b===undefined?Math.random()*a:a+Math.random()*(b-a), clamp=(x,a=0,b=1)=>Math.max(a,Math.min(b,x)), lerp=(a,b,t)=>a+(b-a)*t;
const hsl=(h,s,l,a=1)=>`hsla(${h},${s}%,${l}%,${a})`;
const NOISE=(()=>{const p=new Float32Array(512);for(let i=0;i<512;i++)p[i]=Math.random();const f=x=>{const i=Math.floor(x),t=x-i,s=t*t*(3-2*t);return lerp(p[i&511],p[(i+1)&511],s)};return (x,y=0)=>f(x+f(y*1.7)*8);})();
function fade(g,S,a,c='0,0,0'){g.fillStyle=`rgba(${c},${a})`;g.fillRect(0,0,S.W,S.H);}
function bolt(g,x,y,x2,y2,w,d=0){ if(d>5||Math.hypot(x2-x,y2-y)<12){g.lineWidth=w;g.beginPath();g.moveTo(x,y);g.lineTo(x2,y2);g.stroke();return;}
  const mx=(x+x2)/2+rnd(-1,1)*Math.abs(y2-y)*0.25, my=(y+y2)/2; bolt(g,x,y,mx,my,w,d+1); bolt(g,mx,my,x2,y2,w,d+1); if(Math.random()<0.25) bolt(g,mx,my,mx+rnd(-80,80),my+rnd(40,120),w*0.5,d+2); }
function rain(S,n,g,ang=0.2,col='rgba(170,200,255,.5)'){ S.rain=S.rain||Array.from({length:400},()=>[rnd(S.W),rnd(S.H),rnd(.5,1)]);
  g.strokeStyle=col; g.lineWidth=1; g.beginPath(); for(let i=0;i<Math.min(n,400);i++){const r=S.rain[i]; r[1]+=r[2]*S.H*S.dt*1.6; r[0]+=ang*r[2]*S.H*S.dt; if(r[1]>S.H){r[1]=-20;r[0]=rnd(-S.W*.2,S.W);} g.moveTo(r[0],r[1]); g.lineTo(r[0]-ang*18*r[2],r[1]-18*r[2]); } g.stroke(); }
function stars(g,S,n=150,a=1){ S.st=S.st||Array.from({length:200},()=>[rnd(),rnd(),rnd(.3,1.6)]); g.fillStyle='#fff'; for(let i=0;i<n;i++){const s=S.st[i]; g.globalAlpha=a*(0.4+0.6*NOISE(S.t*2+i)); g.fillRect(s[0]*S.W,s[1]*S.H,s[2],s[2]);} g.globalAlpha=1; }
function ghost(g,x,y,s,a,t){ g.save(); g.translate(x,y); g.globalAlpha=a; g.fillStyle='rgba(220,240,255,.8)'; g.beginPath(); g.arc(0,0,s,Math.PI,0); for(let i=0;i<=6;i++){ g.lineTo(s-i*s/3, s*1.6+Math.sin(t*6+i)*s*.15*(i%2?1:-1)); } g.closePath(); g.fill();
  g.fillStyle='#000'; g.beginPath(); g.ellipse(-s*.35,-s*.1,s*.15,s*.25,0,0,TAU); g.ellipse(s*.35,-s*.1,s*.15,s*.25,0,0,TAU); g.fill(); g.restore(); }

const SCENES = [
{ name:'Supercell Birth', sub:'clear sky → towering cumulus → mesocyclone → tornado → rainbow', dur:240, acts:['Clear Sky','Towering Cumulus','The Mesocyclone','Rainbow Clearing'],
  init(S){ S.cl=Array.from({length:60},()=>({x:rnd(-.2,1.2),y:rnd(.15,.55),r:rnd(.03,.09),v:rnd(.5,1.5)})); S.ang=0; S.bolts=[]; },
  draw(g,S){ const {W,H,a,act,ap}=S, dark = act==0?ap*.3:act==1?.3+ap*.5:act==2?.85:lerp(.85,0,clamp(ap*2));
    const sky=g.createLinearGradient(0,0,0,H); sky.addColorStop(0,hsl(lerp(205,260,dark),60,lerp(55,8,dark))); sky.addColorStop(1,hsl(lerp(40,150,dark),50,lerp(75,15,dark))); g.fillStyle=sky; g.fillRect(0,0,W,H);
    const grow = act==0?ap*.4:act==1?.4+ap*.6:1; S.ang += S.dt*(act>=2&&act<3?0.6+a.bass*2:0.05);
    const cx=W*.5, base=H*.42;
    for(const c of S.cl){ const r=c.r*W*(0.6+grow*1.4)*(1+a.bass*.15); let x=c.x*W, y=c.y*H;
      if(act>=1){ y=lerp(y, base-(1-c.y)*H*grow*1.2, grow*.6); x=lerp(x, cx+(c.x-.5)*W*.8, grow*.4); }
      if(act==2){ const dx=x-cx, ang=S.ang*c.v*.4; x=cx+dx*Math.cos(ang); y += Math.sin(ang)*20; }
      const l=lerp(97,22,dark); g.fillStyle=hsl(act==2?260+a.mid*60:220,act==2?40:10,l,.55); g.beginPath(); g.arc(x,y,r,0,TAU); g.fill(); }
    if(act==2){ // funnel
      const top=base, len=(H-top)*clamp(ap*2.5); g.fillStyle=`rgba(40,30,60,.85)`;
      for(let i=0;i<40;i++){ const f=i/40, y=top+f*len, w=lerp(W*.12,W*.012,Math.pow(f,.6))*(1+a.bass*.4), x=cx+Math.sin(S.t*2+f*6)*W*.05*f+Math.sin(S.ang*3+f*20)*w*.3;
        g.beginPath(); g.ellipse(x,y,w,8,0,0,TAU); g.fill(); }
      rain(S,200+a.high*200,g,-.4,'rgba(160,140,220,.4)');
      if(a.beat && Math.random()<.5){ S.bolts.push({x:rnd(W),l:1}); S.flash(.6);} }
    if(act==1) rain(S,ap*150,g,.1);
    g.strokeStyle='#e8f0ff'; S.bolts=S.bolts.filter(b=>(b.l-=S.dt*4)>0); for(const b of S.bolts){ g.globalAlpha=b.l; g.shadowBlur=20; g.shadowColor='#a8f'; bolt(g,b.x,0,b.x+rnd(-200,200),H*.9,3); } g.globalAlpha=1; g.shadowBlur=0;
    if(act==3){ const ra=clamp(ap*2)*(0.6+a.mid*.4); for(let i=0;i<7;i++){ g.strokeStyle=hsl(i*45,90,60,ra*.5); g.lineWidth=H*.018; g.beginPath(); g.arc(W*.5,H*1.1,H*.75-i*H*.018,Math.PI,TAU); g.stroke(); } }
    g.fillStyle=hsl(100,30,lerp(30,5,dark)); g.fillRect(0,H*.92,W,H*.08); } },

{ name:'The Haunted Cellar Wakes', sub:'a creak, a candle, a hundred eyes — then the door gives', dur:150, acts:['Dust & Silence','Candles Kindle','The Door Breaks','Settling Dust'],
  init(S){ S.eyes=Array.from({length:60},()=>({x:rnd(),y:rnd(.1,.85),o:0,b:rnd(TAU)})); S.gh=[]; S.dust=Array.from({length:150},()=>[rnd(),rnd(),rnd(.2,1)]); },
  draw(g,S){ const {W,H,a,act,ap}=S; fade(g,S,.35,'8,4,10');
    // perspective cellar walls
    const vx=W*.5, vy=H*.45, dw=W*.12*(1+(act==2?ap*3:0)), dh=H*.25*(1+(act==2?ap*3:0));
    g.strokeStyle='rgba(120,80,60,.35)'; g.lineWidth=2; for(let i=0;i<12;i++){ const f=i/12; g.beginPath(); g.moveTo(0,H*f); g.lineTo(vx-dw,vy-dh+f*2*dh); g.moveTo(W,H*f); g.lineTo(vx+dw,vy-dh+f*2*dh); g.stroke(); }
    const open = act<2?a.bass*.05:act==2?clamp(ap*3):1-ap; const glow=g.createRadialGradient(vx,vy,0,vx,vy,dh*2);
    glow.addColorStop(0,hsl(25,100,60,open*.9)); glow.addColorStop(1,'rgba(0,0,0,0)'); g.fillStyle=glow; g.fillRect(0,0,W,H);
    g.fillStyle='#1a0e08'; g.fillRect(vx-dw,vy-dh,dw*2*(1-open),dh*2); g.strokeStyle='#4a2a15'; g.strokeRect(vx-dw,vy-dh,dw*2,dh*2);
    const nc = act==0?0:act==1?Math.floor(ap*8)+1:8; for(let i=0;i<nc;i++){ const x=W*(.1+i*.115), y=H*.82, fl=NOISE(S.t*8+i*9)*.6+a.high*.6;
      g.fillStyle='#d8c8a0'; g.fillRect(x-6,y,12,40); const fg=g.createRadialGradient(x,y-10,0,x,y-10,60+fl*60); fg.addColorStop(0,'rgba(255,200,90,.9)'); fg.addColorStop(1,'rgba(255,120,0,0)'); g.fillStyle=fg; g.beginPath(); g.arc(x,y-10,60+fl*60,0,TAU); g.fill(); }
    if(act>=1){ if(a.beat) for(let k=0;k<3;k++){ const e=S.eyes[(Math.random()*60)|0]; e.o=1; } for(const e of S.eyes){ e.o*=0.985; if(e.o<.05) continue; const x=e.x*W,y=e.y*H, bl=Math.sin(S.t*3+e.b)>.95?.1:1;
      g.fillStyle=hsl(act==2?0:55,100,55,e.o); g.beginPath(); g.ellipse(x-9,y,6,4*bl,0,0,TAU); g.ellipse(x+9,y,6,4*bl,0,0,TAU); g.fill(); } }
    if(act==2 && (a.beat||Math.random()<.05)) S.gh.push({x:vx,y:vy,vx:rnd(-1,1)*W*.4,vy:rnd(-.6,.3)*H*.4,s:5,l:1});
    S.gh=S.gh.filter(o=>(o.l-=S.dt*.4)>0).slice(-40); for(const o of S.gh){ o.x+=o.vx*S.dt; o.y+=o.vy*S.dt; o.s+=S.dt*60; ghost(g,o.x,o.y,o.s,o.l*.7,S.t); }
    if(a.drop) S.flash(.5);
    g.fillStyle='rgba(255,230,200,.5)'; for(const d of S.dust){ d[1]+=S.dt*.01*d[2]; d[0]+=Math.sin(S.t+d[2]*9)*.0005; if(d[1]>1)d[1]=0; g.fillRect(d[0]*W,d[1]*H,d[2]*2,d[2]*2);} } },

{ name:'Lighthouse in the Gale', sub:'one keeper, one beam, a sea that will not sleep', dur:180, acts:['Dusk Swell','Gale Rising','Rogue Wave','The Light Holds'],
  init(S){ S.beam=0; },
  draw(g,S){ const {W,H,a,act,ap}=S, storm = act==0?ap*.3:act==1?.3+ap*.5:act==2?1:1-ap*.7;
    const sky=g.createLinearGradient(0,0,0,H); sky.addColorStop(0,hsl(230,40,lerp(18,4,storm))); sky.addColorStop(1,hsl(260,30,lerp(30,8,storm))); g.fillStyle=sky; g.fillRect(0,0,W,H);
    const lx=W*.72, ly=H*.38; S.beam += S.dt*(0.6+(a.bpm?a.bpm/120:1)*storm*1.2);
    const bx=Math.cos(S.beam), bw=.08; g.save(); g.globalCompositeOperation='lighter';
    const bg=g.createLinearGradient(lx,ly,lx+bx*W,ly); bg.addColorStop(0,'rgba(255,240,180,.7)'); bg.addColorStop(1,'rgba(255,240,180,0)'); g.fillStyle=bg;
    g.beginPath(); g.moveTo(lx,ly); g.lineTo(lx+bx*W*1.2,ly-H*bw*(1+Math.abs(bx)*3)); g.lineTo(lx+bx*W*1.2,ly+H*bw*(1+Math.abs(bx)*3)); g.fill(); g.restore();
    // tower
    g.fillStyle='#ddd'; g.beginPath(); g.moveTo(lx-W*.025,H*.75); g.lineTo(lx-W*.015,ly); g.lineTo(lx+W*.015,ly); g.lineTo(lx+W*.025,H*.75); g.fill();
    g.fillStyle='#b22'; for(let i=0;i<3;i++) g.fillRect(lx-W*.022+i*.002*W, ly+(i*2+1)*H*.055, W*.044-i*.004*W, H*.04);
    g.fillStyle=hsl(50,100,70+a.high*30); g.beginPath(); g.arc(lx,ly-8,10+a.bass*10,0,TAU); g.fill();
    // sea: stacked waveform lines from analyser
    const wv=a.wave, rows=18; for(let r=0;r<rows;r++){ const f=r/rows, y0=H*(.68+f*.36), amp=H*(.02+storm*.08+(act==2?ap*.15:0))*(1-f*.5);
      g.beginPath(); g.moveTo(0,H); for(let x=0;x<=W;x+=W/64){ const wi=wv?((wv[((x/W*1000+r*37)|0)%wv.length]-128)/128):0; g.lineTo(x, y0+Math.sin(x/W*8+S.t*(1+storm*2)+r)*amp+wi*amp*a.sens); } g.lineTo(W,H);
      g.fillStyle=hsl(200+storm*30,50,8+f*10,.85); g.fill(); g.strokeStyle=hsl(190,70,60+a.high*30,.35); g.stroke(); }
    rain(S,storm*350,g,.6); if(storm>.6&&a.beat&&Math.random()<.3){ S.flash(.4); g.strokeStyle='#fff'; bolt(g,rnd(W*.4),0,rnd(W*.5),H*.6,2);} } },

{ name:'Antarctic Aurora Walk', sub:'boots on the ice, the sky catches fire, then the ground lets go', dur:300, acts:['Ice Field','Curtains Unfurl','Aurora Storm','Liftoff'],
  init(S){ S.walk=0; },
  draw(g,S){ const {W,H,a,act,ap}=S, inten= act==0?.25+ap*.3:act==1?.55+ap*.45:1, lift= act==3?ap:0; fade(g,S,.2,'2,4,14'); stars(g,S,180,1-inten*.3);
    S.walk+=S.dt*(.3+a.bass*.3)*(1-lift);
    g.save(); g.translate(0,lift*H*.9); g.globalCompositeOperation='lighter'; const fr=a.freq;
    for(let c=0;c<4;c++){ const hue=[130,160,280,320][c]+Math.sin(S.t*.1)*20, yb=H*(.12+c*.07);
      for(let x=0;x<W;x+=6){ const i=x/W, f=fr?fr[(4+i*120)|0]/255:.3, n=NOISE(i*3+S.t*.15*(c+1),c*5);
        const y=yb+(n-.5)*H*.3, h=H*(.1+.35*inten*(.4+f*a.sens*.8)*(act==2&&a.beat?1.3:1));
        const gr=g.createLinearGradient(0,y,0,y+h); gr.addColorStop(0,hsl(hue,90,60,0)); gr.addColorStop(.3,hsl(hue,90,60,.09*inten)); gr.addColorStop(1,hsl(hue+30,90,50,0)); g.fillStyle=gr; g.fillRect(x,y,6,h); } }
    g.globalCompositeOperation='source-over';
    // ice sheet with perspective stripes scrolling toward viewer + aurora reflection
    const hz=H*.62; const ig=g.createLinearGradient(0,hz,0,H); ig.addColorStop(0,'#1b2a44'); ig.addColorStop(1,'#cfe3f5'); g.fillStyle=ig; g.fillRect(0,hz,W,H-hz);
    g.strokeStyle='rgba(120,255,200,.25)'; for(let i=0;i<14;i++){ const z=((i/14)+S.walk*.1)%1, y=hz+z*z*(H-hz); g.lineWidth=1+z*2; g.beginPath(); g.moveTo(0,y); g.lineTo(W,y); g.stroke(); }
    g.fillStyle='#e8f2ff'; g.beginPath(); g.moveTo(0,hz); for(let x=0;x<=W;x+=W/24) g.lineTo(x,hz-NOISE(x/W*5+9)*H*.07); g.lineTo(W,hz); g.fill();
    // walker silhouette
    const wx=W*.5, wy=H*.9, st=Math.sin(S.walk*6); g.fillStyle='#05070d'; g.beginPath(); g.ellipse(wx,wy-H*.13,H*.035,H*.06,0,0,TAU); g.arc(wx,wy-H*.21,H*.022,0,TAU); g.fill();
    g.lineWidth=H*.016; g.strokeStyle='#05070d'; g.beginPath(); g.moveTo(wx,wy-H*.08); g.lineTo(wx+st*H*.03,wy); g.moveTo(wx,wy-H*.08); g.lineTo(wx-st*H*.03,wy); g.stroke();
    g.restore(); if(lift){ stars(g,S,200,lift); } } },

{ name:'Graveyard Bloom', sub:'every stone grows a flower when the bass hits the dirt', dur:90, acts:['Night Soil','Sprouting','Full Bloom','Petal Fall'],
  init(S){ S.stones=Array.from({length:9},(_,i)=>({x:(i+.5)/9+rnd(-.03,.03),h:rnd(.1,.18),gr:0})); S.pet=[]; },
  draw(g,S){ const {W,H,a,act,ap}=S; g.fillStyle='#07040c'; g.fillRect(0,0,W,H); stars(g,S,80);
    g.fillStyle='#eee8c8'; g.beginPath(); g.arc(W*.85,H*.18,H*.07,0,TAU); g.fill();
    const target= act==0?0:act==1?ap*.7:act==2?1:1; const br=(x,y,len,ang,d,hue)=>{ if(d==0||len<3) { g.fillStyle=hsl(hue+d*20,90,60+a.high*20,.9); g.beginPath(); g.arc(x,y,3+a.bass*6,0,TAU); g.fill(); return;}
      const x2=x+Math.cos(ang)*len, y2=y+Math.sin(ang)*len; g.strokeStyle=hsl(hue,60,30+d*6); g.lineWidth=d*.8; g.beginPath(); g.moveTo(x,y); g.lineTo(x2,y2); g.stroke();
      const sp=.45+a.mid*.4+Math.sin(S.t+d)*.08; br(x2,y2,len*.72,ang-sp,d-1,hue+15); br(x2,y2,len*.72,ang+sp,d-1,hue+15); };
    S.stones.forEach((s,i)=>{ if(a.beat && act>=1) s.gr=Math.min(target, s.gr+.08); s.gr+=(target-s.gr)*S.dt*.3; const x=s.x*W, y=H*.82;
      if(s.gr>.02) br(x,y-s.h*H,H*.12*s.gr,-Math.PI/2+Math.sin(S.t*.7+i)*.1,Math.min(8,2+Math.floor(s.gr*7)),(i*40+S.t*10)%360);
      g.fillStyle='#4a4a55'; g.beginPath(); g.moveTo(x-W*.03,y); g.lineTo(x-W*.03,y-s.h*H*.8); g.arc(x,y-s.h*H*.8,W*.03,Math.PI,0); g.lineTo(x+W*.03,y); g.fill();
      g.fillStyle='#222'; g.fillRect(x-W*.012,y-s.h*H*.75,W*.024,3); g.fillRect(x-1.5,y-s.h*H*.88,3,s.h*H*.25); });
    if(act==3){ if(Math.random()<.5) S.pet.push([rnd(W),-10,rnd(360)]); }
    S.pet=S.pet.slice(-200); for(const p of S.pet){ p[1]+=S.dt*60; p[0]+=Math.sin(S.t+p[2])*1; g.fillStyle=hsl(p[2],80,70); g.fillRect(p[0],p[1],5,3);} g.fillStyle='#120818'; g.fillRect(0,H*.82,W,H*.18); } },

{ name:'Mammatus Dreams', sub:'the underbelly of the storm, breathing like a living thing', dur:120, acts:['Pouches Form','Breathing','Psychedelic Swell','Golden Hour'],
  init(S){},
  draw(g,S){ const {W,H,a,act,ap}=S, psy= act<2?0:act==2?ap:1-ap, gold= act==3?ap:0;
    g.fillStyle=hsl(lerp(25,280,psy),40,8+gold*20); g.fillRect(0,0,W,H);
    const cols=14, rows=8, cw=W/cols, ch=H*.7/rows; for(let r=0;r<rows;r++) for(let c=-1;c<=cols;c++){ const off=r%2?cw/2:0, x=c*cw+off, y=r*ch;
      const br= act==0?ap:1, s=cw*.65*(.6+.5*br+.25*Math.sin(S.t*1.5+r*.7+c*.4)*(act>=1?1:0)+a.bass*.2);
      const gr=g.createRadialGradient(x,y+s*.6,0,x,y,s); const h=lerp(30,(r*30+c*12+S.t*40)%360,psy);
      gr.addColorStop(0,hsl(h,lerp(30,90,psy+gold),lerp(70,60,psy)+a.high*15)); gr.addColorStop(.7,hsl(h+40,lerp(20,80,psy),lerp(30,30,psy))); gr.addColorStop(1,'rgba(0,0,0,0)');
      g.fillStyle=gr; g.beginPath(); g.ellipse(x,y,s*.9,s,0,0,TAU); g.fill(); }
    if(gold){ const sg=g.createLinearGradient(0,H*.6,0,H); sg.addColorStop(0,'rgba(255,170,60,0)'); sg.addColorStop(1,`rgba(255,170,60,${gold*.6})`); g.fillStyle=sg; g.fillRect(0,0,W,H);} } },

{ name:'Planchette Séance', sub:'the board spells what the storm wants to say', dur:75, acts:['Hands on the Board','It Moves','The Message','Goodbye'],
  init(S){ S.msg='THE STORM IS HERE'; S.idx=0; S.px=.5; S.py=.6; S.spelled=''; },
  draw(g,S){ const {W,H,a,act,ap}=S; fade(g,S,.25,'10,5,2');
    const bw=W*.8, bh=H*.6, bx=W*.1, by=H*.18; g.fillStyle=hsl(30,40,25+a.bass*10); g.fillRect(bx,by,bw,bh); g.strokeStyle='#c9a060'; g.lineWidth=3; g.strokeRect(bx+8,by+8,bw-16,bh-16);
    const L='ABCDEFGHIJKLMNOPQRSTUVWXYZ', pos={}; g.font=`${H*.04}px Georgia`; g.textAlign='center'; g.textBaseline='middle';
    for(let i=0;i<26;i++){ const row=i<13?0:1, k=row?i-13:i, ang=Math.PI*(1.15+.7*k/12), r=bh*(row?.42:.62), x=W/2+Math.cos(ang)*r*1.3, y=by+bh*.85+Math.sin(ang)*r;
      pos[L[i]]=[x,y]; const lit = S.cur===L[i]; g.fillStyle= lit?hsl(40,100,70):'#2a1608'; if(lit){g.shadowBlur=30;g.shadowColor='#fa0';} g.fillText(L[i],x,y); g.shadowBlur=0; }
    pos[' ']=[W/2,by+bh*.9]; g.fillStyle='#2a1608'; g.fillText('YES',bx+bw*.12,by+bh*.12); g.fillText('NO',bx+bw*.88,by+bh*.12); g.fillText('GOOD BYE',W/2,by+bh*.95); pos['YES']=[bx+bw*.12,by+bh*.12]; pos['BYE']=[W/2,by+bh*.95];
    let tgt; if(act==0) tgt=[W/2+Math.sin(S.t*3)*10, by+bh*.6]; else if(act==1) tgt=[W/2+NOISE(S.t*.8)*bw*.6-bw*.3, by+bh*(.3+NOISE(S.t*.8,3)*.5)];
    else if(act==2){ if(a.beat && S.idx<S.msg.length){ S.cur=S.msg[S.idx++]; S.spelled+=S.cur; if(S.idx==S.msg.length) S.flash(.4);} tgt=pos[S.cur||'YES']; } else tgt=pos['BYE'];
    S.px=lerp(S.px,tgt[0],S.dt*4); S.py=lerp(S.py,tgt[1],S.dt*4);
    g.save(); g.translate(S.px,S.py); g.fillStyle='rgba(240,220,180,.9)'; g.beginPath(); g.moveTo(0,-H*.09); g.bezierCurveTo(H*.08,-H*.05,H*.07,H*.06,0,H*.06); g.bezierCurveTo(-H*.07,H*.06,-H*.08,-H*.05,0,-H*.09); g.fill();
    g.globalCompositeOperation='destination-out'; g.beginPath(); g.arc(0,-H*.02,H*.025,0,TAU); g.fill(); g.restore();
    g.fillStyle=hsl(10,100,60,.9); g.font=`bold ${H*.05}px Georgia`; g.fillText(S.spelled,W/2,H*.9);
    for(let i=0;i<3;i++){ g.fillStyle=`rgba(255,180,80,${.06+a.high*.1})`; g.beginPath(); g.arc(W*(.08+i*.42),H*.1,90+NOISE(S.t*5+i)*40,0,TAU); g.fill(); } } },

{ name:'The Thirteenth Chime', sub:'the clock tower strikes an hour that does not exist', dur:45, acts:['Ticking','Hands Race','THIRTEEN','Silence'],
  init(S){ S.ch=0; S.rings=[]; S.h=0; },
  draw(g,S){ const {W,H,a,act,ap}=S; fade(g,S,act==2?.12:.3,'5,3,12'); const cx=W/2, cy=H*.45, R=H*.3;
    g.fillStyle='#14101e'; g.fillRect(cx-R*1.3,cy-R*1.3,R*2.6,H); g.beginPath(); g.moveTo(cx-R*1.5,cy-R*1.3); g.lineTo(cx,cy-R*2.4); g.lineTo(cx+R*1.5,cy-R*1.3); g.fill();
    g.fillStyle=hsl(45,60,lerp(70,85,a.bass)); g.beginPath(); g.arc(cx,cy,R,0,TAU); g.fill(); g.strokeStyle='#222'; g.lineWidth=4; g.stroke();
    g.fillStyle='#222'; g.font=`${R*.16}px Georgia`; g.textAlign='center'; g.textBaseline='middle'; const nums=act>=2?13:12;
    for(let i=1;i<=nums;i++){ const an=i/nums*TAU-Math.PI/2; g.fillStyle= i==13?'#c00':'#222'; g.fillText(['I','II','III','IV','V','VI','VII','VIII','IX','X','XI','XII','XIII'][i-1],cx+Math.cos(an)*R*.8,cy+Math.sin(an)*R*.8); }
    const sp= act==0?.05:act==1?ap*4:act==2?.2:0; S.h += S.dt*sp + (a.beat&&act==1?.05:0);
    const hand=(ang,len,w)=>{ g.lineWidth=w; g.beginPath(); g.moveTo(cx,cy); g.lineTo(cx+Math.cos(ang-Math.PI/2)*len,cy+Math.sin(ang-Math.PI/2)*len); g.stroke(); };
    g.strokeStyle='#111'; hand(act>=2?0:S.h,R*.5,8); hand(act>=2?0:S.h*12,R*.75,4);
    if(act==2 && a.beat && S.ch<13){ S.ch++; S.rings.push({r:R,l:1}); S.flash(.25); }
    if(act==2 && !a.active && S.ch<Math.floor(ap*13)) { S.ch++; S.rings.push({r:R,l:1}); }
    S.rings=S.rings.filter(o=>(o.l-=S.dt*.5)>0); for(const o of S.rings){ o.r+=S.dt*W*.4; g.strokeStyle=hsl(0,90,60,o.l); g.lineWidth=6*o.l; g.beginPath(); g.arc(cx,cy,o.r,0,TAU); g.stroke(); }
    g.fillStyle='#c00'; g.font=`bold ${H*.06}px Georgia`; if(S.ch) g.fillText('✦ '.repeat(S.ch).trim(),cx,H*.9);
    if(act==3){ g.fillStyle=`rgba(0,0,0,${ap})`; g.fillRect(0,0,W,H);} } },

{ name:'Barometer Drop', sub:'the needle falls, the isobars tighten, the wind arrives', dur:60, acts:['Fair','Change','STORMY','Recovery'],
  init(S){ S.pts=Array.from({length:1500},()=>[rnd(),rnd()]); S.press=1030; },
  draw(g,S){ const {W,H,a,act,ap}=S, tight = act==0?.2:act==1?.2+ap*.6:act==2?1:1-ap*.8; fade(g,S,.08,'4,8,16');
    const tgt= act==0?1030:act==1?lerp(1030,975,ap):act==2?960-a.bass*10:lerp(960,1013,ap); S.press=lerp(S.press,tgt,S.dt*2);
    g.lineWidth=1.2; const cx=.5+Math.sin(S.t*.2)*.15, cy=.5;
    for(const p of S.pts){ const dx=p[0]-cx, dy=p[1]-cy, d=Math.hypot(dx,dy)+.01, sp=(.02+tight*.12*(1+a.bass))*S.dt*(1/(d*3+.3));
      const ox=p[0]*W, oy=p[1]*H; p[0]+= -dy/d*sp + dx*sp*.05*(act==2?-1:1); p[1]+= dx/d*sp + dy*sp*.05*(act==2?-1:1);
      if(p[0]<0||p[0]>1||p[1]<0||p[1]>1||d<.02){ p[0]=rnd(); p[1]=rnd(); continue; }
      g.strokeStyle=hsl(lerp(200,330,tight)+d*120,80,50+a.high*30,.5); g.beginPath(); g.moveTo(ox,oy); g.lineTo(p[0]*W,p[1]*H); g.stroke(); }
    // dial
    const dx=W*.84, dy=H*.78, R=H*.16; g.fillStyle='rgba(240,230,200,.92)'; g.beginPath(); g.arc(dx,dy,R,0,TAU); g.fill(); g.strokeStyle='#432'; g.lineWidth=5; g.stroke();
    g.fillStyle='#432'; g.font=`${R*.16}px Georgia`; g.textAlign='center'; ['STORMY','RAIN','CHANGE','FAIR','DRY'].forEach((w,i)=>{ const an=Math.PI*(.75+i*.375); g.fillText(w,dx+Math.cos(an)*R*.68,dy+Math.sin(an)*R*.68); });
    const na=Math.PI*(.75+(S.press-950)/(1050-950)*1.5); g.strokeStyle='#a00'; g.lineWidth=3; g.beginPath(); g.moveTo(dx,dy); g.lineTo(dx+Math.cos(na)*R*.9,dy+Math.sin(na)*R*.9); g.stroke();
    g.fillStyle='#222'; g.fillText(S.press.toFixed(0)+' hPa',dx,dy+R*.45); if(act==2&&a.beat) S.flash(.15); } },

{ name:'Into the Hurricane Eye', sub:'through the eyewall, into the stadium of calm', dur:270, acts:['Outer Bands','The Eyewall','Inside the Eye','Back Out to Sea'],
  init(S){ S.hp=Array.from({length:1200},()=>({r:rnd(.05,1),a:rnd(TAU),z:rnd(.5,1.5)})); S.zoom=.4; },
  draw(g,S){ const {W,H,a,act,ap}=S; fade(g,S,act==2?.25:.12,'2,6,12'); const cx=W/2, cy=H/2, M=Math.max(W,H)*.7;
    const zt= act==0?.4+ap*.6:act==1?1+ap*.8:act==2?1.8:1.8-ap*1.4; S.zoom=lerp(S.zoom,zt,S.dt);
    const eye=.12*S.zoom, calm= act==2?1:0;
    for(const p of S.hp){ const w=(.6/(p.r+.15))*(1-calm*.7)*(1+a.bass*.8); p.a+=S.dt*w*.5; const rr=p.r*S.zoom;
      if(rr<eye*.9 && act==2) continue; const sp=rr+Math.sin(p.a*2+p.r*10)*.01, arm=Math.sin(p.a*2-Math.log(p.r)*6)*.5+.5;
      const x=cx+Math.cos(p.a)*sp*M, y=cy+Math.sin(p.a)*sp*M*.8; if(x<0||x>W||y<0||y>H) continue;
      const a0=p.a-.12-w*.05; g.strokeStyle=hsl(190+arm*60+(act==2?100:0),60,40+arm*40+a.high*20,.6); g.lineWidth=1+p.z+arm*2; g.beginPath(); g.moveTo(cx+Math.cos(a0)*sp*M,cy+Math.sin(a0)*sp*M*.8); g.lineTo(x,y); g.stroke(); }
    if(act==2){ const er=eye*M; const eg=g.createRadialGradient(cx,cy,0,cx,cy,er); eg.addColorStop(0,hsl(200,70,70,.35+a.mid*.3)); eg.addColorStop(1,'rgba(0,0,0,0)'); g.fillStyle=eg; g.beginPath(); g.arc(cx,cy,er,0,TAU); g.fill(); stars(g,S,60,ap); }
    if(act==1 && a.beat) S.flash(.2); } },

{ name:'The Lantern Deacon Procession', sub:'a line of lights walks the road to the old chapel', dur:165, acts:['First Lantern','The Line Forms','At the Chapel Door','Embers'],
  init(S){ S.ls=[]; S.walk=0; },
  draw(g,S){ const {W,H,a,act,ap}=S; const sky=g.createLinearGradient(0,0,0,H); sky.addColorStop(0,'#05030c'); sky.addColorStop(1,'#1d0f22'); g.fillStyle=sky; g.fillRect(0,0,W,H); stars(g,S,100,.6);
    const hx=W*.5, hy=H*.42; // chapel at horizon
    g.fillStyle='#000'; g.fillRect(hx-W*.04,hy-H*.1,W*.08,H*.1); g.beginPath(); g.moveTo(hx-W*.05,hy-H*.1); g.lineTo(hx,hy-H*.2); g.lineTo(hx+W*.05,hy-H*.1); g.fill(); g.fillRect(hx-3,hy-H*.25,6,H*.06);
    const dw=act>=2?(act==2?ap:1):0; g.fillStyle=hsl(35,100,60,dw*(.5+a.bass*.5)); g.fillRect(hx-W*.01,hy-H*.05,W*.02,H*.05);
    g.fillStyle='#0c0810'; g.beginPath(); g.moveTo(hx-8,hy); g.lineTo(hx+8,hy); g.lineTo(W*.9,H); g.lineTo(W*.1,H); g.fill();
    for(let s=-1;s<=1;s+=2) for(let i=0;i<10;i++){ const f=(i/10+S.walk*.2)%1, z=f*f, x=hx+s*(20+z*W*.5), y=hy+z*(H-hy); g.strokeStyle='#000'; g.lineWidth=2+z*12; g.beginPath(); g.moveTo(x,y); g.lineTo(x+s*z*30,y-z*H*.35); g.stroke(); }
    const want = act==0?1:act==1?1+Math.floor(ap*24):act==2?25:Math.floor(25*(1-ap));
    if(a.beat && S.ls.length<want) S.ls.push({f:0,h:rnd(20,50)}); if(S.ls.length<want && Math.random()<S.dt*.5) S.ls.push({f:0,h:rnd(20,50)});
    while(S.ls.length>want) S.ls.shift();
    S.walk += S.dt*(.02+a.bass*.03); g.save(); g.globalCompositeOperation='lighter';
    S.ls.forEach((l,i)=>{ l.f=Math.min(1, l.f+S.dt*(.012+a.level*.02)*(act==2?0:1)); const z=Math.pow(1-l.f*.85-i*.0,2), x=hx+Math.sin(i*2.4)*z*W*.12, y=hy+z*(H-hy)*.9, sz=4+z*40, bob=Math.sin(S.t*3+i)*sz*.1;
      const lg=g.createRadialGradient(x,y+bob,0,x,y+bob,sz*(2+a.high)); lg.addColorStop(0,hsl(l.h,100,65,.9)); lg.addColorStop(1,'rgba(0,0,0,0)'); g.fillStyle=lg; g.beginPath(); g.arc(x,y+bob,sz*(2+a.high),0,TAU); g.fill();
      g.globalCompositeOperation='source-over'; g.fillStyle='#000'; g.fillRect(x-sz*.4,y+bob+sz*.4,sz*.8,sz*2.4); g.beginPath(); g.moveTo(x-sz*.5,y+sz*.5); g.lineTo(x,y-sz*.3); g.lineTo(x+sz*.5,y+sz*.5); g.fill(); g.globalCompositeOperation='lighter'; });
    g.restore(); } },

{ name:'Static Child', sub:'something is trying to come through the television', dur:105, acts:['Dead Channel','Interference','SIGNAL','Test Pattern'],
  init(S){ S.cv=document.createElement('canvas'); S.cv.width=200; S.cv.height=112; S.cx=S.cv.getContext('2d'); S.id=S.cx.createImageData(200,112); },
  draw(g,S){ const {W,H,a,act,ap}=S, sig= act==0?0:act==1?ap*.5:act==2?.6+.4*Math.sin(S.t*.5)**2:1-ap*.5, d=S.id.data, wv=a.wave;
    for(let y=0;y<112;y++){ const roll=Math.sin(y*.2+S.t*10)*a.bass*20; for(let x=0;x<200;x++){ const i=(y*200+x)*4, n=Math.random()*255;
      const dx=(x-100)/100, dy=(y-56)/56, r=Math.hypot(dx*1.6,dy);
      // ghost face: head oval, eyes, mouth driven by waveform
      let f= r<.75?150:0; if(Math.hypot(dx*1.6+.28,dy+.15)<.12||Math.hypot(dx*1.6-.28,dy+.15)<.12) f=0; const wi=wv?(wv[(x*5)%wv.length]-128)/128:0; if(Math.abs(dy-.35-wi*.3*a.sens)<.05 && Math.abs(dx)<.35) f=0;
      if(act==3){ f= [255,220,100,0,180,60,30][Math.floor(x/200*7)]; }
      const v=lerp(n,f+roll,sig); d[i]=v*(act==3?(x<100?1:.6):1); d[i+1]=v; d[i+2]=v*(act==2?1.2:1)+(act==3?(x%60)*2:0); d[i+3]=255; } }
    S.cx.putImageData(S.id,0,0); g.imageSmoothingEnabled=false; const jit= a.beat?rnd(-20,20):0; g.drawImage(S.cv,jit,0,W,H); g.imageSmoothingEnabled=true;
    g.fillStyle='rgba(0,0,0,.25)'; for(let y=0;y<H;y+=4) g.fillRect(0,y,W,2);
    const vg=g.createRadialGradient(W/2,H/2,H*.3,W/2,H/2,W*.7); vg.addColorStop(0,'rgba(0,0,0,0)'); vg.addColorStop(1,'rgba(0,0,0,.9)'); g.fillStyle=vg; g.fillRect(0,0,W,H);
    g.fillStyle='#0f0'; g.font=`${H*.04}px monospace`; g.fillText(act==0?'CH 13':act==1?'CH 13  ▒▒ SEARCHING':act==2?'CH 13  SIGNAL ACQUIRED':'CH 13  PLEASE STAND BY',W*.06,H*.1); if(a.drop) S.flash(.4); } },

{ name:'Ocean Theremin Tide', sub:'the sea plays itself under a moon that listens', dur:210, acts:['Low Tide','Moonrise','Spring Tide','Ebb'],
  init(S){ S.hist=[]; },
  draw(g,S){ const {W,H,a,act,ap}=S; g.fillStyle='#000'; g.fillRect(0,0,W,H); const fr=a.freq, row=new Float32Array(64);
    for(let i=0;i<64;i++){ const v=fr?fr[(2+Math.pow(i/64,1.6)*300)|0]/255*a.sens:.2+.2*NOISE(i*.3+S.t); row[i]=v; } S.hist.unshift(row); if(S.hist.length>48) S.hist.pop();
    const my= act==0?H*.6:act==1?lerp(H*.6,H*.2,ap):act==2?H*.2:lerp(H*.2,H*.5,ap); const mg=g.createRadialGradient(W*.5,my,0,W*.5,my,H*.12+a.bass*40);
    mg.addColorStop(0,'#fff8e0'); mg.addColorStop(.5,'rgba(255,240,200,.6)'); mg.addColorStop(1,'rgba(0,0,0,0)'); g.fillStyle=mg; g.beginPath(); g.arc(W*.5,my,H*.12+a.bass*40,0,TAU); g.fill();
    const amp= act==0?.4:act==1?.4+ap*.6:act==2?1.6:1.6-ap*1.2; const tide=act==2?ap:0;
    for(let r=S.hist.length-1;r>=0;r--){ const z=r/48, y0=lerp(H*.98,H*.42,z)-tide*H*.05, sc=lerp(1,.35,z); g.beginPath();
      for(let i=0;i<64;i++){ const x=W/2+(i/63-.5)*W*1.3*sc, y=y0-S.hist[r][i]*H*.22*amp*sc*(.4+.6*Math.sin(i/63*Math.PI)); i?g.lineTo(x,y):g.moveTo(x,y); }
      g.lineTo(W*2,H); g.lineTo(-W,H); g.closePath(); g.fillStyle='#000'; g.fill(); g.strokeStyle=hsl(lerp(190,280,z)+tide*60,80,lerp(70,30,z),1-z*.7); g.lineWidth=1.5; g.stroke(); } } },

{ name:'Fractal Pumpkin Patch', sub:'one jack-o\'-lantern becomes a kaleidoscope of a thousand grins', dur:135, acts:['A Single Grin','Mirrors','The Patch Unfolds','Last Candle'],
  init(S){ S.rot=0; S.cv=document.createElement('canvas'); },
  draw(g,S){ const {W,H,a,act,ap}=S; const seg= act==0?1:act==1?2+Math.floor(ap*4)*2:act==2?12:Math.max(1,12-Math.floor(ap*12)); S.rot+=S.dt*(.1+a.mid*.5)*(act==2?3:1);
    fade(g,S,.25,'6,2,0'); const pump=(x,y,s,hue)=>{ g.fillStyle=hsl(hue,100,45); for(let k=-1;k<=1;k++){ g.beginPath(); g.ellipse(x+k*s*.35,y,s*.5,s*.6,0,0,TAU); g.fill(); }
      g.fillStyle='#3a5'; g.fillRect(x-s*.06,y-s*.8,s*.12,s*.25); g.fillStyle=hsl(55,100,60+a.bass*40);
      g.beginPath(); g.moveTo(x-s*.45,y-s*.1); g.lineTo(x-s*.2,y-s*.35); g.lineTo(x-s*.1,y-s*.05); g.moveTo(x+s*.45,y-s*.1); g.lineTo(x+s*.2,y-s*.35); g.lineTo(x+s*.1,y-s*.05); g.fill();
      g.beginPath(); g.moveTo(x-s*.55,y+s*.15); for(let i=0;i<=6;i++) g.lineTo(x-s*.55+i*s*.183, y+s*.15+(i%2?s*.15:0)+s*.15*a.bass); g.lineTo(x+s*.4,y+s*.45); g.lineTo(x-s*.4,y+s*.45); g.fill(); };
    const cx=W/2, cy=H/2; if(seg==1){ pump(cx,cy,H*.3*(1+a.bass*.15),28); return; }
    for(let s=0;s<seg;s++){ g.save(); g.translate(cx,cy); g.rotate(S.rot+s*TAU/seg); if(s%2) g.scale(1,-1);
      g.beginPath(); g.moveTo(0,0); g.arc(0,0,W,-Math.PI/seg,Math.PI/seg); g.closePath(); g.clip();
      const depth= act==2?6:3; for(let d=0;d<depth;d++){ const r=((d+S.t*.4*(1+a.bass))%depth)/depth, rr=Math.pow(r,1.5)*W*.6; pump(rr,0,10+r*H*.18,(20+d*30+S.t*20)%360); }
      g.restore(); } } },

{ name:'Washed Clean', sub:'the storm is over. the light comes back. finale.', dur:195, acts:['Last Drops','First Light','Sunrise Tunnel','Clear Morning'],
  init(S){ S.dew=Array.from({length:300},()=>({a:rnd(TAU),z:rnd(1),s:rnd(.5,1)})); },
  draw(g,S){ const {W,H,a,act,ap}=S, light= act==0?ap*.2:act==1?.2+ap*.5:act==2?.7+.3*ap:1;
    const sky=g.createLinearGradient(0,0,0,H); sky.addColorStop(0,hsl(lerp(240,205,light),60,lerp(8,60,light))); sky.addColorStop(1,hsl(lerp(260,35,light),80,lerp(12,75,light))); g.fillStyle=sky; g.fillRect(0,0,W,H);
    const sx=W/2, sy=lerp(H*1.05,H*.55,light), cx=W/2, cy=sy;
    g.save(); g.globalCompositeOperation='lighter'; for(let i=0;i<24;i++){ const an=i/24*TAU+S.t*.05, len=W*(.4+light*.6)*(1+(a.beat?.05:0)); g.fillStyle=hsl(40,100,70,.04+light*.05+a.mid*.05);
      g.beginPath(); g.moveTo(sx,sy); g.lineTo(sx+Math.cos(an-.04)*len,sy+Math.sin(an-.04)*len); g.lineTo(sx+Math.cos(an+.04)*len,sy+Math.sin(an+.04)*len); g.fill(); }
    if(act>=2){ // tunnel of light rings
      for(let i=0;i<14;i++){ const z=((i/14)+S.t*.15*(1+a.bass))%1, r=Math.pow(z,2.2)*W; g.strokeStyle=hsl(30+i*12,100,70,(1-z)*.5*(act==2?1:1-ap)); g.lineWidth=2+z*10; g.beginPath(); g.arc(cx,cy,r,0,TAU); g.stroke(); } }
    const sg=g.createRadialGradient(sx,sy,0,sx,sy,H*.18); sg.addColorStop(0,'#fffbe8'); sg.addColorStop(.4,hsl(40,100,70,.8)); sg.addColorStop(1,'rgba(255,160,40,0)'); g.fillStyle=sg; g.beginPath(); g.arc(sx,sy,H*.18*(1+a.bass*.2),0,TAU); g.fill();
    for(const d of S.dew){ d.z+=S.dt*.15*d.s*(1+a.level); if(d.z>1) d.z=0; const r=d.z*d.z*W*.6, x=cx+Math.cos(d.a)*r, y=cy+Math.sin(d.a)*r; g.fillStyle=hsl(190+d.a*20,90,85,d.z); g.beginPath(); g.arc(x,y,1+d.z*3,0,TAU); g.fill(); }
    g.restore(); if(act==0) rain(S,(1-ap)*200,g,.1);
    g.fillStyle=hsl(120,40,lerp(3,25,light)); g.beginPath(); g.moveTo(0,H); for(let x=0;x<=W;x+=W/40) g.lineTo(x,H*.85-NOISE(x/W*4+1)*H*.1); g.lineTo(W,H); g.fill(); } },

{ name:'Haunted Amusement Ride', sub:'one ticket, one car, no way off until the exit gate', dur:140, acts:['Boarding','The Clacking Climb','The Plunge','Exit Gate'],
  init(S){ S.z=0; S.gh=[]; },
  draw(g,S){ const {W,H,a,act,ap}=S; const speed= act==0?.1:act==1?.25+a.bass*.15:act==2?2.2+a.bass*1.5:lerp(1,.05,ap); S.z+=S.dt*speed;
    const climb= act==1?ap:act==2?1-clamp(ap*3):0, hy=H*(.5+climb*.35), cx=W/2+Math.sin(S.z*.7)*W*.08*(act==2?1:.2);
    g.fillStyle='#07020a'; g.fillRect(0,0,W,H); if(climb>.2) stars(g,S,120,climb);
    // track rails & ties (tunnel of arches)
    for(let i=24;i>=0;i--){ const z=((i-(S.z*4)%1))/24; if(z<=0) continue; const sc=1/(z*6+.15), x=cx+(W/2-cx)*(1-z), y=hy+(H*1.05-hy)*Math.min(1,sc*.18);
      const aw=W*.5*sc*.15, ah=H*.9*sc*.15; const lit=(i+Math.floor(S.z*4))%5==0;
      g.strokeStyle= lit?hsl((i*40+S.t*30)%360,90,55,.9):hsl(280,40,20+20*(1-z),.8); g.lineWidth=Math.max(1,6*sc*.15);
      if(act!=1){ g.beginPath(); g.moveTo(x-aw,y); g.lineTo(x-aw,y-ah); g.arc(x,y-ah,aw,Math.PI,0); g.lineTo(x+aw,y); g.stroke(); }
      g.strokeStyle='#5a3a20'; g.beginPath(); g.moveTo(x-aw*.35,y); g.lineTo(x+aw*.35,y); g.stroke(); }
    g.strokeStyle='#888'; g.lineWidth=3; g.beginPath(); g.moveTo(W*.38,H); g.lineTo(cx-4,hy); g.moveTo(W*.62,H); g.lineTo(cx+4,hy); g.stroke();
    if(act==1 && a.beat) S.cl=1; S.cl=(S.cl||0)*.9; g.fillStyle=`rgba(255,220,120,${S.cl*.6})`; g.font=`bold ${H*.05}px monospace`; g.textAlign='center'; if(act==1) g.fillText('CLACK',W/2+rnd(-5,5),H*.2);
    if((act==0||act==2) && a.beat) S.gh.push({x:rnd()<.5?rnd(.05,.3):rnd(.7,.95),s:0,l:1,h:rnd()<.5});
    S.gh=S.gh.filter(o=>(o.l-=S.dt*.8)>0).slice(-10); for(const o of S.gh){ o.s=Math.min(1,o.s+S.dt*5); if(o.h) ghost(g,o.x*W,H*.45,H*.1*o.s,o.l,S.t);
      else { g.save(); g.globalAlpha=o.l; g.fillStyle='#ddd'; g.beginPath(); g.arc(o.x*W,H*.4,H*.08*o.s,0,TAU); g.fill(); g.fillStyle='#000'; g.beginPath(); g.arc(o.x*W-H*.03,H*.39,H*.018,0,TAU); g.arc(o.x*W+H*.03,H*.39,H*.018,0,TAU); g.fill(); g.fillRect(o.x*W-H*.03,H*.43,H*.06,H*.012); g.restore(); } }
    if(act==2 && a.drop) S.flash(.4);
    // car front
    g.fillStyle='#4a0d14'; g.beginPath(); g.moveTo(W*.2,H); g.quadraticCurveTo(W*.5,H*.8,W*.8,H); g.fill(); g.fillStyle='#d4af37'; g.fillRect(W*.47,H*.86,W*.06,H*.02);
    if(act==3){ g.fillStyle=`rgba(0,0,0,${ap*.6})`; g.fillRect(0,0,W,H); g.fillStyle=hsl(40,100,60,ap); g.font=`bold ${H*.07}px Georgia`; g.fillText('EXIT ⇢ THANKS FOR RIDING',W/2,H*.3); } } },

{ name:'Deep Ocean Fishing', sub:'a lone boat, a long line, and something enormous below', dur:255, acts:['Night Boat','The Line Drops','Something Bites','Reeling Up'],
  init(S){ S.bio=Array.from({length:180},()=>({x:rnd(),y:rnd(),h:rnd(160,300),s:rnd(.5,2),p:rnd(TAU)})); S.cam=0; },
  draw(g,S){ const {W,H,a,act,ap}=S; const camT= act==0?0:act==1?ap:act==2?1:1-ap; S.cam=lerp(S.cam,camT,S.dt); const depth=S.cam;
    const sea=g.createLinearGradient(0,0,0,H); sea.addColorStop(0,hsl(215,60,lerp(14,3,depth))); sea.addColorStop(1,hsl(230,70,lerp(5,1,depth))); g.fillStyle=sea; g.fillRect(0,0,W,H);
    const sy=H*(.35-depth*1.2); // surface line scrolls up as we descend
    if(sy>-H*.2){ g.fillStyle='#050816'; g.fillRect(0,0,W,Math.max(0,sy)); stars(g,{...S,H:Math.max(1,sy)},80); g.fillStyle='#f4efd8'; g.beginPath(); g.arc(W*.8,sy*.35,H*.05,0,TAU); g.fill();
      g.strokeStyle='rgba(180,200,255,.5)'; g.beginPath(); for(let x=0;x<=W;x+=10) g.lineTo(x,sy+Math.sin(x*.02+S.t*2)*4+a.bass*6*Math.sin(x*.05)); g.stroke();
      const bx=W*.4, by=sy+Math.sin(S.t*1.3)*5; g.fillStyle='#111'; g.beginPath(); g.moveTo(bx-W*.08,by-10); g.lineTo(bx+W*.08,by-10); g.lineTo(bx+W*.06,by+12); g.lineTo(bx-W*.06,by+12); g.fill(); g.fillRect(bx-4,by-H*.1,3,H*.09);
      g.fillStyle=hsl(45,100,65); g.beginPath(); g.arc(bx-W*.03,by-18,4+a.high*4,0,TAU); g.fill(); }
    // line + hook
    const lx=W*.4+W*.05, hookY= act==0?sy:Math.min(H*.7, sy+H*(.1+ (act==1?ap:1)*1.6)); g.strokeStyle='rgba(220,220,220,.5)'; g.lineWidth=1; g.beginPath(); g.moveTo(lx,Math.max(sy-H*.1,-10)); g.lineTo(lx+Math.sin(S.t)*10,hookY); g.stroke();
    g.fillStyle=hsl(60,100,70,.9); g.beginPath(); g.arc(lx+Math.sin(S.t)*10,hookY,5+a.bass*5,0,TAU); g.fill();
    g.save(); g.globalCompositeOperation='lighter'; for(const b of S.bio){ b.x+=Math.sin(S.t*.5+b.p)*.0006; const y=((b.y-depth*.6)%1+1)%1*H, x=b.x*W, gl=(.2+.8*Math.max(0,Math.sin(S.t*b.s+b.p)))*depth*(.4+a.high);
      g.fillStyle=hsl(b.h,100,60,gl); g.beginPath(); g.arc(x,y,2+b.s*2,0,TAU); g.fill(); if(b.s>1.7){ g.strokeStyle=hsl(b.h,100,60,gl*.5); g.beginPath(); for(let k=0;k<4;k++){ g.moveTo(x-6+k*4,y); g.lineTo(x-6+k*4+Math.sin(S.t*3+k)*4,y+20); } g.stroke(); } }
    g.restore();
    if(act>=2){ // the leviathan: glowing eye + jaw rising
      const rise= act==2?clamp(ap*1.5):1, ey=lerp(H*1.3,H*.75,rise)-(act==3?ap*H*.3:0), ex=lx+40;
      g.fillStyle='rgba(10,20,30,.95)'; g.beginPath(); g.ellipse(ex,ey+H*.2,W*.35,H*.3,0,0,TAU); g.fill();
      g.fillStyle=hsl(20+a.bass*40,100,55); g.shadowBlur=40; g.shadowColor='#f60'; g.beginPath(); g.ellipse(ex+W*.12,ey,H*.04*(1+a.bass*.5),H*.025,0,0,TAU); g.fill(); g.shadowBlur=0;
      g.fillStyle='#ddd'; for(let i=0;i<10;i++){ const tx=ex-W*.15+i*W*.03; g.beginPath(); g.moveTo(tx,ey+H*.08); g.lineTo(tx+W*.012,ey+H*.12+a.bass*20); g.lineTo(tx+W*.024,ey+H*.08); g.fill(); }
      if(a.drop||(act==2&&a.beat&&Math.random()<.2)) S.flash(.25); } } },

{ name:'Flying Through Space', sub:'past the aurora, past the moon, into the warp', dur:200, acts:['Starfield Drift','Nebula Passage','HYPERSPACE','Arrival'],
  init(S){ S.st3=Array.from({length:700},()=>[rnd(-1,1),rnd(-1,1),rnd(.05,1)]); S.pl=[]; S.nh=rnd(360); },
  draw(g,S){ const {W,H,a,act,ap}=S; const sp= act==0?.08:act==1?.15+a.bass*.15:act==2?1.2+a.bass*1.5:lerp(1,.03,clamp(ap*2)); fade(g,S,act==2?.12:.5,'0,0,4');
    if(act>=1){ for(let i=0;i<3;i++){ const nx=W*(.3+i*.25)+Math.sin(S.t*.05+i)*W*.1, ny=H*(.4+Math.cos(S.t*.07+i*2)*.2), r=H*(.4+a.mid*.2);
      const ng=g.createRadialGradient(nx,ny,0,nx,ny,r); ng.addColorStop(0,hsl(S.nh+i*50,90,50,.07)); ng.addColorStop(1,'rgba(0,0,0,0)'); g.fillStyle=ng; g.fillRect(0,0,W,H); } }
    if(a.beat&&Math.random()<.15&&S.pl.length<2&&act!=2) S.pl.push({x:rnd(-.6,.6),y:rnd(-.4,.4),z:1,h:rnd(360),r:rnd()<.4});
    const cx=W/2, cy=H/2, F=W*.5;
    S.pl=S.pl.filter(p=>(p.z-=S.dt*.08*(1+sp))>.05); for(const p of S.pl){ const x=cx+p.x/p.z*F, y=cy+p.y/p.z*F, r=H*.03/p.z; const pg=g.createRadialGradient(x-r*.3,y-r*.3,0,x,y,r); pg.addColorStop(0,hsl(p.h,70,65)); pg.addColorStop(1,hsl(p.h,70,12)); g.fillStyle=pg; g.beginPath(); g.arc(x,y,r,0,TAU); g.fill();
      if(p.r){ g.strokeStyle=hsl(p.h+40,60,70,.6); g.lineWidth=r*.08; g.beginPath(); g.ellipse(x,y,r*1.8,r*.4,-.3,0,TAU); g.stroke(); } }
    g.lineCap='round'; for(const s of S.st3){ const z0=s[2]; s[2]-=S.dt*sp; if(s[2]<.02){ s[0]=rnd(-1,1); s[1]=rnd(-1,1); s[2]=1; continue; }
      const x=cx+s[0]/s[2]*F*.5, y=cy+s[1]/s[2]*F*.5, x0=cx+s[0]/z0*F*.5, y0=cy+s[1]/z0*F*.5; if(x<0||x>W||y<0||y>H) continue;
      g.strokeStyle= act==2?hsl(200+s[0]*80,90,75,1-s[2]):`rgba(255,255,255,${1-s[2]})`; g.lineWidth=(1-s[2])*3; g.beginPath(); g.moveTo(x0,y0); g.lineTo(x,y); g.stroke(); }
    if(act==2&&a.drop) S.flash(.35);
    if(act==3){ const r=H*.25*clamp(ap*1.5); const pg=g.createRadialGradient(cx-r*.3,cy-r*.3,0,cx,cy,r); pg.addColorStop(0,'#9cf'); pg.addColorStop(.6,'#1a4a8a'); pg.addColorStop(1,'#020812'); g.fillStyle=pg; g.beginPath(); g.arc(cx,cy,r,0,TAU); g.fill(); } } },

{ name:'Cruising Route 66', sub:'sundown to neon midnight on the Mother Road', dur:225, acts:['Desert Afternoon','Sunset Miles','Neon Night','Last Motel'],
  init(S){ S.z=0; S.signs=[]; S.n=0; },
  draw(g,S){ const {W,H,a,act,ap}=S, night= act==0?ap*.2:act==1?.2+ap*.6:1; S.z+=S.dt*(1.2+a.bass*1.2)*(act==3?1-ap*.8:1); const hz=H*.55;
    const sky=g.createLinearGradient(0,0,0,hz); sky.addColorStop(0,hsl(lerp(200,265,night),70,lerp(55,8,night))); sky.addColorStop(1,hsl(lerp(35,320,night),90,lerp(70,40,night))); g.fillStyle=sky; g.fillRect(0,0,W,hz);
    if(night>.5) stars(g,{...S,H:hz},100,night);
    const sunY=lerp(hz*.4,hz*1.05,clamp(night*1.3)), sr=H*.16; g.save(); g.beginPath(); g.rect(0,0,W,hz); g.clip(); const sg=g.createLinearGradient(0,sunY-sr,0,sunY+sr); sg.addColorStop(0,'#ffe36e'); sg.addColorStop(1,'#ff2a8a'); g.fillStyle=sg; g.beginPath(); g.arc(W/2,sunY,sr,0,TAU); g.fill();
    g.fillStyle=sky; for(let i=0;i<6;i++) { g.fillStyle=hsl(lerp(35,320,night),90,lerp(70,40,night)); g.fillRect(0,sunY+i*sr*.16,W,2+i*1.5); } g.restore();
    // mesas
    g.fillStyle=hsl(lerp(15,270,night),50,lerp(30,10,night)); g.beginPath(); g.moveTo(0,hz); g.lineTo(W*.05,hz-H*.08); g.lineTo(W*.18,hz-H*.08); g.lineTo(W*.22,hz); g.moveTo(W*.7,hz); g.lineTo(W*.74,hz-H*.12); g.lineTo(W*.86,hz-H*.12); g.lineTo(W*.92,hz); g.fill();
    g.fillStyle=hsl(lerp(30,280,night),50,lerp(45,5,night)); g.fillRect(0,hz,W,H-hz);
    // synthwave grid in night
    if(night>.4){ g.strokeStyle=hsl(300,100,60,(night-.4)*.8); g.lineWidth=1; for(let i=0;i<16;i++){ const z=((i/16)+S.z*.1)%1, y=hz+z*z*(H-hz); g.beginPath(); g.moveTo(0,y); g.lineTo(W,y); g.stroke(); } for(let i=-12;i<=12;i++){ g.beginPath(); g.moveTo(W/2+i*8,hz); g.lineTo(W/2+i*W*.15,H); g.stroke(); } }
    g.fillStyle='#222'; g.beginPath(); g.moveTo(W/2-6,hz); g.lineTo(W/2+6,hz); g.lineTo(W*.85,H); g.lineTo(W*.15,H); g.fill();
    g.fillStyle= a.beat?'#fff':'#ffd000'; for(let i=0;i<12;i++){ const z=((i/12)+S.z*.25)%1, z2=z+.03, y=hz+z*z*(H-hz), y2=hz+z2*z2*(H-hz), w=1+z*12; g.fillRect(W/2-w/2,y,w,Math.max(1,y2-y)); }
    // roadside signs / motels
    if(Math.random()<S.dt*(.4+a.beat*3)) S.signs.push({z:0,side:rnd()<.5?-1:1,k:(S.n++)%4,h:rnd(360)}); S.signs=S.signs.filter(s=>(s.z+=S.dt*.25*(1+a.bass))<1.05);
    for(const s of S.signs){ const z=s.z*s.z, x=W/2+s.side*(W*.06+z*W*.55), y=hz+z*(H-hz), sc=.1+z*2.4; g.fillStyle='#111'; g.fillRect(x-2*sc,y-H*.12*sc,4*sc,H*.12*sc);
      const neon=night>.4, txt=['US 66','MOTEL','EAT','VACANCY'][s.k], col= s.k==0?'#fff':hsl(s.h,100,60); g.font=`bold ${H*.03*sc}px sans-serif`; g.textAlign='center';
      if(s.k==0){ g.fillStyle='#eee'; g.beginPath(); g.arc(x,y-H*.14*sc,H*.035*sc,0,TAU); g.fill(); g.fillStyle='#000'; g.fillText('66',x,y-H*.13*sc); }
      else { if(neon){ g.shadowBlur=20; g.shadowColor=col; } g.fillStyle= neon?col:'#c33'; g.fillText(txt,x,y-H*.13*sc); g.shadowBlur=0; } }
    // dashboard
    g.fillStyle='#0b0b0b'; g.beginPath(); g.moveTo(0,H); g.lineTo(0,H*.88); g.quadraticCurveTo(W/2,H*.8,W,H*.88); g.lineTo(W,H); g.fill(); g.fillStyle='#9ff'; g.font=`${H*.03}px monospace`; g.fillText((55+a.bass*30|0)+' MPH',W/2,H*.95); } },

{ name:'Plummeting Jet', sub:'nose down, altimeter screaming, pull up at the last second', dur:100, acts:['Cruise','Nose Over','TERMINAL DIVE','PULL UP!'],
  init(S){ S.alt=41000; S.cl=Array.from({length:40},()=>({x:rnd(-1,1),y:rnd(-1,1),z:rnd(.1,1)})); S.warn=0; },
  draw(g,S){ const {W,H,a,act,ap}=S; const pitch= act==0?0:act==1?ap*.9:act==2?.95:lerp(.95,-.2,clamp(ap*2)); const sp= act==0?.05:act==1?.1+ap*.5:act==2?1+a.bass:lerp(1,.1,ap);
    const tA= act==0?41000:act==1?lerp(41000,30000,ap):act==2?lerp(30000,1200,ap):lerp(1200,3500,ap); S.alt=lerp(S.alt,tA,S.dt*2);
    const g0=clamp(1-S.alt/41000); // ground closeness
    const hy=H*(.5-pitch*.9)-(act==2?g0*0:0); g.save(); g.translate(W/2,H/2); g.rotate(Math.sin(S.t*.7)*.08*sp+(a.beat?rnd(-.01,.01)*sp:0)); g.translate(-W/2,-H/2);
    const sk=g.createLinearGradient(0,hy-H,0,hy); sk.addColorStop(0,'#0a1a4a'); sk.addColorStop(1,'#7ab8ff'); g.fillStyle=sk; g.fillRect(-W,hy-H*2,W*3,H*2);
    const gr=g.createLinearGradient(0,hy,0,H*1.5); gr.addColorStop(0,hsl(110,30,35)); gr.addColorStop(1,hsl(90,40,lerp(25,40,g0))); g.fillStyle=gr; g.fillRect(-W,hy,W*3,H*2);
    // fields/roads on ground growing as we approach
    g.strokeStyle='rgba(30,40,20,.6)'; const gs=lerp(.2,4,g0*g0); for(let i=-10;i<=10;i++){ g.lineWidth=1+gs; g.beginPath(); g.moveTo(W/2+i*40*gs,hy); g.lineTo(W/2+i*400*gs,H*2); g.stroke(); const y=hy+(i+10)*(i+10)*gs*4+((S.t*sp*200)%(gs*40)); g.beginPath(); g.moveTo(-W,y); g.lineTo(W*2,y); g.stroke(); }
    g.restore();
    // clouds ripping past
    for(const c of S.cl){ c.z-=S.dt*sp*.6; if(c.z<.05){ c.z=1; c.x=rnd(-1,1); c.y=rnd(-1,1); } const x=W/2+c.x/c.z*W*.3, y=H/2+c.y/c.z*H*.3, r=H*.06/c.z; g.fillStyle=`rgba(255,255,255,${.25*(1-c.z)*(act==3&&ap>.5?.2:1)})`; g.beginPath(); g.ellipse(x,y,r*2,r,0,0,TAU); g.fill(); }
    // cockpit frame
    g.fillStyle='#14161a'; g.beginPath(); g.moveTo(0,0); g.lineTo(W*.12,0); g.lineTo(W*.22,H*.68); g.lineTo(0,H*.75); g.fill(); g.beginPath(); g.moveTo(W,0); g.lineTo(W*.88,0); g.lineTo(W*.78,H*.68); g.lineTo(W,H*.75); g.fill(); g.fillRect(0,H*.68,W,H*.32);
    // altimeter
    const ax=W*.3, ay=H*.84, R=H*.11; g.fillStyle='#000'; g.beginPath(); g.arc(ax,ay,R,0,TAU); g.fill(); g.strokeStyle='#888'; g.lineWidth=3; g.stroke();
    g.strokeStyle='#fff'; g.lineWidth=2; for(let i=0;i<10;i++){ const an=i/10*TAU-Math.PI/2; g.beginPath(); g.moveTo(ax+Math.cos(an)*R*.8,ay+Math.sin(an)*R*.8); g.lineTo(ax+Math.cos(an)*R*.95,ay+Math.sin(an)*R*.95); g.stroke(); }
    const n1=(S.alt%1000)/1000*TAU-Math.PI/2, n2=(S.alt%10000)/10000*TAU-Math.PI/2; g.lineWidth=3; g.beginPath(); g.moveTo(ax,ay); g.lineTo(ax+Math.cos(n1)*R*.85,ay+Math.sin(n1)*R*.85); g.stroke(); g.lineWidth=6; g.beginPath(); g.moveTo(ax,ay); g.lineTo(ax+Math.cos(n2)*R*.5,ay+Math.sin(n2)*R*.5); g.stroke();
    g.fillStyle='#0f0'; g.font=`${H*.03}px monospace`; g.textAlign='center'; g.fillText(Math.round(S.alt)+' FT',ax,ay+R+H*.035);
    // warning panel: amber/red lamps pulse on beats (no full-screen strobe)
    if(act>=2&&a.beat) S.warn=1; S.warn*=.9; const wx=W*.62, wy=H*.8; ['PULL UP','TERRAIN','OVERSPEED'].forEach((t,i)=>{ const on= act>=2 && (i==0?S.warn>.3:act==2); g.fillStyle= on?(i==0?'#f22':'#fa0'):'#311'; g.fillRect(wx+i*W*.1,wy,W*.09,H*.06); g.fillStyle=on?'#000':'#622'; g.font=`bold ${H*.022}px monospace`; g.fillText(t,wx+i*W*.1+W*.045,wy+H*.04); });
    if(act==3&&ap<.15&&a.beat) S.flash(.2); } },
];
// curated Story Show order (opens with the cellar, closes washed clean)
const STORY_ORDER=[1,7,15,6,11,18,0,8,19,9,2,16,12,4,10,13,5,3,17,14];
