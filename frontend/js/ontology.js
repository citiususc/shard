/* SHARD ontology helpers. */

/* ---------- ontology ---------- */
function getOntology() { return loadJSON(STORE.ontology, null); }
function setOntology(o) { saveJSON(STORE.ontology, o); }

let ontologyEmbeddingPoll = null;
let activeOntologyEmbedding = null;

async function hashOntologyContent(content) {
  const value = String(content || "");
  if (window.crypto && window.crypto.subtle) {
    const bytes = new TextEncoder().encode(value);
    const digest = await window.crypto.subtle.digest("SHA-256", bytes);
    return Array.from(new Uint8Array(digest))
      .map((b) => b.toString(16).padStart(2, "0")).join("");
  }
  let hash = 2166136261;
  for (let i = 0; i < value.length; i++) {
    hash ^= value.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return `fallback-${(hash >>> 0).toString(16)}`;
}

function ontologySummaryText(o, embeddingState) {
  const properties = o.entities.filter((e) => e.type === "property").length;
  let suffix = "";
  if (embeddingState && embeddingState.status === "ready") {
    suffix = " · semantic index ready";
  } else if (embeddingState && ["preparing", "cancelling"].includes(embeddingState.status)) {
    suffix = ` · indexing ${embeddingState.completed || 0}/${embeddingState.total || o.entities.length}`;
  } else if (embeddingState && embeddingState.status === "disabled") {
    suffix = " · semantic ranking disabled";
  } else if (embeddingState && embeddingState.status === "error") {
    suffix = " · semantic index unavailable";
  }
  return `${o.filename} · ${o.entities.length} entities · ${properties} properties${suffix}`;
}

function renderOntologyEmbeddingState(o, state) {
  const summary = byId("ontology-summary");
  if (summary && o) summary.textContent = ontologySummaryText(o, state);
  document.dispatchEvent(new CustomEvent("ontology-embeddings-status", {
    detail: state || { status: "missing" },
  }));
}

function normalizeNamespace(ns) {
  return String(ns || "").trim();
}

function namespaceValidationError(ns) {
  const value = normalizeNamespace(ns);
  if (!value) return "Namespace cannot be empty.";
  if (/\s/.test(value) || !/^[A-Za-z][A-Za-z0-9+.-]*:/.test(value)) {
    return "Use an absolute IRI without spaces.";
  }
  if (!(value.endsWith("#") || value.endsWith("/") || value.endsWith(":"))) {
    return "Namespace must end in #, / or :.";
  }
  return "";
}

function shapesNamespace(baseNs) {
  const ns = normalizeNamespace(baseNs);
  if (!ns) return "";
  if (ns.endsWith("#")) return ns.slice(0, -1) + "/shapes/";
  if (ns.endsWith("/")) return ns + "shapes/";
  if (ns.endsWith(":")) return ns + "shapes:";
  return ns + "/shapes/";
}

function setPrefixLine(prefixBlock, prefix, namespace) {
  if (!namespace) return prefixBlock || "";
  const name = prefix || "";
  const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const pattern = new RegExp(`(^|\\n)@prefix\\s+${escaped}:\\s*<[^>]*>\\s*\\.`, "m");
  const line = `@prefix ${name}: <${namespace}> .`;
  const current = prefixBlock || "";
  if (pattern.test(current)) {
    return current.replace(pattern, (match, lead) => `${lead}${line}`);
  }
  const trimmed = current.trimEnd();
  return trimmed ? `${trimmed}\n${line}\n` : `${line}\n`;
}

function normalizeShapePrefix(prefix) {
  return String(prefix || "").trim().replace(/:$/, "");
}

function shapePrefixValidationError(prefix) {
  const value = normalizeShapePrefix(prefix);
  if (!value) return "Shape prefix cannot be empty.";
  if (!/^[A-Za-z][A-Za-z0-9._-]*$/.test(value)) {
    return "Start with a letter and use only letters, digits, '.', '_' or '-'.";
  }
  return "";
}

function syncPrefixesWithNamespaces(
  prefixBlock, baseNs, shapeNs, shapePrefix = "shape", managedPrefixes = ["onto", "shape"],
) {
  const base = normalizeNamespace(baseNs);
  const shapes = normalizeNamespace(shapeNs) || shapesNamespace(base);
  const preferred = normalizeShapePrefix(shapePrefix) || "shape";
  const managed = new Set(managedPrefixes || []);
  let next = prefixBlock || "";
  if (base && managed.has("onto")) next = setPrefixLine(next, "onto", base);
  if (shapes) next = setPrefixLine(next, preferred, shapes);
  return next;
}

function prefixEntries(prefixBlock) {
  const re = /(?:@prefix|PREFIX)\s+([^:\s]*):\s*<([^>]+)>\s*\.?/gi;
  const entries = [];
  let match;
  while ((match = re.exec(prefixBlock || "")) !== null) {
    entries.push({ prefix: match[1] || "", namespace: match[2] || "" });
  }
  return entries;
}

function prefixNamespace(prefixBlock, prefix) {
  const wanted = String(prefix || "");
  const entry = prefixEntries(prefixBlock).find((item) => item.prefix === wanted);
  return entry ? entry.namespace : "";
}

function removePrefixLine(prefixBlock, prefix) {
  const wanted = String(prefix || "");
  const escaped = wanted.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const pattern = new RegExp(`^\\s*(?:@prefix|PREFIX)\\s+${escaped}:\\s*<[^>]+>\\s*\\.?\\s*$`, "i");
  const hadTrailingNewline = /\r?\n$/.test(prefixBlock || "");
  const next = String(prefixBlock || "").split(/\r?\n/)
    .filter((line) => !pattern.test(line)).join("\n").trimEnd();
  return next ? `${next}${hadTrailingNewline ? "\n" : ""}` : "";
}

function preferredPrefixForNamespace(prefixBlock, namespace, managedPrefixes = []) {
  const managed = new Set(managedPrefixes || []);
  const candidates = prefixEntries(prefixBlock).filter((entry) =>
    entry.prefix && entry.namespace === namespace && entry.prefix !== "onto-sh");
  const sourceCandidates = candidates.filter((entry) => !managed.has(entry.prefix));
  const ranked = (sourceCandidates.length ? sourceCandidates : candidates).sort((left, right) => {
    const score = (entry) => [
      entry.prefix !== "shape",
      !entry.prefix.endsWith("-sh"),
      !entry.prefix.includes("shape"),
      entry.prefix.length,
      entry.prefix,
    ];
    const a = score(left);
    const b = score(right);
    for (let index = 0; index < a.length; index++) {
      if (a[index] < b[index]) return -1;
      if (a[index] > b[index]) return 1;
    }
    return 0;
  });
  if (ranked.length) return ranked[0].prefix;

  const occupied = new Set(prefixEntries(prefixBlock).map((entry) => entry.prefix));
  if (!occupied.has("shape")) return "shape";
  let index = 2;
  while (occupied.has(`shape${index}`)) index += 1;
  return `shape${index}`;
}

function pruneManagedPrefixAliases(prefixBlock, baseNs, shapeNs, shapePrefix, managedPrefixes) {
  const managed = new Set(managedPrefixes || []);
  let prefixes = prefixBlock || "";

  if (managed.has("onto-sh")) {
    prefixes = removePrefixLine(prefixes, "onto-sh");
    managed.delete("onto-sh");
  }

  Array.from(managed).forEach((prefix) => {
    if (prefix !== "onto" && prefix !== shapePrefix) {
      const namespace = prefixNamespace(prefixes, prefix);
      if (namespace === shapeNs) prefixes = removePrefixLine(prefixes, prefix);
      if (!namespace || namespace === shapeNs) managed.delete(prefix);
    }
  });

  const hasSourcePrimaryPrefix = prefixEntries(prefixes).some((entry) =>
    entry.prefix && entry.prefix !== "onto" && entry.namespace === baseNs);
  if (managed.has("onto") && hasSourcePrimaryPrefix) {
    prefixes = removePrefixLine(prefixes, "onto");
    managed.delete("onto");
  }

  return { prefixes, managedPrefixes: Array.from(managed) };
}

function ensureGeneratorPrefixes(
  prefixBlock, baseNs, shapeNs = "", shapePrefix = "shape", managedPrefixes = ["onto", "shape"],
) {
  const base = normalizeNamespace(baseNs);
  const shapes = normalizeNamespace(shapeNs)
    || prefixNamespace(prefixBlock, normalizeShapePrefix(shapePrefix))
    || prefixNamespace(prefixBlock, "shape")
    || prefixNamespace(prefixBlock, "onto-sh")
    || shapesNamespace(base);
  return syncPrefixesWithNamespaces(
    prefixBlock || "", base, shapes, shapePrefix, managedPrefixes,
  );
}

function namespaceCoverage(ontology, namespace) {
  const terms = (ontology && ontology.entities) || [];
  const selected = normalizeNamespace(namespace);
  const count = selected ? terms.filter((entity) => {
    const iri = entity.full_iri || entity.iri || "";
    return String(iri).startsWith(selected);
  }).length : 0;
  return { count, total: terms.length };
}

function namespaceSourceLabel(ontology) {
  const source = ontology && ontology.namespaceSource;
  if (source === "custom") return "Custom";
  if (source === "prefixes") return "From prefixes";
  if (source === "none" || !(ontology && ontology.baseNamespace)) return "Not detected";
  return "Detected";
}

function shapeNamespaceSourceLabel(ontology) {
  const source = ontology && ontology.shapeNamespaceSource;
  if (source === "custom") return "Custom";
  if (source === "prefixes") return "From prefixes";
  if (source === "declared_prefix") return "Declared";
  if (source === "derived") return "Derived";
  return "Not detected";
}

function shapePrefixSourceLabel(ontology) {
  const source = ontology && ontology.shapePrefixSource;
  if (source === "custom") return "Custom";
  if (source === "prefixes") return "From prefixes";
  if (source === "profile") return "From profile";
  if (source === "declared_prefix") return "Declared";
  return "Default";
}

function namespaceSummaryText(ontology) {
  if (!ontology || !ontology.baseNamespace) return "No primary ontology namespace detected.";
  const coverage = namespaceCoverage(ontology, ontology.baseNamespace);
  const detectedBy = ontology.namespaceAnalysis && ontology.namespaceAnalysis.detected_by;
  const detectedLabels = {
    term_coverage: "Detected by term coverage",
    ontology_iri: "Detected from ontology IRI",
    declared_prefix: "Detected from declared prefix",
  };
  const method = ontology.namespaceSource === "custom"
    ? "User-defined"
    : ontology.namespaceSource === "prefixes"
      ? "Synchronized from prefixes"
      : detectedLabels[detectedBy] || "Detected";
  if (coverage.total && !coverage.count) return `${method} · no ontology terms match`;
  return `${method} · ${coverage.count} / ${coverage.total} ontology terms`;
}

function repairOntologyNamespaces(o) {
  if (!o) return o;
  const baseNamespace = normalizeNamespace(o.baseNamespace || "");
  const storedManagedPrefixes = Array.isArray(o.managedNamespacePrefixes)
    ? o.managedNamespacePrefixes
    : ["shape"];
  const shapeNamespace = normalizeNamespace(
    o.shapeNamespace
      || prefixNamespace(o.prefixes || "", normalizeShapePrefix(o.shapePrefix))
      || prefixNamespace(o.prefixes || "", "shape")
      || prefixNamespace(o.prefixes || "", "onto-sh")
      || shapesNamespace(baseNamespace)
  );
  const inferredShapeNamespaceSource = shapeNamespace
    ? (shapeNamespace === shapesNamespace(baseNamespace) ? "derived" : "custom")
    : "none";
  const shapeNamespaceSource = !o.shapeNamespaceSource || o.shapeNamespaceSource === "none"
    ? inferredShapeNamespaceSource
    : o.shapeNamespaceSource;
  const inferredShapePrefix = preferredPrefixForNamespace(
    o.prefixes || "", shapeNamespace, storedManagedPrefixes,
  );
  const shapePrefix = normalizeShapePrefix(o.shapePrefix) || inferredShapePrefix;
  const existingShapeBinding = prefixNamespace(o.prefixes || "", shapePrefix);
  const shapePrefixSource = o.shapePrefixSource || (
    existingShapeBinding === shapeNamespace && !storedManagedPrefixes.includes(shapePrefix)
      ? "declared_prefix" : "default"
  );
  const pruned = pruneManagedPrefixAliases(
    o.prefixes || "", baseNamespace, shapeNamespace, shapePrefix, storedManagedPrefixes,
  );
  const managedNamespacePrefixes = pruned.managedPrefixes;
  const prefixes = ensureGeneratorPrefixes(
    pruned.prefixes, baseNamespace, shapeNamespace, shapePrefix, managedNamespacePrefixes,
  );
  const next = {
    ...o,
    baseNamespace,
    shapeNamespace,
    shapePrefix,
    namespaceSource: o.namespaceSource || (baseNamespace ? "detected" : "none"),
    shapeNamespaceSource,
    shapePrefixSource,
    managedNamespacePrefixes,
    prefixes,
  };
  if (JSON.stringify(next) === JSON.stringify(o)) return o;
  setOntology(next);
  return next;
}

function replacePreferredShapePrefix(ontology, nextPrefix, source) {
  const o = { ...ontology };
  const preferred = normalizeShapePrefix(nextPrefix);
  const error = shapePrefixValidationError(preferred);
  if (error) throw new Error(error);

  const oldPrefix = normalizeShapePrefix(o.shapePrefix);
  const managed = new Set(o.managedNamespacePrefixes || []);
  let prefixes = o.prefixes || "";
  if (oldPrefix && oldPrefix !== preferred
      && prefixNamespace(prefixes, oldPrefix) === o.shapeNamespace) {
    prefixes = removePrefixLine(prefixes, oldPrefix);
    managed.delete(oldPrefix);
  }

  const existing = prefixNamespace(prefixes, preferred);
  if (existing && existing !== o.shapeNamespace) {
    throw new Error(`Prefix '${preferred}' is already bound to ${existing}.`);
  }
  if (!existing) managed.add(preferred);
  prefixes = setPrefixLine(prefixes, preferred, o.shapeNamespace);

  o.shapePrefix = preferred;
  o.shapePrefixSource = source;
  o.managedNamespacePrefixes = Array.from(managed);
  o.prefixes = prefixes;
  return o;
}

function profileShapePrefixCandidate(shapeNamespace) {
  const declarations = getShapeValidationProfiles()
    .map((profile) => profile.content || "").join("\n");
  const candidates = prefixEntries(declarations).filter((entry) =>
    entry.prefix && entry.namespace === shapeNamespace);
  if (!candidates.length) return "";
  const candidateBlock = candidates
    .map((entry) => `@prefix ${entry.prefix}: <${entry.namespace}> .`).join("\n");
  return preferredPrefixForNamespace(candidateBlock, shapeNamespace, []);
}

function synchronizePreferredShapePrefixWithProfiles({ notify = true } = {}) {
  let o = repairOntologyNamespaces(getOntology());
  if (!o || !o.shapeNamespace || o.shapePrefixSource === "custom") return o;
  const candidate = profileShapePrefixCandidate(o.shapeNamespace);

  if (candidate && ["default", "profile"].includes(o.shapePrefixSource || "default")) {
    o = replacePreferredShapePrefix(o, candidate, "profile");
  } else if (!candidate && o.shapePrefixSource === "profile") {
    const managed = new Set(o.managedNamespacePrefixes || []);
    let prefixes = o.prefixes || "";
    if (managed.has(o.shapePrefix)) {
      prefixes = removePrefixLine(prefixes, o.shapePrefix);
      managed.delete(o.shapePrefix);
    }
    const fallback = preferredPrefixForNamespace(prefixes, o.shapeNamespace, Array.from(managed));
    const source = prefixNamespace(prefixes, fallback) === o.shapeNamespace
      ? "declared_prefix" : "default";
    o = replacePreferredShapePrefix({
      ...o, prefixes, managedNamespacePrefixes: Array.from(managed), shapePrefix: "",
    }, fallback, source);
  }

  setOntology(o);
  if (notify) document.dispatchEvent(new CustomEvent("shape-prefix-preference-changed"));
  return o;
}

async function cancelOntologyEmbeddingPreparation(target = activeOntologyEmbedding) {
  if (!target) return;
  if (ontologyEmbeddingPoll) {
    clearTimeout(ontologyEmbeddingPoll);
    ontologyEmbeddingPoll = null;
  }
  if (activeOntologyEmbedding === target) activeOntologyEmbedding = null;
  try {
    await fetchJSON(SERVICES.cancelTerms, {
      method: "POST",
      body: JSON.stringify({
        ontology_hash: target.ontologyHash,
        embedding_model: target.embeddingModel,
        config_fingerprint: target.configFingerprint,
        inference_config: target.inferenceConfig,
      }),
      keepalive: true,
    }, { label: "Cancel ontology embedding preparation", timeoutMs: 5000 });
  } catch { /* The service may already be stopping or the job may be complete. */ }
}

async function pollOntologyEmbeddingStatus(target) {
  if (activeOntologyEmbedding !== target) return;
  try {
    const state = await fetchJSON(SERVICES.termStatus, {
      method: "POST",
      body: JSON.stringify({
        ontology_hash: target.ontologyHash,
        ontology_fingerprint: target.ontologyFingerprint,
        embedding_model: target.embeddingModel,
        config_fingerprint: target.configFingerprint,
        inference_config: target.inferenceConfig,
      }),
    }, { label: "Ontology embedding status", timeoutMs: 10000 });
    if (activeOntologyEmbedding !== target) return;
    const current = getOntology();
    if (!current || current.contentHash !== target.ontologyHash
        || getModels().embeddingModel !== target.embeddingModel
        || modelConfigFingerprint() !== target.configFingerprint) return;
    renderOntologyEmbeddingState(current, state);
    if (state.status === "preparing" || state.status === "cancelling") {
      ontologyEmbeddingPoll = setTimeout(
        () => pollOntologyEmbeddingStatus(target), 1000,
      );
    }
  } catch {
    if (activeOntologyEmbedding === target) {
      ontologyEmbeddingPoll = setTimeout(
        () => pollOntologyEmbeddingStatus(target), 2000,
      );
    }
  }
}

async function prepareOntologyEmbeddings(o) {
  if (!o || !o.entities || !o.entities.length) return;
  const semanticSettings = semanticSettingsStatus();
  if (!semanticSettings.ready) {
    if (activeOntologyEmbedding) {
      await cancelOntologyEmbeddingPreparation(activeOntologyEmbedding);
    }
    renderOntologyEmbeddingState(o, {
      status: "disabled",
      completed: 0,
      total: o.entities.length,
      message: semanticSettings.message,
    });
    return;
  }
  if (!o.contentHash) {
    o.contentHash = await hashOntologyContent(o.content);
    const current = getOntology();
    if (!current || current.content !== o.content) return;
    setOntology(o);
  }

  const embeddingModel = getModels().embeddingModel;
  const inferenceConfig = getInferenceConfig();
  const configFingerprint = modelConfigFingerprint();
  const target = {
    ontologyHash: o.contentHash,
    embeddingModel,
    configFingerprint,
    inferenceConfig,
    payload: {
      ontology_hash: o.contentHash,
      ontology_terms: o.entities,
      embedding_model: embeddingModel,
      config_fingerprint: configFingerprint,
      inference_config: inferenceConfig,
    },
  };

  if (activeOntologyEmbedding
      && (activeOntologyEmbedding.ontologyHash !== target.ontologyHash
          || activeOntologyEmbedding.embeddingModel !== target.embeddingModel
          || activeOntologyEmbedding.configFingerprint !== target.configFingerprint)) {
    await cancelOntologyEmbeddingPreparation(activeOntologyEmbedding);
  }
  activeOntologyEmbedding = target;

  try {
    const state = await fetchJSON(SERVICES.prepareTerms, {
      method: "POST",
      body: JSON.stringify(target.payload),
    }, { label: "Prepare ontology embeddings", timeoutMs: 15000 });
    if (activeOntologyEmbedding !== target) return;
    target.ontologyFingerprint = state.ontology_fingerprint;
    renderOntologyEmbeddingState(o, state);
    if (state.status === "preparing" || state.status === "cancelling") {
      ontologyEmbeddingPoll = setTimeout(
        () => pollOntologyEmbeddingStatus(target), 1000,
      );
    }
  } catch {
    renderOntologyEmbeddingState(o, {
      status: "error",
      message: "Could not start ontology embedding preparation.",
    });
  }
}

/* Wire ontology upload + base-namespace + prefixes. Calls onLoaded(ontology)
   after a successful parse (and once on init if an ontology is already stored). */
async function wireOntologyControls(onLoaded) {
  const fileInput = byId("ontology-file");
  const summary = byId("ontology-summary");
  const nsInput = byId("base-namespace");
  const shapeNsInput = byId("shape-namespace");
  const shapePrefixInput = byId("shape-prefix");
  const namespaceSource = byId("namespace-source");
  const shapeNamespaceSource = byId("shape-namespace-source");
  const shapePrefixSource = byId("shape-prefix-source");
  const namespaceSummary = byId("namespace-summary");
  const namespaceCandidates = byId("namespace-candidates");
  const shapePrefixCandidates = byId("shape-prefix-candidates");
  const prefixEditor = byId("prefixes-editor");
  const resetPrefixes = byId("reset-prefixes");

  const renderNamespaceControls = (o) => {
    if (!o) return;
    if (nsInput) nsInput.value = o.baseNamespace || "";
    if (shapeNsInput) shapeNsInput.value = o.shapeNamespace || "";
    if (shapePrefixInput) shapePrefixInput.value = o.shapePrefix || "shape";
    if (namespaceSource) {
      namespaceSource.textContent = namespaceSourceLabel(o);
      namespaceSource.className = `namespace-source namespace-source-${o.namespaceSource || "none"}`;
    }
    if (shapeNamespaceSource) {
      shapeNamespaceSource.textContent = shapeNamespaceSourceLabel(o);
      shapeNamespaceSource.className = `namespace-source namespace-source-${o.shapeNamespaceSource || "none"}`;
    }
    if (shapePrefixSource) {
      shapePrefixSource.textContent = shapePrefixSourceLabel(o);
      shapePrefixSource.className = `namespace-source namespace-source-${o.shapePrefixSource || "default"}`;
    }
    if (namespaceSummary) {
      const coverage = namespaceCoverage(o, o.baseNamespace);
      namespaceSummary.textContent = namespaceSummaryText(o);
      namespaceSummary.className = `microcopy namespace-summary${coverage.total && !coverage.count ? " namespace-summary-warning" : ""}`;
    }
    if (namespaceCandidates) {
      namespaceCandidates.innerHTML = "";
      const candidates = (o.namespaceAnalysis && o.namespaceAnalysis.candidates) || [];
      candidates.forEach((candidate) => {
        const option = document.createElement("option");
        option.value = candidate.namespace;
        option.label = `${candidate.term_count || 0} ontology term(s)`;
        namespaceCandidates.appendChild(option);
      });
    }
    if (shapePrefixCandidates) {
      shapePrefixCandidates.innerHTML = "";
      const profileCandidate = profileShapePrefixCandidate(o.shapeNamespace);
      const candidates = new Set([
        o.shapePrefix,
        profileCandidate,
        ...prefixEntries(o.prefixes || "")
          .filter((entry) => entry.namespace === o.shapeNamespace)
          .map((entry) => entry.prefix),
      ].filter(Boolean));
      candidates.forEach((prefix) => {
        const option = document.createElement("option");
        option.value = prefix;
        shapePrefixCandidates.appendChild(option);
      });
    }
  };

  const validNamespaceFromInput = (input, label) => {
    const value = normalizeNamespace(input && input.value);
    const error = namespaceValidationError(value);
    if (input) input.setCustomValidity(error);
    if (error) {
      if (input) input.reportValidity();
      setStatus(`${label}: ${error}`);
      return "";
    }
    return value;
  };

  const validShapePrefixFromInput = () => {
    const value = normalizeShapePrefix(shapePrefixInput && shapePrefixInput.value);
    const error = shapePrefixValidationError(value);
    if (shapePrefixInput) shapePrefixInput.setCustomValidity(error);
    if (error) {
      if (shapePrefixInput) shapePrefixInput.reportValidity();
      setStatus(`Preferred shape prefix: ${error}`);
      return "";
    }
    return value;
  };

  const renderFromStore = () => {
    let o = repairOntologyNamespaces(getOntology());
    if (o) o = synchronizePreferredShapePrefixWithProfiles({ notify: false });
    if (!o) return;
    if (summary) summary.textContent = ontologySummaryText(o);
    renderNamespaceControls(o);
    if (prefixEditor) { prefixEditor.value = o.prefixes || ""; refreshHighlight("prefixes-editor"); }
    if (onLoaded) onLoaded(o);
    prepareOntologyEmbeddings(o);
  };

  if (fileInput) {
    fileInput.addEventListener("change", async (ev) => {
      const file = ev.target.files[0];
      if (!file) return;
      const content = await file.text();
      setStatus("Parsing ontology…");
      try {
        const data = await fetchJSON(SERVICES.parse, {
          method: "POST",
          body: JSON.stringify({ filename: file.name, content }),
        }, { label: "Parse ontology", timeoutMs: 30000 });
        if (data.error) throw new Error(data.error);
        const previous = getOntology();
        const contentHash = await hashOntologyContent(content);
        if (previous && previous.contentHash && previous.contentHash !== contentHash) {
          await cancelOntologyEmbeddingPreparation({
            ontologyHash: previous.contentHash,
            embeddingModel: getModels().embeddingModel,
            configFingerprint: modelConfigFingerprint(),
            inferenceConfig: getInferenceConfig(),
          });
        }
        const namespaceAnalysis = data.namespace_analysis || {};
        const baseNamespace = data.base_namespace || "";
        const shapeNamespace = data.shape_namespace || shapesNamespace(baseNamespace);
        const shapePrefix = normalizeShapePrefix(
          data.shape_prefix
            || namespaceAnalysis.shape_prefix
            || preferredPrefixForNamespace(data.prefixes || "", shapeNamespace, []),
        ) || "shape";
        const managedNamespacePrefixes = Array.isArray(namespaceAnalysis.managed_prefixes)
          ? namespaceAnalysis.managed_prefixes
          : ["onto", shapePrefix];
        setOntology({
          filename: file.name, content,
          contentHash,
          baseNamespace,
          shapeNamespace,
          shapePrefix,
          namespaceSource: baseNamespace ? "detected" : "none",
          shapeNamespaceSource: namespaceAnalysis.shape_namespace_source
            || (shapeNamespace ? "derived" : "none"),
          shapePrefixSource: namespaceAnalysis.shape_prefix_source
            || (shapePrefix === "shape" ? "default" : "declared_prefix"),
          namespaceAnalysis,
          managedNamespacePrefixes,
          prefixes: ensureGeneratorPrefixes(
            data.prefixes || "",
            baseNamespace,
            shapeNamespace,
            shapePrefix,
            managedNamespacePrefixes,
          ),
          entities: data.entities || [],
        });
        synchronizePreferredShapePrefixWithProfiles({ notify: false });
        setStatus(`Ontology loaded (${data.entities.length} entities)`);
        renderFromStore();
      } catch (e) {
        setStatus("Parse failed");
        if (summary) summary.textContent = `Could not parse ontology: ${e.message}`;
      }
    });
  }

  if (nsInput) nsInput.addEventListener("change", () => {
    const o = getOntology();
    if (!o) return;
    const baseNamespace = validNamespaceFromInput(nsInput, "Primary ontology namespace");
    if (!baseNamespace) return;
    const oldBaseNamespace = o.baseNamespace || "";
    const derivedShape = ["derived", "none", ""].includes(o.shapeNamespaceSource || "")
      || !o.shapeNamespace
      || o.shapeNamespace === shapesNamespace(oldBaseNamespace);
    o.baseNamespace = baseNamespace;
    o.namespaceSource = "custom";
    if (derivedShape) {
      o.shapeNamespace = shapesNamespace(baseNamespace);
      o.shapeNamespaceSource = "derived";
    }
    o.prefixes = ensureGeneratorPrefixes(
      o.prefixes || "", o.baseNamespace, o.shapeNamespace, o.shapePrefix,
      o.managedNamespacePrefixes,
    );
    setOntology(o);
    renderNamespaceControls(o);
    if (prefixEditor) {
      prefixEditor.value = o.prefixes;
      refreshHighlight("prefixes-editor");
    }
    setStatus("Primary ontology namespace and prefixes synchronized");
  });
  if (shapeNsInput) shapeNsInput.addEventListener("change", () => {
    const o = getOntology();
    if (!o) return;
    const shapeNamespace = validNamespaceFromInput(shapeNsInput, "Generated shapes namespace");
    if (!shapeNamespace) return;
    o.shapeNamespace = shapeNamespace;
    o.shapeNamespaceSource = "custom";
    if (o.shapePrefixSource === "profile"
        && !profileShapePrefixCandidate(shapeNamespace)) {
      o.shapePrefixSource = "custom";
    }
    o.prefixes = ensureGeneratorPrefixes(
      o.prefixes || "", o.baseNamespace, o.shapeNamespace, o.shapePrefix,
      o.managedNamespacePrefixes,
    );
    setOntology(o);
    renderNamespaceControls(o);
    if (prefixEditor) {
      prefixEditor.value = o.prefixes;
      refreshHighlight("prefixes-editor");
    }
    setStatus("Generated shapes namespace and prefixes synchronized");
  });
  if (shapePrefixInput) shapePrefixInput.addEventListener("change", () => {
    const o = getOntology();
    if (!o) return;
    const shapePrefix = validShapePrefixFromInput();
    if (!shapePrefix) return;
    try {
      const next = replacePreferredShapePrefix(o, shapePrefix, "custom");
      setOntology(next);
      renderNamespaceControls(next);
      if (prefixEditor) {
        prefixEditor.value = next.prefixes;
        refreshHighlight("prefixes-editor");
      }
      setStatus("Preferred shape prefix and prefixes synchronized");
    } catch (error) {
      shapePrefixInput.setCustomValidity(error.message);
      shapePrefixInput.reportValidity();
      setStatus(`Preferred shape prefix: ${error.message}`);
    }
  });
  if (prefixEditor) prefixEditor.addEventListener("input", () => {
    const o = getOntology();
    if (!o) return;
    o.prefixes = prefixEditor.value;
    const managedPrefixes = new Set(o.managedNamespacePrefixes || []);
    const baseNamespace = managedPrefixes.has("onto") ? prefixNamespace(o.prefixes, "onto") : "";
    let shapePrefix = normalizeShapePrefix(o.shapePrefix);
    let shapeNamespace = prefixNamespace(o.prefixes, shapePrefix);
    if (!shapeNamespace) {
      shapePrefix = preferredPrefixForNamespace(
        o.prefixes, o.shapeNamespace, o.managedNamespacePrefixes,
      );
      shapeNamespace = prefixNamespace(o.prefixes, shapePrefix);
    }
    if (baseNamespace && !namespaceValidationError(baseNamespace)) {
      o.baseNamespace = baseNamespace;
      o.namespaceSource = "prefixes";
    }
    if (shapeNamespace && !namespaceValidationError(shapeNamespace)) {
      o.shapeNamespace = shapeNamespace;
      o.shapeNamespaceSource = "prefixes";
      o.shapePrefix = shapePrefix;
      o.shapePrefixSource = "prefixes";
    }
    setOntology(o);
    renderNamespaceControls(o);
  });
  if (resetPrefixes) resetPrefixes.addEventListener("click", async () => {
    let o = getOntology(); if (!o) return;
    const customShapePrefix = o.shapePrefixSource === "custom" ? o.shapePrefix : "";
    const data = await fetchJSON(SERVICES.parse, {
      method: "POST",
      body: JSON.stringify({ filename: o.filename, content: o.content }),
    }, { label: "Reset ontology prefixes", timeoutMs: 30000 });
    o.baseNamespace = o.baseNamespace || data.base_namespace || "";
    o.shapeNamespace = o.shapeNamespace || data.shape_namespace || shapesNamespace(o.baseNamespace);
    o.namespaceAnalysis = data.namespace_analysis || o.namespaceAnalysis || {};
    o.managedNamespacePrefixes = Array.isArray(o.namespaceAnalysis.managed_prefixes)
      ? o.namespaceAnalysis.managed_prefixes : ["onto", "shape"];
    o.prefixes = data.prefixes || "";
    o.shapePrefix = normalizeShapePrefix(data.shape_prefix)
      || preferredPrefixForNamespace(o.prefixes, o.shapeNamespace, o.managedNamespacePrefixes);
    o.shapePrefixSource = o.namespaceAnalysis.shape_prefix_source || "default";
    if (customShapePrefix) {
      o = replacePreferredShapePrefix(o, customShapePrefix, "custom");
    } else {
      o.prefixes = ensureGeneratorPrefixes(
        o.prefixes, o.baseNamespace, o.shapeNamespace, o.shapePrefix,
        o.managedNamespacePrefixes,
      );
    }
    setOntology(o);
    renderNamespaceControls(o);
    if (prefixEditor) { prefixEditor.value = o.prefixes; refreshHighlight("prefixes-editor"); }
  });

  document.addEventListener("shape-prefix-preference-changed", () => {
    const o = repairOntologyNamespaces(getOntology());
    if (!o) return;
    renderNamespaceControls(o);
    if (prefixEditor) {
      prefixEditor.value = o.prefixes || "";
      refreshHighlight("prefixes-editor");
    }
  });

  document.addEventListener("embedding-model-changed", async () => {
    const o = getOntology();
    if (!o) return;
    const semanticSettings = semanticSettingsStatus();
    if (activeOntologyEmbedding) {
      await cancelOntologyEmbeddingPreparation(activeOntologyEmbedding);
    }
    if (!semanticSettings.ready) {
      renderOntologyEmbeddingState(o, {
        status: "disabled",
        completed: 0,
        total: o.entities ? o.entities.length : 0,
        message: semanticSettings.message,
      });
      return;
    }
    prepareOntologyEmbeddings(o);
  });

  renderFromStore();
}
