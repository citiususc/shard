/* guide.js — Workflow 2: Business Rules template → all SHACL shapes (streamed). */

let guideFile = null;       // {filename, content, format, ruleCount}
let queue = [];             // [{index, ruleNumber, property, status, shape, error, attempts, businessRule, acceptedId}]
let activeIndex = null;     // currently reviewed queue item index
let nodeShapes = "";        // aggregated sh:NodeShape block from the "done" event
let generationController = null;
let generationRunning = false;
let generationCancelled = false;
let guideGenerationLogId = null;
let guideRequestId = "";

document.addEventListener("DOMContentLoaded", () => {
  wireReset("reset-demo");
  wireSessionControls();
  wireModelControls();
  wireShapeValidationProfileControls();
  wireAstreaBaselineControls();
  attachTurtleHighlighter("shape-editor", "shape-editor-hl");
  attachTurtleHighlighter("prefixes-editor", "prefixes-editor-hl");
  wireOntologyControls(() => {});
  renderAccepted(byId("accepted-list"), byId("coverage-tag"));
  wireExport("export-shapes", () => nodeShapes);

  byId("guide-file").addEventListener("change", onGuideSelected);
  byId("generate-guide").addEventListener("click", generateAll);
  byId("cancel-generation").addEventListener("click", cancelGeneration);
  byId("accept-all-shapes").addEventListener("click", acceptAllGenerated);
  window.addEventListener("accepted-shapes-changed", syncQueueAcceptedState);
  byId("validate-shape").addEventListener("click", checkShape);
  byId("accept-shape").addEventListener("click", acceptCurrent);
  byId("copy-shape").addEventListener("click", async () => {
    const ok = await copyToClipboard(byId("shape-editor").value);
    setStatus(ok ? "Copied" : "Copy failed");
  });
});

/* ---------- guide upload ---------- */
async function onGuideSelected(ev) {
  const file = ev.target.files[0];
  if (!file) return;
  const content = await file.text();
  try {
    const parsed = validateBusinessRulesTemplate(file.name, content);
    guideFile = {
      filename: file.name,
      content,
      format: parsed.format,
      ruleCount: parsed.ruleCount,
    };
    byId("guide-summary").textContent =
      `${file.name} loaded · valid ${parsed.format.toUpperCase()} Business Rules template · ${parsed.ruleCount} rule(s).`;
    byId("generate-guide").disabled = false;
    setStatus("Business rules template loaded");
  } catch (e) {
    guideFile = null;
    byId("guide-summary").textContent = `Invalid template: ${e.message}`;
    byId("generate-guide").disabled = true;
    setStatus("Invalid business rules template");
  } finally {
    ev.target.value = "";
  }
}

function validateBusinessRulesTemplate(filename, content) {
  const lower = String(filename || "").toLowerCase();
  if (lower.endsWith(".html") || lower.endsWith(".htm")) {
    return validateBusinessRulesHtml(content);
  }
  if (lower.endsWith(".md") || lower.endsWith(".markdown")) {
    return validateBusinessRulesMarkdown(content);
  }
  throw new Error("Use the provided Business Rules template in .html or .md format.");
}

function validateBusinessRulesHtml(content) {
  const doc = new DOMParser().parseFromString(content || "", "text/html");
  const sections = Array.from(doc.querySelectorAll("section.rule"));
  if (!sections.length) throw new Error("HTML template must contain at least one section.rule.");
  let ruleCount = 0;
  for (const section of sections) {
    if (!section.querySelector(".number") || !section.querySelector(".title") || !section.querySelector(".business-rule")) {
      throw new Error("Each HTML rule must contain .number, .title and .business-rule.");
    }
    if (section.querySelector(".business-rule").textContent.trim()) ruleCount += 1;
  }
  if (!ruleCount) throw new Error("The template is valid, but all business rule fields are empty.");
  return { format: "html", ruleCount };
}

function validateBusinessRulesMarkdown(content) {
  const text = String(content || "").replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  const headings = [...text.matchAll(/^\s*##\s+Rule\s*$/gim)];
  if (!headings.length) throw new Error("Markdown template must contain at least one “## Rule” section.");
  let ruleCount = 0;
  for (let i = 0; i < headings.length; i++) {
    const start = headings[i].index + headings[i][0].length;
    const end = i + 1 < headings.length ? headings[i + 1].index : text.length;
    const block = text.slice(start, end);
    if (!/^\s*-\s*Number\s*:/im.test(block) || !/^\s*-\s*Title\s*:/im.test(block) || !/^\s*###\s+Business rule\s*$/im.test(block)) {
      throw new Error("Each Markdown rule must contain “- Number:”, “- Title:” and “### Business rule”.");
    }
    const businessRule = block.split(/^\s*###\s+Business rule\s*$/im)[1] || "";
    const clean = businessRule.split(/^\s*---\s*$/m)[0].trim();
    if (clean) ruleCount += 1;
  }
  if (!ruleCount) throw new Error("The template is valid, but all business rule fields are empty.");
  return { format: "markdown", ruleCount };
}

/* ---------- generate (streamed) ---------- */
async function generateAll() {
  const o = getOntology();
  if (!o) { setStatus("Load an ontology first"); return; }
  if (!guideFile) { setStatus("Upload a valid Business Rules template first"); return; }

  const m = getModels();
  const panel = byId("validation-panel");
  guideRequestId = makeRequestId();
  guideGenerationLogId = beginExecutionRun({
    source: "Guide → Shapes",
    metadata: guideExecutionMetadata(o, m, guideFile.filename, guideRequestId),
  });
  byId("generate-guide").disabled = true;
  appendExecutionEntry(guideGenerationLogId, {
    level: "info", stage: "configuration", message: "Guide generation requested in rule-first mode",
  });

  setStatus("Checking model configuration…");
  if (panel) {
    panel.className = "validation-panel backend";
    panel.textContent = "Checking model configuration before guide generation…";
  }
  let modelCheck;
  try {
    modelCheck = await validateSelectedModels([
      "llmModel", "embeddingModel",
    ]);
  } catch (e) {
    modelCheck = {
      ok: false,
      message: `Could not validate model configuration: ${e.message}`,
    };
  }
  if (!modelCheck.ok) {
    appendExecutionEntry(guideGenerationLogId, {
      level: "error", stage: "configuration", message: "Model configuration check failed",
      details: modelCheck.message,
    });
    appendExecutionEntry(guideGenerationLogId, {
      level: "error", kind: "summary", stage: "summary",
      message: "Failed before generation · model configuration unavailable",
    });
    finishExecutionRun(guideGenerationLogId, "failed");
    if (panel) {
      panel.className = "validation-panel backend error";
      panel.textContent = `Generation blocked by model/backend configuration:\n${modelCheck.message}`;
    }
    setProgress(null, null, modelCheck.message);
    setStatus("Model configuration error");
    byId("generate-guide").disabled = false;
    setGenerationControls(false);
    return;
  }
  appendExecutionEntry(guideGenerationLogId, {
    level: "pass", stage: "configuration", message: "Generation and embedding models are available",
  });

  queue = []; activeIndex = null; nodeShapes = "";
  renderQueue();
  renderCurrentBusinessRule(null);
  byId("shape-editor").value = "";
  refreshHighlight("shape-editor");
  byId("editor-title").textContent = "Editable shape";
  setProgress(0, 0, "Starting…");
  setStatus("Preprocessing business rules…");
  generationController = new AbortController();
  setGenerationControls(true);

  const payload = {
    iteration_mode: "rule",
    ontology_content: o.content, base_namespace: o.baseNamespace,
    shape_namespace: o.shapeNamespace, shape_prefix: o.shapePrefix, prefixes: o.prefixes,
    guide_content: guideFile.content, guide_filename: guideFile.filename, guide_format: guideFile.format,
    domain_context: byId("domain-context").value.trim(),
    generation_guidance: byId("generation-guidance").value.trim(),
    validation_profiles: getShapeValidationProfiles(),
    astrea_baseline: astreaBaselinePayload(),
    llm_model: m.llmModel, text_model: m.textModel, vision_model: m.visionModel,
    embedding_model: m.embeddingModel, temperature: m.temperature, provider: m.provider,
    inference_config: getInferenceConfig(),
  };

  try {
    const res = await fetchStream(SERVICES.guide, {
      method: "POST",
      body: JSON.stringify(payload),
    }, {
      label: "Generate guide shapes",
      timeoutMs: 30000,
      controller: generationController,
      requestId: guideRequestId,
    });
    await consumeStream(res.body);
  } catch (e) {
    if (generationCancelled || e.cancelled) {
      appendExecutionEntry(guideGenerationLogId, {
        level: "warn", kind: "summary", stage: "summary",
        message: `Cancelled · ${queue.filter((item) => item.status === "valid").length} valid result(s) received before cancellation`,
      });
      finishExecutionRun(guideGenerationLogId, "cancelled");
      setProgress(null, null, "Generation cancelled.");
      if (panel) {
        panel.className = "validation-panel";
        panel.textContent = "Guide generation cancelled. Generated shapes already received remain available for review.";
      }
      setStatus("Generation cancelled");
    } else {
      updateExecutionRun(guideGenerationLogId, {
        metadata: { requestId: e.requestId || guideRequestId },
      });
      appendExecutionEntry(guideGenerationLogId, {
        level: "error", stage: "service", message: "Guide generation request failed", details: e.message,
      });
      appendExecutionEntry(guideGenerationLogId, {
        level: "error", kind: "summary", stage: "summary",
        message: "Failed · guide generation did not complete",
      });
      finishExecutionRun(guideGenerationLogId, "failed");
      setProgress(0, 0, `Error: ${e.message}`);
      if (panel) {
        panel.className = "validation-panel backend error";
        panel.textContent = `Guide generation backend/service error:\n${e.message}`;
      }
      setStatus("Generation failed");
    }
  } finally {
    byId("generate-guide").disabled = false;
    setGenerationControls(false);
  }
}

function setGenerationControls(running) {
  generationRunning = running;
  if (!running) {
    generationCancelled = false;
    generationController = null;
  }
  const cancelBtn = byId("cancel-generation");
  if (cancelBtn) cancelBtn.disabled = !running || generationCancelled;
}

function cancelGeneration() {
  if (!generationRunning || generationCancelled) return;
  generationCancelled = true;
  appendExecutionEntry(guideGenerationLogId, {
    level: "warn", stage: "cancel", message: "Generation cancellation requested by the user",
  });
  if (generationController) generationController.abort();
  setProgress(null, null, "Cancelling generation…");
  setStatus("Cancelling generation");
  setGenerationControls(true);
}

async function consumeStream(body) {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    if (generationCancelled) break;
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let sep;
    while ((sep = buffer.indexOf("\n\n")) !== -1) {
      if (generationCancelled) break;
      const chunk = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      const line = chunk.split("\n").find((l) => l.startsWith("data: "));
      if (!line) continue;
      try { handleEvent(JSON.parse(line.slice(6))); } catch { /* ignore */ }
    }
    if (generationCancelled) break;
  }
}

function handleEvent(ev) {
  logGuideEvent(ev);
  if (ev.type === "status") {
    handleStatusEvent(ev);
  } else if (ev.type === "start") {
    setProgress(0, ev.total, `Generating shapes from ${ev.total} business rule(s)…`, "business rules processed");
    if (ev.prefixes) {  // sync the (server-built) prefixes into the editable panel
      const o = getOntology();
      if (o) {
        o.prefixes = ev.prefixes;
        o.shapeNamespace = ev.shape_namespace || o.shapeNamespace;
        o.shapePrefix = ev.shape_prefix || o.shapePrefix;
        setOntology(o);
      }
      byId("prefixes-editor").value = ev.prefixes;
      refreshHighlight("prefixes-editor");
    }
    if (ev.astrea_evidence_active) {
      setStatus(`Generating by rule · Astrea evidence: ${ev.astrea_baseline_name || "loaded"}`);
    }
    else setStatus("Generating by rule…");
  } else if (ev.type === "shape") {
    const item = queueItemFromEvent(ev);
    queue.push(item);
    setProgress(ev.index, ev.total, shapeProgressMessage(item), "business rules processed");
    renderQueue();
    if (activeIndex === null || (queue[activeIndex] && queue[activeIndex].status === "skipped" && item.status !== "skipped")) {
      selectQueueItem(queue.length - 1);
    }
  } else if (ev.type === "done") {
    nodeShapes = ev.shape_document || ev.node_shapes || "";
    setProgress(ev.total, ev.total, `Done — ${ev.valid} valid, ${ev.invalid} discarded, ${ev.skipped} unresolved.`, "business rules processed");
    setStatus("Generation complete");
  } else if (ev.type === "error") {
    setProgress(null, null, `Error: ${ev.message}`);
    const panel = byId("validation-panel");
    if (panel) {
      panel.className = "validation-panel backend error";
      panel.textContent = `Guide generation backend/service error:\n${ev.message}`;
    }
    setStatus("Error");
  }
}

function logGuideEvent(ev) {
  if (!ev || !ev.type) return;
  if (ev.request_id && ev.request_id !== guideRequestId) {
    guideRequestId = ev.request_id;
    updateExecutionRun(guideGenerationLogId, { metadata: { requestId: guideRequestId } });
  }
  if (ev.type === "status") {
    const stage = ev.stage || "pipeline";
    if (stage === "rule") {
      appendExecutionEntry(guideGenerationLogId, {
        level: "info", kind: "rule", stage,
        message: `${ruleLabel(ev)} · ${ev.title || "Untitled business rule"}`,
      });
      appendExecutionEntry(guideGenerationLogId, {
        level: "info", stage, indent: 1, message: "Resolving ontology target(s)",
      });
      return;
    }
    if (stage === "resolution") {
      const targets = formatTargets(ev.targets);
      const confidence = ev.confidence != null && Number.isFinite(Number(ev.confidence))
        ? ` · confidence ${Number(ev.confidence).toFixed(2)}` : "";
      const unresolved = ev.resolved_by === "none" || !targets;
      appendExecutionEntry(guideGenerationLogId, {
        level: unresolved ? "warn" : "info", stage, indent: 1,
        message: unresolved
          ? `Resolution · none${confidence} · no ontology target`
          : `Resolution · ${resolvedByText(ev.resolved_by)}${confidence} · ${targets}`,
      });
      return;
    }
    if (stage === "generation") {
      const step = ev.target_total ? ` (${ev.target_index || 1}/${ev.target_total})` : "";
      appendExecutionEntry(guideGenerationLogId, {
        level: "info", stage, indent: 1,
        message: `Generating target ${ev.target || ev.property || "unknown"}${step}`,
      });
      return;
    }
    appendExecutionEntry(guideGenerationLogId, {
      level: "info", stage, message: ev.message || stage,
    });
    return;
  }
  if (ev.type === "start") {
    updateExecutionRun(guideGenerationLogId, {
      metadata: { ruleCount: ev.total || 0, requestId: ev.request_id || guideRequestId },
    });
    appendExecutionEntry(guideGenerationLogId, {
      level: "info", stage: "start",
      message: `Starting rule-first generation for ${ev.total || 0} business rule(s)`,
    });
    return;
  }
  if (ev.type === "shape") {
    const label = ruleLabel(ev);
    const target = ev.target || ev.property || "unresolved target";
    if (ev.status === "valid") {
      appendExecutionEntry(guideGenerationLogId, {
        level: "pass", stage: "syntax", indent: 1, message: `${target} · Turtle syntax valid`,
      });
      appendExecutionEntry(guideGenerationLogId, {
        level: "pass", stage: "grounding", indent: 1, message: `${target} · ontology grounding passed`,
      });
      appendExecutionEntry(guideGenerationLogId, {
        level: "pass", stage: "validation", indent: 1,
        message: `${target} · SHACL2SHACL passed · ${validationScopeLabel(ev)}`,
      });
      appendExecutionEntry(guideGenerationLogId, {
        level: "done", stage: "result", indent: 1,
        message: `${target} · shape generated in ${ev.attempts || 1} attempt(s)`,
      });
    } else if (ev.status === "skipped") {
      appendExecutionEntry(guideGenerationLogId, {
        level: "warn", stage: "result", indent: 1,
        message: `${label} · ${target} · skipped (${ev.resolved_by === "none" ? "unresolved rule" : "missing ontology target"})`,
        details: ev.error,
      });
    } else {
      appendExecutionEntry(guideGenerationLogId, {
        level: "error", stage: ev.error_type || "generation", indent: 1,
        message: `${target} · discarded (${errorTypeText(ev.error_type)}) after ${ev.attempts || 0} attempt(s)`,
        details: ev.error,
      });
    }
    return;
  }
  if (ev.type === "done") {
    appendExecutionEntry(guideGenerationLogId, {
      level: "pass", stage: "consolidation", message: "Generated shapes consolidated by NodeShape / target class",
    });
    appendExecutionEntry(guideGenerationLogId, {
      level: "done", kind: "summary", stage: "summary",
      message: `Completed · ${ev.total || 0} rules · ${ev.valid || 0} valid · ${ev.invalid || 0} discarded · ${ev.skipped || 0} unresolved`,
    });
    finishExecutionRun(guideGenerationLogId, "completed", {
      rules: ev.total || 0,
      valid: ev.valid || 0,
      invalid: ev.invalid || 0,
      unresolved: ev.skipped || 0,
    });
    return;
  }
  if (ev.type === "error") {
    appendExecutionEntry(guideGenerationLogId, {
      level: "error", stage: "service", message: "Guide generation failed", details: ev.message,
    });
    appendExecutionEntry(guideGenerationLogId, {
      level: "error", kind: "summary", stage: "summary",
      message: "Failed · guide generation did not complete",
    });
    finishExecutionRun(guideGenerationLogId, "failed");
  }
}

function handleStatusEvent(ev) {
  const stage = ev.stage || "";
  if (stage === "rule") {
    const label = ruleLabel(ev);
    setProgress(ev.current, ev.total, `${label}: resolving ontology target(s)…`, "business rules processed");
    setStatus(`Resolving ${label}`);
    return;
  }
  if (stage === "resolution") {
    const label = ruleLabel(ev);
    const signal = resolvedByText(ev.resolved_by);
    const targets = formatTargets(ev.targets);
    const message = ev.resolved_by === "none" || !targets
      ? `${label}: no ontology target resolved.`
      : `${label}: resolved via ${signal} → ${targets}`;
    setProgress(ev.current, ev.total, message, "business rules processed");
    setStatus(`${label}: ${ev.resolved_by || "resolution"}`);
    return;
  }
  if (stage === "generation") {
    const label = ruleLabel(ev);
    const target = ev.target || ev.property || "target";
    const step = ev.target_total ? ` (${ev.target_index || 1}/${ev.target_total})` : "";
    setProgress(ev.current, ev.total, `${label}: generating ${target}${step}`, "business rules processed");
    setStatus(`Generating ${label}`);
    return;
  }

  const pct = ev.total ? Math.round((ev.current / ev.total) * 100) : null;
  const message = ev.message + (pct !== null ? ` (${pct}%)` : "");
  setProgress(null, null, message);
  setStatus(ev.message);
}

function setProgress(current, total, message, unit = "business rules processed") {
  if (current !== null && total !== null) {
    byId("progress-tag").textContent = `${current} / ${total} ${unit}`;
    const pct = total ? Math.round((current / total) * 100) : 0;
    byId("progress-bar").style.width = pct + "%";
  }
  if (message) byId("progress-status").textContent = message;
}

function ruleLabel(source) {
  const number = source && (source.ruleNumber || source.rule_number);
  if (number) return String(number);
  if (source && source.index) return `Rule ${source.index}`;
  return "Rule";
}

function resolvedByText(value) {
  const text = String(value || "").trim();
  if (!text) return "unknown signal";
  if (text === "llm") return "LLM fallback";
  return text;
}

function formatTargets(targets) {
  if (!Array.isArray(targets) || !targets.length) return "";
  return targets.join(", ");
}

function targetLabel(value) {
  if (!value) return "unresolved";
  return String(value).split(/[\/#]/).pop();
}

function errorTypeText(value) {
  const type = String(value || "").trim();
  if (type === "parse") return "invalid Turtle after retry";
  if (type === "grounding") return "IRI outside the uploaded ontology";
  if (type === "profile") return "shape validation profile";
  if (type === "backend") return "backend/model error";
  if (type === "none") return "";
  return type || "not generated";
}

function queueItemFromEvent(ev) {
  const target = ev.target || ev.property || "";
  const ruleNumber = ev.rule_number || (ev.index ? `Rule ${ev.index}` : "Rule");
  const status = ev.status || "skipped";
  const unresolved = status === "skipped" && (!target || ev.resolved_by === "none");
  return {
    index: ev.index,
    ruleNumber,
    ruleTitle: ev.title || "",
    property: ev.property || target || `${ruleNumber} unresolved`,
    target,
    targetType: ev.target_type || "",
    targetIndex: ev.target_index,
    targetTotal: ev.target_total,
    resolvedBy: ev.resolved_by || "",
    status,
    shape: ev.shape || "",
    error: ev.error,
    errorType: ev.error_type || "",
    attempts: ev.attempts || 0,
    syntaxValid: ev.syntax_valid,
    profileValid: ev.profile_valid,
    profileCount: ev.profile_count,
    profileNames: ev.profile_names || [],
    genericProfileActive: ev.generic_profile_active,
    genericProfileName: ev.generic_profile_name,
    domainProfileCount: ev.domain_profile_count,
    domainProfileNames: ev.domain_profile_names || [],
    validationLevel: ev.validation_level,
    validationLabel: validationScopeLabel(ev),
    businessRule: ev.business_rule || "",
    unresolved,
    displayName: `${ruleNumber} · ${target || "unresolved"}`,
    acceptedId: null,
  };
}

function shapeProgressMessage(item) {
  const label = ruleLabel(item);
  if (item.unresolved) return `${label}: unresolved — no target selected by the resolver.`;
  const target = item.target || item.property;
  if (item.status === "valid") return `${label}: ${target} generated.`;
  if (item.status === "invalid") {
    return `${label}: ${target} discarded — ${errorTypeText(item.errorType)}.`;
  }
  return `${label}: ${target || "shape"} skipped.`;
}

function validationShortLabel(item) {
  if (!item || item.status !== "valid") return "";
  const domainCount = Number(item.domainProfileCount || 0);
  return domainCount ? "generic + profile" : "generic";
}

function guideExecutionMetadata(ontology, models, artifact, requestId = "") {
  return {
    artifact,
    ontology: ontology && ontology.filename,
    ruleCount: guideFile && guideFile.ruleCount,
    provider: models && models.provider,
    models: models ? [models.llmModel, models.embeddingModel] : [],
    validation: activeValidationScopeLabel(),
    astreaBaseline: getAstreaBaseline() && getAstreaBaseline().name,
    astreaMergeMode: getAstreaMergeMode(),
    requestId,
  };
}

function beginGuideReviewRun(action, item = null) {
  return beginExecutionRun({
    source: "Guide → Shapes",
    metadata: guideExecutionMetadata(
      getOntology(),
      getModels(),
      item && item.displayName ? `${action} · ${item.displayName}` : action,
    ),
  });
}

/* ---------- queue ---------- */
function renderQueue() {
  const list = byId("queue-list");
  reconcileQueueAcceptedState();
  updateAcceptAllButton();
  byId("queue-count").textContent = `${queue.length} result${queue.length === 1 ? "" : "s"}`;
  list.innerHTML = "";
  queue.forEach((item, i) => {
    const card = document.createElement("div");
    const accepted = item.acceptedId ? " accepted" : "";
    card.className = `queue-card ${item.status}${activeIndex === i ? " active" : ""}${accepted}`;
    const badge = item.acceptedId ? "accepted" : item.status;
    const short = item.displayName || `${ruleLabel(item)} · ${targetLabel(item.property)}`;
    const signal = item.unresolved
      ? "unresolved"
      : item.resolvedBy ? `via ${resolvedByText(item.resolvedBy)}` : "manual review";
    const targetStep = item.targetTotal ? `${item.targetIndex || 1}/${item.targetTotal}` : "";
    const reason = item.status === "invalid"
      ? errorTypeText(item.errorType)
      : item.unresolved ? "unresolved" : signal;
    const meta = [targetStep, signal, validationShortLabel(item), reason !== signal ? reason : ""].filter(Boolean).join(" · ");
    card.innerHTML =
      `<strong>${esc(short)}</strong>` +
      `<div class="qmeta"><span class="qbadge ${badge}">${badge}</span>` +
      `<span>${esc(meta)} · ${item.attempts} attempt(s)</span></div>` +
      `<button class="secondary-button queue-edit" type="button">Edit</button>`;
    card.querySelector(".queue-edit").addEventListener("click", () => selectQueueItem(i));
    list.appendChild(card);
  });
}

function reconcileQueueAcceptedState() {
  const acceptedIds = new Set(getAccepted().map((shape) => shape.id));
  let changed = false;
  queue.forEach((item) => {
    if (item.acceptedId && !acceptedIds.has(item.acceptedId)) {
      item.acceptedId = null;
      changed = true;
    }
  });
  return changed;
}

function syncQueueAcceptedState() {
  if (reconcileQueueAcceptedState()) renderQueue();
  updateAcceptAllButton();
}

function updateAcceptAllButton() {
  const btn = byId("accept-all-shapes");
  if (!btn) return;
  const validPending = queue.filter((item) => item.status === "valid" && item.shape && !item.acceptedId).length;
  btn.disabled = validPending === 0;
  btn.textContent = validPending ? `Accept all (${validPending})` : "Accept all";
}

function selectQueueItem(i) {
  activeIndex = i;
  const item = queue[i];
  byId("shape-editor").value = item.shape || "";
  refreshHighlight("shape-editor");
  byId("editor-title").textContent = item.displayName || targetLabel(item.property) || "Editable shape";
  renderCurrentBusinessRule(item);
  const panel = byId("validation-panel");
  if (item.status === "valid") {
    panel.className = "validation-panel ok";
    panel.textContent = `Valid SHACL generated for ${item.displayName} (${resolvedByText(item.resolvedBy)}).\nValidation: ${item.validationLabel || activeValidationScopeLabel()}.\nEdit if needed, then accept.`;
  } else if (item.status === "invalid") {
    panel.className = "validation-panel shape-error";
    panel.textContent = `Shape discarded for ${item.displayName}.\nReason: ${errorTypeText(item.errorType)} after ${item.attempts} attempt(s).\n\n${item.error || ""}`;
  } else if (item.unresolved) {
    panel.className = "validation-panel";
    panel.textContent = `Business rule not resolved to an ontology target.\nResolver signal: ${item.resolvedBy || "none"}.\nReview the rule or ontology labels before generating manually.`;
  } else {
    panel.className = "validation-panel";
    panel.textContent = `No generated shape for ${item.displayName}.`;
  }
  renderQueue();
}

function renderCurrentBusinessRule(item) {
  const box = byId("current-business-rule");
  const tag = byId("current-rule-tag");
  if (!box || !tag) return;
  if (!item) {
    box.value = "";
    tag.textContent = "—";
    return;
  }
  const header = [
    item.ruleNumber ? `Number: ${item.ruleNumber}` : "",
    item.ruleTitle ? `Title: ${item.ruleTitle}` : "",
    item.resolvedBy ? `Resolved by: ${resolvedByText(item.resolvedBy)}` : "",
    item.target ? `Target: ${item.target}` : "",
  ].filter(Boolean).join("\n");
  const body = item.businessRule || "No specific business rule context was returned for this generated shape.";
  box.value = header ? `${header}\n\n${body}` : body;
  tag.textContent = item.ruleNumber || `#${item.index}`;
}

/* ---------- check / accept ---------- */
async function checkShape() {
  const o = getOntology();
  const shape = byId("shape-editor").value.trim();
  if (!shape) return;
  const item = activeIndex === null ? null : queue[activeIndex];
  const logId = beginGuideReviewRun("Manual shape check", item);
  appendExecutionEntry(logId, {
    level: "info", stage: "validation", message: `Checking edited shape · ${activeValidationScopeLabel()}`,
  });
  const panel = byId("validation-panel");
  panel.className = "validation-panel"; panel.textContent = "Checking…";
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
    panel.className = "validation-panel backend error"; panel.textContent = `Validation service/backend error:\n${e.message}`;
  }
}

async function acceptCurrent() {
  if (activeIndex === null) { setStatus("Select a shape first"); return; }
  const o = getOntology();
  const item = queue[activeIndex];
  const shape = byId("shape-editor").value.trim();
  if (!shape) { setStatus("Nothing to accept"); return; }
  const logId = beginGuideReviewRun("Accept shape", item);
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
    item.shape = shape;
    if (item.acceptedId) removeAccepted(item.acceptedId);
    item.validationLabel = validationScopeLabel(data);
    item.acceptedId = acceptShape(item.displayName || item.property, shape);
    appendExecutionEntry(logId, {
      level: "done", kind: "summary", stage: "accept", message: `${item.displayName} · revalidated and accepted`,
    });
    finishExecutionRun(logId, "completed", { accepted: 1 });
    renderAccepted(byId("accepted-list"), byId("coverage-tag"));
    renderQueue();
    setStatus("Shape accepted");
  } catch (e) {
    appendExecutionEntry(logId, {
      level: "error", stage: "service", message: "Acceptance validation request failed", details: e.message,
    });
    finishExecutionRun(logId, "failed", { accepted: 0 });
    setStatus("Validation failed");
  }
}

async function acceptAllGenerated() {
  const validItems = queue.filter((item) => item.status === "valid" && item.shape);
  if (!validItems.length) {
    setStatus("No valid generated shapes to accept");
    return;
  }

  let acceptedCount = 0;
  let failedCount = 0;
  let firstFailedIndex = null;
  const o = getOntology();
  const logId = beginGuideReviewRun(`Accept all · ${validItems.length} candidate shape(s)`);
  appendExecutionEntry(logId, {
    level: "info", stage: "validation", message: `Revalidating ${validItems.length} shape(s) · ${activeValidationScopeLabel()}`,
  });
  setStatus("Revalidating generated shapes…");
  for (const item of validItems) {
    try {
      const data = await validateTurtle(item.shape, (o && o.prefixes) || "");
      item.validationLabel = validationScopeLabel(data);
      if (!data.valid) {
        appendExecutionEntry(logId, {
          level: "error", stage: "validation", indent: 1,
          message: `${item.displayName} · acceptance blocked`,
          details: data.report_text || data.error || data.message,
        });
        item.status = "invalid";
        item.error = data.report_text || data.error || data.message || "Validation failed.";
        item.errorType = data.error_type || (data.syntax_valid === false ? "parse" : "profile");
        if (item.acceptedId) {
          removeAccepted(item.acceptedId);
          item.acceptedId = null;
        }
        failedCount += 1;
        if (firstFailedIndex === null) firstFailedIndex = queue.indexOf(item);
        continue;
      }
      if (item.acceptedId) removeAccepted(item.acceptedId);
      item.acceptedId = acceptShape(item.displayName || item.property, item.shape);
      appendExecutionEntry(logId, {
        level: "pass", stage: "accept", indent: 1, message: `${item.displayName} · accepted`,
      });
      acceptedCount += 1;
    } catch (e) {
      appendExecutionEntry(logId, {
        level: "error", stage: "service", indent: 1,
        message: `${item.displayName} · validation request failed`, details: e.message,
      });
      item.status = "invalid";
      item.error = e.message;
      item.errorType = "backend";
      failedCount += 1;
      if (firstFailedIndex === null) firstFailedIndex = queue.indexOf(item);
    }
  }
  renderAccepted(byId("accepted-list"), byId("coverage-tag"));
  renderQueue();
  appendExecutionEntry(logId, {
    level: failedCount ? "warn" : "done", kind: "summary", stage: "summary",
    message: `Accept all completed · ${acceptedCount} accepted · ${failedCount} need review`,
  });
  finishExecutionRun(logId, "completed", { accepted: acceptedCount, invalid: failedCount });
  if (firstFailedIndex !== null) selectQueueItem(firstFailedIndex);
  const skipped = queue.length - acceptedCount;
  if (failedCount) {
    setStatus(`Accepted ${acceptedCount}; ${failedCount} failed active validation and need review`);
  } else {
    setStatus(skipped
      ? `Accepted ${acceptedCount} valid shape(s); ${skipped} left for review`
      : `Accepted ${acceptedCount} shape(s)`);
  }
}
