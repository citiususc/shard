/* rule.js — Workflow 1: business rule → single SHACL shape. */

let entityFilter = "all";
let selectedEntity = null;
let lastCandidates = [];
let semanticSearchActive = false;
let ruleGenerationLogId = null;

document.addEventListener("DOMContentLoaded", () => {
  wireReset("reset-demo");
  wireSessionControls();
  wireModelControls();
  wireShapeValidationProfileControls();
  wireAstreaBaselineControls();
  wireExport("export-shapes", () => "");
  renderAccepted(byId("accepted-list"), byId("coverage-tag"));

  // IDE-style Turtle highlighting (attach before ontology wiring so the
  // prefixes editor refreshes when its value is seeded).
  attachTurtleHighlighter("shape-editor", "shape-editor-hl");
  attachTurtleHighlighter("prefixes-editor", "prefixes-editor-hl");

  wireOntologyControls(() => {
    selectedEntity = null;
    byId("selected-label").textContent = "Selected target";
    byId("selected-kind").textContent = "—";
    byId("selected-iri").textContent = "";
    byId("selected-domain").textContent = "";
    byId("selected-range").textContent = "";
    byId("ontology-note").value = "";
    byId("generate-shape").disabled = true;
    clearSemanticResults(false);
    renderEntities();
  });

  // Entity filter buttons (scoped to [data-filter] so the provider toggle,
  // which also uses .switch-button, is not affected).
  document.querySelectorAll("[data-filter]").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("[data-filter]").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      entityFilter = btn.dataset.filter;
      clearSemanticResults(false);
      renderEntities();
    });
  });
  byId("entity-search").addEventListener("input", renderEntities);
  byId("business-rule").addEventListener("input", () => clearSemanticResults(false));

  byId("analyze-rule").addEventListener("click", findRelevantTerms);
  byId("clear-related-terms").addEventListener("click", () => clearSemanticResults());
  byId("generate-shape").addEventListener("click", generateShape);
  byId("validate-shape").addEventListener("click", checkShape);
  byId("accept-shape").addEventListener("click", acceptCurrent);
  byId("copy-shape").addEventListener("click", async () => {
    const ok = await copyToClipboard(byId("shape-editor").value);
    setStatus(ok ? "Copied" : "Copy failed");
  });
});

/* ---------- entity browser ---------- */
function renderEntities() {
  const o = getOntology();
  const list = byId("entity-list");
  list.innerHTML = "";
  if (!o) { list.innerHTML = `<p class="microcopy">Load an ontology to browse its terms.</p>`; return; }

  const q = (byId("entity-search").value || "").toLowerCase();
  const candidateMap = new Map(lastCandidates.map((candidate) => [candidate.entity_id, candidate]));
  const source = semanticSearchActive
    ? lastCandidates.map((candidate) => o.entities.find((e) => e.id === candidate.entity_id)).filter(Boolean)
    : o.entities;
  const items = source.filter((e) => {
    if (entityFilter !== "all" && e.type !== entityFilter) return false;
    if (!q) return true;
    return [e.label, e.iri, e.domain, e.range].some((v) => String(v || "").toLowerCase().includes(q));
  });

  items.slice(0, 400).forEach((e) => {
    const candidate = candidateMap.get(e.id);
    const card = document.createElement("button");
    card.className = "entity-card"
      + (candidate ? " ranked" : "")
      + (selectedEntity && selectedEntity.id === e.id ? " active" : "");
    card.innerHTML =
      (candidate ? `<div class="score">${candidate.score}</div>` : "") +
      `<strong>${esc(e.label)}</strong>` +
      `<span>${esc(e.iri)}${e.type === "property" && e.domain ? " · domain " + esc(e.domain) : ""}</span>` +
      (candidate ? `<small class="entity-reason">${esc((candidate.reasons || []).join(" · "))}</small>` : "");
    card.addEventListener("click", () => selectEntity(e));
    list.appendChild(card);
  });
  if (!items.length) {
    list.innerHTML = `<p class="microcopy">${semanticSearchActive
      ? "No related terms matched the current text search."
      : "No ontology terms matched the current filters."}</p>`;
  }
}

function selectEntity(e) {
  selectedEntity = e;
  byId("selected-label").textContent = e.label;
  byId("selected-kind").textContent = e.kind || e.type;
  byId("selected-iri").textContent = e.iri;
  byId("selected-domain").textContent = e.domain || "—";
  byId("selected-range").textContent = e.range || "—";
  byId("ontology-note").value = e.ontologyNote || "";
  byId("generate-shape").disabled = false;
  renderEntities();
}

/* ---------- find relevant terms ---------- */
function clearSemanticResults(render = true) {
  lastCandidates = [];
  semanticSearchActive = false;
  const clear = byId("clear-related-terms");
  if (clear) clear.hidden = true;
  const status = byId("ontology-search-status");
  if (status) status.textContent = "Search by text or rank terms against the business rule.";
  if (render) renderEntities();
}

async function findRelevantTerms() {
  const o = getOntology();
  if (!o) { setStatus("Load an ontology first"); return; }
  const rule = byId("business-rule").value.trim();
  if (!rule) { setStatus("Write a business rule first"); return; }

  const m = getModels();
  const semanticSettings = semanticSettingsStatus(m);
  if (!semanticSettings.ready) {
    clearSemanticResults(false);
    byId("ontology-search-status").textContent = semanticSettings.message;
    setStatus("Semantic ranking disabled");
    return;
  }
  const button = byId("analyze-rule");
  button.disabled = true;
  setStatus("Ranking ontology terms…");
  byId("ontology-search-status").textContent = "Ranking ontology terms…";
  try {
    const data = await fetchJSON(SERVICES.terms, {
      method: "POST",
      body: JSON.stringify({
        business_rule: rule, ontology_terms: o.entities,
        ontology_hash: o.contentHash || "",
        entity_types: entityFilter === "all" ? [] : [entityFilter],
        embedding_model: m.embeddingModel,
        config_fingerprint: modelConfigFingerprint(m),
        inference_config: getInferenceConfig(),
        model: m.llmModel, provider: m.provider,
      }),
    }, { label: "Rank ontology terms", timeoutMs: 30000 });
    lastCandidates = data.candidates || [];
    semanticSearchActive = true;
    byId("clear-related-terms").hidden = false;
    byId("ontology-search-status").textContent =
      `${lastCandidates.length} related ${entityFilter === "all" ? "terms" : entityFilter + " terms"} · ${data.method || ""}`
      + (data.message ? ` — ${data.message}` : "");
    renderEntities();
    setStatus(`${lastCandidates.length} candidate(s) · ${data.method || ""}`);
    // Auto-select the top candidate to streamline "write rule → generate".
    if (lastCandidates.length) {
      const top = o.entities.find((e) => e.id === lastCandidates[0].entity_id);
      if (top) selectEntity(top);
    }
  } catch (e) {
    setStatus("Ranking failed");
    byId("ontology-search-status").textContent = `Ranking failed: ${e.message}`;
  } finally {
    button.disabled = false;
  }
}

/* ---------- generate ---------- */
async function generateShape() {
  const o = getOntology();
  if (!o || !selectedEntity) { setStatus("Select a target first"); return; }
  const rule = byId("business-rule").value.trim();
  const m = getModels();
  const requestId = makeRequestId();

  ruleGenerationLogId = beginExecutionRun({
    source: "Rule → Shape",
    metadata: ruleExecutionMetadata(o, m, selectedEntity.label || selectedEntity.iri, requestId),
  });
  appendExecutionEntry(ruleGenerationLogId, {
    level: "info",
    kind: "rule",
    stage: "rule",
    message: `Business rule · ${rule || "(empty rule)"}`,
  });
  appendExecutionEntry(ruleGenerationLogId, {
    level: "info",
    stage: "target",
    indent: 1,
    message: `Selected ontology target · ${selectedEntity.iri}`,
  });

  byId("generate-shape").disabled = true;
  const panel = byId("validation-panel");
  panel.className = "validation-panel";

  try {
    appendExecutionEntry(ruleGenerationLogId, {
      level: "info", stage: "configuration", indent: 1, message: "Checking model configuration",
    });
    setStatus("Checking model configuration…");
    panel.className = "validation-panel backend";
    panel.textContent = "Checking model configuration before generation…";
    const modelCheck = await validateSelectedModels(["llmModel"]);
    if (!modelCheck.ok) {
      appendExecutionEntry(ruleGenerationLogId, {
        level: "error", stage: "configuration", indent: 1,
        message: "Model configuration check failed", details: modelCheck.message,
      });
      appendExecutionEntry(ruleGenerationLogId, {
        level: "error", kind: "summary", stage: "summary",
        message: "Failed before generation · model configuration unavailable",
      });
      finishExecutionRun(ruleGenerationLogId, "failed");
      panel.className = "validation-panel backend error";
      panel.textContent = `Generation blocked by model/backend configuration:\n${modelCheck.message}`;
      setStatus("Model configuration error");
      return;
    }
    appendExecutionEntry(ruleGenerationLogId, {
      level: "pass", stage: "configuration", indent: 1, message: "Model configuration available",
    });

    setStatus("Generating SHACL shape…");
    panel.className = "validation-panel";
    panel.textContent = "Generating… (this may take a while on the first call).";
    const target = { ...selectedEntity, ontologyNote: byId("ontology-note").value };
    appendExecutionEntry(ruleGenerationLogId, {
      level: "info", stage: "generation", indent: 1,
      message: `Generating shape for ${selectedEntity.iri}`,
    });
    const data = await fetchJSON(SERVICES.build, {
      method: "POST",
      body: JSON.stringify({
        business_rule: rule, target, prefixes: o.prefixes,
        ontology_content: o.content, base_namespace: o.baseNamespace,
        shape_namespace: o.shapeNamespace,
        shape_prefix: o.shapePrefix,
        domain_context: byId("domain-context").value.trim(),
        generation_guidance: byId("generation-guidance").value.trim(),
        validation_profiles: getShapeValidationProfiles(),
        astrea_baseline: astreaBaselinePayload(),
        model: m.llmModel, provider: m.provider, temperature: m.temperature,
        inference_config: getInferenceConfig(),
      }),
    }, { label: "Generate SHACL shape", timeoutMs: 120000, requestId });
    updateExecutionRun(ruleGenerationLogId, {
      metadata: { requestId: data.request_id || requestId },
    });
    byId("shape-editor").value = data.shape || "";
    refreshHighlight("shape-editor");
    if (data.logs) {
      appendExecutionEntry(ruleGenerationLogId, {
        level: "debug", stage: "backend", indent: 1,
        message: "Backend execution details", details: data.logs,
      });
    }
    if (data.not_found) {
      appendExecutionEntry(ruleGenerationLogId, {
        level: "warn", stage: "generation", indent: 1,
        message: "The model could not justify a shape for this rule and target",
        details: data.message,
      });
      appendExecutionEntry(ruleGenerationLogId, {
        level: "done", kind: "summary", stage: "summary",
        message: `Completed · 0 shapes generated · ${data.attempts || 0} attempt(s)`,
      });
      finishExecutionRun(ruleGenerationLogId, "completed", { shapes: 0 });
      panel.className = "validation-panel";
      panel.textContent = data.message || "No shape could be justified from this rule.";
    } else if (data.valid) {
      appendExecutionEntry(ruleGenerationLogId, {
        level: "pass", stage: "syntax", indent: 1, message: "Turtle syntax is valid",
      });
      appendExecutionEntry(ruleGenerationLogId, {
        level: "pass", stage: "grounding", indent: 1,
        message: `Ontology grounding passed · ${selectedEntity.iri}`,
      });
      appendExecutionEntry(ruleGenerationLogId, {
        level: "pass", stage: "validation", indent: 1,
        message: `SHACL2SHACL validation passed · ${validationScopeLabel(data)}`,
      });
      appendExecutionEntry(ruleGenerationLogId, {
        level: "done", stage: "result", indent: 1,
        message: `Shape generated in ${data.attempts || 1} attempt(s)`,
      });
      appendExecutionEntry(ruleGenerationLogId, {
        level: "done", kind: "summary", stage: "summary",
        message: "Completed · 1 valid shape · 0 discarded",
      });
      finishExecutionRun(ruleGenerationLogId, "completed", { shapes: 1, valid: 1, invalid: 0 });
      panel.className = "validation-panel ok";
      panel.textContent = data.message || "Valid SHACL generated.";
    } else if (data.error_type === "backend") {
      logRuleGenerationFailure(data);
      panel.className = "validation-panel backend error";
      panel.textContent = `Model/backend error — generation did not produce a shape:\n${data.error || data.message}`;
    } else if (data.error_type === "profile") {
      logRuleGenerationFailure(data);
      panel.className = "validation-panel shape-error";
      panel.textContent = validationResultMessage(data);
    } else {
      logRuleGenerationFailure(data);
      panel.className = "validation-panel shape-error";
      panel.textContent = `Shape/Turtle error — the backend ran, but the generated shape is invalid:\nReturned after ${data.attempts} attempts.\n${data.error || data.message}`;
    }
    if (data.valid) setStatus("Shape generated");
    else if (data.error_type === "backend") setStatus("Backend/model error");
    else setStatus("Shape needs fixing");
  } catch (e) {
    updateExecutionRun(ruleGenerationLogId, { metadata: { requestId: e.requestId || requestId } });
    if (e.payload && e.payload.logs) {
      appendExecutionEntry(ruleGenerationLogId, {
        level: "debug", stage: "backend", indent: 1,
        message: "Backend execution details", details: e.payload.logs,
      });
    }
    appendExecutionEntry(ruleGenerationLogId, {
      level: "error", stage: "service", indent: 1,
      message: "Generation service request failed", details: e.message,
    });
    appendExecutionEntry(ruleGenerationLogId, {
      level: "error", kind: "summary", stage: "summary",
      message: "Failed · no shape generated",
    });
    finishExecutionRun(ruleGenerationLogId, "failed", { shapes: 0, invalid: 1 });
    panel.className = "validation-panel backend error";
    panel.textContent = `Generation service/backend error:\n${e.message}`;
    setStatus("Generation failed");
  } finally {
    byId("generate-shape").disabled = false;
  }
}

/* ---------- check / accept ---------- */
async function checkShape() {
  const o = getOntology();
  const shape = byId("shape-editor").value.trim();
  if (!shape) return;
  const panel = byId("validation-panel");
  const m = getModels();
  const logId = beginExecutionRun({
    source: "Rule → Shape",
    metadata: ruleExecutionMetadata(o, m, "Manual shape check"),
  });
  appendExecutionEntry(logId, {
    level: "info", stage: "validation", message: `Checking edited shape · ${activeValidationScopeLabel()}`,
  });
  panel.className = "validation-panel";
  panel.textContent = "Checking…";
  try {
    const data = await validateTurtle(shape, (o && o.prefixes) || "");
    if (data.valid) {
      appendExecutionEntry(logId, {
        level: "pass", stage: "validation", message: `Validation passed · ${validationScopeLabel(data)}`,
      });
      finishExecutionRun(logId, "completed", { valid: 1 });
      panel.className = "validation-panel ok"; panel.textContent = validationResultMessage(data);
    }
    else {
      appendExecutionEntry(logId, {
        level: "error", stage: "validation", message: "Validation failed",
        details: data.report_text || data.error || data.message,
      });
      finishExecutionRun(logId, "failed", { invalid: 1 });
      panel.className = "validation-panel shape-error";
      panel.textContent = validationResultMessage(data);
    }
  } catch (e) {
    appendExecutionEntry(logId, {
      level: "error", stage: "service", message: "Validation service request failed", details: e.message,
    });
    finishExecutionRun(logId, "failed", { invalid: 1 });
    panel.className = "validation-panel backend error";
    panel.textContent = `Validation service/backend error:\n${e.message}`;
  }
}

async function acceptCurrent() {
  const o = getOntology();
  const shape = byId("shape-editor").value.trim();
  if (!shape) { setStatus("Nothing to accept"); return; }
  const logId = beginExecutionRun({
    source: "Rule → Shape",
    metadata: ruleExecutionMetadata(o, getModels(), "Accept shape"),
  });
  appendExecutionEntry(logId, {
    level: "info", stage: "validation", message: `Revalidating before acceptance · ${activeValidationScopeLabel()}`,
  });
  try {
    const data = await validateTurtle(shape, (o && o.prefixes) || "");
    if (!data.valid) {
      appendExecutionEntry(logId, {
        level: "error", stage: "validation", message: "Acceptance blocked by active validation",
        details: data.report_text || data.error || data.message,
      });
      finishExecutionRun(logId, "failed", { accepted: 0 });
      const panel = byId("validation-panel");
      panel.className = "validation-panel shape-error";
      panel.textContent = `Cannot accept invalid generated shape:\n${validationResultMessage(data)}`;
      return;
    }
    acceptShape(selectedEntity ? selectedEntity.iri : "(shape)", shape);
    appendExecutionEntry(logId, {
      level: "done", kind: "summary", stage: "accept", message: "Shape revalidated and accepted",
    });
    finishExecutionRun(logId, "completed", { accepted: 1 });
    renderAccepted(byId("accepted-list"), byId("coverage-tag"));
    setStatus("Shape accepted");
  } catch (e) {
    appendExecutionEntry(logId, {
      level: "error", stage: "service", message: "Acceptance validation request failed", details: e.message,
    });
    finishExecutionRun(logId, "failed", { accepted: 0 });
    setStatus("Validation failed");
  }
}

function ruleExecutionMetadata(ontology, models, artifact, requestId = "") {
  return {
    artifact,
    ontology: ontology && ontology.filename,
    ruleCount: 1,
    provider: models && models.provider,
    models: models ? [models.llmModel] : [],
    validation: activeValidationScopeLabel(),
    astreaBaseline: getAstreaBaseline() && getAstreaBaseline().name,
    astreaMergeMode: getAstreaMergeMode(),
    target: selectedEntity && selectedEntity.iri,
    requestId,
  };
}

function logRuleGenerationFailure(data = {}) {
  const type = data.error_type || "generation";
  const labels = {
    parse: "Generated Turtle remained invalid after retries",
    grounding: "Generated shape contains IRIs outside the uploaded ontology",
    profile: "Generated shape failed active SHACL2SHACL validation",
    backend: "Model/backend generation failed",
  };
  appendExecutionEntry(ruleGenerationLogId, {
    level: "error", stage: type, indent: 1,
    message: labels[type] || "Shape generation failed",
    details: data.error || data.message,
  });
  appendExecutionEntry(ruleGenerationLogId, {
    level: "error", kind: "summary", stage: "summary",
    message: `Failed · 0 valid shapes · 1 discarded · ${data.attempts || 0} attempt(s)`,
  });
  finishExecutionRun(ruleGenerationLogId, "failed", { shapes: 0, valid: 0, invalid: 1 });
}
