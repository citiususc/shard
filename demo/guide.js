/* guide.js — Workflow 2: full guide → all SHACL shapes (streamed). */

let guideFile = null;       // {filename, content, isBase64}
let queue = [];             // [{index, property, status, shape, error, attempts, acceptedId}]
let activeIndex = null;     // currently reviewed queue item index
let nodeShapes = "";        // aggregated sh:NodeShape block from the "done" event

document.addEventListener("DOMContentLoaded", () => {
  wireReset("reset-demo");
  wireModelControls();
  attachTurtleHighlighter("shape-editor", "shape-editor-hl");
  attachTurtleHighlighter("prefixes-editor", "prefixes-editor-hl");
  wireOntologyControls(() => {});
  renderAccepted(byId("accepted-list"), byId("coverage-tag"));
  wireExport("export-shapes", () => nodeShapes);

  byId("guide-file").addEventListener("change", onGuideSelected);
  byId("generate-guide").addEventListener("click", generateAll);
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
  const isPdf = file.name.toLowerCase().endsWith(".pdf");
  if (isPdf) {
    const buf = await file.arrayBuffer();
    let bin = ""; const bytes = new Uint8Array(buf);
    for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
    guideFile = { filename: file.name, content: btoa(bin), isBase64: true };
    byId("html-version").value = "1.6.1";
  } else {
    guideFile = { filename: file.name, content: await file.text(), isBase64: false };
  }
  byId("guide-summary").textContent = `${file.name} loaded (${isPdf ? "PDF" : "HTML"}).`;
}

/* ---------- generate (streamed) ---------- */
async function generateAll() {
  const o = getOntology();
  if (!o) { setStatus("Load an ontology first"); return; }
  if (!guideFile) { setStatus("Upload a guide first"); return; }

  const m = getModels();
  queue = []; activeIndex = null; nodeShapes = "";
  renderQueue();
  setProgress(0, 0, "Starting…");
  byId("generate-guide").disabled = true;
  setStatus("Preprocessing guide…");

  const payload = {
    ontology_content: o.content, base_namespace: o.baseNamespace, prefixes: o.prefixes,
    guide_content: guideFile.content, guide_filename: guideFile.filename, guide_is_base64: guideFile.isBase64,
    html_version: byId("html-version").value,
    llm_model: m.llmModel, text_model: m.textModel, vision_model: m.visionModel,
    embedding_model: m.embeddingModel, temperature: 0.5, provider: m.provider,
  };

  try {
    const res = await fetch(SERVICES.guide, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok || !res.body) throw new Error(`service returned ${res.status}`);
    await consumeStream(res.body);
  } catch (e) {
    setProgress(0, 0, `Error: ${e.message}`);
    setStatus("Generation failed");
  } finally {
    byId("generate-guide").disabled = false;
  }
}

async function consumeStream(body) {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let sep;
    while ((sep = buffer.indexOf("\n\n")) !== -1) {
      const chunk = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      const line = chunk.split("\n").find((l) => l.startsWith("data: "));
      if (!line) continue;
      try { handleEvent(JSON.parse(line.slice(6))); } catch { /* ignore */ }
    }
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
      shape: ev.shape || "", error: ev.error, attempts: ev.attempts, acceptedId: null,
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
  byId("queue-count").textContent = `${queue.length} shapes`;
  list.innerHTML = "";
  queue.forEach((item, i) => {
    const card = document.createElement("button");
    const accepted = item.acceptedId ? " accepted" : "";
    card.className = `queue-card ${item.status}${activeIndex === i ? " active" : ""}${accepted}`;
    const badge = item.acceptedId ? "accepted" : item.status;
    const short = String(item.property).split(/[\/#]/).pop();
    card.innerHTML =
      `<strong>${esc(short)}</strong>` +
      `<div class="qmeta"><span class="qbadge ${badge}">${badge}</span>` +
      `<span>#${item.index} · ${item.attempts} attempt(s)</span></div>`;
    card.addEventListener("click", () => selectQueueItem(i));
    list.appendChild(card);
  });
}

function selectQueueItem(i) {
  activeIndex = i;
  const item = queue[i];
  byId("shape-editor").value = item.shape || "";
  refreshHighlight("shape-editor");
  byId("editor-title").textContent = String(item.property).split(/[\/#]/).pop() || "Editable shape";
  const panel = byId("validation-panel");
  if (item.status === "valid") { panel.className = "validation-panel ok"; panel.textContent = "Valid SHACL. Edit if needed, then accept."; }
  else if (item.status === "invalid") { panel.className = "validation-panel error"; panel.textContent = `Invalid after ${item.attempts} attempts. Fix it, then accept.\nParser error:\n${item.error || ""}`; }
  else { panel.className = "validation-panel"; panel.textContent = "The model reported no shape for this property."; }
  renderQueue();
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
    if (data.valid) { panel.className = "validation-panel ok"; panel.textContent = "Valid Turtle / SHACL."; }
    else { panel.className = "validation-panel error"; panel.textContent = `Parse error:\n${data.error}`; }
  } catch (e) {
    panel.className = "validation-panel error"; panel.textContent = `Validation service error: ${e.message}`;
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
    panel.className = "validation-panel error";
    panel.textContent = `Cannot accept invalid Turtle:\n${data.error}`;
    return;
  }
  item.shape = shape;
  if (item.acceptedId) removeAccepted(item.acceptedId);
  item.acceptedId = acceptShape(item.property, shape);
  renderAccepted(byId("accepted-list"), byId("coverage-tag"));
  renderQueue();
  setStatus("Shape accepted");
}
