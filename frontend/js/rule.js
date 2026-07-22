/* rule.js — Workflow 1: data constraint to single SHACL shape. */

let entityFilter = "all";
const TARGET_ROLE_KEYS = ["focus_nodes", "constraint_paths", "related_terms"];
let targetRoles = emptyTargetRoles();
let resolutionMeta = { resolvedBy: "", resolutionScore: null, scoreKind: "none" };
let lastCandidates = [];
let semanticSearchActive = false;
let ruleGenerationLogId = null;
let draggedResolvedTerm = null;
const DEFAULT_RULE_METADATA = Object.freeze({
  number: "RULE-001",
  title: "Data constraint",
});
let activeRuleMetadata = { ...DEFAULT_RULE_METADATA };

document.addEventListener("DOMContentLoaded", async () => {
  await loadDeploymentCapabilities();
  wireReset("reset-demo");
  wireSessionControls({
    workflow: "rule",
    getWorkspaceState: ruleWorkspaceState,
    applyWorkspaceState: applyRuleWorkspaceState,
  });
  wireModelControls();
  wireShapeValidationProfileControls();
  wireAstreaBaselineControls();
  wireExport("export-shapes");
  renderAccepted(byId("accepted-list"), byId("coverage-tag"));
  wireAcceptedShapesControls(
    byId("accepted-list"),
    byId("coverage-tag"),
    byId("remove-all-accepted-shapes"),
  );

  // IDE-style Turtle highlighting (attach before ontology wiring so the
  // prefixes editor refreshes when its value is seeded).
  attachTurtleHighlighter("shape-editor", "shape-editor-hl");
  attachTurtleHighlighter("prefixes-editor", "prefixes-editor-hl");
  wireExpandableCodeEditors();

  wireOntologyControls(() => {
    clearSemanticResults(false);
    clearResolvedTerms();
    renderEntities();
    void refreshAstreaBaselineForOntology();
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
  byId("business-rule").addEventListener("input", () => {
    activeRuleMetadata = { ...DEFAULT_RULE_METADATA };
    clearSemanticResults(false);
    clearResolvedTerms();
    updateGenerateAvailability();
  });

  byId("analyze-rule").addEventListener("click", resolveBusinessRule);
  byId("clear-related-terms").addEventListener("click", () => clearSemanticResults());
  byId("clear-resolved-terms").addEventListener("click", () => clearResolvedTerms());
  wireResolvedTermDragAndDrop();
  byId("generate-shape").addEventListener("click", generateShape);
  byId("validate-shape").addEventListener("click", checkShape);
  byId("accept-shape").addEventListener("click", acceptCurrent);
  byId("copy-shape").addEventListener("click", async () => {
    const button = byId("copy-shape");
    const ok = await copyToClipboard(byId("shape-editor").value);
    showCopyFeedback(button, ok);
    setStatus(ok ? "Copied" : "Copy failed");
  });
  renderResolvedTerms();
  updateGenerateAvailability();
});

function ruleWorkspaceState() {
  return {
    workflow: "rule",
    domainContext: byId("domain-context").value,
    generationGuidance: byId("generation-guidance").value,
    dataConstraint: {
      number: activeRuleMetadata.number,
      title: activeRuleMetadata.title,
      text: byId("business-rule").value,
    },
    editableShape: byId("shape-editor").value,
    targetRoles,
    resolutionMeta,
    candidates: lastCandidates,
    semanticSearchActive,
    entityFilter,
    ontologySearch: byId("entity-search").value,
  };
}

function applyRuleWorkspaceState(workspace) {
  byId("domain-context").value = String(workspace.domainContext || "");
  byId("generation-guidance").value = String(workspace.generationGuidance || "");
  const constraint = workspace.dataConstraint || workspace.rule || {};
  activeRuleMetadata = {
    number: String(constraint.number || DEFAULT_RULE_METADATA.number),
    title: String(constraint.title || DEFAULT_RULE_METADATA.title),
  };
  byId("business-rule").value = String(constraint.text || "");
  byId("shape-editor").value = String(workspace.editableShape || "");
  targetRoles = workspace.targetRoles && typeof workspace.targetRoles === "object"
    ? workspace.targetRoles : emptyTargetRoles();
  TARGET_ROLE_KEYS.forEach((role) => {
    if (!Array.isArray(targetRoles[role])) targetRoles[role] = [];
  });
  resolutionMeta = workspace.resolutionMeta && typeof workspace.resolutionMeta === "object"
    ? workspace.resolutionMeta
    : { resolvedBy: "", resolutionScore: null, scoreKind: "none" };
  lastCandidates = Array.isArray(workspace.candidates) ? workspace.candidates : [];
  semanticSearchActive = Boolean(workspace.semanticSearchActive && lastCandidates.length);
  entityFilter = ["all", "class", "property"].includes(workspace.entityFilter)
    ? workspace.entityFilter : "all";
  document.querySelectorAll("[data-filter]").forEach((button) => {
    button.classList.toggle("active", button.dataset.filter === entityFilter);
  });
  byId("entity-search").value = String(workspace.ontologySearch || "");
  renderResolvedTerms();
  renderEntities();
  refreshHighlight("shape-editor");
  updateGenerateAvailability();
}

function activeBusinessRule(text) {
  return apiBusinessRule(
    text,
    activeRuleMetadata.number,
    activeRuleMetadata.title,
  );
}

function emptyTargetRoles() {
  return { focus_nodes: [], constraint_paths: [], related_terms: [] };
}

function termKey(term) {
  return String(term && (term.full_iri || term.iri || term.id) || "");
}

function allResolvedTerms() {
  return TARGET_ROLE_KEYS.flatMap((role) => targetRoles[role] || []);
}

function roleForTerm(term) {
  const key = termKey(term);
  return TARGET_ROLE_KEYS.find((role) => targetRoles[role].some((item) => termKey(item) === key)) || "";
}

function ontologyReferenceValues(value) {
  const values = Array.isArray(value) ? value : [value];
  return values
    .filter(Boolean)
    .map((item) => {
      if (typeof item === "string") return item;
      return String(item.iri || item.full_iri || item.label || "");
    })
    .filter(Boolean);
}

function defaultRoleForTerm(term) {
  return term.type === "property" ? "constraint_paths" : "focus_nodes";
}

function validRolesForTerm(term) {
  return term.type === "property"
    ? ["constraint_paths", "related_terms"]
    : ["focus_nodes", "related_terms"];
}

function roleLabel(role) {
  return {
    focus_nodes: "Focus node",
    constraint_paths: "Constrained property",
    related_terms: "Related term",
  }[role] || role;
}

function termTypeLabel(term) {
  const value = String(term.kind || term.type || "Ontology term")
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .replace(/[_-]+/g, " ")
    .trim();
  return value
    ? `${value.charAt(0).toUpperCase()}${value.slice(1).toLowerCase()}`
    : "Ontology term";
}

function resolverSignalLabel(value) {
  const text = String(value || "").trim();
  if (text === "llm") return "LLM fallback";
  if (text === "manual") return "manual review";
  return text || "unknown signal";
}

function removeResolvedTerm(term, render = true) {
  const key = termKey(term);
  TARGET_ROLE_KEYS.forEach((role) => {
    targetRoles[role] = targetRoles[role].filter((item) => termKey(item) !== key);
  });
  if (render) {
    renderResolvedTerms();
    renderEntities();
  }
}

function addResolvedTerm(term, role = defaultRoleForTerm(term), render = true) {
  removeResolvedTerm(term, false);
  const validRole = validRolesForTerm(term).includes(role) ? role : defaultRoleForTerm(term);
  targetRoles[validRole].push(term);
  resolutionMeta = { resolvedBy: "manual", resolutionScore: null, scoreKind: "none" };
  if (render) {
    renderResolvedTerms();
    renderEntities();
  }
}

function moveResolvedTerm(term, role) {
  addResolvedTerm(term, role);
}

function clearResolvedTermDragState() {
  draggedResolvedTerm = null;
  document.querySelectorAll(".resolved-role-group").forEach((group) => {
    group.classList.remove("drop-allowed", "drop-blocked", "is-drag-over");
  });
  document.querySelectorAll(".resolved-term-row.dragging").forEach((row) => {
    row.classList.remove("dragging");
    row.setAttribute("aria-grabbed", "false");
  });
}

function wireResolvedTermDragAndDrop() {
  document.querySelectorAll("[data-target-role]").forEach((group) => {
    const role = group.dataset.targetRole;
    group.addEventListener("dragover", (event) => {
      if (!draggedResolvedTerm || !validRolesForTerm(draggedResolvedTerm).includes(role)) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = "move";
      document.querySelectorAll(".resolved-role-group.is-drag-over").forEach((item) => {
        item.classList.remove("is-drag-over");
      });
      group.classList.add("is-drag-over");
    });
    group.addEventListener("drop", (event) => {
      if (!draggedResolvedTerm || !validRolesForTerm(draggedResolvedTerm).includes(role)) return;
      event.preventDefault();
      const term = draggedResolvedTerm;
      const currentRole = roleForTerm(term);
      clearResolvedTermDragState();
      if (currentRole !== role) moveResolvedTerm(term, role);
    });
  });
  document.addEventListener("dragend", clearResolvedTermDragState);
}

function startResolvedTermDrag(event, term, role) {
  draggedResolvedTerm = term;
  event.currentTarget.classList.add("dragging");
  event.currentTarget.setAttribute("aria-grabbed", "true");
  event.dataTransfer.effectAllowed = "move";
  event.dataTransfer.setData("text/plain", termKey(term));
  document.querySelectorAll("[data-target-role]").forEach((group) => {
    const targetRole = group.dataset.targetRole;
    const compatible = validRolesForTerm(term).includes(targetRole);
    group.classList.toggle("drop-allowed", compatible && targetRole !== role);
    group.classList.toggle("drop-blocked", !compatible);
  });
}

function clearResolvedTerms(render = true) {
  targetRoles = emptyTargetRoles();
  resolutionMeta = { resolvedBy: "", resolutionScore: null, scoreKind: "none" };
  if (render) {
    renderResolvedTerms();
    renderEntities();
  }
}

function renderResolvedTerms() {
  const config = {
    focus_nodes: ["focus-node-list", "focus-node-count"],
    constraint_paths: ["constraint-path-list", "constraint-path-count"],
    related_terms: ["related-term-list", "related-term-count"],
  };
  TARGET_ROLE_KEYS.forEach((role) => {
    const [listId, countId] = config[role];
    const list = byId(listId);
    const terms = targetRoles[role];
    byId(countId).textContent = String(terms.length);
    list.innerHTML = "";
    if (!terms.length) {
      list.innerHTML = `<p class="resolved-term-empty">Drop terms here</p>`;
      return;
    }
    terms.forEach((term) => {
      const row = document.createElement("div");
      row.className = "resolved-term-row";
      row.draggable = true;
      row.title = "Drag to move this term to another compatible column";
      row.setAttribute("aria-label", `${term.label || term.iri}. Drag to move to another compatible role.`);
      row.setAttribute("aria-grabbed", "false");
      row.addEventListener("dragstart", (event) => startResolvedTermDrag(event, term, role));
      const identity = document.createElement("div");
      identity.className = "resolved-term-identity";
      identity.innerHTML = `<strong>${esc(term.label || term.iri)}</strong><small>${esc(termTypeLabel(term))}</small>`;
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "icon-button resolved-term-remove";
      remove.title = "Remove ontology term";
      remove.setAttribute("aria-label", `Remove ${term.label || term.iri}`);
      remove.textContent = "×";
      remove.addEventListener("click", () => removeResolvedTerm(term));
      remove.addEventListener("mousedown", (event) => event.stopPropagation());
      row.append(identity, remove);
      list.appendChild(row);
    });
  });

  const count = allResolvedTerms().length;
  const signal = byId("resolution-signal");
  signal.textContent = resolutionMeta.resolvedBy
    ? (resolutionMeta.resolvedBy === "manual" ? "Manual" : `Via ${resolutionMeta.resolvedBy}`)
    : "Not resolved";
  byId("clear-resolved-terms").disabled = count === 0;
  const resolutionScore = resolutionMeta.resolutionScore == null
    ? ""
    : ` · ${resolutionMeta.scoreKind.replaceAll("_", " ")} score ${Number(resolutionMeta.resolutionScore).toFixed(2)}`;
  signal.title = count
    ? `${count} ontology term${count === 1 ? "" : "s"} in this rule context${resolutionScore}.`
    : "No ontology terms resolved.";
  byId("generate-shape").textContent = count
    ? "Generate SHACL shape"
    : "Resolve and generate SHACL shape";
  updateGenerateAvailability();
  scheduleWorkspacePersistence();
}

function updateGenerateAvailability() {
  const button = byId("generate-shape");
  if (!button) return;
  button.disabled = !(getOntology() && byId("business-rule").value.trim());
}

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
    return [
      e.label,
      e.iri,
      ...ontologyReferenceValues(e.domain),
      ...ontologyReferenceValues(e.range),
    ].some((value) => String(value || "").toLowerCase().includes(q));
  });

  items.slice(0, 400).forEach((e) => {
    const candidate = candidateMap.get(e.id);
    const selectedRole = roleForTerm(e);
    const card = document.createElement("button");
    card.className = "entity-card"
      + (candidate ? " ranked" : "")
      + (selectedRole ? " active" : "");
    const domains = ontologyReferenceValues(e.domain);
    card.innerHTML =
      (candidate ? `<div class="score">${candidate.score}</div>` : "") +
      `<strong>${esc(e.label)}</strong>` +
      `<span>${esc(e.iri)}${e.type === "property" && domains.length ? " · domain " + esc(domains.join(", ")) : ""}</span>` +
      (selectedRole
        ? `<small class="entity-reason">${esc(roleLabel(selectedRole))}</small>`
        : candidate ? `<small class="entity-reason">${esc((candidate.reasons || []).join(" · "))}</small>` : "");
    card.addEventListener("click", () => toggleResolvedTerm(e));
    list.appendChild(card);
  });
  if (!items.length) {
    list.innerHTML = `<p class="microcopy">${semanticSearchActive
      ? "No related terms matched the current text search."
      : "No ontology terms matched the current filters."}</p>`;
  }
}

function toggleResolvedTerm(term) {
  if (roleForTerm(term)) removeResolvedTerm(term);
  else addResolvedTerm(term);
}

/* ---------- find relevant terms ---------- */
function clearSemanticResults(render = true) {
  lastCandidates = [];
  semanticSearchActive = false;
  const clear = byId("clear-related-terms");
  if (clear) clear.hidden = true;
  const status = byId("ontology-search-status");
  if (status) status.textContent = "Search by text, rank and resolve ontology terms against the data constraint, or add them manually.";
  if (render) renderEntities();
}

function ontologyTermByTarget(ontology, target) {
  const value = String(target || "");
  return (ontology.entities || []).find((term) =>
    [term.id, term.iri, term.full_iri, term.label].some((ref) => String(ref || "") === value));
}

function applyResolvedRoles(row, ontology) {
  targetRoles = emptyTargetRoles();
  const resolvedRoles = row.target_roles || row;
  TARGET_ROLE_KEYS.forEach((role) => {
    (resolvedRoles[role] || []).forEach((target) => {
      const term = ontologyTermByTarget(ontology, target.iri || target);
      if (term && !roleForTerm(term)) targetRoles[role].push(term);
    });
  });
  resolutionMeta = {
    resolvedBy: row.resolved_by || "none",
    resolutionScore: row.resolution_score ?? row.confidence ?? null,
    scoreKind: row.score_kind || "none",
  };
  renderResolvedTerms();
}

async function resolveBusinessRule() {
  const o = getOntology();
  if (!o) { setStatus("Load an ontology first"); return false; }
  const rule = byId("business-rule").value.trim();
  if (!rule) { setStatus("Write a data constraint first"); return false; }

  const m = getModels();
  const button = byId("analyze-rule");
  button.disabled = true;
  setStatus("Resolving data constraint…");
  byId("ontology-search-status").textContent = "Resolving focus nodes, constrained properties, and related terms…";
  try {
    const data = await fetchJSON(SERVICES.resolveRule, {
      method: "POST",
      body: JSON.stringify({
        input_type: "rule",
        ontology: apiOntologyInput(o),
        rule: activeBusinessRule(rule),
        inference: apiInferenceOptions(m),
        resolver: { semantic_threshold: 0.60, llm_fallback: true },
      }),
    }, { label: "Resolve data constraint", timeoutMs: 120000 });
    const row = (data.rules || [])[0];
    if (!row) throw new Error("The resolver returned no data-constraint result.");
    lastCandidates = row.candidates || [];
    semanticSearchActive = true;
    byId("clear-related-terms").hidden = false;
    applyResolvedRoles(row, o);
    const count = allResolvedTerms().length;
    byId("ontology-search-status").textContent =
      count
        ? `${count} term${count === 1 ? "" : "s"} resolved via ${resolverSignalLabel(row.resolved_by)}. Select ontology terms to refine the context.`
        : "No ontology terms reached the active resolver score threshold. Add terms manually or revise the data constraint.";
    renderEntities();
    setStatus(count ? `Resolved · ${count}` : "Rule unresolved");
    return count > 0;
  } catch (e) {
    setStatus("Resolution failed");
    byId("ontology-search-status").textContent = `Resolution failed: ${e.message}`;
    return false;
  } finally {
    button.disabled = false;
  }
}

/* ---------- generate ---------- */
async function generateShape() {
  const o = getOntology();
  const rule = byId("business-rule").value.trim();
  if (!o) { setStatus("Load an ontology first"); return; }
  if (!rule) { setStatus("Write a data constraint first"); return; }
  if (!allResolvedTerms().length) {
    const resolved = await resolveBusinessRule();
    if (!resolved) {
      const panel = byId("validation-panel");
      panel.className = "validation-panel shape-error";
      panel.textContent = "The data constraint could not be resolved automatically. Review it or add ontology terms manually before generation.";
      return;
    }
  }
  const primaryTerm = targetRoles.focus_nodes[0]
    || targetRoles.constraint_paths[0]
    || targetRoles.related_terms[0];
  const m = getModels();
  const requestId = makeRequestId();

  ruleGenerationLogId = beginExecutionRun({
    source: "Rule to Shape",
    metadata: ruleExecutionMetadata(o, m, rule, requestId),
  });
  appendExecutionEntry(ruleGenerationLogId, {
    level: "info",
    kind: "rule",
    stage: "rule",
    message: `Data constraint · ${rule || "(empty constraint)"}`,
  });
  TARGET_ROLE_KEYS.forEach((role) => {
    const values = targetRoles[role].map((term) => term.iri || term.full_iri).join(", ");
    appendExecutionEntry(ruleGenerationLogId, {
      level: values ? "info" : "debug",
      stage: "resolution",
      indent: 1,
      message: `${roleLabel(role)}${targetRoles[role].length === 1 ? "" : "s"} · ${values || "none"}`,
    });
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

    const requestedAstreaMode = getAstreaUseMode();
    if (requestedAstreaMode !== "none") {
      appendExecutionEntry(ruleGenerationLogId, {
        level: "info", stage: "astrea", indent: 1,
        message: `Preparing Astrea · ${requestedAstreaMode}`,
      });
      setStatus("Preparing Astrea baseline…");
      panel.className = "validation-panel backend";
      panel.textContent = "Preparing the ontology-derived Astrea baseline before shape generation…";
      const baseline = await ensureAstreaBaseline();
      appendExecutionEntry(ruleGenerationLogId, {
        level: baseline ? "pass" : "warn", stage: "astrea", indent: 1,
        message: baseline
          ? `Astrea baseline ready · ${baseline.shapeCount} shape(s)`
          : "Astrea unavailable · continuing without Astrea",
      });
    }

    setStatus("Generating SHACL shape…");
    panel.className = "validation-panel";
    panel.textContent = "Generating… (this may take a while on the first call).";
    appendExecutionEntry(ruleGenerationLogId, {
      level: "info", stage: "generation", indent: 1,
      message: `Generating one constraint document for ${allResolvedTerms().length} resolved ontology term(s)`,
    });
    const data = await fetchJSON(SERVICES.build, {
      method: "POST",
      body: JSON.stringify({
        ontology: apiOntologyInput(o),
        rule: activeBusinessRule(rule),
        target_roles: {
          focus_nodes: (targetRoles.focus_nodes || []).map(apiTermReference),
          constraint_paths: (targetRoles.constraint_paths || []).map(apiTermReference),
          related_terms: (targetRoles.related_terms || []).map(apiTermReference),
        },
        inference: apiInferenceOptions(m),
        generation: {
          prefixes: o.prefixes,
          base_namespace: o.baseNamespace,
          shape_namespace: o.shapeNamespace,
          shape_prefix: o.shapePrefix,
          domain_context: byId("domain-context").value.trim(),
          generation_guidance: byId("generation-guidance").value.trim(),
          llm_review: true,
          review_max_attempts: 3,
        },
        validation: apiValidationOptions(),
        astrea: apiAstreaOptions(),
      }),
    }, { label: "Generate SHACL shape", timeoutMs: 600000, requestId });
    updateExecutionRun(ruleGenerationLogId, {
      metadata: { requestId: data.request_id || requestId },
    });
    byId("shape-editor").value = data.shape_document || "";
    refreshHighlight("shape-editor");
    scheduleWorkspacePersistence();
    if (data.logs) {
      appendExecutionEntry(ruleGenerationLogId, {
        level: "debug", stage: "backend", indent: 1,
        message: "Backend execution details", details: data.logs,
      });
    }
    if (data.not_found) {
      appendExecutionEntry(ruleGenerationLogId, {
        level: "warn", stage: "generation", indent: 1,
        message: "The model could not justify a shape for this rule context",
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
      if (data.llm_review_applied) {
        appendExecutionEntry(ruleGenerationLogId, {
          level: "pass", stage: "review", indent: 1,
          message: semanticReviewSummary(data),
          details: semanticReviewDetails(data),
        });
      }
      if (data.astrea_merge) {
        const merge = data.astrea_merge;
        appendExecutionEntry(ruleGenerationLogId, {
          level: merge.applied ? "pass" : "warn", stage: "astrea", indent: 1,
          message: merge.applied
            ? `Matching Astrea fragment merged before review · ${merge.strategy}`
            : "Astrea merge not applied",
          details: (merge.warnings || []).join("\n"),
        });
      }
      appendExecutionEntry(ruleGenerationLogId, {
        level: "pass", stage: "syntax", indent: 1, message: "Turtle syntax is valid",
      });
      appendExecutionEntry(ruleGenerationLogId, {
        level: "pass", stage: "grounding", indent: 1,
        message: `Ontology grounding passed · ${allResolvedTerms().length} resolved term(s)`,
      });
      appendExecutionEntry(ruleGenerationLogId, {
        level: "pass", stage: "validation", indent: 1,
        message: `SHACL for SHACL validation passed · ${validationScopeLabel(data)}`,
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
    updateGenerateAvailability();
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
    source: "Rule to Shape",
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
    source: "Rule to Shape",
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
    const label = targetRoles.focus_nodes.map((term) => term.iri).join(", ")
      || targetRoles.constraint_paths.map((term) => term.iri).join(", ")
      || "Data constraint";
    acceptShape(label, shape);
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
    astreaBaseline: currentAstreaBaseline() && currentAstreaBaseline().name,
    astreaUseMode: getAstreaUseMode(),
    astreaMergeTechnique: astreaUsesMerge() ? getAstreaMergeTechnique() : "none",
    focusNodes: targetRoles.focus_nodes.map((term) => term.iri),
    constraintPaths: targetRoles.constraint_paths.map((term) => term.iri),
    relatedTerms: targetRoles.related_terms.map((term) => term.iri),
    requestId,
  };
}

function logRuleGenerationFailure(data = {}) {
  const type = data.error_type || "generation";
  const labels = {
    parse: "Generated Turtle remained invalid after retries",
    grounding: "Generated shape contains IRIs outside the uploaded ontology",
    profile: "Generated shape failed active SHACL for SHACL validation",
    backend: "Model/backend generation failed",
  };
  appendExecutionEntry(ruleGenerationLogId, {
    level: "error", stage: type, indent: 1,
    message: labels[type] || "Shape generation failed",
    details: [semanticReviewDetails(data), data.error || data.message]
      .filter(Boolean).join("\n"),
  });
  appendExecutionEntry(ruleGenerationLogId, {
    level: "error", kind: "summary", stage: "summary",
    message: `Failed · 0 valid shapes · 1 discarded · ${data.attempts || 0} attempt(s)`,
  });
  finishExecutionRun(ruleGenerationLogId, "failed", { shapes: 0, valid: 0, invalid: 1 });
}
