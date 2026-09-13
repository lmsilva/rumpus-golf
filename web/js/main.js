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
    document.documentElement.dataset.theme = st.settings && st.settings.theme !== "auto" ? st.settings.theme : "dark";
    RG.audio.setFromSettings();

    const screen = st.screen;
    const sig = signature(st);
    const rebuild = screen !== lastScreen || sig !== lastSignature;

    if (rebuild) {
      scene.innerHTML = RG.screens.render(st);
      lastScreen = screen;
      lastSignature = sig;
      wire();
      applyMarquee();
      focusFirst();
    }
    RG.feed.show(FEED_SCREENS.has(screen));
    RG.feed.overlay((st.overlay && st.overlay.shapes) || []);
    handleMusic(st);
  }

  function signature(st) {
    // Rebuild HUD only when meaningful text changes (avoid per-frame flicker).
    if (st.screen !== "S11") return "";
    const g = st.game || {};
    const a = st.ui && st.ui.active_player;
    const m = st.ui && st.ui.motion;
    const others = (st.ui.others || []).map((p) => `${p.id}:${(g.scores[p.id] || [])[g.hole - 1] || 0}`).join(",");
    return [a && a.id, a && a.name, g.hole, (g.scores[a && a.id] || [])[g.hole - 1] || 0, m && m.moving, m && m.hidden, m && m.dist_to_cup, others].join("|");
  }

  function handleMusic(st) {
    if (!RG.settings || !RG.settings.music || RG.settings.music.enabled === false) { RG.audio.stopMusic(); return; }
    if (st.state === "PAUSE" || st.state === "SETTINGS") { RG.audio.duck(0.3); return; }
    RG.audio.duck(1);
    const play = PLAY_SCREENS.has(st.screen);
    RG.audio.music(play ? "BackedVibesClean" : "LobbyTime");
  }

  // ---- input wiring (event delegation) ----
  function wire() {
    scene.querySelectorAll("[data-action]").forEach((el) => {
      el.addEventListener("click", (e) => {
        const a = el.getAttribute("data-action");
        if (a === "settings-tab") return;
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
      el.addEventListener("click", () => RG.send({ t: "set", key: "settings.theme", value: el.getAttribute("data-set-theme") })));
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

    scene.querySelectorAll("[data-set-musicvol]").forEach((el) =>
      el.addEventListener("input", () => RG.send({ t: "set", key: "settings.music.volume", value: parseInt(el.value, 10) / 100 })));
    scene.querySelectorAll("[data-set-sfxvol]").forEach((el) =>
      el.addEventListener("input", () => RG.send({ t: "set", key: "settings.sfx.volume", value: parseInt(el.value, 10) / 100 })));

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
      if (el.disabled) return false;
      const r = el.getBoundingClientRect();
      return r.width > 0 && r.height > 0;
    });
  }
  function moveFocus(delta) {
    const els = focusables();
    if (!els.length) return;
    const idx = els.indexOf(document.activeElement);
    const next = idx < 0 ? (delta > 0 ? 0 : els.length - 1) : (idx + delta + els.length) % els.length;
    els[next].focus({ preventScroll: true });
    try { els[next].scrollIntoView({ block: "nearest" }); } catch (err) {}
  }
  function focusFirst() {
    const els = focusables();
    const first = els.find((el) => el.tagName === "BUTTON");
    if (first) first.focus({ preventScroll: true });
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
    const typing = ae && (ae.tagName === "INPUT" || ae.tagName === "TEXTAREA" || ae.tagName === "SELECT");
    if (typing) return; // let the input handle its own keys

    const key = e.key;

    // Movement / focus
    if (key === "ArrowDown" || key === "ArrowRight") { e.preventDefault(); moveFocus(1); return; }
    if (key === "ArrowUp" || key === "ArrowLeft") { e.preventDefault(); moveFocus(-1); return; }
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
    if (key === "Escape") { RG.send({ t: "action", a: "back" }); return; }
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
    scene.style.transform = `scale(${s})`;
  }
  window.addEventListener("resize", fit);
  fit();

  connect();
})();
