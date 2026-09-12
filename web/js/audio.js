// WebAudio: synthesized SFX (CC0-style) + optional bundled music.
// Music/SFX files are dropped into /assets/music and /assets/sfx; if absent the
// game stays silent except for the synthesized turn sting / cheer / ticks.
window.RG = window.RG || {};

(function () {
  let ctx = null;
  function ac() {
    if (!ctx) {
      try { ctx = new (window.AudioContext || window.webkitAudioContext)(); } catch (e) { ctx = null; }
    }
    if (ctx && ctx.state === "suspended") ctx.resume();
    return ctx;
  }

  function tone(freq, dur, type = "sine", gain = 0.18, when = 0) {
    const c = ac(); if (!c) return;
    const t = c.currentTime + when;
    const o = c.createOscillator();
    const g = c.createGain();
    o.type = type; o.frequency.value = freq;
    g.gain.setValueAtTime(0.0001, t);
    g.gain.exponentialRampToValueAtTime(gain, t + 0.01);
    g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    o.connect(g); g.connect(c.destination);
    o.start(t); o.stop(t + dur + 0.02);
  }

  function noise(dur, gain = 0.2, when = 0) {
    const c = ac(); if (!c) return;
    const t = c.currentTime + when;
    const len = Math.floor(c.sampleRate * dur);
    const buf = c.createBuffer(1, len, c.sampleRate);
    const d = buf.getChannelData(0);
    for (let i = 0; i < len; i++) d[i] = (Math.random() * 2 - 1) * (1 - i / len);
    const src = c.createBufferSource(); src.buffer = buf;
    const g = c.createGain(); g.gain.value = gain;
    src.connect(g); g.connect(c.destination);
    src.start(t);
  }

  const sfx = {
    click:   () => tone(880, 0.08, "triangle", 0.12),
    confirm: () => { tone(660, 0.09, "triangle", 0.14); tone(990, 0.12, "triangle", 0.1, 0.04); },
    back:    () => tone(440, 0.09, "triangle", 0.1),
    putter:  () => { tone(220, 0.05, "square", 0.08); noise(0.03, 0.05); },
    thunk:   () => tone(180, 0.12, "triangle", 0.16),
    plink:   () => { tone(1568, 0.06, "sine", 0.12); tone(2093, 0.08, "sine", 0.1, 0.03); },
    cheer:   () => { for (let i = 0; i < 6; i++) noise(0.2, 0.05, i * 0.09); },
    sting:   () => { tone(523.25, 0.18, "sine", 0.16); tone(784.0, 0.22, "sine", 0.16, 0.18); },
  };

  let musicEl = null;
  let musicName = "";
  function ensureMusic() {
    if (musicEl) return musicEl;
    musicEl = document.createElement("audio");
    musicEl.loop = true;
    musicEl.preload = "auto";
    document.body.appendChild(musicEl);
    return musicEl;
  }

  RG.audio = {
    sfx(name) {
      if (!RG.settings || !RG.settings.sfx || RG.settings.sfx.enabled === false) return;
      (sfx[name] || (() => {}))();
    },
    music(name) {
      if (name === musicName) return;
      musicName = name;
      const el = ensureMusic();
      el.pause();
      el.src = `/assets/music/${name}.mp3`;
      el.volume = (RG.settings && RG.settings.music && RG.settings.music.volume) || 0.62;
      const play = () => { if (RG.settings.music.enabled !== false) el.play().catch(() => {}); };
      play();
      el.onerror = () => { el.pause(); };
    },
    duck(level) {
      const el = ensureMusic();
      const base = (RG.settings && RG.settings.music && RG.settings.music.volume) || 0.62;
      el.volume = base * (level || 1);
    },
    stopMusic() { const el = ensureMusic(); el.pause(); },
    setFromSettings() {
      const el = ensureMusic();
      const base = (RG.settings && RG.settings.music && RG.settings.music.volume) || 0.62;
      el.volume = base;
    },
  };
})();
