/* rule.js — Workflow 1: business rule → single SHACL shape. */

let entityFilter = "all";
let selectedEntity = null;
let lastCandidates = [];
let semanticSearchActive = false;

document.addEventListener("DOMContentLoaded", () => {
  wireReset("reset-demo");
  wireModelControls();
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

  // Logs drawer
  byId("logs-toggle").addEventListener("click", () => {
    const open = byId("logs-drawer").classList.toggle("open");
    byId("logs-toggle").classList.toggle("active", open);
  });
  byId("logs-close").addEventListener("click", () => {
    byId("logs-drawer").classList.remove("open");
    byId("logs-toggle").classList.remove("active");
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
  const button = byId("analyze-rule");
  button.disabled = true;
  setStatus("Ranking ontology terms…");
  byId("ontology-search-status").textContent = "Ranking ontology terms…";
  try {
    const res = await fetch(SERVICES.terms, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        business_rule: rule, ontology_terms: o.entities,
        ontology_hash: o.contentHash || "",
        entity_types: entityFilter === "all" ? [] : [entityFilter],
        embedding_model: m.embeddingModel,
        config_fingerprint: modelConfigFingerprint(m),
        inference_config: getInferenceConfig(),
        model: m.llmModel, provider: m.provider,
      }),
    });
    const data = await res.json();
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

  setStatus("Generating SHACL shape…");
  byId("generate-shape").disabled = true;
  const panel = byId("validation-panel");
  panel.className = "validation-panel";
  panel.textContent = "Generating… (this may take a while on the first call).";

  try {
    const target = { ...selectedEntity, ontologyNote: byId("ontology-note").value };
    const res = await fetch(SERVICES.build, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        business_rule: rule, target, prefixes: o.prefixes,
        ontology_content: o.content, base_namespace: o.baseNamespace,
        domain_context: byId("domain-context").value.trim(),
        generation_guidance: byId("generation-guidance").value.trim(),
        model: m.llmModel, provider: m.provider, temperature: m.temperature,
        inference_config: getInferenceConfig(),
      }),
    });
    const data = await res.json();
    byId("shape-editor").value = data.shape || "";
    refreshHighlight("shape-editor");
    renderLogs(data.logs);
    if (data.not_found) {
      panel.className = "validation-panel";
      panel.textContent = data.message || "No shape could be justified from this rule.";
    } else if (data.valid) {
      panel.className = "validation-panel ok";
      panel.textContent = data.message || "Valid SHACL generated.";
    } else if (data.error_type === "backend") {
      panel.className = "validation-panel error";
      panel.textContent = `Model/backend error (not a problem with your shape):\n${data.error || data.message}`;
    } else {
      panel.className = "validation-panel error";
      panel.textContent = `Returned after ${data.attempts} attempts with a Turtle parse error:\n${data.error || data.message}`;
    }
    setStatus(data.valid ? "Shape generated" : "Shape needs fixing");
  } catch (e) {
    panel.className = "validation-panel error";
    panel.textContent = `Generation service error: ${e.message}`;
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
  panel.className = "validation-panel";
  panel.textContent = "Checking…";
  try {
    const data = await validateTurtle(shape, (o && o.prefixes) || "");
    if (data.valid) { panel.className = "validation-panel ok"; panel.textContent = "Valid Turtle / SHACL."; }
    else { panel.className = "validation-panel error"; panel.textContent = `Parse error:\n${data.error}`; }
  } catch (e) {
    panel.className = "validation-panel error";
    panel.textContent = `Validation service error: ${e.message}`;
  }
}

async function acceptCurrent() {
  const o = getOntology();
  const shape = byId("shape-editor").value.trim();
  if (!shape) { setStatus("Nothing to accept"); return; }
  const data = await validateTurtle(shape, (o && o.prefixes) || "");
  if (!data.valid) {
    const panel = byId("validation-panel");
    panel.className = "validation-panel error";
    panel.textContent = `Cannot accept invalid Turtle:\n${data.error}`;
    return;
  }
  acceptShape(selectedEntity ? selectedEntity.iri : "(shape)", shape);
  renderAccepted(byId("accepted-list"), byId("coverage-tag"));
  setStatus("Shape accepted");
}

function renderLogs(logs) {
  const el = byId("logs-content");
  if (!el) return;
  if (!logs || !logs.trim()) { el.textContent = "(no log output captured for this run)"; return; }
  const html = logs.split("\n").map((line) => {
    const e = esc(line);
    if (line.includes("[ERROR]")) return `<span class="log-error">${e}</span>`;
    if (line.includes("[WARN]")) return `<span class="log-warn">${e}</span>`;
    if (line.includes("[INFO]")) return `<span class="log-info">${e}</span>`;
    if (line.includes("[DEBUG]")) return `<span class="log-debug">${e}</span>`;
    return e;
  }).join("\n");
  el.innerHTML = html;
  el.scrollTop = el.scrollHeight;
}
