// Camera feed rendering + overlay SVG + pointer capture.
// The feed is a full-bleed <canvas>; the overlay is an <svg> on top. Shapes are
// in normalized (0..1) coordinates and are scaled to the element on resize.
window.RG = window.RG || {};

(function () {
  let wrap = null, canvas = null, ctx = null, svg = null;
  let pointerDown = false;
  let lastShapes = [];

  function ensure() {
    const scene = document.getElementById("scene");
    if (wrap) return;
    wrap = document.createElement("div");
    wrap.className = "feed-wrap hidden";
    wrap.innerHTML = `<canvas class="feed"></canvas><svg class="overlay" xmlns="http://www.w3.org/2000/svg"></svg>`;
    scene.appendChild(wrap);
    canvas = wrap.querySelector("canvas");
    svg = wrap.querySelector("svg");
    ctx = canvas.getContext("2d");

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

  function size() {
    const r = wrap.getBoundingClientRect();
    canvas.width = r.width; canvas.height = r.height;
    svg.setAttribute("viewBox", `0 0 1 1`);
    svg.setAttribute("width", r.width);
    svg.setAttribute("height", r.height);
  }

  RG.feed = {
    show(v) {
      ensure();
      wrap.classList.toggle("hidden", !v);
      if (v) size();
    },
    draw(bytes) {
      ensure();
      if (canvas.classList.contains("hidden") || !wrap) return;
      if (!bytes) { canvas.style.display = "none"; return; }
      canvas.style.display = "block";
      if ("createImageBitmap" in window) {
        createImageBitmap(new Blob([bytes], { type: "image/jpeg" }))
          .then((bmp) => { if (bmp.width !== canvas.width) size(); ctx.drawImage(bmp, 0, 0, canvas.width, canvas.height); bmp.close(); })
          .catch(() => {});
      } else {
        const img = new Image();
        const url = URL.createObjectURL(new Blob([bytes], { type: "image/jpeg" }));
        img.onload = () => { if (img.width !== canvas.width) size(); ctx.drawImage(img, 0, 0, canvas.width, canvas.height); URL.revokeObjectURL(url); };
        img.src = url;
      }
    },
    overlay(shapes) {
      ensure();
      lastShapes = shapes || [];
      renderSvg(lastShapes);
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
        if (s.label) addLabel(s.pts && s.pts[0], s.label, s.stroke || "#fff", "poly");
      } else if (s.type === "circle") {
        const c = svgEl("circle", {
          cx: s.x, cy: s.y, r: s.r,
          fill: s.fill || "none",
          stroke: s.stroke || "#fff",
          "stroke-width": (s.stroke_width || 3) * 0.0015,
          "stroke-dasharray": s.dash || "",
        });
        svg.appendChild(c);
        if (s.label) addLabel([s.x, s.y], s.label, s.stroke || "#fff", "circle");
      }
    }
  }

  function addLabel(pos, text, color, kind) {
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
