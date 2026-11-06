// Audio engine: input/file/demo -> AnalyserNode -> bands, beat, drop, BPM
const TAU2=Math.PI*2;
const AudioEngine = (() => {
  let ctx, an, freq, wave, src, el, demoNodes = [];
  const A = { bass:0, mid:0, high:0, level:0, beat:false, beatCount:0, drop:false, bpm:0, wave:null, freq:null, active:false, sens:1, sinceBeat:9 };
  let avgB = 0, longB = 0.05, lastBeat = 0, intervals = [], fluxPrev = null, fluxAvg = 0, lastDrop = -99, quietT = 0;
  function ensure() {
    if (ctx) return;
    ctx = new (window.AudioContext || window.webkitAudioContext)();
    an = ctx.createAnalyser(); an.fftSize = 2048; an.smoothingTimeConstant = 0.6;
    freq = new Uint8Array(an.frequencyBinCount); wave = new Uint8Array(an.fftSize);
    A.wave = wave; A.freq = freq;
  }
  function stop() {
    try { src && src.disconnect(); } catch(e){}
    if (src && src.mediaStream) src.mediaStream.getTracks().forEach(t => t.stop());
    if (el) { el.pause(); el = null; }
    demoNodes.forEach(n => { try { n.stop && n.stop(); n.disconnect(); } catch(e){} }); demoNodes = [];
  }
  async function listDevices() {
    try { const d = await navigator.mediaDevices.enumerateDevices(); return d.filter(x => x.kind === 'audioinput'); } catch(e) { return []; }
  }
  async function useInput(deviceId) {
    ensure(); stop(); await ctx.resume();
    const s = await navigator.mediaDevices.getUserMedia({ audio: { deviceId: deviceId ? { exact: deviceId } : undefined, echoCancellation:false, noiseSuppression:false, autoGainControl:false } });
    src = ctx.createMediaStreamSource(s); src.mediaStream = s; src.connect(an); A.active = true;
  }
  async function useFile(file) {
    ensure(); stop(); await ctx.resume();
    el = new Audio(URL.createObjectURL(file)); el.loop = true; el.crossOrigin = 'anonymous';
    src = ctx.createMediaElementSource(el); src.connect(an); an.connect(ctx.destination);
    await el.play(); A.active = true;
  }
  // synthetic 128 BPM trance-ish test signal: kick + bass + arpeggio + hat noise
  async function useDemo() {
    ensure(); stop(); await ctx.resume();
    const out = ctx.createGain(); out.gain.value = 0.5; out.connect(an); demoNodes.push(out);
    const spb = 60/128, t0 = ctx.currentTime + 0.05;
    const nb = ctx.createBuffer(1, ctx.sampleRate*0.05, ctx.sampleRate); const d = nb.getChannelData(0); for (let i=0;i<d.length;i++) d[i]=Math.random()*2-1;
    for (let i = 0; i < 4*64; i++) {
      const t = t0 + i*spb, bar = Math.floor(i/4), breakdown = (bar%16)>=12;
      if (!breakdown) { const o = ctx.createOscillator(), g = ctx.createGain(); o.frequency.setValueAtTime(140,t); o.frequency.exponentialRampToValueAtTime(40,t+0.15);
        g.gain.setValueAtTime(1,t); g.gain.exponentialRampToValueAtTime(0.001,t+0.3); o.connect(g).connect(out); o.start(t); o.stop(t+0.32); demoNodes.push(o); }
      const h = ctx.createBufferSource(), hg = ctx.createGain(), hp = ctx.createBiquadFilter(); hp.type='highpass'; hp.frequency.value=7000;
      h.buffer = nb; hg.gain.value = 0.4; h.connect(hp).connect(hg).connect(out); h.start(t+spb/2); demoNodes.push(h);
      for (let k=0;k<2;k++){ const o=ctx.createOscillator(), g=ctx.createGain(); o.type='sawtooth'; o.frequency.value=[220,330,440,392,294,262][(i*2+k)%6]*(breakdown?1:2);
        g.gain.setValueAtTime(0.12,t+k*spb/2); g.gain.exponentialRampToValueAtTime(0.001,t+k*spb/2+0.2); o.connect(g).connect(out); o.start(t+k*spb/2); o.stop(t+k*spb/2+0.22); demoNodes.push(o); }
    }
    A.active = true;
  }
  // ---- v2 analysis state ----
  const BANDS = { sub:[20,60], bass:[60,150], lowmid:[150,500], mid:[500,2000], high:[2000,6000], air:[6000,16000] };
  const env = {}, pk = {}, avg = {}; for (const k in BANDS) { env[k]=0; pk[k]=0; avg[k]=0.05; }
  let prevSpec = null, fluxHist = [], lastKick = -9, lastSnare = -9, lastHat = -9, beatT0 = 0, gainAuto = 1, lvlAvg = 0.1;
  let eShort = 0, eLong = 0, buildT = 0;
  Object.assign(A, { sub:0, lowmid:0, air:0, peaks:pk, flux:0, kick:false, snare:false, hat:false, kickEnv:0, snareEnv:0, hatEnv:0,
    beatPhase:0, trend:0, buildup:0, energy:0, react:1, autoGain:true, gain:1, dropEnv:0 });
  function update(dt, now) {
    A.beat = false; A.drop = false; A.kick = A.snare = A.hat = false; A.sinceBeat += dt;
    const R = A.react;
    const decay = x => x * Math.pow(0.02, dt); // ~fast exp decay
    A.kickEnv = decay(A.kickEnv); A.snareEnv = decay(A.snareEnv); A.hatEnv = A.hatEnv * Math.pow(0.005, dt); A.dropEnv *= Math.pow(0.3, dt);
    if (!an) { // idle breathing
      const ph = now*2.1; A.bass = 0.25+0.2*Math.max(0,Math.sin(ph))**8; A.sub=A.bass; A.mid = 0.2+0.1*Math.sin(now*.7); A.lowmid=A.mid; A.high = 0.15; A.air=.1; A.level=(A.bass+A.mid+A.high)/3;
      if (Math.sin(ph) > 0.99 && A.sinceBeat > 0.5) { A.beat = A.kick = true; A.kickEnv = 0.6; A.beatCount++; A.sinceBeat = 0; }
      A.beatPhase = (A.sinceBeat*2.1/TAU2)%1; return A;
    }
    an.getByteFrequencyData(freq); an.getByteTimeDomainData(wave);
    const n = freq.length, bin = ctx.sampleRate/2/n;
    const band = (lo, hi) => { let s=0,c=0; for (let i=Math.max(1,Math.floor(lo/bin)); i<=Math.min(n-1,Math.ceil(hi/bin)); i++){ s+=freq[i]; c++; } return c? s/c/255 : 0; };
    // auto-gain: normalise overall level toward ~0.35
    let raw = 0; for (let i=1;i<n/2;i++) raw += freq[i]; raw /= (n/2)*255; lvlAvg = lvlAvg*0.995 + raw*0.005;
    if (A.autoGain && lvlAvg > 0.003) gainAuto += (Math.min(4, Math.max(0.5, 0.22/lvlAvg)) - gainAuto) * Math.min(1, dt*0.5); else if (!A.autoGain) gainAuto = 1;
    A.gain = gainAuto;
    const k = A.sens * gainAuto, w = { sub:1, bass:1, lowmid:1.2, mid:1.5, high:2.2, air:3 };
    for (const b in BANDS) {
      const v = Math.min(1, band(...BANDS[b]) * k * w[b]);
      env[b] += (v - env[b]) * (v > env[b] ? 0.6 : 0.18);            // smoothed, fast attack
      pk[b] = Math.max(v, pk[b] * Math.pow(0.25, dt));                 // peak envelope
      avg[b] = avg[b]*0.99 + v*0.01;
    }
    A.sub = env.sub; A.lowmid = env.lowmid; A.air = env.air;
    A.bass = Math.min(1, (env.sub*0.4 + env.bass*0.6)); A.mid = Math.min(1,(env.lowmid*.3+env.mid*.7)); A.high = Math.min(1,(env.high*.6+env.air*.4));
    A.level = (A.bass+A.mid+A.high)/3;
    // spectral flux per region
    const lowEnd = Math.floor(150/bin), midLo = Math.floor(1500/bin), midHi = Math.floor(5000/bin), hiLo = Math.floor(7000/bin);
    let fL=0,fM=0,fH=0,fA=0;
    if (prevSpec) for (let i=1;i<n;i++){ const df=freq[i]-prevSpec[i]; if (df<=0) continue; fA+=df; if (i<=lowEnd) fL+=df; else if (i>=midLo&&i<=midHi) fM+=df; else if (i>=hiLo) fH+=df; }
    prevSpec = freq.slice(); fL/=255*Math.max(1,lowEnd); fM/=255*Math.max(1,midHi-midLo); fH/=255*Math.max(1,n-hiLo); fA/=255*n;
    A.flux = Math.min(1, fA*20*k);
    fluxHist.push([fL,fM,fH]); if (fluxHist.length > 43) fluxHist.shift();
    const mean = j => fluxHist.reduce((s,x)=>s+x[j],0)/fluxHist.length, sd = (j,m) => Math.sqrt(fluxHist.reduce((s,x)=>s+(x[j]-m)**2,0)/fluxHist.length);
    const mL=mean(0), mM=mean(1), mH=mean(2);
    const thr = 1.4 / Math.max(0.3, A.sens);
    if (fL > mL + thr*sd(0,mL) + 0.004 && env.bass + env.sub > 0.2 && now-lastKick > 0.22) { A.kick = true; lastKick = now; A.kickEnv = 1; }
    if (fM > mM + thr*sd(1,mM) + 0.003 && env.mid > 0.08 && now-lastSnare > 0.15 && !A.kick) { A.snare = true; lastSnare = now; A.snareEnv = 1; }
    if (fH > mH + thr*sd(2,mH) + 0.002 && now-lastHat > 0.08) { A.hat = true; lastHat = now; A.hatEnv = 1; }
    if (A.kick && A.sinceBeat > 0.25) {
      A.beat = true; A.beatCount++;
      if (A.sinceBeat < 2) { intervals.push(A.sinceBeat); if (intervals.length > 32) intervals.shift();
        const hist = {}; intervals.forEach(iv => { let bpm = 60/iv; while (bpm<70) bpm*=2; while (bpm>180) bpm/=2; const r=Math.round(bpm); for (let j=-1;j<=1;j++) hist[r+j]=(hist[r+j]||0)+(j?1:2); });
        let best=0,bv=0; for (const x in hist) if (hist[x]>bv){bv=hist[x];best=+x;} A.bpm = best; }
      A.sinceBeat = 0; beatT0 = now;
    }
    A.beatPhase = A.bpm ? ((now-beatT0)*A.bpm/60)%1 : Math.min(1, A.sinceBeat*2);
    // energy trend / buildup: short vs long energy plus rising highs
    const e = A.level; eShort = eShort*0.97 + e*0.03; eLong = eLong*0.998 + e*0.002; A.energy = eShort;
    A.trend = Math.max(-1, Math.min(1, (eShort - eLong) / Math.max(0.05, eLong) ));
    const rising = (env.high + env.air)/2 > (avg.high+avg.air)/2*1.1 && env.bass < avg.bass*0.9;
    buildT = rising || A.trend > 0.15 ? Math.min(12, buildT + dt) : Math.max(0, buildT - dt*1.5);
    A.buildup = Math.min(1, buildT/8);
    // drop: bass slams back after a quiet stretch or buildup
    if (env.bass < avg.bass*0.55) quietT += dt; else if (env.bass > avg.bass*0.8) quietT = Math.max(0, quietT - dt*0.5);
    if ((quietT > 2 || A.buildup > 0.5) && A.kick && env.bass > avg.bass*1.1 && now-lastDrop > 15) { A.drop = true; lastDrop = now; A.dropEnv = 1; quietT = 0; buildT = 0; }
    // reactivity scales the punch envelopes
    A.kickEnv = Math.min(1.5, A.kickEnv); A.punch = A.kickEnv*R; A.sparkle = A.hatEnv*R; A.snap = A.snareEnv*R;
    return A;
  }
  return { A, listDevices, useInput, useFile, useDemo, update, get ctx(){return ctx;} };
})();
