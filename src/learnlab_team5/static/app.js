const state = {
  assignments: [],
  students: [],
  steps: [],
  selectedAssignment: null,
  selectedSubject: null,
  selectedStep: -1,
  view: "snapshot",
  snapshotCache: new Map(),
  requestToken: 0,
};

const elements = {
  assignmentCount: document.querySelector("#assignment-count"),
  assignmentList: document.querySelector("#assignment-list"),
  studentCount: document.querySelector("#student-count"),
  studentSearch: document.querySelector("#student-search"),
  studentList: document.querySelector("#student-list"),
  timelineTitle: document.querySelector("#timeline-title"),
  timelineCount: document.querySelector("#timeline-count"),
  timelineTools: document.querySelector("#timeline-tools"),
  timelineList: document.querySelector("#timeline-list"),
  scrubber: document.querySelector("#history-scrubber"),
  scrubberPosition: document.querySelector("#scrubber-position"),
  viewerContext: document.querySelector("#viewer-context"),
  previous: document.querySelector("#previous-step"),
  next: document.querySelector("#next-step"),
  stepPosition: document.querySelector("#step-position"),
  workspace: document.querySelector("#snapshot-workspace"),
  viewerEmpty: document.querySelector("#viewer-empty"),
  eventBadge: document.querySelector("#event-badge"),
  snapshotTime: document.querySelector("#snapshot-time"),
  stateId: document.querySelector("#state-id"),
  copyState: document.querySelector("#copy-state"),
  snapshotTab: document.querySelector("#snapshot-tab"),
  changesTab: document.querySelector("#changes-tab"),
  changeCount: document.querySelector("#change-count"),
  snapshotView: document.querySelector("#snapshot-view"),
  changesView: document.querySelector("#changes-view"),
  snapshotCode: document.querySelector("#snapshot-code"),
  diffCode: document.querySelector("#diff-code"),
  unavailable: document.querySelector("#unavailable-state"),
  loading: document.querySelector("#code-loading"),
  astDetails: document.querySelector("#ast-details"),
  astCode: document.querySelector("#ast-code"),
  error: document.querySelector("#app-error"),
  errorMessage: document.querySelector("#error-message"),
  dismissError: document.querySelector("#dismiss-error"),
};

async function api(path, parameters = {}) {
  const url = new URL(path, window.location.origin);
  Object.entries(parameters).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") {
      url.searchParams.set(key, value);
    }
  });
  const response = await fetch(url);
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      message = body.detail || message;
    } catch {
      // Keep the status-based message when the response is not JSON.
    }
    throw new Error(message);
  }
  return response.json();
}

function showError(error) {
  elements.errorMessage.textContent = error instanceof Error ? error.message : String(error);
  elements.error.classList.remove("is-hidden");
}

function hideError() {
  elements.error.classList.add("is-hidden");
}

function formatNumber(value) {
  return new Intl.NumberFormat().format(value);
}

function formatDate(value) {
  if (!value) return "Unknown date";
  const [date] = value.split("T");
  const parsed = new Date(`${date}T00:00:00`);
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  }).format(parsed);
}

function formatTimestamp(value, timezone = "") {
  if (!value) return "Timestamp unavailable";
  return `${value.replace("T", " ")}${timezone ? ` ${timezone}` : ""}`;
}

function initials(subjectId) {
  return subjectId.replace(/[^A-Za-z0-9]/g, "").slice(0, 2).toUpperCase() || "ID";
}

function activeAssignment() {
  return state.assignments.find((item) => item.assignment_id === state.selectedAssignment);
}

function updateUrl() {
  const parameters = new URLSearchParams();
  if (state.selectedAssignment) parameters.set("assignment", state.selectedAssignment);
  if (state.selectedSubject) parameters.set("subject", state.selectedSubject);
  if (state.selectedStep >= 0) parameters.set("step", String(state.selectedStep));
  const query = parameters.toString();
  window.history.replaceState(null, "", query ? `?${query}` : window.location.pathname);
}

function urlSelection() {
  const parameters = new URLSearchParams(window.location.search);
  const rawStep = Number.parseInt(parameters.get("step") || "0", 10);
  return {
    assignment: parameters.get("assignment"),
    subject: parameters.get("subject"),
    step: Number.isFinite(rawStep) ? rawStep : 0,
  };
}

function button(classes, content, onClick) {
  const result = document.createElement("button");
  result.type = "button";
  result.className = classes;
  if (typeof content === "string") {
    result.textContent = content;
  } else {
    result.append(content);
  }
  result.addEventListener("click", onClick);
  return result;
}

function renderAssignments() {
  elements.assignmentList.replaceChildren();
  elements.assignmentCount.textContent = String(state.assignments.length);

  state.assignments.forEach((assignment) => {
    const content = document.createDocumentFragment();
    const title = document.createElement("strong");
    title.textContent = assignment.title;
    const details = document.createElement("span");
    details.textContent = `${assignment.assignment_type} · ${formatNumber(assignment.snapshot_count)} states`;
    content.append(title, details);

    const card = button("assignment-card", content, () => selectAssignment(assignment.assignment_id));
    card.classList.toggle("is-active", assignment.assignment_id === state.selectedAssignment);
    card.dataset.assignmentId = assignment.assignment_id;
    card.setAttribute("aria-pressed", String(assignment.assignment_id === state.selectedAssignment));
    card.title = `${assignment.title}\nDue ${formatDate(assignment.due_date)}`;
    elements.assignmentList.append(card);
  });
}

function renderStudents() {
  elements.studentList.replaceChildren();
  elements.studentCount.textContent = String(state.students.length);

  if (!state.students.length) {
    const message = document.createElement("p");
    message.className = "quiet-message";
    message.textContent = elements.studentSearch.value
      ? "No anonymized IDs match that search."
      : "No student histories are available for this assignment.";
    elements.studentList.append(message);
    return;
  }

  state.students.forEach((student) => {
    const avatar = document.createElement("span");
    avatar.className = "student-avatar";
    avatar.textContent = initials(student.subject_id);

    const copy = document.createElement("span");
    copy.className = "student-copy";
    const name = document.createElement("strong");
    name.textContent = student.subject_id;
    const dates = document.createElement("span");
    dates.textContent = `${formatDate(student.first_timestamp)} – ${formatDate(student.last_timestamp)}`;
    copy.append(name, dates);

    const count = document.createElement("span");
    count.className = "student-snapshots";
    count.textContent = formatNumber(student.snapshot_count);
    count.title = `${formatNumber(student.snapshot_count)} snapshots`;

    const content = document.createDocumentFragment();
    content.append(avatar, copy, count);
    const row = button("student-button", content, () => selectStudent(student.subject_id));
    row.classList.toggle("is-active", student.subject_id === state.selectedSubject);
    row.dataset.subjectId = student.subject_id;
    row.setAttribute("aria-pressed", String(student.subject_id === state.selectedSubject));
    elements.studentList.append(row);
  });
}

function clearHistory() {
  state.steps = [];
  state.selectedStep = -1;
  elements.timelineTitle.textContent = "Select a student";
  elements.timelineCount.textContent = "—";
  elements.timelineTools.classList.add("is-hidden");
  elements.timelineList.innerHTML = `
    <div class="timeline-empty">
      <div class="empty-orbit" aria-hidden="true"><span></span></div>
      <p>Choose a student to reveal their sequence of code snapshots.</p>
    </div>
  `;
  elements.workspace.classList.add("is-hidden");
  elements.viewerEmpty.classList.remove("is-hidden");
  elements.previous.disabled = true;
  elements.next.disabled = true;
  elements.stepPosition.textContent = "— / —";
}

async function selectAssignment(assignmentId, preferredSubject = null, preferredStep = 0) {
  if (state.selectedAssignment === assignmentId && state.students.length) return;
  hideError();
  state.selectedAssignment = assignmentId;
  state.selectedSubject = null;
  clearHistory();
  renderAssignments();
  elements.studentSearch.value = "";
  elements.studentSearch.disabled = false;
  elements.studentList.innerHTML = '<p class="quiet-message">Loading student histories…</p>';

  const token = ++state.requestToken;
  try {
    const students = await api("/api/students", { assignment_id: assignmentId });
    if (token !== state.requestToken) return;
    state.students = students;
    const subject = students.some((item) => item.subject_id === preferredSubject)
      ? preferredSubject
      : students[0]?.subject_id;
    state.selectedSubject = subject || null;
    renderStudents();
    updateUrl();
    if (subject) await selectStudent(subject, preferredStep, true);
  } catch (error) {
    if (token === state.requestToken) showError(error);
  }
}

async function refreshStudentSearch() {
  if (!state.selectedAssignment) return;
  const token = ++state.requestToken;
  try {
    state.students = await api("/api/students", {
      assignment_id: state.selectedAssignment,
      q: elements.studentSearch.value.trim(),
    });
    if (token !== state.requestToken) return;
    renderStudents();
  } catch (error) {
    if (token === state.requestToken) showError(error);
  }
}

async function selectStudent(subjectId, preferredStep = 0, force = false) {
  if (!force && state.selectedSubject === subjectId && state.steps.length) return;
  hideError();
  state.selectedSubject = subjectId;
  state.selectedStep = -1;
  renderStudents();
  elements.timelineTitle.textContent = subjectId;
  elements.timelineCount.textContent = "…";
  elements.timelineTools.classList.add("is-hidden");
  elements.timelineList.innerHTML = '<p class="quiet-message">Loading ordered snapshots…</p>';

  const token = ++state.requestToken;
  try {
    const history = await api("/api/history", {
      assignment_id: state.selectedAssignment,
      subject_id: subjectId,
    });
    if (token !== state.requestToken) return;
    state.steps = history.steps;
    renderTimeline();
    const index = Math.max(0, Math.min(preferredStep, state.steps.length - 1));
    await selectStep(index, { scroll: true });
  } catch (error) {
    if (token === state.requestToken) showError(error);
  }
}

function renderTimeline() {
  elements.timelineList.replaceChildren();
  elements.timelineCount.textContent = formatNumber(state.steps.length);
  elements.timelineTools.classList.remove("is-hidden");
  elements.scrubber.min = "0";
  elements.scrubber.max = String(Math.max(0, state.steps.length - 1));

  state.steps.forEach((step, index) => {
    if (index === 0 || step.session_changed) {
      const divider = document.createElement("div");
      divider.className = "session-divider";
      divider.textContent = index === 0 ? "Session start" : "New session";
      elements.timelineList.append(divider);
    }

    const stepNumber = document.createElement("span");
    stepNumber.className = "timeline-step-index";
    stepNumber.textContent = String(index + 1).padStart(2, "0");

    const copy = document.createElement("span");
    copy.className = "timeline-step-copy";
    const event = document.createElement("strong");
    event.textContent = step.event_type;
    const metadata = document.createElement("span");
    const time = document.createElement("span");
    time.textContent = step.client_timestamp.split("T")[1] || step.client_timestamp;
    const stateLabel = document.createElement("span");
    stateLabel.textContent = `#${step.code_state_id}`;
    metadata.append(time, stateLabel);
    copy.append(event, metadata);

    const content = document.createDocumentFragment();
    content.append(stepNumber, copy);
    const row = button("timeline-step", content, () => selectStep(index, { scroll: false }));
    row.dataset.stepIndex = String(index);
    row.setAttribute("aria-label", `Snapshot ${index + 1}: ${step.event_type}`);
    elements.timelineList.append(row);
  });
}

function updateTimelineSelection(scroll) {
  elements.timelineList.querySelectorAll(".timeline-step.is-active").forEach((item) => {
    item.classList.remove("is-active");
    item.removeAttribute("aria-current");
  });
  const selected = elements.timelineList.querySelector(`[data-step-index="${state.selectedStep}"]`);
  if (selected) {
    selected.classList.add("is-active");
    selected.setAttribute("aria-current", "step");
    if (scroll) selected.scrollIntoView({ block: "nearest" });
  }
  elements.scrubber.value = String(state.selectedStep);
  elements.scrubberPosition.textContent = `${state.selectedStep + 1} / ${state.steps.length}`;
}

async function selectStep(index, { scroll = true } = {}) {
  if (index < 0 || index >= state.steps.length) return;
  state.selectedStep = index;
  const step = state.steps[index];
  const assignment = activeAssignment();

  updateTimelineSelection(scroll);
  updateUrl();
  elements.viewerEmpty.classList.add("is-hidden");
  elements.workspace.classList.remove("is-hidden");
  elements.viewerContext.innerHTML = `
    <p class="eyebrow">Pseudocode snapshot</p>
    <h2></h2>
    <p></p>
  `;
  elements.viewerContext.querySelector("h2").textContent = assignment?.title || state.selectedAssignment;
  elements.viewerContext.querySelector("p:last-child").textContent = `Student ${state.selectedSubject}`;
  elements.stepPosition.textContent = `${index + 1} / ${state.steps.length}`;
  elements.previous.disabled = index === 0;
  elements.next.disabled = index === state.steps.length - 1;
  elements.eventBadge.textContent = step.event_type;
  elements.snapshotTime.textContent = formatTimestamp(step.client_timestamp, step.client_timezone);
  elements.stateId.textContent = `State ${step.code_state_id}`;
  elements.copyState.dataset.value = step.code_state_id;
  elements.astDetails.open = false;

  setLoading(true);
  const cacheKey = `${step.code_state_id}:${step.previous_code_state_id || ""}`;
  try {
    let snapshot = state.snapshotCache.get(cacheKey);
    if (!snapshot) {
      snapshot = await api("/api/snapshot", {
        code_state_id: step.code_state_id,
        previous_code_state_id: step.previous_code_state_id,
      });
      state.snapshotCache.set(cacheKey, snapshot);
    }
    if (state.selectedStep !== index) return;
    renderSnapshot(snapshot);
  } catch (error) {
    if (state.selectedStep === index) showError(error);
  } finally {
    if (state.selectedStep === index) setLoading(false);
  }
}

function setLoading(isLoading) {
  elements.loading.classList.toggle("is-hidden", !isLoading);
  if (isLoading) {
    elements.snapshotView.classList.add("is-hidden");
    elements.changesView.classList.add("is-hidden");
    elements.unavailable.classList.add("is-hidden");
    elements.astDetails.classList.add("is-hidden");
  }
}

function renderSnapshot(snapshot) {
  const unavailable = !snapshot.pseudocode_available;
  elements.unavailable.classList.toggle("is-hidden", !unavailable);
  elements.snapshotView.classList.toggle("is-hidden", unavailable || state.view !== "snapshot");
  elements.changesView.classList.toggle("is-hidden", unavailable || state.view !== "changes");

  if (snapshot.pseudocode_available) {
    renderPseudocode(snapshot.pseudocode, new Set(snapshot.changed_lines));
    renderDiff(snapshot.diff);
  } else {
    elements.snapshotCode.replaceChildren();
    elements.diffCode.replaceChildren();
  }

  elements.changeCount.textContent = String(snapshot.changed_lines.length);
  const astMessage = snapshot.ast_error || "No AST is available for this code state.";
  elements.astCode.textContent = snapshot.ast || astMessage;
  elements.astDetails.classList.toggle("is-hidden", !snapshot.ast_available && !snapshot.ast_error);
}

function renderPseudocode(pseudocode, changedLines) {
  const fragment = document.createDocumentFragment();
  const lines = pseudocode.split(/\r?\n/);
  lines.forEach((line, index) => {
    const item = document.createElement("li");
    item.className = "code-line";
    if (changedLines.has(index + 1)) item.classList.add("is-changed");
    const code = document.createElement("code");
    code.textContent = line || " ";
    item.append(code);
    fragment.append(item);
  });
  elements.snapshotCode.replaceChildren(fragment);
}

function renderDiff(blocks) {
  const fragment = document.createDocumentFragment();
  if (!blocks.length) {
    const message = document.createElement("li");
    message.className = "no-changes";
    message.textContent = state.selectedStep === 0
      ? "This is the first available snapshot; there is no earlier state to compare."
      : "No pseudocode change is available for this transition.";
    fragment.append(message);
    elements.diffCode.replaceChildren(fragment);
    return;
  }

  blocks.forEach((block) => {
    block.lines.forEach((line, offset) => {
      const item = document.createElement("li");
      item.className = `diff-line is-${block.kind}`;
      const oldNumber = document.createElement("span");
      const newNumber = document.createElement("span");
      const marker = document.createElement("b");
      const code = document.createElement("code");

      if (block.kind === "equal") {
        oldNumber.textContent = String(block.old_start + offset);
        newNumber.textContent = String(block.new_start + offset);
        marker.textContent = " ";
      } else if (block.kind === "delete") {
        oldNumber.textContent = String(block.old_start + offset);
        marker.textContent = "−";
      } else {
        newNumber.textContent = String(block.new_start + offset);
        marker.textContent = "+";
      }
      code.textContent = line || " ";
      item.append(oldNumber, newNumber, marker, code);
      fragment.append(item);
    });
  });
  elements.diffCode.replaceChildren(fragment);
}

function setView(view) {
  state.view = view;
  const snapshotSelected = view === "snapshot";
  elements.snapshotTab.classList.toggle("is-active", snapshotSelected);
  elements.changesTab.classList.toggle("is-active", !snapshotSelected);
  elements.snapshotTab.setAttribute("aria-selected", String(snapshotSelected));
  elements.changesTab.setAttribute("aria-selected", String(!snapshotSelected));
  if (!elements.unavailable.classList.contains("is-hidden")) return;
  elements.snapshotView.classList.toggle("is-hidden", !snapshotSelected);
  elements.changesView.classList.toggle("is-hidden", snapshotSelected);
}

async function copyStateId() {
  const value = elements.copyState.dataset.value;
  if (!value) return;
  try {
    await navigator.clipboard.writeText(value);
    const original = elements.copyState.textContent;
    elements.copyState.textContent = "Copied";
    window.setTimeout(() => {
      elements.copyState.textContent = original;
    }, 1100);
  } catch {
    showError(new Error("The browser could not copy the state ID."));
  }
}

function debounce(callback, delay) {
  let timer;
  return (...args) => {
    window.clearTimeout(timer);
    timer = window.setTimeout(() => callback(...args), delay);
  };
}

async function initialize() {
  try {
    state.assignments = await api("/api/assignments");
    if (!state.assignments.length) throw new Error("No assignments were found in the dataset.");
    const desired = urlSelection();
    const assignmentId = state.assignments.some((item) => item.assignment_id === desired.assignment)
      ? desired.assignment
      : state.assignments[0].assignment_id;
    state.selectedAssignment = assignmentId;
    renderAssignments();
    await selectAssignment(assignmentId, desired.subject, desired.step);
  } catch (error) {
    showError(error);
    elements.assignmentList.innerHTML = '<p class="quiet-message">Unable to load the dataset.</p>';
  }
}

elements.previous.addEventListener("click", () => selectStep(state.selectedStep - 1));
elements.next.addEventListener("click", () => selectStep(state.selectedStep + 1));
elements.scrubber.addEventListener("input", (event) => {
  selectStep(Number.parseInt(event.target.value, 10), { scroll: true });
});
elements.studentSearch.addEventListener("input", debounce(refreshStudentSearch, 180));
elements.snapshotTab.addEventListener("click", () => setView("snapshot"));
elements.changesTab.addEventListener("click", () => setView("changes"));
elements.copyState.addEventListener("click", copyStateId);
elements.dismissError.addEventListener("click", hideError);

document.addEventListener("keydown", (event) => {
  const tag = event.target.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || event.metaKey || event.ctrlKey || event.altKey) return;
  if (event.key === "ArrowLeft") {
    event.preventDefault();
    selectStep(state.selectedStep - 1);
  } else if (event.key === "ArrowRight") {
    event.preventDefault();
    selectStep(state.selectedStep + 1);
  }
});

initialize();
