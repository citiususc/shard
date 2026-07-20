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
          generated_shapes: generatedDocument,
          generated_filename: "shard_shapes.ttl",
          astrea_baseline: baseline,
          technique,
          validation_profiles: getShapeValidationProfiles(),
        }),
      }, { label: "Merge Astrea baseline", timeoutMs: 30000 });
      if (!result.valid) {
        throw new Error(result.report_text || result.error || "Merged shapes failed validation.");
      }
      const filename = `shard_shapes_${technique}.ttl`;
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
      application: "SHARD",
      version: 2,
      exportedAt: new Date().toISOString(),
      ontology: getOntology(),
      accepted: getAccepted(),
      shapeValidationProfiles: getShapeValidationProfiles(),
      astreaBaseline: getAstreaBaseline(),
      astreaUseMode: getAstreaUseMode(),
      astreaMergeTechnique: getAstreaMergeTechnique(),
      models: sanitizedModelsForExport(),
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
          saveJSON(STORE.models, mergeModels(current, {
            provider: payload.models.provider || current.provider,
            llmModel: payload.models.llmModel || current.llmModel,
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
    localStorage.removeItem(STORE.astreaUseMode);
    localStorage.removeItem(STORE.astreaMergeTechnique);
    localStorage.removeItem(STORE.astreaMergeMode);
    location.reload();
  });
}
