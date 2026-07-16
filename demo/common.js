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
  validateModel: "http://127.0.0.1:9102/validate-model",
  guide:    "http://127.0.0.1:9103/generate-from-guide",
};

/* Suggested inference backends. Users can add extra models from the UI; these
   lists are only defaults so a fresh browser session starts with useful choices. */
const MODEL_CATALOG = {
  databricks: {
    chat: [
      "gemma-3-12b",
      "qwen35-122b-a10b",
      "gpt-oss-20b",
      "gpt-oss-120b",
      "glm-5-2",
      "meta-llama-3-1-8b-instruct",
      "qwen3-next-80b-a3b-instruct",
      "meta-llama-3-3-70b-instruct",
      "llama-4-maverick",
      "databricks-genie",
    ],
    vision: [
      "gemma-3-12b",
      "llama-4-maverick",
    ],
    embedding: [
      "gte-large-en",
      "bge-large-en",
      "qwen3-embedding-0-6b",
    ],
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
  models:   "t2s.models",     // provider + credentials + selected/default/custom models
  accepted: "t2s.accepted",   // [{id, property, shape}]
  shapeProfiles: "t2s.shapeProfiles", // [{id, name, size, content}]
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

function makeRequestId() {
  if (window.crypto && window.crypto.randomUUID) return window.crypto.randomUUID();
  return "req-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 8);
}

async function responsePayload(res) {
  const text = await res.text();
  if (!text) return {};
  try { return JSON.parse(text); }
  catch { return { message: text }; }
}

async function fetchJSON(url, options = {}, meta = {}) {
  const timeoutMs = meta.timeoutMs || 30000;
  const label = meta.label || "Request";
  const requestId = meta.requestId || makeRequestId();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const headers = {
      "Content-Type": "application/json",
      "X-Request-ID": requestId,
      ...(options.headers || {}),
    };
    const res = await fetch(url, { ...options, headers, signal: controller.signal });
    const payload = await responsePayload(res);
    if (!res.ok) {
      const msg = payload.message || payload.error || `${label} failed with HTTP ${res.status}`;
      const err = new Error(`${msg} (request ${payload.request_id || requestId})`);
      err.status = res.status;
      err.payload = payload;
      err.requestId = payload.request_id || requestId;
      throw err;
    }
    return payload;
  } catch (err) {
    if (err.name === "AbortError") {
      throw new Error(`${label} timed out after ${Math.round(timeoutMs / 1000)}s (request ${requestId})`);
    }
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

async function fetchStream(url, options = {}, meta = {}) {
  const timeoutMs = meta.timeoutMs || 30000;
  const label = meta.label || "Streaming request";
  const requestId = meta.requestId || makeRequestId();
  const controller = meta.controller || new AbortController();
  let timedOut = false;
  const timer = timeoutMs ? setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs) : null;
  try {
    const headers = {
      "Content-Type": "application/json",
      "X-Request-ID": requestId,
      ...(options.headers || {}),
    };
    const res = await fetch(url, { ...options, headers, signal: controller.signal });
    clearTimeout(timer);
    if (!res.ok || !res.body) {
      const payload = await responsePayload(res);
      const msg = payload.message || payload.error || `${label} failed with HTTP ${res.status}`;
      const err = new Error(`${msg} (request ${payload.request_id || requestId})`);
      err.status = res.status;
      err.payload = payload;
      err.requestId = payload.request_id || requestId;
      throw err;
    }
    return res;
  } catch (err) {
    if (timer) clearTimeout(timer);
    if (err.name === "AbortError") {
      if (!timedOut) {
        const cancelErr = new Error(`${label} cancelled (request ${requestId})`);
        cancelErr.name = "AbortError";
        cancelErr.cancelled = true;
        cancelErr.requestId = requestId;
        throw cancelErr;
      }
      throw new Error(`${label} timed out after ${Math.round(timeoutMs / 1000)}s (request ${requestId})`);
    }
    throw err;
  }
}

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
const DEFAULT_TEMPERATURE = 0.5;

const MODEL_ROLE_CATALOG = {
  llmModel: "chat",
  textModel: "chat",
  visionModel: "vision",
  embeddingModel: "embedding",
};

const MODEL_ROLE_SELECTS = [
  { key: "llmModel", selectId: "llm-model", label: "Generation LLM", catalogRole: "chat" },
  { key: "textModel", selectId: "text-model", label: "Text model", catalogRole: "chat" },
  { key: "visionModel", selectId: "vision-model", label: "Vision model", catalogRole: "vision" },
  { key: "embeddingModel", selectId: "embedding-model", label: "Embedding model", catalogRole: "embedding" },
];

function emptyCustomModels() {
  return {
    databricks: { chat: [], vision: [], embedding: [] },
    huggingface: { chat: [], vision: [], embedding: [] },
  };
}

function uniqueList(values) {
  return Array.from(new Set((values || []).filter(Boolean).map(String)));
}

function normaliseCustomModels(value) {
  const out = emptyCustomModels();
  ["databricks", "huggingface"].forEach((provider) => {
    ["chat", "vision", "embedding"].forEach((role) => {
      out[provider][role] = uniqueList(
        value && value[provider] && Array.isArray(value[provider][role])
          ? value[provider][role].map((model) => normalizeModelId(provider, model))
          : [],
      );
    });
  });
  return out;
}

function clampTemperature(value) {
  const n = Number.parseFloat(value);
  if (!Number.isFinite(n)) return DEFAULT_TEMPERATURE;
  return Math.min(2, Math.max(0, n));
}

function normalizeModelId(provider, modelId) {
  let id = String(modelId || "").trim();
  if (provider === "databricks") {
    if (id.startsWith("system.ai.")) id = id.slice("system.ai.".length);
    if (id.startsWith("databricks-") && id !== "databricks-genie") {
      id = id.slice("databricks-".length);
    }
  }
  return id;
}

function defaultModels(provider) {
  const c = MODEL_CATALOG[provider];
  return {
    provider,
    llmModel: c.chat[0],
    textModel: c.chat[0],
    visionModel: c.vision[0],
    embeddingModel: c.embedding[0],
    temperature: DEFAULT_TEMPERATURE,
    databricks: { token: "", baseUrl: "" },
    huggingface: { token: "" },
    customModels: emptyCustomModels(),
  };
}

function defaultModelSelection(provider) {
  const c = MODEL_CATALOG[provider] || MODEL_CATALOG[DEFAULT_PROVIDER];
  return {
    llmModel: c.chat[0],
    textModel: c.chat[0],
    visionModel: c.vision[0],
    embeddingModel: c.embedding[0],
  };
}

function catalogOptions(provider, catalogRole, customModels) {
  const defaults = (MODEL_CATALOG[provider] && MODEL_CATALOG[provider][catalogRole]) || [];
  const custom = customModels && customModels[provider] && customModels[provider][catalogRole]
    ? customModels[provider][catalogRole]
    : [];
  return uniqueList([...defaults, ...custom]);
}

function getModels() {
  const stored = loadJSON(STORE.models, null);
  const provider = stored && MODEL_CATALOG[stored.provider] ? stored.provider : DEFAULT_PROVIDER;
  const customModels = normaliseCustomModels(stored && stored.customModels);
  const pick = (key) => {
    const catalogRole = MODEL_ROLE_CATALOG[key];
    const storedValue = normalizeModelId(provider, stored && stored[key]);
    const defaults = catalogOptions(provider, catalogRole, customModels);
    return defaults.includes(storedValue) ? storedValue : defaults[0];
  };
  return {
    provider,
    llmModel:       pick("llmModel"),
    textModel:      pick("textModel"),
    visionModel:    pick("visionModel"),
    embeddingModel: pick("embeddingModel"),
    temperature: clampTemperature(stored && stored.temperature),
    databricks: {
      token: (stored && stored.databricks && stored.databricks.token) || "",
      baseUrl: (stored && stored.databricks && (stored.databricks.baseUrl || stored.databricks.base_url)) || "",
    },
    huggingface: {
      token: (stored && stored.huggingface && stored.huggingface.token) || "",
    },
    customModels,
  };
}

function mergeModels(base, patch) {
  return {
    ...base,
    ...patch,
    databricks: { ...(base.databricks || {}), ...(patch.databricks || {}) },
    huggingface: { ...(base.huggingface || {}), ...(patch.huggingface || {}) },
    customModels: patch.customModels ? normaliseCustomModels(patch.customModels) : base.customModels,
  };
}

function setModels(patch) { saveJSON(STORE.models, mergeModels(getModels(), patch)); }

function fillSelect(select, options, selected) {
  if (!select) return;
  options = options || [];
  select.innerHTML = "";
  const finalOptions = uniqueList(selected && !options.includes(selected)
    ? [...options, selected]
    : options);
  finalOptions.forEach((opt) => {
    const o = document.createElement("option");
    o.value = opt; o.textContent = opt;
    if (opt === selected) o.selected = true;
    select.appendChild(o);
  });
}

function getInferenceConfig() {
  const m = getModels();
  return {
    provider: m.provider,
    temperature: m.temperature,
    databricks: {
      token: m.databricks.token || "",
      base_url: m.databricks.baseUrl || "",
    },
    huggingface: {
      token: m.huggingface.token || "",
    },
  };
}

function semanticSettingsStatus(models = getModels()) {
  if (!models.embeddingModel) {
    return {
      ready: false,
      message: "Semantic ranking disabled until model settings are configured.",
    };
  }
  if (models.provider === "databricks"
      && (!models.databricks.token || !models.databricks.baseUrl)) {
    return {
      ready: false,
      message: "Semantic ranking disabled until model settings are configured.",
    };
  }
  return { ready: true, message: "" };
}

function generationSettingsStatus(models = getModels()) {
  if (!models.llmModel) {
    return {
      ready: false,
      message: "Generation disabled until a model is selected.",
    };
  }
  if (models.provider === "databricks"
      && (!models.databricks.token || !models.databricks.baseUrl)) {
    return {
      ready: false,
      message: "Generation disabled until Databricks token and base URL are configured.",
    };
  }
  return { ready: true, message: "" };
}

async function validateSelectedModels(roleKeys) {
  const models = getModels();
  const settings = generationSettingsStatus(models);
  if (!settings.ready) {
    return { ok: false, message: settings.message };
  }

  const uniqueRoleKeys = Array.from(new Set(roleKeys || []));
  for (const roleKey of uniqueRoleKeys) {
    const catalogRole = MODEL_ROLE_CATALOG[roleKey];
    const modelId = models[roleKey];
    if (!catalogRole || !modelId) continue;
    const data = await fetchJSON(SERVICES.validateModel, {
      method: "POST",
      body: JSON.stringify({
        provider: models.provider,
        role: catalogRole,
        model: modelId,
        inference_config: getInferenceConfig(),
      }),
    }, { label: `Validate model '${modelId}'`, timeoutMs: 25000 });
    if (!data.ok) {
      return {
        ok: false,
        role: roleKey,
        model: modelId,
        message: data.message || `Model '${modelId}' is not available.`,
      };
    }
  }
  return { ok: true, message: "Model configuration validated." };
}

function hashString(value) {
  let hash = 2166136261;
  const text = String(value || "");
  for (let i = 0; i < text.length; i++) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(16).padStart(8, "0");
}

function modelConfigFingerprint(models = getModels()) {
  const payload = {
    provider: models.provider,
    databricksBaseUrl: models.databricks.baseUrl || "",
    databricksTokenHash: hashString(models.databricks.token || ""),
    hfTokenHash: hashString(models.huggingface.token || ""),
  };
  return hashString(JSON.stringify(payload));
}

function setModelStatus(message, kind = "") {
  const el = byId("model-config-status");
  if (!el) return;
  el.textContent = message;
  el.classList.toggle("ok", kind === "ok");
  el.classList.toggle("error", kind === "error");
}

/* Wire the provider toggle + model selects present in a page's rail. ids:
   provider buttons ([data-provider]), llm-model, text-model, vision-model,
   embedding-model (text/vision/embedding optional per page), optional
   credential fields and custom-model controls. */
function wireModelControls() {
  function availableRoleRows() {
    return MODEL_ROLE_SELECTS.filter((row) => byId(row.selectId));
  }

  function fillCustomRoleSelect() {
    const roleSelect = byId("custom-model-role");
    if (!roleSelect) return;
    const previous = roleSelect.value;
    roleSelect.innerHTML = "";
    availableRoleRows().forEach((row) => {
      const opt = document.createElement("option");
      opt.value = row.key;
      opt.textContent = row.label;
      roleSelect.appendChild(opt);
    });
    if (previous && Array.from(roleSelect.options).some((o) => o.value === previous)) {
      roleSelect.value = previous;
    }
  }

  function apply(provider, keepSelections) {
    const current = getModels();
    const sel = keepSelections
      ? { ...current, provider }
      : mergeModels(current, { provider, ...defaultModelSelection(provider) });
    saveJSON(STORE.models, sel);

    const fresh = getModels();
    document.querySelectorAll("[data-provider]").forEach((b) =>
      b.classList.toggle("active", b.dataset.provider === provider));
    document.querySelectorAll("[data-provider-config]").forEach((el) => {
      const active = el.dataset.providerConfig === provider;
      el.classList.toggle("is-active", active);
      el.classList.toggle("is-inactive", !active);
      el.setAttribute("aria-hidden", active ? "false" : "true");
      el.querySelectorAll("input, select, textarea, button").forEach((control) => {
        control.disabled = !active;
      });
    });

    MODEL_ROLE_SELECTS.forEach((row) => {
      fillSelect(
        byId(row.selectId),
        catalogOptions(provider, row.catalogRole, fresh.customModels),
        fresh[row.key],
      );
    });

    if (byId("databricks-token")) byId("databricks-token").value = fresh.databricks.token || "";
    if (byId("databricks-base-url")) byId("databricks-base-url").value = fresh.databricks.baseUrl || "";
    if (byId("hf-token")) byId("hf-token").value = fresh.huggingface.token || "";
    if (byId("temperature")) byId("temperature").value = String(fresh.temperature);
    fillCustomRoleSelect();
    setModelStatus("Model settings are stored in this browser and sent with each request.");
  }

  const init = getModels();
  apply(init.provider, true);

  document.querySelectorAll("[data-provider]").forEach((btn) => {
    btn.addEventListener("click", () => {
      apply(btn.dataset.provider, false);
      document.dispatchEvent(new CustomEvent("embedding-model-changed", {
        detail: {
          embeddingModel: getModels().embeddingModel,
          configFingerprint: modelConfigFingerprint(),
        },
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
      detail: {
        embeddingModel: embeddingSelect.value,
        configFingerprint: modelConfigFingerprint(),
      },
    }));
  });

  const bindConfig = (id, patcher, affectsEmbeddings = false) => {
    const el = byId(id);
    if (!el) return;
    el.addEventListener("change", () => {
      setModels(patcher(el.value));
      if (affectsEmbeddings) {
        document.dispatchEvent(new CustomEvent("embedding-model-changed", {
          detail: {
            embeddingModel: getModels().embeddingModel,
            configFingerprint: modelConfigFingerprint(),
          },
        }));
      }
    });
  };
  bindConfig("databricks-token", (value) => ({ databricks: { token: value } }), true);
  bindConfig("databricks-base-url", (value) => ({ databricks: { baseUrl: value.trim() } }), true);
  bindConfig("hf-token", (value) => ({ huggingface: { token: value } }), true);
  bindConfig("temperature", (value) => ({ temperature: clampTemperature(value) }), false);

  const addBtn = byId("add-custom-model");
  if (addBtn) {
    addBtn.addEventListener("click", async () => {
      const roleSelect = byId("custom-model-role");
      const modelInput = byId("custom-model-id");
      const roleKey = roleSelect && roleSelect.value;
      const role = MODEL_ROLE_CATALOG[roleKey];
      const models = getModels();
      const modelId = normalizeModelId(models.provider, modelInput && modelInput.value);
      if (!role || !modelId) {
        setModelStatus("Choose a model role and enter a model id.", "error");
        return;
      }
      setModelStatus(`Checking '${modelId}'…`);
      addBtn.disabled = true;
      try {
        const data = await fetchJSON(SERVICES.validateModel, {
          method: "POST",
          body: JSON.stringify({
            provider: models.provider,
            role,
            model: modelId,
            inference_config: getInferenceConfig(),
          }),
        }, { label: `Validate model '${modelId}'`, timeoutMs: 25000 });
        if (!data.ok) throw new Error(data.message || "Model validation failed.");

        const customModels = normaliseCustomModels(models.customModels);
        const list = customModels[models.provider][role];
        if (!list.includes(modelId)) list.push(modelId);
        setModels({ customModels, [roleKey]: modelId });
        apply(models.provider, true);
        if (modelInput) modelInput.value = "";
        setModelStatus(data.message || `Added '${modelId}'.`, "ok");
        if (roleKey === "embeddingModel") {
          document.dispatchEvent(new CustomEvent("embedding-model-changed", {
            detail: {
              embeddingModel: modelId,
              configFingerprint: modelConfigFingerprint(),
            },
          }));
        }
      } catch (err) {
        setModelStatus(err.message || String(err), "error");
      } finally {
        addBtn.disabled = false;
      }
    });
  }
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
  } else if (embeddingState && embeddingState.status === "disabled") {
    suffix = " · semantic ranking disabled";
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

function normalizeNamespace(ns) {
  const value = String(ns || "").trim();
  if (!value) return "";
  if (value.endsWith("#") || value.endsWith("/")) return value;
  return value + "/";
}

function shapesNamespace(baseNs) {
  const ns = normalizeNamespace(baseNs);
  if (!ns) return "";
  if (ns.endsWith("#")) return ns.slice(0, -1) + "/shapes/";
  return ns + "shapes/";
}

function setPrefixLine(prefixBlock, prefix, namespace) {
  if (!namespace) return prefixBlock || "";
  const name = prefix || "";
  const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const pattern = new RegExp(`(^|\\n)@prefix\\s+${escaped}:\\s*<[^>]*>\\s*\\.`, "m");
  const line = `@prefix ${name}: <${namespace}> .`;
  const current = prefixBlock || "";
  if (pattern.test(current)) {
    return current.replace(pattern, (match, lead) => `${lead}${line}`);
  }
  const trimmed = current.trimEnd();
  return trimmed ? `${trimmed}\n${line}\n` : `${line}\n`;
}

function syncPrefixesWithBaseNamespace(prefixBlock, baseNs) {
  const base = normalizeNamespace(baseNs);
  const shapeNs = shapesNamespace(base);
  if (!base || !shapeNs) return prefixBlock || "";
  let next = prefixBlock || "";
  ["", "onto", "era"].forEach((prefix) => {
    next = setPrefixLine(next, prefix, base);
  });
  ["onto-sh", "shape", "era-sh"].forEach((prefix) => {
    next = setPrefixLine(next, prefix, shapeNs);
  });
  return next;
}

function prefixNamespace(prefixBlock, prefix) {
  const wanted = String(prefix || "");
  const re = /(?:@prefix|PREFIX)\s+([^:\s]*):\s*<([^>]+)>\s*\.?/gi;
  let match;
  while ((match = re.exec(prefixBlock || "")) !== null) {
    if ((match[1] || "") === wanted) return match[2] || "";
  }
  return "";
}

function ensureGeneratorPrefixes(prefixBlock, baseNs) {
  let next = prefixBlock || "";
  const base = normalizeNamespace(baseNs);
  const baseAlias = prefixNamespace(next, "era")
    || prefixNamespace(next, "onto")
    || prefixNamespace(next, "")
    || base;
  const shapeAlias = prefixNamespace(next, "shape")
    || prefixNamespace(next, "era-sh")
    || prefixNamespace(next, "onto-sh")
    || shapesNamespace(base);

  if (!prefixNamespace(next, "era") && baseAlias) {
    next = setPrefixLine(next, "era", baseAlias);
  }
  if (!prefixNamespace(next, "shape") && shapeAlias) {
    next = setPrefixLine(next, "shape", shapeAlias);
  }
  if (!prefixNamespace(next, "era-sh") && shapeAlias) {
    next = setPrefixLine(next, "era-sh", shapeAlias);
  }
  return next;
}

function repairOntologyPrefixes(o) {
  if (!o) return o;
  const repaired = ensureGeneratorPrefixes(o.prefixes || "", o.baseNamespace || "");
  if (repaired === (o.prefixes || "")) return o;
  const next = { ...o, prefixes: repaired };
  setOntology(next);
  return next;
}

async function cancelOntologyEmbeddingPreparation(target = activeOntologyEmbedding) {
  if (!target) return;
  if (ontologyEmbeddingPoll) {
    clearTimeout(ontologyEmbeddingPoll);
    ontologyEmbeddingPoll = null;
  }
  if (activeOntologyEmbedding === target) activeOntologyEmbedding = null;
  try {
    await fetchJSON(SERVICES.cancelTerms, {
      method: "POST",
      body: JSON.stringify({
        ontology_hash: target.ontologyHash,
        embedding_model: target.embeddingModel,
        config_fingerprint: target.configFingerprint,
        inference_config: target.inferenceConfig,
      }),
      keepalive: true,
    }, { label: "Cancel ontology embedding preparation", timeoutMs: 5000 });
  } catch { /* The service may already be stopping or the job may be complete. */ }
}

async function pollOntologyEmbeddingStatus(target) {
  if (activeOntologyEmbedding !== target) return;
  try {
    const state = await fetchJSON(SERVICES.termStatus, {
      method: "POST",
      body: JSON.stringify({
        ontology_hash: target.ontologyHash,
        ontology_fingerprint: target.ontologyFingerprint,
        embedding_model: target.embeddingModel,
        config_fingerprint: target.configFingerprint,
        inference_config: target.inferenceConfig,
      }),
    }, { label: "Ontology embedding status", timeoutMs: 10000 });
    if (activeOntologyEmbedding !== target) return;
    const current = getOntology();
    if (!current || current.contentHash !== target.ontologyHash
        || getModels().embeddingModel !== target.embeddingModel
        || modelConfigFingerprint() !== target.configFingerprint) return;
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
  const semanticSettings = semanticSettingsStatus();
  if (!semanticSettings.ready) {
    if (activeOntologyEmbedding) {
      await cancelOntologyEmbeddingPreparation(activeOntologyEmbedding);
    }
    renderOntologyEmbeddingState(o, {
      status: "disabled",
      completed: 0,
      total: o.entities.length,
      message: semanticSettings.message,
    });
    return;
  }
  if (!o.contentHash) {
    o.contentHash = await hashOntologyContent(o.content);
    const current = getOntology();
    if (!current || current.content !== o.content) return;
    setOntology(o);
  }

  const embeddingModel = getModels().embeddingModel;
  const inferenceConfig = getInferenceConfig();
  const configFingerprint = modelConfigFingerprint();
  const target = {
    ontologyHash: o.contentHash,
    embeddingModel,
    configFingerprint,
    inferenceConfig,
    payload: {
      ontology_hash: o.contentHash,
      ontology_terms: o.entities,
      embedding_model: embeddingModel,
      config_fingerprint: configFingerprint,
      inference_config: inferenceConfig,
    },
  };

  if (activeOntologyEmbedding
      && (activeOntologyEmbedding.ontologyHash !== target.ontologyHash
          || activeOntologyEmbedding.embeddingModel !== target.embeddingModel
          || activeOntologyEmbedding.configFingerprint !== target.configFingerprint)) {
    await cancelOntologyEmbeddingPreparation(activeOntologyEmbedding);
  }
  activeOntologyEmbedding = target;

  try {
    const state = await fetchJSON(SERVICES.prepareTerms, {
      method: "POST",
      body: JSON.stringify(target.payload),
    }, { label: "Prepare ontology embeddings", timeoutMs: 15000 });
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
    const o = repairOntologyPrefixes(getOntology());
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
        const data = await fetchJSON(SERVICES.parse, {
          method: "POST",
          body: JSON.stringify({ filename: file.name, content }),
        }, { label: "Parse ontology", timeoutMs: 30000 });
        if (data.error) throw new Error(data.error);
        const previous = getOntology();
        const contentHash = await hashOntologyContent(content);
        if (previous && previous.contentHash && previous.contentHash !== contentHash) {
          await cancelOntologyEmbeddingPreparation({
            ontologyHash: previous.contentHash,
            embeddingModel: getModels().embeddingModel,
            configFingerprint: modelConfigFingerprint(),
            inferenceConfig: getInferenceConfig(),
          });
        }
        setOntology({
          filename: file.name, content,
          contentHash,
          baseNamespace: data.base_namespace || "",
          prefixes: ensureGeneratorPrefixes(data.prefixes || "", data.base_namespace || ""),
          entities: data.entities || [],
        });
        setStatus(`Ontology loaded (${data.entities.length} entities)`);
        renderFromStore();
      } catch (e) {
        setStatus("Parse failed");
        if (summary) summary.textContent = `Could not parse ontology: ${e.message}`;
      }
    });
  }

  if (nsInput) nsInput.addEventListener("change", () => {
    const o = getOntology();
    if (!o) return;
    o.baseNamespace = normalizeNamespace(nsInput.value);
    o.prefixes = ensureGeneratorPrefixes(
      syncPrefixesWithBaseNamespace(o.prefixes || "", o.baseNamespace),
      o.baseNamespace,
    );
    setOntology(o);
    nsInput.value = o.baseNamespace;
    if (prefixEditor) {
      prefixEditor.value = o.prefixes;
      refreshHighlight("prefixes-editor");
    }
    setStatus("Base namespace and prefixes synchronized");
  });
  if (prefixEditor) prefixEditor.addEventListener("input", () => {
    const o = getOntology(); if (o) { o.prefixes = prefixEditor.value; setOntology(o); }
  });
  if (resetPrefixes) resetPrefixes.addEventListener("click", async () => {
    const o = getOntology(); if (!o) return;
    const data = await fetchJSON(SERVICES.parse, {
      method: "POST",
      body: JSON.stringify({ filename: o.filename, content: o.content }),
    }, { label: "Reset ontology prefixes", timeoutMs: 30000 });
    o.prefixes = ensureGeneratorPrefixes(
      syncPrefixesWithBaseNamespace(data.prefixes || "", o.baseNamespace || data.base_namespace || ""),
      o.baseNamespace || data.base_namespace || "",
    );
    setOntology(o);
    if (prefixEditor) { prefixEditor.value = o.prefixes; refreshHighlight("prefixes-editor"); }
  });

  document.addEventListener("embedding-model-changed", async () => {
    const o = getOntology();
    if (!o) return;
    const semanticSettings = semanticSettingsStatus();
    if (activeOntologyEmbedding) {
      await cancelOntologyEmbeddingPreparation(activeOntologyEmbedding);
    }
    if (!semanticSettings.ready) {
      renderOntologyEmbeddingState(o, {
        status: "disabled",
        completed: 0,
        total: o.entities ? o.entities.length : 0,
        message: semanticSettings.message,
      });
      return;
    }
    prepareOntologyEmbeddings(o);
  });

  renderFromStore();
}

/* ---------- accepted shapes (shared across pages) ---------- */
function getAccepted() { return loadJSON(STORE.accepted, []); }
function setAccepted(list) { saveJSON(STORE.accepted, list); }
function notifyAcceptedShapesChanged(detail = {}) {
  window.dispatchEvent(new CustomEvent("accepted-shapes-changed", {
    detail: { accepted: getAccepted(), ...detail },
  }));
}

function acceptShape(property, shape) {
  const list = getAccepted();
  const id = "shp-" + Date.now() + "-" + Math.random().toString(36).slice(2, 6);
  list.push({ id, property: property || "(shape)", shape });
  setAccepted(list);
  notifyAcceptedShapesChanged({ action: "accept", id });
  return id;
}
function removeAccepted(id) {
  setAccepted(getAccepted().filter((s) => s.id !== id));
  notifyAcceptedShapesChanged({ action: "remove", id });
}

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

/* ---------- generated-shape validation profiles ---------- */
function getShapeValidationProfiles() { return loadJSON(STORE.shapeProfiles, []); }
function setShapeValidationProfiles(list) { saveJSON(STORE.shapeProfiles, Array.isArray(list) ? list : []); }
function removeShapeValidationProfile(id) {
  const next = getShapeValidationProfiles().filter((profile) => (profile.id || profile.name) !== id);
  setShapeValidationProfiles(next);
  renderShapeValidationProfiles();
  setStatus("Shape validation profile file removed");
}

function shapeProfileSummary() {
  const profiles = getShapeValidationProfiles();
  if (!profiles.length) return "Syntax only · no shape validation profile loaded.";
  return `${profiles.length} profile file${profiles.length === 1 ? "" : "s"}: ${profiles.map((p) => p.name).join(", ")}`;
}

function renderShapeValidationProfiles() {
  const listEl = byId("shape-profile-list");
  const clearBtn = byId("clear-shape-profile");
  if (!listEl) return;
  const profiles = getShapeValidationProfiles();
  if (clearBtn) clearBtn.disabled = profiles.length === 0;
  if (!profiles.length) {
    listEl.innerHTML = `<p class="microcopy">No validation profile loaded. Only syntax checks will run.</p>`;
    return;
  }
  listEl.innerHTML = "";
  profiles.forEach((profile) => {
    const row = document.createElement("div");
    row.className = "profile-file-item";
    row.title = profile.name;
    const size = profile.size || String(profile.content || "").length;
    const id = profile.id || profile.name;
    row.innerHTML =
      `<code>${esc(profile.name)}</code>` +
      `<span>${Math.max(1, Math.round(size / 1024))} KB</span>` +
      `<button class="profile-file-remove" type="button" title="Remove">×</button>`;
    const removeBtn = row.querySelector(".profile-file-remove");
    removeBtn.setAttribute("aria-label", `Remove ${profile.name}`);
    removeBtn.addEventListener("click", () => removeShapeValidationProfile(id));
    listEl.appendChild(row);
  });
}

function wireShapeValidationProfileControls() {
  const input = byId("shape-profile-files");
  const clearBtn = byId("clear-shape-profile");
  renderShapeValidationProfiles();
  if (input) {
    input.addEventListener("change", async (ev) => {
      const files = Array.from(ev.target.files || []);
      if (!files.length) return;
      try {
        const incoming = await Promise.all(files.map(async (file) => ({
          id: `${file.name}-${file.size}-${file.lastModified}`,
          name: file.name,
          size: file.size,
          content: await file.text(),
          loadedAt: new Date().toISOString(),
        })));
        const existing = getShapeValidationProfiles();
        const byKey = new Map(existing.map((profile) => [profile.id || profile.name, profile]));
        incoming.forEach((profile) => byKey.set(profile.id || profile.name, profile));
        setShapeValidationProfiles(Array.from(byKey.values()));
        renderShapeValidationProfiles();
        setStatus(`Loaded ${incoming.length} shape validation profile file(s)`);
      } catch (e) {
        setStatus(`Could not load validation profile: ${e.message}`);
      } finally {
        input.value = "";
      }
    });
  }
  if (clearBtn) {
    clearBtn.addEventListener("click", () => {
      setShapeValidationProfiles([]);
      renderShapeValidationProfiles();
      setStatus("Shape validation profile cleared");
    });
  }
}

function validationResultMessage(data) {
  const profileCount = Number(data.profile_count || 0);
  if (data.valid) {
    return profileCount
      ? `Valid Turtle / SHACL. Shape profile OK (${profileCount} file${profileCount === 1 ? "" : "s"}).`
      : "Valid Turtle / SHACL. No shape validation profile loaded.";
  }
  if (data.syntax_valid === false) return `Shape/Turtle parse error:\n${data.error}`;
  if (data.profile_valid === false) {
    const report = data.report_text || data.error || "";
    return `Shape validation profile failed (${profileCount} file${profileCount === 1 ? "" : "s"}):\n${report}`;
  }
  return `Shape/Turtle validation error:\n${data.error || data.message || "Unknown validation error"}`;
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

function downloadText(filename, text, type = "text/plain") {
  const blob = new Blob([text], { type });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = filename;
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
}

async function saveTextAsFile(defaultName, text, type = "text/plain", description = "Text file", extensions = []) {
  if (window.showSaveFilePicker) {
    try {
      const options = { suggestedName: defaultName };
      if (extensions.length) {
        options.types = [{ description, accept: { [type]: extensions } }];
      }
      const handle = await window.showSaveFilePicker(options);
      const writable = await handle.createWritable();
      await writable.write(new Blob([text], { type }));
      await writable.close();
      return { saved: true, picked: true, name: handle.name || defaultName };
    } catch (e) {
      if (e && e.name === "AbortError") return { saved: false, cancelled: true };
      throw e;
    }
  }

  const chosen = prompt("Save session as", defaultName);
  if (chosen === null) return { saved: false, cancelled: true };
  const name = chosen.trim() || defaultName;
  downloadText(name, text, type);
  return { saved: true, picked: false, name };
}

function wireExport(buttonId, getNodeShapes) {
  const btn = byId(buttonId);
  if (!btn) return;
  btn.addEventListener("click", () => {
    if (getAccepted().length === 0) { setStatus("No accepted shapes to export"); return; }
    const node = getNodeShapes ? getNodeShapes() : "";
    downloadText("generated_shapes.ttl", buildTurtleDocument(node), "text/turtle");
    setStatus("Exported generated_shapes.ttl");
  });
}

/* ---------- session import / export ---------- */
function sanitizedModelsForExport() {
  const m = getModels();
  return {
    provider: m.provider,
    llmModel: m.llmModel,
    textModel: m.textModel,
    visionModel: m.visionModel,
    embeddingModel: m.embeddingModel,
    temperature: m.temperature,
    customModels: m.customModels,
  };
}

function wireSessionControls() {
  const exportBtn = byId("export-session");
  const importBtn = byId("import-session");
  const importInput = byId("session-file");

  if (exportBtn) exportBtn.addEventListener("click", async () => {
    const payload = {
      version: 1,
      exportedAt: new Date().toISOString(),
      ontology: getOntology(),
      accepted: getAccepted(),
      shapeValidationProfiles: getShapeValidationProfiles(),
      models: sanitizedModelsForExport(),
    };
    try {
      const result = await saveTextAsFile(
        "session.json",
        JSON.stringify(payload, null, 2),
        "application/json",
        "JSON session",
        [".json"]
      );
      if (result.cancelled) {
        setStatus("Session export cancelled");
      } else if (result.picked) {
        setStatus(`Session saved as ${result.name} without credentials`);
      } else {
        setStatus(`Session downloaded as ${result.name} without credentials`);
      }
    } catch (e) {
      setStatus(`Could not export session: ${e.message}`);
    }
  });

  if (importBtn && importInput) {
    importBtn.addEventListener("click", () => importInput.click());
    importInput.addEventListener("change", async (ev) => {
      const file = ev.target.files && ev.target.files[0];
      if (!file) return;
      try {
        const payload = JSON.parse(await file.text());
        if (payload.ontology) setOntology(payload.ontology);
        if (Array.isArray(payload.accepted)) setAccepted(payload.accepted);
        if (Array.isArray(payload.shapeValidationProfiles)) {
          setShapeValidationProfiles(payload.shapeValidationProfiles);
        }
        if (payload.models) {
          const current = getModels();
          saveJSON(STORE.models, mergeModels(current, {
            provider: payload.models.provider || current.provider,
            llmModel: payload.models.llmModel || current.llmModel,
            textModel: payload.models.textModel || current.textModel,
            visionModel: payload.models.visionModel || current.visionModel,
            embeddingModel: payload.models.embeddingModel || current.embeddingModel,
            temperature: clampTemperature(payload.models.temperature),
            customModels: payload.models.customModels || current.customModels,
          }));
        }
        setStatus("Session imported");
        location.reload();
      } catch (e) {
        setStatus(`Could not import session: ${e.message}`);
      } finally {
        importInput.value = "";
      }
    });
  }
}

/* ---------- copy / validate ---------- */
async function copyToClipboard(text) {
  try { await navigator.clipboard.writeText(text); return true; }
  catch { return false; }
}

async function validateTurtle(shape, prefixes) {
  return fetchJSON(SERVICES.validate, {
    method: "POST",
    body: JSON.stringify({
      shape,
      prefixes,
      validation_profiles: getShapeValidationProfiles(),
    }),
  }, { label: "Validate Turtle", timeoutMs: 30000 }); // {valid, error}
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
        configFingerprint: modelConfigFingerprint(),
        inferenceConfig: getInferenceConfig(),
      });
    }
    localStorage.removeItem(STORE.ontology);
    localStorage.removeItem(STORE.accepted);
    localStorage.removeItem(STORE.shapeProfiles);
    location.reload();
  });
}
