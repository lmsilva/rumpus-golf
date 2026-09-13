// Screen templates. Each returns an HTML string; main.js injects it into #scene.
window.RG = window.RG || {};

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}
function dot(color, size = 16) {
  return `<span class="dot" style="width:${size}px;height:${size}px;background:${color}"></span>`;
}
function topbar(subtitle, opts = {}) {
  const right = [RG.badge()];
  if (opts.browse) {
    right.push(`<span class="hint">${RG.glyph("prev")}${RG.glyph("next")}<span>Browse</span></span>`);
  }
  if (opts.menu) {
    right.push(`<button type="button" class="hint back-btn" data-action="menu">${RG.glyph("menu")}<span>Pause</span></button>`);
  }
  if (opts.back !== false) {
    right.push(`<button type="button" class="hint back-btn" data-action="back">${RG.glyph("back")}<span>Back</span></button>`);
  }
  return `<div class="topbar">
    <span class="brand">Rumpus Golf</span>
    ${opts.rail || ""}
    ${!opts.rail && subtitle ? `<span class="subtitle">${esc(subtitle)}</span>` : ""}
    <span class="right">${right.join("")}</span>
  </div>`;
}
function stepRail(current) {
  const steps = ["Floor", "Play area", "Course", "Cup", "Balls & players"];
  return `<span class="steprail">${steps.map((s, i) => {
    const cls = i === current - 1 ? "current" : (i < current - 1 ? "done" : "");
    return `<span class="step ${cls}"><span class="n">${i < current - 1 ? "✓" : i + 1}</span>${s}</span>`;
  }).join("")}</span>`;
}
function photo(name, filter, scrim, opts = {}) {
  const bg = `linear-gradient(160deg, #2a2e36 0%, #1a1c22 60%, #14161a 100%)`;
  const place = opts.right ? `inset:0 0 0 auto;width:${opts.right}px;` : "";
  const op = opts.opacity != null ? `opacity:${opts.opacity};` : "";
  return `<div class="photo-layer">
    <div class="photo" style="background-image:url('/assets/photos/${name}.jpg'),${bg};background-blend-mode:normal;filter:${filter || "none"};${place}${op}"></div>
    <div class="scrim" style="background:${scrim || "transparent"}"></div>
  </div>`;
}
function livePill(st, compact) {
  const w = (st.feed && st.feed.w) || 0;
  const h = (st.feed && st.feed.h) || 0;
  return `<span class="pill live-pill">${compact ? "LIVE" : `LIVE · ${w} × ${h} · 30 fps`}</span>`;
}
function hasDepth(st) {
  const d = (st.sensor && st.sensor.depth_res) || (st.ui && st.ui.sensor && st.ui.sensor.depth_res);
  return !!(d && d[0]);
}
function marquee(text) {
  return `<span class="marquee" data-marquee><span>${esc(text)}</span></span>`;
}

// ---------------------------------------------------------------------------
const S = {};

S.S01 = function (st) {
  const ui = st.ui || {};
  const items = [
    { label: "New game", action: "new_game", cls: "primary", verb: "confirm" },
    { label: "Load last setup", action: "load", cls: "secondary", verb: "confirm", meta: ui.saved_meta || "" },
    { label: "Course library", action: "library", cls: "secondary", verb: "confirm" },
    { label: "Settings", action: "settings", cls: "secondary", verb: "confirm" },
  ];
  return `
  ${photo("s01", "saturate(.75) brightness(.8)", "linear-gradient(90deg,var(--ground) 0 780px,rgba(var(--ground-rgb),.85) 980px,rgba(var(--ground-rgb),.15) 1500px)", { right: 1040 })}
  <div style="position:absolute;inset:0;padding:72px 96px;display:flex;flex-direction:column">
    <div class="row" style="gap:12px">
      ${dot("#ff8a3d", 28)}${dot("#ff5fa8", 28)}${dot("#5b8cff", 28)}${dot("#ffd84d", 28)}
      <span style="margin-left:auto">${RG.badge(true)}</span>
    </div>
    <div style="margin-top:auto">
      <h1 style="font:800 200px/1 var(--font-display);letter-spacing:-.05em">Rumpus<br>Golf</h1>
      <div style="font:400 32px/1.3 var(--font-body);color:var(--text-muted);margin-top:28px;max-width:720px">Turn any floor into a mini golf course.</div>
    </div>
    <div class="col" style="margin-top:56px;max-width:560px;gap:12px">
      ${items.map((it) => `<button class="btn ${it.cls} stretch" data-action="${it.action}"><span>${esc(it.label)}</span>${it.meta ? `<span class="meta">${esc(it.meta)}</span>` : ""}${it.verb ? RG.btnHint(it.verb) : ""}</button>`).join("")}
    </div>
  </div>
  <div class="pill" style="position:absolute;right:56px;bottom:48px;padding:14px 20px;font:600 17px var(--font-body)">
    <span class="dot" style="background:var(--mint);width:10px;height:10px"></span>${esc((st.sensor && st.sensor.model) || "Kinect v1")} connected
    <span class="text-muted">· Lobby Time — Kevin MacLeod</span>
  </div>`;
};

S.S02 = function (st) {
  const d = st.ui && st.ui.sensor;
  const facts = d ? [
    ["Depth", `${d.depth_res[0]} × ${d.depth_res[1]}`],
    ["Color", `${d.color_res[0]} × ${d.color_res[1]}`],
    ["Field of view", `${d.fov_h_deg}° horizontal`],
    ["Reliable range", `${d.reliable_min_m} – ${d.reliable_max_m} m`],
    ["Note", d.note || ""],
  ] : [];
  const noSensor = !d;
  return `
  ${topbar("New game")}
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:32px;padding:56px;height:calc(100% - 96px)">
    <div class="card" style="padding:56px 64px;display:flex;flex-direction:column">
      <div class="kicker mint">Sensor</div>
      <h2 style="font-size:80px;letter-spacing:-.035em;margin:14px 0 36px">${noSensor ? "No camera" : esc(d.model)}<br>found.</h2>
      ${noSensor ? "" : `<div style="display:grid;grid-template-columns:auto 1fr;gap:14px 40px;font:400 23px/1.3 var(--font-body)">
             ${facts.map(([k, v]) => `<span class="text-muted">${esc(k)}</span><span>${esc(v)}</span>`).join("")}
           </div>`}
      ${noSensor ? `<div style="margin-top:auto;background:rgba(255,107,87,.12);border:1px solid rgba(255,107,87,.4);border-radius:18px;padding:24px 28px;display:flex;gap:18px">
             <span class="dot" style="width:12px;height:12px;background:var(--coral);margin-top:10px"></span>
             <div><div style="font:700 22px var(--font-display)">Error state — no sensor</div>
             <div style="font:400 17px/1.45 var(--font-body);color:var(--text-muted);margin-top:4px">No camera found. Plug in a Kinect v1 or v2 and press Retry. Nothing else is reachable until a sensor answers.</div>
             <button class="btn primary sm" style="margin-top:16px" data-action="retry"><span>Retry</span>${RG.btnHint("confirm")}</button></div>
           </div>` : ""}
    </div>
    <div class="col" style="gap:20px;min-height:0">
      <button type="button" class="action-card" data-action="load">
        <div style="font:800 40px var(--font-display);letter-spacing:-.02em">Load saved setup</div>
        <div style="font:400 21px/1.45 var(--font-body);margin-top:8px;opacity:.8">${esc((st.ui && st.ui.saved_meta) || "No saved setup yet")}<br>${esc((st.ui && st.ui.saved_when) || "")}</div>
        <span class="hint-row">${RG.glyph("confirm")}Load</span>
      </button>
      <button type="button" class="action-card photo-card" data-action="fresh">
        ${photo("s02", "saturate(.7)", "linear-gradient(90deg,rgba(var(--ground-rgb),.92) 0%,rgba(var(--ground-rgb),.75) 55%,rgba(var(--ground-rgb),.35) 100%)")}
        <div class="action-card-body">
          <div style="font:800 40px var(--font-display);letter-spacing:-.02em">New calibration</div>
          <div style="font:400 21px/1.45 var(--font-body);margin-top:8px;color:var(--text-soft)">Five steps, about 3 minutes. Clear the floor first.</div>
          <span class="hint-row" style="color:var(--text-muted)">${RG.glyph("secondary")}Start fresh</span>
        </div>
      </button>
    </div>
  </div>`;
};

S.S03 = function (st) {
  const setup = st.setup || {};
  return `
  <div class="glass" style="position:absolute;left:56px;top:56px;max-width:900px;border-radius:24px;padding:32px 40px;z-index:3">
    <div class="kicker mint">Verify · ${esc(setup.name || "Living room")}</div>
    <h2 style="font-size:60px;margin:10px 0 16px">Do the lines still sit on the floor?</h2>
    <div style="font:400 21px/1.45 var(--font-body);color:var(--text-soft)">White = play area, start and the obstacles you confirmed. Mint = hole. If only something moved, Recalibrate lets you redo just that — cup, obstacles or zones.</div>
  </div>
  <div class="pill" style="position:absolute;right:56px;top:56px;z-index:3">LIVE · ${(st.feed && st.feed.w) || 0} × ${(st.feed && st.feed.h) || 0} · 30 fps</div>
  <div class="row" style="position:absolute;left:56px;bottom:48px;gap:14px;z-index:3">
    <button class="btn primary" data-action="confirm" style="min-width:400px"><span>Looks right</span>${RG.btnHint("confirm")}</button>
    <button class="btn glass" data-action="recalibrate" style="min-width:400px"><span>Recalibrate</span>${RG.btnHint("undo")}</button>
    <button class="btn glass" data-action="back"><span>Back</span>${RG.btnHint("back")}</button>
  </div>`;
};

S.S04 = function (st) {
  const depth = hasDepth(st);
  const helper = depth
    ? "Averages 2 s of depth frames, then fits the floor plane. A mint progress line runs along the top of the feed."
    : "Captures a color reference of the empty floor. A mint progress line runs along the top of the feed.";
  return `
  ${topbar("", { rail: stepRail(1) })}
  <div style="display:grid;grid-template-columns:1fr 520px;gap:24px;padding:32px 56px 48px;height:calc(100% - 96px)">
    <div class="feed-slot" data-feed-slot style="border-radius:24px">
      <div class="feed-band" style="top:0;height:21.3%"></div>
      <div class="feed-band" style="bottom:0;height:7.4%"></div>
      <div class="feed-band-line" style="top:21.3%"></div>
      <div class="feed-band-line" style="bottom:7.4%"></div>
      <div class="feed-band-label" style="top:14%">4.0 m — too far for reliable depth</div>
      <div class="feed-band-label" style="bottom:2%">0.8 m — too close</div>
      <div class="feed-band-label" style="top:26%;font:700 30px var(--font-display);color:var(--mint)">Good floor — fit a course inside this band</div>
      ${livePill(st, true)}
      ${st.setup && st.setup.capturing ? `<div class="feed-progress"></div>` : ""}
    </div>
    <div class="card" style="padding:44px;display:flex;flex-direction:column">
      <div class="kicker mint">Step 1 of 5</div>
      <h2 style="font-size:56px;margin:14px 0 20px">Clear the floor.</h2>
      <div style="font:400 21px/1.45 var(--font-body);color:var(--text-muted)">Remove balls, cup, obstacles, feet. We photograph the bare floor once so anything added later shows up as “above floor”.</div>
      <div style="margin-top:auto;display:flex;flex-direction:column;gap:12px">
        <button class="btn primary stretch" data-action="confirm"><span>${st.setup.capturing ? "Capturing…" : "Capture floor"}</span>${RG.btnHint("confirm")}</button>
        <div style="font:400 15px/1.45 var(--font-body);color:var(--text-muted)">${helper}</div>
      </div>
    </div>
  </div>`;
};

S.S05 = function (st) {
  const cur = (st.ui && st.ui.preset) || "medium";
  const colorOnly = !!(st.ui && st.ui.color_only);
  const corners = (st.ui && st.ui.corners) || 0;
  const w = (st.ui && st.ui.area_w) || 3;
  const h = (st.ui && st.ui.area_h) || 2;
  const presets = [
    ["small", "Small", "2 × 1.5 m"],
    ["medium", "Medium", "3 × 2 m"],
    ["large", "Large", "4 × 2.5 m"],
  ];
  const body = colorOnly
    ? "This camera cannot measure distance. Drag a numbered corner, or Clear corners and click four new ones of a rectangle whose real size you know — tape, a rug, or the course edge."
    : "Drag the corners, or Clear and click a new outline. Balls stopping outside are out of bounds.";
  const sizeKicker = colorOnly ? "How big is that rectangle?" : "Start from a preset";
  const sizeHelp = colorOnly
    ? `The webcam only sees pixels. ${w} × ${h} m is the real size of the rectangle you marked — that’s what turns those four corners into meters.`
    : "Drops a rectangle of that size on the floor. Drag the corners to fit what you have.";
  const confirmDisabled = colorOnly && corners < 4;
  return `
  ${topbar("", { rail: stepRail(2) })}
  <div style="display:grid;grid-template-columns:1fr 520px;gap:24px;padding:32px 56px 48px;height:calc(100% - 96px)">
    <div class="feed-slot" data-feed-slot data-feed-interactive style="border-radius:24px">
      ${livePill(st, true)}
      <div class="hints">${RG.hint("stick", "Move corner")}${RG.hint("confirm", "Place")}${RG.hint("undo", "Undo corner")}${colorOnly ? "" : RG.hint("secondary", "Close shape")}</div>
    </div>
    <div class="card" style="padding:44px;display:flex;flex-direction:column">
      <div class="kicker mint">Step 2 of 5</div>
      <h2 style="font-size:56px;margin:14px 0 20px">Mark the course edge.</h2>
      <div style="font:400 21px/1.45 var(--font-body);color:var(--text-muted);margin-bottom:28px">${body}</div>
      <div class="kicker" style="margin-bottom:10px">${sizeKicker}</div>
      <div class="seg presets">
        ${presets.map(([id, name, sub]) => `<button type="button" class="opt ${id === cur ? "sel" : ""}" data-set-preset="${id}">${name}<span class="sub">${sub}</span></button>`).join("")}
      </div>
      <div data-size-help style="font:400 16px/1.45 var(--font-body);color:var(--text-muted);margin-top:12px">${sizeHelp}</div>
      <div data-corner-progress style="font:600 16px var(--font-body);color:var(--mint);margin-top:10px">${colorOnly ? `${corners} of 4 corners` : ""}</div>
      <div style="margin-top:auto;display:flex;flex-direction:column;gap:12px">
        <button class="btn primary stretch" data-action="confirm"${confirmDisabled ? " disabled" : ""}><span>Use this area</span>${RG.btnHint("confirm")}</button>
        <button class="btn secondary stretch quiet" data-action="clear"><span>Clear corners</span>${RG.btnHint("undo")}</button>
      </div>
    </div>
  </div>`;
};

S.S06 = function (st) {
  const courses = st.ui.courses || [];
  const cur = st.game && st.game.course_id;
  return `
  ${photo("s06", "saturate(.8)", "linear-gradient(180deg,rgba(var(--ground-rgb),.4) 0%,rgba(var(--ground-rgb),.92) 60%)", { opacity: 0.28 })}
  ${topbar("Step 3 of 5 · Course", { browse: true })}
  <div style="position:relative;padding:40px 56px 48px;display:flex;flex-direction:column;gap:20px;height:calc(100% - 96px)">
    <div><h2 style="font-size:60px">Pick a course for hole 1.</h2>
    <div style="font:400 21px/1.45 var(--font-body);color:var(--text-muted);margin-top:8px">Drawn for your ${(st.ui && st.ui.area_w) || "3.0"} × ${(st.ui && st.ui.area_h) || "2.0"} m area. You’ll place the real objects next and can nudge anything.</div></div>
    <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:20px;flex:1">
      ${courses.map((c, i) => {
        const sel = c.id === cur;
        return `<div class="card course-card${sel ? " is-selected" : ""}" data-action="select" data-index="${i}" tabindex="0" role="button">
          <div class="row" style="justify-content:space-between">
            <div><div class="kicker" style="opacity:.7">${esc(c.level)}</div>
            <div style="font:800 40px/1 var(--font-display);letter-spacing:-.02em;margin-top:6px">${esc(c.name)}</div></div>
            <span class="pill course-par">Par ${c.par}</span>
          </div>
          <div class="course-map">${courseMap(c)}</div>
          <div style="font:400 18px/1.4 var(--font-body);opacity:.8">${esc(c.blurb)}</div>
          <div class="kicker" style="margin-top:14px">You will need</div>
          <div style="font:400 17px/1.4 var(--font-body);margin-top:6px">${c.needs.map(esc).join(" · ")}</div>
          <div class="row" style="margin-top:16px"><button type="button" class="btn ${sel ? "primary" : "secondary"} sm" data-action="confirm" data-index="${i}" style="${sel ? "" : "background:rgba(242,239,232,.08)"}"><span>${sel ? "Use this course" : "Choose"}</span>${RG.btnHint("confirm")}</button></div>
        </div>`;
      }).join("")}
    </div>
  </div>`;
};

function courseMap(c) {
  const obs = c.obstacles || [];
  let s = `<span style="position:absolute;left:${c.start.x}%;top:${c.start.y}%;width:11%;aspect-ratio:1;transform:translate(-50%,-50%);border:2px solid #f2efe8;border-radius:50%"></span>`;
  s += `<span style="position:absolute;left:${c.hole.x}%;top:${c.hole.y}%;width:7%;aspect-ratio:1;transform:translate(-50%,-50%);background:var(--mint);border-radius:50%"></span>`;
  for (const o of obs) {
    const fill = o.kind === "hazard" ? "repeating-linear-gradient(45deg,#ff6b57 0 4px,#b9483a 4px 8px)"
      : o.kind === "book" ? "#f2efe8" : o.kind === "tube" ? "#c9c4b8" : "#8a8f99";
    s += `<span style="position:absolute;left:${o.x}%;top:${o.y}%;width:${o.w}%;height:${o.h}%;background:${fill};border-radius:2px"></span>`;
  }
  return s;
}

S.S07 = function (st) {
  const ui = st.ui || {};
  const course = ui.course || {};
  const ghosts = ui.ghosts || [];
  const sel = ui.selected;
  const rebuilding = st.state === "HOLE_START";
  return `
  ${topbar("", { rail: rebuilding ? "" : stepRail(3), back: !rebuilding, menu: rebuilding })}
  <div style="display:grid;grid-template-columns:1fr 480px;gap:24px;padding:32px 56px 48px;height:calc(100% - 96px)">
    <div class="feed-slot" data-feed-slot data-feed-interactive style="border-radius:24px">
      ${livePill(st, true)}
      <div class="hints">${RG.hint("stick", "Move / corner")}${RG.hint("undo", "Rotate 90°")}${RG.hint("secondary", "Reset")}${RG.hint("next", "Next object")}</div>
    </div>
    <div class="card" style="padding:36px;display:flex;flex-direction:column">
      <div class="kicker">${esc(course.level || "")} · Par ${course.par || ""}</div>
      <h2 style="font-size:48px;line-height:1;margin:8px 0 10px">${rebuilding ? `Rebuild hole ${st.game.hole}.` : `Build “${esc(course.name || "")}”.`}</h2>
      <div style="font:400 17px/1.45 var(--font-body);color:var(--text-muted)">The dashed boxes are where this course wants each object. Put the real items on the floor, then drag the outline and its corners until they sit on what the camera sees.</div>
      <div class="col" style="margin-top:20px;gap:8px;overflow:auto">
        ${ghosts.map((g, i) => `<div class="row" data-action="select" data-index="${i}" tabindex="0" role="button" style="cursor:pointer;background:${i === sel ? "rgba(139,233,195,.16)" : "rgba(242,239,232,.05)"};border:${i === sel ? "1px solid var(--mint)" : "1px solid transparent"};border-radius:14px;padding:14px 16px">
          <span style="width:30px;height:30px;border-radius:50%;flex:none;display:grid;place-items:center;border:1px solid ${i === sel ? "var(--mint)" : "rgba(242,239,232,.3)"};font:700 14px var(--font-display)">${i + 1}</span>
          <div style="flex:1"><div style="font:700 19px var(--font-display)">${esc(g.label)}</div><div style="font:400 14px var(--font-body);color:var(--text-muted)">${esc(g.item)}</div></div>
          <span style="font:600 14px var(--font-body);color:var(--text-muted)">${(g.real_size_cm || []).length ? `~${(g.real_size_cm || []).join(" × ")} cm` : "custom"}</span>
        </div>`).join("")}
      </div>
      <div class="col" style="margin-top:auto;gap:10px">
        <button class="btn primary stretch" data-action="confirm"><span>Outlines match</span>${RG.btnHint("confirm")}</button>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
          <button class="btn secondary" data-action="draw"><span>Add outline</span></button>
          <button class="btn secondary" data-action="delete"><span>Remove</span></button>
        </div>
        <button class="btn secondary stretch quiet" data-action="prev"><span>Swap course</span>${RG.btnHint("prev")}</button>
      </div>
    </div>
  </div>`;
};

S["S07b"] = function (st) {
  const obs = st.ui.obstacles || [];
  const flagged = obs.filter((o) => o.state === "proposed" && o.confidence < 0.7);
  return `
  <div class="glass-strong" style="position:absolute;right:40px;top:56px;bottom:56px;width:520px;border-radius:24px;padding:36px;z-index:3;display:flex;flex-direction:column">
    <div class="kicker mint">Step 3 of 5 · Obstacles</div>
    <h2 style="font-size:48px;margin:8px 0 10px">Found ${obs.length} objects.</h2>
    <div style="font:400 17px/1.45 var(--font-body);color:var(--text-muted)">Outlines are approximate on purpose — they only draw the map and keep hidden balls from counting as lost. Delete anything that isn’t an object, then confirm.</div>
    <div class="col" style="margin-top:20px;gap:8px;overflow:auto">
      ${obs.map((o, i) => `<div class="row" data-action="select" data-index="${i}" tabindex="0" role="button" style="cursor:pointer;background:${o.confidence < 0.7 ? "rgba(255,107,87,.1)" : "rgba(242,239,232,.05)"};border:${o.confidence < 0.7 ? "1px solid rgba(255,107,87,.35)" : "none"};border-radius:14px;padding:12px 14px">
        <span style="width:30px;height:30px;border-radius:50%;background:${o.confidence < 0.7 ? "var(--coral)" : "var(--mint)"};display:grid;place-items:center;color:var(--mint-text);font:800 16px var(--font-display)">${i + 1}</span>
        <div style="flex:1;min-width:0"><div class="nowrap" style="font:700 19px/1.3 var(--font-display)">${esc(o.label)}</div><div class="text-muted nowrap" style="font:400 14px var(--font-body)">${esc(o.kind)}</div></div>
        <span class="pill" style="font-size:13px;background:${o.confidence >= 0.7 ? "var(--mint)" : "var(--coral)"};color:var(--mint-text)">${Math.round(o.confidence * 100)}%</span>
      </div>`).join("")}
    </div>
    <div class="col" style="margin-top:auto;gap:10px">
      <button class="btn primary stretch" data-action="confirm_all" ${flagged.length ? "disabled" : ""}><span>Confirm all</span>${RG.btnHint("confirm")}</button>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
        <button class="btn secondary" data-action="redetect"><span>Re-detect</span>${RG.btnHint("next")}</button>
        <button class="btn secondary" data-action="draw"><span>Draw one</span>${RG.btnHint("secondary")}</button>
      </div>
      <div style="font:400 14px var(--font-body);color:var(--text-muted)">Low-confidence outlines must be confirmed or deleted before Confirm all.</div>
    </div>
  </div>
  <div class="glass" style="position:absolute;left:56px;top:56px;padding:16px 24px;border-radius:16px;z-index:3;display:flex;align-items:center;gap:16px"><span class="brand" style="font:700 22px var(--font-display)">Rumpus Golf</span> <span class="text-muted">Step 3 of 5 · Obstacles</span><button type="button" class="hint back-btn" data-action="back">${RG.glyph("back")}<span>Back</span></button></div>
  <div class="pill" style="position:absolute;left:56px;top:150px;z-index:3"><span class="dot" style="background:var(--mint)"></span>Scanned 1.5 s of depth · dashed = proposed, not yet on the course</div>
  <div class="hints">${RG.hint("stick", "Move cursor")}${RG.hint("confirm", "Select object")}${RG.hint("undo", "Delete")}${RG.hint("secondary", "Draw one")}${RG.hint("prev", "Undo")}</div>`;
};

S["S07c"] = function (st) {
  const obs = st.ui.obstacles || [];
  const sel = st.ui.selected;
  const pending = obs.filter((o) => o.state === "proposed" || o.state === "drawing");
  const drawing = obs.filter((o) => o.state === "drawing").length;
  const statusLabel = { proposed: "Pending", confirmed: "Confirmed", selected: "Selected", deleted: "Deleted", drawing: "Drawing" };
  const selected = sel != null ? obs[sel] : null;
  return `
  <div class="glass" style="position:absolute;left:56px;top:56px;padding:16px 24px;border-radius:16px;z-index:3;display:flex;align-items:center;gap:16px"><span class="brand" style="font:700 22px var(--font-display)">Rumpus Golf</span> <span class="text-muted">Step 3 of 5 · Obstacles</span><button type="button" class="hint back-btn" data-action="back">${RG.glyph("back")}<span>Back</span></button></div>
  <div class="legend">
    <span class="pill">${`<span class="swatch" style="background:var(--mint)"></span>`} Selected</span>
    <span class="pill">${`<span class="swatch" style="background:#f2efe8"></span>`} Confirmed</span>
    <span class="pill">${`<span class="swatch" style="background:rgba(242,239,232,.3)"></span>`} Pending</span>
    <span class="pill">${`<span class="swatch" style="background:var(--coral);opacity:.35"></span>`} Deleted</span>
  </div>
  <div class="glass-strong" style="position:absolute;right:40px;top:56px;bottom:56px;width:520px;border-radius:24px;padding:36px;z-index:3;display:flex;flex-direction:column">
    <div class="kicker mint">Object ${(sel != null ? sel + 1 : 1)} · ${selected ? esc(selected.label || "") : ""}</div>
    <h2 style="font-size:48px;margin:8px 0 10px">Fix the outline.</h2>
    <div style="font:400 17px/1.45 var(--font-body);color:var(--text-muted)">Drag the corners until the outline roughly covers the object. Close enough is good enough. Click an edge to add a corner; press stick to remove one.</div>
    <div class="col" style="margin-top:20px;gap:8px;overflow:auto">
      ${obs.map((o, i) => {
        const state = i === sel ? "selected" : (o.state === "proposed" ? "pending" : o.state);
        const label = i === sel ? "Selected" : (statusLabel[o.state] || o.state);
        return `<div class="row" data-action="select" data-index="${i}" tabindex="0" role="button" style="cursor:pointer;background:rgba(242,239,232,.05);border-radius:14px;padding:12px 14px">
        <span style="width:28px;height:28px;border-radius:50%;background:rgba(242,239,232,.1);display:grid;place-items:center;font:800 15px var(--font-display)">${i + 1}</span>
        <div style="flex:1"><div style="font:700 19px var(--font-display)">${esc(o.label)}</div><div style="font:400 14px var(--font-body);color:var(--text-muted)">${esc(o.kind || "")}</div></div>
        <span class="status-pill ${state}">${esc(label)}</span>
      </div>`;
      }).join("")}
    </div>
    <div class="col" style="margin-top:auto;gap:10px">
      <button class="btn primary stretch" data-action="confirm_one"><span>Confirm this outline</span>${RG.btnHint("confirm")}</button>
      <button class="btn secondary stretch quiet" data-action="confirm" ${pending.length ? "disabled" : ""}><span>Course is set</span><span class="meta">${pending.length} pending${drawing ? ` · ${drawing} drawing` : ""}</span></button>
      <div style="font:400 14px var(--font-body);color:var(--text-muted)">Undo steps back through every edit in this step, including re-detects.</div>
    </div>
  </div>
  <div class="hints">${RG.hint("stick", "Drag corner")}${RG.hint("confirm", "Confirm this one")}${RG.hint("undo", "Delete")}${RG.hint("secondary", "Add / remove corner")}${RG.hint("prev", "Undo")}</div>`;
};

S.S08 = function (st) {
  return `
  ${topbar("", { rail: stepRail(4) })}
  <div style="display:grid;grid-template-columns:1fr 480px;gap:24px;padding:32px 56px 48px;height:calc(100% - 96px)">
    <div class="feed-slot" data-feed-slot data-feed-interactive style="border-radius:24px">
      ${livePill(st, true)}
      <div class="hints">${RG.hint("stick", "Move cup")}${RG.hint("undo", "Reset")}${RG.hint("secondary", "Click to place")}</div>
    </div>
    <div class="card" style="padding:36px;display:flex;flex-direction:column">
      <div class="kicker mint">Step 4 of 5 · Cup</div>
      <h2 style="font-size:48px;line-height:1;margin:8px 0 12px">Mark the hole.</h2>
      <div style="font:400 17px/1.45 var(--font-body);color:var(--text-muted)">The mint circle starts where this course suggests the cup. Put the real cup on the floor, then drag the circle onto it. Drag the mint dot on the edge to resize.</div>
      <div style="margin-top:auto;display:flex;flex-direction:column;gap:10px">
        <button class="btn primary stretch" data-action="confirm"><span>That’s the hole</span>${RG.btnHint("confirm")}</button>
        <button class="btn secondary stretch" data-action="draw"><span>Click to place</span>${RG.btnHint("secondary")}</button>
        <button class="btn secondary stretch quiet" data-action="redetect"><span>Reset to suggested</span>${RG.btnHint("undo")}</button>
      </div>
    </div>
  </div>`;
};

S.S09 = function (st) {
  const balls = st.ui.balls || [];
  const players = st.ui.players || [];
  const rejected = st.ui.rejected || 0;
  const sel = st.ui.selected;
  return `
  ${topbar("", { rail: stepRail(5) })}
  <div style="position:absolute;left:56px;top:120px;z-index:3"><h2 style="font:800 48px var(--font-display)">Put every ball on the floor.</h2></div>
  <div class="glass-strong" style="position:absolute;right:40px;top:120px;bottom:56px;width:640px;border-radius:24px;padding:36px;z-index:3;display:flex;flex-direction:column">
    <div class="kicker">${balls.length} ball${balls.length === 1 ? "" : "s"} found · ${rejected} rejected</div>
    <h2 style="font-size:48px;margin:8px 0 10px">Who’s who?</h2>
    <div style="font:400 17px/1.45 var(--font-body);color:var(--text-muted)">One player per color. Numbers on the course match this tee-off list. Click a row to point at that ball. Remove a row if auto-detect was wrong.</div>
    <div class="col" style="margin-top:20px;gap:10px;overflow:auto">
      ${players.map((p, i) => `<div class="row player-row${i === sel ? " is-selected" : ""}" data-action="select" data-index="${i}" tabindex="0" role="button">
        <span class="player-ord">${i + 1}</span>
        ${dot(p.color, 40)}
        <input class="player-name" data-text="player_name" data-index="${i}" value="${esc(p.name)}" placeholder="Player name">
        <span class="player-hue">${esc(p.hue_name)}</span>
        <div class="player-tools" onclick="event.stopPropagation()">
          <button type="button" class="icon-btn" data-action="move_up" data-index="${i}" ${i === 0 ? "disabled" : ""} aria-label="Move up">↑</button>
          <button type="button" class="icon-btn" data-action="move_down" data-index="${i}" ${i === players.length - 1 ? "disabled" : ""} aria-label="Move down">↓</button>
          <button type="button" class="icon-btn danger" data-action="delete" data-index="${i}" aria-label="Remove player">×</button>
        </div>
      </div>`).join("")}
      <div class="row" style="border:1px dashed rgba(242,239,232,.2);border-radius:16px;padding:16px;color:var(--text-muted);font:400 17px var(--font-body)">${players.length ? `Room for ${Math.max(0, 6 - players.length)} more colors — click another ball on the camera.` : "No balls yet — click each ball on the camera to add a player."}</div>
    </div>
    <div class="col" style="margin-top:auto;gap:10px">
      <button class="btn primary stretch" data-action="confirm"${players.length ? "" : " disabled"}><span>Save setup & continue</span>${RG.btnHint("confirm")}</button>
      <div style="font:400 14px var(--font-body);color:var(--text-muted)">Writes rumpus-setup.json: floor plane, play area, course template, hole zone, ball hues, names.</div>
    </div>
  </div>`;
};

S.S10 = function (st) {
  const players = st.ui.players || [];
  const holes = st.ui.holes;
  const cap = st.ui.stroke_cap;
  return `
  ${photo("s10", "saturate(.75) brightness(.8)", "linear-gradient(90deg,rgba(var(--ground-rgb),.95) 0%,rgba(var(--ground-rgb),.35) 48%,rgba(var(--ground-rgb),.6) 100%)", { opacity: 0.3 })}
  <div style="position:relative;display:grid;grid-template-columns:1fr 760px;gap:32px;padding:56px;height:100%">
    <div class="col" style="gap:20px">
      <div class="kicker mint">Setup saved · Living room</div>
      <h2 style="font:800 112px/1 var(--font-display);letter-spacing:-.045em">Game<br>night.</h2>
      <div>
        <div class="kicker">Holes this game</div>
        <div class="seg" style="max-width:760px;margin-top:10px">
          ${[1,2,3,4,5,6,7,8,9].map((n) => `<button class="opt ${n === holes ? "sel" : ""}" data-set-holes="${n}">${n}</button>`).join("")}
        </div>
        <div style="font:400 16px var(--font-body);color:var(--text-muted);margin-top:8px">Hole 1 → Beginner, 2 → Advanced, 3 → Expert, then repeat. Swap any hole’s course from Pause.</div>
      </div>
      <div>
        <div class="kicker">Stroke cap</div>
        <div class="row" style="margin-top:10px">
          <button type="button" class="stepper" data-set-cap="${cap - 1}">−</button>
          <span style="font:700 28px var(--font-display);min-width:120px;text-align:center">${cap} strokes</span>
          <button type="button" class="stepper" data-set-cap="${cap + 1}">+</button>
        </div>
      </div>
      <div class="row" style="margin-top:auto;gap:14px;align-items:center">
        <button class="btn primary lg" data-action="confirm"><span>Tee off</span>${RG.btnHint("confirm")}</button>
        ${RG.hint("menu", "Pause")}
      </div>
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
  const awaiting = !!ui.awaiting_tee;
  const status = playStatus(ui, a, activeStrokes);
  return `
  <div style="position:absolute;inset:40px;z-index:6;pointer-events:none;display:flex;flex-direction:column">
    <div class="row" style="gap:16px;align-items:stretch">
      ${a ? `<div style="background:${a.color};color:var(--player-text);border-radius:24px;padding:26px 40px;box-shadow:var(--shadow-card);display:flex;align-items:center;gap:36px">
        <div><div class="kicker" style="opacity:.7;color:var(--player-text)">Your turn</div><div style="font:800 76px/1.15 var(--font-display);overflow:hidden">${marquee(a.name)}</div></div>
        <div style="width:2px;align-self:stretch;background:rgba(var(--ground-rgb),.25)"></div>
        <div><div class="kicker" style="opacity:.7;color:var(--player-text)">Stroke</div><div style="font:800 76px/1.15 var(--font-display)">${activeStrokes}</div></div>
        <div style="width:2px;align-self:stretch;background:rgba(var(--ground-rgb),.25)"></div>
        <div data-play-status style="font:600 26px/1.25 var(--font-body);max-width:420px;opacity:.85">${esc(status)}</div>
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
      ${awaiting ? `<button class="btn primary sm" data-action="ready"><span>It’s in the start zone — let’s play</span>${RG.btnHint("confirm")}</button>` : ""}
      ${RG.hint("undo", "Undo last shot")}${RG.hint("menu", "Pause")}
    </div>
    <div class="pill" style="position:absolute;right:0;top:230px;pointer-events:auto">
      ${dot(a ? a.color : "#fff", 10)}<span data-play-pill>${esc(playPill(ui, motion))}</span>
    </div>
  </div>`;
};

function playStatus(ui, a, strokes) {
  const motion = (ui && ui.motion) || {};
  if (!a) return "";
  if (motion.moving) return "Ball rolling…";
  if (motion.hidden) return "Stopped, hidden — position estimated";
  if (ui && ui.awaiting_tee) {
    if (ui.in_start) return "In the start zone. Putt when ready, or confirm.";
    if (ui.ball_seen) return "Move it into the start circle, click it on the camera, or confirm when ready.";
    return "I don’t see your ball yet — click it on the camera, or confirm when it’s ready.";
  }
  return strokes === 0 ? "Ready. Putt when ready." : "Ball stopped. Putt when ready.";
}

function playPill(ui, motion) {
  if (ui && ui.awaiting_tee) {
    if (ui.in_start) return "In start zone · waiting for putt";
    if (ui.ball_seen) return "Ball seen · not in the start circle yet";
    return "Looking for your ball";
  }
  if (motion.moving) return "Ball moving";
  if (motion.hidden) return "Ball stopped, hidden · position estimated";
  if (motion.dist_to_cup != null) return `Ball stopped · ${motion.dist_to_cup} m from cup`;
  return "Ball stopped";
}

S.S12 = function (st) {
  const p = st.ui.player || {};
  const stroke = st.ui.stroke;
  return `
  <div style="position:absolute;inset:0;background:rgba(var(--ground-rgb),.4);z-index:4"></div>
  <div style="position:absolute;left:0;top:0;bottom:0;width:1240px;background:${p.color};color:var(--player-text);border-radius:0 120px 120px 0;box-shadow:40px 0 120px rgba(0,0,0,.4);animation:rg-slidein-left .35s ease-out;display:flex;flex-direction:column;justify-content:flex-end;padding:0 96px 96px;z-index:5">
    <div class="kicker" style="color:var(--player-text);opacity:.65">Next up</div>
    <div style="font:800 230px/1.15 var(--font-display);letter-spacing:-.05em;overflow:hidden">${marquee(p.name || "Player")}</div>
    <div style="font:600 32px/1.35 var(--font-body);opacity:.8;word-spacing:0.12em">${stroke === 0 ? `Place the ${esc(p.hue_name || "next")} ball in the start zone` : `Play the ${esc(p.hue_name || "next")} ball where it lies · stroke ${stroke}`}</div>
    <div style="margin-top:28px">${RG.hint("confirm", "Skip")}</div>
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
  <div class="row" style="position:absolute;left:56px;bottom:48px;gap:12px;z-index:5">
    ${RG.hint("undo", "Wrong call? Undo within 5 s")}
    <button class="btn primary sm" data-action="confirm"><span>Continue</span>${RG.btnHint("confirm")}</button>
  </div>`;
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
  const flyout = !!(ui.recal_flyout || focus === 3);
  const rows = [
    ["Resume", "resume", "back"],
    ["Undo last shot", "undo", "undo"],
    ["Fix score", "fix", "confirm"],
    ["Recalibrate…", "recalibrate", "confirm"],
    ["Change course for this hole", "course", "confirm"],
    ["Music & sound", "music", "confirm"],
    ["Quit to start", "quit", "confirm"],
  ];
  const recal = [
    ["Re-detect cup", "Someone kicked it. Re-runs the cup step only.", "cup"],
    ["Re-assign balls", "Lighting changed or balls swapped.", "balls"],
    ["Redraw play area", "Rug moved or you want a bigger course.", "area"],
    ["Edit obstacles", "Delete or reshape the outlines on the floor.", "obstacles"],
    ["Redo floor snapshot", "Camera or tripod moved. Wizard keeps current values as hints.", "floor"],
    ["Verify only", "Show all zones over the feed and confirm.", "verify"],
  ];
  const active = ui.active || {};
  const scores = ui.scores || {};
  const scoreCard = `
  <div class="glass" style="position:absolute;right:40px;top:40px;width:520px;border-radius:24px;padding:28px 32px;z-index:3">
    <div class="kicker">Hole ${ui.hole} of 3 · Par 3</div>
    <div style="font:800 34px/1 var(--font-display);margin:4px 0 14px">${esc(st.game && st.game.course ? st.game.course.name : "")}</div>
    ${((st.game && st.game.players) || []).map((p) => `<div class="row" style="padding:6px 0">
      ${dot(p.color, 16)}<span style="font:700 22px/1.25 var(--font-display);flex:1">${esc(p.name)}</span>
      <span style="font:800 26px var(--font-display)">${(scores[p.id] || [])[ui.hole - 1] || 0}</span>
    </div>`).join("")}
  </div>`;
  const flyoutCard = `
  <div class="recal-flyout" style="position:absolute;left:744px;top:40px;bottom:40px;width:900px;padding:48px 24px;z-index:3;display:flex;flex-direction:column;box-sizing:border-box">
    <div class="kicker mint" style="margin-top:236px">Recalibrate — pick only what moved</div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:16px">
      ${recal.map(([title, body, kind]) => `<button type="button" class="glass recal-card" data-action="recal_${kind}">
        <div style="font:700 26px var(--font-display)">${esc(title)}</div>
        <div style="font:400 17px/1.45 var(--font-body);color:var(--text-muted);margin-top:6px">${esc(body)}</div>
      </button>`).join("")}
    </div>
    <div style="margin-top:auto;font:400 15px var(--font-body);color:var(--text-muted)">Opens that setup screen with current values. Back returns here with the game intact.</div>
  </div>`;
  return `
  ${photo("s15", "saturate(.75) brightness(.7)", "linear-gradient(90deg,rgba(var(--ground-rgb),.55) 0%,rgba(var(--ground-rgb),.2) 45%,rgba(var(--ground-rgb),.75) 100%)")}
  <div class="glass-strong" style="position:absolute;left:40px;top:40px;bottom:40px;width:680px;border-radius:28px;padding:48px;z-index:3;display:flex;flex-direction:column">
    <div class="kicker muted">Hole ${ui.hole} · ${esc(active.name || "")} to play</div>
    <h2 style="font-size:88px">Paused.</h2>
    <div class="col" style="margin-top:24px;gap:8px">
      ${rows.map(([label, key, verb], i) => `<button class="row" data-action="select" data-index="${i}" style="border-radius:16px;padding:18px 22px;font:700 26px var(--font-display);background:${i === focus ? "var(--mint)" : "var(--fill-quiet)"};color:${i === focus ? "var(--mint-text)" : "var(--text)"};border:none;text-align:left;cursor:pointer">
        <span style="flex:1">${esc(label)}</span>${RG.glyph(verb)}
      </button>`).join("")}
    </div>
    <div style="margin-top:auto;font:400 15px var(--font-body);color:var(--text-muted)">${flyout ? "Recalibrate opens the setup screens with current values pre-filled; Back returns here with the game intact." : "Music ducks to 30% while paused."}</div>
  </div>
  ${flyout ? flyoutCard : scoreCard}
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
      <div style="background:var(--invert);color:var(--invert-text);border-radius:18px;padding:20px 24px;margin-top:16px">
        <div class="kicker" style="color:var(--invert-text);opacity:.7">Undo last shot</div>
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
        ${photo("s17", "saturate(.8)", "linear-gradient(180deg,rgba(var(--ground-rgb),.35) 0%,rgba(var(--ground-rgb),.92) 70%)")}
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
      <div style="margin-top:auto;height:280px;border-radius:20px;background:repeating-linear-gradient(45deg,rgba(var(--ground-rgb),.18) 0 14px,rgba(var(--ground-rgb),.08) 14px 28px);position:relative">
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

function settingsCard(title, desc, inner, extra = "") {
  return `<div class="s-card ${extra}">
    ${title ? `<div class="s-card-title">${esc(title)}</div>${desc ? `<div class="s-card-desc">${esc(desc)}</div>` : ""}` : ""}
    ${inner || ""}
  </div>`;
}

S.S19 = function (st) {
  const s = st.settings || {};
  s.music = s.music || {};
  s.sfx = s.sfx || {};
  s.controller = s.controller || {};
  s.display = s.display || {};
  const ui = st.ui || {};
  const tab = ui.tab || "display";
  const theme = s.theme || "dark";
  const rules = s.rules || {};
  const cam = ui.camera || {};
  const sensor = st.sensor;
  const tabKeys = ["display", "rules", "players", "camera", "about"];

  const displayTab = `
    <div class="s-card s-inline">
      <div class="grow"><div class="s-card-title">Theme</div><div class="s-card-desc">Dark keeps the room dim for the camera; light is for daytime play.</div></div>
      <div class="seg">${["dark", "light", "auto"].map((t) => `<button type="button" class="opt ${theme === t ? "sel" : ""}" data-set-theme="${t}">${t[0].toUpperCase()}${t.slice(1)}</button>`).join("")}</div>
    </div>
    <div class="s-card" style="display:flex;flex-direction:column;gap:18px">
      <div class="s-inline">
        <div class="grow"><div class="s-card-title">Music</div><div class="s-card-desc">Lo-fi & jazz, CC-licensed. Ducks to 30% on pause.</div></div>
        <div class="seg">${["On", "Off"].map((t, i) => `<button type="button" class="opt mint ${(s.music.enabled ? 0 : 1) === i ? "sel" : ""}" data-set-music="${i === 0}">${t}</button>`).join("")}</div>
      </div>
      <div class="s-inline"><span class="text-muted" style="width:120px">Volume</span><input class="slider grow" type="range" min="0" max="100" value="${Math.round((s.music.volume || 0) * 100)}" data-set-musicvol><span style="font:700 18px var(--font-display);width:56px;text-align:right">${Math.round((s.music.volume || 0) * 100)}%</span></div>
      <div class="s-inline"><span class="text-muted" style="width:120px">Playlist</span><div class="row" style="flex:1;flex-wrap:wrap;gap:8px">${[
        ["lounge-jazz", "Lounge jazz"], ["lofi-hiphop", "Lo-fi hip hop"], ["both-shuffled", "Both, shuffled"]
      ].map(([v, l]) => `<button type="button" class="chip ${(s.music.playlist || "lounge-jazz") === v ? "sel" : ""}" data-set-playlist="${v}">${l}</button>`).join("")}</div></div>
    </div>
    <div class="s-card" style="display:flex;flex-direction:column;gap:18px">
      <div class="s-inline">
        <div class="grow"><div class="s-card-title">Sound effects</div><div class="s-card-desc">Putter tick, bumps, cup plink, cheers, turn sting.</div></div>
        <div class="seg">${["On", "Off"].map((t, i) => `<button type="button" class="opt mint ${(s.sfx.enabled ? 0 : 1) === i ? "sel" : ""}" data-set-sfx="${i === 0}">${t}</button>`).join("")}</div>
      </div>
      <div class="s-inline"><span class="text-muted" style="width:120px">Volume</span><input class="slider grow" type="range" min="0" max="100" value="${Math.round((s.sfx.volume || 0) * 100)}" data-set-sfxvol><span style="font:700 18px var(--font-display);width:56px;text-align:right">${Math.round((s.sfx.volume || 0) * 100)}%</span></div>
      <div class="s-inline">
        <div class="grow"><div style="font:700 20px var(--font-display)">Announcer</div><div class="s-card-desc">Calls the next player’s name out loud.</div></div>
        <button type="button" class="toggle ${s.sfx.announcer ? "on" : ""}" data-set-announcer><span class="knob"></span></button>
      </div>
    </div>
    <div class="s-card" style="display:grid;grid-template-columns:1fr 1fr;gap:24px">
      <div class="s-inline">
        <div class="grow"><div style="font:700 20px var(--font-display)">Controller rumble</div><div class="s-card-desc">Buzz on hole-out and turn change.</div></div>
        <button type="button" class="toggle ${s.controller.rumble ? "on" : ""}" data-set-rumble><span class="knob"></span></button>
      </div>
      <div class="s-inline">
        <div class="grow"><div style="font:700 20px var(--font-display)">Show camera feed</div><div class="s-card-desc">Off = zones only, no picture.</div></div>
        <button type="button" class="toggle ${s.display.showCameraFeed ? "on" : ""}" data-set-feed><span class="knob"></span></button>
      </div>
    </div>`;

  const rulesTab = `
    ${settingsCard("Holes per game", "1 – 9 holes", `<div class="seg" style="margin-top:12px;flex-wrap:wrap">${[1, 2, 3, 4, 5, 6, 7, 8, 9].map((n) => `<button class="opt ${n === rules.holes ? "sel" : ""}" data-set-rule-holes="${n}">${n}</button>`).join("")}</div>`)}
    ${settingsCard("Stroke cap", "Give up after this many strokes", `<div class="row" style="margin-top:10px">
      <button class="btn secondary" style="width:56px;height:56px;padding:0;justify-content:center" data-set-rule-cap="${(rules.strokeCap || 8) - 1}">−</button>
      <span style="font:700 28px var(--font-display);min-width:120px;text-align:center">${rules.strokeCap || 8} strokes</span>
      <button class="btn secondary" style="width:56px;height:56px;padding:0;justify-content:center" data-set-rule-cap="${(rules.strokeCap || 8) + 1}">+</button>
    </div>`)}
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
      ${settingsCard("Out-of-bounds penalty", "Add +1 when a ball leaves the area", `<div class="row"><button type="button" class="toggle ${rules.oobPenalty ? "on" : ""}" data-set-rule-oob style="margin-left:auto"><span class="knob"></span></button></div>`)}
      ${settingsCard("Tunnel bonus", "−1 when a ball goes through a tunnel", `<div class="row"><button type="button" class="toggle ${rules.tunnelBonus ? "on" : ""}" data-set-rule-tunnel style="margin-left:auto"><span class="knob"></span></button></div>`)}
    </div>`;

  const players = (st.game && st.game.players) || [];
  const playersTab = players.length
    ? settingsCard("Players", "Saved names & colors", `<div class="col" style="gap:10px;margin-top:14px">${players.map((p) => `<div class="row" style="background:var(--fill-quiet);border-radius:14px;padding:12px 16px">${dot(p.color, 32)}<span style="font:700 24px var(--font-display)">${esc(p.name)}</span><span style="margin-left:auto;font:400 16px var(--font-body);color:var(--text-muted)">${esc(p.hue_name || "")}</span></div>`).join("")}</div>`)
    : settingsCard("Players", "No players yet", `<div style="font:400 17px var(--font-body);color:var(--text-muted);margin-top:8px">Players are created during setup when you put colored balls on the floor (step 5).</div>`);

  const facts = sensor ? [
    ["In use", sensor.model],
    ["Color", `${sensor.color_res[0]} × ${sensor.color_res[1]}`],
    ["Depth", (sensor.depth_res && sensor.depth_res[0]) ? `${sensor.depth_res[0]} × ${sensor.depth_res[1]}` : "— (color only)"],
    ["Field of view", `${sensor.fov_h_deg}°`],
    ["Reliable range", `${sensor.reliable_min_m} – ${sensor.reliable_max_m} m`],
    ["Note", sensor.note || ""],
  ] : [["In use", "No camera attached"]];
  const devices = cam.devices || [];
  const selectedName = ((devices.find((d) => d.index === cam.active_device) || {}).driver)
    || ((devices.find((d) => d.index === cam.active_device) || {}).name)
    || "the selected camera";
  const mockWarn = cam.is_mock
    ? `<div class="warn-banner" style="margin-bottom:12px">Still on the mock sensor. Selected device is <strong>${esc(selectedName)}</strong>. Click Apply camera — close any other app using it first.</div>`
    : "";
  const err = cam.error ? `<div class="warn-banner" style="margin-bottom:12px">${esc(cam.error)}</div>` : "";
  const cameraTab = `
    ${mockWarn}${err}
    ${settingsCard("Live preview", "What this camera sees right now", `<div class="feed-slot" data-feed-slot style="margin-top:14px;aspect-ratio:16/9;border-radius:16px"></div>`)}
    ${settingsCard("Sensor", "What is actually attached right now", `<dl class="s-fact">${facts.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("")}</dl>`)}
    ${settingsCard("Camera input", "Choose which device watches the floor", `
      <div style="margin-top:14px;display:flex;flex-direction:column;gap:12px">
        <div class="s-form-row"><label>Device</label>
          ${devices.length
            ? `<select class="select grow" data-set-camera-device>${devices.map((d) => `<option value="${d.index}" ${d.index === cam.active_device ? "selected" : ""}>${esc(d.driver || d.name)}${d.working ? "" : " — no signal"}</option>`).join("")}</select>`
            : `<span class="text-muted" style="font:400 17px var(--font-body)">${cam.scanning ? "Scanning for cameras…" : "No cameras found"}</span>`}
        </div>
        <div class="s-form-row"><label>Resolution</label>
          <select class="select grow" data-set-camera-resolution>${["640x480", "1280x720", "1920x1080"].map((r) => `<option value="${r}" ${r === cam.resolution ? "selected" : ""}>${r}</option>`).join("")}</select>
        </div>
        <div class="s-form-row"><label>Backend</label>
          <select class="select grow" data-set-camera-backend>${[["auto", "Auto (Kinect → webcam → mock)"], ["webcam", "Webcam (2D)"], ["kinect", "Kinect only"]].map(([v, l]) => `<option value="${v}" ${v === cam.backend ? "selected" : ""}>${l}</option>`).join("")}</select>
        </div>
      </div>
      <div class="row" style="margin-top:18px;gap:12px">
        <button class="btn primary sm" data-action="apply_camera"><span>Apply camera</span></button>
        <button class="btn secondary sm" data-action="refresh_cameras"><span>Rescan</span></button>
      </div>`)}`;

  const aboutTab = `
    ${settingsCard("Rumpus Golf", "Turn any floor into a mini golf course", `<div style="font:800 56px/1 var(--font-display);margin-top:10px">v${st.version}</div><div style="font:400 17px/1.5 var(--font-body);color:var(--text-muted);margin-top:10px">Python 3.11 · OpenCV · FastAPI. No machine learning, no cloud — just your living room.</div>`)}
    ${settingsCard("Credits & licenses", "Royalty-free music, sounds & photos", `<div class="col" style="gap:10px;margin-top:12px">
      <button class="btn secondary sm" data-action="credits" style="justify-content:space-between"><span>Credits</span><span>→</span></button>
      <button class="btn secondary sm" data-action="changelog" style="justify-content:space-between"><span>What's new</span><span>→</span></button>
    </div>`)}`;

  const tabContent = { display: displayTab, rules: rulesTab, players: playersTab, camera: cameraTab, about: aboutTab }[tab] || displayTab;

  const previewLight = theme === "light" || (theme === "auto" && window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches);
  return `
  ${photo("s19", "saturate(.7) brightness(.75)", "linear-gradient(90deg,rgba(var(--ground-rgb),.97) 0%,rgba(var(--ground-rgb),.92) 48%,rgba(var(--ground-rgb),.35) 100%)")}
  <div class="topbar" style="border:none"><span class="brand">Rumpus Golf</span><span class="subtitle">Settings</span><span class="right">${RG.badge()}<button type="button" class="hint back-btn" data-action="back">${RG.glyph("back")}<span>Back</span></button></span></div>
  <div class="s19-rail">
    ${["Display & sound", "Game rules", "Players", "Camera", "About"].map((t, i) => `<button type="button" class="s19-tab ${tabKeys[i] === tab ? "on" : ""}" data-action="settings-tab" data-index="${i}">${t}</button>`).join("")}
    <div style="margin-top:auto;font:400 14px/1.5 var(--font-body);color:var(--text-muted)">Settings save instantly. Nothing here touches the calibration file.</div>
  </div>
  <div class="s19-main">${tabContent}</div>
  <div class="s19-aside">
    <div class="kicker">Theme preview · ${previewLight ? "Light" : "Dark"}</div>
    <div class="theme-preview ${previewLight ? "" : "dark"}">
      <div style="background:#ff8a3d;color:#15171c;border-radius:14px;padding:16px 20px">
        <div class="kicker" style="font-size:11px;color:#15171c;opacity:.7">Your turn</div>
        <div style="font:800 40px/1.2 var(--font-display);letter-spacing:-.03em;margin-top:4px">Leo</div>
      </div>
      <div class="row" style="gap:8px">
        <span class="theme-mini-chip">${dot("#ff5fa8", 12)} Maya</span>
        <span class="theme-mini-chip">${dot("#5b8cff", 12)} Sam</span>
        <span class="theme-mini-chip">${dot("#ffd84d", 12)} Rio</span>
      </div>
      <div style="display:flex;align-items:center;gap:12px;background:#0f7a5a;color:#f2efe8;padding:14px 18px;border-radius:12px;font:700 18px var(--font-display)"><span>Primary action</span><span style="margin-left:auto">${RG.glyph("confirm")}</span></div>
      <div style="font:400 13px/1.45 var(--font-body);color:#5c6068">Light theme swaps ground/ink and deepens mint so contrast stays above 4.5:1. Ball colors are untouched.</div>
    </div>
    <div class="s19-aside-links">
      <button type="button" class="s19-link" data-action="credits"><span>Credits</span><span class="meta">→</span></button>
      <button type="button" class="s19-link" data-action="changelog"><span>What’s new</span><span class="pill" style="margin-left:auto;background:var(--mint);color:var(--mint-text);font:700 13px var(--font-display)">v${st.version}</span></button>
    </div>
  </div>`;
};

S.S20 = function (st) {
  return `
  ${photo("s20", "saturate(.8) brightness(.7)", "linear-gradient(90deg,rgba(var(--ground-rgb),.3) 0%,rgba(var(--ground-rgb),.85) 45%,rgba(var(--ground-rgb),.97) 100%)")}
  <div class="topbar" style="border:none"><span class="brand">Rumpus Golf</span><span class="subtitle">Credits</span><span class="right">${RG.badge()}<button type="button" class="hint back-btn" data-action="back">${RG.glyph("back")}<span>Back</span></button></span></div>
  <div style="position:absolute;left:56px;bottom:56px;width:560px;z-index:2">
    <div class="kicker mint">Made with</div>
    <div style="font:800 88px/1 var(--font-display);letter-spacing:-.04em;text-shadow:0 2px 20px rgba(0,0,0,.5)">Books,<br>a cup and<br>a camera.</div>
    <div style="font:400 18px/1.5 var(--font-body);color:var(--text-soft)">Rumpus Golf v${st.version} · Python 3.11 · OpenCV · libfreenect / libfreenect2. No machine learning, no cloud — just your living room.</div>
  </div>
  <div style="position:absolute;left:760px;right:56px;top:120px;bottom:56px;display:grid;grid-template-columns:1fr 1fr;gap:16px;overflow:auto;z-index:2">
    ${creditGroup("Music", [['"Lobby Time" Kevin MacLeod (incompetech.com)', "CC BY 4.0"], ['"Backed Vibes Clean" Kevin MacLeod (incompetech.com)', "CC BY 4.0"]])}
    ${creditGroup("Sound effects", [["Kenney — Interface Sounds", "CC0"], ["Kenney — Impact Sounds", "CC0"], ["Freesound — small crowd cheer", "CC0"]])}
    ${creditGroup("Photography", [["Alex Gruber", "Unsplash"], ["Minh Pham", "Unsplash"], ["mark tulin", "Unsplash"], ["Waldemar Brandt", "Unsplash"], ["Clay Banks", "Unsplash"], ["Kayla Farmer", "Unsplash"], ["Spacejoy", "Unsplash"], ["Aaron Burden", "Unsplash"], ["Katja Rooke", "Unsplash"]])}
    ${creditGroup("Open source", [["OpenCV", "Apache 2.0"], ["libfreenect", "Apache 2.0 / GPL 2"], ["libfreenect2", "Apache 2.0 / GPL 2"]])}
  </div>`;
};

function creditGroup(title, items) {
  return `<div class="glass" style="border-radius:20px;padding:24px 28px">
    <div class="kicker mint">${title}</div>
    <div class="col" style="margin-top:12px;gap:10px">
      ${items.map(([t, l]) => `<div class="row" style="justify-content:space-between;gap:12px"><span style="font:700 19px/1.3 var(--font-display)">${esc(t)}</span><span class="pill license">${esc(l)}</span></div>`).join("")}
    </div>
  </div>`;
}

S.S21 = function (st) {
  const rels = st.ui.changelog || [];
  return `
  ${photo("s21", "saturate(.7) brightness(.7)", "linear-gradient(90deg,rgba(var(--ground-rgb),.97) 0%,rgba(var(--ground-rgb),.9) 55%,rgba(var(--ground-rgb),.3) 100%)")}
  <div class="topbar" style="border:none"><span class="brand">Rumpus Golf</span><span class="subtitle">What’s new</span><span class="right">${RG.badge()}<button type="button" class="hint back-btn" data-action="back">${RG.glyph("back")}<span>Back</span></button></span></div>
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
