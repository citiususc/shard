/* SHARD core helpers. */

/* Shared browser state and helpers for SHARD.
 *
 * This is a locally-served web app (run_demo.py), not a sandboxed artifact, so
 * localStorage is used to share the loaded ontology, model configuration,
 * prefixes and accepted shapes across the two pages.
 */

const SERVICES = {
  capabilities: "/api/v1/capabilities",
  parse:    "/api/v1/ontology/parse",
  terms:    "/api/v1/ontology/search",
  prepareTerms: "/api/v1/ontology/index",
  termStatus:   "/api/v1/ontology/index/status",
  cancelTerms:  "/api/v1/ontology/index/cancel",
  resolveRule: "/api/v1/rules/resolve-targets",
  build:    "/api/v1/shapes/build",
  validate: "/api/v1/shapes/validate",
  astrea:   "/api/v1/baselines/astrea",
  merge:    "/api/v1/shapes/merge",
  validateModel: "/api/v1/models/check",
  localModelStatus: "/api/v1/models/local/status",
  downloadLocalModel: "/api/v1/models/local/download",
  guide:    "/api/v1/guides/generate",
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

/* Suggested inference models. These values populate the option lists only;
   a fresh browser session never selects or downloads one automatically. */
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
    embedding: ["Qwen/Qwen3-Embedding-0.6B", "BAAI/bge-large-en-v1.5", "Alibaba-NLP/gte-large-en-v1.5"],
  },
};

const STORE = {
  ontology: "shard.ontology",   // ontology content, namespaces, prefixes and entity catalog
  models:   "shard.models",     // backend selection + local access token + model choices
  accepted: "shard.accepted",   // [{id, property, shape}]
  shapeProfiles: "shard.shapeProfiles", // [{id, name, size, content}]
  astreaBaseline: "shard.astreaBaseline", // API-generated baseline for the active ontology
  astreaUseMode: "shard.astreaUseMode", // none | baseline | merge | both
  astreaMergeTechnique: "shard.astreaMergeTechnique", // priority-llm | restrictive
  astreaMergeMode: "shard.astreaMergeMode", // deprecated session migration key
  executionLogs: "shard.executionLogs", // structured history of generation/review runs
};

const LEGACY_STORE = Object.fromEntries(
  Object.entries(STORE).map(([name, key]) => [key, `t2s.${key.slice("shard.".length)}`]),
);

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
  try {
    let raw = localStorage.getItem(key);
    if (raw == null && LEGACY_STORE[key]) {
      raw = localStorage.getItem(LEGACY_STORE[key]);
      if (raw != null) localStorage.setItem(key, raw);
    }
    return JSON.parse(raw) ?? fallback;
  }
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

function loadDeploymentCapabilities() {
  if (deploymentCapabilitiesRequest) return deploymentCapabilitiesRequest;
  deploymentCapabilitiesRequest = fetchJSON(SERVICES.capabilities, {}, {
    label: "Load deployment capabilities",
    timeoutMs: 5000,
  }).then((payload) => {
    const runtimeEndpoints = payload.api && payload.api.runtime_endpoints;
    if (runtimeEndpoints && typeof runtimeEndpoints === "object") {
      Object.keys(SERVICES).forEach((key) => {
        if (typeof runtimeEndpoints[key] === "string" && runtimeEndpoints[key]) {
          SERVICES[key] = runtimeEndpoints[key];
        }
      });
    }
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
