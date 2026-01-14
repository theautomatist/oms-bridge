const mqttForm = document.getElementById("mqtt-form");
const mqttStatus = document.getElementById("mqtt-status");
const mqttTestBtn = document.getElementById("mqtt-test");
const mqttSendTestBtn = document.getElementById("mqtt-send-test");
const themeToggle = document.getElementById("theme-toggle");

const lobaroBanner = document.getElementById("lobaro-banner");

const cardMqtt = document.getElementById("card-mqtt");
const cardMqttSub = document.getElementById("card-mqtt-sub");
const cardPendingSub = document.getElementById("card-pending-sub");
const cardKnownSub = document.getElementById("card-known-sub");
const iconMqtt = document.getElementById("icon-mqtt");
const iconPending = document.getElementById("icon-pending");
const iconKnown = document.getElementById("icon-known");

const pendingList = document.getElementById("pending-list");
const knownList = document.getElementById("known-list");
const refreshPendingBtn = document.getElementById("refresh-pending");
const refreshKnownBtn = document.getElementById("refresh-known");

const keyModal = document.getElementById("key-modal");
const keyMeterId = document.getElementById("key-meter-id");
const keyInput = document.getElementById("key-input");
const keySaveBtn = document.getElementById("key-save");
const keyError = document.getElementById("key-error");

const telegramsModal = document.getElementById("telegrams-modal");
const telegramsMeterId = document.getElementById("telegrams-meter-id");
const telegramsList = document.getElementById("telegrams-list");
const telegramInput = document.getElementById("telegram-input");
const telegramParsed = document.getElementById("telegram-parsed");

const toastEl = document.getElementById("toast");

let activeMeterId = null;
let activeTelegramId = null;
let pendingCount = 0;
let knownCount = 0;
let mqttConfigured = false;
let mqttConnected = false;
let lobaroTokenSet = false;

const THEME_KEY = "oms-theme";

const fetchJson = async (url, options = {}) => {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    const raw = await response.text();
    let message = raw || response.statusText;
    try {
      const parsed = JSON.parse(raw);
      if (parsed && parsed.detail) {
        message = parsed.detail;
      }
    } catch (err) {
      // Fall back to raw text.
    }
    throw new Error(message);
  }
  return response.json();
};

const toast = (message, tone = "success") => {
  toastEl.textContent = message;
  toastEl.className = `toast show ${tone}`;
  setTimeout(() => {
    toastEl.className = "toast";
  }, 2000);
};

const setPill = (el, text, state) => {
  if (!el) {
    return;
  }
  el.textContent = text;
  el.dataset.state = state;
};

const setCardState = (card, state) => {
  card.classList.remove("ok", "warn", "error", "info");
  card.classList.add(state);
};

const applyIconMask = (el) => {
  const name = el.dataset.icon;
  if (!name) {
    return;
  }
  const url = `/static/icons/${name}.svg`;
  el.style.maskImage = `url(${url})`;
  el.style.webkitMaskImage = `url(${url})`;
  el.style.backgroundColor = "currentColor";
};

const setIcon = (el, name) => {
  if (!el) {
    return;
  }
  el.dataset.icon = name;
  applyIconMask(el);
};

const applyAllIcons = () => {
  document.querySelectorAll(".icon").forEach((el) => applyIconMask(el));
};

const applyTheme = (theme) => {
  const nextTheme = theme === "dark" ? "dark" : "light";
  document.documentElement.dataset.theme = nextTheme;
  if (themeToggle) {
    themeToggle.textContent = nextTheme === "dark" ? "Light" : "Dark";
  }
  localStorage.setItem(THEME_KEY, nextTheme);
};

const updateStatusCards = () => {
  cardPendingSub.textContent = `${pendingCount} pending`;
  cardKnownSub.textContent = `${knownCount} meters`;

  if (!mqttConfigured) {
    setCardState(cardMqtt, "warn");
    cardMqttSub.textContent = "Not configured";
    setIcon(iconMqtt, "cloud_off");
  } else if (mqttConnected) {
    setCardState(cardMqtt, "ok");
    cardMqttSub.textContent = "Connected";
    setIcon(iconMqtt, "cloud_done");
  } else {
    setCardState(cardMqtt, "warn");
    cardMqttSub.textContent = "Configured, offline";
    setIcon(iconMqtt, "cloud_off");
  }

  if (lobaroBanner) {
    lobaroBanner.classList.toggle("hidden", lobaroTokenSet);
  }

  setIcon(iconPending, "sensors");
  setIcon(iconKnown, "sensors");
};

const loadHealth = async () => {
  try {
    const data = await fetchJson("/healthz");
    mqttConnected = data.mqtt_connected;
    mqttConfigured = data.mqtt_configured;
    lobaroTokenSet = data.lobaro_token_set;
    if (lobaroBanner) {
      lobaroBanner.classList.toggle("hidden", lobaroTokenSet);
    }
    updateStatusCards();
  } catch (err) {
    if (lobaroBanner) {
      lobaroBanner.classList.remove("hidden");
    }
    if (lobaroBanner) {
      lobaroBanner.classList.remove("hidden");
    }
  }
};

const loadMqtt = async () => {
  try {
    const data = await fetchJson("/api/mqtt");
    mqttForm.url.value = data.url;
    mqttForm.username.value = data.username || "";
    mqttForm.topic_template.value = data.topic_template;
    mqttForm.qos.value = data.qos;
    mqttForm.retain.value = data.retain ? "true" : "false";
    mqttForm.password.value = "";
    mqttConfigured = data.configured;
    const lockHint = "Managed by environment. Edit the env file to change.";
    mqttForm.url.disabled = data.locked_url;
    mqttForm.username.disabled = data.locked_username;
    mqttForm.password.disabled = data.locked_password;
    mqttForm.topic_template.disabled = data.locked_topic;
    mqttForm.url.title = data.locked_url ? lockHint : "";
    mqttForm.username.title = data.locked_username ? lockHint : "";
    mqttForm.password.title = data.locked_password ? lockHint : "";
    mqttForm.topic_template.title = data.locked_topic ? lockHint : "";
    if (!data.configured) {
      mqttStatus.textContent = "Not configured";
    } else if (data.password_set) {
      mqttStatus.textContent = "Configured (password set)";
    } else {
      mqttStatus.textContent = "Configured";
    }
    updateStatusCards();
  } catch (err) {
    mqttStatus.textContent = "Load failed";
  }
};

const saveMqtt = async (event) => {
  event.preventDefault();
  mqttStatus.textContent = "Saving...";
  const payload = {
    url: mqttForm.url.value.trim(),
    username: mqttForm.username.value.trim() || null,
    password: mqttForm.password.value,
    topic_template: mqttForm.topic_template.value.trim(),
    qos: Number(mqttForm.qos.value || 0),
    retain: mqttForm.retain.value === "true",
  };
  try {
    const data = await fetchJson("/api/mqtt", {
      method: "PUT",
      body: JSON.stringify(payload),
    });
    mqttStatus.textContent = data.password_set ? "Saved (password set)" : "Saved";
    mqttForm.password.value = "";
    mqttConfigured = data.configured;
    updateStatusCards();
    toast("MQTT config saved", "success");
  } catch (err) {
    mqttStatus.textContent = "Save failed";
    toast("MQTT save failed", "error");
  }
};

const testMqtt = async () => {
  mqttStatus.textContent = "Testing...";
  try {
    await fetchJson("/api/mqtt/test", { method: "POST" });
    mqttStatus.textContent = "Connected";
    mqttConnected = true;
    updateStatusCards();
    toast("MQTT connected", "success");
  } catch (err) {
    mqttConnected = false;
    updateStatusCards();
    toast(`MQTT not reachable (${err.message})`, "error");
  }
};

const sendTestMessage = async () => {
  mqttStatus.textContent = "Sending test...";
  try {
    const data = await fetchJson("/api/mqtt/test-message", { method: "POST" });
    mqttStatus.textContent = `Test sent to ${data.topic}`;
    toast("MQTT test sent", "success");
  } catch (err) {
    toast(`MQTT test failed (${err.message})`, "error");
  }
};

const clearList = (el) => {
  while (el.firstChild) {
    el.removeChild(el.firstChild);
  }
};

const renderListItem = (textLeft, textRight) => {
  const item = document.createElement("div");
  item.className = "list-item";
  const left = document.createElement("div");
  left.className = "column";
  const primary = document.createElement("span");
  primary.className = "mono";
  primary.textContent = textLeft;
  const secondary = document.createElement("span");
  secondary.className = "muted";
  secondary.textContent = textRight;
  left.appendChild(primary);
  left.appendChild(secondary);
  item.appendChild(left);
  return { item, left };
};

const renderPending = (meters) => {
  clearList(pendingList);
  pendingCount = meters.length;
  if (meters.length === 0) {
    const empty = document.createElement("div");
    empty.className = "list-item";
    empty.innerHTML = "<span class=\"muted\">No new meters.</span>";
    pendingList.appendChild(empty);
    updateStatusCards();
    return;
  }
  meters.forEach((meter) => {
    const { item } = renderListItem(
      meter.meter_id,
      meter.last_seen ? `Last seen: ${meter.last_seen}` : "Last seen: unknown"
    );
    const actions = document.createElement("div");
    actions.className = "list-actions";
    const addBtn = document.createElement("button");
    addBtn.className = "primary";
    addBtn.type = "button";
    addBtn.textContent = "Add";
    addBtn.addEventListener("click", (event) => {
      event.stopPropagation();
      openKeyModal(meter.meter_id);
    });
    actions.appendChild(addBtn);
    item.appendChild(actions);
    pendingList.appendChild(item);
  });
  updateStatusCards();
};

const renderKnown = (meters) => {
  clearList(knownList);
  knownCount = meters.length;
  if (meters.length === 0) {
    const empty = document.createElement("div");
    empty.className = "list-item";
    empty.innerHTML = "<span class=\"muted\">No known meters.</span>";
    knownList.appendChild(empty);
    updateStatusCards();
    return;
  }
  meters.forEach((meter) => {
    const forwarded = meter.forwarded_count || 0;
    const lastSeen = meter.last_seen ? `Last seen: ${meter.last_seen}` : "Last seen: unknown";
    const { item } = renderListItem(meter.meter_id, `Forwarded: ${forwarded} • ${lastSeen}`);
    item.classList.add("meter-row");

    const actions = document.createElement("div");
    actions.className = "list-actions";

    const viewBtn = document.createElement("button");
    viewBtn.className = "accent";
    viewBtn.type = "button";
    viewBtn.textContent = "View";
    viewBtn.addEventListener("click", (event) => {
      event.stopPropagation();
      openTelegrams(meter.meter_id);
    });

    const delBtn = document.createElement("button");
    delBtn.className = "icon-btn icon-trash";
    delBtn.type = "button";
    delBtn.title = "Delete";
    delBtn.addEventListener("click", (event) => {
      event.stopPropagation();
      deleteKey(meter.meter_id);
    });

    actions.appendChild(viewBtn);
    actions.appendChild(delBtn);
    item.appendChild(actions);

    item.addEventListener("click", () => openTelegrams(meter.meter_id));
    knownList.appendChild(item);
  });
  updateStatusCards();
};

const loadPending = async () => {
  try {
    const data = await fetchJson("/api/meters/pending");
    renderPending(data.meters || []);
  } catch (err) {
    toast(`Failed to load pending meters (${err.message})`, "error");
  }
};

const loadKnown = async () => {
  try {
    const data = await fetchJson("/api/meters/known");
    renderKnown(data.meters || []);
  } catch (err) {
    toast(`Failed to load known meters (${err.message})`, "error");
  }
};

const openKeyModal = (meterId) => {
  activeMeterId = meterId;
  keyMeterId.textContent = meterId;
  keyInput.value = "";
  keyInput.classList.remove("input-error");
  if (keyError) {
    keyError.classList.add("hidden");
  }
  keyModal.classList.remove("hidden");
};

const closeKeyModal = () => {
  keyModal.classList.add("hidden");
  activeMeterId = null;
};

const showKeyError = (message) => {
  if (!keyError) {
    return;
  }
  keyError.textContent = message;
  keyError.classList.remove("hidden");
  keyInput.classList.add("input-error");
};

const saveKey = async () => {
  if (!activeMeterId) {
    return;
  }
  const keyHex = keyInput.value.trim();
  const hexPattern = /^[0-9a-fA-F]{32}$/;
  if (!hexPattern.test(keyHex)) {
    showKeyError("Key must be 32 hex characters.");
    return;
  }
  try {
    await fetchJson(`/api/keys/${encodeURIComponent(activeMeterId)}`, {
      method: "PUT",
      body: JSON.stringify({ key_hex: keyHex }),
    });
    toast("Key saved", "success");
    closeKeyModal();
    await loadPending();
    await loadKnown();
  } catch (err) {
    showKeyError("Failed to save key.");
  }
};

const deleteKey = async (meterId) => {
  if (!confirm(`Remove key for meter ${meterId}?`)) {
    return;
  }
  try {
    await fetchJson(`/api/keys/${encodeURIComponent(meterId)}`, {
      method: "DELETE",
    });
    toast("Key removed", "success");
    await loadKnown();
  } catch (err) {
    toast("Failed to delete key", "error");
  }
};

const openTelegrams = async (meterId) => {
  telegramsMeterId.textContent = meterId;
  telegramInput.textContent = "Select a telegram to view details.";
  telegramParsed.textContent = "Select a telegram to view details.";
  activeTelegramId = null;
  clearList(telegramsList);
  telegramsModal.classList.remove("hidden");

  try {
    const data = await fetchJson(`/api/meters/${encodeURIComponent(meterId)}/telegrams`);
    const items = data.telegrams || [];
    if (items.length === 0) {
      const empty = document.createElement("div");
      empty.className = "list-item";
      empty.innerHTML = "<span class=\"muted\">No telegrams stored.</span>";
      telegramsList.appendChild(empty);
      return;
    }
    items.forEach((telegram) => {
      const item = document.createElement("div");
      item.className = "list-item selectable";
      item.textContent = `${telegram.received_at} • ${telegram.status}`;
      item.addEventListener("click", () => loadTelegramDetail(meterId, telegram.id, item));
      telegramsList.appendChild(item);
    });
    const first = telegramsList.querySelector(".list-item.selectable");
    if (first && items[0]) {
      loadTelegramDetail(meterId, items[0].id, first);
    }
  } catch (err) {
    const errorItem = document.createElement("div");
    errorItem.className = "list-item";
    errorItem.innerHTML = "<span class=\"muted\">Failed to load telegrams.</span>";
    telegramsList.appendChild(errorItem);
  }
};

const loadTelegramDetail = async (meterId, telegramId, listItem) => {
  if (activeTelegramId === telegramId) {
    return;
  }
  activeTelegramId = telegramId;
  Array.from(telegramsList.children).forEach((child) => child.classList.remove("active"));
  listItem.classList.add("active");
  telegramInput.textContent = "Loading...";
  telegramParsed.textContent = "Loading...";
  try {
    const detail = await fetchJson(`/api/meters/${encodeURIComponent(meterId)}/telegrams/${telegramId}`);
    telegramInput.textContent = JSON.stringify(detail.payload, null, 2);
    telegramParsed.textContent = detail.parsed ? JSON.stringify(detail.parsed, null, 2) : "No parsed payload.";
  } catch (err) {
    telegramInput.textContent = "Failed to load telegram.";
    telegramParsed.textContent = "Failed to load telegram.";
  }
};

const closeTelegrams = () => {
  telegramsModal.classList.add("hidden");
  clearList(telegramsList);
};

const bindModalClose = () => {
  document.querySelectorAll("[data-close]").forEach((el) => {
    el.addEventListener("click", () => {
      const target = el.getAttribute("data-close");
      if (target === "key") {
        closeKeyModal();
      }
      if (target === "telegrams") {
        closeTelegrams();
      }
    });
  });
};

mqttForm.addEventListener("submit", saveMqtt);
mqttTestBtn.addEventListener("click", testMqtt);
if (mqttSendTestBtn) {
  mqttSendTestBtn.addEventListener("click", sendTestMessage);
}
if (themeToggle) {
  themeToggle.addEventListener("click", () => {
    const current = document.documentElement.dataset.theme || "light";
    applyTheme(current === "dark" ? "light" : "dark");
  });
}
refreshPendingBtn.addEventListener("click", loadPending);
refreshKnownBtn.addEventListener("click", loadKnown);
if (keySaveBtn) {
  keySaveBtn.addEventListener("click", saveKey);
}

bindModalClose();
applyAllIcons();
applyTheme(localStorage.getItem(THEME_KEY) || "light");
loadMqtt();
loadPending();
loadKnown();
loadHealth();
