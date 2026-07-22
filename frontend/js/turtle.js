/* SHARD turtle helpers. */

/* ---------- Turtle syntax highlighting (textarea overlay) ----------
   Textareas can't colour individual characters, so we render a coloured <pre>
   layer behind a transparent-text textarea and keep them scroll-synced. */
const TURTLE_RE = /(#[^\n]*)|("""[\s\S]*?"""|'''[\s\S]*?'''|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')|(<[^>\s]*>)|(@prefix\b|@base\b|PREFIX\b|BASE\b)|(\^\^)|(@[A-Za-z][A-Za-z0-9-]*)|([A-Za-z_][\w.\-]*:[\w.\-%]*|:[\w.\-%]+)|(\ba\b)|(\b(?:true|false)\b)|([+-]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?)|([;,\[\]()])/g;

const TURTLE_CORE_PREFIXES = new Set([
  "rdf", "rdfs", "owl", "xsd", "skos", "dct", "dcterms", "prov",
]);

function turtleQNameClass(source, index, token) {
  const lineStart = source.lastIndexOf("\n", Math.max(0, index - 1)) + 1;
  const beforeToken = source.slice(lineStart, index);
  if (/(?:@prefix|PREFIX)\s+$/i.test(beforeToken)) return "tk-prefix-decl";

  const separator = token.indexOf(":");
  const prefix = separator >= 0 ? token.slice(0, separator).toLowerCase() : "";
  const localName = separator >= 0 ? token.slice(separator + 1) : token;
  if (prefix === "sh") return "tk-shacl";
  if (TURTLE_CORE_PREFIXES.has(prefix)) return "tk-vocab";
  if (prefix.includes("shape") || /shape$/i.test(localName)) return "tk-shape";
  return "tk-ontology";
}

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
    else if (m[7]) cls = turtleQNameClass(src, m.index, m[0]);
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

function wireExpandableCodeEditors() {
  document.querySelectorAll("[data-expand-editor]").forEach((button) => {
    if (button.dataset.expandWired === "true") return;
    const source = byId(button.dataset.expandEditor);
    if (!source) return;

    const modalId = `expanded-${source.id}`;
    const titleId = `${modalId}-title`;
    const highlightId = `${modalId}-hl`;
    const dialog = document.createElement("dialog");
    dialog.className = "editor-modal";
    dialog.id = `${modalId}-dialog`;
    dialog.setAttribute("aria-labelledby", titleId);
    dialog.innerHTML = `
      <div class="editor-modal-shell">
        <header class="editor-modal-header">
          <h2 id="${titleId}"></h2>
          <button class="icon-button editor-modal-close" type="button" title="Close expanded editor" aria-label="Close expanded editor">
            <svg viewBox="0 0 24 24" aria-hidden="true"><line x1="18" x2="6" y1="6" y2="18"></line><line x1="6" x2="18" y1="6" y2="18"></line></svg>
          </button>
        </header>
        <div class="code-wrap editor-modal-code-wrap">
          <pre class="code-highlight" aria-hidden="true"><code id="${highlightId}"></code></pre>
          <textarea id="${modalId}" class="code-editor editor-modal-code-editor" spellcheck="false" wrap="off"></textarea>
        </div>
      </div>`;
    document.body.appendChild(dialog);

    const expanded = byId(modalId);
    const modalTitle = byId(titleId);
    const closeButton = dialog.querySelector(".editor-modal-close");
    let syncing = false;
    attachTurtleHighlighter(modalId, highlightId);

    const titleText = () => {
      const target = button.dataset.expandTitleTarget
        ? byId(button.dataset.expandTitleTarget) : null;
      return (target && target.textContent.trim())
        || button.dataset.expandTitle
        || "Expanded editor";
    };
    const syncToSource = () => {
      if (source.value === expanded.value) return;
      syncing = true;
      source.value = expanded.value;
      source.dispatchEvent(new Event("input", { bubbles: true }));
      refreshHighlight(source.id);
      syncing = false;
    };
    const syncFromSource = () => {
      if (syncing || !dialog.open || expanded.value === source.value) return;
      expanded.value = source.value;
      refreshHighlight(modalId);
    };

    expanded.addEventListener("input", syncToSource);
    source.addEventListener("input", syncFromSource);
    closeButton.addEventListener("click", () => dialog.close());
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) dialog.close();
    });
    dialog.addEventListener("close", syncToSource);
    button.addEventListener("click", () => {
      modalTitle.textContent = titleText();
      expanded.value = source.value;
      expanded.placeholder = source.placeholder || "";
      refreshHighlight(modalId);
      dialog.showModal();
      requestAnimationFrame(() => {
        expanded.focus();
        const cursor = Math.min(source.selectionStart || 0, expanded.value.length);
        expanded.setSelectionRange(cursor, cursor);
      });
    });
    button.dataset.expandWired = "true";
  });
}
