// Screen templates. Each returns an HTML string; main.js injects it into #scene.
window.RG = window.RG || {};

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}
function dot(color, size = 16) {
  return `<span class="dot" style="width:${size}px;height:${size}px;background:${color}"></span>`;
}
function topbar(subtitle, opts = {}) {
  return `<div class="topbar">
    <span class="brand">Rumpus Golf</span>
    ${subtitle ? `<span class="subtitle">${esc(subtitle)}</span>` : ""}
    ${opts.rail || ""}
    <span class="right">${RG.badge()}${opts.back !== false ? `<span class="hint">${RG.glyph("back")}<span>Back</span></span>` : ""}</span>
  </div>`;
}
function stepRail(current) {
  const steps = ["Floor", "Play area", "Course", "Cup", "Balls & players"];
  return `<span class="steprail">${steps.map((s, i) => {
    const cls = i === current - 1 ? "current" : (i < current - 1 ? "done" : "");
    return `<span class="step ${cls}"><span class="n">${i < current - 1 ? "✓" : i + 1}</span>${s}</span>`;
  }).join("")}</span>`;
}
function photo(name, filter, scrim, credit) {
  const bg = `linear-gradient(160deg, #2a2e36 0%, #1a1c22 60%, #14161a 100%)`;
  return `<div class="photo" style="background-image:url('/assets/photos/${name}.jpg'),${bg};background-blend-mode:normal;filter:${filter || "none"}">
    <div class="scrim" style="background:${scrim || "transparent"}"></div>
  </div>`;
}
function marquee(text) {
  return `<span class="marquee" data-marquee><span>${esc(text)}</span></span>`;
}

// ---------------------------------------------------------------------------
const S = {};

S.S01 = function (st) {
  const ui = st.ui || {};
  const menu = ui.menu || [];
  const items = [
    { label: "New game", action: "new_game", cls: "primary", verb: "confirm" },
    { label: "Load last setup", action: "load", cls: "secondary", verb: "confirm", meta: ui.saved_meta || "" },
    { label: "Course library", action: "library", cls: "secondary", verb: "confirm" },
    { label: "Settings", action: "settings", cls: "secondary", verb: "confirm" },
  ];
  return `
  ${photo("s01", "saturate(.75) brightness(.8)", "linear-gradient(90deg,#15171c 0 780px,rgba(21,23,28,.85) 980px,rgba(21,23,28,.15) 1500px)")}
  <div style="position:absolute;inset:0;padding:72px 96px;display:flex;flex-direction:column">
    <div class="row" style="gap:12px">
      ${dot("#ff8a3d", 28)}${dot("#ff5fa8", 28)}${dot("#5b8cff", 28)}${dot("#ffd84d", 28)}
      <span style="margin-left:auto">${RG.badge()}</span>
    </div>
    <div style="margin-top:auto">
      <h1 style="font:800 200px/1 var(--font-display);letter-spacing:-.05em">Rumpus<br>Golf</h1>
      <div style="font:400 32px/1.3 var(--font-body);color:var(--text-muted);margin-top:28px;max-width:720px">Turn any floor into a mini golf course.</div>
    </div>
    <div class="col" style="margin-top:56px;max-width:560px;gap:12px">
      ${items.map((it) => `<button class="btn ${it.cls}" data-action="${it.action}"><span>${esc(it.label)}</span>${it.meta ? `<span class="meta">${esc(it.meta)}</span>` : ""}${it.verb ? RG.btnHint(it.verb) : ""}</button>`).join("")}
    </div>
  </div>
  <div class="pill" style="position:absolute;right:56px;bottom:48px">
    <span class="dot" style="background:var(--mint)"></span>${esc((st.sensor && st.sensor.model) || "Kinect v1")} connected
    <span class="text-muted">· Lobby Time — Kevin MacLeod</span>
  </div>`;
};

S.S02 = function (st) {
  const d = st.ui && st.ui.sensor;
  const facts = d ? [
    ["Depth", `${d.depth_res[0]} × ${d.depth_res[1]}`],
    ["Color", `${d.color_res[0]} × ${d.color_res[1]}`],
    ["Field of view", `${d.fov_h_deg}°`],
    ["Reliable range", `${d.reliable_min_m} – ${d.reliable_max_m} m`],
    ["Note", d.note || ""],
  ] : [];
  const noSensor = !d;
  return `
  ${topbar("New game")}
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:32px;padding:56px">
    <div class="card" style="padding:56px 64px;display:flex;flex-direction:column">
      <div class="kicker mint">Sensor</div>
      ${noSensor
        ? `<div style="margin-top:14px"><h2 style="font-size:80px">No camera<br>found.</h2></div>`
        : `<h2 style="font-size:80px;line-height:1;margin:14px 0 36px">${esc(d.model)}<br>found.</h2>`}
      ${noSensor
        ? `<div style="margin-top:auto;background:rgba(255,107,87,.12);border:1px solid rgba(255,107,87,.4);border-radius:18px;padding:24px 28px;display:flex;gap:18px">
             <span class="dot" style="background:var(--coral);margin-top:8px"></span>
             <div><div style="font:700 22px var(--font-display)">Error state — no sensor</div>
             <div style="font:400 17px/1.45 var(--font-body);color:var(--text-muted);margin-top:4px">No camera found. Plug in a Kinect v1 or v2 and press Retry. Nothing else is reachable until a sensor answers.</div>
             <button class="btn primary sm" style="margin-top:16px" data-action="retry"><span>Retry</span></button></div>
           </div>`
        : `<div style="display:grid;grid-template-columns:auto 1fr;gap:14px 40px;font:400 23px/1.3 var(--font-body)">
             ${facts.map(([k, v]) => `<span class="text-muted">${esc(k)}</span><span>${esc(v)}</span>`).join("")}
           </div>`}
    </div>
    <div class="col" style="gap:20px">
      <div style="background:var(--mint);color:var(--mint-text);border-radius:24px;padding:40px 44px;flex:1;display:flex;flex-direction:column">
        <div style="font:800 40px var(--font-display)">Load saved setup</div>
        <div style="font:400 21px/1.45 var(--font-body);margin-top:8px;opacity:.8">Living room · 3 courses · 4 balls<br>saved recently</div>
        <span style="margin-top:auto;display:inline-flex;align-items:center;gap:8px;font:600 16px var(--font-body)">
          <button class="btn primary" data-action="load"><span>Load</span>${RG.btnHint("confirm")}</button>
        </span>
      </div>
      <div style="position:relative;overflow:hidden;border-radius:24px;flex:1;display:flex;flex-direction:column">
        ${photo("s02", "saturate(.7)", "linear-gradient(90deg,rgba(21,23,28,.92) 0%,rgba(21,23,28,.75) 55%,rgba(21,23,28,.35) 100%)")}
        <div style="position:relative;padding:40px 44px;flex:1;display:flex;flex-direction:column">
          <div style="font:800 40px var(--font-display)">New calibration</div>
          <div style="font:400 21px/1.45 var(--font-body);margin-top:8px;color:var(--text-soft)">Five steps, about 3 minutes. Clear the floor first.</div>
          <span style="margin-top:auto"><button class="btn secondary" data-action="fresh"><span>Start fresh</span>${RG.btnHint("secondary")}</button></span>
        </div>
      </div>
    </div>
  </div>`;
};

S.S03 = function (st) {
  const setup = st.setup || {};
  return `
  <div class="glass" style="position:absolute;left:56px;top:56px;max-width:900px;border-radius:24px;padding:32px 40px;z-index:3">
    <div class="kicker mint">Verify · ${esc(setup.name || "Living room")}</div>
    <h2 style="font-size:60px;line-height:1;margin:10px 0 16px">Do the lines still sit on the floor?</h2>
    <div style="font:400 21px/1.45 var(--font-body);color:var(--text-soft)">White = play area, start and the obstacles you confirmed. Mint = hole. If only something moved, Recalibrate lets you redo just that — cup, obstacles or zones.</div>
  </div>
  <div class="pill" style="position:absolute;right:56px;top:56px;z-index:3">LIVE · ${st.feed.w} × ${st.feed.h} · 30 fps</div>
  <div class="row" style="position:absolute;left:56px;bottom:48px;gap:14px;z-index:3">
    <button class="btn primary" data-action="confirm" style="min-width:400px"><span>Looks right</span>${RG.btnHint("confirm")}</button>
    <button class="btn glass" data-action="recalibrate"><span>Recalibrate</span>${RG.btnHint("undo")}</button>
  </div>`;
};

S.S04 = function (st) {
  return `
  ${topbar("Step 1 of 5 · Floor", { rail: stepRail(1) })}
  <div style="display:grid;grid-template-columns:1fr 520px;gap:24px;padding:32px 56px 48px;height:calc(100% - 96px)">
    <div style="position:relative;background:#242629;border-radius:24px;overflow:hidden">
      <div style="position:absolute;top:0;left:0;right:0;height:230px;background:rgba(13,15,18,.6);z-index:2"></div>
      <div style="position:absolute;bottom:0;left:0;right:0;height:80px;background:rgba(13,15,18,.6);z-index:2"></div>
      <div style="position:absolute;top:230px;left:0;right:0;height:3px;background:var(--mint);z-index:3"></div>
      <div style="position:absolute;bottom:80px;left:0;right:0;height:3px;background:var(--mint);z-index:3"></div>
      <div style="position:absolute;top:120px;left:32px;z-index:3;font:600 30px var(--font-body)">4.0 m — too far…</div>
      <div style="position:absolute;bottom:20px;left:32px;z-index:3;font:600 30px var(--font-body)">0.8 m — too close</div>
      <div style="position:absolute;top:290px;left:32px;z-index:3;font:700 30px var(--font-display);color:var(--mint)">Good floor — fit a course inside this band</div>
    </div>
    <div class="card" style="padding:44px;display:flex;flex-direction:column">
      <div class="kicker mint">Step 1 of 5</div>
      <h2 style="font-size:56px;line-height:1;margin:14px 0 16px">Clear the floor, capture it.</h2>
      <div style="font:400 21px/1.45 var(--font-body);color:var(--text-muted)">Move everything off the floor, then capture. The app averages 2 seconds of depth and fits the floor plane.</div>
      <div style="margin-top:auto;display:flex;flex-direction:column;gap:12px">
        <button class="btn primary" data-action="confirm"><span>${st.setup.capturing ? "Capturing…" : "Capture floor"}</span>${RG.btnHint("confirm")}</button>
        ${st.setup.capturing ? `<div style="height:4px;background:var(--fill-quiet);border-radius:999px;overflow:hidden"><div style="height:100%;width:100%;background:var(--mint);animation:rg-progress 2s linear"></div></div>` : ""}
      </div>
    </div>
  </div>
  <style>@keyframes rg-progress{from{width:0}to{width:100%}}</style>`;
};

S.S05 = function (st) {
  return `
  ${topbar("Step 2 of 5 · Play area", { rail: stepRail(2) })}
  <div style="display:grid;grid-template-columns:1fr 520px;gap:24px;padding:32px 56px 48px;height:calc(100% - 96px)">
    <div style="position:relative;background:#242629;border-radius:24px;overflow:hidden"></div>
    <div class="card" style="padding:44px;display:flex;flex-direction:column">
      <div class="kicker mint">Step 2 of 5</div>
      <h2 style="font-size:56px;line-height:1;margin:14px 0 16px">Draw the play area.</h2>
      <div style="font:400 21px/1.45 var(--font-body);color:var(--text-muted)">Click corners on the feed to trace the boundary, then close the shape. Presets give a starting rectangle.</div>
      <div class="seg" style="margin:20px 0;flex-wrap:wrap">
        ${(st.ui.presets || ["small", "medium", "large"]).map((p) => `<button class="opt" data-set-preset="${p}">${p === "small" ? "Small 2 × 1.5 m" : p === "medium" ? "Medium 3 × 2 m" : "Large 4 × 2.5 m"}</button>`).join("")}
      </div>
      <div style="margin-top:auto;display:flex;flex-direction:column;gap:12px">
        <button class="btn primary" data-action="confirm"><span>Use this area</span>${RG.btnHint("confirm")}</button>
        <button class="btn secondary" data-action="clear"><span>Clear corners</span>${RG.btnHint("undo")}</button>
      </div>
    </div>
  </div>
  <div class="hints">${RG.hint("stick", "Move corner")}${RG.hint("confirm", "Place")}${RG.hint("undo", "Undo corner")}${RG.hint("secondary", "Close shape")}</div>`;
};

S.S06 = function (st) {
  const courses = st.ui.courses || [];
  const cur = st.game && st.game.course_id;
  return `
  ${photo("s06", "saturate(.8)", "linear-gradient(180deg,rgba(21,23,28,.4) 0%,rgba(21,23,28,.92) 60%)")}
  ${topbar("Step 3 of 5 · Course", { rail: stepRail(3), back: false })}
  <div style="position:relative;padding:40px 56px 48px;display:flex;flex-direction:column;gap:20px;height:calc(100% - 96px)">
    <div><h2 style="font-size:60px;line-height:1">Pick a course for hole 1.</h2>
    <div style="font:400 21px var(--font-body);color:var(--text-muted)">Three templates authored for a 3.0 × 2.0 m play area.</div></div>
    <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:20px;flex:1">
      ${courses.map((c, i) => {
        const sel = c.id === cur;
        return `<div class="card" data-action="select" data-index="${i}" style="text-align:left;cursor:pointer;${sel ? "background:var(--text);color:var(--player-text);border:2px solid var(--text)" : "border:2px solid transparent"}">
          <div class="row" style="justify-content:space-between">
            <div><div class="kicker" style="opacity:.7">${esc(c.level)}</div>
            <div style="font:800 40px/1 var(--font-display)">${esc(c.name)}</div></div>
            <span class="pill" style="${sel ? "background:var(--player-text);color:var(--text)" : ""}">Par ${c.par}</span>
          </div>
          <div style="margin:16px 0;aspect-ratio:3/2;border-radius:16px;background:${sel ? "#e3e0d8" : "#15171c"};position:relative;overflow:hidden">
            ${courseMap(c, sel)}
          </div>
          <div style="font:400 18px/1.4 var(--font-body);opacity:.8">${esc(c.blurb)}</div>
          <div class="kicker" style="margin-top:14px">You will need</div>
          <div style="font:400 17px/1.4 var(--font-body);margin-top:6px">${c.needs.map(esc).join(" · ")}</div>
          <div class="row" style="margin-top:16px"><button class="btn ${sel ? "primary" : "secondary"} sm" data-action="confirm" data-index="${i}" style="${sel ? "" : "background:rgba(242,239,232,.08)"}"><span>${sel ? "Selected" : "Choose"}</span>${RG.btnHint("confirm")}</button></div>
        </div>`;
      }).join("")}
    </div>
  </div>`;
};

function courseMap(c, sel) {
  const fg = sel ? "#15171c" : "#f2efe8";
  const obs = c.obstacles || [];
  let s = `<span style="position:absolute;left:${c.start.x}%;top:${c.start.y}%;width:11%;aspect-ratio:1;transform:translate(-50%,-50%);border:2px solid ${fg};border-radius:50%"></span>`;
  s += `<span style="position:absolute;left:${c.hole.x}%;top:${c.hole.y}%;width:7%;aspect-ratio:1;transform:translate(-50%,-50%);background:var(--mint);border-radius:50%"></span>`;
  for (const o of obs) {
    const fill = o.kind === "hazard" ? "repeating-linear-gradient(45deg,#ff6b57 0 4px,#b9483a 4px 8px)"
      : o.kind === "book" ? "#f2efe8" : o.kind === "tube" ? "#c9c4b8" : "#6b7280";
    s += `<span style="position:absolute;left:${o.x}%;top:${o.y}%;width:${o.w}%;height:${o.h}%;background:${fill};border-radius:2px"></span>`;
  }
  return s;
}

S.S07 = function (st) {
  const ui = st.ui || {};
  const course = ui.course || {};
  const ghosts = ui.ghosts || [];
  const rebuilding = st.state === "HOLE_START";
  return `
  <div class="glass-strong" style="position:absolute;right:40px;top:56px;bottom:56px;width:480px;border-radius:24px;padding:36px;z-index:3;display:flex;flex-direction:column">
    <div class="kicker">${esc(course.level || "")} · Par ${course.par || ""}</div>
    <h2 style="font-size:48px;line-height:1;margin:8px 0 10px">${rebuilding ? `Rebuild hole ${st.game.hole}.` : `Build “${esc(course.name || "")}”.`}</h2>
    <div style="font:400 17px/1.45 var(--font-body);color:var(--text-muted)">Put each object roughly inside its ghost — the ticks are a hint. When everything is down, scan: the app measures what’s really on the floor and you confirm it.</div>
    <div class="col" style="margin-top:20px;gap:8px;overflow:auto">
      ${ghosts.map((g) => `<div class="row" style="background:var(--fill-quiet);border-radius:14px;padding:14px 16px">
        <span style="width:30px;height:30px;border-radius:50%;background:var(--mint);display:grid;place-items:center;color:var(--mint-text);font:700 18px var(--font-display)">✓</span>
        <div style="flex:1"><div style="font:700 19px var(--font-display)">${esc(g.label)}</div><div style="font:400 14px var(--font-body);color:var(--text-muted)">${esc(g.item)}</div></div>
        <span style="font:600 14px var(--font-body);color:var(--text-muted)">${(g.real_size_cm || []).join("×")} cm</span>
      </div>`).join("")}
    </div>
    <div class="col" style="margin-top:auto;gap:10px">
      <button class="btn primary" data-action="confirm"><span>Scan obstacles</span>${RG.btnHint("confirm")}</button>
      <button class="btn secondary" data-action="prev"><span>Swap course</span>${RG.btnHint("prev")}</button>
    </div>
  </div>
  <div class="glass" style="position:absolute;left:56px;top:56px;padding:16px 24px;border-radius:16px;z-index:3"><span class="brand" style="font:700 22px var(--font-display)">Rumpus Golf</span> <span class="text-muted" style="font:400 17px var(--font-body)">Step 3 of 5 · ${esc(course.name || "")}</span></div>
  <div class="hints">${RG.hint("stick", "Move")}${RG.hint("confirm", "Grab / drop")}${RG.hint("undo", "Rotate 90°")}${RG.hint("secondary", "Reset")}${RG.hint("next", "Next object")}</div>`;
};

S["S07b"] = function (st) {
  const obs = st.ui.obstacles || [];
  const flagged = obs.filter((o) => o.state === "proposed" && o.confidence < 0.7);
  return `
  <div class="glass-strong" style="position:absolute;right:40px;top:56px;bottom:56px;width:520px;border-radius:24px;padding:36px;z-index:3;display:flex;flex-direction:column">
    <div class="kicker mint">Step 3 of 5 · Obstacles</div>
    <h2 style="font-size:48px;line-height:1;margin:8px 0 10px">Found ${obs.length} objects.</h2>
    <div style="font:400 17px/1.45 var(--font-body);color:var(--text-muted)">Outlines are approximate on purpose — dashed = proposed, not yet on the course.</div>
    <div class="col" style="margin-top:20px;gap:8px;overflow:auto">
      ${obs.map((o, i) => `<div class="row" style="background:${o.confidence < 0.7 ? "rgba(255,107,87,.1)" : "var(--fill-quiet)"};border:${o.confidence < 0.7 ? "1px solid rgba(255,107,87,.35)" : "none"};border-radius:14px;padding:12px 14px">
        <span style="width:30px;height:30px;border-radius:50%;background:${o.confidence < 0.7 ? "var(--coral)" : "var(--mint)"};display:grid;place-items:center;color:var(--mint-text);font:800 16px var(--font-display)">${i + 1}</span>
        <div style="flex:1;min-width:0"><div class="nowrap" style="font:700 19px/1.3 var(--font-display)">${esc(o.label)}</div><div class="text-muted nowrap" style="font:400 14px var(--font-body)">${esc(o.kind)}</div></div>
        <span class="pill" style="font-size:13px;background:${o.confidence >= 0.7 ? "var(--mint)" : "var(--coral)"};color:var(--mint-text)">${Math.round(o.confidence * 100)}%</span>
      </div>`).join("")}
    </div>
    <div class="col" style="margin-top:auto;gap:10px">
      <button class="btn primary" data-action="confirm_all" ${flagged.length ? "disabled" : ""}><span>Confirm all</span>${RG.btnHint("confirm")}</button>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
        <button class="btn secondary" data-action="redetect"><span>Re-detect</span>${RG.btnHint("next")}</button>
        <button class="btn secondary" data-action="draw"><span>Draw one</span>${RG.btnHint("secondary")}</button>
      </div>
      <div style="font:400 14px var(--font-body);color:var(--text-muted)">Low-confidence outlines must be confirmed or deleted before Confirm all.</div>
    </div>
  </div>
  <div class="glass" style="position:absolute;left:56px;top:56px;padding:16px 24px;border-radius:16px;z-index:3"><span class="brand" style="font:700 22px var(--font-display)">Rumpus Golf</span> <span class="text-muted">Step 3 of 5 · Obstacles</span></div>
  <div class="pill" style="position:absolute;left:56px;top:150px;z-index:3"><span class="dot" style="background:var(--mint)"></span>Scanned 1.5 s of depth · dashed = proposed, not yet on the course</div>
  <div class="hints">${RG.hint("stick", "Move cursor")}${RG.hint("confirm", "Select object")}${RG.hint("undo", "Delete")}${RG.hint("secondary", "Draw one")}${RG.hint("prev", "Undo")}</div>`;
};

S["S07c"] = function (st) {
  const obs = st.ui.obstacles || [];
  const sel = st.ui.selected;
  return `
  <div class="glass-strong" style="position:absolute;right:40px;top:56px;bottom:56px;width:520px;border-radius:24px;padding:36px;z-index:3;display:flex;flex-direction:column">
    <div class="kicker mint">Object ${(sel != null ? sel + 1 : 1)} · ${sel != null ? esc((obs[sel] || {}).label || "") : ""}</div>
    <h2 style="font-size:48px;line-height:1;margin:8px 0 10px">Fix the outline.</h2>
    <div style="font:400 17px/1.45 var(--font-body);color:var(--text-muted)">Drag a corner to resize. Click an edge to add a corner; Delete removes one.</div>
    <div class="col" style="margin-top:20px;gap:8px;overflow:auto">
      ${obs.map((o, i) => `<div class="row" style="background:var(--fill-quiet);border-radius:14px;padding:12px 14px">
        <span style="width:28px;height:28px;border-radius:50%;background:var(--fill-quiet);display:grid;place-items:center;font:800 15px var(--font-display)">${i + 1}</span>
        <div style="flex:1"><div style="font:700 19px var(--font-display)">${esc(o.label)}</div></div>
        <span class="pill" style="font-size:13px">${esc(o.state)}</span>
      </div>`).join("")}
    </div>
    <div class="col" style="margin-top:auto;gap:10px">
      <button class="btn primary" data-action="confirm"><span>Confirm this outline</span>${RG.btnHint("confirm")}</button>
      <button class="btn secondary" data-action="confirm" ${obs.some((o) => o.state === "proposed" || o.state === "drawing") ? "disabled" : ""}><span>Course is set</span></button>
    </div>
  </div>
  <div class="hints">${RG.hint("stick", "Drag corner")}${RG.hint("confirm", "Confirm this one")}${RG.hint("undo", "Delete")}${RG.hint("secondary", "Add corner")}${RG.hint("prev", "Undo")}</div>`;
};

S.S08 = function (st) {
  const searching = st.ui.searching;
  return `
  <div class="glass" style="position:absolute;left:56px;top:56px;max-width:800px;border-radius:24px;padding:32px 40px;z-index:3">
    <div class="kicker mint">Step 4 of 5 · Cup</div>
    <h2 style="font-size:60px;line-height:1;margin:10px 0 14px">${searching ? "Place the putting cup inside the play area." : "Found the cup."}</h2>
    ${searching ? `<div style="height:4px;background:var(--fill-quiet);border-radius:999px;overflow:hidden"><div style="height:100%;width:40%;background:var(--mint);animation:rg-slide 1.5s linear infinite"></div></div>` : `<div style="font:400 21px/1.45 var(--font-body);color:var(--text-soft)">The white ring is detected. Adjust by dragging if it’s off.</div>`}
  </div>
  <div class="row" style="position:absolute;left:56px;bottom:48px;gap:14px;z-index:3">
    <button class="btn primary" data-action="confirm"><span>That’s the hole</span>${RG.btnHint("confirm")}</button>
    <button class="btn glass" data-action="redetect"><span>Detect again</span>${RG.btnHint("undo")}</button>
    <button class="btn glass" data-action="draw"><span>Draw it myself</span>${RG.btnHint("secondary")}</button>
  </div>
  <style>@keyframes rg-slide{from{transform:translateX(-100%)}to{transform:translateX(250%)}}</style>`;
};

S.S09 = function (st) {
  const balls = st.ui.balls || [];
  const players = st.ui.players || [];
  return `
  <div style="position:absolute;left:56px;top:56px;z-index:3"><h2 style="font:800 48px var(--font-display)">Put every ball on the floor.</h2></div>
  <div class="glass-strong" style="position:absolute;right:40px;top:56px;bottom:56px;width:640px;border-radius:24px;padding:36px;z-index:3;display:flex;flex-direction:column">
    <div class="kicker">${balls.length} balls found · 0 rejected</div>
    <h2 style="font-size:48px;line-height:1;margin:8px 0 10px">Who’s who?</h2>
    <div style="font:400 17px/1.45 var(--font-body);color:var(--text-muted)">Each ball is sampled for its hue. Give each player a name.</div>
    <div class="col" style="margin-top:20px;gap:10px;overflow:auto">
      ${players.map((p, i) => `<div class="row" style="background:var(--fill-quiet);border-radius:16px;padding:10px 12px 10px 16px">
        <span style="width:20px;font:700 16px var(--font-display);color:var(--text-muted)">${i + 1}</span>
        ${dot(p.color, 48)}
        <input class="player-name" data-index="${i}" value="${esc(p.name)}" style="background:var(--player-text);color:var(--text);border:1px solid var(--line-strong);border-radius:12px;padding:12px 16px;font:700 22px var(--font-display);width:220px">
        <span style="font:400 14px var(--font-body);color:var(--text-muted)">${esc(p.hue_name)}</span>
        <span style="margin-left:auto;color:var(--text-muted)">⋮⋮</span>
      </div>`).join("")}
      <div class="row" style="border:1px dashed rgba(242,239,232,.2);border-radius:16px;padding:16px;color:var(--text-muted);font:400 17px var(--font-body)">Room for two more balls — max 6 players.</div>
    </div>
    <div class="col" style="margin-top:auto;gap:10px">
      <button class="btn primary" data-action="confirm"><span>Save setup & continue</span>${RG.btnHint("confirm")}</button>
      <div style="font:400 14px var(--font-body);color:var(--text-muted)">Saved to rumpus-setup.json.</div>
    </div>
  </div>`;
};

S.S10 = function (st) {
  const players = st.ui.players || [];
  const holes = st.ui.holes;
  const cap = st.ui.stroke_cap;
  return `
  ${photo("s10", "saturate(.75) brightness(.8)", "linear-gradient(90deg,rgba(21,23,28,.95) 0%,rgba(21,23,28,.35) 48%,rgba(21,23,28,.6) 100%)")}
  <div style="position:relative;display:grid;grid-template-columns:1fr 760px;gap:32px;padding:56px;height:100%">
    <div class="col" style="gap:20px">
      <div class="kicker mint">Setup saved · Living room</div>
      <h2 style="font:800 112px/1 var(--font-display);letter-spacing:-.045em">Game /<br>night.</h2>
      <div>
        <div class="kicker">Holes this game</div>
        <div class="seg" style="max-width:760px;margin-top:10px">
          ${[1,2,3,4,5,6,7,8,9].map((n) => `<button class="opt ${n === holes ? "sel" : ""}" data-set-holes="${n}">${n}</button>`).join("")}
        </div>
      </div>
      <div>
        <div class="kicker">Stroke cap</div>
        <div class="row" style="margin-top:10px">
          <button class="btn secondary" style="width:56px;height:56px;padding:0;justify-content:center" data-set-cap="${cap - 1}">−</button>
          <span style="font:700 28px var(--font-display);min-width:120px;text-align:center">${cap} strokes</span>
          <button class="btn secondary" style="width:56px;height:56px;padding:0;justify-content:center" data-set-cap="${cap + 1}">+</button>
        </div>
      </div>
      <div style="margin-top:auto"><button class="btn primary lg" data-action="confirm"><span>Tee off</span>${RG.btnHint("confirm")}</button></div>
    </div>
    <div class="col" style="gap:12px">
      <div class="kicker">Tee-off order · ${players.length} players</div>
      ${players.map((p, i) => `<div style="background:${p.color};color:var(--player-text);border-radius:24px;padding:0 40px;flex:1;display:flex;align-items:center;gap:24px">
        <span style="font:800 64px var(--font-display);opacity:.45">${i + 1}</span>
        <span style="font:800 60px/1.2 var(--font-display);flex:1;overflow:hidden">${marquee(p.name)}</span>
        <span style="font:600 18px var(--font-body);letter-spacing:.12em;opacity:.7">${esc((p.hue_name || "").toUpperCase())} BALL</span>
      </div>`).join("")}
    </div>
  </div>`;
};

S.S11 = function (st) {
  const ui = st.ui || {};
  const a = ui.active_player;
  const others = ui.others || [];
  const course = ui.course || {};
  const motion = ui.motion || {};
  const scores = ui.scores || {};
  const finished = ui.finished_hole || {};
  const activeStrokes = a ? (scores[a.id] || [])[st.game.hole - 1] || 0 : 0;
  const status = !a ? "" : motion.moving ? "Ball rolling…" : motion.hidden ? "Stopped, hidden — position estimated" : activeStrokes === 0 ? "Place your ball in the start zone" : "Ball stopped. Putt when ready.";
  return `
  <div style="position:absolute;inset:40px;z-index:3;pointer-events:none;display:flex;flex-direction:column">
    <div class="row" style="gap:16px;align-items:stretch">
      ${a ? `<div style="background:${a.color};color:var(--player-text);border-radius:24px;padding:26px 40px;box-shadow:var(--shadow-card);display:flex;align-items:center;gap:36px">
        <div><div class="kicker" style="opacity:.7;color:var(--player-text)">Your turn</div><div style="font:800 76px/1.15 var(--font-display);overflow:hidden">${marquee(a.name)}</div></div>
        <div style="width:2px;align-self:stretch;background:rgba(21,23,28,.25)"></div>
        <div><div class="kicker" style="opacity:.7;color:var(--player-text)">Stroke</div><div style="font:800 76px/1.15 var(--font-display)">${activeStrokes}</div></div>
        <div style="width:2px;align-self:stretch;background:rgba(21,23,28,.25)"></div>
        <div style="font:600 26px/1.25 var(--font-body);max-width:420px;opacity:.85">${esc(status)}</div>
      </div>` : ""}
      <div class="row" style="margin-left:auto;gap:10px;align-items:stretch">
        ${others.map((p) => `<div class="glass" style="border-radius:20px;padding:20px 24px;min-width:190px">
          <div class="row" style="gap:8px">${dot(p.color, 16)}<div style="font:700 24px/1.25 var(--font-display);max-width:150px;overflow:hidden">${marquee(p.name)}</div></div>
          <div style="font:400 18px var(--font-body);color:var(--text-muted)">${finished[p.id] ? `Holed · ${(scores[p.id] || [])[st.game.hole - 1] || 0}` : `Stroke ${(scores[p.id] || [])[st.game.hole - 1] || 0} · in play`}</div>
        </div>`).join("")}
      </div>
    </div>
    <div class="glass" style="position:absolute;left:0;bottom:0;border-radius:20px;padding:18px 24px;pointer-events:auto">
      <div class="kicker">Hole ${st.game.hole} of ${st.game.holes} · ${esc(course.level || "")} · Par ${course.par || ""}</div>
      <div style="font:800 34px/1 var(--font-display)">${esc(course.name || "")}</div>
    </div>
    <div class="row" style="position:absolute;right:0;bottom:0;gap:10px;pointer-events:auto">
      ${RG.hint("undo", "Undo last shot")}${RG.hint("menu", "Pause")}
    </div>
    <div class="pill" style="position:absolute;right:0;top:230px;pointer-events:auto">
      ${dot(a ? a.color : "#fff", 10)}${motion.moving ? "Ball moving" : motion.hidden ? "Ball stopped, hidden · position estimated" : motion.dist_to_cup != null ? `Ball stopped · ${motion.dist_to_cup} m from cup` : "Ball stopped"}
    </div>
  </div>`;
};

S.S12 = function (st) {
  const p = st.ui.player || {};
  const stroke = st.ui.stroke;
  return `
  <div style="position:absolute;inset:0;background:rgba(21,23,28,.4);z-index:4"></div>
  <div style="position:absolute;left:0;top:0;bottom:0;width:1240px;background:${p.color};color:var(--player-text);border-radius:0 120px 120px 0;box-shadow:40px 0 120px rgba(0,0,0,.4);animation:rg-slidein-left .35s ease-out;display:flex;flex-direction:column;justify-content:flex-end;padding:0 96px 96px;z-index:5">
    <div class="kicker" style="color:var(--player-text);opacity:.65">Next up</div>
    <div style="font:800 230px/1.15 var(--font-display);letter-spacing:-.05em;overflow:hidden">${marquee(p.name)}</div>
    <div style="font:600 32px/1.3 var(--font-body);opacity:.8">${stroke === 0 ? `Place the ${esc(p.hue_name || "")} ball in the start zone` : `Play the ${esc(p.hue_name || "")} ball where it lies · stroke ${stroke}`}</div>
  </div>`;
};

S.S13 = function (st) {
  const p = st.ui.player || {};
  const n = st.ui.strokes;
  const par = st.ui.par;
  const diff = n - par;
  const sub = diff === 0 ? "On par" : diff < 0 ? "Under par — birdie!" : "Over par, still counts";
  return `
  <div style="position:absolute;left:40px;top:40px;right:40px;background:${p.color};color:var(--player-text);border-radius:28px;padding:48px 64px;box-shadow:0 30px 80px rgba(0,0,0,.4);display:flex;align-items:center;gap:40px;z-index:5;animation:rg-slideup .4s ease-out">
    <div style="font:800 200px/1 var(--font-display);letter-spacing:-.05em">In!</div>
    <div><div style="font:800 64px/1.05 var(--font-display)">${esc(p.name)} holed in ${n}.</div>
    <div style="font:600 30px var(--font-body);opacity:.7">${sub} · Par ${par}</div></div>
  </div>
  <div style="position:absolute;left:56px;bottom:48px;z-index:5">${RG.hint("undo", "Wrong call? Undo within 5 s — the ball goes back in play at the cup.")}</div>`;
};

S.S14 = function (st) {
  const p = st.ui.player || {};
  return `
  <div class="row" style="position:absolute;inset:40px;z-index:3;align-items:flex-start;gap:16px">
    ${p ? `<div style="background:${p.color};color:var(--player-text);border-radius:24px;padding:26px 40px;display:flex;align-items:center;gap:36px">
      <div><div class="kicker" style="color:var(--player-text);opacity:.7">Your turn</div><div style="font:800 76px/1.15 var(--font-display)">${esc(p.name)}</div></div>
      <div><div class="kicker" style="color:var(--player-text);opacity:.7">Stroke</div><div style="font:800 76px/1.15 var(--font-display)">${st.ui.strokes} + 1</div></div>
    </div>` : ""}
    <div style="background:var(--coral);color:#15171c;border-radius:24px;padding:26px 40px">
      <div class="kicker" style="color:#15171c;opacity:.7">Out of bounds</div>
      <div style="font:800 52px/1 var(--font-display)">+1 penalty</div>
    </div>
  </div>
  <div class="glass" style="position:absolute;left:40px;bottom:40px;right:40px;border-radius:24px;padding:22px 28px;z-index:3;display:flex;align-items:center;gap:16px">
    ${dot(p.color, 20)}<span style="font:700 28px var(--font-display)">Replace the ball at the exit point.</span>
    <span style="font:400 21px var(--font-body);color:var(--text-muted)">Exit point — put the ball back here, then press A.</span>
    <span style="margin-left:auto"><button class="btn primary sm" data-action="confirm"><span>Replaced</span>${RG.btnHint("confirm")}</button></span>
  </div>`;
};

S.S15 = function (st) {
  const ui = st.ui || {};
  const focus = ui.focus || 0;
  const rows = [
    ["Resume", "resume", "back"],
    ["Undo last shot", "undo", "undo"],
    ["Fix score", "fix", "confirm"],
    ["Recalibrate…", "recalibrate", "confirm"],
    ["Change course for this hole", "course", "confirm"],
    ["Music & sound", "music", "confirm"],
    ["Quit to start", "quit", "confirm"],
  ];
  const active = ui.active || {};
  const scores = ui.scores || {};
  return `
  ${photo("s15", "saturate(.75) brightness(.7)", "linear-gradient(90deg,rgba(21,23,28,.55) 0%,rgba(21,23,28,.2) 45%,rgba(21,23,28,.75) 100%)")}
  <div class="glass-strong" style="position:absolute;left:40px;top:40px;bottom:40px;width:680px;border-radius:28px;padding:48px;z-index:3;display:flex;flex-direction:column">
    <div class="kicker muted">Hole ${ui.hole} · ${esc(active.name || "")} to play</div>
    <h2 style="font-size:88px;line-height:1">Paused.</h2>
    <div class="col" style="margin-top:24px;gap:8px">
      ${rows.map(([label, key, verb], i) => `<button class="row" data-action="select" data-index="${i}" style="border-radius:16px;padding:18px 22px;font:700 26px var(--font-display);background:${i === focus ? "var(--mint)" : "var(--fill-quiet)"};color:${i === focus ? "var(--mint-text)" : "var(--text)"};border:none;text-align:left;cursor:pointer">
        <span style="flex:1">${esc(label)}</span>${RG.glyph(verb)}
      </button>`).join("")}
    </div>
    <div style="margin-top:auto;font:400 15px var(--font-body);color:var(--text-muted)">Music ducks to 30% while paused.</div>
  </div>
  <div class="glass" style="position:absolute;right:40px;top:40px;width:520px;border-radius:24px;padding:28px 32px;z-index:3">
    <div class="kicker">Hole ${ui.hole} of 3 · Par 3</div>
    <div style="font:800 34px/1 var(--font-display);margin:4px 0 14px">${esc(st.game && st.game.course ? st.game.course.name : "")}</div>
    ${(st.game.players || []).map((p) => `<div class="row" style="padding:6px 0">
      ${dot(p.color, 16)}<span style="font:700 22px/1.25 var(--font-display);flex:1">${esc(p.name)}</span>
      <span style="font:800 26px var(--font-display)">${(scores[p.id] || [])[ui.hole - 1] || 0}</span>
    </div>`).join("")}
  </div>
  <div class="pill" style="position:absolute;right:40px;bottom:40px;z-index:3"><span class="dot" style="background:var(--mint)"></span>Lobby Time — Kevin MacLeod · ducked to 30%</div>`;
};

S.S16 = function (st) {
  const players = st.ui.players || [];
  const scores = st.game.scores || {};
  const hole = st.game.hole;
  return `
  <div style="display:grid;grid-template-columns:1fr 700px;gap:32px;padding:56px;height:100%">
    <div class="col" style="gap:14px">
      <div><div class="kicker mint">Fix score · Hole ${hole}</div><h2 style="font-size:68px;line-height:1">Strokes this hole</h2></div>
      ${players.map((p) => `<div class="card" style="border-radius:20px;padding:18px 24px;display:flex;align-items:center;gap:16px">
        ${dot(p.color, 44)}
        <span style="font:700 36px/1.25 var(--font-display);width:240px;overflow:hidden">${marquee(p.name)}</span>
        <div class="row" style="margin-left:auto">
          <button class="btn secondary" style="width:56px;height:56px;padding:0;justify-content:center" data-set-stepper="${p.id}" data-delta="-1">−</button>
          <span style="font:800 40px var(--font-display);min-width:100px;text-align:center">${(scores[p.id] || [])[hole - 1] || 0}</span>
          <button class="btn secondary" style="width:56px;height:56px;padding:0;justify-content:center" data-set-stepper="${p.id}" data-delta="1">+</button>
        </div>
      </div>`).join("")}
      <div class="row" style="margin-top:auto;gap:14px">
        <button class="btn primary" data-action="confirm" style="min-width:340px"><span>Apply</span>${RG.btnHint("confirm")}</button>
        <button class="btn secondary" data-action="back"><span>Cancel</span>${RG.btnHint("back")}</button>
      </div>
    </div>
    <div class="card" style="padding:40px">
      <div class="kicker">Event log · newest first</div>
      <div style="background:var(--text);color:var(--player-text);border-radius:18px;padding:20px 24px;margin-top:16px">
        <div class="kicker" style="color:var(--player-text);opacity:.7">Undo last shot</div>
        <div style="font:700 24px var(--font-display)">${esc((st.game.event_log || []).slice(-1)[0] ? "Most recent stroke" : "No events yet")}</div>
      </div>
      <div class="col" style="margin-top:16px;gap:0">
        ${(st.game.event_log || []).slice().reverse().slice(0, 12).map((e) => `<div class="row" style="padding:14px 0;border-bottom:1px solid var(--line)">
          <span style="font:600 14px var(--font-body);color:var(--text-muted);width:52px">${e.hole}</span>
          ${dot("#fff", 12)}<span style="font:400 18px var(--font-body)">${esc(e.type)}</span>
        </div>`).join("")}
      </div>
    </div>
  </div>`;
};

S.S17 = function (st) {
  const sc = st.ui.scorecard || { players: [], holes: [], pars: [] };
  const lead = st.ui.leading;
  const next = st.ui.next_course;
  return `
  <div style="display:grid;grid-template-columns:1fr 560px;gap:32px;padding:56px;height:100%">
    <div class="col" style="gap:18px">
      <div><div class="kicker mint">Hole ${st.game.hole} of ${st.game.holes} done</div><h2 style="font-size:80px;line-height:1">Scorecard</h2></div>
      <div class="card" style="padding:12px 32px;flex:1;overflow:auto">
        <table style="width:100%;border-collapse:collapse;font:400 28px var(--font-body)">
          <tr style="border-bottom:1px solid var(--line)">
            <th style="text-align:left;padding:10px 0;font:600 13px var(--font-body);letter-spacing:.18em;text-transform:uppercase;color:var(--text-muted)"></th>
            ${sc.holes.map((h) => `<th style="text-align:center;padding:10px 0;font:600 13px var(--font-body);letter-spacing:.18em;color:var(--text-muted)">${h}</th>`).join("")}
            <th style="text-align:center;color:var(--mint)">Total</th>
          </tr>
          ${sc.players.map((p) => `<tr style="border-bottom:1px solid var(--line)">
            <td style="padding:12px 0">${dot(p.color, 26)} <span style="font:700 30px var(--font-display);max-width:260px;overflow:hidden;display:inline-block;vertical-align:middle">${marquee(p.name)}</span></td>
            ${sc.holes.map((h) => { const v = p.scores[h - 1]; const over = v != null && sc.pars[h - 1] != null && v > sc.pars[h - 1]; return `<td style="text-align:center;${over ? "color:var(--coral)" : ""}">${v == null ? "○" : v}</td>`; }).join("")}
            <td style="text-align:center;font:800 30px var(--font-display)">${p.total}</td>
          </tr>`).join("")}
        </table>
      </div>
      <div class="row" style="gap:14px">
        <button class="btn primary" data-action="confirm" style="min-width:440px"><span>${st.ui.is_last ? "See results" : `Build hole ${st.ui.next_hole}`}</span>${RG.btnHint("confirm")}</button>
        <button class="btn secondary" data-action="replay"><span>Replay this hole</span></button>
        <button class="btn secondary" data-action="fix"><span>Fix a score</span></button>
      </div>
    </div>
    <div class="col" style="gap:20px">
      ${lead ? `<div style="background:${lead.color};color:var(--player-text);border-radius:24px;padding:40px">
        <div class="kicker" style="color:var(--player-text);opacity:.65">Leading after ${st.game.hole}</div>
        <div style="font:800 72px/1.2 var(--font-display)">${esc(lead.name)}</div>
        <div style="font:600 22px var(--font-body);opacity:.75">${lead.total} strokes · ${lead.vs_par}</div>
      </div>` : ""}
      ${next ? `<div style="position:relative;overflow:hidden;border-radius:24px;flex:1;display:flex;flex-direction:column">
        ${photo("s17", "saturate(.8)", "linear-gradient(180deg,rgba(21,23,28,.35) 0%,rgba(21,23,28,.92) 70%)")}
        <div style="position:relative;padding:36px 40px;margin-top:auto">
          <div class="kicker" style="color:var(--text-soft)">Up next · Hole ${st.ui.next_hole}</div>
          <div style="font:800 44px/1 var(--font-display)">${esc(next.name)}</div>
          <div style="font:400 19px var(--font-body);color:var(--text-muted)">${esc(next.level)} · Par ${next.par}. Rebuild: ${next.obstacles.map((o) => o.item).join(", ")}</div>
        </div>
      </div>` : ""}
    </div>
  </div>`;
};

S.S18 = function (st) {
  const c = st.ui.champion || {};
  const stand = st.ui.standings || [];
  const stats = st.ui.stats || {};
  return `
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:32px;padding:56px;height:100%">
    <div style="background:${c.color};color:var(--player-text);border-radius:28px;padding:56px 64px;display:flex;flex-direction:column">
      <div class="kicker" style="color:var(--player-text);opacity:.65">Champion · ${st.game.holes} holes</div>
      <div style="font:800 190px/1.15 var(--font-display);letter-spacing:-.05em;overflow:hidden">${marquee(c.name)}</div>
      <div style="font:700 40px var(--font-display);opacity:.8">${c.total} strokes · ${c.vs_par}</div>
      <div style="margin-top:auto;height:280px;border-radius:20px;background:repeating-linear-gradient(45deg,rgba(21,23,28,.18) 0 14px,rgba(21,23,28,.08) 14px 28px);position:relative">
        <span class="pill" style="position:absolute;left:16px;bottom:16px;background:var(--player-text);color:var(--text);font:400 15px monospace">live snapshot from color camera · winning putt</span>
      </div>
    </div>
    <div class="col" style="gap:18px">
      <div class="kicker">Final standings</div>
      ${stand.map((p) => `<div class="card" style="border-radius:18px;padding:16px 24px;display:flex;align-items:center;gap:16px">
        <span style="font:800 26px var(--font-display);color:var(--text-muted);width:36px">${p.pos}</span>
        ${dot(p.color, 28)}<span style="font:700 34px/1.25 var(--font-display);flex:1;overflow:hidden">${marquee(p.name)}</span>
        <span style="font:800 34px var(--font-display)">${p.total}</span>
        <span style="font:400 20px var(--font-body);color:var(--text-muted);width:70px;text-align:right">${p.vs_par}</span>
      </div>`).join("")}
      <div class="kicker">Night stats</div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px">
        <div class="card" style="border-radius:18px;padding:18px 22px"><div class="kicker">Total strokes</div><div style="font:800 34px/1 var(--font-display)">${stats.total_strokes}</div></div>
        <div class="card" style="border-radius:18px;padding:18px 22px"><div class="kicker">Holes played</div><div style="font:800 34px/1 var(--font-display)">${stats.holes_played}</div></div>
      </div>
      <div class="row" style="margin-top:auto;gap:14px">
        <button class="btn primary" data-action="again"><span>Play again</span>${RG.btnHint("confirm")}</button>
        <button class="btn secondary" data-action="new_courses"><span>New courses</span></button>
        <button class="btn secondary" data-action="start"><span>Start screen</span></button>
      </div>
    </div>
  </div>`;
};

S.S19 = function (st) {
  const s = st.settings || {};
  const theme = s.theme || "dark";
  return `
  ${photo("s19", "saturate(.7) brightness(.75)", "linear-gradient(90deg,rgba(21,23,28,.97) 0%,rgba(21,23,28,.92) 48%,rgba(21,23,28,.35) 100%)")}
  <div class="topbar" style="border:none"><span class="brand">Rumpus Golf</span><span class="subtitle">Settings</span><span class="right">${RG.badge()}<span class="hint">${RG.glyph("back")}<span>Back</span></span></span></div>
  <div style="position:absolute;left:56px;top:120px;bottom:56px;width:300px;display:flex;flex-direction:column;gap:8px;z-index:2">
    ${["Display & sound", "Game rules", "Players", "Camera", "About"].map((t, i) => `<button style="border-radius:14px;padding:16px 20px;font:700 22px var(--font-display);text-align:left;border:none;cursor:pointer;background:${i === 0 ? "var(--text)" : "var(--fill-quiet)"};color:${i === 0 ? "var(--player-text)" : "var(--text)"}" data-action="settings-tab" data-index="${i}">${t}</button>`).join("")}
    <div style="margin-top:auto;font:400 14px var(--font-body);color:var(--text-muted)">Rumpus Golf v${st.version}</div>
  </div>
  <div class="col" style="position:absolute;left:388px;top:120px;bottom:56px;width:860px;overflow:auto;gap:12px;z-index:2">
    <div class="glass" style="border-radius:20px;padding:24px 28px">
      <div style="font:700 24px var(--font-display)">Theme</div>
      <div class="seg" style="margin-top:12px">${["dark", "light", "auto"].map((t) => `<button class="opt ${theme === t ? "sel" : ""}" data-set-theme="${t}">${t}</button>`).join("")}</div>
    </div>
    <div class="glass" style="border-radius:20px;padding:24px 28px">
      <div class="row"><div><div style="font:700 24px var(--font-display)">Music</div><div style="font:400 16px var(--font-body);color:var(--text-muted)">Menu / gameplay soundtrack</div></div>
      <div class="seg" style="margin-left:auto">${["On", "Off"].map((t, i) => `<button class="opt mint ${(s.music.enabled ? 0 : 1) === i ? "sel" : ""}" data-set-music="${i === 0}">${t}</button>`).join("")}</div></div>
      <div class="row" style="margin-top:14px"><span style="width:120px">Volume ${Math.round((s.music.volume || 0) * 100)}%</span><input class="slider" type="range" min="0" max="100" value="${Math.round((s.music.volume || 0) * 100)}" data-set-musicvol style="flex:1"></div>
    </div>
    <div class="glass" style="border-radius:20px;padding:24px 28px">
      <div class="row"><div><div style="font:700 24px var(--font-display)">Sound effects</div><div style="font:400 16px var(--font-body);color:var(--text-muted)">Ticks, thunks, plinks</div></div>
      <div class="seg" style="margin-left:auto">${["On", "Off"].map((t, i) => `<button class="opt mint ${(s.sfx.enabled ? 0 : 1) === i ? "sel" : ""}" data-set-sfx="${i === 0}">${t}</button>`).join("")}</div></div>
      <div class="row" style="margin-top:14px"><span style="width:120px">Volume ${Math.round((s.sfx.volume || 0) * 100)}%</span><input class="slider" type="range" min="0" max="100" value="${Math.round((s.sfx.volume || 0) * 100)}" data-set-sfxvol style="flex:1"></div>
    </div>
    <div class="glass" style="border-radius:20px;padding:24px 28px">
      <div class="row"><div><div style="font:700 20px var(--font-display)">Announcer</div><div style="font:400 15px var(--font-body);color:var(--text-muted)">Say player names on turn change</div></div>
      <div class="toggle ${s.sfx.announcer ? "on" : ""}" data-set-announcer style="margin-left:auto"><span class="knob"></span></div></div>
    </div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
      <div class="glass" style="border-radius:20px;padding:24px 28px"><div class="row"><div style="font:700 20px var(--font-display)">Controller rumble</div><div class="toggle ${s.controller.rumble ? "on" : ""}" data-set-rumble style="margin-left:auto"><span class="knob"></span></div></div></div>
      <div class="glass" style="border-radius:20px;padding:24px 28px"><div class="row"><div style="font:700 20px var(--font-display)">Show camera feed</div><div class="toggle ${s.display.showCameraFeed ? "on" : ""}" data-set-feed style="margin-left:auto"><span class="knob"></span></div></div></div>
    </div>
  </div>
  <div class="col" style="position:absolute;right:56px;top:120px;width:240px;gap:12px;z-index:2">
    <button class="glass" style="border-radius:16px;padding:18px 22px;font:700 22px var(--font-display);border:none;text-align:left;cursor:pointer" data-action="credits">Credits →</button>
    <button class="glass" style="border-radius:16px;padding:18px 22px;font:700 22px var(--font-display);border:none;text-align:left;cursor:pointer" data-action="changelog">What’s new</button>
  </div>`;
};

S.S20 = function (st) {
  return `
  ${photo("s20", "saturate(.8) brightness(.7)", "linear-gradient(90deg,rgba(21,23,28,.3) 0%,rgba(21,23,28,.85) 45%,rgba(21,23,28,.97) 100%)")}
  <div class="topbar" style="border:none"><span class="brand">Rumpus Golf</span><span class="subtitle">Credits</span><span class="right">${RG.badge()}<span class="hint">${RG.glyph("back")}<span>Back</span></span></span></div>
  <div style="position:absolute;left:56px;bottom:56px;width:560px;z-index:2">
    <div class="kicker mint">Made with</div>
    <div style="font:800 88px/1 var(--font-display);letter-spacing:-.04em;text-shadow:0 2px 20px rgba(0,0,0,.5)">Books,<br>a cup and<br>a camera.</div>
    <div style="font:400 18px/1.5 var(--font-body);color:var(--text-soft)">Rumpus Golf v${st.version} · Python 3.11 · OpenCV · libfreenect / libfreenect2. No machine learning, no cloud — just your living room.</div>
  </div>
  <div style="position:absolute;left:760px;right:56px;top:120px;bottom:56px;display:grid;grid-template-columns:1fr 1fr;gap:16px;overflow:auto;z-index:2">
    ${creditGroup("Music", [["Lobby Time — Kevin MacLeod", "CC BY 4.0"], ["Backed Vibes Clean — Kevin MacLeod", "CC BY 4.0"]])}
    ${creditGroup("Sound effects", [["Kenney — Interface Sounds", "CC0"], ["Kenney — Impact Sounds", "CC0"], ["Freesound — small crowd cheer", "CC0"]])}
    ${creditGroup("Photography", [["Alex Gruber", "Unsplash"], ["Minh Pham", "Unsplash"], ["mark tulin", "Unsplash"], ["Waldemar Brandt", "Unsplash"], ["Clay Banks", "Unsplash"], ["Kayla Farmer", "Unsplash"], ["Spacejoy", "Unsplash"], ["Aaron Burden", "Unsplash"], ["Katja Rooke", "Unsplash"]])}
    ${creditGroup("Open source", [["OpenCV", "Apache 2.0"], ["libfreenect", "Apache 2.0 / GPL 2"], ["libfreenect2", "Apache 2.0 / GPL 2"]])}
  </div>`;
};

function creditGroup(title, items) {
  return `<div class="glass" style="border-radius:20px;padding:24px 28px">
    <div class="kicker mint">${title}</div>
    <div class="col" style="margin-top:12px;gap:10px">
      ${items.map(([t, l]) => `<div class="row" style="justify-content:space-between"><span style="font:700 19px/1.3 var(--font-display)">${esc(t)}</span><span class="pill" style="font-size:12px">${esc(l)}</span></div>`).join("")}
    </div>
  </div>`;
}

S.S21 = function (st) {
  const rels = st.ui.changelog || [];
  return `
  ${photo("s21", "saturate(.7) brightness(.7)", "linear-gradient(90deg,rgba(21,23,28,.97) 0%,rgba(21,23,28,.9) 55%,rgba(21,23,28,.3) 100%)")}
  <div class="topbar" style="border:none"><span class="brand">Rumpus Golf</span><span class="subtitle">What’s new</span><span class="right">${RG.badge()}<span class="hint">${RG.glyph("back")}<span>Back</span></span></span></div>
  <div style="position:absolute;left:56px;top:120px;width:420px;z-index:2">
    <div class="kicker mint">Current version</div>
    <div style="font:800 120px/1 var(--font-display);letter-spacing:-.05em">v${st.version}</div>
    <div style="font:400 18px/1.5 var(--font-body);color:var(--text-muted);margin-top:12px">
      <span style="color:var(--mint)">new</span> · <span>improved</span> · <span style="color:var(--coral)">fixed</span>
    </div>
  </div>
  <div class="col" style="position:absolute;left:540px;top:120px;bottom:56px;width:900px;overflow:auto;gap:14px;z-index:2">
    ${rels.map((r, i) => `<div class="glass" style="border-radius:20px;padding:24px 28px">
      <div class="row"><span style="font:800 30px var(--font-display)">${esc(r.version)}</span><span style="font:400 16px var(--font-body);color:var(--text-muted)">${esc(r.date)}</span><span class="pill" style="${i === 0 ? "background:var(--mint);color:var(--mint-text)" : ""}">${esc(r.tag)}</span></div>
      <div class="col" style="margin-top:12px;gap:8px">
        ${r.items.map(([tag, txt]) => `<div class="row" style="align-items:flex-start"><span style="width:78px;font:700 11px var(--font-body);letter-spacing:.12em;text-transform:uppercase;color:${tag === "new" ? "var(--mint)" : tag === "fixed" ? "var(--coral)" : "var(--text)"}">${tag}</span><span style="font:400 18px/1.4 var(--font-body)">${esc(txt)}</span></div>`).join("")}
      </div>
    </div>`).join("")}
  </div>`;
};

// ---------------------------------------------------------------------------
RG.screens = {
  render(st) {
    const fn = S[st.screen];
    return fn ? fn(st) : "";
  },
};
