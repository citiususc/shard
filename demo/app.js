const defaultPrefixes = `@prefix ex: <https://example.org/ontology/> .
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
`;

let ontologyPrefixes = defaultPrefixes;
let entities = [];
let selectedId = "";
let currentFilter = "all";
let acceptedShapes = [];
let savedRules = [];
let lastCandidates = [];
let lastHints = [];

const services = {
  parseOntology: "http://127.0.0.1:9100/parse-ontology",
  findRelevantTerms: "http://127.0.0.1:9101/find-relevant-terms",
  buildShaclShape: "http://127.0.0.1:9102/build-shacl-shape"
};

const byId = (id) => document.getElementById(id);

function currentEntity() {
  return entities.find((entity) => entity.id === selectedId) || null;
}

function displayKind(entity) {
  if (!entity) return "No target";
  if (entity.kind === "DatatypeProperty") return "Datatype property";
  if (entity.kind === "ObjectProperty") return "Object property";
  return entity.kind;
}

function shapePrefixes() {
  const required = [
    ["ex", "@prefix ex: <https://example.org/shapes/> ."],
    ["sh", "@prefix sh: <http://www.w3.org/ns/shacl#> ."],
    ["xsd", "@prefix xsd: <http://www.w3.org/2001/XMLSchema#> ."]
  ];
  const lines = ontologyPrefixes.trim().split(/\n+/);
  required.forEach(([prefix, declaration]) => {
    if (!new RegExp(`(?:@prefix|PREFIX)\\s+${prefix}:`, "i").test(ontologyPrefixes)) {
      lines.push(declaration);
    }
  });
  return `${lines.join("\n")}\n`;
}

function renderEntityList() {
  const query = byId("entity-search").value.trim().toLowerCase();
  const list = byId("entity-list");
  list.innerHTML = "";
  if (!entities.length) {
    list.innerHTML = `<div class="validation-panel">Upload an ontology to browse classes and properties.</div>`;
    return;
  }
  entities
    .filter((entity) => currentFilter === "all" || entity.type === currentFilter)
    .filter((entity) => `${entity.label} ${entity.iri} ${entity.domain} ${entity.range}`.toLowerCase().includes(query))
    .forEach((entity) => {
      const button = document.createElement("button");
      button.className = `entity-card ${entity.id === selectedId ? "active" : ""}`;
      const detail = entity.type === "class" ? entity.iri : `${entity.domain || "No domain"} -> ${entity.range || "No range"}`;
      button.innerHTML = `<strong>${escapeHtml(entity.label)}</strong><span>${escapeHtml(displayKind(entity))} · ${escapeHtml(detail)}</span>`;
      button.addEventListener("click", () => {
        selectedId = entity.id;
        lastHints = [];
        syncView();
        byId("shape-editor").value = "";
        renderHints(entity);
        renderCandidates();
        setStatus("Entity selected");
      });
      list.appendChild(button);
    });
}

function currentRule() {
  return byId("business-rule").value.trim();
}

function renderRules() {
  byId("rule-cards").innerHTML = savedRules.length
    ? savedRules.map((text) => `<article class="rule-card"><p>${escapeHtml(text)}</p></article>`).join("")
    : `<div class="validation-panel">No saved rules yet.</div>`;
}

function renderHints(entity) {
  byId("hint-count").textContent = `${lastHints.length} hints`;
  if (!entity) {
    byId("hint-list").innerHTML = `<div class="validation-panel">Upload an ontology and select a candidate target first.</div>`;
    return;
  }
  byId("hint-list").innerHTML = lastHints.length
    ? lastHints.map((hint) => `<div class="hint-item"><span>${escapeHtml(hint.reason || "Service hint")}</span><code>${escapeHtml(hint.constraint || "no direct SHACL constraint")}</code></div>`).join("")
    : `<div class="validation-panel">Build a SHACL shape to receive service-generated constraint hints.</div>`;
}

function renderCandidates() {
  byId("candidate-count").textContent = `${lastCandidates.length} matches`;
  if (!lastCandidates.length) {
    byId("candidate-list").innerHTML = `<div class="validation-panel">Upload an ontology and write a business rule to rank classes and properties.</div>`;
    return;
  }

  byId("candidate-list").innerHTML = lastCandidates.map(({ entity, score, reasons }) => {
    const selected = entity.id === selectedId ? " active" : "";
    const detail = entity.type === "class" ? entity.iri : `${entity.domain || "No domain"} -> ${entity.range || "No range"}`;
    return `<button class="candidate-card${selected}" data-entity-id="${escapeHtml(entity.id)}">
      <span class="score">${Math.min(99, Math.round(score))}</span>
      <strong>${escapeHtml(entity.label)}</strong>
      <small>${escapeHtml(displayKind(entity))} · ${escapeHtml(detail)}</small>
      <em>${escapeHtml(reasons.slice(0, 2).join("; "))}</em>
    </button>`;
  }).join("");

  document.querySelectorAll(".candidate-card").forEach((button) => {
    button.addEventListener("click", () => {
      selectedId = button.dataset.entityId;
      lastHints = [];
      syncView();
      byId("shape-editor").value = "";
      renderHints(currentEntity());
      renderCandidates();
      setStatus("Candidate selected");
    });
  });
}

function apiConfig() {
  return {
    provider: byId("api-provider").value,
    model: byId("api-model").value,
    api_key: byId("api-key").value
  };
}

function servicePayload(extra = {}) {
  return {
    ...apiConfig(),
    business_rule: currentRule(),
    ontology_terms: entities,
    prefixes: shapePrefixes(),
    ...extra
  };
}

async function postJson(url, payload, timeoutMs = 15000) {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs);
  let response;
  try {
    response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal: controller.signal
    });
  } catch (error) {
    if (error.name === "AbortError") {
      throw new Error(`Request timed out after ${Math.round(timeoutMs / 1000)} seconds`);
    }
    throw error;
  } finally {
    window.clearTimeout(timeout);
  }

  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`);
  }
  return response.json();
}

async function analyzeRule() {
  if (!entities.length || !currentRule()) {
    lastCandidates = [];
    renderCandidates();
    setStatus(!entities.length ? "Upload ontology first" : "Write a business rule");
    return;
  }

  setStatus("Calling term service");
  try {
    const data = await postJson(services.findRelevantTerms, servicePayload());
    lastCandidates = (data.candidates || [])
      .map((candidate) => ({
        entity: entities.find((entity) => entity.id === candidate.entity_id),
        score: candidate.score || 0,
        reasons: candidate.reasons || ["service response"]
      }))
      .filter((candidate) => candidate.entity);
    selectedId = lastCandidates[0]?.entity.id || "";
  } catch (error) {
    lastCandidates = [];
    setStatus("Term service unavailable");
    byId("candidate-list").innerHTML = `<div class="validation-panel error">Could not call ${services.findRelevantTerms}: ${escapeHtml(error.message)}</div>`;
    return;
  }

  syncView();
  renderCandidates();
  setStatus(lastCandidates.length ? "Ontology terms ranked" : "No candidates found");
}

function syncView() {
  const entity = currentEntity();
  renderEntityList();
  byId("selected-label").textContent = entity ? entity.label : "No target selected";
  byId("selected-kind").textContent = displayKind(entity);
  byId("selected-iri").textContent = entity ? entity.iri : "Not selected";
  byId("selected-domain").textContent = entity ? (entity.type === "class" ? entity.iri : entity.domain || "Not found") : "Not selected";
  byId("selected-range").textContent = entity ? (entity.type === "class" ? "Class shape" : entity.range || "Not found") : "Not selected";
  byId("ontology-note").value = entity ? entity.ontologyNote : "";
  if (!entity) byId("shape-editor").value = "";
  byId("validation-panel").className = "validation-panel";
  byId("validation-panel").textContent = entity
    ? "Edit the proposal and check the essentials before accepting it."
    : "Upload an ontology, write a business rule, and select a ranked target.";
  renderRules();
  renderHints(entity);
  renderAccepted();
}

function setStatus(text) {
  byId("status-pill").textContent = text;
}

function updateEntityFromInputs() {
  const entity = currentEntity();
  if (!entity) return;
  entity.ontologyNote = byId("ontology-note").value.trim();
}

async function regenerateShape() {
  updateEntityFromInputs();
  const entity = currentEntity();
  if (!entity || !currentRule()) {
    setStatus(!entity ? "No target selected" : "Write a business rule");
    renderHints(entity);
    return;
  }

  setStatus("Calling SHACL service");
  try {
    const data = await postJson(services.buildShaclShape, servicePayload({ target: entity, timeout: 10 }), 12000);
    byId("shape-editor").value = data.shape || "";
    lastHints = data.hints || [];
    renderHints(entity);
    setStatus(data.fallback ? "Fallback SHACL proposal received" : "Databricks content received");
    byId("validation-panel").className = data.fallback ? "validation-panel warn" : "validation-panel ok";
    byId("validation-panel").textContent = data.message || "SHACL proposal received.";
    validateShape(false);
  } catch (error) {
    setStatus("SHACL service unavailable");
    byId("validation-panel").className = "validation-panel error";
    byId("validation-panel").textContent = `Could not call ${services.buildShaclShape}: ${error.message}`;
  }
}

function validateShape(showStatus = true) {
  const text = byId("shape-editor").value;
  const panel = byId("validation-panel");
  const entity = currentEntity();
  const errors = [];

  if (!entity) {
    panel.className = "validation-panel warn";
    panel.textContent = "Select a target class or property before checking a shape.";
    if (showStatus) setStatus("No target selected");
    return false;
  }

  if (!text.includes("@prefix sh:") && !/PREFIX\s+sh:/i.test(text)) errors.push("Missing sh prefix.");
  if (entity.type === "class" && !text.includes("a sh:NodeShape")) errors.push("A selected class should produce a sh:NodeShape.");
  if (entity.type === "property" && !text.includes("a sh:PropertyShape")) errors.push("A selected property should produce a sh:PropertyShape.");
  if (!text.includes("sh:targetClass")) errors.push("Missing sh:targetClass.");
  if (entity.type === "property" && !text.includes("sh:path")) errors.push("Missing sh:path.");
  if (!text.trim().endsWith(".")) errors.push("The Turtle block should end with a period.");

  if (errors.length) {
    panel.className = "validation-panel error";
    panel.textContent = errors.join(" ");
    if (showStatus) setStatus("Check syntax");
    return false;
  }

  const warnings = [];
  if (!text.includes(entity.iri) && entity.type === "class") warnings.push("The target class does not match the selected class.");
  if (entity.type === "property" && !text.includes(entity.iri)) warnings.push("The sh:path does not match the selected property.");
  if (entity.type === "property" && entity.domain && !text.includes(entity.domain)) warnings.push("The sh:targetClass does not match the selected domain.");

  if (warnings.length) {
    panel.className = "validation-panel warn";
    panel.textContent = warnings.join(" ");
    if (showStatus) setStatus("Check scope");
    return true;
  }

  panel.className = "validation-panel ok";
  panel.textContent = "The shape has the minimum structure and matches the selected ontology entity.";
  if (showStatus) setStatus("Check passed");
  return true;
}

function acceptShape() {
  updateEntityFromInputs();
  const checks = ["check-grounded", "check-scope", "check-operative"].every((id) => byId(id).checked);
  const valid = validateShape(false);
  const panel = byId("validation-panel");

  if (!valid || !checks) {
    panel.className = "validation-panel warn";
    panel.textContent = "Before accepting, check the shape and mark the three review criteria.";
    return;
  }

  const entity = currentEntity();
  if (!entity) return;
  acceptedShapes = acceptedShapes.filter((shape) => shape.entityId !== entity.id);
  acceptedShapes.push({
    entityId: entity.id,
    label: entity.label,
    iri: entity.iri,
    text: byId("shape-editor").value
  });
  setStatus("Shape accepted");
  renderAccepted();
}

function renderAccepted() {
  byId("coverage-tag").textContent = `${acceptedShapes.length} accepted`;
  byId("accepted-list").innerHTML = acceptedShapes.length
    ? acceptedShapes.map((shape) => `<div class="accepted-item"><code>${escapeHtml(shape.iri)}</code><span>accepted</span></div>`).join("")
    : `<div class="validation-panel">No accepted shapes yet.</div>`;
}

function addRule() {
  updateEntityFromInputs();
  const text = currentRule();
  if (!text) return;
  savedRules = [text, ...savedRules.filter((rule) => rule !== text)].slice(0, 5);
  renderRules();
  renderCandidates();
  renderHints(currentEntity());
  setStatus("Rule saved");
}

async function handleOntologyUpload(event) {
  const file = event.target.files[0];
  if (!file) return;
  const text = await file.text();
  setStatus("Parsing ontology");
  let parsed;
  try {
    parsed = await postJson(services.parseOntology, { filename: file.name, content: text });
  } catch (error) {
    setStatus("Ontology parser unavailable");
    byId("ontology-summary").textContent = `${file.name}: parser service unavailable.`;
    return;
  }
  if (!parsed.entities.length) {
    setStatus("No classes or properties found");
    byId("ontology-summary").textContent = `${file.name}: ${parsed.error || "no supported declarations found."}`;
    return;
  }

  ontologyPrefixes = parsed.prefixes || defaultPrefixes;
  entities = parsed.entities;
  selectedId = "";
  acceptedShapes = [];
  lastCandidates = [];
  lastHints = [];
  byId("ontology-summary").textContent = `${file.name}: ${entities.filter((entity) => entity.type === "class").length} classes, ${entities.filter((entity) => entity.type === "property").length} properties.`;
  syncView();
  renderCandidates();
  setStatus("Ontology loaded");
}

function copyShape() {
  byId("shape-editor").select();
  document.execCommand("copy");
  setStatus("Shape copied");
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "\"": "&quot;",
    "'": "&#039;"
  })[char]);
}

document.querySelectorAll(".switch-button").forEach((button) => {
  button.addEventListener("click", () => {
    currentFilter = button.dataset.filter;
    document.querySelectorAll(".switch-button").forEach((item) => item.classList.toggle("active", item === button));
    renderEntityList();
  });
});

byId("ontology-file").addEventListener("change", handleOntologyUpload);
byId("entity-search").addEventListener("input", renderEntityList);
byId("business-rule").addEventListener("input", () => {
  lastCandidates = [];
  lastHints = [];
  renderCandidates();
  renderHints(currentEntity());
});
byId("ontology-note").addEventListener("input", () => {
  lastCandidates = [];
  lastHints = [];
  renderCandidates();
  renderHints(currentEntity());
});
byId("analyze-rule").addEventListener("click", analyzeRule);
byId("regenerate").addEventListener("click", regenerateShape);
byId("validate-shape").addEventListener("click", () => validateShape(true));
byId("accept-shape").addEventListener("click", acceptShape);
byId("add-rule").addEventListener("click", addRule);
byId("copy-shape").addEventListener("click", copyShape);
byId("reset-demo").addEventListener("click", () => {
  ontologyPrefixes = defaultPrefixes;
  entities = [];
  selectedId = "";
  currentFilter = "all";
  acceptedShapes = [];
  savedRules = [];
  lastCandidates = [];
  lastHints = [];
  byId("ontology-summary").textContent = "No ontology loaded.";
  byId("ontology-file").value = "";
  byId("business-rule").value = "";
  document.querySelectorAll(".switch-button").forEach((button) => button.classList.toggle("active", button.dataset.filter === "all"));
  ["check-grounded", "check-scope", "check-operative"].forEach((id) => {
    byId(id).checked = false;
  });
  syncView();
  renderCandidates();
  setStatus("Demo reset");
});

syncView();
renderCandidates();
