const form = document.querySelector("#chat-form");
const input = document.querySelector("#message-input");
const sendButton = document.querySelector("#send-button");
const clearButton = document.querySelector("#clear-button");
const messages = document.querySelector("#messages");
const loadingTemplate = document.querySelector("#loading-template");
const traceEmpty = document.querySelector("#trace-empty");
const traceContent = document.querySelector("#trace-content");
const runState = document.querySelector("#run-state");
const traceIdLabel = document.querySelector("#trace-id");
const sessionLabel = document.querySelector("#session-label");
const authScreen = document.querySelector("#auth-screen");
const authForm = document.querySelector("#auth-form");
const authTitle = document.querySelector("#auth-title");
const authDescription = document.querySelector("#auth-description");
const authSubmit = document.querySelector("#auth-submit");
const authToggle = document.querySelector("#auth-toggle");
const authError = document.querySelector("#auth-error");
const logoutButton = document.querySelector("#logout-button");
const userLabel = document.querySelector("#user-label");
const preferencesButton = document.querySelector("#preferences-button");
const preferencesDialog = document.querySelector("#preferences-dialog");
const preferencesForm = document.querySelector("#preferences-form");
const preferencesClose = document.querySelector("#preferences-close");
const preferencesCancel = document.querySelector("#preferences-cancel");
const preferencesSave = document.querySelector("#preferences-save");
const preferencesFeedback = document.querySelector("#preferences-feedback");
let conversationId = sessionStorage.getItem("commerce_conversation_id");
let accessToken = sessionStorage.getItem("commerce_access_token");
let currentUsername = sessionStorage.getItem("commerce_username");
let authMode = "login";
let liveAgentCount = 0;
let plannedAgentCount = 0;

updateSessionLabel();
updateAuthState();

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

authToggle.addEventListener("click", () => {
  authMode = authMode === "login" ? "register" : "login";
  authTitle.textContent = authMode === "login" ? "登录后开始协作" : "创建本地账号";
  authDescription.textContent = authMode === "login"
    ? "身份用于隔离会话和订单数据。"
    : "密码只保存为加盐哈希，不保存明文。";
  authSubmit.textContent = authMode === "login" ? "登录" : "创建账号";
  authToggle.textContent = authMode === "login"
    ? "没有账号？创建账号"
    : "已有账号？返回登录";
  authError.classList.add("hidden");
});

authForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  authSubmit.disabled = true;
  authError.classList.add("hidden");
  const username = document.querySelector("#auth-username").value.trim();
  const password = document.querySelector("#auth-password").value;
  try {
    const response = await fetch(`/v1/auth/${authMode}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || "认证失败");
    accessToken = body.access_token;
    currentUsername = body.user.username;
    sessionStorage.setItem("commerce_access_token", accessToken);
    sessionStorage.setItem("commerce_username", currentUsername);
    authForm.reset();
    updateAuthState();
  } catch (error) {
    authError.textContent = error.message;
    authError.classList.remove("hidden");
  } finally {
    authSubmit.disabled = false;
  }
});

logoutButton.addEventListener("click", logout);
preferencesButton.addEventListener("click", openPreferences);
preferencesClose.addEventListener("click", () => preferencesDialog.close());
preferencesCancel.addEventListener("click", () => preferencesDialog.close());
preferencesForm.addEventListener("submit", savePreferences);

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
  beginLiveTrace();

  try {
    const body = await consumeTurnStream(text);
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

async function consumeTurnStream(message) {
  const response = await fetch("/v1/turns/stream", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Authorization": `Bearer ${accessToken}`,
    },
    body: JSON.stringify({ message, conversation_id: conversationId }),
  });
  if (response.status === 401) {
    logout();
    throw new Error("登录已过期，请重新登录");
  }
  if (!response.ok || !response.body) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail ? JSON.stringify(body.detail) : `HTTP ${response.status}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let completedResponse = null;
  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    let match = buffer.match(/\r?\n\r?\n/);
    while (match && match.index !== undefined) {
      const block = buffer.slice(0, match.index);
      buffer = buffer.slice(match.index + match[0].length);
      const parsed = parseSseBlock(block);
      if (parsed) {
        handleStreamEvent(parsed.event, parsed.data);
        if (parsed.event === "error") {
          throw new Error(parsed.data.error || "流式请求失败");
        }
        if (parsed.event === "complete") completedResponse = parsed.data.response;
      }
      match = buffer.match(/\r?\n\r?\n/);
    }
    if (done) break;
  }
  if (!completedResponse) throw new Error("流式响应提前结束");
  return completedResponse;
}

function parseSseBlock(block) {
  if (!block.trim()) return null;
  let event = "message";
  const dataLines = [];
  block.split(/\r?\n/).forEach((line) => {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
  });
  if (!dataLines.length) return null;
  return { event, data: JSON.parse(dataLines.join("\n")) };
}

function beginLiveTrace() {
  liveAgentCount = 0;
  plannedAgentCount = 0;
  traceEmpty.classList.add("hidden");
  traceContent.classList.remove("hidden");
  traceIdLabel.textContent = "TRACE · WAITING";
  ["metric-time", "metric-agents", "metric-claims"].forEach((id) => setText(id, "—"));
  ["planning-source", "agent-status", "grounding-status", "answer-source"].forEach((id) => setPill(id, "WAITING"));
  document.querySelector("#agent-reports").replaceChildren();
  setText("agent-count-label", "0 REPORTS");
  renderPersonalization({});
}

function handleStreamEvent(event, data) {
  if (data.trace_id) traceIdLabel.textContent = `TRACE · ${data.trace_id.slice(0, 10).toUpperCase()}`;
  if (event === "planning_started") setPill("planning-source", "RUNNING");
  if (event === "planning_completed") {
    plannedAgentCount = data.agents.length;
    setText("metric-agents", plannedAgentCount);
    setPill("planning-source", `${data.source} · ${formatNumber(data.elapsed_ms)} ms`, true);
    setPill("agent-status", `0/${plannedAgentCount} RUNNING`);
  }
  if (event === "agent_started") setPill("agent-status", `${liveAgentCount}/${plannedAgentCount} RUNNING`);
  if (event === "agent_completed") {
    liveAgentCount += 1;
    setPill(
      "agent-status",
      `${liveAgentCount}/${plannedAgentCount} ${data.status === "completed" ? "OK" : "FAILED"}`,
      data.status === "completed",
    );
  }
  if (event === "grounding_completed") {
    setText("metric-claims", data.checked_claims);
    setPill(
      "grounding-status",
      `${data.passed ? "PASSED" : "BLOCKED"} · ${formatNumber(data.elapsed_ms)} ms`,
      data.passed,
    );
  }
  if (event === "answer_completed") {
    setPill("answer-source", `${data.source} · ${formatNumber(data.elapsed_ms)} ms`, true);
  }
  if (event === "workflow_completed") {
    setText("metric-time", `${formatNumber(data.elapsed_ms)} ms`);
  }
}

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
  traceIdLabel.textContent = `TRACE · ${body.trace.trace_id.slice(0, 10).toUpperCase()}`;
  const planningTiming = findStageTiming(body.trace, "planning");
  const groundingTiming = findStageTiming(body.trace, "grounding");
  const answeringTiming = findStageTiming(body.trace, "answering");
  setPill("planning-source", formatStage(body.trace.planning_source, planningTiming));
  setPill("agent-status", `${body.reports.filter((item) => item.status === "completed").length}/${body.reports.length} OK`);
  setPill("grounding-status", formatStage(body.trace.grounding.passed ? "PASSED" : "BLOCKED", groundingTiming), body.trace.grounding.passed);
  setPill("answer-source", formatStage(body.trace.answer_source, answeringTiming));
  setText("agent-count-label", `${body.reports.length} REPORT${body.reports.length === 1 ? "" : "S"}`);

  const reportContainer = document.querySelector("#agent-reports");
  reportContainer.replaceChildren(...body.reports.map(createAgentCard));
  renderPersonalization(body.trace.applied_preferences || {});
  renderErrors(body);
}

function findStageTiming(trace, stage) {
  return (trace.stage_timings || []).find((item) => item.stage === stage);
}

function formatStage(label, timing) {
  return timing ? `${label} · ${formatNumber(timing.elapsed_ms)} ms` : label;
}

function renderPersonalization(preferences) {
  const section = document.querySelector("#personalization-section");
  const values = document.querySelector("#personalization-values");
  const entries = Object.entries(preferences);
  section.classList.toggle("hidden", entries.length === 0);
  values.replaceChildren(...entries.map(([key, value]) => {
    const chip = document.createElement("span");
    chip.className = "preference-chip";
    chip.textContent = `${preferenceLabel(key)}：${formatPreferenceValue(key, value)}`;
    return chip;
  }));
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
  traceIdLabel.textContent = "TRACE · —";
  renderPersonalization({});
}

async function openPreferences() {
  preferencesDialog.showModal();
  setPreferencesFeedback("正在读取偏好……");
  try {
    const response = await fetch("/v1/me/preferences", {
      headers: { "Authorization": `Bearer ${accessToken}` },
    });
    const body = await response.json();
    if (response.status === 401) {
      preferencesDialog.close();
      logout();
      throw new Error("登录已过期，请重新登录");
    }
    if (!response.ok) throw new Error(readError(body, "读取偏好失败"));
    fillPreferencesForm(body);
    setPreferencesFeedback("本轮明确条件会优先于这些默认偏好。");
  } catch (error) {
    setPreferencesFeedback(error.message, "error");
  }
}

async function savePreferences(event) {
  event.preventDefault();
  preferencesSave.disabled = true;
  setPreferencesFeedback("正在保存……");
  const budgetYuan = document.querySelector("#preference-budget").value;
  const payload = {
    max_budget_cents: budgetYuan === "" ? null : Math.round(Number(budgetYuan) * 100),
    preferred_categories: splitTerms(document.querySelector("#preference-categories").value),
    preferred_brands: splitTerms(document.querySelector("#preference-brands").value),
    scenarios: splitTerms(document.querySelector("#preference-scenarios").value),
    priorities: splitTerms(document.querySelector("#preference-priorities").value),
  };
  try {
    const response = await fetch("/v1/me/preferences", {
      method: "PUT",
      headers: {
        "Content-Type": "application/json",
        "Authorization": `Bearer ${accessToken}`,
      },
      body: JSON.stringify(payload),
    });
    const body = await response.json();
    if (response.status === 401) {
      preferencesDialog.close();
      logout();
      throw new Error("登录已过期，请重新登录");
    }
    if (!response.ok) throw new Error(readError(body, "保存偏好失败"));
    fillPreferencesForm(body);
    setPreferencesFeedback("偏好已保存，下次商品推荐会自动补充缺失条件。", "success");
  } catch (error) {
    setPreferencesFeedback(error.message, "error");
  } finally {
    preferencesSave.disabled = false;
  }
}

function fillPreferencesForm(preferences) {
  document.querySelector("#preference-budget").value = preferences.max_budget_cents == null
    ? ""
    : preferences.max_budget_cents / 100;
  document.querySelector("#preference-categories").value = preferences.preferred_categories.join("、");
  document.querySelector("#preference-brands").value = preferences.preferred_brands.join("、");
  document.querySelector("#preference-scenarios").value = preferences.scenarios.join("、");
  document.querySelector("#preference-priorities").value = preferences.priorities.join("、");
}

function splitTerms(value) {
  return [...new Set(value.split(/[,，、]/).map((item) => item.trim()).filter(Boolean))];
}

function setPreferencesFeedback(message, state = "") {
  preferencesFeedback.textContent = message;
  preferencesFeedback.className = `preferences-feedback ${state}`.trim();
}

function readError(body, fallback) {
  if (typeof body.detail === "string") return body.detail;
  if (Array.isArray(body.detail)) return body.detail.map((item) => item.msg).join("；");
  return fallback;
}

function preferenceLabel(key) {
  return {
    max_budget_cents: "预算",
    preferred_categories: "品类",
    preferred_brands: "品牌",
    scenarios: "场景",
    priorities: "关注点",
  }[key] || key;
}

function formatPreferenceValue(key, value) {
  if (key === "max_budget_cents") return `${Number(value) / 100} 元`;
  return Array.isArray(value) ? value.join("、") : String(value);
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

function updateAuthState() {
  const authenticated = Boolean(accessToken && currentUsername);
  authScreen.classList.toggle("hidden", authenticated);
  userLabel.textContent = authenticated ? `USER · ${currentUsername.toUpperCase()}` : "";
  if (authenticated) input.focus();
}

function logout() {
  accessToken = null;
  currentUsername = null;
  conversationId = null;
  sessionStorage.removeItem("commerce_access_token");
  sessionStorage.removeItem("commerce_username");
  sessionStorage.removeItem("commerce_conversation_id");
  resetTrace();
  updateSessionLabel();
  updateAuthState();
}
