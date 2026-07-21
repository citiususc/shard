/* SHARD export helpers. */

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
    const useMode = getAstreaUseMode();
    btn.disabled = true;
    try {
      if (!astreaUsesMerge(useMode)) {
        downloadText("shard_shapes.ttl", generatedDocument, "text/turtle");
        setStatus("Exported shard_shapes.ttl · no Astrea merge");
        return;
      }
      let baseline = astreaBaselinePayload();
      if (!baseline) {
        await ensureAstreaBaseline();
        baseline = astreaBaselinePayload();
      }
      if (!baseline) {
        downloadText("shard_shapes.ttl", generatedDocument, "text/turtle");
        setStatus("Exported without Astrea because its baseline was unavailable");
        return;
      }
      const technique = getAstreaMergeTechnique();
      setStatus(`Merging final shapes · ${technique}`);
      const result = await fetchJSON(SERVICES.merge, {
        method: "POST",
        body: JSON.stringify({
          generated: { name: "shard_shapes.ttl", content: generatedDocument },
          baseline: { name: baseline.name || "astrea.ttl", content: baseline.content },
          merge_strategy: technique,
          validation: apiValidationOptions(),
        }),
      }, { label: "Merge Astrea baseline", timeoutMs: 30000 });
      if (!result.valid) {
        throw new Error(result.report_text || result.error || "Merged shapes failed validation.");
      }
      const filename = `shard_shapes_${technique}.ttl`;
      downloadText(filename, result.shape_document, "text/turtle");
      const warnings = result.merge && Array.isArray(result.merge.warnings)
        ? result.merge.warnings.length : 0;
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
const SESSION_EXAMPLES_MANIFEST = "examples/manifest.json";
const PENDING_SESSION_WORKSPACE = "shard.pendingSessionWorkspace";

function sanitizedModelsForExport() {
  const m = getModels();
  return {
    provider: m.provider,
    llmModel: m.llmModel,
    embeddingModel: m.embeddingModel,
    temperature: m.temperature,
    customModels: m.customModels,
  };
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
  if (!raw) return;
  try {
    const pending = JSON.parse(raw);
    const workspace = pending && pending.workspace;
    if (!workspace || workspace.workflow !== options.workflow) return;
    if (typeof options.applyWorkspaceState === "function") {
      options.applyWorkspaceState(workspace);
    }
    sessionStorage.removeItem(PENDING_SESSION_WORKSPACE);
    setStatus(`${pending.sourceLabel || "Session"} imported`);
  } catch (error) {
    sessionStorage.removeItem(PENDING_SESSION_WORKSPACE);
    setStatus(`Could not restore session workspace: ${error.message}`);
  }
}

function importSessionPayload(payload, options = {}) {
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
  if (sessionHas(payload, "astreaBaseline")) {
    setAstreaBaseline(payload.astreaBaseline && payload.astreaBaseline.content
      ? payload.astreaBaseline : null);
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
    const current = getModels();
    const pick = (key, fallback) => sessionHas(payload.models, key)
      ? payload.models[key] : fallback;
    saveJSON(STORE.models, mergeModels(current, {
      provider: pick("provider", current.provider),
      llmModel: pick("llmModel", current.llmModel),
      embeddingModel: pick("embeddingModel", current.embeddingModel),
      temperature: clampTemperature(pick("temperature", current.temperature)),
      customModels: pick("customModels", current.customModels),
    }));
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
            importSessionPayload(payload, {
              workflow: options.workflow,
              sourceLabel: example.title || "Example session",
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
    const payload = {
      application: "SHARD",
      version: 3,
      exportedAt: new Date().toISOString(),
      ontology: getOntology(),
      accepted: getAccepted(),
      shapeValidationProfiles: getShapeValidationProfiles(),
      astreaBaseline: getAstreaBaseline(),
      astreaUseMode: getAstreaUseMode(),
      astreaMergeTechnique: getAstreaMergeTechnique(),
      models: sanitizedModelsForExport(),
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
    }
  });

  if (importBtn && importInput) {
    createSessionImportMenu(importBtn, importInput, options);
    importInput.addEventListener("change", async (ev) => {
      const file = ev.target.files && ev.target.files[0];
      if (!file) return;
      try {
        const payload = JSON.parse(await file.text());
        importSessionPayload(payload, {
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

  restorePendingSessionWorkspace(options);
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
    const o = getOntology();
    if (o) {
      await cancelOntologyEmbeddingPreparation(activeOntologyEmbedding || {
        ontologyHash: o.contentHash,
        embeddingModel: getModels().embeddingModel,
        configFingerprint: modelConfigFingerprint(),
        inferenceConfig: getInferenceConfig(),
      });
    }
    removeStoredValue(STORE.ontology);
    removeStoredValue(STORE.accepted);
    removeStoredValue(STORE.shapeProfiles);
    removeStoredValue(STORE.astreaBaseline);
    removeStoredValue(STORE.astreaUseMode);
    removeStoredValue(STORE.astreaMergeTechnique);
    removeStoredValue(STORE.astreaMergeMode);
    location.reload();
  });
}
