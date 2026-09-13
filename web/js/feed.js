// Camera feed rendering + overlay SVG + pointer capture.
// The feed canvas lives outside screen HTML so it survives innerHTML rebuilds.
// Screens may host it full-bleed on #scene or dock it into [data-feed-slot].
window.RG = window.RG || {};

(function () {
  const HANDLE_PX = 36;
  let wrap = null, canvas = null, ctx = null, svg = null, htmlLayer = null;
  let pointerDown = false;
  let lastShapes = [];
  let lastOverlayKey = "";
  let wired = false;
  let moveRaf = 0;
  let pendingMove = null;
  let drawGen = 0;
  let dragHandle = null;
  let dragX = 0;
  let dragY = 0;

  function ensure() {
    if (wrap) return;
    wrap = document.createElement("div");
    wrap.className = "feed-wrap hidden";
    wrap.innerHTML = `<canvas class="feed"></canvas><svg class="overlay" xmlns="http://www.w3.org/2000/svg"></svg><div class="overlay-html"></div>`;
    canvas = wrap.querySelector("canvas");
    svg = wrap.querySelector("svg");
    htmlLayer = wrap.querySelector(".overlay-html");
    ctx = canvas.getContext("2d");
    if (typeof ResizeObserver !== "undefined") {
      new ResizeObserver(() => size()).observe(wrap);
    }
    if (wired) return;
    wired = true;
    const norm = (e) => {
      const r = wrap.getBoundingClientRect();
      const w = r.width || 1;
      const h = r.height || 1;
      return { x: (e.clientX - r.left) / w, y: (e.clientY - r.top) / h };
    };
    const send = (type, e, handle) => {
      const p = norm(e);
      const msg = { t: "pointer", type, x: Math.min(1, Math.max(0, p.x)), y: Math.min(1, Math.max(0, p.y)) };
      if (handle != null) msg.handle = handle;
      if (e && e.shiftKey) msg.shift = true;
      RG.send(msg);
    };
    const flushMove = () => {
      moveRaf = 0;
      if (!pendingMove || !pointerDown) { pendingMove = null; return; }
      const e = pendingMove;
      pendingMove = null;
      const p = norm(e);
      dragX = Math.min(1, Math.max(0, p.x));
      dragY = Math.min(1, Math.max(0, p.y));
      if (dragHandle != null) applyLocalCorner(dragHandle, dragX, dragY);
      send("move", e, dragHandle);
    };
    const handleAt = (e) => {
      const r = wrap.getBoundingClientRect();
      const px = e.clientX - r.left;
      const py = e.clientY - r.top;
      let best = null, bestD = HANDLE_PX;
      for (const s of lastShapes) {
        const id = s.id || "";
        if (s.type !== "circle") continue;
        const parsed = parseHandleId(id);
        if (parsed == null) continue;
        const d = Math.hypot(px - s.x * r.width, py - s.y * r.height);
        if (d <= bestD) {
          bestD = d;
          best = parsed;
        }
      }
      return best;
    };
    const endDrag = (type, e) => {
      if (moveRaf) { cancelAnimationFrame(moveRaf); flushMove(); }
      send(type, e, dragHandle);
      pointerDown = false;
      dragHandle = null;
      wrap.style.cursor = "";
    };
    wrap.addEventListener("pointerdown", (e) => {
      if (e.button != null && e.button !== 0) return;
      e.preventDefault();
      pointerDown = true;
      try { wrap.setPointerCapture(e.pointerId); } catch (_) {}
      dragHandle = handleAt(e);
      const p = norm(e);
      dragX = p.x;
      dragY = p.y;
      wrap.style.cursor = dragHandle != null ? "grabbing" : "crosshair";
      send("down", e, dragHandle);
    });
    wrap.addEventListener("pointermove", (e) => {
      if (!pointerDown) {
        wrap.style.cursor = handleAt(e) != null ? "grab" : "crosshair";
        return;
      }
      pendingMove = e;
      if (!moveRaf) moveRaf = requestAnimationFrame(flushMove);
    });
    wrap.addEventListener("pointerup", (e) => endDrag("up", e));
    wrap.addEventListener("pointercancel", (e) => endDrag("up", e));
  }

  function parseHandleId(id) {
    const o = /^o(\d+)c(\d+)$/.exec(id);
    if (o) return `o:${o[1]}:${o[2]}`;
    const g = /^g(\d+)c(\d+)$/.exec(id);
    if (g) return `${g[1]}:${g[2]}`;
    if (id.startsWith("corner")) {
      const n = parseInt(id.slice(6), 10);
      return Number.isFinite(n) ? n : null;
    }
    return null;
  }

  function applyLocalCorner(handle, x, y) {
    let circleId = null, polyId = null, idx = null;
    if (typeof handle === "string" && handle.startsWith("o:")) {
      const parts = handle.split(":");
      circleId = `o${parts[1]}c${parts[2]}`;
      polyId = null;
      idx = parseInt(parts[2], 10);
      for (const s of lastShapes) {
        if (s.type === "polygon" && s.id && String(s.id).startsWith("obstacle_")) {
          if (s.pts && s.pts[idx]) { s.pts[idx] = [x, y]; }
        }
        if (s.id === circleId) { s.x = x; s.y = y; }
      }
      lastOverlayKey = "";
      renderSvg(lastShapes);
      return;
    }
    if (typeof handle === "string" && handle.includes(":")) {
      const [gi, ci] = handle.split(":");
      circleId = `g${gi}c${ci}`;
      polyId = `ghost${gi}`;
      idx = parseInt(ci, 10);
    } else if (handle != null) {
      circleId = `corner${handle}`;
      polyId = "play_area_draft";
      idx = handle;
    }
    if (idx == null || !Number.isFinite(idx)) return;
    let changed = false;
    for (const s of lastShapes) {
      if (circleId && s.id === circleId) {
        s.x = x;
        s.y = y;
        changed = true;
      }
      if (polyId && s.id === polyId && s.pts && s.pts[idx]) {
        s.pts[idx] = [x, y];
        changed = true;
      }
    }
    if (!changed) return;
    lastOverlayKey = "";
    renderSvg(lastShapes);
  }

  function mount(slot) {
    ensure();
    const scene = document.getElementById("scene");
    const parent = slot || scene;
    if (!parent) return;
    if (wrap.parentNode !== parent) parent.insertBefore(wrap, parent.firstChild);
    wrap.classList.toggle("slotted", !!slot);
    const interactive = !slot || slot.hasAttribute("data-feed-interactive");
    wrap.classList.toggle("interactive", interactive);
  }

  function size() {
    if (!wrap || !wrap.isConnected) return;
    const w = Math.max(1, wrap.clientWidth);
    const h = Math.max(1, wrap.clientHeight);
    if (canvas.width !== w || canvas.height !== h) {
      canvas.width = w;
      canvas.height = h;
    }
    svg.setAttribute("viewBox", "0 0 1 1");
    svg.setAttribute("preserveAspectRatio", "none");
  }

  RG.feed = {
    detach() {
      if (wrap && wrap.parentNode) wrap.parentNode.removeChild(wrap);
    },
    show(v, slot) {
      ensure();
      if (!v) {
        wrap.classList.add("hidden");
        return;
      }
      mount(slot || null);
      wrap.classList.remove("hidden");
      requestAnimationFrame(size);
    },
    draw(bytes) {
      if (!wrap || !wrap.isConnected || wrap.classList.contains("hidden") || !ctx) return;
      if (!bytes) return;
      const blob = new Blob([bytes], { type: "image/jpeg" });
      const gen = ++drawGen;
      if ("createImageBitmap" in window) {
        createImageBitmap(blob)
          .then((bmp) => {
            if (gen !== drawGen || !wrap.isConnected || wrap.classList.contains("hidden")) { bmp.close(); return; }
            ctx.drawImage(bmp, 0, 0, canvas.width, canvas.height);
            bmp.close();
          })
          .catch(() => {});
      } else {
        const img = new Image();
        const url = URL.createObjectURL(blob);
        img.onload = () => {
          if (gen === drawGen && wrap.isConnected && !wrap.classList.contains("hidden")) {
            ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
          }
          URL.revokeObjectURL(url);
        };
        img.src = url;
      }
    },
    overlay(shapes) {
      ensure();
      lastShapes = (shapes || []).map((s) => {
        const copy = { ...s };
        if (s.pts) copy.pts = s.pts.map((p) => [p[0], p[1]]);
        return copy;
      });
      if (dragHandle != null) applyLocalCorner(dragHandle, dragX, dragY);
      const key = JSON.stringify(lastShapes);
      if (key === lastOverlayKey) return;
      lastOverlayKey = key;
      if (svg) renderSvg(lastShapes);
    },
    shapes() { return lastShapes; },
  };

  function svgEl(tag, attrs) {
    const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const k in attrs) el.setAttribute(k, attrs[k]);
    return el;
  }

  function renderSvg(shapes) {
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    if (htmlLayer) htmlLayer.innerHTML = "";
    for (const s of shapes) {
      if (s.type === "html") {
        addHtmlLabel(s);
        continue;
      }
      if (s.type === "polygon" || s.type === "polyline") {
        const pts = (s.pts || []).map((p) => `${p[0]},${p[1]}`).join(" ");
        const p = svgEl(s.type === "polyline" ? "polyline" : "polygon", {
          points: pts,
          fill: s.fill || "none",
          stroke: s.stroke || "#fff",
          "stroke-opacity": s.stroke_opacity != null ? s.stroke_opacity : 1,
          "stroke-width": (s.stroke_width || 3) * 0.0015,
          "stroke-dasharray": s.dash || "",
          "stroke-linejoin": "round",
          "stroke-linecap": "round",
        });
        if (s.opacity != null) p.setAttribute("opacity", s.opacity);
        if (s.class) p.setAttribute("class", s.class);
        svg.appendChild(p);
        if (s.label) addLabel(s.pts && s.pts[0], s.label, s.stroke || "#fff");
      } else if (s.type === "label") {
        addLabel([s.x, s.y], s.text || s.label || "", s.fill || "#f2efe8", s);
      } else if (s.type === "circle") {
        const c = svgEl("circle", {
          cx: s.x, cy: s.y, r: s.r,
          fill: s.fill || "none",
          stroke: s.stroke || "none",
          "stroke-width": (s.stroke_width || 3) * 0.0015,
          "stroke-dasharray": s.dash || "",
        });
        if (s.opacity != null) c.setAttribute("opacity", s.opacity);
        if (s.class) c.setAttribute("class", s.class);
        svg.appendChild(c);
        if (s.label) addLabel([s.x, s.y], s.label, s.fill === "#8be9c3" ? "#8be9c3" : "#f2efe8");
      }
    }
  }

  function addHtmlLabel(s) {
    if (!htmlLayer || !s.text) return;
    const el = document.createElement("div");
    el.className = s.class || "overlay-outline-label";
    el.textContent = s.text;
    el.style.left = (s.x * 100) + "%";
    el.style.top = (s.y * 100) + "%";
    if (s.color) el.style.color = s.color;
    htmlLayer.appendChild(el);
  }

  function addLabel(pos, text, color, opts) {
    if (!pos || text == null || text === "") return;
    const o = opts || {};
    const placed = o.type === "label";
    const t = svgEl("text", {
      x: pos[0],
      y: placed ? pos[1] : Math.max(0.024, pos[1] - 0.028),
      "font-family": "Outfit, sans-serif",
      "font-size": String(o.size || 0.022),
      fill: color,
      "font-weight": "700",
      "text-anchor": o.anchor || "middle",
    });
    t.textContent = text;
    svg.appendChild(t);
  }

  window.addEventListener("resize", () => { if (wrap && !wrap.classList.contains("hidden")) size(); });
})();
