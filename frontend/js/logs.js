/* SHARD logs helpers. */

const EXECUTION_LOG_VERSION = 2;
const MAX_EXECUTION_RUNS = 5;
const MAX_EXECUTION_ENTRIES = 800;
const MAX_EXECUTION_DETAIL_LENGTH = 12000;

function emptyExecutionLogState() {
  return { version: EXECUTION_LOG_VERSION, activeRunId: "", runs: [] };
}

function levelFromLegacyLine(line) {
  if (line.includes("[ERROR]")) return "error";
  if (line.includes("[WARN]")) return "warn";
  if (line.includes("[DEBUG]")) return "debug";
  if (line.includes("[INFO]")) return "info";
  return "info";
}

function loadExecutionLogState() {
  const raw = loadJSON(STORE.executionLogs, null);
  if (raw && raw.version === EXECUTION_LOG_VERSION && Array.isArray(raw.runs)) return raw;
  if (!raw || !raw.content) return emptyExecutionLogState();

  const timestamp = raw.updatedAt || new Date().toISOString();
  const run = {
    id: `legacy-${Date.now().toString(36)}`,
    source: raw.source || "Generation",
    startedAt: timestamp,
    endedAt: timestamp,
    status: "completed",
    metadata: {},
    entries: String(raw.content).split("\n").filter(Boolean).map((line) => ({
      timestamp,
      level: levelFromLegacyLine(line),
      kind: "entry",
      stage: "legacy",
      indent: 0,
      message: line.replace(/^\[(?:ERROR|WARN|INFO|DEBUG)\]\s*/, ""),
      details: "",
    })),
  };
  return { version: EXECUTION_LOG_VERSION, activeRunId: "", runs: [run] };
}

function saveExecutionLogState(state) {
  state.runs = (state.runs || []).slice(-MAX_EXECUTION_RUNS);
  saveJSON(STORE.executionLogs, state);
}

function executionRun(state, runId) {
  return (state.runs || []).find((run) => run.id === runId) || null;
}

function beginExecutionRun(config = {}) {
  const state = loadExecutionLogState();
  const now = new Date().toISOString();
  const run = {
    id: makeRequestId(),
    source: String(config.source || "Generation"),
    startedAt: now,
    endedAt: "",
    status: "running",
    metadata: { ...(config.metadata || {}) },
    entries: [],
  };
  state.runs.push(run);
  state.activeRunId = run.id;
  saveExecutionLogState(state);
  renderExecutionLogs(state);
  return run.id;
}

function updateExecutionRun(runId, patch = {}) {
  const state = loadExecutionLogState();
  const run = executionRun(state, runId);
  if (!run) return;
  if (patch.source) run.source = String(patch.source);
  if (patch.status) run.status = String(patch.status);
  if (patch.startedAt) run.startedAt = patch.startedAt;
  if (patch.endedAt) run.endedAt = patch.endedAt;
  if (patch.metadata) run.metadata = { ...(run.metadata || {}), ...patch.metadata };
  saveExecutionLogState(state);
  renderExecutionLogs(state);
}

function appendExecutionEntry(runId, entry = {}) {
  if (!runId || !entry.message) return;
  const state = loadExecutionLogState();
  const run = executionRun(state, runId);
  if (!run) return;
  run.entries.push({
    timestamp: entry.timestamp || new Date().toISOString(),
    level: String(entry.level || "info").toLowerCase(),
    kind: entry.kind || "entry",
    stage: entry.stage || "",
    indent: Math.max(0, Math.min(3, Number(entry.indent || 0))),
    message: String(entry.message),
    details: compactExecutionDetails(entry.details),
  });
  run.entries = run.entries.slice(-MAX_EXECUTION_ENTRIES);
  saveExecutionLogState(state);
  renderExecutionLogs(state);
}

function compactExecutionDetails(value) {
  if (!value) return "";
  const redacted = String(value)
    .replace(/(authorization\s*:\s*bearer\s+)[^\s]+/gi, "$1[redacted]")
    .replace(/((?:databricks|hugging[ _-]?face|hf)?[ _-]?token\s*[=:]\s*)[^\s,;]+/gi, "$1[redacted]");
  if (redacted.length <= MAX_EXECUTION_DETAIL_LENGTH) return redacted;
  return `${redacted.slice(0, MAX_EXECUTION_DETAIL_LENGTH)}\n… details truncated`;
}

function finishExecutionRun(runId, status = "completed", metadata = {}) {
  updateExecutionRun(runId, {
    status,
    endedAt: new Date().toISOString(),
    metadata,
  });
}

function executionTime(value) {
  const date = new Date(value || "");
  if (Number.isNaN(date.getTime())) return "--:--:--";
  return date.toLocaleTimeString([], {
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  });
}

function executionDuration(run) {
  const start = new Date(run.startedAt || "").getTime();
  const end = new Date(run.endedAt || "").getTime();
  if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) return "";
  const seconds = Math.max(0, Math.round((end - start) / 1000));
  if (seconds < 60) return `${seconds}s`;
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

function logLevelLabel(level) {
  return ({ pass: "PASS", done: "DONE", warn: "WARN", error: "ERROR", debug: "DEBUG" })[level] || "INFO";
}

function runHeaderLines(run) {
  const meta = run.metadata || {};
  const title = [run.source, meta.artifact].filter(Boolean).join(" · ");
  const models = Array.isArray(meta.models) ? meta.models.filter(Boolean).join(", ") : meta.models;
  const first = [`Started ${executionTime(run.startedAt)}`, meta.provider, models].filter(Boolean).join(" · ");
  const second = [meta.ontology ? `Ontology: ${meta.ontology}` : "", meta.ruleCount != null ? `${meta.ruleCount} rule(s)` : ""].filter(Boolean).join(" · ");
  const third = [meta.validation ? `Validation: ${meta.validation}` : "", meta.requestId ? `Request: ${meta.requestId}` : ""].filter(Boolean).join(" · ");
  return { title: title || run.source, lines: [first, second, third].filter(Boolean) };
}

function executionEntryMatches(entry, query, levelFilter) {
  const level = String(entry.level || "info");
  if (levelFilter === "issues" && !["warn", "error"].includes(level)) return false;
  if (levelFilter === "errors" && level !== "error") return false;
  if (!query) return true;
  return `${entry.message || ""} ${entry.details || ""} ${entry.stage || ""}`.toLowerCase().includes(query);
}

function renderExecutionLogs(state = loadExecutionLogState()) {
  const contentEl = byId("logs-content");
  if (!contentEl) return;
  const previousScrollTop = contentEl.scrollTop;
  const titleEl = byId("logs-title");
  const searchEl = byId("logs-search");
  const levelEl = byId("logs-level");
  const query = String(searchEl && searchEl.value || "").trim().toLowerCase();
  const levelFilter = String(levelEl && levelEl.value || "all");
  const runs = state.runs || [];

  if (titleEl) titleEl.textContent = runs.length ? `Execution logs · ${runs.length} run${runs.length === 1 ? "" : "s"}` : "Execution logs";
  contentEl.innerHTML = "";

  runs.forEach((run) => {
    const header = runHeaderLines(run);
    const searchableHeader = `${header.title} ${header.lines.join(" ")}`.toLowerCase();
    const matchingEntries = (run.entries || []).filter((entry) => executionEntryMatches(entry, query, levelFilter));
    if ((query && !searchableHeader.includes(query) && !matchingEntries.length)
        || (levelFilter !== "all" && !matchingEntries.length)) return;

    const section = document.createElement("section");
    section.className = `log-run log-run-${run.status || "completed"}`;
    const heading = document.createElement("div");
    heading.className = "log-run-heading";
    heading.innerHTML = `<strong>${esc(header.title)}</strong><span>${esc(run.status || "completed")}</span>`;
    section.appendChild(heading);

    header.lines.forEach((line) => {
      const metaLine = document.createElement("div");
      metaLine.className = "log-run-meta";
      metaLine.textContent = line;
      section.appendChild(metaLine);
    });

    const duration = executionDuration(run);
    if (duration) {
      const durationLine = document.createElement("div");
      durationLine.className = "log-run-meta";
      durationLine.textContent = `Finished ${executionTime(run.endedAt)} · duration ${duration}`;
      section.appendChild(durationLine);
    }

    matchingEntries.forEach((entry) => {
      const line = document.createElement("div");
      line.className = `log-line log-${entry.level || "info"} log-kind-${entry.kind || "entry"}`;
      line.style.setProperty("--log-indent", String(entry.indent || 0));
      line.innerHTML = `<time>${esc(executionTime(entry.timestamp))}</time><span class="log-level">${esc(logLevelLabel(entry.level))}</span><span class="log-message">${esc(entry.message)}</span>`;
      if (entry.details) {
        const details = document.createElement("details");
        details.className = "log-entry-details";
        details.innerHTML = `<summary>Details</summary><pre>${esc(entry.details)}</pre>`;
        line.appendChild(details);
      }
      section.appendChild(line);
    });
    contentEl.appendChild(section);
  });

  if (!contentEl.children.length) {
    const empty = document.createElement("p");
    empty.className = "logs-empty";
    empty.textContent = runs.length ? "No log entries match the current filters." : "No generation logs are available yet.";
    contentEl.appendChild(empty);
  }

  updateLogsBadge(state);
  const autoscroll = byId("logs-autoscroll");
  if (!autoscroll || autoscroll.checked) contentEl.scrollTop = contentEl.scrollHeight;
  else contentEl.scrollTop = previousScrollTop;
}

function updateLogsBadge(state = loadExecutionLogState()) {
  const badge = byId("logs-count");
  if (!badge) return;
  const latest = (state.runs || []).slice(-1)[0];
  const count = latest ? (latest.entries || []).filter((entry) => ["warn", "error"].includes(entry.level)).length : 0;
  badge.textContent = String(count);
  badge.hidden = count === 0;
}

function executionLogsAsText(state = loadExecutionLogState()) {
  const lines = [];
  (state.runs || []).forEach((run) => {
    const header = runHeaderLines(run);
    lines.push(header.title, ...header.lines);
    (run.entries || []).forEach((entry) => {
      const indent = "  ".repeat(entry.indent || 0);
      lines.push(`${indent}${executionTime(entry.timestamp)}  ${logLevelLabel(entry.level).padEnd(5)}  ${entry.message}`);
      if (entry.details) lines.push(`${indent}  Details:\n${entry.details}`);
    });
    const duration = executionDuration(run);
    if (duration) lines.push(`Finished ${executionTime(run.endedAt)} · duration ${duration}`);
    lines.push("─".repeat(64));
  });
  return lines.join("\n");
}

function clearExecutionLogs() {
  localStorage.removeItem(STORE.executionLogs);
  renderExecutionLogs(emptyExecutionLogState());
}

function wireLogsDrawer() {
  const toggle = byId("logs-toggle");
  const drawer = byId("logs-drawer");
  const close = byId("logs-close");
  if (!toggle || !drawer || !close) return;
  renderExecutionLogs();
  toggle.setAttribute("aria-expanded", "false");
  toggle.addEventListener("click", () => {
    const open = drawer.classList.toggle("open");
    toggle.classList.toggle("active", open);
    toggle.setAttribute("aria-expanded", open ? "true" : "false");
  });
  close.addEventListener("click", () => {
    drawer.classList.remove("open");
    toggle.classList.remove("active");
    toggle.setAttribute("aria-expanded", "false");
  });
  const search = byId("logs-search");
  const level = byId("logs-level");
  const copy = byId("logs-copy");
  const clear = byId("logs-clear");
  if (search) search.addEventListener("input", () => renderExecutionLogs());
  if (level) level.addEventListener("change", () => renderExecutionLogs());
  if (copy) copy.addEventListener("click", async () => {
    const copied = await copyToClipboard(executionLogsAsText());
    copy.textContent = copied ? "Copied" : "Copy failed";
    setTimeout(() => { copy.textContent = "Copy"; }, 1200);
  });
  if (clear) clear.addEventListener("click", clearExecutionLogs);
}

document.addEventListener("DOMContentLoaded", wireLogsDrawer);
