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

  // chuông xe tuyết: vài tiếng leng keng kim loại rất ngắn
  function jingle(t, gain = 0.05, dest) {
    for (let k = 0; k < 3; k++) {
      const tt = t + k * 0.012 + Math.random() * 0.006;
      hiss(tt, 0.06, { filter: "bandpass", freq: 7000 + Math.random() * 2500, q: 6, gain: gain * 1.6, dest });
      tone(tt, 4200 + Math.random() * 1800, 0.05, { type: "sine", gain: gain * 0.5, dest });
    }
  }

  // [[nốt, số bước 1/16], ...] -> mảng theo bước: mỗi ô là [nốt, độ dài] tại điểm bắt đầu nốt
  function seq(notes) {
    const out = [];
    for (const [m, d] of notes) { out.push([m, d]); for (let i = 1; i < d; i++) out.push(null); }
    return out;
  }
  const TRIAD = { C: [60, 64, 67], F: [60, 65, 69], G: [59, 62, 67], D: [57, 62, 66] };
  const ROOT = { C: 36, F: 41, G: 43, D: 38 };
  const FIFTH = { C: 43, F: 36, G: 38, D: 45 };

  // Jingle Bells (James Lord Pierpont, 1857 – phạm vi công cộng), điệp khúc, Đô trưởng
  const E5 = 76, G5 = 79, C5 = 72, D5 = 74, F5 = 77;
  const JB_A = [[E5,4],[E5,4],[E5,8], [E5,4],[E5,4],[E5,8], [E5,4],[G5,4],[C5,6],[D5,2], [E5,16]];
  const JINGLE = seq([...JB_A,
    [F5,4],[F5,4],[F5,6],[F5,2], [F5,4],[E5,4],[E5,4],[E5,2],[E5,2], [E5,4],[D5,4],[D5,4],[E5,4], [D5,8],[G5,8],
    ...JB_A,
    [F5,4],[F5,4],[F5,6],[F5,2], [F5,4],[E5,4],[E5,4],[E5,2],[E5,2], [G5,4],[G5,4],[F5,4],[D5,4], [C5,16]]);
  const JINGLE_CH = "CCCC FCDG CCCC FCGC".replace(/ /g, "").split("");

  // Deck the Halls (giai điệu dân gian xứ Wales – phạm vi công cộng), Đô trưởng
  const n = (m) => m + 12;
  const DECK_A = [[n(67),6],[n(65),2],[n(64),4],[n(62),4], [n(60),4],[n(62),4],[n(64),4],[n(60),4],
                  [n(62),2],[n(64),2],[n(65),2],[n(62),2],[n(64),6],[n(62),2], [n(60),4],[n(59),4],[n(60),8]];
  const DECK = seq([...DECK_A, ...DECK_A,
    [n(62),6],[n(64),2],[n(65),4],[n(62),4], [n(64),6],[n(65),2],[n(67),4],[n(62),4],
    [n(64),2],[n(66),2],[n(67),4],[n(69),2],[n(71),2],[n(72),4], [n(71),4],[n(69),4],[n(67),8],
    [n(67),6],[n(65),2],[n(64),4],[n(62),4], [n(60),4],[n(62),4],[n(64),4],[n(60),4],
    [n(69),2],[n(69),2],[n(69),2],[n(69),2],[n(67),6],[n(65),2], [n(64),4],[n(62),4],[n(60),8]]);
  const DECK_CH = "CCGC CCGC GCDG CCFC".replace(/ /g, "").split("");

  // ── nhạc nền: bước 1/16, vòng 4 ô nhịp = 64 bước ──
  const _ = null;
  const TRACKS = {
    // Bình chọn: Jingle Bells, nhịp nhanh vui tươi, chuông xe tuyết
    vote: {
      bpm: 150,
      play(s, t, sd) {
        const len = JINGLE.length, i = s % len, bar = Math.floor(i / 16), b = i & 15, ch = JINGLE_CH[bar];
        if (b === 0 || b === 8) { tone(t, hz(b === 0 ? ROOT[ch] : FIFTH[ch]), sd * 3, { type: "triangle", gain: 0.2 }); kick(t, 0.22); }
        if (b === 4 || b === 12) TRIAD[ch].forEach(m => tone(t, hz(m), sd * 1.5, { type: "square", gain: 0.02 }));
        if (b % 2 === 0) jingle(t, b % 4 === 2 ? 0.045 : 0.028);
        const note = JINGLE[i];
        if (note) {
          tone(t, hz(note[0]), sd * note[1] * 0.9, { type: "triangle", gain: 0.12 });
          tone(t, hz(note[0] + 12), sd * Math.min(note[1], 4) * 0.8, { type: "sine", gain: 0.04 });   // tiếng chuông sáng
        }
      }
    },
    // Zombie ăn bánh: Deck the Halls, nhịp nhún nhảy kiểu kèn tuba + chuông
    draw: {
      bpm: 132,
      play(s, t, sd) {
        const len = DECK.length, i = s % len, bar = Math.floor(i / 16), b = i & 15, ch = DECK_CH[bar];
        if (b === 0 || b === 8) tone(t, hz(b === 0 ? ROOT[ch] : FIFTH[ch]), sd * 2.5, { type: "triangle", gain: 0.22 });
        if (b === 4 || b === 12) TRIAD[ch].forEach(m => tone(t, hz(m), sd * 0.8, { type: "square", gain: 0.022 }));
        if (b % 4 === 2) jingle(t, 0.04);
        if (b % 2 === 0) hiss(t, 0.025, { filter: "bandpass", freq: 2400, q: 5, gain: 0.04 });
        const note = DECK[i];
        if (note) {
          tone(t, hz(note[0]), sd * Math.min(note[1], 3) * 0.7, { type: "square", gain: 0.045 });
          tone(t, hz(note[0]), sd * note[1] * 0.9, { type: "triangle", gain: 0.06 });
        }
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
    // "cạp cạp": hai miếng cắn giòn, mỗi miếng có tiếng răng va và tiếng bánh vỡ vụn
    chomp(t) {
      const p = 0.9 + Math.random() * 0.2;
      [0, 0.17].forEach((d, k) => {
        const tt = t + d;
        tone(tt, 1800 * p, 0.02, { type: "square", gain: 0.12, dest: st.fx });                       // răng va "cạp"
        hiss(tt + 0.005, 0.05, { filter: "bandpass", freq: 3200 * p, q: 1.5, gain: 0.7, dest: st.fx });
        hiss(tt + 0.03, 0.08, { filter: "bandpass", freq: 1500 * p - k * 200, q: 1, gain: 0.45, dest: st.fx });   // bánh vỡ
        tone(tt, 190 * p, 0.09, { type: "sine", slideTo: 80, gain: 0.3, dest: st.fx });                // hàm khép lại
      });
    },
    // tiếng cười "hô hô hô" giọng trầm của ông già Noel (tổng hợp giọng nói bằng bộ lọc âm vị "ô")
    hohoho(t) {
      jingle(t, 0.08, st.fx); jingle(t + 0.12, 0.07, st.fx); jingle(t + 0.24, 0.06, st.fx);
      const ctx = st.ctx;
      [[0.35, 0.3, 150, 118], [0.8, 0.3, 140, 110], [1.25, 0.55, 132, 92]].forEach(([d, dur, f0, f1]) => {
        const at = t + d;
        const out = ctx.createGain();
        out.gain.setValueAtTime(0.0001, at);
        out.gain.exponentialRampToValueAtTime(1, at + 0.04);
        out.gain.setValueAtTime(1, at + dur * 0.55);
        out.gain.exponentialRampToValueAtTime(0.0001, at + dur);
        out.connect(st.fx);
        // hơi thở "h" trước nguyên âm
        hiss(at - 0.06, 0.09, { filter: "bandpass", freq: 900, q: 1.2, gain: 0.25, dest: st.fx });
        // nguồn giọng: răng cưa + rung nhẹ, hạ giọng dần
        const src = ctx.createOscillator();
        src.type = "sawtooth";
        src.frequency.setValueAtTime(f0, at);
        src.frequency.exponentialRampToValueAtTime(f1, at + dur);
        const vib = ctx.createOscillator(), vg = ctx.createGain();
        vib.frequency.value = 6; vg.gain.value = 3; vib.connect(vg); vg.connect(src.frequency);
        // ba vùng cộng hưởng của âm "ô" giọng nam trầm
        [[430, 7, 1.0], [800, 9, 0.5], [2450, 12, 0.12]].forEach(([fq, q, g]) => {
          const bp = ctx.createBiquadFilter(), gg = ctx.createGain();
          bp.type = "bandpass"; bp.frequency.value = fq; bp.Q.value = q; gg.gain.value = g * 0.9;
          src.connect(bp); bp.connect(gg); gg.connect(out);
        });
        const chest = ctx.createGain(); chest.gain.value = 0.05;      // độ ấm của giọng ngực
        src.connect(chest); chest.connect(out);
        src.start(at); vib.start(at); src.stop(at + dur + 0.05); vib.stop(at + dur + 0.05);
      });
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
