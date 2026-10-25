"use strict";

const $ = (query) => document.querySelector(query);
const state = {
  backups: [],
  selected: null,
  unlocked: false,
  generation: 0,
  epoch: 0,
  cache: {},
  view: "home",
  busy: false,
  history: false,
  unlockedCount: 0,
};
const appNames = {
  photos: "Fotos",
  conversations: "Nachrichten",
  contacts: "Kontakte",
  notes: "Notizen",
  recordings: "Sprachmemos",
};

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key.startsWith("on"))
      node.addEventListener(key.slice(2).toLowerCase(), value);
    else if (key === "class") node.className = value;
    else if (value !== null && value !== undefined)
      node.setAttribute(key, value);
  }
  for (const child of children.flat()) {
    if (child !== null && child !== undefined)
      node.append(
        child instanceof Node ? child : document.createTextNode(String(child)),
      );
  }
  return node;
}

async function api(path, data) {
  const response = await fetch(`/api/${path}`, {
    method: data === undefined ? "GET" : "POST",
    headers:
      data === undefined
        ? {}
        : { "Content-Type": "application/json", "X-iBackup": "1" },
    body: data === undefined ? undefined : JSON.stringify(data),
    credentials: "same-origin",
    cache: "no-store",
  });
  const value = await response.json();
  if (!response.ok) {
    const error = new Error(
      value.error || "Die Ansicht konnte nicht geladen werden.",
    );
    error.code = value.code;
    error.status = response.status;
    throw error;
  }
  return value;
}

function formatDate(value, options = {}) {
  if (!value) return "Datum unbekannt";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "Datum unbekannt"
    : date.toLocaleDateString("de-DE", {
        day: "numeric",
        month: "short",
        year: "numeric",
        ...options,
      });
}

function initials(name) {
  return name
    .split(/\s+/)
    .slice(0, 2)
    .map((part) => [...part][0] || "")
    .join("")
    .toUpperCase();
}

function icon(name) {
  const artwork = {
    recordings:
      '<rect width="64" height="64" rx="14" fill="#242424"/><path d="M7 30v5m5-12v19m5-27v35m5-31v27m5-38v49m5-42v34m5-28v22m5-32v42m5-35v28m5-21v14m5-10v7" stroke="white" stroke-width="2"/><path d="M32 9v46" stroke="#f25256" stroke-width="2"/>',
    photos:
      '<rect width="64" height="64" rx="14" fill="white"/>' +
      [
        "#ffad35",
        "#ffd645",
        "#93cc5c",
        "#43bfac",
        "#5caff3",
        "#7778d1",
        "#d969ac",
        "#f57870",
      ]
        .map(
          (color, i) =>
            `<ellipse cx="32" cy="20" rx="9" ry="14" fill="${color}" fill-opacity=".82" transform="rotate(${i * 45} 32 32)"/>`,
        )
        .join("") +
      '<circle cx="32" cy="32" r="5" fill="#ffe9b5" fill-opacity=".6"/>',
    conversations:
      '<defs><linearGradient id="message-green" x2="0" y2="1"><stop stop-color="#85ed56"/><stop offset="1" stop-color="#1fc348"/></linearGradient></defs><rect width="64" height="64" rx="14" fill="url(#message-green)"/><ellipse cx="32" cy="29" rx="23" ry="19" fill="white"/><path d="M18 43 15 52 28 46" fill="white"/>',
    contacts:
      '<rect width="64" height="64" rx="14" fill="#c7b69e"/><path d="M13 0v64" stroke="#a18f77" stroke-width="2"/><path d="M17 0v64" stroke="#e1d7c7" stroke-width="1"/><circle cx="36" cy="23" r="9" fill="#94836c"/><path d="M20 49c0-10 7-15 16-15s16 5 16 15" fill="#94836c"/><path d="M60 10v10" stroke="#77b8dc" stroke-width="8"/><path d="M60 23v10" stroke="#9bcb7a" stroke-width="8"/><path d="M60 36v10" stroke="#e9ad7b" stroke-width="8"/>',
    notes:
      '<rect width="64" height="64" rx="14" fill="#fffef8"/><path d="M0 14Q0 0 14 0h36q14 0 14 14v5H0" fill="#ffdb4e"/><path d="M0 20h64" stroke="#e8bd37" stroke-width="1"/><path d="M0 32h64M0 44h64M0 56h64" stroke="#e5e2d6" stroke-width="1"/><path d="M0 23h64" stroke="#f1f0e6" stroke-dasharray="2 2"/>',
  };
  const node = el("span", { class: "icon", "aria-hidden": "true" });
  // Only fixed, developer-owned SVG markup; backup content always uses text nodes.
  node.innerHTML = `<svg viewBox="0 0 64 64" xmlns="http://www.w3.org/2000/svg">${artwork[name]}</svg>`;
  return node;
}

function showSidebarError(message = "") {
  $("#sidebar-error").textContent = message;
  $("#sidebar-error").hidden = !message;
}

function renderMetadata() {
  const backup = state.selected;
  $("#backup-warning").textContent = backup?.layout?.notice || "";
  $("#backup-warning").hidden = !backup?.layout?.notice;
  $("#lock-button").disabled = !state.unlocked && !state.unlockedCount;
  $("#backup-meta").replaceChildren();
  if (!backup) {
    $("#backup-meta").textContent =
      "Kein Backup gefunden. Wähle einen lokalen Backupordner.";
    $("#device-caption").textContent = "Dein Backup wartet hier.";
    return;
  }
  for (const [label, value] of [
    ["Gerät", `${backup.model} · iOS ${backup.ios}`],
    ["Gesichert", formatDate(backup.date)],
    [
      "Zugriff",
      state.unlocked
        ? "Entsperrt · nur lesend"
        : backup.encrypted
          ? "Verschlüsselt"
          : "Nicht verschlüsselt",
    ],
  ]) {
    $("#backup-meta").append(
      el(
        "div",
        { class: "meta-row" },
        el("span", {}, label),
        el("strong", {}, value),
      ),
    );
  }
  $("#device-caption").textContent = `${backup.model} · iOS ${backup.ios}`;
}

function renderSelect() {
  const select = $("#backup-select");
  select.replaceChildren(
    ...state.backups.map((backup) =>
      el(
        "option",
        { value: backup.id },
        `${backup.name} · ${formatDate(backup.date)}`,
      ),
    ),
  );
  if (!state.backups.length)
    select.append(el("option", {}, "Kein Backup gefunden"));
  if (state.selected) select.value = state.selected.id;
  select.disabled = !state.backups.length;
  renderMetadata();
  renderTimeline();
}

function renderTimeline() {
  const backups = state.backups
    .filter(
      (backup) =>
        backup.id === state.selected?.id ||
        (state.selected?.device_id &&
          backup.device_id === state.selected.device_id),
    )
    .sort((a, b) => new Date(a.date || 0) - new Date(b.date || 0));
  $("#snapshot-count").textContent =
    `${backups.length} ${backups.length === 1 ? "Stand" : "Stände"}`;
  const timeline = $("#timeline");
  timeline.replaceChildren();
  const index = Math.max(
    0,
    backups.findIndex((backup) => backup.id === state.selected?.id),
  );
  const previous = el(
    "button",
    {
      type: "button",
      onClick: () => selectBackup(backups[index - 1].id),
      "aria-label": "Älterer Backup-Stand",
    },
    "← Älter",
  );
  previous.disabled = index === 0 || state.busy;
  const next = el(
    "button",
    {
      type: "button",
      onClick: () => selectBackup(backups[index + 1].id),
      "aria-label": "Neuerer Backup-Stand",
    },
    "Neuer →",
  );
  next.disabled = index >= backups.length - 1 || state.busy;
  const slider = el("input", {
    type: "range",
    min: "0",
    max: String(Math.max(0, backups.length - 1)),
    value: String(index),
    step: "1",
    "aria-label": "Backup-Zeitpunkt",
    onChange: (event) => selectBackup(backups[Number(event.target.value)].id),
  });
  slider.disabled = backups.length < 2 || state.busy;
  timeline.append(
    el("div", { class: "time-controls" }, previous, next),
    slider,
  );
  backups.forEach((backup) =>
    timeline.append(
      el(
        "button",
        {
          type: "button",
          class: `snapshot ${backup.id === state.selected?.id ? "selected" : ""}`,
          onClick: () => selectBackup(backup.id),
        },
        el("span", {}, formatDate(backup.date)),
        el("small", {}, backup.unlocked ? "Entsperrt" : "Öffnen"),
      ),
    ),
  );
  timeline.append(
    el(
      "p",
      { class: "timeline-help" },
      backups.length < 2
        ? "Ein Backup ist ein Zeitpunkt. Füge einen vollständigen älteren Stand als getrennten Ordner hinzu. Zusammengeführte Dateien ergeben keinen zweiten Stand."
        : "Öffne ältere Stände einmal pro Sitzung. Danach lassen sich ihre Inhalte gemeinsam anzeigen.",
    ),
  );
}

function applyStatus(result) {
  state.selected = result.backup;
  state.unlocked = Boolean(result.backup?.unlocked);
  state.unlockedCount = result.unlocked_count ?? (state.unlocked ? 1 : 0);
  state.generation = result.generation;
  state.cache = {};
  state.backups = state.backups.map((backup) =>
    backup.id === state.selected?.id
      ? { ...backup, unlocked: state.unlocked }
      : backup,
  );
}

async function refreshBackups() {
  if (state.busy) return;
  state.busy = true;
  $("#refresh-backups").disabled = true;
  showSidebarError();
  try {
    const [backups, status] = await Promise.all([api("backups"), api("status")]);
    const selectedId = state.selected?.id;
    state.backups = backups;
    applyStatus(status);
    if (!state.selected) {
      state.selected = backups.find((backup) => backup.id === selectedId) || backups[0] || null;
    }
  } catch (error) {
    showSidebarError(error.message);
  } finally {
    state.busy = false;
    $("#refresh-backups").disabled = false;
    renderSelect();
    home();
  }
}

async function selectBackup(id) {
  if (state.busy) return;
  state.busy = true;
  state.epoch++;
  $("#display").replaceChildren(empty("Zeitpunkt wird geöffnet …", "", true));
  try {
    applyStatus(await api("select", { id }));
    state.busy = false;
    renderSelect();
    home();
  } catch (error) {
    state.busy = false;
    showSidebarError(error.message);
    home();
  }
}

function statusLabel(item) {
  return [
    item.deleted ? "Gelöscht" : "",
    item.fragment ? "Fragment" : "",
    item.historical ? "Historisch" : "",
  ]
    .filter(Boolean)
    .join(" · ");
}

function provenance(item) {
  const label = statusLabel(item);
  return label
    ? el(
        "span",
        { class: `provenance ${item.deleted ? "deleted" : "historical"}` },
        label + (item.historical ? ` · ${formatDate(item.source_date)}` : ""),
      )
    : null;
}

function duration(value) {
  return value == null
    ? "Audioaufnahme"
    : `${Math.floor(value / 60)}:${String(Math.floor(value % 60)).padStart(2, "0")}`;
}

function empty(title, description, loading = false) {
  return el(
    "div",
    { class: "empty-state" },
    el(
      "span",
      { class: loading ? "spinner" : "empty-symbol", "aria-hidden": "true" },
      loading ? "" : "◌",
    ),
    el("h3", {}, title),
    el("p", {}, description),
  );
}

function home() {
  if (state.busy) return;
  state.epoch++;
  state.view = "home";
  $("#screen").className = "screen home";
  const content = el("div", { class: "home-content" });
  const date = state.selected?.date;
  content.append(
    el(
      "div",
      { class: "home-date" },
      el("div", { class: "large-time" }, "9:41"),
      el(
        "div",
        { class: "date-line" },
        date
          ? formatDate(date, { weekday: "long", month: "long" })
          : "Deine Erinnerungen. Dein Gerät.",
      ),
    ),
  );
  const grid = el("div", { class: "app-grid" });
  for (const [name, label] of Object.entries(appNames)) {
    if (name === "recordings" && !state.unlocked) continue;
    grid.append(
      el(
        "button",
        {
          class: "app-icon",
          type: "button",
          onClick: () => (state.unlocked ? openApp(name) : focusUnlock()),
          "aria-label": label,
        },
        icon(name),
        el("span", {}, label),
      ),
    );
  }
  content.append(grid);
  if (state.unlocked) {
    content.append(
      el(
        "p",
        { class: "home-note" },
        "Ein Moment von damals.",
        el("br"),
        "Tippe auf eine App und schau dich um.",
      ),
      el(
        "div",
        { class: "page-dots", "aria-hidden": "true" },
        el("i"),
        el("i"),
      ),
      el(
        "div",
        { class: "home-dock" },
        el("span", { class: "dock-symbol", "aria-hidden": "true" }, "✓"),
        el(
          "span",
          {},
          el("strong", {}, "Zum Anschauen. Zum Erinnern."),
          "Dieses Backup lässt sich hier nur lesen.",
        ),
      ),
    );
  } else {
    content.append(unlockPanel());
  }
  $("#display").replaceChildren(content);
  renderMetadata();
}

function focusUnlock() {
  $("#password")?.focus();
}

function unlockPanel() {
  const panel = el("section", {
    class: "unlock-panel",
    "aria-label": "Backup entsperren",
  });
  if (!state.selected) {
    panel.append(
      el("h2", {}, "Hallo, Erinnerungen."),
      el(
        "p",
        {},
        "Wähle links ein lokales Backup. Dann kannst du hier durch deine Inhalte blättern.",
      ),
    );
    return panel;
  }
  if (!state.selected.supported) {
    panel.append(
      el("h2", {}, "Ein neueres Backup."),
      el(
        "p",
        {},
        "Diese erste Version liest ältere iOS-Backups mit Manifest.mbdb. Dein Backup nutzt das neuere Manifest.db-Format.",
      ),
    );
    return panel;
  }
  const encrypted = state.selected.encrypted;
  panel.append(
    el("h2", {}, encrypted ? "Willkommen zurück." : "Dein Backup ist bereit."),
    el(
      "p",
      {},
      encrypted
        ? "Entsperre dein Backup, um deine Erinnerungen zu öffnen."
        : "Fotos, Nachrichten, Kontakte und Notizen. Alles nur zum Lesen.",
    ),
  );
  const form = el("form", { onSubmit: unlock });
  if (encrypted) {
    const input = el("input", {
      id: "password",
      type: "password",
      placeholder: "Backup-Passwort",
      "aria-label": "Backup-Passwort",
      autocomplete: "off",
      spellcheck: "false",
      maxlength: "1024",
    });
    const toggle = el(
      "button",
      {
        type: "button",
        "aria-label": "Passwort anzeigen",
        onClick: () => {
          input.type = input.type === "password" ? "text" : "password";
          toggle.textContent =
            input.type === "password" ? "Zeigen" : "Verbergen";
          toggle.setAttribute(
            "aria-label",
            input.type === "password"
              ? "Passwort anzeigen"
              : "Passwort verbergen",
          );
        },
      },
      "Zeigen",
    );
    form.append(el("div", { class: "password-row" }, input, toggle));
  }
  form.append(
    el(
      "button",
      { class: "primary", type: "submit" },
      encrypted ? "Backup entsperren" : "Backup öffnen",
    ),
    el("p", {
      id: "unlock-error",
      class: "inline-error",
      role: "alert",
      hidden: "",
    }),
  );
  panel.append(
    form,
    el(
      "p",
      { class: "lock-caption" },
      encrypted
        ? "Das Passwort wird nicht gespeichert."
        : "Dein Originalbackup bleibt unverändert.",
    ),
  );
  return panel;
}

async function unlock(event) {
  event.preventDefault();
  if (state.busy) return;
  state.busy = true;
  $("#home-button").disabled = true;
  $("#backup-select").disabled = true;
  $("#choose-path").disabled = true;
  const ticket = ++state.epoch;
  const form = event.currentTarget;
  const button = form.querySelector(".primary");
  const input = $("#password");
  const password = input?.value || "";
  if (input) input.value = "";
  button.disabled = true;
  button.textContent = "Wird entsperrt …";
  $("#unlock-error").hidden = true;
  try {
    const result = await api("open", { id: state.selected.id, password });
    if (ticket !== state.epoch) return;
    applyStatus(result);
    state.busy = false;
    renderSelect();
    home();
  } catch (error) {
    if (ticket !== state.epoch) return;
    state.unlocked = false;
    state.unlockedCount = 0;
    state.backups.forEach((backup) => (backup.unlocked = false));
    renderMetadata();
    renderTimeline();
    const label = $("#unlock-error");
    label.textContent = error.message;
    label.hidden = false;
    button.disabled = false;
    button.textContent = "Erneut versuchen";
    input?.focus();
  } finally {
    state.busy = false;
    $("#home-button").disabled = false;
    $("#backup-select").disabled = !state.backups.length;
    $("#choose-path").disabled = false;
  }
}

async function lockBackup() {
  if (state.busy) return;
  state.epoch++;
  state.unlocked = false;
  state.unlockedCount = 0;
  state.backups.forEach((backup) => (backup.unlocked = false));
  state.cache = {};
  home();
  renderTimeline();
  try {
    await api("lock", {});
  } catch (error) {
    fatal(
      "Sperren nicht bestätigt",
      "Schließe das Serverfenster, um die Sitzung sicher zu beenden. " +
        error.message,
    );
  }
}

function shell(title, back = home, extra = "") {
  $("#screen").className = `screen in-app ${extra}`;
  const heading = el("h2", {}, title);
  const nav = el(
    "div",
    { class: "navigation" },
    el(
      "button",
      {
        class: "back-button",
        type: "button",
        onClick: back,
        "aria-label": "Zurück",
      },
      "Zurück",
    ),
    heading,
  );
  const body = el("div", { class: "app-body" });
  $("#display").replaceChildren(nav, body);
  return body;
}

async function openApp(name) {
  if (!state.unlocked) return home();
  const ticket = ++state.epoch;
  state.view = name;
  const body = shell(appNames[name], home, name);
  body.append(empty("Einen Moment …", "Dein Backup wird gelesen.", true));
  try {
    const result =
      state.cache[name] || (await api(`${name}?history=${state.history}`));
    if (ticket !== state.epoch) return;
    state.cache[name] = result;
    const items = Array.isArray(result) ? result : result.items;
    body.replaceChildren();
    if (result.notice)
      body.append(el("p", { class: "content-notice" }, result.notice));
    if (!items.length) {
      body.append(
        empty(
          "Hier ist es noch still.",
          `Keine ${appNames[name]} in diesem Backup gefunden.`,
        ),
      );
      return;
    }
    if (name === "photos") renderPhotos(body, items);
    else renderList(body, name, items);
  } catch (error) {
    if (ticket !== state.epoch) return;
    if (error.status === 423) {
      state.unlocked = false;
      state.cache = {};
      home();
      return;
    }
    body.replaceChildren(
      empty(
        error.code === "missing_content"
          ? "Nicht im Backup"
          : "Noch nicht lesbar",
        error.message,
      ),
    );
  }
}

function mediaURL(item) {
  return `/api/media/${encodeURIComponent(item.id)}?generation=${state.generation}&source=${encodeURIComponent(item.source_id || state.selected.id)}`;
}

async function mediaFailure(item) {
  try {
    const response = await fetch(mediaURL(item), {
      headers: { Range: "bytes=0-0" },
      credentials: "same-origin",
      cache: "no-store",
    });
    if (!response.ok) {
      const error = await response.json();
      return error.error || "Diese Aufnahme konnte nicht gelesen werden.";
    }
    return "Die Datei ist lesbar, aber dein Browser kann sie nicht anzeigen. Der Codec oder die Mediendatei wird nicht unterstützt.";
  } catch {
    return "Die Aufnahme konnte nicht geladen werden. Prüfe, ob die lokale App noch läuft.";
  }
}

function renderPhotos(body, photos) {
  const grid = el("div", { class: "photos-grid" });
  photos.forEach((photo, index) => {
    const button = el("button", {
      class: "photo-tile",
      type: "button",
      "aria-label": `${photo.kind === "video" ? "Video" : "Foto"} ${photo.name}`,
      onClick: () => showPhoto(photos, index),
    });
    if (photo.kind === "video") {
      button.append(
        el("span", { class: "video-placeholder", "aria-hidden": "true" }, "▷"),
        el("span", { class: "video-label" }, "VIDEO"),
      );
    } else {
      button.append(
        el("img", {
          src: mediaURL(photo),
          alt: photo.name,
          loading: "lazy",
          decoding: "async",
          onError: () => button.classList.add("broken"),
        }),
      );
    }
    if (statusLabel(photo))
      button.append(el("span", { class: "photo-status" }, statusLabel(photo)));
    grid.append(button);
  });
  body.append(
    grid,
    el(
      "div",
      { class: "list-count" },
      `${photos.filter((p) => p.kind === "photo").length} Fotos · ${photos.filter((p) => p.kind === "video").length} Videos`,
    ),
  );
  $("#display").append(
    el(
      "div",
      { class: "tabbar" },
      el("span", { class: "active" }, "Alle Aufnahmen"),
      el("span", {}, "Nur im Backup gespeicherte Medien"),
    ),
  );
}

function showPhoto(photos, index) {
  state.epoch++;
  state.view = "photo";
  const photo = photos[index];
  const body = shell(photo.name, () => openApp("photos"));
  body.className = "viewer";
  const mediaArea = el("div", { class: "viewer-media" });
  const media = el(photo.kind === "video" ? "video" : "img", {
    src: mediaURL(photo),
    alt: photo.name,
    ...(photo.kind === "video"
      ? { controls: "", playsinline: "", preload: "metadata" }
      : {}),
    onError: async () =>
      mediaArea.replaceChildren(
        el("p", { class: "media-error" }, await mediaFailure(photo)),
      ),
  });
  mediaArea.append(media);
  const previous = el(
    "button",
    {
      type: "button",
      "aria-label": "Vorherige Aufnahme",
      onClick: () => showPhoto(photos, index - 1),
    },
    "‹",
  );
  previous.disabled = index === 0;
  const next = el(
    "button",
    {
      type: "button",
      "aria-label": "Nächste Aufnahme",
      onClick: () => showPhoto(photos, index + 1),
    },
    "›",
  );
  next.disabled = index === photos.length - 1;
  body.append(
    mediaArea,
    el(
      "div",
      { class: "viewer-controls" },
      previous,
      el("span", {}, `${index + 1} von ${photos.length}`),
      next,
    ),
    provenance(photo),
  );
}

function renderList(body, name, items) {
  const search = el("input", {
    type: "search",
    placeholder: "Suchen",
    "aria-label": `${appNames[name]} durchsuchen`,
    autocomplete: "off",
  });
  const list = el("div", { class: "list" });
  const count = el("div", { class: "list-count" });
  function update() {
    const query = search.value.toLocaleLowerCase("de-DE");
    const filtered = items.filter((item) =>
      [
        item.name,
        item.title,
        item.body,
        item.preview,
        ...(item.phones || []),
        ...(item.emails || []),
      ]
        .filter(Boolean)
        .join(" ")
        .toLocaleLowerCase("de-DE")
        .includes(query),
    );
    list.replaceChildren(
      ...filtered.map((item) => {
        const title = item.name || item.title;
        const subtitle =
          name === "notes"
            ? item.body
            : name === "conversations"
              ? item.preview
              : name === "recordings"
                ? `${duration(item.duration)} · ${formatDate(item.date)}`
                : item.organization ||
                  item.phones?.[0] ||
                  item.emails?.[0] ||
                  "";
        const row = el("button", {
          class: `list-row ${name === "notes" ? "note-row" : ""}`,
          type: "button",
          onClick: () =>
            name === "contacts"
              ? showContact(item)
              : name === "notes"
                ? showNote(item)
                : name === "recordings"
                  ? showRecording(item)
                  : showChat(item),
        });
        if (name !== "notes")
          row.append(
            el(
              "span",
              { class: "avatar", "aria-hidden": "true" },
              initials(title),
            ),
          );
        row.append(
          el(
            "div",
            { class: "row-main" },
            el("div", { class: "row-title" }, title),
            el("div", { class: "row-subtitle" }, subtitle),
            provenance(item),
          ),
          el("span", { class: "chevron", "aria-hidden": "true" }, "›"),
        );
        return row;
      }),
    );
    count.textContent = filtered.length
      ? `${filtered.length} ${appNames[name]}`
      : "Keine Treffer";
  }
  search.addEventListener("input", update);
  body.append(el("div", { class: "search-box" }, search), list, count);
  update();
}

function showContact(contact) {
  state.epoch++;
  state.view = "contact";
  const body = shell("Kontakt", () => openApp("contacts"));
  if (provenance(contact)) body.append(provenance(contact));
  body.append(
    el(
      "div",
      { class: "contact-hero" },
      el(
        "span",
        { class: "avatar", "aria-hidden": "true" },
        initials(contact.name),
      ),
      el("h3", {}, contact.name),
      contact.organization ? el("p", {}, contact.organization) : null,
    ),
  );
  const fields = el("div", { class: "contact-fields" });
  for (const [label, values] of [
    ["Telefon", contact.phones],
    ["E-Mail", contact.emails],
  ]) {
    for (const value of values)
      fields.append(
        el(
          "div",
          { class: "contact-field" },
          el("small", {}, label),
          el("p", {}, value),
        ),
      );
  }
  if (!contact.phones.length && !contact.emails.length)
    fields.append(
      el(
        "p",
        { class: "list-count" },
        "Keine Telefonnummer oder E-Mail gespeichert.",
      ),
    );
  body.append(fields);
  $("#display").append(
    el(
      "div",
      { class: "readonly-bar" },
      "Kontakt aus deinem Backup · nur lesend",
    ),
  );
}

function showNote(note) {
  state.epoch++;
  state.view = "note";
  const body = shell("Notiz", () => openApp("notes"), "notes");
  if (note.notice)
    body.append(el("p", { class: "content-notice" }, note.notice));
  if (provenance(note)) body.append(provenance(note));
  body.append(
    el(
      "article",
      { class: "note-body" },
      el("div", { class: "note-date" }, formatDate(note.date)),
      el("h3", {}, note.title),
      el("div", {}, note.body),
    ),
  );
  $("#display").append(
    el("div", { class: "readonly-bar" }, "Gespeicherte Notiz · nur lesend"),
  );
}

function showRecording(recording) {
  state.epoch++;
  state.view = "recording";
  const body = shell("Sprachmemo", () => openApp("recordings"));
  const player = el(
    "div",
    { class: "recording-player" },
    el(
      "div",
      { class: "recording-art", "aria-hidden": "true" },
      icon("recordings"),
    ),
    el("h3", {}, recording.name),
    el("p", {}, formatDate(recording.date)),
    provenance(recording),
  );
  const audio = el("audio", {
    controls: "",
    preload: "metadata",
    src: mediaURL(recording),
    "aria-label": recording.name,
    onError: async () =>
      player.append(
        el("p", { class: "inline-error" }, await mediaFailure(recording)),
      ),
  });
  player.append(
    audio,
    el(
      "p",
      { class: "recording-hint" },
      recording.unindexed
        ? "Aufnahmedatei vorhanden. Kein eindeutiger Datenbankeintrag gefunden."
        : "Originalaufnahme aus deinem Backup.",
    ),
  );
  body.append(player);
  $("#display").append(
    el("div", { class: "readonly-bar" }, "Nur Wiedergabe · keine Bearbeitung"),
  );
}

async function showChat(chat) {
  const ticket = ++state.epoch;
  state.view = "chat";
  const body = shell(chat.name, () => openApp("conversations"));
  body.append(empty("Gespräch wird geöffnet …", "", true));
  $("#display").append(
    el("div", { class: "readonly-bar" }, "Gespeichertes Gespräch · nur lesend"),
  );
  let offset = 0;
  let all = [];
  async function load(older = false) {
    try {
      const page = await api(
        `messages/${chat.id}?offset=${offset}&source=${encodeURIComponent(chat.source_id || state.selected.id)}&history=${state.history}`,
      );
      if (ticket !== state.epoch) return;
      offset = page.offset;
      all = [...page.items, ...all];
      const scrollBefore = body.scrollHeight;
      const conversation = el("div", { class: "conversation-body" });
      if (page.notice)
        conversation.append(el("p", { class: "content-notice" }, page.notice));
      if (page.has_more) {
        const more = el(
          "button",
          {
            class: "load-older",
            type: "button",
            onClick: () => {
              more.disabled = true;
              more.textContent = "Wird geladen …";
              load(true);
            },
          },
          "Ältere Nachrichten laden",
        );
        conversation.append(more);
      }
      let previous = "";
      for (const message of all) {
        const date = formatDate(message.date);
        if (date !== previous) {
          conversation.append(el("div", { class: "message-date" }, date));
          previous = date;
        }
        conversation.append(
          el(
            "div",
            {
              class: `bubble ${message.from_me ? "mine" : ""}`,
              title: message.date
                ? new Date(message.date).toLocaleString("de-DE")
                : "",
            },
            message.text,
            provenance(message),
          ),
        );
      }
      if (!all.length)
        conversation.append(
          empty(
            "Keine Nachrichten",
            "Dieses Gespräch enthält keine lesbaren Nachrichten.",
          ),
        );
      body.replaceChildren(conversation);
      body.scrollTop = older
        ? body.scrollHeight - scrollBefore
        : body.scrollHeight;
    } catch (error) {
      if (ticket === state.epoch)
        body.replaceChildren(empty("Gespräch nicht lesbar", error.message));
    }
  }
  await load();
}

function fatal(title, message) {
  state.epoch++;
  state.unlocked = false;
  state.cache = {};
  $("#screen").className = "screen fatal";
  $("#display").replaceChildren(empty(title, message));
  $("#lock-button").disabled = true;
}

$("#home-button").addEventListener("click", home);
$("#lock-button").addEventListener("click", lockBackup);
$("#refresh-backups").addEventListener("click", refreshBackups);
$("#choose-path").addEventListener("click", () => {
  $("#path-form").hidden = !$("#path-form").hidden;
  if (!$("#path-form").hidden) $("#backup-path").focus();
});
$("#backup-select").addEventListener("change", async (event) => {
  if (state.busy) return;
  await selectBackup(event.target.value);
});
$("#path-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (state.busy) return;
  const button = event.currentTarget.querySelector("button");
  button.disabled = true;
  showSidebarError();
  try {
    const backup = await api("inspect", { path: $("#backup-path").value });
    state.backups = state.backups.filter((item) => item.id !== backup.id);
    state.backups.push(backup);
    $("#path-form").hidden = true;
    $("#backup-path").value = "";
    await selectBackup(backup.id);
  } catch (error) {
    showSidebarError(error.message);
  } finally {
    button.disabled = false;
  }
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") home();
});
$("#show-history").addEventListener("change", (event) => {
  if (state.busy) {
    event.target.checked = state.history;
    return;
  }
  state.history = event.target.checked;
  state.cache = {};
  const section =
    state.view in appNames
      ? state.view
      : {
          photo: "photos",
          contact: "contacts",
          note: "notes",
          chat: "conversations",
          recording: "recordings",
        }[state.view];
  if (state.unlocked && section) openApp(section);
  else home();
});

async function boot() {
  home();
  try {
    const token = new URLSearchParams(location.hash.slice(1)).get("token");
    if (token) {
      history.replaceState(null, "", location.pathname);
      await api("session", { token });
    }
    state.backups = await api("backups");
    const status = await api("status");
    state.selected =
      state.backups.find((item) => item.id === status.backup?.id) ||
      state.backups[0] ||
      null;
    state.unlocked = Boolean(status.backup?.unlocked);
    state.unlockedCount = status.unlocked_count || (state.unlocked ? 1 : 0);
    state.generation = status.generation;
    renderSelect();
    home();
  } catch (error) {
    fatal(
      "Sitzung öffnen",
      error.message || "Starte iBackup mit dem Startskript.",
    );
    showSidebarError(error.message);
  }
}

boot();
