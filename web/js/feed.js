// Camera feed rendering + overlay SVG + pointer capture.
// The feed canvas lives outside screen HTML so it survives innerHTML rebuilds.
// Screens may host it full-bleed on #scene or dock it into [data-feed-slot].
window.RG = window.RG || {};

(function () {
  let wrap = null, canvas = null, ctx = null, svg = null;
  let pointerDown = false;
  let lastShapes = [];
  let wired = false;

  function ensure() {
    if (wrap) return;
    wrap = document.createElement("div");
    wrap.className = "feed-wrap hidden";
    wrap.innerHTML = `<canvas class="feed"></canvas><svg class="overlay" xmlns="http://www.w3.org/2000/svg"></svg>`;
    canvas = wrap.querySelector("canvas");
    svg = wrap.querySelector("svg");
    ctx = canvas.getContext("2d");
    if (wired) return;
    wired = true;
    const norm = (e) => {
      const r = wrap.getBoundingClientRect();
      return { x: (e.clientX - r.left) / r.width, y: (e.clientY - r.top) / r.height };
    };
    const send = (type, e) => {
      const p = norm(e);
      RG.send({ t: "pointer", type, x: Math.min(1, Math.max(0, p.x)), y: Math.min(1, Math.max(0, p.y)) });
    };
    wrap.addEventListener("pointerdown", (e) => { pointerDown = true; wrap.setPointerCapture(e.pointerId); send("down", e); });
    wrap.addEventListener("pointermove", (e) => { if (pointerDown) send("move", e); });
    wrap.addEventListener("pointerup", (e) => { send("up", e); pointerDown = false; });
    wrap.addEventListener("pointercancel", () => { pointerDown = false; });
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
    const r = wrap.getBoundingClientRect();
    const w = Math.max(1, Math.round(r.width));
    const h = Math.max(1, Math.round(r.height));
    if (canvas.width !== w || canvas.height !== h) {
      canvas.width = w;
      canvas.height = h;
    }
    svg.setAttribute("viewBox", "0 0 1 1");
    svg.setAttribute("width", String(w));
    svg.setAttribute("height", String(h));
  }

  RG.feed = {
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
      if ("createImageBitmap" in window) {
        createImageBitmap(blob)
          .then((bmp) => {
            if (!wrap.isConnected || wrap.classList.contains("hidden")) { bmp.close(); return; }
            size();
            ctx.drawImage(bmp, 0, 0, canvas.width, canvas.height);
            bmp.close();
          })
          .catch(() => {});
      } else {
        const img = new Image();
        const url = URL.createObjectURL(blob);
        img.onload = () => {
          if (wrap.isConnected && !wrap.classList.contains("hidden")) {
            size();
            ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
          }
          URL.revokeObjectURL(url);
        };
        img.src = url;
      }
    },
    overlay(shapes) {
      ensure();
      lastShapes = shapes || [];
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
    for (const s of shapes) {
      if (s.type === "polygon") {
        const pts = (s.pts || []).map((p) => `${p[0]},${p[1]}`).join(" ");
        const p = svgEl("polygon", {
          points: pts,
          fill: s.fill || "none",
          stroke: s.stroke || "#fff",
          "stroke-opacity": s.stroke_opacity != null ? s.stroke_opacity : 1,
          "stroke-width": (s.stroke_width || 3) * 0.0015,
          "stroke-dasharray": s.dash || "",
          "stroke-linejoin": "round",
        });
        svg.appendChild(p);
        if (s.label) addLabel(s.pts && s.pts[0], s.label, s.stroke || "#fff");
      } else if (s.type === "circle") {
        const c = svgEl("circle", {
          cx: s.x, cy: s.y, r: s.r,
          fill: s.fill || "none",
          stroke: s.stroke || "#fff",
          "stroke-width": (s.stroke_width || 3) * 0.0015,
          "stroke-dasharray": s.dash || "",
        });
        svg.appendChild(c);
        if (s.label) addLabel([s.x, s.y], s.label, s.stroke || "#fff");
      }
    }
  }

  function addLabel(pos, text, color) {
    const t = svgEl("text", {
      x: pos[0], y: Math.max(0.02, pos[1] - 0.02),
      "font-family": "Outfit, sans-serif", "font-size": "0.018",
      fill: color, "font-weight": "700", "text-anchor": "middle",
    });
    t.textContent = text;
    svg.appendChild(t);
  }

  window.addEventListener("resize", () => { if (wrap && !wrap.classList.contains("hidden")) size(); });
})();
