const form = document.querySelector("#chat-form");
const input = document.querySelector("#message-input");
const sendButton = document.querySelector("#send-button");
const clearButton = document.querySelector("#clear-button");
const messages = document.querySelector("#messages");
const loadingTemplate = document.querySelector("#loading-template");
const traceEmpty = document.querySelector("#trace-empty");
const traceContent = document.querySelector("#trace-content");
const runState = document.querySelector("#run-state");
const sessionLabel = document.querySelector("#session-label");
let conversationId = sessionStorage.getItem("commerce_conversation_id");

updateSessionLabel();

const roleMeta = {
  product_scout: { name: "Product Scout", symbol: "PS" },
  order_care: { name: "Order Care", symbol: "OC" },
  conversation_guide: { name: "Conversation Guide", symbol: "CG" },
};

document.querySelectorAll("[data-example]").forEach((button) => {
  button.addEventListener("click", () => {
    input.value = button.dataset.example;
    resizeInput();
    input.focus();
  });
});

input.addEventListener("input", resizeInput);
input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    form.requestSubmit();
  }
});

clearButton.addEventListener("click", () => {
  messages.innerHTML = "";
  appendMessage("assistant", "你好，我可以帮你挑选商品，也可以查询订单、物流和退换货政策。");
  conversationId = null;
  sessionStorage.removeItem("commerce_conversation_id");
  updateSessionLabel();
  resetTrace();
  input.focus();
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = input.value.trim();
  if (!text || sendButton.disabled) return;

  appendMessage("user", text);
  input.value = "";
  resizeInput();
  setRunning(true);
  const loading = loadingTemplate.content.firstElementChild.cloneNode(true);
  messages.append(loading);
  scrollMessages();

  try {
    const response = await fetch("/v1/turns", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, conversation_id: conversationId }),
    });
    const body = await response.json();
    if (!response.ok) {
      throw new Error(body.detail ? JSON.stringify(body.detail) : `HTTP ${response.status}`);
    }
    conversationId = body.conversation_id;
    sessionStorage.setItem("commerce_conversation_id", conversationId);
    updateSessionLabel();
    loading.remove();
    appendMessage("assistant", body.answer);
    renderTrace(body);
    setRunning(false, "done");
  } catch (error) {
    loading.remove();
    appendMessage("assistant", `请求失败：${error.message}`);
    renderRequestError(error);
    setRunning(false, "failed");
  }
});

function appendMessage(role, text) {
  const article = document.createElement("article");
  article.className = `message ${role}-message`;

  const avatar = document.createElement("div");
  avatar.className = "avatar";
  avatar.setAttribute("aria-hidden", "true");
  avatar.textContent = role === "user" ? "YOU" : "AI";

  const body = document.createElement("div");
  body.className = "message-body";
  const label = document.createElement("span");
  label.className = "message-label";
  label.textContent = role === "user" ? "USER" : "COPILOT";
  const paragraph = document.createElement("p");
  paragraph.textContent = text;
  body.append(label, paragraph);
  article.append(avatar, body);
  messages.append(article);
  scrollMessages();
}

function renderTrace(body) {
  traceEmpty.classList.add("hidden");
  traceContent.classList.remove("hidden");

  setText("metric-time", `${formatNumber(body.elapsed_ms)} ms`);
  setText("metric-agents", body.reports.length);
  setText("metric-claims", body.trace.grounding.checked_claims);
  setPill("planning-source", body.trace.planning_source);
  setPill("agent-status", `${body.reports.filter((item) => item.status === "completed").length}/${body.reports.length} OK`);
  setPill("grounding-status", body.trace.grounding.passed ? "PASSED" : "BLOCKED", body.trace.grounding.passed);
  setPill("answer-source", body.trace.answer_source);
  setText("agent-count-label", `${body.reports.length} REPORT${body.reports.length === 1 ? "" : "S"}`);

  const reportContainer = document.querySelector("#agent-reports");
  reportContainer.replaceChildren(...body.reports.map(createAgentCard));
  renderErrors(body);
}

function createAgentCard(report) {
  const meta = roleMeta[report.role] || { name: report.role, symbol: "AG" };
  const card = document.createElement("article");
  card.className = "agent-card";

  const summary = document.createElement("button");
  summary.type = "button";
  summary.className = "agent-summary";
  summary.setAttribute("aria-expanded", "true");
  summary.innerHTML = `
    <span class="agent-symbol">${meta.symbol}</span>
    <span class="agent-name"><strong>${escapeHtml(meta.name)}</strong><small>${escapeHtml(report.status.toUpperCase())} · ${report.evidence.length} evidence</small></span>
    <span class="agent-time">${formatNumber(report.elapsed_ms)} ms</span>
  `;

  const details = document.createElement("div");
  details.className = "agent-details";
  const message = document.createElement("p");
  message.textContent = report.message;
  details.append(message);
  report.evidence.forEach((item) => details.append(createEvidence(item)));

  summary.addEventListener("click", () => {
    const expanded = summary.getAttribute("aria-expanded") === "true";
    summary.setAttribute("aria-expanded", String(!expanded));
    details.classList.toggle("hidden", expanded);
  });
  card.append(summary, details);
  return card;
}

function createEvidence(evidence) {
  const wrapper = document.createElement("div");
  wrapper.className = "evidence-item";
  const head = document.createElement("div");
  head.className = "evidence-head";
  const reference = document.createElement("strong");
  reference.textContent = evidence.reference;
  const source = document.createElement("span");
  source.textContent = evidence.source;
  head.append(reference, source);

  const attributes = document.createElement("div");
  attributes.className = "attributes";
  Object.entries(evidence.attributes).forEach(([key, value]) => {
    const attribute = document.createElement("span");
    attribute.className = "attribute";
    attribute.textContent = `${key}: ${formatValue(value)}`;
    attributes.append(attribute);
  });
  wrapper.append(head, attributes);
  return wrapper;
}

function renderErrors(body) {
  const section = document.querySelector("#error-section");
  const list = document.querySelector("#error-list");
  const errors = [];
  if (body.trace.planner_error) errors.push(`planner: ${body.trace.planner_error}`);
  if (body.trace.composer_error) errors.push(`composer: ${body.trace.composer_error}`);
  body.trace.grounding.issues.forEach((issue) => {
    errors.push(`${issue.role}: ${issue.reason} · ${issue.statement}`);
  });
  section.classList.toggle("hidden", errors.length === 0);
  list.replaceChildren(...errors.map((value) => {
    const item = document.createElement("div");
    item.className = "error-item";
    item.textContent = value;
    return item;
  }));
}

function renderRequestError(error) {
  traceEmpty.classList.add("hidden");
  traceContent.classList.remove("hidden");
  ["metric-time", "metric-agents", "metric-claims"].forEach((id) => setText(id, "—"));
  ["planning-source", "agent-status", "grounding-status", "answer-source"].forEach((id) => setPill(id, "FAILED", false));
  document.querySelector("#agent-reports").replaceChildren();
  const section = document.querySelector("#error-section");
  section.classList.remove("hidden");
  const item = document.createElement("div");
  item.className = "error-item";
  item.textContent = `request: ${error.message}`;
  document.querySelector("#error-list").replaceChildren(item);
}

function resetTrace() {
  traceEmpty.classList.remove("hidden");
  traceContent.classList.add("hidden");
  runState.className = "state-badge idle";
  runState.textContent = "IDLE";
}

function setRunning(running, result = "") {
  sendButton.disabled = running;
  input.disabled = running;
  runState.className = `state-badge ${running ? "running" : result}`;
  runState.textContent = running ? "RUNNING" : result === "failed" ? "FAILED" : "DONE";
}

function setPill(id, value, success = null) {
  const element = document.querySelector(`#${id}`);
  element.textContent = String(value).toUpperCase();
  element.className = "source-pill";
  if (success === true) element.classList.add("good");
  if (success === false) element.classList.add("bad");
}

function setText(id, value) {
  document.querySelector(`#${id}`).textContent = value;
}

function resizeInput() {
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 150)}px`;
}

function scrollMessages() {
  messages.scrollTop = messages.scrollHeight;
}

function formatNumber(value) {
  return Number(value).toLocaleString("zh-CN", { maximumFractionDigits: 2 });
}

function formatValue(value) {
  if (Array.isArray(value)) return value.join(", ");
  if (value && typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function updateSessionLabel() {
  sessionLabel.textContent = conversationId
    ? `SESSION · ${conversationId.slice(0, 8).toUpperCase()}`
    : "NEW SESSION";
}
