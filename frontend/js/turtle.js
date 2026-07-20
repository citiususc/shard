/* SHARD turtle helpers. */

/* ---------- Turtle syntax highlighting (textarea overlay) ----------
   Textareas can't colour individual characters, so we render a coloured <pre>
   layer behind a transparent-text textarea and keep them scroll-synced. */
const TURTLE_RE = /(#[^\n]*)|("""[\s\S]*?"""|'''[\s\S]*?'''|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')|(<[^>\s]*>)|(@prefix\b|@base\b|PREFIX\b|BASE\b)|(\^\^)|(@[A-Za-z][A-Za-z0-9-]*)|([A-Za-z_][\w.\-]*:[\w.\-%]*|:[\w.\-%]+)|(\ba\b)|(\b(?:true|false)\b)|([+-]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?)|([;,\[\]()])/g;

function highlightTurtle(src) {
  let out = "", last = 0, m;
  TURTLE_RE.lastIndex = 0;
  while ((m = TURTLE_RE.exec(src)) !== null) {
    if (m.index > last) out += esc(src.slice(last, m.index));
    const t = esc(m[0]);
    let cls = "tk";
    if (m[1]) cls = "tk-comment";
    else if (m[2]) cls = "tk-string";
    else if (m[3]) cls = "tk-iri";
    else if (m[4]) cls = "tk-directive";
    else if (m[5]) cls = "tk-op";
    else if (m[6]) cls = "tk-lang";
    else if (m[7]) cls = "tk-pname";
    else if (m[8]) cls = "tk-kw";
    else if (m[9]) cls = "tk-bool";
    else if (m[10]) cls = "tk-num";
    else if (m[11]) cls = "tk-punc";
    out += `<span class="${cls}">${t}</span>`;
    last = m.index + m[0].length;
  }
  out += esc(src.slice(last));
  return out;
}

function attachTurtleHighlighter(taId, preId) {
  const ta = byId(taId), code = byId(preId);
  if (!ta || !code) return;
  const viewport = code.closest(".code-highlight") || code;
  const update = () => { code.innerHTML = highlightTurtle(ta.value) + "\n"; };
  const sync = () => {
    viewport.scrollTop = ta.scrollTop;
    viewport.scrollLeft = ta.scrollLeft;
  };
  ta.addEventListener("input", update);
  ta.addEventListener("scroll", sync);
  ta._refreshHL = () => { update(); sync(); };
  if (window.ResizeObserver) {
    const observer = new ResizeObserver(sync);
    observer.observe(ta);
    ta._highlightResizeObserver = observer;
  }
  update();
  sync();
}

function refreshHighlight(taId) {
  const ta = byId(taId);
  if (ta && ta._refreshHL) ta._refreshHL();
}
