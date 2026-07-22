/* SHARD core helpers. */

/* Shared browser state and helpers for SHARD.
 *
 * This is a locally-served web app (run_demo.py), not a sandboxed artifact, so
 * localStorage is used to share the loaded ontology, model configuration,
 * prefixes and accepted shapes across the two pages.
 */

function normalizeApiBase(value) {
  const base = String(value || "api/v1/").trim() || "api/v1/";
  return base.endsWith("/") ? base : `${base}/`;
}

const API_BASE = normalizeApiBase(window.SHARD_API_BASE || "api/v1/");
const CANONICAL_API_PREFIX = "/api/v1/";

function apiUrl(path) {
  return `${API_BASE}${String(path || "").replace(/^\/+/, "")}`;
}

function isLoopbackHostname(hostname) {
  const host = String(hostname || "").trim().toLowerCase().replace(/^\[|\]$/g, "");
  return host === "localhost" || host === "::1" || /^127(?:\.\d{1,3}){3}$/.test(host);
}

function resolveRuntimeEndpoint(value) {
  const endpoint = String(value || "").trim();
  if (!endpoint) return "";
  if (endpoint === CANONICAL_API_PREFIX.slice(0, -1)) {
    return API_BASE.slice(0, -1);
  }
  if (endpoint.startsWith(CANONICAL_API_PREFIX)) {
    return apiUrl(endpoint.slice(CANONICAL_API_PREFIX.length));
  }
  if (endpoint.startsWith(CANONICAL_API_PREFIX.slice(1))) {
    return apiUrl(endpoint.slice(CANONICAL_API_PREFIX.length - 1));
  }

  try {
    const parsed = new URL(endpoint, window.location.href);
    if (isLoopbackHostname(parsed.hostname)
        && !isLoopbackHostname(window.location.hostname)) {
      return "";
    }
  } catch (_err) {
    return "";
  }
  return endpoint;
}

const SERVICES = {
  capabilities: apiUrl("capabilities"),
  parse: apiUrl("ontology/parse"),
  prepareTerms: apiUrl("ontology/indexes"),
  resolveRule: apiUrl("rules/resolve-targets"),
  build: apiUrl("shapes/build"),
  validate: apiUrl("shapes/validate"),
  exportShapes: apiUrl("shapes/export"),
  astrea: apiUrl("baselines/astrea"),
  validateModel: apiUrl("models/check"),
  localModelStatus: apiUrl("models/local/status"),
  downloadLocalModel: apiUrl("models/local/downloads"),
  batch: apiUrl("batches/generate"),
};

function apiOntologyInput(ontology) {
  return {
    filename: (ontology && ontology.filename) || "ontology.ttl",
    content: (ontology && ontology.content) || "",
  };
}

function apiBusinessRule(text, number = "RULE-001", title = "Data constraint") {
  return { number, title, text: String(text || "") };
}

function apiTermReference(term) {
  if (typeof term === "string") return { iri: term };
  return {
    iri: String((term && (term.iri || term.target || term.full_iri)) || ""),
    ...(term && term.label ? { label: String(term.label) } : {}),
  };
}

function apiReferenceList(value) {
  const values = Array.isArray(value) ? value : [value];
  return values.filter(Boolean).map(apiTermReference).filter((item) => item.iri);
}

function apiOntologyTerm(term) {
  return {
    id: String(term.id || ""),
    iri: String(term.iri || term.full_iri || ""),
    full_iri: String(term.full_iri || term.iri || ""),
    label: String(term.label || ""),
    type: term.type,
    kind: String(term.kind || ""),
    domain: apiReferenceList(term.domain),
    range: apiReferenceList(term.range),
    superclasses: apiReferenceList(term.superclasses || []),
    comment: String(term.comment || ""),
    ontology_note: String(term.ontology_note || term.ontologyNote || ""),
    annotations: term.annotations && typeof term.annotations === "object"
      ? term.annotations : {},
  };
}

function apiInferenceOptions(models = getModels()) {
  const config = getInferenceConfig();
  return {
    provider: models.provider,
    ...(models.llmModel ? { generation_model: models.llmModel } : {}),
    ...(models.embeddingModel ? { embedding_model: models.embeddingModel } : {}),
    temperature: models.temperature,
    ...(config.databricks ? { databricks: config.databricks } : {}),
    ...(config.huggingface ? { huggingface: config.huggingface } : {}),
  };
}

function apiValidationOptions() {
  return {
    profiles: getShapeValidationProfiles().map((profile) => ({
      name: profile.name || "profile.ttl",
      content: profile.content || "",
    })),
  };
}

function apiAstreaOptions() {
  const baseline = getAstreaUseMode() === "none" ? null : astreaBaselinePayload();
  return {
    mode: getAstreaUseMode(),
    merge_strategy: getAstreaMergeTechnique(),
    failure_policy: "continue",
    ...(baseline && baseline.content
      ? { baseline: { name: baseline.name || "astrea.ttl", content: baseline.content } }
      : {}),
  };
}

const DEFAULT_DEPLOYMENT_CAPABILITIES = {
  deployment_profile: "local",
  repository_url: "https://github.com/citiususc/shard",
  providers: {
    databricks: { enabled: true, execution: "remote" },
    huggingface: { enabled: true, execution: "local", message: "" },
  },
};
let deploymentCapabilities = DEFAULT_DEPLOYMENT_CAPABILITIES;
let deploymentCapabilitiesRequest = null;
let deploymentCapabilitiesLoaded = false;

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
  astreaBaselines: "shard.astreaBaselines", // ontologyHash -> reusable API baseline
  astreaUseMode: "shard.astreaUseMode", // none | evidence | merge | evidence-and-merge
  astreaMergeTechnique: "shard.astreaMergeTechnique", // generated-priority | restrictive
  astreaMergeMode: "shard.astreaMergeMode", // deprecated session migration key
  executionLogs: "shard.executionLogs", // structured history of generation/review runs
  ruleWorkspace: "shard.workspace.rule", // current Rule-to-Shape authoring state
  batchWorkspace: "shard.workspace.batch", // current Batch-to-Shapes authoring state
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
    const legacyKey = LEGACY_STORE[key];
    if (raw == null && legacyKey) {
      raw = localStorage.getItem(legacyKey);
      if (raw != null) localStorage.setItem(key, raw);
    }
    if (legacyKey && raw != null) localStorage.removeItem(legacyKey);
    return JSON.parse(raw) ?? fallback;
  }
  catch { return fallback; }
}
function saveJSON(key, value) { localStorage.setItem(key, JSON.stringify(value)); }
function removeStoredValue(key) {
  localStorage.removeItem(key);
  if (LEGACY_STORE[key]) localStorage.removeItem(LEGACY_STORE[key]);
}


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
      const endpointKeys = {
        prepare_terms: "prepareTerms",
        resolve_rule: "resolveRule",
        validate_model: "validateModel",
        local_model_status: "localModelStatus",
        download_local_model: "downloadLocalModel",
        export_shapes: "exportShapes",
      };
      Object.keys(runtimeEndpoints).forEach((publicKey) => {
        const key = endpointKeys[publicKey] || publicKey;
        const endpoint = resolveRuntimeEndpoint(runtimeEndpoints[publicKey]);
        if (Object.prototype.hasOwnProperty.call(SERVICES, key)
            && endpoint) {
          SERVICES[key] = endpoint;
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
    deploymentCapabilitiesLoaded = true;
    return deploymentCapabilities;
  }).catch((err) => {
    console.warn("Could not load deployment capabilities; using local defaults.", err);
    deploymentCapabilitiesLoaded = true;
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
