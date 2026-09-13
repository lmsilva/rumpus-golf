// Input-mode glyph rendering (keyboard keycaps vs Xbox glyphs).
// The mode flips when a gamepad connects/disconnects (Gamepad API).
window.RG = window.RG || {};

const VERBS = {
  confirm:   { key: "Enter",  pad: "A",  cls: "g-a",  shape: "circle" },
  back:      { key: "Esc",    pad: "B",  cls: "g-b",  shape: "circle" },
  undo:      { key: "Z",      pad: "X",  cls: "g-x",  shape: "circle" },
  secondary: { key: "Y",      pad: "Y",  cls: "g-y",  shape: "circle" },
  next:      { key: "Tab",    pad: "RB", cls: "g-gray", shape: "rect" },
  prev:      { key: "Shift ⇥", pad: "LB", cls: "g-gray", shape: "rect" },
  menu:      { key: "Esc",    pad: "≡",  cls: "g-gray", shape: "circle" },
  stick:     { key: "← ↑ → ↓", pad: "L", cls: "g-gray", shape: "circle" },
};

RG.inputMode = "keyboard";

function pollGamepads() {
  let found = false;
  try {
    const pads = navigator.getGamepads ? navigator.getGamepads() : [];
    for (const p of pads) if (p && p.buttons && p.buttons.length >= 8) { found = true; break; }
  } catch (e) { /* ignore */ }
  const mode = found ? "controller" : "keyboard";
  if (mode !== RG.inputMode) {
    RG.inputMode = mode;
    if (RG.onInputMode) RG.onInputMode(mode);
  }
}
setInterval(pollGamepads, 1000);

RG.glyph = function (verb) {
  const v = VERBS[verb] || VERBS.confirm;
  if (RG.inputMode === "controller") {
    const shape = v.shape === "circle" ? "circle" : "";
    return `<span class="glyph ${v.cls} ${shape}">${v.pad}</span>`;
  }
  return `<span class="glyph">${v.key}</span>`;
};

RG.hint = function (verb, label) {
  return `<button type="button" class="hintpill" data-action="${verb}">${RG.glyph(verb)}${label ? `<span>${label}</span>` : ""}</button>`;
};

RG.btnHint = function (verb) {
  return `<span class="hint">${RG.glyph(verb)}</span>`;
};

RG.pauseGlyph = function (letter, verb) {
  if (RG.inputMode === "controller" && verb) return RG.glyph(verb);
  return `<span class="glyph">${letter}</span>`;
};

RG.badge = function (framed) {
  const label = RG.inputMode === "controller" ? "Controller connected" : "Keyboard & mouse";
  return `<span class="badge${framed ? " framed" : ""}"><span class="dot"></span>${label}</span>`;
};
