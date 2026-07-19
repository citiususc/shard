/* common.js — shared state and helpers for both pages (br2shacl-ui).
 *
 * This is a locally-served web app (run_demo.py), not a sandboxed artifact, so
 * localStorage is used to share the loaded ontology, model configuration,
 * prefixes and accepted shapes across the two pages.
 */

const SERVICES = {
  capabilities: "/api/capabilities",
  parse:    "http://127.0.0.1:9100/parse-ontology",
  terms:    "http://127.0.0.1:9101/find-relevant-terms",
  prepareTerms: "http://127.0.0.1:9101/prepare-ontology-embeddings",
  termStatus:   "http://127.0.0.1:9101/ontology-embedding-status",
  cancelTerms:  "http://127.0.0.1:9101/cancel-ontology-embeddings",
  build:    "http://127.0.0.1:9102/build-shacl-shape",
  validate: "http://127.0.0.1:9102/validate-shape",
  merge:    "http://127.0.0.1:9102/merge-shapes",
  validateModel: "http://127.0.0.1:9102/validate-model",
  guide:    "http://127.0.0.1:9103/generate-from-guide",
};

const DEFAULT_DEPLOYMENT_CAPABILITIES = {
  deployment_profile: "local",
  repository_url: "https://github.com/citiususc/br2shacl-ui",
  providers: {
    databricks: { enabled: true, execution: "remote" },
    huggingface: { enabled: true, execution: "local", message: "" },
  },
};
let deploymentCapabilities = DEFAULT_DEPLOYMENT_CAPABILITIES;
let deploymentCapabilitiesRequest = null;

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
  ontology: "t2s.ontology",   // ontology content, namespaces, prefixes and entity catalog
  models:   "t2s.models",     // provider + credentials + selected/default/custom models
  accepted: "t2s.accepted",   // [{id, property, shape}]
  shapeProfiles: "t2s.shapeProfiles", // [{id, name, size, content}]
  astreaBaseline: "t2s.astreaBaseline", // {id, name, size, content, validation}
  astreaMergeMode: "t2s.astreaMergeMode", // none | priority-llm | restrictive
  executionLogs: "t2s.executionLogs", // structured history of the latest generation/review runs
};

/* ---------- tiny helpers ---------- */
const byId = (id) => document.getElementById(id);
const esc = (s) => String(s == null ? "" : s)
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

function setStatus(text) {
  const pill = byId("status-pill");
  if (pill) {
    pill.textContent = text;
    pill.title = text;
  }
}

function loadJSON(key, fallback) {
  try { return JSON.parse(localStorage.getItem(key)) ?? fallback; }
  catch { return fallback; }
}
function saveJSON(key, value) { localStorage.setItem(key, JSON.stringify(value)); }

const EXECUTION_LOG_VERSION = 2;
const MAX_EXECUTION_RUNS = 5;
const MAX_EXECUTION_ENTRIES = 800;
const MAX_EXECUTION_DETAIL_LENGTH = 12000;

function emptyExecutionLogState() {
  return { version: EXECUTION_LOG_VERSION, activeRunId: "", runs: [] };
}

function levelFromLegacyLine(line) {
  if (line.includes("[ERROR]")) return "error";
  if (line.includes("[WARN]")) return "warn";
  if (line.includes("[DEBUG]")) return "debug";
  if (line.includes("[INFO]")) return "info";
  return "info";
}

function loadExecutionLogState() {
  const raw = loadJSON(STORE.executionLogs, null);
  if (raw && raw.version === EXECUTION_LOG_VERSION && Array.isArray(raw.runs)) return raw;
  if (!raw || !raw.content) return emptyExecutionLogState();

  const timestamp = raw.updatedAt || new Date().toISOString();
  const run = {
    id: `legacy-${Date.now().toString(36)}`,
    source: raw.source || "Generation",
    startedAt: timestamp,
    endedAt: timestamp,
    status: "completed",
    metadata: {},
    entries: String(raw.content).split("\n").filter(Boolean).map((line) => ({
      timestamp,
      level: levelFromLegacyLine(line),
      kind: "entry",
      stage: "legacy",
      indent: 0,
      message: line.replace(/^\[(?:ERROR|WARN|INFO|DEBUG)\]\s*/, ""),
      details: "",
    })),
  };
  return { version: EXECUTION_LOG_VERSION, activeRunId: "", runs: [run] };
}

function saveExecutionLogState(state) {
  state.runs = (state.runs || []).slice(-MAX_EXECUTION_RUNS);
  saveJSON(STORE.executionLogs, state);
}

function executionRun(state, runId) {
  return (state.runs || []).find((run) => run.id === runId) || null;
}

function beginExecutionRun(config = {}) {
  const state = loadExecutionLogState();
  const now = new Date().toISOString();
  const run = {
    id: makeRequestId(),
    source: String(config.source || "Generation"),
    startedAt: now,
    endedAt: "",
    status: "running",
    metadata: { ...(config.metadata || {}) },
    entries: [],
  };
  state.runs.push(run);
  state.activeRunId = run.id;
  saveExecutionLogState(state);
  renderExecutionLogs(state);
  return run.id;
}

function updateExecutionRun(runId, patch = {}) {
  const state = loadExecutionLogState();
  const run = executionRun(state, runId);
  if (!run) return;
  if (patch.source) run.source = String(patch.source);
  if (patch.status) run.status = String(patch.status);
  if (patch.startedAt) run.startedAt = patch.startedAt;
  if (patch.endedAt) run.endedAt = patch.endedAt;
  if (patch.metadata) run.metadata = { ...(run.metadata || {}), ...patch.metadata };
  saveExecutionLogState(state);
  renderExecutionLogs(state);
}

function appendExecutionEntry(runId, entry = {}) {
  if (!runId || !entry.message) return;
  const state = loadExecutionLogState();
  const run = executionRun(state, runId);
  if (!run) return;
  run.entries.push({
    timestamp: entry.timestamp || new Date().toISOString(),
    level: String(entry.level || "info").toLowerCase(),
    kind: entry.kind || "entry",
    stage: entry.stage || "",
    indent: Math.max(0, Math.min(3, Number(entry.indent || 0))),
    message: String(entry.message),
    details: compactExecutionDetails(entry.details),
  });
  run.entries = run.entries.slice(-MAX_EXECUTION_ENTRIES);
  saveExecutionLogState(state);
  renderExecutionLogs(state);
}

function compactExecutionDetails(value) {
  if (!value) return "";
  const redacted = String(value)
    .replace(/(authorization\s*:\s*bearer\s+)[^\s]+/gi, "$1[redacted]")
    .replace(/((?:databricks|hugging[ _-]?face|hf)?[ _-]?token\s*[=:]\s*)[^\s,;]+/gi, "$1[redacted]");
  if (redacted.length <= MAX_EXECUTION_DETAIL_LENGTH) return redacted;
  return `${redacted.slice(0, MAX_EXECUTION_DETAIL_LENGTH)}\n… details truncated`;
}

function finishExecutionRun(runId, status = "completed", metadata = {}) {
  updateExecutionRun(runId, {
    status,
    endedAt: new Date().toISOString(),
    metadata,
  });
}

function executionTime(value) {
  const date = new Date(value || "");
  if (Number.isNaN(date.getTime())) return "--:--:--";
  return date.toLocaleTimeString([], {
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  });
}

function executionDuration(run) {
  const start = new Date(run.startedAt || "").getTime();
  const end = new Date(run.endedAt || "").getTime();
  if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) return "";
  const seconds = Math.max(0, Math.round((end - start) / 1000));
  if (seconds < 60) return `${seconds}s`;
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

function logLevelLabel(level) {
  return ({ pass: "PASS", done: "DONE", warn: "WARN", error: "ERROR", debug: "DEBUG" })[level] || "INFO";
}

function runHeaderLines(run) {
  const meta = run.metadata || {};
  const title = [run.source, meta.artifact].filter(Boolean).join(" · ");
  const models = Array.isArray(meta.models) ? meta.models.filter(Boolean).join(", ") : meta.models;
  const first = [`Started ${executionTime(run.startedAt)}`, meta.provider, models].filter(Boolean).join(" · ");
  const second = [meta.ontology ? `Ontology: ${meta.ontology}` : "", meta.ruleCount != null ? `${meta.ruleCount} rule(s)` : ""].filter(Boolean).join(" · ");
  const third = [meta.validation ? `Validation: ${meta.validation}` : "", meta.requestId ? `Request: ${meta.requestId}` : ""].filter(Boolean).join(" · ");
  return { title: title || run.source, lines: [first, second, third].filter(Boolean) };
}

function executionEntryMatches(entry, query, levelFilter) {
  const level = String(entry.level || "info");
  if (levelFilter === "issues" && !["warn", "error"].includes(level)) return false;
  if (levelFilter === "errors" && level !== "error") return false;
  if (!query) return true;
  return `${entry.message || ""} ${entry.details || ""} ${entry.stage || ""}`.toLowerCase().includes(query);
}

function renderExecutionLogs(state = loadExecutionLogState()) {
  const contentEl = byId("logs-content");
  if (!contentEl) return;
  const previousScrollTop = contentEl.scrollTop;
  const titleEl = byId("logs-title");
  const searchEl = byId("logs-search");
  const levelEl = byId("logs-level");
  const query = String(searchEl && searchEl.value || "").trim().toLowerCase();
  const levelFilter = String(levelEl && levelEl.value || "all");
  const runs = state.runs || [];

  if (titleEl) titleEl.textContent = runs.length ? `Execution logs · ${runs.length} run${runs.length === 1 ? "" : "s"}` : "Execution logs";
  contentEl.innerHTML = "";

  runs.forEach((run) => {
    const header = runHeaderLines(run);
    const searchableHeader = `${header.title} ${header.lines.join(" ")}`.toLowerCase();
    const matchingEntries = (run.entries || []).filter((entry) => executionEntryMatches(entry, query, levelFilter));
    if ((query && !searchableHeader.includes(query) && !matchingEntries.length)
        || (levelFilter !== "all" && !matchingEntries.length)) return;

    const section = document.createElement("section");
    section.className = `log-run log-run-${run.status || "completed"}`;
    const heading = document.createElement("div");
    heading.className = "log-run-heading";
    heading.innerHTML = `<strong>${esc(header.title)}</strong><span>${esc(run.status || "completed")}</span>`;
    section.appendChild(heading);

    header.lines.forEach((line) => {
      const metaLine = document.createElement("div");
      metaLine.className = "log-run-meta";
      metaLine.textContent = line;
      section.appendChild(metaLine);
    });

    const duration = executionDuration(run);
    if (duration) {
      const durationLine = document.createElement("div");
      durationLine.className = "log-run-meta";
      durationLine.textContent = `Finished ${executionTime(run.endedAt)} · duration ${duration}`;
      section.appendChild(durationLine);
    }

    matchingEntries.forEach((entry) => {
      const line = document.createElement("div");
      line.className = `log-line log-${entry.level || "info"} log-kind-${entry.kind || "entry"}`;
      line.style.setProperty("--log-indent", String(entry.indent || 0));
      line.innerHTML = `<time>${esc(executionTime(entry.timestamp))}</time><span class="log-level">${esc(logLevelLabel(entry.level))}</span><span class="log-message">${esc(entry.message)}</span>`;
      if (entry.details) {
        const details = document.createElement("details");
        details.className = "log-entry-details";
        details.innerHTML = `<summary>Details</summary><pre>${esc(entry.details)}</pre>`;
        line.appendChild(details);
      }
      section.appendChild(line);
    });
    contentEl.appendChild(section);
  });

  if (!contentEl.children.length) {
    const empty = document.createElement("p");
    empty.className = "logs-empty";
    empty.textContent = runs.length ? "No log entries match the current filters." : "No generation logs are available yet.";
    contentEl.appendChild(empty);
  }

  updateLogsBadge(state);
  const autoscroll = byId("logs-autoscroll");
  if (!autoscroll || autoscroll.checked) contentEl.scrollTop = contentEl.scrollHeight;
  else contentEl.scrollTop = previousScrollTop;
}

function updateLogsBadge(state = loadExecutionLogState()) {
  const badge = byId("logs-count");
  if (!badge) return;
  const latest = (state.runs || []).slice(-1)[0];
  const count = latest ? (latest.entries || []).filter((entry) => ["warn", "error"].includes(entry.level)).length : 0;
  badge.textContent = String(count);
  badge.hidden = count === 0;
}

function executionLogsAsText(state = loadExecutionLogState()) {
  const lines = [];
  (state.runs || []).forEach((run) => {
    const header = runHeaderLines(run);
    lines.push(header.title, ...header.lines);
    (run.entries || []).forEach((entry) => {
      const indent = "  ".repeat(entry.indent || 0);
      lines.push(`${indent}${executionTime(entry.timestamp)}  ${logLevelLabel(entry.level).padEnd(5)}  ${entry.message}`);
      if (entry.details) lines.push(`${indent}  Details:\n${entry.details}`);
    });
    const duration = executionDuration(run);
    if (duration) lines.push(`Finished ${executionTime(run.endedAt)} · duration ${duration}`);
    lines.push("─".repeat(64));
  });
  return lines.join("\n");
}

function clearExecutionLogs() {
  localStorage.removeItem(STORE.executionLogs);
  renderExecutionLogs(emptyExecutionLogState());
}

function wireLogsDrawer() {
  const toggle = byId("logs-toggle");
  const drawer = byId("logs-drawer");
  const close = byId("logs-close");
  if (!toggle || !drawer || !close) return;
  renderExecutionLogs();
  toggle.setAttribute("aria-expanded", "false");
  toggle.addEventListener("click", () => {
    const open = drawer.classList.toggle("open");
    toggle.classList.toggle("active", open);
    toggle.setAttribute("aria-expanded", open ? "true" : "false");
  });
  close.addEventListener("click", () => {
    drawer.classList.remove("open");
    toggle.classList.remove("active");
    toggle.setAttribute("aria-expanded", "false");
  });
  const search = byId("logs-search");
  const level = byId("logs-level");
  const copy = byId("logs-copy");
  const clear = byId("logs-clear");
  if (search) search.addEventListener("input", () => renderExecutionLogs());
  if (level) level.addEventListener("change", () => renderExecutionLogs());
  if (copy) copy.addEventListener("click", async () => {
    const copied = await copyToClipboard(executionLogsAsText());
    copy.textContent = copied ? "Copied" : "Copy failed";
    setTimeout(() => { copy.textContent = "Copy"; }, 1200);
  });
  if (clear) clear.addEventListener("click", clearExecutionLogs);
}

document.addEventListener("DOMContentLoaded", wireLogsDrawer);

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

function loadDeploymentCapabilities() {
  if (deploymentCapabilitiesRequest) return deploymentCapabilitiesRequest;
  deploymentCapabilitiesRequest = fetchJSON(SERVICES.capabilities, {}, {
    label: "Load deployment capabilities",
    timeoutMs: 5000,
  }).then((payload) => {
    deploymentCapabilities = {
      ...DEFAULT_DEPLOYMENT_CAPABILITIES,
      ...payload,
      providers: {
        ...DEFAULT_DEPLOYMENT_CAPABILITIES.providers,
        ...(payload.providers || {}),
      },
    };
    return deploymentCapabilities;
  }).catch((err) => {
    console.warn("Could not load deployment capabilities; using local defaults.", err);
    return deploymentCapabilities;
  });
  return deploymentCapabilitiesRequest;
}

function providerCapability(provider) {
  return (deploymentCapabilities.providers || {})[provider] || { enabled: false };
}

function providerIsEnabled(provider) {
  return providerCapability(provider).enabled !== false;
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
  if (!providerIsEnabled(models.provider)) {
    return {
      ready: false,
      message: providerCapability(models.provider).message
        || "The selected inference provider is unavailable in this deployment.",
    };
  }
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
  if (!providerIsEnabled(models.provider)) {
    return {
      ready: false,
      message: providerCapability(models.provider).message
        || "The selected inference provider is unavailable in this deployment.",
    };
  }
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
    const providerEnabled = providerIsEnabled(provider);
    document.querySelectorAll("[data-provider]").forEach((b) => {
      const active = b.dataset.provider === provider;
      b.classList.toggle("active", active);
      b.setAttribute("aria-pressed", active ? "true" : "false");
    });
    document.querySelectorAll("[data-provider-config]").forEach((el) => {
      const active = el.dataset.providerConfig === provider;
      el.classList.toggle("is-active", active);
      el.classList.toggle("is-inactive", !active);
      el.setAttribute("aria-hidden", active ? "false" : "true");
      el.querySelectorAll("input, select, textarea, button").forEach((control) => {
        control.disabled = !active || !providerEnabled;
      });
    });

    const hfEnabled = providerIsEnabled("huggingface");
    document.querySelectorAll("[data-hf-local-config]").forEach((el) => {
      el.hidden = !hfEnabled;
    });
    document.querySelectorAll("[data-hf-public-notice]").forEach((el) => {
      el.hidden = hfEnabled;
      const message = el.querySelector("[data-provider-disabled-message]");
      const link = el.querySelector("a");
      if (message) {
        message.textContent = providerCapability("huggingface").message
          || "Local Hugging Face inference is unavailable in this deployment.";
      }
      if (link) link.href = deploymentCapabilities.repository_url;
    });
    document.querySelectorAll("[data-inference-setting]").forEach((el) => {
      el.hidden = !providerEnabled;
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
    const providerControlIds = [
      "temperature", "llm-model", "text-model", "vision-model", "embedding-model",
      "custom-model-role", "custom-model-id", "add-custom-model",
    ];
    providerControlIds.forEach((id) => {
      const control = byId(id);
      if (control) control.disabled = !providerEnabled;
    });
    setModelStatus(providerEnabled
      ? "Model settings are stored in this browser and sent with each request."
      : providerCapability(provider).message);
  }

  const init = getModels();
  apply(init.provider, true);
  loadDeploymentCapabilities().then(() => apply(getModels().provider, true));

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
  return String(ns || "").trim();
}

function namespaceValidationError(ns) {
  const value = normalizeNamespace(ns);
  if (!value) return "Namespace cannot be empty.";
  if (/\s/.test(value) || !/^[A-Za-z][A-Za-z0-9+.-]*:/.test(value)) {
    return "Use an absolute IRI without spaces.";
  }
  if (!(value.endsWith("#") || value.endsWith("/") || value.endsWith(":"))) {
    return "Namespace must end in #, / or :.";
  }
  return "";
}

function shapesNamespace(baseNs) {
  const ns = normalizeNamespace(baseNs);
  if (!ns) return "";
  if (ns.endsWith("#")) return ns.slice(0, -1) + "/shapes/";
  if (ns.endsWith("/")) return ns + "shapes/";
  if (ns.endsWith(":")) return ns + "shapes:";
  return ns + "/shapes/";
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

function normalizeShapePrefix(prefix) {
  return String(prefix || "").trim().replace(/:$/, "");
}

function shapePrefixValidationError(prefix) {
  const value = normalizeShapePrefix(prefix);
  if (!value) return "Shape prefix cannot be empty.";
  if (!/^[A-Za-z][A-Za-z0-9._-]*$/.test(value)) {
    return "Start with a letter and use only letters, digits, '.', '_' or '-'.";
  }
  return "";
}

function syncPrefixesWithNamespaces(
  prefixBlock, baseNs, shapeNs, shapePrefix = "shape", managedPrefixes = ["onto", "shape"],
) {
  const base = normalizeNamespace(baseNs);
  const shapes = normalizeNamespace(shapeNs) || shapesNamespace(base);
  const preferred = normalizeShapePrefix(shapePrefix) || "shape";
  const managed = new Set(managedPrefixes || []);
  let next = prefixBlock || "";
  if (base && managed.has("onto")) next = setPrefixLine(next, "onto", base);
  if (shapes) next = setPrefixLine(next, preferred, shapes);
  return next;
}

function prefixEntries(prefixBlock) {
  const re = /(?:@prefix|PREFIX)\s+([^:\s]*):\s*<([^>]+)>\s*\.?/gi;
  const entries = [];
  let match;
  while ((match = re.exec(prefixBlock || "")) !== null) {
    entries.push({ prefix: match[1] || "", namespace: match[2] || "" });
  }
  return entries;
}

function prefixNamespace(prefixBlock, prefix) {
  const wanted = String(prefix || "");
  const entry = prefixEntries(prefixBlock).find((item) => item.prefix === wanted);
  return entry ? entry.namespace : "";
}

function removePrefixLine(prefixBlock, prefix) {
  const wanted = String(prefix || "");
  const escaped = wanted.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const pattern = new RegExp(`^\\s*(?:@prefix|PREFIX)\\s+${escaped}:\\s*<[^>]+>\\s*\\.?\\s*$`, "i");
  const hadTrailingNewline = /\r?\n$/.test(prefixBlock || "");
  const next = String(prefixBlock || "").split(/\r?\n/)
    .filter((line) => !pattern.test(line)).join("\n").trimEnd();
  return next ? `${next}${hadTrailingNewline ? "\n" : ""}` : "";
}

function preferredPrefixForNamespace(prefixBlock, namespace, managedPrefixes = []) {
  const managed = new Set(managedPrefixes || []);
  const candidates = prefixEntries(prefixBlock).filter((entry) =>
    entry.prefix && entry.namespace === namespace && entry.prefix !== "onto-sh");
  const sourceCandidates = candidates.filter((entry) => !managed.has(entry.prefix));
  const ranked = (sourceCandidates.length ? sourceCandidates : candidates).sort((left, right) => {
    const score = (entry) => [
      entry.prefix !== "shape",
      !entry.prefix.endsWith("-sh"),
      !entry.prefix.includes("shape"),
      entry.prefix.length,
      entry.prefix,
    ];
    const a = score(left);
    const b = score(right);
    for (let index = 0; index < a.length; index++) {
      if (a[index] < b[index]) return -1;
      if (a[index] > b[index]) return 1;
    }
    return 0;
  });
  if (ranked.length) return ranked[0].prefix;

  const occupied = new Set(prefixEntries(prefixBlock).map((entry) => entry.prefix));
  if (!occupied.has("shape")) return "shape";
  let index = 2;
  while (occupied.has(`shape${index}`)) index += 1;
  return `shape${index}`;
}

function pruneManagedPrefixAliases(prefixBlock, baseNs, shapeNs, shapePrefix, managedPrefixes) {
  const managed = new Set(managedPrefixes || []);
  let prefixes = prefixBlock || "";

  if (managed.has("onto-sh")) {
    prefixes = removePrefixLine(prefixes, "onto-sh");
    managed.delete("onto-sh");
  }

  Array.from(managed).forEach((prefix) => {
    if (prefix !== "onto" && prefix !== shapePrefix) {
      const namespace = prefixNamespace(prefixes, prefix);
      if (namespace === shapeNs) prefixes = removePrefixLine(prefixes, prefix);
      if (!namespace || namespace === shapeNs) managed.delete(prefix);
    }
  });

  const hasSourcePrimaryPrefix = prefixEntries(prefixes).some((entry) =>
    entry.prefix && entry.prefix !== "onto" && entry.namespace === baseNs);
  if (managed.has("onto") && hasSourcePrimaryPrefix) {
    prefixes = removePrefixLine(prefixes, "onto");
    managed.delete("onto");
  }

  return { prefixes, managedPrefixes: Array.from(managed) };
}

function ensureGeneratorPrefixes(
  prefixBlock, baseNs, shapeNs = "", shapePrefix = "shape", managedPrefixes = ["onto", "shape"],
) {
  const base = normalizeNamespace(baseNs);
  const shapes = normalizeNamespace(shapeNs)
    || prefixNamespace(prefixBlock, normalizeShapePrefix(shapePrefix))
    || prefixNamespace(prefixBlock, "shape")
    || prefixNamespace(prefixBlock, "onto-sh")
    || shapesNamespace(base);
  return syncPrefixesWithNamespaces(
    prefixBlock || "", base, shapes, shapePrefix, managedPrefixes,
  );
}

function namespaceCoverage(ontology, namespace) {
  const terms = (ontology && ontology.entities) || [];
  const selected = normalizeNamespace(namespace);
  const count = selected ? terms.filter((entity) => {
    const iri = entity.full_iri || entity.iri || "";
    return String(iri).startsWith(selected);
  }).length : 0;
  return { count, total: terms.length };
}

function namespaceSourceLabel(ontology) {
  const source = ontology && ontology.namespaceSource;
  if (source === "custom") return "Custom";
  if (source === "prefixes") return "From prefixes";
  if (source === "none" || !(ontology && ontology.baseNamespace)) return "Not detected";
  return "Detected";
}

function shapeNamespaceSourceLabel(ontology) {
  const source = ontology && ontology.shapeNamespaceSource;
  if (source === "custom") return "Custom";
  if (source === "prefixes") return "From prefixes";
  if (source === "declared_prefix") return "Declared";
  if (source === "derived") return "Derived";
  return "Not detected";
}

function shapePrefixSourceLabel(ontology) {
  const source = ontology && ontology.shapePrefixSource;
  if (source === "custom") return "Custom";
  if (source === "prefixes") return "From prefixes";
  if (source === "profile") return "From profile";
  if (source === "declared_prefix") return "Declared";
  return "Default";
}

function namespaceSummaryText(ontology) {
  if (!ontology || !ontology.baseNamespace) return "No primary ontology namespace detected.";
  const coverage = namespaceCoverage(ontology, ontology.baseNamespace);
  const detectedBy = ontology.namespaceAnalysis && ontology.namespaceAnalysis.detected_by;
  const detectedLabels = {
    term_coverage: "Detected by term coverage",
    ontology_iri: "Detected from ontology IRI",
    declared_prefix: "Detected from declared prefix",
  };
  const method = ontology.namespaceSource === "custom"
    ? "User-defined"
    : ontology.namespaceSource === "prefixes"
      ? "Synchronized from prefixes"
      : detectedLabels[detectedBy] || "Detected";
  if (coverage.total && !coverage.count) return `${method} · no ontology terms match`;
  return `${method} · ${coverage.count} / ${coverage.total} ontology terms`;
}

function repairOntologyNamespaces(o) {
  if (!o) return o;
  const baseNamespace = normalizeNamespace(o.baseNamespace || "");
  const storedManagedPrefixes = Array.isArray(o.managedNamespacePrefixes)
    ? o.managedNamespacePrefixes
    : ["shape"];
  const shapeNamespace = normalizeNamespace(
    o.shapeNamespace
      || prefixNamespace(o.prefixes || "", normalizeShapePrefix(o.shapePrefix))
      || prefixNamespace(o.prefixes || "", "shape")
      || prefixNamespace(o.prefixes || "", "onto-sh")
      || shapesNamespace(baseNamespace)
  );
  const inferredShapeNamespaceSource = shapeNamespace
    ? (shapeNamespace === shapesNamespace(baseNamespace) ? "derived" : "custom")
    : "none";
  const shapeNamespaceSource = !o.shapeNamespaceSource || o.shapeNamespaceSource === "none"
    ? inferredShapeNamespaceSource
    : o.shapeNamespaceSource;
  const inferredShapePrefix = preferredPrefixForNamespace(
    o.prefixes || "", shapeNamespace, storedManagedPrefixes,
  );
  const shapePrefix = normalizeShapePrefix(o.shapePrefix) || inferredShapePrefix;
  const existingShapeBinding = prefixNamespace(o.prefixes || "", shapePrefix);
  const shapePrefixSource = o.shapePrefixSource || (
    existingShapeBinding === shapeNamespace && !storedManagedPrefixes.includes(shapePrefix)
      ? "declared_prefix" : "default"
  );
  const pruned = pruneManagedPrefixAliases(
    o.prefixes || "", baseNamespace, shapeNamespace, shapePrefix, storedManagedPrefixes,
  );
  const managedNamespacePrefixes = pruned.managedPrefixes;
  const prefixes = ensureGeneratorPrefixes(
    pruned.prefixes, baseNamespace, shapeNamespace, shapePrefix, managedNamespacePrefixes,
  );
  const next = {
    ...o,
    baseNamespace,
    shapeNamespace,
    shapePrefix,
    namespaceSource: o.namespaceSource || (baseNamespace ? "detected" : "none"),
    shapeNamespaceSource,
    shapePrefixSource,
    managedNamespacePrefixes,
    prefixes,
  };
  if (JSON.stringify(next) === JSON.stringify(o)) return o;
  setOntology(next);
  return next;
}

function replacePreferredShapePrefix(ontology, nextPrefix, source) {
  const o = { ...ontology };
  const preferred = normalizeShapePrefix(nextPrefix);
  const error = shapePrefixValidationError(preferred);
  if (error) throw new Error(error);

  const oldPrefix = normalizeShapePrefix(o.shapePrefix);
  const managed = new Set(o.managedNamespacePrefixes || []);
  let prefixes = o.prefixes || "";
  if (oldPrefix && oldPrefix !== preferred
      && prefixNamespace(prefixes, oldPrefix) === o.shapeNamespace) {
    prefixes = removePrefixLine(prefixes, oldPrefix);
    managed.delete(oldPrefix);
  }

  const existing = prefixNamespace(prefixes, preferred);
  if (existing && existing !== o.shapeNamespace) {
    throw new Error(`Prefix '${preferred}' is already bound to ${existing}.`);
  }
  if (!existing) managed.add(preferred);
  prefixes = setPrefixLine(prefixes, preferred, o.shapeNamespace);

  o.shapePrefix = preferred;
  o.shapePrefixSource = source;
  o.managedNamespacePrefixes = Array.from(managed);
  o.prefixes = prefixes;
  return o;
}

function profileShapePrefixCandidate(shapeNamespace) {
  const declarations = getShapeValidationProfiles()
    .map((profile) => profile.content || "").join("\n");
  const candidates = prefixEntries(declarations).filter((entry) =>
    entry.prefix && entry.namespace === shapeNamespace);
  if (!candidates.length) return "";
  const candidateBlock = candidates
    .map((entry) => `@prefix ${entry.prefix}: <${entry.namespace}> .`).join("\n");
  return preferredPrefixForNamespace(candidateBlock, shapeNamespace, []);
}

function synchronizePreferredShapePrefixWithProfiles({ notify = true } = {}) {
  let o = repairOntologyNamespaces(getOntology());
  if (!o || !o.shapeNamespace || o.shapePrefixSource === "custom") return o;
  const candidate = profileShapePrefixCandidate(o.shapeNamespace);

  if (candidate && ["default", "profile"].includes(o.shapePrefixSource || "default")) {
    o = replacePreferredShapePrefix(o, candidate, "profile");
  } else if (!candidate && o.shapePrefixSource === "profile") {
    const managed = new Set(o.managedNamespacePrefixes || []);
    let prefixes = o.prefixes || "";
    if (managed.has(o.shapePrefix)) {
      prefixes = removePrefixLine(prefixes, o.shapePrefix);
      managed.delete(o.shapePrefix);
    }
    const fallback = preferredPrefixForNamespace(prefixes, o.shapeNamespace, Array.from(managed));
    const source = prefixNamespace(prefixes, fallback) === o.shapeNamespace
      ? "declared_prefix" : "default";
    o = replacePreferredShapePrefix({
      ...o, prefixes, managedNamespacePrefixes: Array.from(managed), shapePrefix: "",
    }, fallback, source);
  }

  setOntology(o);
  if (notify) document.dispatchEvent(new CustomEvent("shape-prefix-preference-changed"));
  return o;
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
  const shapeNsInput = byId("shape-namespace");
  const shapePrefixInput = byId("shape-prefix");
  const namespaceSource = byId("namespace-source");
  const shapeNamespaceSource = byId("shape-namespace-source");
  const shapePrefixSource = byId("shape-prefix-source");
  const namespaceSummary = byId("namespace-summary");
  const namespaceCandidates = byId("namespace-candidates");
  const shapePrefixCandidates = byId("shape-prefix-candidates");
  const prefixEditor = byId("prefixes-editor");
  const resetPrefixes = byId("reset-prefixes");

  const renderNamespaceControls = (o) => {
    if (!o) return;
    if (nsInput) nsInput.value = o.baseNamespace || "";
    if (shapeNsInput) shapeNsInput.value = o.shapeNamespace || "";
    if (shapePrefixInput) shapePrefixInput.value = o.shapePrefix || "shape";
    if (namespaceSource) {
      namespaceSource.textContent = namespaceSourceLabel(o);
      namespaceSource.className = `namespace-source namespace-source-${o.namespaceSource || "none"}`;
    }
    if (shapeNamespaceSource) {
      shapeNamespaceSource.textContent = shapeNamespaceSourceLabel(o);
      shapeNamespaceSource.className = `namespace-source namespace-source-${o.shapeNamespaceSource || "none"}`;
    }
    if (shapePrefixSource) {
      shapePrefixSource.textContent = shapePrefixSourceLabel(o);
      shapePrefixSource.className = `namespace-source namespace-source-${o.shapePrefixSource || "default"}`;
    }
    if (namespaceSummary) {
      const coverage = namespaceCoverage(o, o.baseNamespace);
      namespaceSummary.textContent = namespaceSummaryText(o);
      namespaceSummary.className = `microcopy namespace-summary${coverage.total && !coverage.count ? " namespace-summary-warning" : ""}`;
    }
    if (namespaceCandidates) {
      namespaceCandidates.innerHTML = "";
      const candidates = (o.namespaceAnalysis && o.namespaceAnalysis.candidates) || [];
      candidates.forEach((candidate) => {
        const option = document.createElement("option");
        option.value = candidate.namespace;
        option.label = `${candidate.term_count || 0} ontology term(s)`;
        namespaceCandidates.appendChild(option);
      });
    }
    if (shapePrefixCandidates) {
      shapePrefixCandidates.innerHTML = "";
      const profileCandidate = profileShapePrefixCandidate(o.shapeNamespace);
      const candidates = new Set([
        o.shapePrefix,
        profileCandidate,
        ...prefixEntries(o.prefixes || "")
          .filter((entry) => entry.namespace === o.shapeNamespace)
          .map((entry) => entry.prefix),
      ].filter(Boolean));
      candidates.forEach((prefix) => {
        const option = document.createElement("option");
        option.value = prefix;
        shapePrefixCandidates.appendChild(option);
      });
    }
  };

  const validNamespaceFromInput = (input, label) => {
    const value = normalizeNamespace(input && input.value);
    const error = namespaceValidationError(value);
    if (input) input.setCustomValidity(error);
    if (error) {
      if (input) input.reportValidity();
      setStatus(`${label}: ${error}`);
      return "";
    }
    return value;
  };

  const validShapePrefixFromInput = () => {
    const value = normalizeShapePrefix(shapePrefixInput && shapePrefixInput.value);
    const error = shapePrefixValidationError(value);
    if (shapePrefixInput) shapePrefixInput.setCustomValidity(error);
    if (error) {
      if (shapePrefixInput) shapePrefixInput.reportValidity();
      setStatus(`Preferred shape prefix: ${error}`);
      return "";
    }
    return value;
  };

  const renderFromStore = () => {
    let o = repairOntologyNamespaces(getOntology());
    if (o) o = synchronizePreferredShapePrefixWithProfiles({ notify: false });
    if (!o) return;
    if (summary) summary.textContent = ontologySummaryText(o);
    renderNamespaceControls(o);
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
        const namespaceAnalysis = data.namespace_analysis || {};
        const baseNamespace = data.base_namespace || "";
        const shapeNamespace = data.shape_namespace || shapesNamespace(baseNamespace);
        const shapePrefix = normalizeShapePrefix(
          data.shape_prefix
            || namespaceAnalysis.shape_prefix
            || preferredPrefixForNamespace(data.prefixes || "", shapeNamespace, []),
        ) || "shape";
        const managedNamespacePrefixes = Array.isArray(namespaceAnalysis.managed_prefixes)
          ? namespaceAnalysis.managed_prefixes
          : ["onto", shapePrefix];
        setOntology({
          filename: file.name, content,
          contentHash,
          baseNamespace,
          shapeNamespace,
          shapePrefix,
          namespaceSource: baseNamespace ? "detected" : "none",
          shapeNamespaceSource: namespaceAnalysis.shape_namespace_source
            || (shapeNamespace ? "derived" : "none"),
          shapePrefixSource: namespaceAnalysis.shape_prefix_source
            || (shapePrefix === "shape" ? "default" : "declared_prefix"),
          namespaceAnalysis,
          managedNamespacePrefixes,
          prefixes: ensureGeneratorPrefixes(
            data.prefixes || "",
            baseNamespace,
            shapeNamespace,
            shapePrefix,
            managedNamespacePrefixes,
          ),
          entities: data.entities || [],
        });
        synchronizePreferredShapePrefixWithProfiles({ notify: false });
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
    const baseNamespace = validNamespaceFromInput(nsInput, "Primary ontology namespace");
    if (!baseNamespace) return;
    const oldBaseNamespace = o.baseNamespace || "";
    const derivedShape = ["derived", "none", ""].includes(o.shapeNamespaceSource || "")
      || !o.shapeNamespace
      || o.shapeNamespace === shapesNamespace(oldBaseNamespace);
    o.baseNamespace = baseNamespace;
    o.namespaceSource = "custom";
    if (derivedShape) {
      o.shapeNamespace = shapesNamespace(baseNamespace);
      o.shapeNamespaceSource = "derived";
    }
    o.prefixes = ensureGeneratorPrefixes(
      o.prefixes || "", o.baseNamespace, o.shapeNamespace, o.shapePrefix,
      o.managedNamespacePrefixes,
    );
    setOntology(o);
    renderNamespaceControls(o);
    if (prefixEditor) {
      prefixEditor.value = o.prefixes;
      refreshHighlight("prefixes-editor");
    }
    setStatus("Primary ontology namespace and prefixes synchronized");
  });
  if (shapeNsInput) shapeNsInput.addEventListener("change", () => {
    const o = getOntology();
    if (!o) return;
    const shapeNamespace = validNamespaceFromInput(shapeNsInput, "Generated shapes namespace");
    if (!shapeNamespace) return;
    o.shapeNamespace = shapeNamespace;
    o.shapeNamespaceSource = "custom";
    if (o.shapePrefixSource === "profile"
        && !profileShapePrefixCandidate(shapeNamespace)) {
      o.shapePrefixSource = "custom";
    }
    o.prefixes = ensureGeneratorPrefixes(
      o.prefixes || "", o.baseNamespace, o.shapeNamespace, o.shapePrefix,
      o.managedNamespacePrefixes,
    );
    setOntology(o);
    renderNamespaceControls(o);
    if (prefixEditor) {
      prefixEditor.value = o.prefixes;
      refreshHighlight("prefixes-editor");
    }
    setStatus("Generated shapes namespace and prefixes synchronized");
  });
  if (shapePrefixInput) shapePrefixInput.addEventListener("change", () => {
    const o = getOntology();
    if (!o) return;
    const shapePrefix = validShapePrefixFromInput();
    if (!shapePrefix) return;
    try {
      const next = replacePreferredShapePrefix(o, shapePrefix, "custom");
      setOntology(next);
      renderNamespaceControls(next);
      if (prefixEditor) {
        prefixEditor.value = next.prefixes;
        refreshHighlight("prefixes-editor");
      }
      setStatus("Preferred shape prefix and prefixes synchronized");
    } catch (error) {
      shapePrefixInput.setCustomValidity(error.message);
      shapePrefixInput.reportValidity();
      setStatus(`Preferred shape prefix: ${error.message}`);
    }
  });
  if (prefixEditor) prefixEditor.addEventListener("input", () => {
    const o = getOntology();
    if (!o) return;
    o.prefixes = prefixEditor.value;
    const managedPrefixes = new Set(o.managedNamespacePrefixes || []);
    const baseNamespace = managedPrefixes.has("onto") ? prefixNamespace(o.prefixes, "onto") : "";
    let shapePrefix = normalizeShapePrefix(o.shapePrefix);
    let shapeNamespace = prefixNamespace(o.prefixes, shapePrefix);
    if (!shapeNamespace) {
      shapePrefix = preferredPrefixForNamespace(
        o.prefixes, o.shapeNamespace, o.managedNamespacePrefixes,
      );
      shapeNamespace = prefixNamespace(o.prefixes, shapePrefix);
    }
    if (baseNamespace && !namespaceValidationError(baseNamespace)) {
      o.baseNamespace = baseNamespace;
      o.namespaceSource = "prefixes";
    }
    if (shapeNamespace && !namespaceValidationError(shapeNamespace)) {
      o.shapeNamespace = shapeNamespace;
      o.shapeNamespaceSource = "prefixes";
      o.shapePrefix = shapePrefix;
      o.shapePrefixSource = "prefixes";
    }
    setOntology(o);
    renderNamespaceControls(o);
  });
  if (resetPrefixes) resetPrefixes.addEventListener("click", async () => {
    let o = getOntology(); if (!o) return;
    const customShapePrefix = o.shapePrefixSource === "custom" ? o.shapePrefix : "";
    const data = await fetchJSON(SERVICES.parse, {
      method: "POST",
      body: JSON.stringify({ filename: o.filename, content: o.content }),
    }, { label: "Reset ontology prefixes", timeoutMs: 30000 });
    o.baseNamespace = o.baseNamespace || data.base_namespace || "";
    o.shapeNamespace = o.shapeNamespace || data.shape_namespace || shapesNamespace(o.baseNamespace);
    o.namespaceAnalysis = data.namespace_analysis || o.namespaceAnalysis || {};
    o.managedNamespacePrefixes = Array.isArray(o.namespaceAnalysis.managed_prefixes)
      ? o.namespaceAnalysis.managed_prefixes : ["onto", "shape"];
    o.prefixes = data.prefixes || "";
    o.shapePrefix = normalizeShapePrefix(data.shape_prefix)
      || preferredPrefixForNamespace(o.prefixes, o.shapeNamespace, o.managedNamespacePrefixes);
    o.shapePrefixSource = o.namespaceAnalysis.shape_prefix_source || "default";
    if (customShapePrefix) {
      o = replacePreferredShapePrefix(o, customShapePrefix, "custom");
    } else {
      o.prefixes = ensureGeneratorPrefixes(
        o.prefixes, o.baseNamespace, o.shapeNamespace, o.shapePrefix,
        o.managedNamespacePrefixes,
      );
    }
    setOntology(o);
    renderNamespaceControls(o);
    if (prefixEditor) { prefixEditor.value = o.prefixes; refreshHighlight("prefixes-editor"); }
  });

  document.addEventListener("shape-prefix-preference-changed", () => {
    const o = repairOntologyNamespaces(getOntology());
    if (!o) return;
    renderNamespaceControls(o);
    if (prefixEditor) {
      prefixEditor.value = o.prefixes || "";
      refreshHighlight("prefixes-editor");
    }
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
  synchronizePreferredShapePrefixWithProfiles();
  setStatus("Shape validation profile file removed");
}

function shapeProfileSummary() {
  const profiles = getShapeValidationProfiles();
  if (!profiles.length) return "Syntax + generic SHACL2SHACL · no domain profile loaded.";
  return `Syntax + generic SHACL2SHACL + ${profiles.length} domain profile${profiles.length === 1 ? "" : "s"}: ${profiles.map((p) => p.name).join(", ")}`;
}

function renderShapeValidationProfiles() {
  const listEl = byId("shape-profile-list");
  const clearBtn = byId("clear-shape-profile");
  if (!listEl) return;
  const profiles = getShapeValidationProfiles();
  if (clearBtn) clearBtn.disabled = profiles.length === 0;
  if (!profiles.length) {
    listEl.innerHTML = `<p class="microcopy" title="Generic SHACL2SHACL is always active; no domain profile is loaded.">Generic SHACL2SHACL active.</p>`;
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
        synchronizePreferredShapePrefixWithProfiles();
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
      synchronizePreferredShapePrefixWithProfiles();
      setStatus("Shape validation profile cleared");
    });
  }
}

/* ---------- Astrea baseline evidence + final merge ---------- */
const ASTREA_MERGE_MODES = new Set(["none", "priority-llm", "restrictive"]);

function getAstreaBaseline() { return loadJSON(STORE.astreaBaseline, null); }
function setAstreaBaseline(value) {
  if (value && value.content) saveJSON(STORE.astreaBaseline, value);
  else localStorage.removeItem(STORE.astreaBaseline);
}
function getAstreaMergeMode() {
  const value = localStorage.getItem(STORE.astreaMergeMode) || "none";
  return ASTREA_MERGE_MODES.has(value) ? value : "none";
}
function setAstreaMergeMode(value) {
  const normalized = ASTREA_MERGE_MODES.has(value) ? value : "none";
  localStorage.setItem(STORE.astreaMergeMode, normalized);
  renderAstreaBaselineControls();
}
function astreaBaselinePayload() {
  const baseline = getAstreaBaseline();
  if (!baseline || !baseline.content) return null;
  return {
    id: baseline.id,
    name: baseline.name,
    size: baseline.size,
    content: baseline.content,
  };
}

function renderAstreaBaselineControls() {
  const baseline = getAstreaBaseline();
  const list = byId("astrea-baseline-list");
  const clear = byId("clear-astrea-baseline");
  const mode = getAstreaMergeMode();
  if (clear) clear.disabled = !baseline;
  document.querySelectorAll("[data-astrea-merge]").forEach((button) => {
    button.classList.toggle("active", button.dataset.astreaMerge === mode);
    button.disabled = !baseline;
    button.setAttribute("aria-pressed", button.dataset.astreaMerge === mode ? "true" : "false");
  });
  if (!list) return;
  if (!baseline) {
    list.innerHTML = `<p class="microcopy">No Astrea baseline loaded.</p>`;
    return;
  }
  const size = baseline.size || String(baseline.content || "").length;
  const validation = baseline.validation && baseline.validation.valid
    ? "generic valid"
    : "Turtle valid";
  list.innerHTML =
    `<div class="profile-file-item astrea-file-item">` +
    `<code>${esc(baseline.name)}</code>` +
    `<span>${Math.max(1, Math.round(size / 1024))} KB · ${validation}</span>` +
    `</div>`;
  list.firstElementChild.title = baseline.name;
}

function wireAstreaBaselineControls() {
  const input = byId("astrea-baseline-file");
  const clear = byId("clear-astrea-baseline");
  renderAstreaBaselineControls();

  if (input) {
    input.addEventListener("change", async (event) => {
      const file = event.target.files && event.target.files[0];
      if (!file) return;
      try {
        const content = await file.text();
        const validation = await fetchJSON(SERVICES.validate, {
          method: "POST",
          body: JSON.stringify({ shape: content, prefixes: "", validation_profiles: [] }),
        }, { label: "Validate Astrea baseline", timeoutMs: 30000 });
        if (validation.syntax_valid === false) {
          throw new Error(validation.error || "The uploaded file is not valid Turtle/RDF.");
        }
        setAstreaBaseline({
          id: `${file.name}-${file.size}-${file.lastModified}`,
          name: file.name,
          size: file.size,
          content,
          loadedAt: new Date().toISOString(),
          validation: {
            valid: Boolean(validation.valid),
            genericProfileActive: Boolean(validation.generic_profile_active),
          },
        });
        renderAstreaBaselineControls();
        setStatus(validation.valid
          ? "Astrea baseline loaded and validated"
          : "Astrea baseline loaded · review generic validation during merge");
      } catch (error) {
        setStatus(`Could not load Astrea baseline: ${error.message}`);
      } finally {
        input.value = "";
      }
    });
  }

  if (clear) {
    clear.addEventListener("click", () => {
      setAstreaBaseline(null);
      setAstreaMergeMode("none");
      renderAstreaBaselineControls();
      setStatus("Astrea baseline cleared");
    });
  }

  document.querySelectorAll("[data-astrea-merge]").forEach((button) => {
    button.addEventListener("click", () => {
      if (!getAstreaBaseline()) return;
      setAstreaMergeMode(button.dataset.astreaMerge);
      setStatus(`Astrea export strategy: ${button.textContent.trim()}`);
    });
  });
}

function activeValidationScopeLabel() {
  const profiles = getShapeValidationProfiles();
  if (!profiles.length) return "syntax + generic SHACL2SHACL";
  return `syntax + generic SHACL2SHACL + profile: ${profiles.map((p) => p.name).join(", ")}`;
}

function validationScopeLabel(data = {}) {
  const domainNames = Array.isArray(data.domain_profile_names) ? data.domain_profile_names : [];
  const domainCount = Number(data.domain_profile_count || domainNames.length || 0);
  if (data.validation_level || data.generic_profile_active || data.profile_count != null) {
    if (!domainCount) return "syntax + generic SHACL2SHACL";
    return `syntax + generic SHACL2SHACL + profile: ${domainNames.join(", ") || `${domainCount} file${domainCount === 1 ? "" : "s"}`}`;
  }
  return activeValidationScopeLabel();
}

function validationResultMessage(data) {
  const scope = validationScopeLabel(data);
  if (data.valid) {
    return `Valid Turtle / SHACL. Validation OK: ${scope}.`;
  }
  if (data.syntax_valid === false) return `Shape/Turtle parse error:\n${data.error}`;
  if (data.profile_valid === false) {
    const report = data.report_text || data.error || "";
    return `SHACL2SHACL validation failed (${scope}):\n${report}`;
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
  btn.addEventListener("click", async () => {
    if (getAccepted().length === 0) { setStatus("No accepted shapes to export"); return; }
    const node = getNodeShapes ? getNodeShapes() : "";
    const generatedDocument = buildTurtleDocument(node);
    const mode = getAstreaMergeMode();
    const baseline = astreaBaselinePayload();
    btn.disabled = true;
    try {
      if (mode === "none") {
        downloadText("generated_shapes.ttl", generatedDocument, "text/turtle");
        setStatus("Exported generated_shapes.ttl · no Astrea merge");
        return;
      }
      if (!baseline) throw new Error("Load an Astrea baseline before merging.");
      setStatus(`Merging final shapes · ${mode}`);
      const result = await fetchJSON(SERVICES.merge, {
        method: "POST",
        body: JSON.stringify({
          generated_shapes: generatedDocument,
          generated_filename: "generated_shapes.ttl",
          astrea_baseline: baseline,
          technique: mode,
          validation_profiles: getShapeValidationProfiles(),
        }),
      }, { label: "Merge Astrea baseline", timeoutMs: 30000 });
      if (!result.valid) {
        throw new Error(result.report_text || result.error || "Merged shapes failed validation.");
      }
      const filename = `generated_shapes_${mode}.ttl`;
      downloadText(filename, result.shape_document, "text/turtle");
      const warnings = Array.isArray(result.warnings) ? result.warnings.length : 0;
      setStatus(`Exported ${filename}${warnings ? ` · ${warnings} merge warning(s)` : ""}`);
    } catch (error) {
      const panel = byId("validation-panel");
      if (panel) {
        panel.className = "validation-panel shape-error";
        panel.textContent = `Astrea merge failed:\n${error.message}`;
      }
      setStatus("Astrea merge failed");
    } finally {
      btn.disabled = false;
    }
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
      astreaBaseline: getAstreaBaseline(),
      astreaMergeMode: getAstreaMergeMode(),
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
        if (payload.astreaBaseline && payload.astreaBaseline.content) {
          setAstreaBaseline(payload.astreaBaseline);
        }
        if (payload.astreaMergeMode) setAstreaMergeMode(payload.astreaMergeMode);
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
    localStorage.removeItem(STORE.astreaBaseline);
    localStorage.removeItem(STORE.astreaMergeMode);
    location.reload();
  });
}
