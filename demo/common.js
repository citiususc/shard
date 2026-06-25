/* common.js — shared state and helpers for both pages (br2shacl-ui).
 *
 * This is a locally-served web app (run_demo.py), not a sandboxed artifact, so
 * localStorage is used to share the loaded ontology, model configuration,
 * prefixes and accepted shapes across the two pages.
 */

const SERVICES = {
  parse:    "http://127.0.0.1:9100/parse-ontology",
  terms:    "http://127.0.0.1:9101/find-relevant-terms",
  prepareTerms: "http://127.0.0.1:9101/prepare-ontology-embeddings",
  termStatus:   "http://127.0.0.1:9101/ontology-embedding-status",
  cancelTerms:  "http://127.0.0.1:9101/cancel-ontology-embeddings",
  build:    "http://127.0.0.1:9102/build-shacl-shape",
  validate: "http://127.0.0.1:9102/validate-shape",
  guide:    "http://127.0.0.1:9103/generate-from-guide",
};

/* Inference backends. The model_loader routes by id format (HF ids contain "/",
   Databricks short names do not), so the provider toggle is also a UI filter for
   which catalog to show. Roles: chat (text generation/summaries), vision
   (multimodal, for image description in Mode B), embedding (RAG/ranking).
   Credentials come from the .env (DATABRICKS_TOKEN/DATABRICKS_BASE_URL or HF_TOKEN). */
const MODEL_CATALOG = {
  databricks: {
    chat: [
      "databricks-gpt-oss-120b",
      "databricks-qwen3-next-80b-a3b-instruct",
      "databricks-meta-llama-3-3-70b-instruct",
      "databricks-qwen35-122b-a10b",
      "databricks-gpt-oss-20b",
      "databricks-meta-llama-3-1-8b-instruct",
      "databricks-gemma-3-12b",
      "databricks-llama-4-maverick",
    ],
    vision: ["databricks-gemma-3-12b", "databricks-llama-4-maverick"],
    embedding: ["databricks-qwen3-embedding-0-6b", "databricks-bge-large-en", "databricks-gte-large-en"],
  },
  huggingface: {
    chat: [
      "meta-llama/Llama-3.3-70B-Instruct",
      "openai/gpt-oss-120b",
      "Qwen/Qwen3-Next-80B-A3B-Instruct",
      "Qwen/Qwen3.5-122B-A10B",
      "openai/gpt-oss-20b",
      "meta-llama/Llama-3.1-8B-Instruct",
      "google/gemma-3-12b-it",
      "meta-llama/Llama-4-Maverick-17B-128E-Instruct",
    ],
    vision: ["google/gemma-3-12b-it", "meta-llama/Llama-4-Maverick-17B-128E-Instruct"],
    embedding: ["Qwen/Qwen3-Embedding-0.6B", "BAAI/bge-large-en-v1.5", "Alibaba-NLP/gte-large-en-v1.5"],
  },
};

const STORE = {
  ontology: "t2s.ontology",   // {filename, content, baseNamespace, prefixes, entities}
  models:   "t2s.models",     // {provider, llmModel, textModel, visionModel, embeddingModel}
  accepted: "t2s.accepted",   // [{id, property, shape}]
};

/* ---------- tiny helpers ---------- */
const byId = (id) => document.getElementById(id);
const esc = (s) => String(s == null ? "" : s)
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

function setStatus(text) {
  const pill = byId("status-pill");
  if (pill) pill.textContent = text;
}

function loadJSON(key, fallback) {
  try { return JSON.parse(localStorage.getItem(key)) ?? fallback; }
  catch { return fallback; }
}
function saveJSON(key, value) { localStorage.setItem(key, JSON.stringify(value)); }

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

/* ---------- model config ---------- */
const DEFAULT_PROVIDER = "databricks";

function defaultModels(provider) {
  const c = MODEL_CATALOG[provider];
  return {
    provider,
    llmModel: c.chat[0],
    textModel: c.chat[0],
    visionModel: c.vision[0],
    embeddingModel: c.embedding[0],
  };
}

function getModels() {
  const stored = loadJSON(STORE.models, null);
  const provider = stored && MODEL_CATALOG[stored.provider] ? stored.provider : DEFAULT_PROVIDER;
  const c = MODEL_CATALOG[provider];
  const pick = (val, list) => (list.includes(val) ? val : list[0]);
  return {
    provider,
    llmModel:       pick(stored && stored.llmModel,       c.chat),
    textModel:      pick(stored && stored.textModel,      c.chat),
    visionModel:    pick(stored && stored.visionModel,    c.vision),
    embeddingModel: pick(stored && stored.embeddingModel, c.embedding),
  };
}
function setModels(patch) { saveJSON(STORE.models, { ...getModels(), ...patch }); }

function fillSelect(select, options, selected) {
  if (!select) return;
  select.innerHTML = "";
  options.forEach((opt) => {
    const o = document.createElement("option");
    o.value = opt; o.textContent = opt;
    if (opt === selected) o.selected = true;
    select.appendChild(o);
  });
}

/* Wire the provider toggle + model selects present in a page's rail. ids:
   provider buttons ([data-provider]), llm-model, text-model, vision-model,
   embedding-model (text/vision/embedding optional per page). */
function wireModelControls() {
  function apply(provider, keepSelections) {
    const c = MODEL_CATALOG[provider];
    const sel = keepSelections ? getModels() : defaultModels(provider);
    sel.provider = provider;
    setModels(sel);

    document.querySelectorAll("[data-provider]").forEach((b) =>
      b.classList.toggle("active", b.dataset.provider === provider));

    fillSelect(byId("llm-model"), c.chat, sel.llmModel);
    fillSelect(byId("text-model"), c.chat, sel.textModel);
    fillSelect(byId("vision-model"), c.vision, sel.visionModel);
    fillSelect(byId("embedding-model"), c.embedding, sel.embeddingModel);
  }

  const init = getModels();
  apply(init.provider, true);

  document.querySelectorAll("[data-provider]").forEach((btn) => {
    btn.addEventListener("click", () => {
      apply(btn.dataset.provider, false);
      document.dispatchEvent(new CustomEvent("embedding-model-changed", {
        detail: { embeddingModel: getModels().embeddingModel },
      }));
    });
  });

  const bind = (id, key) => {
    const el = byId(id);
    if (el) el.addEventListener("change", () => setModels({ [key]: el.value }));
  };
  bind("llm-model", "llmModel");
  bind("text-model", "textModel");
  bind("vision-model", "visionModel");
  const embeddingSelect = byId("embedding-model");
  if (embeddingSelect) embeddingSelect.addEventListener("change", () => {
    setModels({ embeddingModel: embeddingSelect.value });
    document.dispatchEvent(new CustomEvent("embedding-model-changed", {
      detail: { embeddingModel: embeddingSelect.value },
    }));
  });
}

/* ---------- ontology ---------- */
function getOntology() { return loadJSON(STORE.ontology, null); }
function setOntology(o) { saveJSON(STORE.ontology, o); }

let ontologyEmbeddingPoll = null;
let activeOntologyEmbedding = null;

async function hashOntologyContent(content) {
  const value = String(content || "");
  if (window.crypto && window.crypto.subtle) {
    const bytes = new TextEncoder().encode(value);
    const digest = await window.crypto.subtle.digest("SHA-256", bytes);
    return Array.from(new Uint8Array(digest))
      .map((b) => b.toString(16).padStart(2, "0")).join("");
  }
  let hash = 2166136261;
  for (let i = 0; i < value.length; i++) {
    hash ^= value.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return `fallback-${(hash >>> 0).toString(16)}`;
}

function ontologySummaryText(o, embeddingState) {
  const properties = o.entities.filter((e) => e.type === "property").length;
  let suffix = "";
  if (embeddingState && embeddingState.status === "ready") {
    suffix = " · semantic index ready";
  } else if (embeddingState && ["preparing", "cancelling"].includes(embeddingState.status)) {
    suffix = ` · indexing ${embeddingState.completed || 0}/${embeddingState.total || o.entities.length}`;
  } else if (embeddingState && embeddingState.status === "error") {
    suffix = " · semantic index unavailable";
  }
  return `${o.filename} · ${o.entities.length} entities · ${properties} properties${suffix}`;
}

function renderOntologyEmbeddingState(o, state) {
  const summary = byId("ontology-summary");
  if (summary && o) summary.textContent = ontologySummaryText(o, state);
  document.dispatchEvent(new CustomEvent("ontology-embeddings-status", {
    detail: state || { status: "missing" },
  }));
}

async function cancelOntologyEmbeddingPreparation(target = activeOntologyEmbedding) {
  if (!target) return;
  if (ontologyEmbeddingPoll) {
    clearTimeout(ontologyEmbeddingPoll);
    ontologyEmbeddingPoll = null;
  }
  if (activeOntologyEmbedding === target) activeOntologyEmbedding = null;
  try {
    await fetch(SERVICES.cancelTerms, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ontology_hash: target.ontologyHash,
        embedding_model: target.embeddingModel,
      }),
      keepalive: true,
    });
  } catch { /* The service may already be stopping or the job may be complete. */ }
}

async function pollOntologyEmbeddingStatus(target) {
  if (activeOntologyEmbedding !== target) return;
  try {
    const res = await fetch(SERVICES.termStatus, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ontology_hash: target.ontologyHash,
        ontology_fingerprint: target.ontologyFingerprint,
        embedding_model: target.embeddingModel,
      }),
    });
    const state = await res.json();
    if (activeOntologyEmbedding !== target) return;
    const current = getOntology();
    if (!current || current.contentHash !== target.ontologyHash
        || getModels().embeddingModel !== target.embeddingModel) return;
    renderOntologyEmbeddingState(current, state);
    if (state.status === "preparing" || state.status === "cancelling") {
      ontologyEmbeddingPoll = setTimeout(
        () => pollOntologyEmbeddingStatus(target), 1000,
      );
    }
  } catch {
    if (activeOntologyEmbedding === target) {
      ontologyEmbeddingPoll = setTimeout(
        () => pollOntologyEmbeddingStatus(target), 2000,
      );
    }
  }
}

async function prepareOntologyEmbeddings(o) {
  if (!o || !o.entities || !o.entities.length) return;
  if (!o.contentHash) {
    o.contentHash = await hashOntologyContent(o.content);
    const current = getOntology();
    if (!current || current.content !== o.content) return;
    setOntology(o);
  }

  const embeddingModel = getModels().embeddingModel;
  const target = {
    ontologyHash: o.contentHash,
    embeddingModel,
    payload: {
      ontology_hash: o.contentHash,
      ontology_terms: o.entities,
      embedding_model: embeddingModel,
    },
  };

  if (activeOntologyEmbedding
      && (activeOntologyEmbedding.ontologyHash !== target.ontologyHash
          || activeOntologyEmbedding.embeddingModel !== target.embeddingModel)) {
    await cancelOntologyEmbeddingPreparation(activeOntologyEmbedding);
  }
  activeOntologyEmbedding = target;

  try {
    const res = await fetch(SERVICES.prepareTerms, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(target.payload),
    });
    const state = await res.json();
    if (activeOntologyEmbedding !== target) return;
    target.ontologyFingerprint = state.ontology_fingerprint;
    renderOntologyEmbeddingState(o, state);
    if (state.status === "preparing" || state.status === "cancelling") {
      ontologyEmbeddingPoll = setTimeout(
        () => pollOntologyEmbeddingStatus(target), 1000,
      );
    }
  } catch {
    renderOntologyEmbeddingState(o, {
      status: "error",
      message: "Could not start ontology embedding preparation.",
    });
  }
}

/* Wire ontology upload + base-namespace + prefixes. Calls onLoaded(ontology)
   after a successful parse (and once on init if an ontology is already stored). */
async function wireOntologyControls(onLoaded) {
  const fileInput = byId("ontology-file");
  const summary = byId("ontology-summary");
  const nsInput = byId("base-namespace");
  const prefixEditor = byId("prefixes-editor");
  const resetPrefixes = byId("reset-prefixes");

  const renderFromStore = () => {
    const o = getOntology();
    if (!o) return;
    if (summary) summary.textContent = ontologySummaryText(o);
    if (nsInput) nsInput.value = o.baseNamespace || "";
    if (prefixEditor) { prefixEditor.value = o.prefixes || ""; refreshHighlight("prefixes-editor"); }
    if (onLoaded) onLoaded(o);
    prepareOntologyEmbeddings(o);
  };

  if (fileInput) {
    fileInput.addEventListener("change", async (ev) => {
      const file = ev.target.files[0];
      if (!file) return;
      const content = await file.text();
      setStatus("Parsing ontology…");
      try {
        const res = await fetch(SERVICES.parse, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ filename: file.name, content }),
        });
        const data = await res.json();
        if (data.error) throw new Error(data.error);
        const previous = getOntology();
        const contentHash = await hashOntologyContent(content);
        if (previous && previous.contentHash && previous.contentHash !== contentHash) {
          await cancelOntologyEmbeddingPreparation({
            ontologyHash: previous.contentHash,
            embeddingModel: getModels().embeddingModel,
          });
        }
        setOntology({
          filename: file.name, content,
          contentHash,
          baseNamespace: data.base_namespace || "",
          prefixes: data.prefixes || "", entities: data.entities || [],
        });
        setStatus(`Ontology loaded (${data.entities.length} entities)`);
        renderFromStore();
      } catch (e) {
        setStatus("Parse failed");
        if (summary) summary.textContent = `Could not parse ontology: ${e.message}`;
      }
    });
  }

  if (nsInput) nsInput.addEventListener("input", () => {
    const o = getOntology(); if (o) { o.baseNamespace = nsInput.value; setOntology(o); }
  });
  if (prefixEditor) prefixEditor.addEventListener("input", () => {
    const o = getOntology(); if (o) { o.prefixes = prefixEditor.value; setOntology(o); }
  });
  if (resetPrefixes) resetPrefixes.addEventListener("click", async () => {
    const o = getOntology(); if (!o) return;
    const res = await fetch(SERVICES.parse, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filename: o.filename, content: o.content }),
    });
    const data = await res.json();
    o.prefixes = data.prefixes || ""; setOntology(o);
    if (prefixEditor) { prefixEditor.value = o.prefixes; refreshHighlight("prefixes-editor"); }
  });

  document.addEventListener("embedding-model-changed", async () => {
    const o = getOntology();
    if (!o) return;
    if (activeOntologyEmbedding) {
      await cancelOntologyEmbeddingPreparation(activeOntologyEmbedding);
    }
    prepareOntologyEmbeddings(o);
  });

  renderFromStore();
}

/* ---------- accepted shapes (shared across pages) ---------- */
function getAccepted() { return loadJSON(STORE.accepted, []); }
function setAccepted(list) { saveJSON(STORE.accepted, list); }

function acceptShape(property, shape) {
  const list = getAccepted();
  const id = "shp-" + Date.now() + "-" + Math.random().toString(36).slice(2, 6);
  list.push({ id, property: property || "(shape)", shape });
  setAccepted(list);
  return id;
}
function removeAccepted(id) { setAccepted(getAccepted().filter((s) => s.id !== id)); }

function renderAccepted(listEl, countEl) {
  const list = getAccepted();
  if (countEl) countEl.textContent = `${list.length} accepted`;
  if (!listEl) return;
  listEl.innerHTML = "";
  list.forEach((s) => {
    const row = document.createElement("div");
    row.className = "accepted-item";
    row.innerHTML = `<code>${esc(s.property)}</code>`;
    const del = document.createElement("button");
    del.className = "text-button"; del.textContent = "Remove";
    del.addEventListener("click", () => { removeAccepted(s.id); renderAccepted(listEl, countEl); });
    row.appendChild(del);
    listEl.appendChild(row);
  });
}

/* ---------- export ---------- */
function buildTurtleDocument(extraNodeShapes) {
  const o = getOntology();
  const prefixes = (o && o.prefixes) || "";
  const bodies = getAccepted().map((s) => s.shape.trim()).filter(Boolean);
  let doc = prefixes.trim() + "\n\n";
  if (extraNodeShapes && extraNodeShapes.trim()) doc += extraNodeShapes.trim() + "\n\n";
  doc += bodies.join("\n\n") + "\n";
  return doc;
}

function downloadText(filename, text) {
  const blob = new Blob([text], { type: "text/turtle" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = filename;
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
}

function wireExport(buttonId, getNodeShapes) {
  const btn = byId(buttonId);
  if (!btn) return;
  btn.addEventListener("click", () => {
    if (getAccepted().length === 0) { setStatus("No accepted shapes to export"); return; }
    const node = getNodeShapes ? getNodeShapes() : "";
    downloadText("generated_shapes.ttl", buildTurtleDocument(node));
    setStatus("Exported generated_shapes.ttl");
  });
}

/* ---------- copy / validate ---------- */
async function copyToClipboard(text) {
  try { await navigator.clipboard.writeText(text); return true; }
  catch { return false; }
}

async function validateTurtle(shape, prefixes) {
  const res = await fetch(SERVICES.validate, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ shape, prefixes }),
  });
  return res.json(); // {valid, error}
}

/* ---------- reset ---------- */
function wireReset(buttonId) {
  const btn = byId(buttonId);
  if (!btn) return;
  btn.addEventListener("click", async () => {
    if (!confirm("Clear the loaded ontology and accepted shapes?")) return;
    const o = getOntology();
    if (o) {
      await cancelOntologyEmbeddingPreparation(activeOntologyEmbedding || {
        ontologyHash: o.contentHash,
        embeddingModel: getModels().embeddingModel,
      });
    }
    localStorage.removeItem(STORE.ontology);
    localStorage.removeItem(STORE.accepted);
    location.reload();
  });
}
