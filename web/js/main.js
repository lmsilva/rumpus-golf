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
    ws = new WebSocket(`${proto}://${location.host}/ws${location.search || ""}`);
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
      const screenChanged = screen !== lastScreen;
      const keep = focusKey(document.activeElement);
      if (RG.feed && RG.feed.detach) RG.feed.detach();
      scene.innerHTML = RG.screens.render(st);
      lastScreen = screen;
      lastSignature = sig;
      wire();
      applyMarquee();
      if (screen === "S06" && focusSelectedCourse()) {
        /* course tile highlight is the selected card, not a leftover DOM node */
      } else if (screen === "S15" && focusPauseRow(st)) {
        /* mint highlight is server-driven — follow it, not the old DOM node */
      } else if (!restoreFocus(keep)) {
        focusFirst();
      }
      if (screenChanged) {
        rumbleFor(screen);
        cueScreenAudio(screen, st);
      }
    }
    const slot = scene.querySelector("[data-feed-slot]");
    const feedOn = !(st.feed && st.feed.enabled === false);
    RG.feed.show((FEED_SCREENS.has(screen) && feedOn) || !!slot, slot);
    RG.feed.overlay((st.overlay && st.overlay.shapes) || []);
    patchS05(st);
    patchS11(st);
    handleMusic(st);
  }

  function signature(st) {
    if (st.screen === "S19") return settingsSig(st);
    if (st.screen === "S04") return st.setup && st.setup.capturing ? "cap" : "";
    if (st.screen === "S05") {
      const n = (st.ui && st.ui.corners) || 0;
      return `${(st.ui && st.ui.preset) || ""}|${(st.ui && st.ui.color_only) ? 1 : 0}|${n}`;
    }
    if (st.screen === "S06") return (st.game && st.game.course_id) || "";
    if (st.screen === "S07") return `${(st.ui && st.ui.ghosts || []).length}|${st.ui && st.ui.selected}`;
    if (st.screen === "S08") return `${(st.ui && st.ui.has_hole) ? 1 : 0}|${(st.ui && st.ui.searching) ? 1 : 0}|${(st.ui && st.ui.manual) ? 1 : 0}`;
    if (st.screen === "S10") return [(st.ui && st.ui.holes), (st.ui && st.ui.stroke_cap)].join("|");
    if (st.screen === "S07b" || st.screen === "S07c") {
      const obs = (st.ui && st.ui.obstacles) || [];
      return [st.ui && st.ui.selected, st.ui && st.ui.undo_count, obs.map((o) => `${o.state}:${o.confidence}`).join(",")].join("|");
    }
    if (st.screen === "S09") {
      const ps = (st.ui && st.ui.players) || [];
      return `${st.ui && st.ui.selected}|${ps.map((p) => p.id).join(",")}|${ps.map((p) => p.hue_name).join(",")}|${st.ui && st.ui.hue_clash ? 1 : 0}`;
    }
    if (st.screen === "S14") return String(((st.ui && st.ui.lost_balls) || []).length);
    if (st.screen === "S15") return `${st.ui && st.ui.focus}|${st.ui && st.ui.recal_flyout ? 1 : 0}`;
    if (st.screen !== "S11") return "";
    const g = st.game || {};
    const ui = st.ui || {};
    const a = ui.active_player;
    const others = (ui.others || []).map((p) => `${p.id}:${(g.scores[p.id] || [])[g.hole - 1] || 0}`).join(",");
    const lost = ((ui.lost_balls || []).map((b) => b.id).join(","));
    return [a && a.id, g.hole, (g.scores[a && a.id] || [])[g.hole - 1] || 0, ui.awaiting_tee ? 1 : 0, others, lost].join("|");
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
      cam.lock_notice, cam.locked, cam.exposure_control, cam.show_driver_settings,
      cam.fourcc, cam.capture_api, cam.actual_resolution, cam.measured_fps,
      s.display && s.display.debugOverlay,
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

  function patchS05(st) {
    if (st.screen !== "S05") return;
    const colorOnly = !!(st.ui && st.ui.color_only);
    const n = (st.ui && st.ui.corners) || 0;
    const w = (st.ui && st.ui.area_w) || 3;
    const h = (st.ui && st.ui.area_h) || 2;
    const prog = scene.querySelector("[data-corner-progress]");
    if (prog) prog.textContent = colorOnly ? `${n} of 4 corners` : "";
    const help = scene.querySelector("[data-size-help]");
    if (help && colorOnly) {
      help.textContent = `The webcam only sees pixels. ${w} × ${h} m is the real size of the rectangle you marked — that’s what turns those four corners into meters.`;
    }
    scene.querySelectorAll("button.btn[data-action=confirm]").forEach((btn) => {
      btn.disabled = colorOnly && n < 4;
    });
  }

  function playStatusText(st) {
    const ui = st.ui || {};
    const a = ui.active_player;
    const motion = ui.motion || {};
    const strokes = a ? ((st.game && st.game.scores[a.id]) || [])[st.game.hole - 1] || 0 : 0;
    if (!a) return "";
    if (motion.moving) return "Ball rolling…";
    if (motion.hidden) return "Stopped, hidden — position estimated";
    if (ui.awaiting_tee) {
      if (ui.in_start) return "In the start zone. Putt when ready, or confirm.";
      if (ui.ball_seen) return "Move it into the start circle, click it on the camera, or confirm when ready.";
      return "I don’t see your ball yet — click it on the camera, or confirm when it’s ready.";
    }
    return strokes === 0 ? "Ready. Putt when ready." : "Ball stopped. Putt when ready.";
  }

  function playPillText(st) {
    const ui = st.ui || {};
    const motion = ui.motion || {};
    if (ui.awaiting_tee) {
      if (ui.in_start) return "In start zone · waiting for putt";
      if (ui.ball_seen) return "Ball seen · not in the start circle yet";
      return "Looking for your ball";
    }
    if (motion.moving) return "Ball moving";
    if (motion.hidden) return "Ball stopped, hidden · position estimated";
    if (motion.dist_to_cup != null) return `Ball stopped · ${motion.dist_to_cup} m from cup`;
    return "Ball stopped";
  }

  function patchS11(st) {
    if (st.screen !== "S11") return;
    const status = scene.querySelector("[data-play-status]");
    if (status) status.textContent = playStatusText(st);
    const pill = scene.querySelector("[data-play-pill]");
    if (pill) pill.textContent = playPillText(st);
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
        if (a === "select" && el.classList.contains("course-card")) {
          paintCourseSelection(el);
        }
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
    scene.querySelectorAll("[data-set-debug-overlay]").forEach((el) =>
      el.addEventListener("click", () => RG.send({ t: "set", key: "settings.display.debugOverlay", value: !(RG.settings && RG.settings.display && RG.settings.display.debugOverlay) })));

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
    scene.querySelectorAll("[data-set-camera-exposure]").forEach((el) =>
      el.addEventListener("input", () => {
        const v = parseFloat(el.value);
        const label = el.parentElement && el.parentElement.querySelector("[data-exposure-label]");
        if (label) label.textContent = String(v);
        RG.send({ t: "set", key: "settings.camera.exposure", value: v });
      }));

    scene.querySelectorAll("input[data-text]").forEach((el) => {
      const key = el.getAttribute("data-text");
      const index = parseInt(el.getAttribute("data-index"), 10);
      const send = () => RG.send({ t: "text", key, index, value: el.value });
      el.addEventListener("click", (e) => e.stopPropagation());
      el.addEventListener("input", send);
      el.addEventListener("change", send);
      el.addEventListener("keydown", (e) => { if (e.key === "Enter") { send(); el.blur(); } e.stopPropagation(); });
    });

    if (state && (state.screen === "S12" || state.screen === "S13")) {
      scene.addEventListener("click", (e) => {
        if (e.target.closest && e.target.closest("button, input, [data-action]")) return;
        RG.send({ t: "action", a: "confirm" });
      });
    }
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
    return Array.from(scene.querySelectorAll("button, [tabindex], input, select, textarea, [data-action]")).filter((el) => {
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
    syncCourseHighlight(el);
  }
  function paintCourseSelection(card) {
    if (!card) return;
    scene.querySelectorAll(".course-card").forEach((c) => {
      const on = c === card;
      c.classList.toggle("is-selected", on);
      const btn = c.querySelector("button.btn[data-action=confirm]");
      if (!btn) return;
      btn.classList.toggle("primary", on);
      btn.classList.toggle("secondary", !on);
      if (on) btn.style.removeProperty("background");
      else btn.style.background = "rgba(242,239,232,.08)";
      const label = btn.querySelector(":scope > span:first-child");
      if (label) label.textContent = on ? "Selected" : "Choose";
    });
  }
  function syncCourseHighlight(el) {
    if (!state || state.screen !== "S06") return;
    const card = el && el.closest ? el.closest(".course-card") : null;
    if (!card || card.classList.contains("is-selected")) return;
    paintCourseSelection(card);
    const idx = parseInt(card.getAttribute("data-index"), 10);
    if (!Number.isNaN(idx)) RG.send({ t: "action", a: "select", index: idx });
  }
  function focusSelectedCourse() {
    const card = scene.querySelector(".course-card.is-selected") || scene.querySelector(".course-card");
    if (!card) return false;
    activate(card);
    return true;
  }
  function courseArrow(dir) {
    scene.classList.remove("mouse-nav");
    const cards = Array.from(scene.querySelectorAll(".course-card"));
    if (!cards.length) { moveFocus2d(dir.dx || 0, dir.dy || 0); return; }
    const ae = document.activeElement;
    const cur = (ae && ae.closest && ae.closest(".course-card")) || cards.find((c) => c.classList.contains("is-selected"));
    let i = cards.indexOf(cur);
    if (i < 0) i = 0;
    const step = dir.dx || dir.dy || 1;
    activate(cards[(i + step + cards.length) % cards.length]);
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
  function focusPauseRow(st) {
    const ui = (st || state || {}).ui || {};
    const idx = ui.focus != null ? ui.focus : 0;
    const row = scene.querySelector(`button[data-action="select"][data-index="${idx}"]`);
    if (!row) return false;
    activate(row);
    return true;
  }
  function pauseArrow(dir) {
    scene.classList.remove("mouse-nav");
    const ae = document.activeElement;
    const onCard = ae && ae.getAttribute && String(ae.getAttribute("data-action") || "").startsWith("recal_");
    const flyout = state && state.ui && (state.ui.recal_flyout || state.ui.focus === 3);
    if (flyout && onCard) {
      moveFocus2d(dir.dx || 0, dir.dy || 0);
      return;
    }
    if (flyout && !onCard && (dir.dx > 0 || dir.dy > 0)) {
      const card = scene.querySelector("[data-action^=recal_]");
      if (card) { activate(card); return; }
    }
    RG.send({ t: "action", a: (dir.dy > 0 || dir.dx > 0) ? "down" : "up" });
  }
  function focusFirst() {
    if (state && state.screen === "S06" && focusSelectedCourse()) return;
    if (state && state.screen === "S15" && focusPauseRow(state)) return;
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
      e.preventDefault();
      if (isText || isSelect) { ae.blur(); return; }
      RG.send({ t: "action", a: "back" });
      return;
    }
    if (isText) return;
    if (isSelect && (key === "ArrowDown" || key === "ArrowUp" || key === "Enter" || key === " ")) return;
    if (isRange && (key === "ArrowLeft" || key === "ArrowRight")) return;

    // Movement / focus — spatial on arrows, document order on Tab
    if (key === "ArrowDown" || key === "ArrowUp" || key === "ArrowRight" || key === "ArrowLeft") {
      e.preventDefault();
      scene.classList.remove("mouse-nav");
      if (state && state.screen === "S06") {
        courseArrow({
          dx: key === "ArrowRight" ? 1 : key === "ArrowLeft" ? -1 : 0,
          dy: key === "ArrowDown" ? 1 : key === "ArrowUp" ? -1 : 0,
        });
        return;
      }
      if (state && state.screen === "S15") {
        pauseArrow({
          dx: key === "ArrowRight" ? 1 : key === "ArrowLeft" ? -1 : 0,
          dy: key === "ArrowDown" ? 1 : key === "ArrowUp" ? -1 : 0,
        });
        return;
      }
      if (key === "ArrowDown") moveFocus2d(0, 1);
      else if (key === "ArrowUp") moveFocus2d(0, -1);
      else if (key === "ArrowRight") moveFocus2d(1, 0);
      else moveFocus2d(-1, 0);
      return;
    }
    if (key === "Tab") { e.preventDefault(); scene.classList.remove("mouse-nav"); moveFocus(e.shiftKey ? -1 : 1); return; }
    if (key === "PageDown") { e.preventDefault(); scrollPane(1); return; }
    if (key === "PageUp") { e.preventDefault(); scrollPane(-1); return; }

    // Activate the focused control, else send a generic confirm.
    if (key === "Enter" || key === " ") {
      if (ae && ae.classList && ae.classList.contains("course-card")) {
        e.preventDefault();
        const btn = ae.querySelector("[data-action=confirm]");
        if (btn) btn.click();
        else RG.send({ t: "action", a: "confirm", index: parseInt(ae.getAttribute("data-index"), 10) });
        return;
      }
      if (ae && ae !== document.body && (ae.tagName === "BUTTON" || (ae.hasAttribute && ae.hasAttribute("data-action")))) {
        e.preventDefault();
        ae.click();
      } else {
        RG.send({ t: "action", a: "confirm" });
      }
      return;
    }

    // Pause extras (INPUT.md): F fix · R recalibrate · C course · M music · Q quit
    const screen = state && state.screen;
    if (screen === "S15") {
      if (key === "b") { RG.send({ t: "action", a: "resume" }); return; }
      if (key === "x") { RG.send({ t: "action", a: "undo" }); return; }
      if (key === "f") { RG.send({ t: "action", a: "fix" }); return; }
      if (key === "r") { RG.send({ t: "action", a: "recalibrate" }); return; }
      if (key === "c") { RG.send({ t: "action", a: "course" }); return; }
      if (key === "m") { RG.send({ t: "action", a: "music" }); return; }
      if (key === "q") { RG.send({ t: "action", a: "quit" }); return; }
    }
    if ((screen === "S07c" || screen === "S07b") && (key === "Delete" || key === "Backspace")) {
      RG.send({ t: "action", a: "remove_corner" });
      return;
    }

    // Global shortcuts (work regardless of focus)
    if (key === "z" || key === "r") { RG.send({ t: "action", a: "undo" }); return; }
    if (key === "y" || key === "d") { RG.send({ t: "action", a: "secondary" }); return; }
    if (key === "f") { toggleFullscreen(); return; }
    if (key === "m") { RG.send({ t: "action", a: "menu" }); return; }
    if (key === "+" || key === "=") { RG.send({ t: "action", a: "grow" }); return; }
    if (key === "-" || key === "_") { RG.send({ t: "action", a: "shrink" }); return; }
  });

  function toggleFullscreen() {
    if (!document.fullscreenElement) document.documentElement.requestFullscreen().catch(() => {});
    else document.exitFullscreen();
  }

  let screenAudioT = [];
  function cueScreenAudio(screen, st) {
    screenAudioT.forEach((id) => clearTimeout(id));
    screenAudioT = [];
    const later = (fn, ms) => { screenAudioT.push(setTimeout(fn, ms)); };
    if (screen === "S12") {
      RG.audio.sfx("sting");
      const name = st.ui && st.ui.player && st.ui.player.name;
      later(() => RG.audio.announce(name), 300);
    }
    if (screen === "S13") {
      RG.audio.sfx("plink");
      later(() => RG.audio.sfx("cheer"), 200);
      const p = st.ui && st.ui.player;
      const n = st.ui && st.ui.strokes;
      if (p) RG.audio.announce(`${p.name} holed in ${n}`);
    }
  }

  function rumbleFor(screen) {
    if (!(RG.settings && RG.settings.controller && RG.settings.controller.rumble)) return;
    const spec = screen === "S13" ? [400, 1] : screen === "S12" ? [120, 0.45] : null;
    if (!spec) return;
    try {
      const pads = navigator.getGamepads ? navigator.getGamepads() : [];
      for (const p of pads) {
        if (p && p.vibrationActuator) {
          p.vibrationActuator.playEffect("dual-rumble", {
            duration: spec[0], strongMagnitude: spec[1], weakMagnitude: spec[1] * 0.55,
          });
        }
      }
    } catch (err) { /* ignore */ }
  }

  // ---- gamepad (same semantic verbs as keyboard) ----
  const PAD_DEAD = 0.25;
  const PAD_FIRST = 350;
  const PAD_REPEAT = 90;
  const padHeld = {};
  let stickReady = true;
  let stickNextAt = 0;
  window.addEventListener("mousemove", () => scene.classList.add("mouse-nav"));

  function padEdge(buttons, i) {
    const b = buttons[i];
    const down = !!(b && (b.pressed || b.value > 0.5));
    const was = !!padHeld[i];
    padHeld[i] = down;
    return down && !was;
  }

  function dispatchPad(verb) {
    scene.classList.remove("mouse-nav");
    if (verb === "confirm") {
      const ae = document.activeElement;
      if (ae && ae.classList && ae.classList.contains("course-card")) {
        const btn = ae.querySelector("[data-action=confirm]");
        if (btn) btn.click();
        else RG.send({ t: "action", a: "confirm", index: parseInt(ae.getAttribute("data-index"), 10) });
        return;
      }
      if (ae && scene.contains(ae) && (ae.tagName === "BUTTON" || (ae.hasAttribute && ae.hasAttribute("data-action")))) {
        ae.click();
      } else {
        RG.send({ t: "action", a: "confirm" });
      }
      return;
    }
    if (verb === "next" || verb === "prev") {
      const screen = state && state.screen;
      if (screen === "S15") {
        pauseArrow({ dx: 0, dy: verb === "next" ? 1 : -1 });
        return;
      }
      if (screen === "S06" || screen === "S07" || screen === "S09" || screen === "S19") {
        RG.send({ t: "action", a: verb });
        return;
      }
      moveFocus(verb === "next" ? 1 : -1);
      return;
    }
    RG.send({ t: "action", a: verb });
  }

  function pollPad() {
    requestAnimationFrame(pollPad);
    let pad = null;
    try {
      const pads = navigator.getGamepads ? navigator.getGamepads() : [];
      for (const p of pads) {
        if (p && p.buttons && p.buttons.length >= 8) { pad = p; break; }
      }
    } catch (err) { return; }
    const mode = pad ? "controller" : "keyboard";
    if (mode !== RG.inputMode) {
      RG.inputMode = mode;
      if (state) {
        lastSignature = "";
        onState(state);
      }
    }
    if (!pad) return;
    const btns = pad.buttons;
    if (padEdge(btns, 0)) dispatchPad("confirm");
    if (padEdge(btns, 1)) dispatchPad("back");
    if (padEdge(btns, 2)) dispatchPad("undo");
    if (padEdge(btns, 3)) dispatchPad("secondary");
    if (padEdge(btns, 4)) dispatchPad("prev");
    if (padEdge(btns, 5)) dispatchPad("next");
    if (padEdge(btns, 8) || padEdge(btns, 9)) dispatchPad("menu");

    const now = performance.now();
    let dx = 0;
    let dy = 0;
    if (btns[14] && btns[14].pressed) dx = -1;
    else if (btns[15] && btns[15].pressed) dx = 1;
    if (btns[12] && btns[12].pressed) dy = -1;
    else if (btns[13] && btns[13].pressed) dy = 1;
    const ax = pad.axes[0] || 0;
    const ay = pad.axes[1] || 0;
    if (!dx && Math.abs(ax) > PAD_DEAD) dx = ax > 0 ? 1 : -1;
    if (!dy && Math.abs(ay) > PAD_DEAD) dy = ay > 0 ? 1 : -1;
    if (dx || dy) {
      scene.classList.remove("mouse-nav");
      const step = () => {
        if (state && state.screen === "S15") pauseArrow({ dx, dy });
        else if (state && state.screen === "S06") courseArrow({ dx, dy });
        else moveFocus2d(dx, dy);
      };
      if (stickReady) {
        step();
        stickReady = false;
        stickNextAt = now + PAD_FIRST;
      } else if (now >= stickNextAt) {
        step();
        stickNextAt = now + PAD_REPEAT;
      }
    } else {
      stickReady = true;
    }
    const ry = pad.axes.length > 3 ? pad.axes[3] : 0;
    if (Math.abs(ry) > PAD_DEAD) scrollPane(ry > 0 ? 1 : -1);
  }
  requestAnimationFrame(pollPad);

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
