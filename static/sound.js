// Âm thanh cho màn hình lớn: tất cả được tổng hợp bằng Web Audio ngay trên trình duyệt.
// Nhạc nền là giai điệu tự soạn cho app (không dùng file nhạc, không dùng bài hát có sẵn).
//   Sound.unlock()           gọi trong một lần bấm của người dùng (trình duyệt yêu cầu)
//   Sound.music("vote")      đổi nhạc nền: "quiz" | "vote" | "draw" | "drum" | null
//   Sound.sfx("chomp")       hiệu ứng: chomp, ding, buzz, tick, pop, fanfare, out
//   Sound.set(on, vol)       bật/tắt và âm lượng 0..100 (MC chỉnh từ điện thoại)
(function () {
  "use strict";
  const st = { ctx: null, master: null, music: null, fx: null, noise: null, on: true, vol: 0.7,
               track: null, step: 0, nextT: 0, timer: null, log: [] };
  window.__soundLog = st.log;

  function log(ev) { st.log.push(ev); if (st.log.length > 1000) st.log.splice(0, 200); }

  function ensure() {
    if (st.ctx) return st.ctx;
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return null;
    const ctx = new AC();
    st.ctx = ctx;
    st.master = ctx.createGain();
    st.master.gain.value = st.on ? st.vol : 0;
    // bộ nén: tiếng to và đều trong sảnh ồn mà không bị rè
    const comp = ctx.createDynamicsCompressor();
    comp.threshold.value = -20; comp.knee.value = 12; comp.ratio.value = 4; comp.attack.value = 0.005; comp.release.value = 0.2;
    const makeup = ctx.createGain(); makeup.gain.value = 1.5;
    st.analyser = ctx.createAnalyser();
    st.analyser.fftSize = 2048;
    st.master.connect(comp); comp.connect(makeup); makeup.connect(st.analyser);
    st.analyser.connect(ctx.destination);
    st.music = ctx.createGain(); st.music.gain.value = 1.0; st.music.connect(st.master);
    st.fx = ctx.createGain(); st.fx.gain.value = 1.0; st.fx.connect(st.master);
    const len = ctx.sampleRate * 1.5;
    st.noise = ctx.createBuffer(1, len, ctx.sampleRate);
    const data = st.noise.getChannelData(0);
    for (let i = 0; i < len; i++) data[i] = Math.random() * 2 - 1;
    return ctx;
  }

  const hz = (m) => 440 * Math.pow(2, (m - 69) / 12);

  function tone(t, freq, dur, o = {}) {
    const ctx = st.ctx, osc = ctx.createOscillator(), g = ctx.createGain();
    osc.type = o.type || "triangle";
    osc.frequency.setValueAtTime(freq, t);
    if (o.slideTo) osc.frequency.exponentialRampToValueAtTime(o.slideTo, t + dur);
    if (o.detune) osc.detune.value = o.detune;
    const peak = o.gain == null ? 0.1 : o.gain, a = o.attack || 0.005;
    g.gain.setValueAtTime(0.0001, t);
    g.gain.exponentialRampToValueAtTime(peak, t + a);
    g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    osc.connect(g); g.connect(o.dest || st.music);
    osc.start(t); osc.stop(t + dur + 0.05);
  }

  function hiss(t, dur, o = {}) {
    const ctx = st.ctx, src = ctx.createBufferSource(), f = ctx.createBiquadFilter(), g = ctx.createGain();
    src.buffer = st.noise;
    f.type = o.filter || "highpass"; f.frequency.value = o.freq || 6000; f.Q.value = o.q || 0.7;
    g.gain.setValueAtTime(o.gain == null ? 0.05 : o.gain, t);
    g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    src.connect(f); f.connect(g); g.connect(o.dest || st.music);
    src.start(t, Math.random() * 1.2); src.stop(t + dur + 0.02);
  }

  function kick(t, gain = 0.35) { tone(t, 140, 0.18, { type: "sine", slideTo: 45, gain }); }

  // ── nhạc nền: bước 1/16, vòng 4 ô nhịp = 64 bước ──
  const _ = null;
  const TRACKS = {
    // Bình chọn: Đô trưởng, vui tươi, có tiếng chuông xe tuyết
    vote: {
      bpm: 128,
      chords: [[60, 64, 67], [59, 62, 67], [57, 60, 64], [57, 60, 65]],
      bass: [36, 43, 45, 41],
      mel: [72,_,_,76,_,_,79,_,77,_,76,_,74,_,72,_,  74,_,_,79,_,_,83,_,81,_,79,_,77,_,74,_,
            76,_,_,72,_,_,76,_,81,_,79,_,76,_,74,_,  72,_,_,77,_,_,81,_,79,_,77,_,76,_,74,_],
      play(s, t, sd) {
        const bar = (s >> 4) & 3, b = s & 15;
        if (b % 4 === 0) kick(t, 0.28);
        if (b % 4 === 0 || b === 6 || b === 14) tone(t, hz(this.bass[bar]), sd * 1.6, { type: "triangle", gain: 0.16 });
        if (b % 4 === 2) this.chords[bar].forEach(m => tone(t, hz(m), sd * 0.9, { type: "square", gain: 0.022 }));
        if (b % 2 === 1) hiss(t, 0.05, { freq: 7500, gain: b % 4 === 3 ? 0.05 : 0.025 });
        const m = this.mel[s & 63];
        if (m) tone(t, hz(m), sd * 1.8, { type: "sine", gain: 0.07 });
      }
    },
    // Zombie ăn bánh: kiểu "lén lút" vui nhộn, nhịp oom-pah
    draw: {
      bpm: 112,
      roots: [45, 40, 45, 40], fifths: [52, 47, 52, 47],
      chords: [[57, 60, 64], [56, 59, 64], [57, 60, 64], [56, 59, 62]],
      mel: [69,_,72,_,71,_,69,_,68,_,_,_,64,_,_,_,  68,_,71,_,69,_,68,_,64,_,_,_,_,_,_,_,
            69,_,72,_,76,_,75,_,76,_,_,_,72,_,_,_,  71,_,68,_,64,_,68,_,71,_,_,_,_,_,_,_],
      play(s, t, sd) {
        const bar = (s >> 4) & 3, b = s & 15;
        if (b === 0) tone(t, hz(this.roots[bar]), sd * 2.5, { type: "triangle", gain: 0.2 });
        if (b === 8) tone(t, hz(this.fifths[bar] - 12), sd * 2.5, { type: "triangle", gain: 0.18 });
        if (b === 4 || b === 12) this.chords[bar].forEach(m => tone(t, hz(m), sd * 0.6, { type: "square", gain: 0.02 }));
        if (b % 2 === 0) hiss(t, 0.03, { filter: "bandpass", freq: 2200, q: 4, gain: 0.05 });
        const m = this.mel[s & 63];
        if (m) tone(t, hz(m), sd * 1.2, { type: "square", gain: 0.035 });
      }
    },
    // Đố vui: nhịp đập hồi hộp, tiếng đồng hồ
    quiz: {
      bpm: 120,
      roots: [33, 33, 29, 31],
      pads: [[57, 60, 64], [57, 60, 64], [53, 57, 60], [55, 59, 62]],
      play(s, t, sd) {
        const bar = (s >> 4) & 3, b = s & 15;
        if (b % 2 === 0) tone(t, hz(this.roots[bar]), sd * 1.4, { type: "triangle", gain: 0.17 });
        if (b % 4 === 0) hiss(t, 0.03, { freq: b % 8 === 0 ? 5000 : 3000, gain: 0.07 });
        if (b === 0) this.pads[bar].forEach(m => tone(t, hz(m), sd * 15, { type: "sine", gain: 0.025, attack: 0.3 }));
        if (bar === 3 && b % 2 === 0) tone(t, hz(69 + b), sd * 1.5, { type: "sine", gain: 0.04 });
        if (b === 0 || b === 10) kick(t, 0.22);
      }
    },
    // Công bố kết quả bình chọn: trống dồn
    drum: {
      bpm: 140,
      play(s, t, sd) {
        const b = s & 31;
        hiss(t, 0.06, { filter: "bandpass", freq: 1800, q: 0.8, gain: 0.04 + 0.08 * (b / 31) });
        if (b === 0 || b === 16) tone(t, 70, 0.5, { type: "sine", gain: 0.3, slideTo: 50 });
      }
    }
  };

  function schedule() {
    if (!st.ctx || !st.track) return;
    const tr = TRACKS[st.track], sd = 60 / tr.bpm / 4;
    if (st.nextT < st.ctx.currentTime) st.nextT = st.ctx.currentTime + 0.05;
    while (st.nextT < st.ctx.currentTime + 0.15) {
      try { tr.play(st.step, st.nextT, sd); } catch (_) {}
      st.nextT += sd;
      st.step++;
    }
  }

  function music(name) {
    name = name || null;
    if (name === st.track) return;
    log("music:" + name);
    st.track = name;
    st.step = 0;
    if (!ensure()) return;
    st.nextT = st.ctx.currentTime + 0.08;
    if (!st.timer) st.timer = setInterval(schedule, 25);
  }

  const SFX = {
    chomp(t) {
      const p = 0.85 + Math.random() * 0.3;
      for (let k = 0; k < 3; k++) hiss(t + k * 0.085, 0.07, { filter: "bandpass", freq: 1400 * p + k * 300, q: 1.2, gain: 0.55, dest: st.fx });
      tone(t, 160 * p, 0.12, { type: "sine", slideTo: 70, gain: 0.35, dest: st.fx });
    },
    ding(t) { [1047, 1319, 1568, 2093].forEach((f, i) => tone(t + i * 0.07, f, 0.6, { type: "sine", gain: 0.18, dest: st.fx })); },
    buzz(t) { tone(t, 110, 0.55, { type: "square", gain: 0.12, dest: st.fx }); tone(t, 116, 0.55, { type: "square", gain: 0.1, dest: st.fx }); },
    tick(t) { tone(t, 1600, 0.05, { type: "sine", gain: 0.22, dest: st.fx }); },
    pop(t) { tone(t, 520, 0.14, { type: "sine", slideTo: 980, gain: 0.25, dest: st.fx }); hiss(t, 0.04, { freq: 4000, gain: 0.08, dest: st.fx }); },
    out(t) { [[196, 185], [185, 175], [175, 147]].forEach(([a, b], i) => tone(t + i * 0.32, a, i === 2 ? 0.7 : 0.3, { type: "sawtooth", slideTo: b, gain: 0.07, dest: st.fx })); },
    fanfare(t) {
      [72, 76, 79].forEach((m, i) => tone(t + i * 0.13, hz(m), 0.16, { type: "square", gain: 0.08, dest: st.fx }));
      [72, 76, 79, 84].forEach(m => tone(t + 0.42, hz(m), 1.3, { type: "triangle", gain: 0.08, dest: st.fx }));
      hiss(t + 0.42, 1.2, { freq: 6000, gain: 0.08, dest: st.fx });
      tone(t + 0.42, 140, 0.18, { type: "sine", slideTo: 45, gain: 0.28, dest: st.fx });
    }
  };

  function sfx(name) {
    log("sfx:" + name);
    if (!ensure() || !SFX[name] || st.ctx.state !== "running") return;
    try { SFX[name](st.ctx.currentTime + 0.01); } catch (_) {}
  }

  function set(on, vol) {
    st.on = !!on;
    if (typeof vol === "number") st.vol = Math.max(0, Math.min(1, vol / 100));
    if (st.master) st.master.gain.setTargetAtTime(st.on ? st.vol : 0, st.ctx.currentTime, 0.05);
  }

  function unlock() {
    const ctx = ensure();
    if (!ctx) return Promise.resolve(false);
    return ctx.resume().then(() => ctx.state === "running").catch(() => false);
  }

  // Mức âm lượng đang phát ra loa (0..1), để kiểm thử và hiển thị
  function level() {
    if (!st.analyser) return 0;
    const buf = new Float32Array(st.analyser.fftSize);
    st.analyser.getFloatTimeDomainData(buf);
    let sum = 0;
    for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
    return Math.sqrt(sum / buf.length);
  }

  window.Sound = { music, sfx, set, unlock, level, unlocked: () => !!(st.ctx && st.ctx.state === "running"), current: () => st.track };
})();
