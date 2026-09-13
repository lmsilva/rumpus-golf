// WebAudio: synthesized SFX (CC0-style) + bundled music.
// Browsers block playback until a user gesture; we unlock on the first
// pointer/key event and retry so Settings / first-load are not silent.
window.RG = window.RG || {};

(function () {
  let ctx = null;
  let unlocked = false;

  function ac() {
    if (!ctx) {
      try { ctx = new (window.AudioContext || window.webkitAudioContext)(); } catch (e) { ctx = null; }
    }
    return ctx;
  }

  function musicVol() {
    const v = RG.settings && RG.settings.music && RG.settings.music.volume;
    return v == null ? 0.62 : Number(v);
  }
  function sfxVol() {
    const v = RG.settings && RG.settings.sfx && RG.settings.sfx.volume;
    return v == null ? 0.8 : Number(v);
  }
  function musicOn() {
    return !(RG.settings && RG.settings.music && RG.settings.music.enabled === false);
  }
  function sfxOn() {
    return !(RG.settings && RG.settings.sfx && RG.settings.sfx.enabled === false);
  }

  function tone(freq, dur, type = "sine", gain = 0.18, when = 0) {
    const c = ac(); if (!c) return;
    if (c.state === "suspended") c.resume();
    const t = c.currentTime + when;
    const o = c.createOscillator();
    const g = c.createGain();
    o.type = type; o.frequency.value = freq;
    const amp = Math.max(0.0001, gain * sfxVol());
    g.gain.setValueAtTime(0.0001, t);
    g.gain.exponentialRampToValueAtTime(amp, t + 0.01);
    g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    o.connect(g); g.connect(c.destination);
    o.start(t); o.stop(t + dur + 0.02);
  }

  function noise(dur, gain = 0.2, when = 0) {
    const c = ac(); if (!c) return;
    if (c.state === "suspended") c.resume();
    const t = c.currentTime + when;
    const len = Math.floor(c.sampleRate * dur);
    const buf = c.createBuffer(1, len, c.sampleRate);
    const d = buf.getChannelData(0);
    for (let i = 0; i < len; i++) d[i] = (Math.random() * 2 - 1) * (1 - i / len);
    const src = c.createBufferSource(); src.buffer = buf;
    const g = c.createGain(); g.gain.value = gain * sfxVol();
    src.connect(g); g.connect(c.destination);
    src.start(t);
  }

  const synth = {
    click:   () => tone(880, 0.08, "triangle", 0.12),
    confirm: () => { tone(660, 0.09, "triangle", 0.14); tone(990, 0.12, "triangle", 0.1, 0.04); },
    back:    () => tone(440, 0.09, "triangle", 0.1),
    putter:  () => { tone(220, 0.05, "square", 0.08); noise(0.03, 0.05); },
    thunk:   () => tone(180, 0.12, "triangle", 0.16),
    plink:   () => { tone(1568, 0.06, "sine", 0.12); tone(2093, 0.08, "sine", 0.1, 0.03); },
    cheer:   () => { for (let i = 0; i < 6; i++) noise(0.2, 0.05, i * 0.09); },
    sting:   () => { tone(523.25, 0.18, "sine", 0.16); tone(784.0, 0.22, "sine", 0.16, 0.18); },
  };

  const SFX_FILES = {
    click: "click.ogg", confirm: "confirm.ogg", back: "back.ogg",
    putter: "putter.ogg", thunk: "thunk.ogg", plink: "plink.ogg",
  };
  const sfxCache = {};

  function playSfx(name) {
    const file = SFX_FILES[name];
    if (!file || sfxCache[name] === null) { (synth[name] || (() => {}))(); return; }
    if (!sfxCache[name]) {
      const el = new Audio(`/assets/sfx/${file}`);
      el.preload = "auto";
      el.addEventListener("error", () => { sfxCache[name] = null; });
      sfxCache[name] = el;
    }
    const el = sfxCache[name];
    el.volume = Math.max(0, Math.min(1, sfxVol()));
    try { el.currentTime = 0; } catch (e) { /* ignore seek-before-ready */ }
    el.play().then(() => { unlocked = true; }).catch((err) => {
      if (err && err.name === "NotAllowedError") { (synth[name] || (() => {}))(); return; }
      sfxCache[name] = null;
      (synth[name] || (() => {}))();
    });
  }

  let musicEl = null;
  let musicName = "";
  let wantPlay = false;

  function ensureMusic() {
    if (musicEl) return musicEl;
    musicEl = document.createElement("audio");
    musicEl.loop = true;
    musicEl.preload = "auto";
    musicEl.setAttribute("playsinline", "");
    document.body.appendChild(musicEl);
    return musicEl;
  }

  function tryPlayMusic() {
    if (!musicEl || !wantPlay || !musicOn()) return;
    musicEl.volume = Math.max(0, Math.min(1, musicEl._duck != null ? musicEl._duck : musicVol()));
    musicEl.play().then(() => { unlocked = true; }).catch(() => {});
  }

  function unlock() {
    const c = ac();
    if (c && c.state === "suspended") c.resume();
    unlocked = true;
    tryPlayMusic();
  }
  window.addEventListener("pointerdown", unlock, { capture: true });
  window.addEventListener("keydown", unlock, { capture: true });

  RG.audio = {
    unlock,
    sfx(name) {
      if (!sfxOn()) return;
      unlock();
      playSfx(name);
    },
    music(name) {
      const el = ensureMusic();
      if (!musicOn()) { wantPlay = false; el.pause(); return; }
      wantPlay = true;
      if (name && name !== musicName) {
        musicName = name;
        el.src = `/assets/music/${name}.mp3`;
      }
      el._duck = musicVol();
      tryPlayMusic();
    },
    duck(level) {
      const el = ensureMusic();
      const base = musicVol();
      el._duck = base * (level == null ? 1 : level);
      el.volume = Math.max(0, Math.min(1, el._duck));
    },
    stopMusic() {
      wantPlay = false;
      if (musicEl) musicEl.pause();
    },
    setFromSettings() {
      if (!musicOn()) { this.stopMusic(); return; }
      const el = ensureMusic();
      const base = musicVol();
      el._duck = el._duck != null && el._duck <= base ? el._duck : base;
      el.volume = Math.max(0, Math.min(1, el._duck));
      if (wantPlay) tryPlayMusic();
    },
  };
})();
