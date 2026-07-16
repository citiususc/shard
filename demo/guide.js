/* guide.js — Workflow 2: Business Rules template → all SHACL shapes (streamed). */

let guideFile = null;       // {filename, content, format, ruleCount}
let queue = [];             // [{index, property, status, shape, error, attempts, businessRule, acceptedId}]
let activeIndex = null;     // currently reviewed queue item index
let nodeShapes = "";        // aggregated sh:NodeShape block from the "done" event
let generationController = null;
let generationRunning = false;
let generationCancelled = false;

document.addEventListener("DOMContentLoaded", () => {
  wireReset("reset-demo");
  wireSessionControls();
  wireModelControls();
  wireShapeValidationProfileControls();
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
  byId("generate-guide").disabled = true;

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
    ontology_content: o.content, base_namespace: o.baseNamespace, prefixes: o.prefixes,
    guide_content: guideFile.content, guide_filename: guideFile.filename, guide_format: guideFile.format,
    domain_context: byId("domain-context").value.trim(),
    generation_guidance: byId("generation-guidance").value.trim(),
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
    });
    await consumeStream(res.body);
  } catch (e) {
    if (generationCancelled || e.cancelled) {
      setProgress(null, null, "Generation cancelled.");
      if (panel) {
        panel.className = "validation-panel";
        panel.textContent = "Guide generation cancelled. Generated shapes already received remain available for review.";
      }
      setStatus("Generation cancelled");
    } else {
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
  if (ev.type === "status") {
    const pct = ev.total ? Math.round((ev.current / ev.total) * 100) : null;
    setProgress(null, null, ev.message + (pct !== null ? ` (${pct}%)` : ""));
    setStatus(ev.message);
  } else if (ev.type === "start") {
    setProgress(0, ev.total, `Generating ${ev.total} shape(s)…`);
    if (ev.prefixes) {  // sync the (server-built) prefixes into the editable panel
      const o = getOntology(); if (o) { o.prefixes = ev.prefixes; setOntology(o); }
      byId("prefixes-editor").value = ev.prefixes;
      refreshHighlight("prefixes-editor");
    }
    setStatus("Generating…");
  } else if (ev.type === "shape") {
    queue.push({
      index: ev.index, property: ev.property, status: ev.status,
      shape: ev.shape || "", error: ev.error, attempts: ev.attempts,
      businessRule: ev.business_rule || "", acceptedId: null,
    });
    setProgress(ev.index, ev.total, `${ev.index} / ${ev.total} SHACL shapes generated`);
    renderQueue();
    if (activeIndex === null && ev.status !== "skipped") selectQueueItem(queue.length - 1);
  } else if (ev.type === "done") {
    nodeShapes = ev.node_shapes || "";
    setProgress(ev.total, ev.total, `Done — ${ev.valid} valid, ${ev.invalid} invalid, ${ev.skipped} skipped.`);
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

function setProgress(current, total, message) {
  if (current !== null && total !== null) {
    byId("progress-tag").textContent = `${current} / ${total} SHACL shapes generated`;
    const pct = total ? Math.round((current / total) * 100) : 0;
    byId("progress-bar").style.width = pct + "%";
  }
  if (message) byId("progress-status").textContent = message;
}

/* ---------- queue ---------- */
function renderQueue() {
  const list = byId("queue-list");
  reconcileQueueAcceptedState();
  updateAcceptAllButton();
  byId("queue-count").textContent = `${queue.length} shapes`;
  list.innerHTML = "";
  queue.forEach((item, i) => {
    const card = document.createElement("div");
    const accepted = item.acceptedId ? " accepted" : "";
    card.className = `queue-card ${item.status}${activeIndex === i ? " active" : ""}${accepted}`;
    const badge = item.acceptedId ? "accepted" : item.status;
    const short = String(item.property).split(/[\/#]/).pop();
    card.innerHTML =
      `<strong>${esc(short)}</strong>` +
      `<div class="qmeta"><span class="qbadge ${badge}">${badge}</span>` +
      `<span>#${item.index} · ${item.attempts} attempt(s)</span></div>` +
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
  byId("editor-title").textContent = String(item.property).split(/[\/#]/).pop() || "Editable shape";
  renderCurrentBusinessRule(item);
  const panel = byId("validation-panel");
  if (item.status === "valid") { panel.className = "validation-panel ok"; panel.textContent = "Valid SHACL. Edit if needed, then accept."; }
  else if (item.status === "invalid") { panel.className = "validation-panel shape-error"; panel.textContent = `Shape/Turtle error — backend completed, but this generated shape is invalid.\nInvalid after ${item.attempts} attempts. Fix it, then accept.\nParser error:\n${item.error || ""}`; }
  else { panel.className = "validation-panel"; panel.textContent = "The model reported no shape for this property."; }
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
  box.value = item.businessRule || "No specific business rule context was returned for this generated shape.";
  tag.textContent = `#${item.index}`;
}

/* ---------- check / accept ---------- */
async function checkShape() {
  const o = getOntology();
  const shape = byId("shape-editor").value.trim();
  if (!shape) return;
  const panel = byId("validation-panel");
  panel.className = "validation-panel"; panel.textContent = "Checking…";
  try {
    const data = await validateTurtle(shape, (o && o.prefixes) || "");
    if (data.valid) { panel.className = "validation-panel ok"; panel.textContent = validationResultMessage(data); }
    else {
      panel.className = "validation-panel shape-error";
      panel.textContent = validationResultMessage(data);
    }
  } catch (e) {
    panel.className = "validation-panel backend error"; panel.textContent = `Validation service/backend error:\n${e.message}`;
  }
}

async function acceptCurrent() {
  if (activeIndex === null) { setStatus("Select a shape first"); return; }
  const o = getOntology();
  const item = queue[activeIndex];
  const shape = byId("shape-editor").value.trim();
  if (!shape) { setStatus("Nothing to accept"); return; }

  const data = await validateTurtle(shape, (o && o.prefixes) || "");
  if (!data.valid) {
    const panel = byId("validation-panel");
    panel.className = "validation-panel shape-error";
    panel.textContent = `Cannot accept invalid generated shape:\n${validationResultMessage(data)}`;
    return;
  }
  item.shape = shape;
  if (item.acceptedId) removeAccepted(item.acceptedId);
  item.acceptedId = acceptShape(item.property, shape);
  renderAccepted(byId("accepted-list"), byId("coverage-tag"));
  renderQueue();
  setStatus("Shape accepted");
}

function acceptAllGenerated() {
  const validItems = queue.filter((item) => item.status === "valid" && item.shape);
  if (!validItems.length) {
    setStatus("No valid generated shapes to accept");
    return;
  }

  let acceptedCount = 0;
  validItems.forEach((item) => {
    if (item.acceptedId) removeAccepted(item.acceptedId);
    item.acceptedId = acceptShape(item.property, item.shape);
    acceptedCount += 1;
  });
  renderAccepted(byId("accepted-list"), byId("coverage-tag"));
  renderQueue();
  const skipped = queue.length - validItems.length;
  setStatus(skipped
    ? `Accepted ${acceptedCount} valid shape(s); ${skipped} left for review`
    : `Accepted ${acceptedCount} shape(s)`);
}
