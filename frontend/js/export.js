/* SHARD export helpers. */

/* ---------- export ---------- */
async function buildTurtleDocument() {
  const o = getOntology();
  const prefixes = (o && o.prefixes) || "";
  const documents = getAccepted()
    .map((item, index) => ({
      name: `${item.property || `accepted-shape-${index + 1}`}.ttl`,
      content: String(item.shape || "").trim(),
    }))
    .filter((item) => item.content);
  if (!documents.length) throw new Error("No accepted shapes to export.");
  const result = await fetchJSON(SERVICES.exportShapes, {
    method: "POST",
    body: JSON.stringify({
      documents,
      prefixes,
      validation: apiValidationOptions(),
    }),
  }, { label: "Prepare accepted shape export", timeoutMs: 120000 });
  if (!result.valid) {
    throw new Error(
      result.report_text || result.error || result.message
      || "The consolidated SHACL export did not pass validation."
    );
  }
  if (!result.statistics || result.statistics.constraints_preserved !== true) {
    throw new Error("The backend could not prove that all reviewed constraints were preserved.");
  }
  return result;
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

function wireExport(buttonId) {
  const btn = byId(buttonId);
  if (!btn) return;
  btn.addEventListener("click", async () => {
    if (getAccepted().length === 0) { setStatus("No accepted shapes to export"); return; }
    btn.disabled = true;
    try {
      const result = await buildTurtleDocument();
      downloadText("shard_shapes.ttl", result.shape_document, "text/turtle");
      const stats = result.statistics;
      const cleaned = stats.duplicate_constraints_removed + stats.empty_node_shapes_removed;
      setStatus(
        `Exported shard_shapes.ttl · ${stats.distinct_constraints} constraints preserved`
        + (cleaned ? ` · ${cleaned} redundant item(s) removed` : "")
      );
    } catch (error) {
      const panel = byId("validation-panel");
      if (panel) {
        panel.className = "validation-panel shape-error";
        panel.textContent = `Shape export failed:\n${error.message}`;
      }
      setStatus("Shape export failed");
    } finally {
      btn.disabled = false;
    }
  });
}

/* ---------- session import / export ---------- */
const SESSION_EXAMPLES_MANIFEST = "examples/manifest.json";
const PENDING_SESSION_WORKSPACE = "shard.pendingSessionWorkspace";
const WORKSPACE_RESET_MARKER = "shard.workspaceResetPending";
let workspacePersistenceTimer = null;
let workspacePersistenceSuspended = false;

function workspaceStoreKey(workflow) {
  return workflow === "rule" ? STORE.ruleWorkspace
    : workflow === "batch" ? STORE.batchWorkspace : "";
}

function persistedWorkspace(workflow) {
  const key = workspaceStoreKey(workflow);
  return key ? loadJSON(key, null) : null;
}

function persistWorkspace(options) {
  if (workspacePersistenceSuspended) return;
  if (!options || typeof options.getWorkspaceState !== "function") return;
  const key = workspaceStoreKey(options.workflow);
  if (!key) return;
  const workspace = options.getWorkspaceState();
  if (workspace && typeof workspace === "object") saveJSON(key, workspace);
}

function scheduleWorkspacePersistence() {
  if (workspacePersistenceSuspended) return;
  document.dispatchEvent(new CustomEvent("shard-workspace-state-changed"));
}

function beginWorkspaceReset() {
  workspacePersistenceSuspended = true;
  if (workspacePersistenceTimer) {
    clearTimeout(workspacePersistenceTimer);
    workspacePersistenceTimer = null;
  }
  sessionStorage.setItem(WORKSPACE_RESET_MARKER, "1");
}

function restoreWorkspaceOnLoad(options) {
  if (sessionStorage.getItem(WORKSPACE_RESET_MARKER) === "1") {
    sessionStorage.removeItem(WORKSPACE_RESET_MARKER);
    sessionStorage.removeItem(PENDING_SESSION_WORKSPACE);
    if (options && typeof options.applyWorkspaceState === "function") {
      options.applyWorkspaceState({ workflow: options.workflow });
    }
    return "reset";
  }
  if (restorePendingSessionWorkspace(options)) return "pending";
  if (restorePersistedWorkspace(options)) return "persisted";
  if (options && typeof options.applyWorkspaceState === "function") {
    options.applyWorkspaceState({ workflow: options.workflow });
  }
  return "empty";
}

async function localModelIsDownloaded(modelId) {
  if (!modelId) return false;
  try {
    const status = await fetchJSON(SERVICES.localModelStatus, {
      method: "POST",
      body: JSON.stringify({ model_id: modelId }),
    }, { label: `Check local model '${modelId}'`, timeoutMs: 25000 });
    return Boolean(status.downloaded);
  } catch {
    return false;
  }
}

async function sanitizedModelsForExport() {
  const m = getModels();
  let llmModel = m.llmModel;
  let embeddingModel = m.embeddingModel;
  if (m.provider === "huggingface") {
    const availability = await Promise.all([
      localModelIsDownloaded(llmModel),
      localModelIsDownloaded(embeddingModel),
    ]);
    if (!availability[0]) llmModel = "";
    if (!availability[1]) embeddingModel = "";
  }
  return {
    provider: m.provider,
    llmModel,
    embeddingModel,
    temperature: m.temperature,
    customModels: m.customModels,
    credentialsIncluded: false,
  };
}

async function importedModels(payloadModels, options = {}) {
  const current = getModels();
  if (!payloadModels || options.preserveCurrentModels) return current;

  const requestedProvider = String(payloadModels.provider || "");
  const provider = MODEL_CATALOG[requestedProvider] && providerIsEnabled(requestedProvider)
    ? requestedProvider : current.provider;
  const customModels = sessionHas(payloadModels, "customModels")
    ? normaliseCustomModels(payloadModels.customModels)
    : current.customModels;
  const portable = mergeModels(current, {
    provider,
    temperature: sessionHas(payloadModels, "temperature")
      ? clampTemperature(payloadModels.temperature) : current.temperature,
    customModels,
  });
  for (const key of ["llmModel", "embeddingModel"]) {
    const imported = normalizeModelId(provider, payloadModels[key]);
    portable[key] = imported || (provider === current.provider ? current[key] : "");
  }

  // Provider credentials remain local to this browser and are never imported.
  portable.databricks = current.databricks;
  portable.huggingface = current.huggingface;
  if (provider === "huggingface") {
    const availability = await Promise.all([
      localModelIsDownloaded(portable.llmModel),
      localModelIsDownloaded(portable.embeddingModel),
    ]);
    if (!availability[0]) portable.llmModel = "";
    if (!availability[1]) portable.embeddingModel = "";
  }
  return portable;
}

function sessionHas(payload, key) {
  return Object.prototype.hasOwnProperty.call(payload || {}, key);
}

function sessionWorkflowPage(workflow) {
  if (workflow === "rule") return "rule.html";
  if (workflow === "batch") return "batch.html";
  return "";
}

function savePendingSessionWorkspace(workspace, sourceLabel) {
  if (!workspace || typeof workspace !== "object") {
    sessionStorage.removeItem(PENDING_SESSION_WORKSPACE);
    return;
  }
  sessionStorage.setItem(PENDING_SESSION_WORKSPACE, JSON.stringify({
    workspace,
    sourceLabel: String(sourceLabel || "Imported session"),
  }));
}

function restorePendingSessionWorkspace(options) {
  const raw = sessionStorage.getItem(PENDING_SESSION_WORKSPACE);
  if (!raw) return false;
  try {
    const pending = JSON.parse(raw);
    const workspace = pending && pending.workspace;
    if (!workspace || workspace.workflow !== options.workflow) return false;
    if (typeof options.applyWorkspaceState === "function") {
      options.applyWorkspaceState(workspace);
    }
    const key = workspaceStoreKey(options.workflow);
    if (key) saveJSON(key, workspace);
    sessionStorage.removeItem(PENDING_SESSION_WORKSPACE);
    setStatus(`${pending.sourceLabel || "Session"} imported`);
    return true;
  } catch (error) {
    sessionStorage.removeItem(PENDING_SESSION_WORKSPACE);
    setStatus(`Could not restore session workspace: ${error.message}`);
    return false;
  }
}

function restorePersistedWorkspace(options) {
  if (!options || typeof options.applyWorkspaceState !== "function") return false;
  const workspace = persistedWorkspace(options.workflow);
  if (!workspace || workspace.workflow !== options.workflow) return false;
  options.applyWorkspaceState(workspace);
  return true;
}

async function importSessionPayload(payload, options = {}) {
  if (!payload || payload.application !== "SHARD") {
    throw new Error("The selected file is not a SHARD session.");
  }
  if (sessionHas(payload, "ontology")) {
    if (payload.ontology) setOntology(payload.ontology);
    else removeStoredValue(STORE.ontology);
  }
  if (sessionHas(payload, "accepted")) {
    setAccepted(Array.isArray(payload.accepted) ? payload.accepted : []);
  }
  if (sessionHas(payload, "shapeValidationProfiles")) {
    setShapeValidationProfiles(
      Array.isArray(payload.shapeValidationProfiles) ? payload.shapeValidationProfiles : [],
    );
  }
  if (sessionHas(payload, "astreaBaselines")) {
    importAstreaBaselines(payload.astreaBaselines);
  }
  if (payload.astreaBaseline && payload.astreaBaseline.content) {
    setAstreaBaseline(payload.astreaBaseline);
  } else if (sessionHas(payload, "astreaBaseline")) {
    // A legacy null value means that the session contributes no baseline. It
    // must not evict a reusable baseline already cached for this ontology.
    removeStoredValue(STORE.astreaBaseline);
  }
  if (payload.astreaMergeTechnique) {
    setAstreaMergeTechnique(payload.astreaMergeTechnique, { render: false });
  }
  if (payload.astreaUseMode) {
    setAstreaUseMode(payload.astreaUseMode, { render: false });
  } else if (payload.astreaMergeMode) {
    setAstreaMergeMode(payload.astreaMergeMode);
  }
  if (payload.models) {
    saveJSON(STORE.models, await importedModels(payload.models, options));
  }

  const sourceLabel = options.sourceLabel || "Session";
  savePendingSessionWorkspace(payload.workspace, sourceLabel);
  const targetPage = sessionWorkflowPage(payload.workspace && payload.workspace.workflow);
  if (targetPage && options.workflow && payload.workspace.workflow !== options.workflow) {
    location.assign(targetPage);
  } else {
    location.reload();
  }
}

function createSessionImportMenu(importBtn, importInput, options) {
  const menu = document.createElement("div");
  menu.className = "session-import-menu";
  menu.hidden = true;
  menu.setAttribute("role", "menu");
  menu.setAttribute("aria-label", "Import a SHARD session");
  menu.innerHTML = `
    <button class="session-import-option" type="button" role="menuitem" data-session-file>
      <span class="session-import-option-title">Import from file</span>
      <span class="session-import-option-detail">Open a previously exported SHARD session</span>
    </button>
    <div class="session-import-separator" role="separator"></div>
    <p class="session-import-heading">Preloaded examples</p>
    <div class="session-example-list" data-session-examples>
      <p class="session-import-loading">Loading examples...</p>
    </div>`;
  importBtn.closest(".session-toolbar").appendChild(menu);
  importBtn.setAttribute("aria-haspopup", "menu");
  importBtn.setAttribute("aria-expanded", "false");

  const close = () => {
    menu.hidden = true;
    importBtn.setAttribute("aria-expanded", "false");
  };
  const toggle = () => {
    menu.hidden = !menu.hidden;
    importBtn.setAttribute("aria-expanded", String(!menu.hidden));
  };
  importBtn.addEventListener("click", (event) => {
    event.stopPropagation();
    toggle();
  });
  menu.querySelector("[data-session-file]").addEventListener("click", () => {
    close();
    importInput.click();
  });
  menu.addEventListener("click", (event) => event.stopPropagation());
  document.addEventListener("click", close);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") close();
  });

  const manifestUrl = new URL(SESSION_EXAMPLES_MANIFEST, document.baseURI);
  const list = menu.querySelector("[data-session-examples]");
  fetch(manifestUrl)
    .then((response) => {
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return response.json();
    })
    .then((manifest) => {
      const examples = Array.isArray(manifest.examples) ? manifest.examples : [];
      list.innerHTML = "";
      examples.forEach((example) => {
        const button = document.createElement("button");
        button.className = "session-import-option session-example-option";
        button.type = "button";
        button.setAttribute("role", "menuitem");
        button.innerHTML = `
          <span class="session-import-option-title">${esc(example.title || example.id)}</span>
          <span class="session-import-option-detail">${esc(example.description || "")}</span>`;
        button.addEventListener("click", async () => {
          button.disabled = true;
          setStatus(`Loading ${example.title || "example"}...`);
          try {
            const sessionUrl = new URL(example.session, manifestUrl);
            const response = await fetch(sessionUrl);
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const payload = await response.json();
            await importSessionPayload(payload, {
              workflow: options.workflow,
              sourceLabel: example.title || "Example session",
              preserveCurrentModels: true,
            });
          } catch (error) {
            button.disabled = false;
            setStatus(`Could not load example: ${error.message}`);
          }
        });
        list.appendChild(button);
      });
      if (!examples.length) {
        list.innerHTML = '<p class="session-import-loading">No examples available.</p>';
      }
    })
    .catch((error) => {
      list.innerHTML = `<p class="session-import-error">Examples unavailable: ${esc(error.message)}</p>`;
    });
}

function wireSessionControls(options = {}) {
  const exportBtn = byId("export-session");
  const importBtn = byId("import-session");
  const importInput = byId("session-file");

  if (exportBtn) exportBtn.addEventListener("click", async () => {
    exportBtn.disabled = true;
    const payload = {
      application: "SHARD",
      version: 3,
      exportedAt: new Date().toISOString(),
      ontology: getOntology(),
      accepted: getAccepted(),
      shapeValidationProfiles: getShapeValidationProfiles(),
      astreaBaseline: getAstreaBaseline(),
      astreaBaselines: getAstreaBaselines(),
      astreaUseMode: getAstreaUseMode(),
      astreaMergeTechnique: getAstreaMergeTechnique(),
      models: await sanitizedModelsForExport(),
      workspace: typeof options.getWorkspaceState === "function"
        ? options.getWorkspaceState() : null,
    };
    try {
      const result = await saveTextAsFile(
        "shard_session.json",
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
    } finally {
      exportBtn.disabled = false;
    }
  });

  if (importBtn && importInput) {
    createSessionImportMenu(importBtn, importInput, options);
    importInput.addEventListener("change", async (ev) => {
      const file = ev.target.files && ev.target.files[0];
      if (!file) return;
      try {
        const payload = JSON.parse(await file.text());
        await importSessionPayload(payload, {
          workflow: options.workflow,
          sourceLabel: file.name,
        });
      } catch (e) {
        setStatus(`Could not import session: ${e.message}`);
      } finally {
        importInput.value = "";
      }
    });
  }

  const queuePersistence = () => {
    if (workspacePersistenceSuspended) return;
    if (workspacePersistenceTimer) clearTimeout(workspacePersistenceTimer);
    workspacePersistenceTimer = setTimeout(() => {
      workspacePersistenceTimer = null;
      persistWorkspace(options);
    }, 180);
  };
  document.addEventListener("input", queuePersistence, true);
  document.addEventListener("change", queuePersistence, true);
  document.addEventListener("shard-workspace-state-changed", queuePersistence);
  window.addEventListener("beforeunload", () => persistWorkspace(options));
  queueMicrotask(() => {
    restoreWorkspaceOnLoad(options);
    queuePersistence();
  });
}

/* ---------- copy / validate ---------- */
const copyFeedbackTimers = new WeakMap();

function showCopyFeedback(button, copied, durationMs = 2000) {
  if (!button) return;
  const previousTimer = copyFeedbackTimers.get(button);
  if (previousTimer) clearTimeout(previousTimer);
  const defaultLabel = button.dataset.copyDefaultLabel || button.textContent || "Copy";
  button.dataset.copyDefaultLabel = defaultLabel;
  button.textContent = copied ? "Copied" : "Copy failed";
  button.classList.toggle("copy-feedback-active", copied);
  button.setAttribute("aria-live", "polite");
  copyFeedbackTimers.set(button, setTimeout(() => {
    button.textContent = defaultLabel;
    button.classList.remove("copy-feedback-active");
    copyFeedbackTimers.delete(button);
  }, durationMs));
}

async function copyToClipboard(text) {
  try { await navigator.clipboard.writeText(text); return true; }
  catch { return false; }
}

async function validateTurtle(shape, prefixes) {
  return fetchJSON(SERVICES.validate, {
    method: "POST",
    body: JSON.stringify({
      shape_document: shape,
      prefixes,
      validation: apiValidationOptions(),
    }),
  }, { label: "Validate Turtle", timeoutMs: 30000 }); // {valid, error}
}

/* ---------- reset ---------- */
function wireReset(buttonId) {
  const btn = byId(buttonId);
  if (!btn) return;
  btn.addEventListener("click", async () => {
    if (!confirm("Clear the entire workspace? This removes the ontology, accepted shapes, validation profiles, Astrea settings, and current work.")) return;
    beginWorkspaceReset();
    const o = getOntology();
    const embeddingTarget = o
      ? activeOntologyEmbedding || {
        ontologyHash: o.contentHash,
        embeddingModel: getModels().embeddingModel,
        configFingerprint: modelConfigFingerprint(),
        inferenceConfig: getInferenceConfig(),
      }
      : null;
    removeStoredValue(STORE.ontology);
    removeStoredValue(STORE.accepted);
    removeStoredValue(STORE.shapeProfiles);
    removeStoredValue(STORE.astreaBaseline);
    removeStoredValue(STORE.astreaBaselines);
    removeStoredValue(STORE.astreaUseMode);
    removeStoredValue(STORE.astreaMergeTechnique);
    removeStoredValue(STORE.astreaMergeMode);
    removeStoredValue(STORE.ruleWorkspace);
    removeStoredValue(STORE.batchWorkspace);
    sessionStorage.removeItem(PENDING_SESSION_WORKSPACE);
    try {
      if (embeddingTarget) await cancelOntologyEmbeddingPreparation(embeddingTarget);
    } finally {
      location.reload();
    }
  });
}
