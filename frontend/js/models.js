/* SHARD models helpers. */

/* ---------- model config ---------- */
const DEFAULT_PROVIDER = "databricks";
const DEFAULT_TEMPERATURE = 0.5;
const MODEL_SELECTION_POLICY_KEY = "shard.modelSelectionPolicy";
const MODEL_SELECTION_POLICY = "explicit-v1";
const LOCAL_MODEL_AVAILABILITY = {
  llmModel: "idle",
  embeddingModel: "idle",
};

const MODEL_ROLE_CATALOG = {
  llmModel: "chat",
  embeddingModel: "embedding",
};

const MODEL_ROLE_SELECTS = [
  { key: "llmModel", selectId: "llm-model", label: "Generation LLM", catalogRole: "chat" },
  { key: "embeddingModel", selectId: "embedding-model", label: "Embedding model", catalogRole: "embedding" },
];

function emptyCustomModels() {
  return {
    databricks: { chat: [], embedding: [] },
    huggingface: { chat: [], embedding: [] },
  };
}

function uniqueList(values) {
  return Array.from(new Set((values || []).filter(Boolean).map(String)));
}

function normaliseCustomModels(value) {
  const out = emptyCustomModels();
  ["databricks", "huggingface"].forEach((provider) => {
    ["chat", "embedding"].forEach((role) => {
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
  return {
    provider,
    llmModel: "",
    embeddingModel: "",
    temperature: DEFAULT_TEMPERATURE,
    huggingface: { token: "" },
    customModels: emptyCustomModels(),
  };
}

function defaultModelSelection(provider) {
  return {
    llmModel: "",
    embeddingModel: "",
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
    return defaults.includes(storedValue) ? storedValue : "";
  };
  return {
    provider,
    llmModel:       pick("llmModel"),
    embeddingModel: pick("embeddingModel"),
    temperature: clampTemperature(stored && stored.temperature),
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
    huggingface: { ...(base.huggingface || {}), ...(patch.huggingface || {}) },
    customModels: patch.customModels ? normaliseCustomModels(patch.customModels) : base.customModels,
  };
}

function setModels(patch) { saveJSON(STORE.models, mergeModels(getModels(), patch)); }

function fillSelect(select, options, selected, placeholder = "Select a model") {
  if (!select) return;
  options = options || [];
  select.innerHTML = "";
  const empty = document.createElement("option");
  empty.value = "";
  empty.textContent = placeholder;
  empty.selected = !selected;
  select.appendChild(empty);
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
  const config = {
    provider: m.provider,
    temperature: m.temperature,
  };
  if (m.provider === "huggingface" && m.huggingface.token) {
    config.huggingface = { token: m.huggingface.token };
  }
  return config;
}

function providerUnavailableMessage(provider) {
  return provider === "huggingface"
    ? "Local inference is unavailable in this deployment."
    : "Remote inference is unavailable in this deployment.";
}

function semanticSettingsStatus(models = getModels()) {
  if (!providerIsEnabled(models.provider)) {
    return {
      ready: false,
      message: providerCapability(models.provider).message
        || providerUnavailableMessage(models.provider),
    };
  }
  if (!models.embeddingModel) {
    return {
      ready: false,
      message: "Semantic ranking disabled until model settings are configured.",
    };
  }
  if (models.provider === "huggingface"
      && LOCAL_MODEL_AVAILABILITY.embeddingModel !== "ready") {
    return {
      ready: false,
      message: "Semantic ranking requires a downloaded local embedding model.",
    };
  }
  return { ready: true, message: "" };
}

function generationSettingsStatus(models = getModels()) {
  if (!providerIsEnabled(models.provider)) {
    return {
      ready: false,
      message: providerCapability(models.provider).message
        || providerUnavailableMessage(models.provider),
    };
  }
  if (!models.llmModel) {
    return {
      ready: false,
      message: "Generation disabled until a model is selected.",
    };
  }
  if (models.provider === "huggingface"
      && LOCAL_MODEL_AVAILABILITY.llmModel !== "ready") {
    return {
      ready: false,
      message: "Generation requires a downloaded local model.",
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
    if (!catalogRole) continue;
    if (!modelId) {
      return { ok: false, role: roleKey, message: "Select every required model first." };
    }
    if (models.provider === "huggingface"
        && LOCAL_MODEL_AVAILABILITY[roleKey] !== "ready") {
      return {
        ok: false,
        role: roleKey,
        model: modelId,
        message: `Local model '${modelId}' has not been downloaded.`,
      };
    }
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

function migrateToExplicitModelSelection() {
  try {
    if (localStorage.getItem(MODEL_SELECTION_POLICY_KEY) === MODEL_SELECTION_POLICY) return;
    const stored = loadJSON(STORE.models, null);
    if (stored) saveJSON(STORE.models, { ...stored, llmModel: "", embeddingModel: "" });
    localStorage.setItem(MODEL_SELECTION_POLICY_KEY, MODEL_SELECTION_POLICY);
  } catch { /* storage may be unavailable */ }
}

/* Wire the provider toggle + model selects present in a page's rail. ids:
   provider buttons ([data-provider]), llm-model, embedding-model, optional
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

  function rowForKey(roleKey) {
    return MODEL_ROLE_SELECTS.find((row) => row.key === roleKey);
  }

  function renderLocalState(roleKey, state = "idle", message = "") {
    LOCAL_MODEL_AVAILABILITY[roleKey] = state;
    const row = rowForKey(roleKey);
    const element = row && byId(`${row.selectId}-local-state`);
    if (!element) return;
    const visible = getModels().provider === "huggingface" && state !== "idle" && message;
    element.hidden = !visible;
    element.dataset.state = state;
    element.textContent = visible ? message : "";
  }

  function renderLocalProgress(roleKey, event = null) {
    const row = rowForKey(roleKey);
    const container = row && byId(`${row.selectId}-download`);
    if (!container) return;
    if (!event) {
      container.hidden = true;
      return;
    }
    const progress = container.querySelector("progress");
    const label = container.querySelector("small");
    const percent = Number(event.percent);
    container.hidden = false;
    if (progress) progress.value = Number.isFinite(percent) ? Math.max(0, Math.min(100, percent)) : 0;
    if (label) {
      if (event.total_bytes && event.type === "start") {
        const units = ["B", "KB", "MB", "GB", "TB"];
        let value = Number(event.total_bytes) || 0;
        let unit = 0;
        while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit += 1; }
        label.textContent = `${value.toFixed(unit ? 1 : 0)} ${units[unit]}`;
      } else {
        label.textContent = event.message || `${Math.round(percent || 0)}%`;
      }
    }
  }

  function localModelPayload(modelId) {
    return {
      provider: "huggingface",
      model: modelId,
      inference_config: getInferenceConfig(),
    };
  }

  async function consumeLocalDownload(response, roleKey) {
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let completed = false;
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let separator;
      while ((separator = buffer.indexOf("\n\n")) !== -1) {
        const chunk = buffer.slice(0, separator);
        buffer = buffer.slice(separator + 2);
        const line = chunk.split("\n").find((item) => item.startsWith("data: "));
        if (!line) continue;
        const event = JSON.parse(line.slice(6));
        if (event.type === "error") throw new Error(event.message || "Local model download failed.");
        if (event.type === "done") {
          completed = true;
          renderLocalProgress(roleKey, { ...event, percent: 100 });
          break;
        }
        renderLocalProgress(roleKey, event);
      }
      if (completed) break;
    }
    if (!completed) throw new Error("The local model download ended before completion.");
  }

  async function downloadSelectedLocalModel(roleKey, modelId) {
    renderLocalState(roleKey, "downloading", "Downloading local model…");
    renderLocalProgress(roleKey, { percent: 0, message: "Starting…" });
    const response = await fetchStream(SERVICES.downloadLocalModel, {
      method: "POST",
      body: JSON.stringify(localModelPayload(modelId)),
    }, { label: `Download local model '${modelId}'`, timeoutMs: 0 });
    await consumeLocalDownload(response, roleKey);
    renderLocalProgress(roleKey, null);
    renderLocalState(roleKey, "ready", "Downloaded locally");
    return true;
  }

  async function ensureLocalModel(roleKey, modelId, askToDownload) {
    renderLocalProgress(roleKey, null);
    renderLocalState(roleKey, "checking", "Checking local cache…");
    const status = await fetchJSON(SERVICES.localModelStatus, {
      method: "POST",
      body: JSON.stringify(localModelPayload(modelId)),
    }, { label: `Check local model '${modelId}'`, timeoutMs: 25000 });
    if (status.downloaded) {
      renderLocalState(roleKey, "ready", "Downloaded locally");
      return true;
    }
    renderLocalState(roleKey, "missing", "Not downloaded locally");
    if (!askToDownload) return false;
    const confirmed = window.confirm(
      `The model '${modelId}' is not downloaded locally. Download it now? `
      + "The download may be large and inference will remain unavailable until it finishes.",
    );
    if (!confirmed) return false;
    return downloadSelectedLocalModel(roleKey, modelId);
  }

  function clearRoleSelection(roleKey, modelId) {
    const models = getModels();
    if (models[roleKey] !== modelId) return;
    setModels({ [roleKey]: "" });
    const row = rowForKey(roleKey);
    const select = row && byId(row.selectId);
    if (select) select.value = "";
    renderLocalProgress(roleKey, null);
    renderLocalState(roleKey, "idle", "");
  }

  async function handleRoleSelection(roleKey, modelId) {
    const row = rowForKey(roleKey);
    const select = row && byId(row.selectId);
    if (select) select.disabled = true;
    setModels({ [roleKey]: modelId });
    let available = true;
    try {
      if (getModels().provider === "huggingface" && modelId) {
        available = await ensureLocalModel(roleKey, modelId, true);
        if (!available) clearRoleSelection(roleKey, modelId);
      } else {
        renderLocalProgress(roleKey, null);
        renderLocalState(roleKey, "idle", "");
      }
    } catch (error) {
      available = false;
      renderLocalProgress(roleKey, null);
      renderLocalState(roleKey, "error", error.message || String(error));
      setModelStatus(error.message || String(error), "error");
      clearRoleSelection(roleKey, modelId);
    } finally {
      if (select) select.disabled = !providerIsEnabled(getModels().provider);
    }
    if (roleKey === "embeddingModel") {
      document.dispatchEvent(new CustomEvent("embedding-model-changed", {
        detail: {
          embeddingModel: available ? modelId : "",
          configFingerprint: modelConfigFingerprint(),
        },
      }));
    }
  }

  function apply(provider, keepSelections) {
    const current = getModels();
    const sel = keepSelections
      ? { ...current, provider }
      : mergeModels(current, { provider, ...defaultModelSelection(provider) });
    saveJSON(STORE.models, sel);

    let fresh = getModels();
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
          || "Local inference is unavailable in this deployment.";
      }
      if (link) link.href = deploymentCapabilities.repository_url;
    });
    document.querySelectorAll("[data-inference-setting]").forEach((el) => {
      el.hidden = !providerEnabled;
    });
    const customModelsEnabled = deploymentCapabilities.deployment_profile !== "public";
    document.querySelectorAll("[data-custom-model-setting]").forEach((el) => {
      el.hidden = !providerEnabled || !customModelsEnabled;
    });

    const visibleCustomModels = customModelsEnabled
      ? fresh.customModels
      : emptyCustomModels();
    const invalidSelections = {};
    MODEL_ROLE_SELECTS.forEach((row) => {
      const options = catalogOptions(provider, row.catalogRole, visibleCustomModels);
      if (fresh[row.key] && !options.includes(fresh[row.key])) {
        invalidSelections[row.key] = "";
      }
    });
    if (Object.keys(invalidSelections).length) {
      setModels(invalidSelections);
      fresh = getModels();
    }

    MODEL_ROLE_SELECTS.forEach((row) => {
      fillSelect(
        byId(row.selectId),
        catalogOptions(provider, row.catalogRole, visibleCustomModels),
        fresh[row.key],
        `Select ${row.label.toLowerCase()}`,
      );
    });

    if (byId("hf-token")) byId("hf-token").value = fresh.huggingface.token || "";
    if (byId("temperature")) byId("temperature").value = String(fresh.temperature);
    fillCustomRoleSelect();
    const providerControlIds = [
      "temperature", "llm-model", "embedding-model",
      "custom-model-role", "custom-model-id", "add-custom-model",
    ];
    providerControlIds.forEach((id) => {
      const control = byId(id);
      if (control) {
        const customControl = ["custom-model-role", "custom-model-id", "add-custom-model"].includes(id);
        control.disabled = !providerEnabled || (customControl && !customModelsEnabled);
      }
    });
    MODEL_ROLE_SELECTS.forEach((row) => {
      renderLocalProgress(row.key, null);
      if (provider !== "huggingface" || !fresh[row.key]) {
        renderLocalState(row.key, "idle", "");
        return;
      }
      ensureLocalModel(row.key, fresh[row.key], false).then((available) => {
        if (!available) clearRoleSelection(row.key, fresh[row.key]);
      }).catch((error) => {
        renderLocalState(row.key, "error", error.message || String(error));
        clearRoleSelection(row.key, fresh[row.key]);
      });
    });
    const readyMessage = provider === "huggingface"
      ? "Local model settings are stored in this browser."
      : "Remote inference is configured by this deployment.";
    setModelStatus(providerEnabled
      ? readyMessage
      : (providerCapability(provider).message || providerUnavailableMessage(provider)));
  }

  migrateToExplicitModelSelection();
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

  MODEL_ROLE_SELECTS.forEach((row) => {
    const select = byId(row.selectId);
    if (!select) return;
    select.addEventListener("change", () => {
      handleRoleSelection(row.key, select.value);
    });
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
        if (models.provider === "huggingface") {
          if (!modelId.includes("/")) {
            throw new Error("Use a repository-style model id such as organisation/model.");
          }
          await fetchJSON(SERVICES.localModelStatus, {
            method: "POST",
            body: JSON.stringify(localModelPayload(modelId)),
          }, { label: `Check local model '${modelId}'`, timeoutMs: 25000 });
        } else {
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
        }

        const customModels = normaliseCustomModels(models.customModels);
        const list = customModels[models.provider][role];
        if (!list.includes(modelId)) list.push(modelId);
        setModels({ customModels });
        apply(models.provider, true);
        if (modelInput) modelInput.value = "";
        setModelStatus(`Added '${modelId}'. Select it to use it.`, "ok");
      } catch (err) {
        setModelStatus(err.message || String(err), "error");
      } finally {
        addBtn.disabled = false;
      }
    });
  }
}
