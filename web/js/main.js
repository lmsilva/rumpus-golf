// Main: websocket client + render dispatch + input wiring.
window.RG = window.RG || {};

(function () {
  const scene = document.getElementById("scene");
  let ws = null;
  let lastScreen = null;
  let lastSignature = "";
  let state = null;
  RG.settings = null;

  const FEED_SCREENS = new Set(["S03", "S04", "S05", "S07", "S07b", "S07c", "S08", "S09", "S11", "S12", "S13", "S14"]);
  const PLAY_SCREENS = new Set(["S11", "S12", "S13", "S14"]);

  // ---- websocket ----
  function connect() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/ws`);
    ws.binaryType = "arraybuffer";
    ws.onmessage = (ev) => {
      if (typeof ev.data === "string") {
        try { onState(JSON.parse(ev.data)); } catch (e) {}
      } else {
        RG.feed.draw(new Uint8Array(ev.data));
      }
    };
    ws.onclose = () => setTimeout(connect, 1000);
  }

  RG.send = function (msg) {
    if (ws && ws.readyState === 1) ws.send(JSON.stringify(msg));
  };

  // ---- state handling ----
  function onState(st) {
    state = st;
    RG.settings = st.settings || RG.settings;
    applyTheme(st.settings && st.settings.theme);
    RG.audio.setFromSettings();

    const screen = st.screen;
    const sig = signature(st);
    const rebuild = screen !== lastScreen || sig !== lastSignature;

    if (rebuild) {
      const keep = focusKey(document.activeElement);
      scene.innerHTML = RG.screens.render(st);
      lastScreen = screen;
      lastSignature = sig;
      wire();
      applyMarquee();
      if (!restoreFocus(keep)) focusFirst();
    }
    const slot = scene.querySelector("[data-feed-slot]");
    const feedOn = !(st.feed && st.feed.enabled === false);
    RG.feed.show((FEED_SCREENS.has(screen) && feedOn) || !!slot, slot);
    RG.feed.overlay((st.overlay && st.overlay.shapes) || []);
    handleMusic(st);
  }

  function signature(st) {
    if (st.screen === "S19") return settingsSig(st);
    if (st.screen === "S04") return st.setup && st.setup.capturing ? "cap" : "";
    if (st.screen === "S05") return (st.ui && st.ui.preset) || "";
    if (st.screen === "S06") return (st.game && st.game.course_id) || "";
    if (st.screen === "S10") return [(st.ui && st.ui.holes), (st.ui && st.ui.stroke_cap)].join("|");
    if (st.screen === "S07b" || st.screen === "S07c") {
      const obs = (st.ui && st.ui.obstacles) || [];
      return [st.ui && st.ui.selected, obs.map((o) => `${o.state}:${o.confidence}`).join(",")].join("|");
    }
    if (st.screen === "S09") return ((st.ui && st.ui.players) || []).map((p) => p.name).join(",");
    if (st.screen === "S15") return String(st.ui && st.ui.focus);
    if (st.screen !== "S11") return "";
    const g = st.game || {};
    const a = st.ui && st.ui.active_player;
    const m = st.ui && st.ui.motion;
    const others = (st.ui.others || []).map((p) => `${p.id}:${(g.scores[p.id] || [])[g.hole - 1] || 0}`).join(",");
    return [a && a.id, a && a.name, g.hole, (g.scores[a && a.id] || [])[g.hole - 1] || 0, m && m.moving, m && m.hidden, m && m.dist_to_cup, others].join("|");
  }

  function settingsSig(st) {
    const s = st.settings || {};
    const cam = (st.ui && st.ui.camera) || {};
    const names = (cam.devices || []).map((d) => d.driver || d.name).join(",");
    return [
      st.ui && st.ui.tab, cam.scanning, s.theme,
      s.music && s.music.enabled, s.music && s.music.volume, s.music && s.music.playlist,
      s.sfx && s.sfx.enabled, s.sfx && s.sfx.volume, s.sfx && s.sfx.announcer,
      s.controller && s.controller.rumble, s.display && s.display.showCameraFeed,
      s.rules && s.rules.holes, s.rules && s.rules.strokeCap, s.rules && s.rules.oobPenalty, s.rules && s.rules.tunnelBonus,
      cam.active_device, cam.resolution, cam.backend, names,
      cam.is_mock, cam.error, st.sensor && st.sensor.model,
    ].join("|");
  }

  function applyTheme(theme) {
    let t = theme || "dark";
    if (t === "auto") {
      t = window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
    }
    document.documentElement.dataset.theme = t;
  }
  if (window.matchMedia) {
    window.matchMedia("(prefers-color-scheme: light)").addEventListener("change", () => {
      if (RG.settings && RG.settings.theme === "auto") applyTheme("auto");
    });
  }

  function handleMusic(st) {
    if (!RG.settings || !RG.settings.music || RG.settings.music.enabled === false) {
      RG.audio.stopMusic();
      return;
    }
    const playlist = (RG.settings.music && RG.settings.music.playlist) || "lounge-jazz";
    const play = PLAY_SCREENS.has(st.screen);
    const track = (play || playlist === "lofi-hiphop") ? "BackedVibesClean" : "LobbyTime";
    RG.audio.music(track);
    RG.audio.duck(st.state === "PAUSE" || st.state === "SETTINGS" ? 0.3 : 1);
  }

  // ---- input wiring (event delegation) ----
  function wire() {
    scene.querySelectorAll("[data-action]").forEach((el) => {
      el.addEventListener("click", (e) => {
        const a = el.getAttribute("data-action");
        RG.audio.sfx(a === "confirm" ? "confirm" : "click");
        const msg = { t: "action", a };
        if (el.hasAttribute("data-index")) msg.index = parseInt(el.getAttribute("data-index"), 10);
        RG.send(msg);
        e.stopPropagation();
      });
    });

    scene.querySelectorAll("[data-set-preset]").forEach((el) =>
      el.addEventListener("click", () => RG.send({ t: "set", key: "preset", value: el.getAttribute("data-set-preset") })));
    scene.querySelectorAll("[data-set-holes]").forEach((el) =>
      el.addEventListener("click", () => RG.send({ t: "set", key: "holes", value: parseInt(el.getAttribute("data-set-holes"), 10) })));
    scene.querySelectorAll("[data-set-cap]").forEach((el) =>
      el.addEventListener("click", () => RG.send({ t: "set", key: "stroke_cap", value: parseInt(el.getAttribute("data-set-cap"), 10) })));
    scene.querySelectorAll("[data-set-stepper]").forEach((el) =>
      el.addEventListener("click", () => RG.send({ t: "set", key: "stepper", player_id: el.getAttribute("data-set-stepper"), delta: parseInt(el.getAttribute("data-delta"), 10) })));
    scene.querySelectorAll("[data-set-theme]").forEach((el) =>
      el.addEventListener("click", () => {
        const v = el.getAttribute("data-set-theme");
        applyTheme(v);
        RG.send({ t: "set", key: "settings.theme", value: v });
      }));
    scene.querySelectorAll("[data-set-playlist]").forEach((el) =>
      el.addEventListener("click", () => RG.send({ t: "set", key: "settings.music.playlist", value: el.getAttribute("data-set-playlist") })));
    scene.querySelectorAll("[data-set-music]").forEach((el) =>
      el.addEventListener("click", () => RG.send({ t: "set", key: "settings.music.enabled", value: el.getAttribute("data-set-music") === "true" })));
    scene.querySelectorAll("[data-set-sfx]").forEach((el) =>
      el.addEventListener("click", () => RG.send({ t: "set", key: "settings.sfx.enabled", value: el.getAttribute("data-set-sfx") === "true" })));
    scene.querySelectorAll("[data-set-announcer]").forEach((el) =>
      el.addEventListener("click", () => RG.send({ t: "set", key: "settings.sfx.announcer", value: !(RG.settings && RG.settings.sfx && RG.settings.sfx.announcer) })));
    scene.querySelectorAll("[data-set-rumble]").forEach((el) =>
      el.addEventListener("click", () => RG.send({ t: "set", key: "settings.controller.rumble", value: !(RG.settings && RG.settings.controller && RG.settings.controller.rumble) })));
    scene.querySelectorAll("[data-set-feed]").forEach((el) =>
      el.addEventListener("click", () => RG.send({ t: "set", key: "settings.display.showCameraFeed", value: !(RG.settings && RG.settings.display && RG.settings.display.showCameraFeed) })));

    function paintSlider(el) {
      const min = Number(el.min || 0), max = Number(el.max || 100), val = Number(el.value || 0);
      el.style.setProperty("--pct", `${((val - min) / Math.max(1, max - min)) * 100}%`);
    }
    scene.querySelectorAll(".slider").forEach((el) => {
      paintSlider(el);
      el.addEventListener("input", () => paintSlider(el));
    });
    scene.querySelectorAll("[data-set-musicvol]").forEach((el) =>
      el.addEventListener("input", () => {
        RG.audio.unlock();
        RG.send({ t: "set", key: "settings.music.volume", value: parseInt(el.value, 10) / 100 });
      }));
    scene.querySelectorAll("[data-set-sfxvol]").forEach((el) =>
      el.addEventListener("input", () => RG.send({ t: "set", key: "settings.sfx.volume", value: parseInt(el.value, 10) / 100 })));

    // Settings → Game rules
    scene.querySelectorAll("[data-set-rule-holes]").forEach((el) =>
      el.addEventListener("click", () => RG.send({ t: "set", key: "settings.rules.holes", value: parseInt(el.getAttribute("data-set-rule-holes"), 10) })));
    scene.querySelectorAll("[data-set-rule-cap]").forEach((el) =>
      el.addEventListener("click", () => RG.send({ t: "set", key: "settings.rules.strokeCap", value: parseInt(el.getAttribute("data-set-rule-cap"), 10) })));
    scene.querySelectorAll("[data-set-rule-oob]").forEach((el) =>
      el.addEventListener("click", () => RG.send({ t: "set", key: "settings.rules.oobPenalty", value: !(RG.settings && RG.settings.rules && RG.settings.rules.oobPenalty) })));
    scene.querySelectorAll("[data-set-rule-tunnel]").forEach((el) =>
      el.addEventListener("click", () => RG.send({ t: "set", key: "settings.rules.tunnelBonus", value: !(RG.settings && RG.settings.rules && RG.settings.rules.tunnelBonus) })));

    // Settings → Camera
    scene.querySelectorAll("[data-set-camera-device]").forEach((el) =>
      el.addEventListener("change", () => RG.send({ t: "set", key: "settings.camera.device", value: parseInt(el.value, 10) })));
    scene.querySelectorAll("[data-set-camera-resolution]").forEach((el) =>
      el.addEventListener("change", () => RG.send({ t: "set", key: "settings.camera.resolution", value: el.value })));
    scene.querySelectorAll("[data-set-camera-backend]").forEach((el) =>
      el.addEventListener("change", () => RG.send({ t: "set", key: "settings.camera.backend", value: el.value })));

    scene.querySelectorAll("input[data-text]").forEach((el) => {
      const key = el.getAttribute("data-text");
      const index = parseInt(el.getAttribute("data-index"), 10);
      el.addEventListener("change", () => RG.send({ t: "text", key, index, value: el.value }));
      el.addEventListener("keydown", (e) => { if (e.key === "Enter") { RG.send({ t: "text", key, index, value: el.value }); el.blur(); } e.stopPropagation(); });
    });
  }

  function applyMarquee() {
    scene.querySelectorAll("[data-marquee]").forEach((el) => {
      const inner = el.firstElementChild;
      if (!inner) return;
      el.classList.remove("animate");
      el.style.setProperty("--slot-w", el.clientWidth + "px");
      if (inner.scrollWidth > el.clientWidth + 4) el.classList.add("animate");
    });
  }

  // ---- keyboard navigation & shortcuts ----
  // Focus is a DOM concern: arrows / Tab move a visible focus ring, Enter /
  // Space activate the focused control (which sends the same semantic action a
  // mouse click does). This keeps the browser a dumb display while making every
  // menu reachable without a pointer.
  function focusables() {
    return Array.from(scene.querySelectorAll("button, [tabindex], input, select, textarea")).filter((el) => {
      if (el.disabled || el.getAttribute("aria-hidden") === "true") return false;
      if (el.style && el.style.pointerEvents === "none") return false;
      const r = el.getBoundingClientRect();
      return r.width > 0 && r.height > 0;
    });
  }
  function focusKey(el) {
    if (!el || !el.getAttribute) return "";
    const attrs = [
      "data-action", "data-index", "data-set-theme", "data-set-music", "data-set-sfx",
      "data-set-playlist", "data-set-rule-holes", "data-set-rule-cap", "data-set-camera-device",
      "data-set-camera-resolution", "data-set-camera-backend", "data-set-preset", "data-set-holes",
      "data-set-announcer", "data-set-rumble", "data-set-feed", "data-set-rule-oob", "data-set-rule-tunnel",
      "data-text",
    ];
    return attrs.filter((a) => el.hasAttribute(a)).map((a) => `${a}=${el.getAttribute(a)}`).join("|");
  }
  function restoreFocus(key) {
    if (!key) return false;
    const els = focusables();
    const match = els.find((el) => focusKey(el) === key);
    if (!match) return false;
    match.focus({ preventScroll: true });
    markFocus(match);
    return true;
  }
  function markFocus(el) {
    scene.querySelectorAll(".kb-focus").forEach((n) => n.classList.remove("kb-focus"));
    if (el) el.classList.add("kb-focus");
  }
  function activate(el) {
    if (!el) return;
    el.focus({ preventScroll: true });
    markFocus(el);
    try { el.scrollIntoView({ block: "nearest" }); } catch (err) {}
  }
  function moveFocus(delta) {
    const els = focusables();
    if (!els.length) return;
    const idx = els.indexOf(document.activeElement);
    const next = idx < 0 ? (delta > 0 ? 0 : els.length - 1) : (idx + delta + els.length) % els.length;
    activate(els[next]);
  }
  function moveFocus2d(dx, dy) {
    const els = focusables();
    if (!els.length) return;
    const cur = els.includes(document.activeElement) ? document.activeElement : null;
    if (!cur) { moveFocus(dy || dx); return; }
    const group = cur.closest(".seg, .nav-row");
    if (group && dx && !dy) {
      const gEls = els.filter((e) => group.contains(e));
      const gi = gEls.indexOf(cur);
      if (gi >= 0 && gEls.length > 1) {
        activate(gEls[(gi + dx + gEls.length) % gEls.length]);
        return;
      }
    }
    const r = cur.getBoundingClientRect();
    const cx = r.left + r.width / 2;
    const cy = r.top + r.height / 2;
    let best = null;
    let bestScore = Infinity;
    for (const el of els) {
      if (el === cur) continue;
      const er = el.getBoundingClientRect();
      const ex = er.left + er.width / 2;
      const ey = er.top + er.height / 2;
      const ddx = ex - cx;
      const ddy = ey - cy;
      if (dx && ddx * dx <= 8) continue;
      if (dy && ddy * dy <= 8) continue;
      const along = dx ? Math.abs(ddx) : Math.abs(ddy);
      const across = dx ? Math.abs(ddy) : Math.abs(ddx);
      const score = along + across * 3;
      if (score < bestScore) { bestScore = score; best = el; }
    }
    if (best) activate(best);
    else moveFocus(dy || dx);
  }
  function focusFirst() {
    const els = focusables();
    const first = els.find((el) => el.tagName === "BUTTON" && el.getAttribute("data-action") !== "back") || els[0];
    if (first) activate(first);
  }
  function scrollPane(dir) {
    const ae = document.activeElement;
    let node = ae;
    while (node && node !== scene) {
      if (node.scrollHeight > node.clientHeight + 4) {
        node.scrollBy({ top: dir * (node.clientHeight * 0.8) });
        return;
      }
      node = node.parentElement;
    }
  }

  window.addEventListener("keydown", (e) => {
    const ae = document.activeElement;
    const isText = ae && ((ae.tagName === "INPUT" && ae.type !== "range" && ae.type !== "checkbox") || ae.tagName === "TEXTAREA");
    const isSelect = ae && ae.tagName === "SELECT";
    const isRange = ae && ae.tagName === "INPUT" && ae.type === "range";
    const key = e.key;

    if (key === "Escape") {
      if (isText || isSelect) { ae.blur(); return; }
      RG.send({ t: "action", a: "back" });
      return;
    }
    if (isText) return;
    if (isSelect && (key === "ArrowDown" || key === "ArrowUp" || key === "Enter" || key === " ")) return;
    if (isRange && (key === "ArrowLeft" || key === "ArrowRight")) return;

    // Movement / focus — spatial on arrows, document order on Tab
    if (key === "ArrowDown") { e.preventDefault(); moveFocus2d(0, 1); return; }
    if (key === "ArrowUp") { e.preventDefault(); moveFocus2d(0, -1); return; }
    if (key === "ArrowRight") { e.preventDefault(); moveFocus2d(1, 0); return; }
    if (key === "ArrowLeft") { e.preventDefault(); moveFocus2d(-1, 0); return; }
    if (key === "Tab") { e.preventDefault(); moveFocus(e.shiftKey ? -1 : 1); return; }
    if (key === "PageDown") { e.preventDefault(); scrollPane(1); return; }
    if (key === "PageUp") { e.preventDefault(); scrollPane(-1); return; }

    // Activate the focused control, else send a generic confirm.
    if (key === "Enter" || key === " ") {
      if (ae && ae !== document.body && (ae.tagName === "BUTTON" || (ae.hasAttribute && ae.hasAttribute("data-action")))) {
        e.preventDefault();
        ae.click();
      } else {
        RG.send({ t: "action", a: "confirm" });
      }
      return;
    }

    // Global shortcuts (work regardless of focus)
    if (key === "z" || key === "r") { RG.send({ t: "action", a: "undo" }); return; }
    if (key === "y" || key === "d") { RG.send({ t: "action", a: "secondary" }); return; }
    if (key === "f") { toggleFullscreen(); return; }
    if (key === "m") { RG.send({ t: "action", a: "menu" }); return; }
  });

  function toggleFullscreen() {
    if (!document.fullscreenElement) document.documentElement.requestFullscreen().catch(() => {});
    else document.exitFullscreen();
  }

  // ---- scene scaling ----
  function fit() {
    const s = Math.min(window.innerWidth / 1920, window.innerHeight / 1080);
    const x = (window.innerWidth - 1920 * s) / 2;
    const y = (window.innerHeight - 1080 * s) / 2;
    scene.style.transform = `translate(${x}px, ${y}px) scale(${s})`;
  }
  window.addEventListener("resize", fit);
  fit();

  connect();
})();
