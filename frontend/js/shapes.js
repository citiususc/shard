/* SHARD shapes helpers. */

/* ---------- accepted shapes (shared across pages) ---------- */
function getAccepted() { return loadJSON(STORE.accepted, []); }
function setAccepted(list) { saveJSON(STORE.accepted, list); }
function notifyAcceptedShapesChanged(detail = {}) {
  window.dispatchEvent(new CustomEvent("accepted-shapes-changed", {
    detail: { accepted: getAccepted(), ...detail },
  }));
}

function acceptShape(property, shape) {
  const list = getAccepted();
  const id = "shp-" + Date.now() + "-" + Math.random().toString(36).slice(2, 6);
  list.push({ id, property: property || "(shape)", shape });
  setAccepted(list);
  notifyAcceptedShapesChanged({ action: "accept", id });
  return id;
}
function removeAccepted(id) {
  setAccepted(getAccepted().filter((s) => s.id !== id));
  notifyAcceptedShapesChanged({ action: "remove", id });
}

function renderAccepted(listEl, countEl) {
  const list = getAccepted();
  if (countEl) countEl.textContent = `${list.length} accepted`;
  if (!listEl) return;
  listEl.innerHTML = "";
  list.forEach((s) => {
    const row = document.createElement("div");
    row.className = "accepted-item";
    row.innerHTML = `<code>${esc(s.property)}</code>`;
    const del = document.createElement("button");
    del.className = "text-button accepted-remove-button"; del.textContent = "Remove";
    del.addEventListener("click", () => { removeAccepted(s.id); renderAccepted(listEl, countEl); });
    row.appendChild(del);
    listEl.appendChild(row);
  });
}

/* ---------- generated-shape validation profiles ---------- */
function getShapeValidationProfiles() { return loadJSON(STORE.shapeProfiles, []); }
function setShapeValidationProfiles(list) { saveJSON(STORE.shapeProfiles, Array.isArray(list) ? list : []); }
function removeShapeValidationProfile(id) {
  const next = getShapeValidationProfiles().filter((profile) => (profile.id || profile.name) !== id);
  setShapeValidationProfiles(next);
  renderShapeValidationProfiles();
  synchronizePreferredShapePrefixWithProfiles();
  setStatus("Shape validation profile file removed");
}

function shapeProfileSummary() {
  const profiles = getShapeValidationProfiles();
  if (!profiles.length) return "Syntax + generic SHACL for SHACL · no domain profile loaded.";
  return `Syntax + generic SHACL for SHACL + ${profiles.length} domain profile${profiles.length === 1 ? "" : "s"}: ${profiles.map((p) => p.name).join(", ")}`;
}

function renderShapeValidationProfiles() {
  const listEl = byId("shape-profile-list");
  const clearBtn = byId("clear-shape-profile");
  if (!listEl) return;
  const profiles = getShapeValidationProfiles();
  if (clearBtn) clearBtn.disabled = profiles.length === 0;
  if (!profiles.length) {
    listEl.innerHTML = `<p class="microcopy" title="Generic SHACL for SHACL validation is always active; no domain profile is loaded.">Generic SHACL for SHACL active.</p>`;
    return;
  }
  listEl.innerHTML = "";
  profiles.forEach((profile) => {
    const row = document.createElement("div");
    row.className = "profile-file-item";
    row.title = profile.name;
    const size = profile.size || String(profile.content || "").length;
    const id = profile.id || profile.name;
    row.innerHTML =
      `<code>${esc(profile.name)}</code>` +
      `<span>${Math.max(1, Math.round(size / 1024))} KB</span>` +
      `<button class="profile-file-remove" type="button" title="Remove">×</button>`;
    const removeBtn = row.querySelector(".profile-file-remove");
    removeBtn.setAttribute("aria-label", `Remove ${profile.name}`);
    removeBtn.addEventListener("click", () => removeShapeValidationProfile(id));
    listEl.appendChild(row);
  });
}

function wireShapeValidationProfileControls() {
  const input = byId("shape-profile-files");
  const clearBtn = byId("clear-shape-profile");
  renderShapeValidationProfiles();
  if (input) {
    input.addEventListener("change", async (ev) => {
      const files = Array.from(ev.target.files || []);
      if (!files.length) return;
      try {
        const incoming = await Promise.all(files.map(async (file) => ({
          id: `${file.name}-${file.size}-${file.lastModified}`,
          name: file.name,
          size: file.size,
          content: await file.text(),
          loadedAt: new Date().toISOString(),
        })));
        const existing = getShapeValidationProfiles();
        const byKey = new Map(existing.map((profile) => [profile.id || profile.name, profile]));
        incoming.forEach((profile) => byKey.set(profile.id || profile.name, profile));
        setShapeValidationProfiles(Array.from(byKey.values()));
        renderShapeValidationProfiles();
        synchronizePreferredShapePrefixWithProfiles();
        setStatus(`Loaded ${incoming.length} shape validation profile file(s)`);
      } catch (e) {
        setStatus(`Could not load validation profile: ${e.message}`);
      } finally {
        input.value = "";
      }
    });
  }
  if (clearBtn) {
    clearBtn.addEventListener("click", () => {
      setShapeValidationProfiles([]);
      renderShapeValidationProfiles();
      synchronizePreferredShapePrefixWithProfiles();
      setStatus("Shape validation profile cleared");
    });
  }
}

/* ---------- Astrea baseline evidence + final merge ---------- */
const ASTREA_USE_MODES = new Set(["none", "baseline", "merge", "both"]);
const ASTREA_MERGE_TECHNIQUES = new Set(["priority-llm", "restrictive"]);
let astreaGenerationPromise = null;
let astreaControlState = "idle";
let astreaControlMessage = "";

function getAstreaBaseline() { return loadJSON(STORE.astreaBaseline, null); }
function setAstreaBaseline(value) {
  if (value && value.content) saveJSON(STORE.astreaBaseline, value);
  else localStorage.removeItem(STORE.astreaBaseline);
}

function migrateLegacyAstreaSettings() {
  if (!localStorage.getItem(STORE.astreaUseMode)) {
    const legacyMode = localStorage.getItem(STORE.astreaMergeMode) || "none";
    const hasBaseline = Boolean(getAstreaBaseline() && getAstreaBaseline().content);
    const useMode = hasBaseline
      ? (ASTREA_MERGE_TECHNIQUES.has(legacyMode) ? "both" : "baseline")
      : "none";
    localStorage.setItem(STORE.astreaUseMode, useMode);
  }
  if (!localStorage.getItem(STORE.astreaMergeTechnique)) {
    const legacyMode = localStorage.getItem(STORE.astreaMergeMode) || "";
    const technique = ASTREA_MERGE_TECHNIQUES.has(legacyMode)
      ? legacyMode
      : "priority-llm";
    localStorage.setItem(STORE.astreaMergeTechnique, technique);
  }
}

function getAstreaUseMode() {
  migrateLegacyAstreaSettings();
  const value = localStorage.getItem(STORE.astreaUseMode) || "none";
  return ASTREA_USE_MODES.has(value) ? value : "none";
}
function setAstreaUseMode(value, { render = true } = {}) {
  const normalized = ASTREA_USE_MODES.has(value) ? value : "none";
  localStorage.setItem(STORE.astreaUseMode, normalized);
  if (render) renderAstreaBaselineControls();
  return normalized;
}
function getAstreaMergeTechnique() {
  migrateLegacyAstreaSettings();
  const value = localStorage.getItem(STORE.astreaMergeTechnique) || "priority-llm";
  return ASTREA_MERGE_TECHNIQUES.has(value) ? value : "priority-llm";
}
function setAstreaMergeTechnique(value, { render = true } = {}) {
  const normalized = ASTREA_MERGE_TECHNIQUES.has(value) ? value : "priority-llm";
  localStorage.setItem(STORE.astreaMergeTechnique, normalized);
  if (render) renderAstreaBaselineControls();
  return normalized;
}
function astreaUsesEvidence(mode = getAstreaUseMode()) {
  return mode === "baseline" || mode === "both";
}
function astreaUsesMerge(mode = getAstreaUseMode()) {
  return mode === "merge" || mode === "both";
}

// Compatibility for version-1 sessions that stored merge and use as one value.
function getAstreaMergeMode() {
  return astreaUsesMerge() ? getAstreaMergeTechnique() : "none";
}
function setAstreaMergeMode(value) {
  if (ASTREA_MERGE_TECHNIQUES.has(value)) {
    setAstreaMergeTechnique(value, { render: false });
    setAstreaUseMode(getAstreaBaseline() ? "both" : "merge");
  } else {
    setAstreaUseMode(getAstreaBaseline() ? "baseline" : "none");
  }
}

function currentAstreaBaseline() {
  const baseline = getAstreaBaseline();
  const ontology = getOntology();
  if (!baseline || !baseline.content || !ontology || !ontology.content) return null;
  if (baseline.source !== "astrea-api") return null;
  if (!ontology.contentHash || baseline.ontologyHash !== ontology.contentHash) return null;
  return baseline;
}

function astreaBaselinePayload() {
  const baseline = currentAstreaBaseline();
  if (!baseline) return null;
  return {
    id: baseline.id,
    name: baseline.name,
    size: baseline.size,
    content: baseline.content,
    source: baseline.source,
    ontology_hash: baseline.ontologyHash,
  };
}

function astreaEvidencePayload() {
  return astreaUsesEvidence() ? astreaBaselinePayload() : null;
}

function setAstreaControlMessage(message, state = "idle") {
  astreaControlMessage = String(message || "");
  astreaControlState = state;
}

function renderAstreaBaselineControls() {
  const mode = getAstreaUseMode();
  const technique = getAstreaMergeTechnique();
  const baseline = currentAstreaBaseline();
  const useControl = byId("astrea-use-mode");
  const mergeControl = byId("astrea-merge-technique");
  const mergeRow = byId("astrea-merge-controls");
  const status = byId("astrea-baseline-status");
  const busy = astreaControlState === "busy";

  if (useControl) {
    useControl.value = mode;
    useControl.disabled = busy;
    useControl.title = {
      none: "Do not use Astrea",
      baseline: "Send the ontology to external Astrea and use its shapes only as generation evidence",
      merge: "Send the ontology to external Astrea and use its shapes only for the final output merge",
      both: "Send the ontology to external Astrea and use its shapes as evidence and for the final merge",
    }[mode];
  }
  if (mergeControl) {
    mergeControl.value = technique;
    mergeControl.disabled = busy;
    mergeControl.title = technique === "restrictive"
      ? "Keep the strongest compatible constraints from SHARD and Astrea"
      : "Prefer SHARD for covered targets and use Astrea as fallback";
  }
  if (mergeRow) mergeRow.hidden = !astreaUsesMerge(mode);
  if (!status) return;

  let message = astreaControlMessage;
  let state = astreaControlState;
  if (!message && busy) {
    message = "Generating baseline from the loaded ontology…";
  } else if (!message && mode === "none") {
    message = "Astrea is not used.";
  } else if (!message && baseline) {
    message = baseline.shapeCount
      ? `${baseline.shapeCount} Astrea shape(s) ready.`
      : "Astrea baseline ready.";
    state = "ready";
  } else if (!message && !getOntology()) {
    message = "Load an ontology before enabling Astrea.";
  } else if (!message) {
    message = "Astrea baseline will be generated from the loaded ontology.";
  }
  status.textContent = message;
  status.title = message;
  status.className = "microcopy astrea-baseline-status" +
    (state === "error" ? " astrea-status-error" : "") +
    (state === "ready" ? " astrea-status-ready" : "");
}

function astreaFailureMessage(error) {
  const payload = error && error.payload;
  if (payload && payload.error_type === "astrea_unavailable") {
    return "Astrea is currently unavailable. Astrea use was set to No.";
  }
  const detail = payload && (payload.error || payload.message);
  return `Astrea could not generate a usable baseline. Astrea use was set to No.${detail ? ` ${detail}` : ""}`;
}

async function ensureAstreaBaseline() {
  if (getAstreaUseMode() === "none") return null;
  let ontology = getOntology();
  if (!ontology || !ontology.content) {
    setAstreaBaseline(null);
    setAstreaUseMode("none", { render: false });
    setAstreaControlMessage("Load an ontology before enabling Astrea.", "error");
    renderAstreaBaselineControls();
    return null;
  }

  if (!ontology.contentHash) {
    ontology = { ...ontology, contentHash: await hashOntologyContent(ontology.content) };
    setOntology(ontology);
  }
  const cached = currentAstreaBaseline();
  if (cached) return cached;
  if (astreaGenerationPromise) return astreaGenerationPromise;

  setAstreaControlMessage("Generating baseline from the loaded ontology…", "busy");
  renderAstreaBaselineControls();
  let ontologyChanged = false;
  astreaGenerationPromise = (async () => {
    try {
      const result = await fetchJSON(SERVICES.astrea, {
        method: "POST",
        body: JSON.stringify({
          ontology_content: ontology.content,
          ontology_filename: ontology.filename || "ontology.ttl",
          ontology_hash: ontology.contentHash,
        }),
      }, { label: "Generate Astrea baseline", timeoutMs: 150000 });
      if (!result.available || !result.shape_document) {
        throw new Error(result.error || "Astrea returned no baseline shapes.");
      }
      if (!getOntology() || getOntology().contentHash !== ontology.contentHash) {
        ontologyChanged = true;
        setAstreaControlMessage("Ontology changed · regenerating Astrea baseline…", "busy");
        return null;
      }
      const baseline = {
        id: `astrea-${result.ontology_hash || ontology.contentHash}`,
        name: result.name || "astrea_baseline.ttl",
        size: result.size || result.shape_document.length,
        content: result.shape_document,
        source: "astrea-api",
        ontologyHash: result.ontology_hash || ontology.contentHash,
        shapeCount: result.shape_count || 0,
        nodeShapeCount: result.node_shape_count || 0,
        propertyShapeCount: result.property_shape_count || 0,
        partial: Boolean(result.partial),
        validation: result.validation || null,
        generatedAt: new Date().toISOString(),
      };
      setAstreaBaseline(baseline);
      const qualifier = baseline.partial ? " (partial response)" : "";
      setAstreaControlMessage(
        `${baseline.shapeCount} Astrea shape(s) ready${qualifier}.`,
        "ready",
      );
      setStatus(`Astrea ready · ${baseline.shapeCount}`);
      return baseline;
    } catch (error) {
      if (ontologyChanged) return null;
      setAstreaBaseline(null);
      setAstreaUseMode("none", { render: false });
      const message = astreaFailureMessage(error);
      setAstreaControlMessage(message, "error");
      setStatus(message);
      return null;
    } finally {
      astreaGenerationPromise = null;
      renderAstreaBaselineControls();
      if (ontologyChanged && getAstreaUseMode() !== "none") {
        void ensureAstreaBaseline();
      }
    }
  })();
  return astreaGenerationPromise;
}

async function refreshAstreaBaselineForOntology() {
  if (getAstreaBaseline() && !currentAstreaBaseline()) setAstreaBaseline(null);
  setAstreaControlMessage("", "idle");
  renderAstreaBaselineControls();
  if (getAstreaUseMode() !== "none") return ensureAstreaBaseline();
  return null;
}

function wireAstreaBaselineControls() {
  const useControl = byId("astrea-use-mode");
  const mergeControl = byId("astrea-merge-technique");
  migrateLegacyAstreaSettings();
  renderAstreaBaselineControls();

  if (useControl) {
    useControl.addEventListener("change", async () => {
      setAstreaControlMessage("", "idle");
      const mode = setAstreaUseMode(useControl.value, { render: false });
      renderAstreaBaselineControls();
      if (mode === "none") {
        setStatus("Astrea disabled");
        return;
      }
      const baseline = await ensureAstreaBaseline();
      if (baseline) setStatus(`Astrea ready · ${baseline.shapeCount}`);
    });
  }
  if (mergeControl) {
    mergeControl.addEventListener("change", () => {
      const technique = setAstreaMergeTechnique(mergeControl.value);
      setStatus(`Astrea merge strategy: ${technique === "restrictive" ? "More restrictive" : "Priority LLM"}`);
    });
  }
  if (getAstreaUseMode() !== "none" && getOntology()) {
    void ensureAstreaBaseline();
  }
}

function activeValidationScopeLabel() {
  const profiles = getShapeValidationProfiles();
  if (!profiles.length) return "syntax + generic SHACL for SHACL";
  return `syntax + generic SHACL for SHACL + profile: ${profiles.map((p) => p.name).join(", ")}`;
}

function validationScopeLabel(data = {}) {
  const domainNames = Array.isArray(data.domain_profile_names) ? data.domain_profile_names : [];
  const domainCount = Number(data.domain_profile_count || domainNames.length || 0);
  if (data.validation_level || data.generic_profile_active || data.profile_count != null) {
    if (!domainCount) return "syntax + generic SHACL for SHACL";
    return `syntax + generic SHACL for SHACL + profile: ${domainNames.join(", ") || `${domainCount} file${domainCount === 1 ? "" : "s"}`}`;
  }
  return activeValidationScopeLabel();
}

function validationResultMessage(data) {
  const scope = validationScopeLabel(data);
  if (data.valid) {
    return `Valid Turtle / SHACL. Validation OK: ${scope}.`;
  }
  if (data.syntax_valid === false) return `Shape/Turtle parse error:\n${data.error}`;
  if (data.profile_valid === false) {
    const report = data.report_text || data.error || "";
    return `SHACL for SHACL validation failed (${scope}):\n${report}`;
  }
  return `Shape/Turtle validation error:\n${data.error || data.message || "Unknown validation error"}`;
}
