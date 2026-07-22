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
function removeAllAccepted() {
  const count = getAccepted().length;
  if (!count) return 0;
  setAccepted([]);
  notifyAcceptedShapesChanged({ action: "remove_all", count });
  return count;
}

function renderAccepted(listEl, countEl) {
  const list = getAccepted();
  if (countEl) countEl.textContent = `${list.length} accepted`;
  const removeAllButton = byId("remove-all-accepted-shapes");
  if (removeAllButton) removeAllButton.disabled = list.length === 0;
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

function wireAcceptedShapesControls(listEl, countEl, removeAllButton) {
  if (!removeAllButton) return;
  removeAllButton.addEventListener("click", () => {
    const count = getAccepted().length;
    if (!count) return;
    const noun = count === 1 ? "shape" : "shapes";
    if (!window.confirm(
      `Remove all ${count} accepted ${noun}? This action cannot be undone.`
    )) return;
    const removed = removeAllAccepted();
    renderAccepted(listEl, countEl);
    setStatus(`${removed} accepted ${noun} removed`);
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

/* ---------- Astrea baseline evidence + rule-focused merge ---------- */
const ASTREA_BASELINE_REQUEST_TIMEOUT_MS = 30 * 60 * 1000;

const ASTREA_USE_MODES = new Set(["none", "evidence", "merge", "evidence-and-merge"]);
const ASTREA_MERGE_TECHNIQUES = new Set(["generated-priority", "restrictive"]);
let astreaGenerationPromise = null;
let astreaControlState = "idle";
let astreaControlMessage = "";
let volatileAstreaBaselines = {};

function astreaBaselineCache() {
  const stored = loadJSON(STORE.astreaBaselines, {});
  const persistent = stored && typeof stored === "object" && !Array.isArray(stored) ? stored : {};
  return { ...persistent, ...volatileAstreaBaselines };
}

function persistAstreaBaselineCache(cache) {
  // Older versions stored the active baseline twice, which could exhaust localStorage.
  removeStoredValue(STORE.astreaBaseline);
  try {
    saveJSON(STORE.astreaBaselines, cache);
    volatileAstreaBaselines = {};
    return true;
  } catch (error) {
    if (error && (error.name === "QuotaExceededError" || error.code === 22)) {
      volatileAstreaBaselines = { ...cache };
      return false;
    }
    throw error;
  }
}

function normalizedAstreaBaseline(value) {
  if (!value || !value.content) return null;
  const ontologyHash = value.ontologyHash || value.ontology_hash;
  return ontologyHash ? { ...value, ontologyHash } : null;
}

function migrateLegacyAstreaBaseline() {
  const legacy = normalizedAstreaBaseline(loadJSON(STORE.astreaBaseline, null));
  if (!legacy) return;
  const cache = astreaBaselineCache();
  if (!cache[legacy.ontologyHash]) {
    cache[legacy.ontologyHash] = legacy;
    persistAstreaBaselineCache(cache);
  } else {
    removeStoredValue(STORE.astreaBaseline);
  }
}

function getAstreaBaselines() {
  migrateLegacyAstreaBaseline();
  return Object.values(astreaBaselineCache()).filter((value) => normalizedAstreaBaseline(value));
}

function importAstreaBaselines(values) {
  const incoming = Array.isArray(values) ? values : Object.values(values || {});
  const cache = astreaBaselineCache();
  incoming.forEach((value) => {
    const baseline = normalizedAstreaBaseline(value);
    if (baseline) cache[baseline.ontologyHash] = baseline;
  });
  persistAstreaBaselineCache(cache);
}

function getAstreaBaseline() {
  migrateLegacyAstreaBaseline();
  const ontology = getOntology();
  if (!ontology || !ontology.contentHash) return null;
  return normalizedAstreaBaseline(astreaBaselineCache()[ontology.contentHash]);
}

function setAstreaBaseline(value) {
  const baseline = normalizedAstreaBaseline(value);
  const cache = astreaBaselineCache();
  if (baseline) {
    cache[baseline.ontologyHash] = baseline;
    return persistAstreaBaselineCache(cache);
  }

  const ontology = getOntology();
  if (ontology && ontology.contentHash && cache[ontology.contentHash]) {
    delete cache[ontology.contentHash];
    persistAstreaBaselineCache(cache);
  }
  removeStoredValue(STORE.astreaBaseline);
  return true;
}

function migrateLegacyAstreaSettings() {
  if (!localStorage.getItem(STORE.astreaUseMode)) {
    const legacyMode = localStorage.getItem(STORE.astreaMergeMode) || "none";
    const hasBaseline = Boolean(getAstreaBaseline() && getAstreaBaseline().content);
    const useMode = hasBaseline
      ? (ASTREA_MERGE_TECHNIQUES.has(legacyMode) ? "evidence-and-merge" : "evidence")
      : "none";
    localStorage.setItem(STORE.astreaUseMode, useMode);
  }
  if (!localStorage.getItem(STORE.astreaMergeTechnique)) {
    const legacyMode = localStorage.getItem(STORE.astreaMergeMode) || "";
    const technique = ASTREA_MERGE_TECHNIQUES.has(legacyMode)
      ? legacyMode
      : "generated-priority";
    localStorage.setItem(STORE.astreaMergeTechnique, technique);
  }
}

function getAstreaUseMode() {
  migrateLegacyAstreaSettings();
  const stored = localStorage.getItem(STORE.astreaUseMode) || "none";
  const value = ({ baseline: "evidence", both: "evidence-and-merge" })[stored] || stored;
  if (value !== stored) localStorage.setItem(STORE.astreaUseMode, value);
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
  const stored = localStorage.getItem(STORE.astreaMergeTechnique) || "generated-priority";
  const value = stored === "priority-llm" ? "generated-priority" : stored;
  if (value !== stored) localStorage.setItem(STORE.astreaMergeTechnique, value);
  return ASTREA_MERGE_TECHNIQUES.has(value) ? value : "generated-priority";
}
function setAstreaMergeTechnique(value, { render = true } = {}) {
  const normalized = ASTREA_MERGE_TECHNIQUES.has(value) ? value : "generated-priority";
  localStorage.setItem(STORE.astreaMergeTechnique, normalized);
  if (render) renderAstreaBaselineControls();
  return normalized;
}
function astreaUsesEvidence(mode = getAstreaUseMode()) {
  return mode === "evidence" || mode === "evidence-and-merge";
}
function astreaUsesMerge(mode = getAstreaUseMode()) {
  return mode === "merge" || mode === "evidence-and-merge";
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
  const payload = {
    id: baseline.id,
    name: baseline.name,
    size: baseline.size,
    content: baseline.content,
    source: baseline.source,
    ontology_hash: baseline.ontologyHash,
  };
  if (Object.prototype.hasOwnProperty.call(baseline, "mergeContent")) {
    payload.merge_content = baseline.mergeContent || "";
  }
  return payload;
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
      merge: "Merge the matching Astrea fragment into each generated shape before human review",
      both: "Use matching Astrea shapes as evidence and merge them before human review",
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
    (state === "warning" ? " astrea-status-warning" : "") +
    (state === "ready" ? " astrea-status-ready" : "");
}

function astreaViolationCount(validation = {}) {
  const structured = Number(validation.violation_count);
  if (Number.isFinite(structured) && structured >= 0) return structured;
  const report = String(validation.report_text || validation.message || validation.error || "");
  const resultCount = report.match(/Results\s*\((\d+)\)/i);
  if (resultCount) return Number(resultCount[1]);
  const occurrences = report.match(/Constraint Violation in /g);
  return occurrences ? occurrences.length : 0;
}

function astreaProfileMessage(
  validation = {},
  { guardedMerge = false, evidenceAvailable = false } = {},
) {
  const count = astreaViolationCount(validation);
  const countText = count ? `; ${count} violation${count === 1 ? "" : "s"} found` : "";
  const policy = guardedMerge
    ? " The baseline remains available as evidence. Focused merges are validated separately; non-conforming fragments are skipped."
    : evidenceAvailable ? " The syntactically valid baseline remains available as generation evidence." : "";
  return `Astrea generated the baseline, but it did not conform to the active SHACL-for-SHACL profile${countText}.${policy}`;
}

function astreaFailureMessage(error) {
  const payload = error && error.payload;
  const validation = (payload && payload.validation)
    || (payload && payload.details && payload.details.validation)
    || null;
  if (validation && (validation.error_type === "profile" || validation.profile_valid === false)) {
    return astreaProfileMessage(validation);
  }
  const code = String((payload && payload.code) || "");
  const detail = String(
    (payload && (payload.message || payload.error))
    || (error && error.message)
    || "",
  ).trim();
  const suffix = detail ? ` ${detail}` : "";

  if (code === "ASTREA_REQUEST_TIMEOUT" || /timed out/i.test(detail)) {
    return `Astrea did not finish before the configured timeout. Astrea use was set to No.${suffix}`;
  }
  if (code === "ASTREA_RATE_LIMIT_EXCEEDED") {
    return `Astrea rate limited the request. Astrea use was set to No.${suffix}`;
  }
  if (code === "ASTREA_UNAVAILABLE" || (payload && payload.error_type === "astrea_unavailable")) {
    return `Astrea is currently unavailable. Astrea use was set to No.${suffix}`;
  }
  if (code === "ASTREA_INVALID_RESPONSE") {
    return `Astrea returned no usable SHACL baseline. Astrea use was set to No.${suffix}`;
  }
  return `Astrea could not generate a usable baseline. Astrea use was set to No.${suffix}`;
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
  if (cached) {
    const hasSeparateMergeDocument = Boolean(cached.mergeContent);
    const mergeSafe = cached.mergeSafe !== false && (
      hasSeparateMergeDocument
        ? cached.mergeValidation?.profile_valid !== false
        : cached.validation?.profile_valid !== false
    );
    if (!mergeSafe) {
      setAstreaControlMessage(
        astreaProfileMessage(cached.validation || {}, {
          guardedMerge: astreaUsesMerge(),
          evidenceAvailable: true,
        }),
        "warning",
      );
      renderAstreaBaselineControls();
    } else if (cached.validation?.profile_valid === false) {
      const retained = Number(cached.normalization?.retained_shapes || 0);
      const quarantined = Number(cached.normalization?.quarantined_shapes || 0);
      setAstreaControlMessage(
        `Astrea baseline normalized · ${retained} conforming fragment(s) ready for merge` +
          (quarantined ? ` · ${quarantined} quarantined` : "") + ".",
        "warning",
      );
      renderAstreaBaselineControls();
    }
    return { ...cached, mergeSafe };
  }
  if (astreaGenerationPromise) return astreaGenerationPromise;

  setAstreaControlMessage("Generating baseline from the loaded ontology…", "busy");
  renderAstreaBaselineControls();
  let ontologyChanged = false;
  astreaGenerationPromise = (async () => {
    try {
      const result = await fetchJSON(SERVICES.astrea, {
        method: "POST",
        body: JSON.stringify({ ontology: apiOntologyInput(ontology) }),
      }, {
        label: "Generate Astrea baseline",
        timeoutMs: ASTREA_BASELINE_REQUEST_TIMEOUT_MS,
      });
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
        mergeContent: result.merge_shape_document || "",
        quarantinedContent: result.quarantined_shape_document || "",
        source: "astrea-api",
        ontologyHash: result.ontology_hash || ontology.contentHash,
        shapeCount: result.shape_count || 0,
        nodeShapeCount: result.node_shape_count || 0,
        propertyShapeCount: result.property_shape_count || 0,
        partial: Boolean(result.partial),
        validation: result.validation || null,
        mergeValidation: result.merge_validation || null,
        evidenceSafe: result.evidence_safe !== false,
        mergeSafe: result.merge_safe !== false &&
          Boolean(result.merge_shape_document) &&
          result.merge_validation?.profile_valid !== false,
        normalization: result.normalization || null,
        warnings: Array.isArray(result.warnings) ? result.warnings : [],
        generatedAt: new Date().toISOString(),
      };
      const persisted = setAstreaBaseline(baseline);
      const qualifier = baseline.partial ? " (partial response)" : "";
      if (!baseline.mergeSafe) {
        setAstreaControlMessage(
          astreaProfileMessage(baseline.validation || {}, {
            guardedMerge: astreaUsesMerge(),
            evidenceAvailable: true,
          }),
          "warning",
        );
        setStatus(astreaUsesMerge()
          ? "Astrea evidence ready · guarded merge"
          : "Astrea evidence ready");
      } else if (baseline.validation?.profile_valid === false) {
        const retained = Number(baseline.normalization?.retained_shapes || 0);
        const quarantined = Number(baseline.normalization?.quarantined_shapes || 0);
        setAstreaControlMessage(
          `Astrea baseline normalized · ${retained} conforming fragment(s) ready for merge` +
            (quarantined ? ` · ${quarantined} quarantined` : "") + ".",
          "warning",
        );
        setStatus(`Astrea normalized · ${retained} merge-ready`);
      } else if (!persisted) {
        setAstreaControlMessage(
          `${baseline.shapeCount} Astrea shape(s) ready${qualifier}. Browser storage is full; the baseline is available for this page but was not cached.`,
          "warning",
        );
        setStatus(`Astrea ready · ${baseline.shapeCount} · not cached`);
      } else {
        setAstreaControlMessage(
          `${baseline.shapeCount} Astrea shape(s) ready${qualifier}.`,
          "ready",
        );
        setStatus(`Astrea ready · ${baseline.shapeCount}`);
      }
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
  setAstreaControlMessage("", "idle");
  renderAstreaBaselineControls();
  if (!getOntology()) return null;
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
      if (baseline) {
        if (baseline.mergeSafe === false) {
          setStatus(astreaUsesMerge()
            ? "Astrea evidence ready · guarded merge"
            : "Astrea evidence ready");
        } else {
          setStatus(`Astrea ready · ${baseline.shapeCount}`);
        }
      }
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

function semanticReviewSummary(data = {}) {
  const review = data.semantic_review || data.semanticReview || {};
  if (review.status !== "passed") return "Semantic review not passed";
  const checks = Number(review.critic_calls || 0);
  const corrections = Number(review.correction_count || 0);
  return `Semantic critique passed · ${checks} check(s) · ${corrections} correction(s)`;
}

function semanticReviewDetails(data = {}) {
  const review = data.semantic_review || data.semanticReview || {};
  return (review.issues || []).map((issue) => {
    const path = issue.path ? `${issue.path}: ` : "";
    return `${issue.code || "SEMANTIC_REVIEW_ISSUE"} · ${path}${issue.message || ""}`;
  }).join("\n");
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
