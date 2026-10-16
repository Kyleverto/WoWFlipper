"use strict";
const $ = (id) => document.getElementById(id);
let state,
  rows = [],
  shopping = [],
  csrf,
  currentTab = "opportunities";
const inventoryRows = new Map(), inventoryRetries = new Map();
let inventoryTable, stateSequence = 0, stateGeneration = 0;
const shoppingRows = new Map();
const shoppingGroups = new Map();
let shoppingSequence = 0;
let resultsSequence = 0;
let resultsRequest = null;
let resultsPage = 1, exclusionsPage = 1, exclusionsSequence = 0;
let correctionDirty = false;
let resaleSaving = false;
let resaleMutationPending = false, resaleGeneration = 0;
const resaleDrafts = new Map();
const pending = new Map(
  JSON.parse(sessionStorage.getItem("pending-ledger-actions") || "[]"),
);
function savePending() {
  sessionStorage.setItem(
    "pending-ledger-actions",
    JSON.stringify([...pending]),
  );
  syncBackupControls();
}
let knownDataEpoch = sessionStorage.getItem("personal-data-epoch") || null;
let epochReloading = false, activeWrites = 0, backupDocument = null, backupPreviewBusy = false, backupRestoring = false, backupPreviewGeneration = 0;
function invalidatePersonalData() {
  epochReloading = true;
  pending.clear(); savePending();
  stateGeneration++; stateSequence++; shoppingSequence++; resultsSequence++; exclusionsSequence++; resaleGeneration++;
  resultsRequest?.abort();
}
function acceptDataEpoch(next) {
  if (!next.data_epoch) return true;
  const previous = knownDataEpoch;
  knownDataEpoch = next.data_epoch;
  sessionStorage.setItem("personal-data-epoch", knownDataEpoch);
  if (previous && previous !== knownDataEpoch) {
    invalidatePersonalData();
    globalThis.location?.reload();
    return false;
  }
  return true;
}
function backupBlocked() {
  return epochReloading || backupRestoring || backupPreviewBusy || activeWrites > 0 || !!state?.refresh?.running || pending.size > 0;
}
function syncBackupControls() {
  const restore = $("backup-restore");
  if (!restore) return;
  restore.disabled = !backupDocument || backupBlocked();
  $("backup-preview").disabled = backupPreviewBusy || backupRestoring;
  $("backup-file").disabled = backupPreviewBusy || backupRestoring;
  const notice = $("backup-notice");
  if (notice) notice.textContent = state?.refresh?.running ? "Wait for the refresh to finish before restoring." : pending.size ? "Resolve unconfirmed actions before restoring. Retry their original values." : activeWrites && !backupRestoring ? "Wait for current changes to finish before restoring." : "";
}
function showRestoreNotice() {
  const notice = sessionStorage.getItem("restore-completion-notice");
  if (!notice || epochReloading) return;
  sessionStorage.setItem("restore-completion-notice", "");
  message(notice);
}
function initializeBackupControls() {
  const fileInput = $("backup-file"), preview = $("backup-preview"), restore = $("backup-restore"), summary = $("backup-summary");
  fileInput.onchange = () => {backupPreviewGeneration++; backupDocument = null; summary.textContent = "Preview the chosen backup before restoring."; syncBackupControls();};
  preview.onclick = async () => {
    if (backupPreviewBusy || backupRestoring) return;
    const generation = ++backupPreviewGeneration;
    backupDocument = null; backupPreviewBusy = true; syncBackupControls();
    try {
      const file = fileInput.files?.[0];
      if (!file) throw Error("Choose a JSON backup file.");
      if (file.size > 16 * 1024 * 1024) throw Error("Choose a backup no larger than 16 MiB.");
      let document;
      try {document = JSON.parse(await file.text());} catch (_) {throw Error("This file is not valid JSON. Choose an exported backup.");}
      const result = await api("/api/backup/preview", {backup:document});
      if (generation !== backupPreviewGeneration || epochReloading) return;
      const counts = result.counts || {};
      summary.textContent = `Backup from ${result.created_at || "unknown date"} (${result.mode || "unknown mode"}): ${counts.events || 0} events, ${counts.selections || 0} selections, ${counts.inventory || 0} inventory, ${counts.requests || 0} saved action IDs, ${counts.overrides || 0} overrides${result.cash !== undefined ? `. Cash: ${gold(result.cash)}` : ""}. Ready to restore.`;
      backupDocument = document;
    } catch (error) {summary.textContent = error.message;}
    finally {backupPreviewBusy = false; syncBackupControls();}
  };
  restore.onclick = async () => {
    if (!backupDocument || backupBlocked()) return;
    backupRestoring = true; syncBackupControls();
    try {
      const result = await api("/api/backup/restore", {backup:backupDocument});
      if (!result.restored) throw Error("Restore was not confirmed. Reload and inspect your data before trying again.");
      const notice = `Personal backup restored. Recovery copy saved in backups/${result.recovery_backup}. Refresh sources if restored selections use stale listings.`;
      sessionStorage.setItem("restore-completion-notice", notice);
      summary.textContent = notice + " Reloading…";
      invalidatePersonalData();
      knownDataEpoch = null; sessionStorage.setItem("personal-data-epoch", "");
      globalThis.location?.reload();
    } catch (error) {summary.textContent = error.message;}
    finally {backupRestoring = false; syncBackupControls();}
  };
  syncBackupControls();
}
let monitorAcceptedQuery = null, monitorViewCursor = null, monitorCurrentCursor = null, newOnlyAfter = null;
let monitorRevision = 0, monitorConfigRevision = 0;
let monitorPolling = false, monitorConfiguring = false, monitorPreferences = {enabled:false,sound:false,desktop:false};
let monitorAudio = null, monitorMemoryNotified = 0, monitorUpdate = null;
function monitorAlertStatus(text) {$("monitor-alert-status").textContent = text;}
function monitorStoredCursor() {
  try {return Math.max(monitorMemoryNotified, Number(globalThis.localStorage?.getItem("notified-opportunities:" + knownDataEpoch)) || 0);}
  catch (_) {return monitorMemoryNotified;}
}
function monitorSaveCursor(cursor, shared = false) {
  monitorMemoryNotified = Math.max(monitorMemoryNotified, cursor);
  try {if (shared) globalThis.localStorage?.setItem("notified-opportunities:" + knownDataEpoch, String(monitorMemoryNotified));} catch (_) { /* This tab still remembers claimed alerts. */ }
}
async function monitorUnlockSound() {
  const Audio = globalThis.AudioContext || globalThis.webkitAudioContext;
  if (!Audio) throw Error("Sound is unavailable in this browser.");
  if (!monitorAudio) monitorAudio = new Audio();
  if (monitorAudio.state === "suspended") await monitorAudio.resume();
}
function monitorPlaySound() {
  if (!monitorAudio || monitorAudio.state === "suspended") {
    monitorAlertStatus("Use Test alert to enable sound in this browser tab."); return;
  }
  const tone = monitorAudio.createOscillator(), volume = monitorAudio.createGain(), now = monitorAudio.currentTime;
  tone.frequency.setValueAtTime(660, now); volume.gain.setValueAtTime(0.06, now);
  volume.gain.exponentialRampToValueAtTime(0.001, now + 0.25);
  tone.connect(volume); volume.connect(monitorAudio.destination); tone.start(now); tone.stop(now + 0.25);
}
async function monitorDesktopPermission() {
  if (!globalThis.Notification) throw Error("Desktop notifications are unavailable in this browser.");
  let permission = Notification.permission;
  if (permission === "default") permission = await Notification.requestPermission();
  if (permission !== "granted") throw Error("Desktop notification permission was denied. Enable it in browser settings to use desktop alerts.");
}
function monitorNotify(update) {
  const cursor = Number(update.cursor) || 0;
  // Re-read shared storage immediately before claiming: another tab may have alerted.
  const previous = monitorStoredCursor();
  if (cursor <= previous) return;
  const canSound = monitorPreferences.sound && monitorAudio && monitorAudio.state !== "suspended" && monitorAudio.state !== "closed";
  const canDesktop = monitorPreferences.desktop && globalThis.Notification?.permission === "granted";
  // Every tab advances its own quiet cursor. Only a tab able to alert claims
  // the shared cursor, so a silent or nonmatching view cannot suppress another.
  monitorSaveCursor(cursor, !update.baseline && !!update.notify_count && !!(canSound || canDesktop));
  if (update.baseline || !update.notify_count) return;
  try {if (monitorPreferences.sound) monitorPlaySound();} catch (_) {monitorAlertStatus("Sound could not play. Use Test alert to enable it again.");}
  if (monitorPreferences.desktop) {
    if (globalThis.Notification?.permission === "granted") {
      try {new Notification(`${update.notify_count} new WoW Flipper opportunit${update.notify_count === 1 ? "y" : "ies"}`, {
        body:(update.preview || []).slice(0,3).map(item => item.name).join(", "),
        tag:"wowflipper-" + knownDataEpoch + "-" + cursor,
      });} catch (_) {monitorAlertStatus("Desktop alerts could not be shown. Check browser notification settings.");}
    } else monitorAlertStatus("Desktop alerts need permission. Enable Desktop alerts or use Test alert.");
  }
}
function renderMonitorControls(status) {
  monitorPreferences = {enabled:!!status.enabled,sound:!!status.sound,desktop:!!status.desktop};
  if (!monitorConfiguring) for (const key of ["enabled","sound","desktop"]) $("monitor-" + key).checked = monitorPreferences[key];
  $("monitor-enabled").disabled = monitorConfiguring || state?.mode === "offline-demo";
  $("monitor-sound").disabled = monitorConfiguring;
  $("monitor-desktop").disabled = monitorConfiguring;
  $("monitor-status").textContent = status.enabled
    ? `Checks every 2 minutes while this app server is running${status.next_check_at ? "; next check " + new Date(status.next_check_at).toLocaleTimeString() : ""}.`
    : "Automatic checks are off. The app server must stay running for checks.";
}
function renderUpdateBanner(update) {
  monitorUpdate = update;
  $("updates-banner").hidden = !update.updated && !update.new_count && newOnlyAfter === null;
  $("updates-count").textContent = update.new_count ? `${update.new_count} new matching opportunit${update.new_count === 1 ? "y" : "ies"}. Your current list is unchanged.` : !update.updated && newOnlyAfter !== null ? "Showing new opportunities from the last update." : "Market data updated. Your current list is unchanged.";
  $("show-new").hidden = !update.new_count;
  $("update-list").hidden = !!update.new_count;
  $("all-opportunities").hidden = newOnlyAfter === null;
}
async function pollMonitor() {
  if (!state || monitorAcceptedQuery === null || monitorPolling || epochReloading || backupRestoring || activeWrites > 0) return;
  monitorPolling = true;
  const accepted = monitorAcceptedQuery, revision = monitorRevision, configRevision = monitorConfigRevision;
  try {
    const status = await api("/api/monitor");
    if (!acceptDataEpoch(status)) return;
    if (configRevision === monitorConfigRevision && !monitorConfiguring) renderMonitorControls(status);
    if (status.refresh) {
      state.refresh = status.refresh;
      renderSources(); syncBackupControls();
      $("refresh").disabled = state.mode === "offline-demo" || state.refresh.running;
      $("refresh").textContent = state.refresh.running ? "Refreshing sources…" : "Refresh sources";
    }
    const query = new URLSearchParams(accepted), feed = new URLSearchParams({q:query.get("q") || "",categories:query.get("categories") || "",notify_after:monitorStoredCursor()});
    if (monitorViewCursor !== null) feed.set("after", monitorViewCursor);
    const update = await api("/api/updates?" + feed.toString());
    if (!acceptDataEpoch(update) || accepted !== monitorAcceptedQuery || revision !== monitorRevision || update.deferred) return;
    monitorCurrentCursor = update.cursor;
    if (update.baseline || monitorViewCursor === null) {
      monitorViewCursor = update.cursor; monitorSaveCursor(update.cursor);
      renderUpdateBanner({...update,updated:false,new_count:0});
    } else {monitorNotify(update); renderUpdateBanner(update);}
  } catch (error) {$("monitor-status").textContent = "Automatic check status unavailable. " + error.message;}
  finally {monitorPolling = false;}
}
async function reviewMarketUpdates(onlyNew, all = false) {
  if (monitorAcceptedQuery === null) return;
  const previous = newOnlyAfter, cutoff = monitorViewCursor, cursor = monitorCurrentCursor;
  const query = new URLSearchParams(monitorAcceptedQuery);
  query.set("page", "1");
  newOnlyAfter = onlyNew ? cutoff : all ? null : previous;
  if (newOnlyAfter !== null) query.set("new_after", newOnlyAfter); else query.delete("new_after");
  for (const id of ["show-new","update-list","all-opportunities"]) $(id).disabled = true;
  try {
    await loadResults(true,query.toString());
    if (monitorAcceptedQuery !== query.toString()) return;
    ++monitorRevision;
    if (cursor !== null) monitorViewCursor = cursor;
    renderUpdateBanner({updated:false,new_count:0});
    // Keep a way back to the full list even after the updates banner is dismissed.
    $("updates-banner").hidden = newOnlyAfter === null;
    if (newOnlyAfter !== null) $("updates-count").textContent = "Showing new opportunities from the last update.";
  } catch (error) {newOnlyAfter = previous; message(error.message,true);}
  finally {for (const id of ["show-new","update-list","all-opportunities"]) $(id).disabled = false;}
}
function startMonitorPolling() {
  if (monitorAcceptedQuery === null) return;
  setInterval(() => pollMonitor(),5000);
  document.addEventListener("visibilitychange", () => {if (!document.hidden) pollMonitor();});
}
function initializeMonitorControls() {
  for (const key of ["enabled","sound","desktop"]) $("monitor-" + key).onchange = async () => {
    if (monitorConfiguring) return;
    monitorConfiguring = true; ++monitorConfigRevision;
    let value = $("monitor-" + key).checked;
    try {
      if (key === "sound" && value) await monitorUnlockSound();
      if (key === "desktop" && value) await monitorDesktopPermission();
    } catch (error) {value = false; monitorAlertStatus(error.message);}
    try {const status = await api("/api/monitor", {[key]:value}); monitorConfiguring = false; renderMonitorControls(status);}
    catch (error) {monitorConfiguring = false; renderMonitorControls(monitorPreferences); monitorAlertStatus(error.message);}
  };
  $("test-alert").onclick = async () => {
    try {
      await monitorUnlockSound(); monitorPlaySound();
      if ($("monitor-desktop").checked) {await monitorDesktopPermission(); new Notification("WoW Flipper test alert",{body:"New matching opportunities will appear here."});}
      monitorAlertStatus("Test alert sent. Sound is ready in this tab.");
    } catch (error) {monitorAlertStatus(error.message);}
  };
  $("show-new").onclick = () => reviewMarketUpdates(true);
  $("update-list").onclick = () => reviewMarketUpdates(false);
  $("all-opportunities").onclick = () => reviewMarketUpdates(false,true);
  $("updates-banner").hidden = true;
  renderMonitorControls(monitorPreferences);
}

const moneyFields = {
  capital: "Initial capital",
  reserve: "Cash reserve",
  item_exposure: "Maximum item exposure",
  min_profit: "Minimum estimated profit",
  extra_costs: "Extra cost per listing",
};
const percentFields = {
  discount: "Minimum discount (%)",
  auction_cut: "Auction cut (%)",
  resale_factor: "Assumed resale factor (%)",
  min_roi: "Minimum return (%)",
  min_sale_rate: "Minimum regional sale rate (%)",
};
const numericFields = {
  min_sold_per_day: "Minimum regional sold per day",
  max_copies: "Maximum copies per item",
  max_tsm_age_hours: "Maximum valuation age (hours)",
  max_auction_age_hours: "Maximum auction age (hours)",
};
function el(tag, text, cls) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (cls) node.className = cls;
  return node;
}
function gold(copper) {
  return (
    (Number(copper) / 10000).toLocaleString("en-US", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 4,
    }) + "g"
  );
}
function inputGold(copper) {
  return (Number(copper) / 10000).toFixed(4).replace(/\.?0+$/, "") || "0";
}
function copper(value, signed = false) {
  const clean = String(value).trim();
  if (!(signed ? /^-?\d+(\.\d{1,4})?$/ : /^\d+(\.\d{1,4})?$/).test(clean))
    throw Error("Enter gold with up to four decimal places");
  const sign = clean.startsWith("-") ? -1 : 1;
  const [whole, fraction = ""] = clean.replace("-", "").split(".");
  const result =
    sign * (Number(whole) * 10000 + Number(fraction.padEnd(4, "0")));
  if (!Number.isSafeInteger(result))
    throw Error("Money exceeds supported bounds");
  return result;
}
function fractionPercent(value) {
  const n = Number(value);
  if (!Number.isFinite(n) || n < 0)
    throw Error("Enter a nonnegative percentage");
  return String(n / 100);
}
function message(text, error = false) {
  $("message").textContent = text;
  $("message").className = error ? "error" : "";
}
async function api(path, data, options = {}) {
  const writing = data !== undefined && path !== "/api/backup/preview";
  if (epochReloading || (backupRestoring && writing && path !== "/api/backup/restore")) throw Error("Personal data changed. Reload before making more changes.");
  const epoch = knownDataEpoch;
  if (writing) {activeWrites++; syncBackupControls();}
  try {
    const result = await apiRequest(path, data, options);
    if (epochReloading || (epoch && knownDataEpoch !== epoch)) throw Error("Personal data changed. Reload before making more changes.");
    return result;
  } finally {if (writing) {activeWrites--; syncBackupControls();}}
}
async function apiRequest(path, data, options = {}) {
  const opts =
    data === undefined
      ? {}
      : {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
          body: JSON.stringify(data),
        };
  let response;
  try {
    response = await fetch(path, {...opts, ...options});
  } catch (error) {
    if (error.name === "AbortError") throw error;
    throw Error(
      "Connection interrupted. Retry this action; its request ID is preserved until success.",
    );
  }
  let result;
  try {
    result = await response.json();
  } catch (error) {
    throw Error(
      "Response was interrupted. Retry this action; its request ID is preserved until success.",
    );
  }
  if (!response.ok) {
    const error = Error(result.error || "Local request failed");
    error.confirmed = response.status < 500;
    throw error;
  }
  return result;
}
async function mutation(path, payload, key) {
  const fingerprint = JSON.stringify([path, payload]);
  let entry = pending.get(key);
  if (entry && entry.fingerprint !== fingerprint)
    throw Error(
      "An earlier action is unconfirmed. Retry it with its original values before changing this action.",
    );
  if (!entry) {
    entry = { fingerprint, id: crypto.randomUUID() };
    pending.set(key, entry);
    savePending();
  }
  try {
    const result = await api(path, { ...payload, request_id: entry.id });
    if (key === "correction") correctionDirty = false;
    pending.delete(key);
    savePending();
    return result;
  } catch (error) {
    if (error.confirmed) {
      pending.delete(key);
      savePending();
    }
    throw error;
  }
}
async function action(button, work) {
  button.disabled = true;
  try {
    await work();
  } catch (error) {
    message(error.message, true);
  } finally {
    button.disabled = button.id === "refresh" && (state?.refresh.running || state?.mode === "offline-demo");
  }
}
const opportunityCategoryLabels = {
  recipe: "Recipes", mount: "Mounts", toy: "Toys", decor: "Decor", "battle-pet": "Battle pets",
};
let visibleCategories = Object.keys(opportunityCategoryLabels);
function initializeOpportunityCategories() {
  const known = Object.keys(opportunityCategoryLabels);
  visibleCategories = known;
  try {
    const saved = JSON.parse(sessionStorage.getItem("opportunity-categories"));
    if (Array.isArray(saved) && saved.every(c => known.includes(c)))
      visibleCategories = known.filter(c => saved.includes(c));
  } catch (_) { /* Invalid or unavailable storage uses the default selection. */ }
  const container = $("opportunity-categories");
  container.replaceChildren();
  const checks = [];
  for (const category of known) {
    const label = el("label"), input = el("input");
    input.type = "checkbox";
    input.id = "view-category-" + category;
    input.value = category;
    input.checked = visibleCategories.includes(category);
    checks.push(input);
    input.onchange = () => {
      visibleCategories = checks.filter(c => c.checked).map(c => c.value);
      try {
        sessionStorage.setItem("opportunity-categories", JSON.stringify(visibleCategories));
      } catch (_) { /* Filtering still works when browser storage is unavailable. */ }
      resultsPage = 1;
      loadResults().catch(e => message(e.message, true));
    };
    label.append(input, document.createTextNode(opportunityCategoryLabels[category]));
    container.append(label);
  }
}
function params() {
  const query = new URLSearchParams({
    q: $("search").value,
    categories: visibleCategories.join(","),
    sort: $("sort").value,
    direction: $("direction").value,
    page: resultsPage,
    page_size: 100,
  });
  if (newOnlyAfter !== null) query.set("new_after",newOnlyAfter);
  return query;
}
function table(headers) {
  const wrap = el("div", undefined, "table-wrap"),
    table = el("table"),
    head = el("thead"),
    tr = el("tr");
  headers.forEach((h) => tr.append(el("th", h)));
  head.append(tr);
  const body = el("tbody");
  table.append(head, body);
  wrap.append(table);
  return { wrap, body };
}
function addCell(tr, text, cls) {
  const td = el("td", text, cls);
  tr.append(td);
  return td;
}
function age(time) {
  if (!time) return "Missing";
  const hours = (Date.now() - Date.parse(time)) / 3600000;
  return hours < 1
    ? Math.max(0, Math.round(hours * 60)) + "m"
    : hours.toFixed(1) + "h";
}
function destinationControl(value, name) {
  const label = el("label", "Destination"), select = el("select");
  select.setAttribute("aria-label", "Destination for " + name);
  const realms = state.settings.selling_realms;
  if (!realms.includes(value)) {
    const placeholder = new Option(value ? value + " (no longer configured)" : "Choose selling realms in Settings", value || "");
    placeholder.disabled = true;
    select.append(placeholder);
  }
  for (const realm of realms) select.append(new Option(realm, realm));
  select.value = value || "";
  select.disabled = !realms.length;
  label.append(select);
  return {label, select};
}
function configuredDestination(value) {
  return state.settings.selling_realms.includes(value);
}
async function loadState(initial = false) {
  const sequence = ++stateSequence, generation = stateGeneration;
  const next = await api("/api/state");
  if (sequence !== stateSequence || generation !== stateGeneration) return;
  if (!acceptDataEpoch(next)) return;
  state = next;
  syncBackupControls();
  for (const controller of inventoryRows.values()) controller.refreshRequired = false;
  csrf = state.csrf;
  $("mode").textContent =
    state.mode === "offline-demo"
      ? "Offline demo • synthetic market"
      : "Local research";
  $("refresh").disabled = state.mode === "offline-demo" || state.refresh.running;
  $("refresh").textContent = state.refresh.running ? "Refreshing sources…" : "Refresh sources";
  renderCapital();
  renderSources();
  renderInventory();
  renderLedger();
  if (initial) renderSettings();
  else if (currentTab === "settings") { renderRealmSelection(true); renderResaleOverrides(); }
  populateDestinations();
  for (const controller of shoppingRows.values()) controller.updateSource();
  renderShoppingGroups();
}
function renderCapital() {
  const container = $("capital");
  container.replaceChildren();
  for (const [label, key] of [
    ["Cash", "cash"],
    ["Reserve", "reserve"],
    ["Committed", "committed"],
    ["Available", "available_cash"],
    ["Inventory basis", "inventory_cost"],
    ["Realized profit", "realized_profit"],
  ]) {
    const div = el("div");
    div.append(
      el("small", label),
      el(
        "strong",
        gold(state.summary[key]),
        key === "realized_profit" ? "positive" : "",
      ),
    );
    container.append(div);
  }
  if (state.summary.limits_error) message(state.summary.limits_error, true);
}
function renderSources() {
  const target = $("sources");
  target.replaceChildren();
  if (!state.sources.length)
    target.append(
      el(
        "p",
        "No market evidence yet. Choose realms and refresh, or import your offline files in Settings.",
      ),
    );
  const evidence = el("details");
  if (state.sources.length) {
    evidence.append(el("summary", `${state.sources.length} saved source snapshots`));
    target.append(evidence);
  }
  for (const s of state.sources)
    evidence.append(
      el(
        "p",
        `${s.source}: ${s.mode.startsWith("offline") ? "Offline evidence" : "Source snapshot"} • age ${age(s.source_time)} • source ${s.source_time || "unknown"} • fetched ${s.fetched_at} • snapshot ${s.snapshot_id}`,
      ),
    );
  const messages = {
    auth_missing: "Blizzard credentials are not configured. Set the documented environment variables and restart the server.",
    auth_denied: "Authentication was denied. Check the configured credentials and API access.",
    unavailable: "This upstream resource is unavailable. Refresh realm discovery and retry available realms.",
    rate_limited: "The upstream service rate limit was reached. Wait and refresh again.",
    upstream_error: "The upstream service is temporarily unavailable. Retry later.",
    network_error: "The upstream service could not be reached. Check the connection and retry.",
    invalid_schema: "The source returned invalid data. The previous snapshot was retained; retry later.",
    unsupported_realm: "This is not an active United States realm in the current connected realm index; it was skipped.",
    realm_unavailable: "This selected realm is unavailable in current discovery; it was skipped.",
    verification_pending: "Current realm membership could not be verified. Refresh discovery and retry.",
    source_error: "The source could not be refreshed. Retry later.",
  };
  const sourceLabel = (source) => {
    if (source === "realms:us") return "United States realm discovery";
    if (source === "tsm:us") return "Regional valuations";
    if (source === "toys:us") return "Toy classification";
    if (source === "decor:us") return "Housing decor classification";
    if (source === "tsm-pets:us") return "Battle pet regional valuations (species aggregate)";
    if (source.startsWith("auctions:us:")) {
      const house = Number(source.split(":")[2]);
      const names = (state.realms || []).filter(realm => realm.house_id === house)
        .map(realm => realm.name || realm.slug);
      return names.length ? `${names.slice(0, 3).join(" / ")}${names.length > 3 ? " / …" : ""} (house ${house})` : `Auction house ${house}`;
    }
    if (source.startsWith("realm:us:")) return `Realm ${source.split(":")[2]}`;
    return "Source";
  };
  const refresh = state.refresh, entries = Object.entries(refresh.sources || {});
  const houses = entries.filter(([key]) => key.startsWith("auctions:us:"));
  const finalized = houses.filter(([,value]) => ["success", "unchanged", "failed", "skipped"].includes(value.state)).length;
  const seconds = Math.max(0, Math.floor(Number(refresh.elapsed_seconds) || 0));
  const elapsed = seconds >= 60 ? `${Math.floor(seconds / 60)}m ${seconds % 60}s` : `${seconds}s`;
  const phases = {valuations:"Regional valuations", realms:"Realm discovery", classification:"Classification", collections:"Toy and decor classification", auctions:"Auction houses", auction_fetch:"Downloading auction houses", metadata:"Item and pet details", publication:"Saving auction snapshots", complete:"Refresh complete", failed:"Refresh stopped"};
  const summary = $("refresh-summary");
  summary.setAttribute?.("aria-live", "polite");
  summary.setAttribute?.("role", "status");
  summary.textContent = refresh.phase || refresh.running
    ? `${phases[refresh.phase] || "Refreshing sources"} • ${elapsed} elapsed${houses.length ? ` • ${finalized} of ${houses.length} houses finalized` : ""}${refresh.running && refresh.total ? ` • ${refresh.completed || 0} of ${refresh.total} phase steps` : ""}`
    : "Source evidence and refresh progress";
  const completed = el("details");
  const completeCount = houses.filter(([,value]) => ["success","unchanged"].includes(value.state) && !value.metadata_error_code && !value.metadata_deferred && !value.metadata_failed).length;
  if (completeCount) {
    completed.append(el("summary", `${completeCount} completed auction house${completeCount === 1 ? "" : "s"}`));
    target.append(completed);
  }
  for (const [source, status] of entries) {
    const failed = status.state === "failed" || status.state === "skipped";
    const detail = failed ? messages[status.error_code] || messages.source_error : "";
    const httpStatus = Number.isInteger(status.http_status) && status.http_status >= 100 && status.http_status <= 599 ? ` (HTTP ${status.http_status})` : "";
    const counts = [];
    if (status.classification === "item_metadata") counts.push("Classified from item details");
    if (status.known_items !== undefined) counts.push(`${status.known_items} known items`);
    if (status.details_attempted !== undefined) counts.push(`${status.details_attempted} detail lookups attempted`);
    if (status.details_fetched !== undefined) counts.push(`${status.details_fetched} details fetched`);
    if (status.pending) counts.push(`${status.pending} classification lookups pending; ${refresh.running ? "continuing this refresh" : "refresh again to continue"}`);
    if (status.unavailable) counts.push(`${status.unavailable} unavailable upstream (permanent missing resources; excluded from pending lookups)`);
    if (status.failed && !failed) counts.push(`${status.failed} lookups need another attempt; ${messages[status.error_code] || "Refresh again to retry temporary lookup failures."}`);
    if (status.phase) counts.push(phases[status.phase] || "Working");
    const progress = status.progress || status;
    if (progress.total) counts.push(`${progress.completed || 0} of ${progress.total} lookups complete${progress.pending ? `, ${progress.pending} pending` : ""}`);
    if (status.metadata_fetched) counts.push(`${status.metadata_fetched} item details fetched`);
    if (status.metadata_skipped) counts.push(`${status.metadata_skipped} item details still pending`);
    if (status.metadata_failed) counts.push(`${status.metadata_failed} item detail lookups need retry`);
    if (status.metadata_deferred) counts.push(`${status.metadata_deferred} item detail lookups deferred`);
    if (status.metadata_error_code) counts.push(`Item classification incomplete: ${messages[status.metadata_error_code] || messages.source_error}`);
    if (status.using_cached_items) counts.push("Retained known classifications; new membership remains unverified.");
    const paragraph = el("p", `${sourceLabel(source)}: ${status.state}${detail ? ` — ${detail}${httpStatus}` : ""}${counts.length ? ` • ${counts.join(" • ")}` : ""}`, failed || status.failed || status.metadata_error_code || status.metadata_deferred || status.metadata_failed ? "source-failure" : "source-progress");
    (source.startsWith("auctions:us:") && ["success","unchanged"].includes(status.state) && !status.metadata_error_code && !status.metadata_deferred && !status.metadata_failed ? completed : target).append(paragraph);
  }
}
function populateDestinations() {
  const old = $("destination").value;
  $("destination").replaceChildren();
  for (const realm of state.settings.selling_realms)
    $("destination").append(new Option(realm, realm));
  if (state.settings.selling_realms.includes(old)) $("destination").value = old;
  if (!state.settings.selling_realms.length)
    $("destination").append(
      new Option("Choose selling realms in Settings", ""),
    );
}
function invalidateResults(text = "Loading opportunities…") {
  rows = [];
  $("results").replaceChildren();
  $("results").setAttribute("aria-busy", "true");
  $("results-status").textContent = text;
  $("export").removeAttribute("href");
  $("export").setAttribute("aria-disabled", "true");
  $("result-previous").disabled = true;
  $("result-next").disabled = true;
  $("result-page-label").textContent = "";
  ++exclusionsSequence;
  $("exclusions").replaceChildren();
  $("exclusion-pagination").hidden = true;
}
function retireResults() {
  resultsRequest?.controller.abort();
  resultsRequest = null;
  ++resultsSequence;
  invalidateResults();
}
function loadResults(force = false, accepted = null) {
  if (resaleMutationPending) return Promise.resolve();
  const query = accepted === null ? params().toString() : accepted;
  if (!force && resultsRequest?.query === query) return resultsRequest.promise;
  resultsRequest?.controller.abort();
  const sequence = ++resultsSequence;
  const controller = new AbortController();
  invalidateResults();
  const request = {query, controller};
  resultsRequest = request;
  request.promise = performResults(query, sequence, controller.signal).finally(() => {
    if (resultsRequest === request) resultsRequest = null;
  });
  return request.promise;
}
async function performResults(query, sequence, signal) {
  let result;
  try {
    result = await api("/api/results?" + query, undefined, {signal});
  } catch (error) {
    // An obsolete request must not overwrite a newer result or its error state.
    if (sequence !== resultsSequence) return;
    $("results").setAttribute("aria-busy", "false");
    $("results-status").textContent = "Loading failed. " + error.message;
    throw error;
  }
  if (sequence !== resultsSequence) return;
  $("results").setAttribute("aria-busy", "false");
  $("results-status").textContent = `Loaded ${result.rows.length} opportunities (${result.total} total).`;
  $("export").setAttribute("aria-disabled", "false");
  rows = result.rows;
  resultsPage = result.page;
  const acceptedQuery = new URLSearchParams(query);
  const previousView = monitorAcceptedQuery === null ? null : new URLSearchParams(monitorAcceptedQuery);
  if (previousView && (previousView.get("q") !== acceptedQuery.get("q") || previousView.get("categories") !== acceptedQuery.get("categories"))) {monitorViewCursor = null; ++monitorRevision;}
  monitorAcceptedQuery = query;
  acceptedQuery.set("page", result.page);
  acceptedQuery.set("page_size", result.page_size);
  $("export").href = "/api/export?" + acceptedQuery.toString();
  renderResults();
  pageControls("result", result);
  $("exclusion-count").textContent =
    `Inspection exclusions (${result.exclusion_total})`;
  ++exclusionsSequence;
  exclusionsPage = 1;
  $("exclusions").replaceChildren();
  $("exclusion-pagination").hidden = true;
  if ($("exclusion-inspection").open) await loadExclusions();
}
function pageControls(prefix, result) {
  const start = result.total ? (result.page - 1) * result.page_size + 1 : 0;
  const end = Math.min(result.page * result.page_size, result.total);
  $(prefix + "-page-label").textContent =
    `${start}–${end} of ${result.total} • Page ${result.page} of ${result.pages}`;
  $(prefix + "-previous").disabled = result.page <= 1;
  $(prefix + "-next").disabled = result.page >= result.pages;
}
async function loadExclusions() {
  const sequence = ++exclusionsSequence;
  let result;
  try {
    result = await api(`/api/exclusions?page=${exclusionsPage}&page_size=100`);
  } catch (error) {
    if (sequence !== exclusionsSequence) return;
    throw error;
  }
  if (sequence !== exclusionsSequence || !$("exclusion-inspection").open) return;
  exclusionsPage = result.page;
  $("exclusions").replaceChildren();
  for (const row of result.rows) {
    $("exclusions").append(el("p",
      `${row.name} (${row.item_id}): ${row.reason.replaceAll("_", " ")}`));
  }
  $("exclusion-pagination").hidden = false;
  pageControls("exclusion", result);
}
function renderResults() {
  const container = $("results");
  container.replaceChildren();
  if (!rows.length) {
    container.append(
      el(
        "div",
        (!visibleCategories.length ? "Select at least one category to show opportunities." : "No eligible opportunities. Choose buying and selling realms in Settings, refresh sources or import offline evidence. Stale sources, uncertain variants and filters exclude rows."),
        "empty",
      ),
    );
    return;
  }
  const { wrap, body } = table([
    "Item / source",
    "Quantity",
    "Total buyout",
    "Unit buyout",
    "Assumed unit resale",
    "Net proceeds estimate",
    "Profit estimate",
    "Return",
    "Regional sale rate",
    "Regional sold/day",
    "Market / historical",
    "Source ages",
  ]);
  const sortable = {
    0: "name",
    1: "quantity",
    2: "cost",
    6: "profit",
    7: "roi",
    8: "sale_rate",
    9: "sold_per_day",
  };
  for (const [index, key] of Object.entries(sortable)) {
    const th = wrap.querySelectorAll("th")[Number(index)],
      button = el("button", th.textContent, "sort-button");
    button.title = "Sort by " + th.textContent;
    button.onclick = () => {
      const same = $("sort").value === key;
      $("sort").value = key;
      if (same)
        $("direction").value = $("direction").value === "desc" ? "asc" : "desc";
      resultsPage = 1;
      loadResults().catch((e) => message(e.message, true));
    };
    th.replaceChildren(button);
  }
  for (const row of rows) {
    const a = row.auction,
      v = row.valuation,
      tr = el("tr");
    let td = addCell(tr, "");
    td.append(
      el("strong", candidateName(row)),
      el(
        "small",
        `${a.item_id} • ${row.source_realms.join(", ")} • ${a.category}`,
      ),
    );
    if (row.mode.startsWith("offline"))
      td.append(el("small", "Offline evidence"));
    addCell(tr, a.quantity);
    addCell(tr, gold(a.buyout));
    addCell(tr, gold(a.buyout / a.quantity));
    const resaleCell = addCell(tr, gold(effectiveResale(row)));
    if (row.resale_override) resaleCell.append(el("small", "Manual resale"));
    addCell(tr, gold(row.proceeds));
    addCell(tr, gold(row.profit), "positive");
    addCell(tr, (Number(row.roi) * 100).toFixed(1) + "%");
    const rateCell = addCell(tr, (Number(v.sale_rate) * 100).toFixed(1) + "%");
    const soldCell = addCell(tr, v.sold_per_day);
    const marketCell = addCell(tr, `${gold(v.market_value)} / ${gold(v.historical)}`);
    if (petVariant(row)) for (const cell of [rateCell, soldCell, marketCell]) cell.append(el("small", "Species aggregate"));
    addCell(
      tr,
      `${age(a.source_time)} auctions / ${age(v.updated_at)} valuation`,
    );
    const controls = el("div", undefined, "row-actions");
    td.append(controls);
    const inspect = el("button", "Compare", "secondary"),
      select = el("button", "Select listing");
    const selectionKey = "select:" + row.key.join(":");
    const unconfirmedSelection = pending.get(selectionKey);
    let destination = unconfirmedSelection ? JSON.parse(unconfirmedSelection.fingerprint)[1].destination : $("destination").value, selecting = false;
    const compareDestination = destinationControl(destination, v.name + " comparison");
    const compareSelect = el("button", "Select listing");
    function updateSelection() {
      compareDestination.select.value = destination;
      compareDestination.select.disabled = selecting || pending.has(selectionKey) || !state.settings.selling_realms.length;
      for (const button of [select, compareSelect]) {
        button.disabled = selecting || (!configuredDestination(destination) && !pending.has(selectionKey));
        button.title = button.disabled ? "Choose a configured selling realm in Settings" : "Reserve this whole listing";
      }
    }
    compareDestination.select.onchange = () => { destination = compareDestination.select.value; updateSelection(); };
    controls.append(inspect, select);
    updateSelection();
    body.append(tr);
    const details = el("tr", undefined, "details");
    details.hidden = true;
    const detail = addCell(details, "");
    detail.colSpan = 12;
    detail.append(
      el("p", candidateName(row)),
      el("p", row.explanation),
      el(
        "p",
        row.risks.map((r) => r.replaceAll("_", " ")).join("; ") ||
          "Manual verification required",
        "caution",
      ),
    );
    const comparisons = el("div", undefined, "comparison");
    for (const c of row.comparisons) {
      const box = el("div");
      box.append(
        el(
          "strong",
          c.realm + (c.mode.startsWith("offline") ? " • Offline evidence" : ""),
        ),
        el("p", `${c.listing_count} competing listings • ${c.quantity} units`),
        el(
          "p",
          `Lowest unit ask: ${c.lowest_unit_ask === null ? "No fresh evidence" : gold(c.lowest_unit_ask)}`,
        ),
        el("p", `Assumed resale: ${gold(c.assumed_unit_resale)}`),
        el("p", c.explanation),
        el(
          "p",
          (c.risks || []).map((r) => r.replaceAll("_", " ")).join("; "),
          "caution",
        ),
      );
      comparisons.append(box);
    }
    const compareControls = el("div", undefined, "row-actions");
    compareControls.append(compareDestination.label, compareSelect);
    detail.append(
      resaleVerification(row),
      comparisons,
      compareControls,
      el(
        "p",
        `Auction ${a.auction_id} • Source ${a.source_time} • Valuation ${v.updated_at} • Immutable evidence ${a.provenance}`,
        "evidence",
      ),
    );
    body.append(details);
    inspect.onclick = () => {
      details.hidden = !details.hidden;
      inspect.setAttribute("aria-expanded", String(!details.hidden));
    };
    for (const button of [select, compareSelect]) button.onclick = async () => {
      if (selecting || (!configuredDestination(destination) && !pending.has(selectionKey))) return;
      selecting = true;
      updateSelection();
      try {
        await mutation("/api/shopping", {key: row.key, destination}, selectionKey);
        message("Listing added to shopping list.");
        await loadState();
        await loadShopping();
      } catch (error) {
        message(error.message, true);
      } finally {
        selecting = false;
        updateSelection();
      }
    };
  }
  container.append(wrap);
}
function buyingHouse(item) {
  return item.candidate.auction?.house_id ?? item.key[1];
}
function buyingRealmNames(house) {
  const names = (state.realms || []).filter(realm => String(realm.house_id) === String(house))
    .map(realm => realm.name || realm.slug).filter(Boolean);
  return [...new Set(names)].join(", ") || "Realm mapping unavailable";
}
function reconcileShoppingChildren(parent, ordered) {
  const wanted = new Set(ordered);
  for (const child of [...parent.children]) if (!wanted.has(child)) parent.removeChild(child);
  for (const [index, child] of ordered.entries()) {
    if (parent.children[index] !== child) parent.insertBefore(child, parent.children[index] || null);
  }
}
function renderShoppingGroups() {
  const groups = new Map();
  for (const item of shopping) {
    const controller = shoppingRows.get(JSON.stringify(item.key));
    if (!controller) continue;
    const house = buyingHouse(item), key = String(house);
    if (!groups.has(key)) groups.set(key, {house, items: [], rows: []});
    const group = groups.get(key);
    group.items.push(item);
    group.rows.push(controller.row);
  }
  for (const key of shoppingGroups.keys()) if (!groups.has(key)) shoppingGroups.delete(key);
  const sections = [];
  // House IDs give groups a stable order even when realm names or destinations change.
  for (const [key, group] of [...groups].sort((a, b) => a[0].localeCompare(b[0], "en", {numeric: true}))) {
    let section = shoppingGroups.get(key);
    if (!section) {
      const node = el("section", undefined, "shopping-group"), title = el("h3"), totals = el("p", undefined, "shopping-group-totals"), list = el("div");
      node.append(title, totals, list);
      section = {node, title, totals, list};
      shoppingGroups.set(key, section);
    }
    section.title.textContent = `${buyingRealmNames(group.house)} • Connected auction house ${group.house}`;
    const quantity = group.items.reduce((total, item) => total + Number(item.quantity), 0);
    const cost = group.items.reduce((total, item) => total + Number(item.cost), 0);
    section.totals.textContent = `${group.items.length} ${group.items.length === 1 ? "listing" : "listings"} • ${quantity} ${quantity === 1 ? "unit" : "units"} • ${gold(cost)} quoted`;
    reconcileShoppingChildren(section.list, group.rows);
    sections.push(section.node);
  }
  reconcileShoppingChildren($("shopping-items"), sections.length ? sections : [el("div", "No selected listings. Select a whole listing from Opportunities.", "empty")]);
}
async function loadShopping() {
  const sequence = ++shoppingSequence, generation = resaleGeneration;
  const loaded = await api("/api/shopping");
  if (sequence !== shoppingSequence || generation !== resaleGeneration || resaleMutationPending) return;
  shopping = loaded;
  const keys = new Set(shopping.map(item => JSON.stringify(item.key)));
  for (const key of shoppingRows.keys()) if (!keys.has(key)) shoppingRows.delete(key);
  for (const item of shopping) {
    const rowKey = JSON.stringify(item.key);
    const existing = shoppingRows.get(rowKey);
    if (existing) {
      existing.update(item);
      continue;
    }
    const row = el("div", undefined, "shopping-row"),
      info = el("div");
    const source = el("p");
    function updateSource() {
      const sourceHouse = buyingHouse(item);
      source.textContent = `Buy on: ${buyingRealmNames(sourceHouse)} • Connected auction house ${sourceHouse}`;
    }
    updateSource();
    const eligibility = el("p", item.valid ? "Currently eligible; manually verify in game" : item.validation_error, "caution");
    info.append(
      el("strong", candidateName(item.candidate)),
      el(
        "p",
        `${item.quantity} units • ${gold(item.cost)} quoted`,
      ),
      source,
      eligibility,
    );
    const form = el("form"),
      label = el("label", "Actual total paid (gold)"),
      input = el("input");
    const purchaseKey = "purchase:" + item.key.join(":");
    const unconfirmedPurchase = pending.get(purchaseKey);
    input.value = inputGold(unconfirmedPurchase ? JSON.parse(unconfirmedPurchase.fingerprint)[1].event.cost : item.cost);
    input.inputMode = "decimal";
    label.append(input);
    const buy = el("button", "Record purchase");
    buy.type = "submit";
    const destinationKey = "destination:" + item.key.join(":");
    const unconfirmedDestination = pending.get(destinationKey);
    const draftDestination = unconfirmedDestination ? JSON.parse(unconfirmedDestination.fingerprint)[1].destination : item.destination;
    const destination = destinationControl(draftDestination, candidateName(item.candidate));
    const save = el("button", "Save destination", "secondary"), status = el("p");
    save.type = "button";
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");
    let saving = false, purchasing = false, verified = true, resaleStale = false;
    function updateDestination() {
      const dirty = destination.select.value !== item.destination;
      const unconfirmed = pending.has(destinationKey);
      const purchaseUnconfirmed = pending.has(purchaseKey);
      input.disabled = purchasing || purchaseUnconfirmed;
      buy.disabled = purchasing || saving || resaleStale || dirty || unconfirmed || !verified || !item.valid || !configuredDestination(item.destination);
      save.disabled = purchasing || purchaseUnconfirmed || saving || (!configuredDestination(destination.select.value) && !unconfirmed) || (!dirty && !unconfirmed && verified);
      destination.select.disabled = purchasing || purchaseUnconfirmed || saving || unconfirmed || !state.settings.selling_realms.length;
      status.textContent = resaleStale ? "Resale changed; reload the shopping list to confirm eligibility." : saving ? "Saving destination…" : unconfirmed ? "Destination save not confirmed. Retry Save destination before purchasing." : !verified ? "Destination saved; eligibility not confirmed. Retry Save destination." : dirty ? "Destination changed. Save destination before recording a purchase." : "Saved destination: " + item.destination;
    }
    destination.select.onchange = updateDestination;
    save.onclick = async () => {
      if (save.disabled) return;
      saving = true;
      updateDestination();
      try {
        const updated = await mutation("/api/shopping/destination", {key: item.key, destination: destination.select.value}, destinationKey);
        if (shoppingRows.get(rowKey) !== controller) return;
        item.destination = updated.destination;
        verified = false;
        const generation = resaleGeneration;
        const current = (await api("/api/shopping")).find(entry => JSON.stringify(entry.key) === JSON.stringify(item.key));
        if (generation !== resaleGeneration || resaleMutationPending) return;
        if (!current) throw Error("This selection is no longer available. Reload the shopping list.");
        if (shoppingRows.get(rowKey) !== controller) return;
        Object.assign(item, current);
        resaleStale = false;
        eligibility.textContent = item.valid ? "Currently eligible; manually verify in game" : item.validation_error;
        verified = true;
        ++shoppingSequence;
        message("Destination saved.");
      } catch (error) {
        message(error.message, true);
      } finally {
        saving = false;
        updateDestination();
      }
    };
    updateDestination();
    const destinationActions = el("div", undefined, "row-actions");
    destinationActions.append(destination.label, save);
    form.append(label, buy);
    form.onsubmit = (event) => {
      event.preventDefault();
      if (buy.disabled) return;
      purchasing = true;
      updateDestination();
      return action(buy, async () => {
        await mutation(
          "/api/ledger",
          {
            event: {
              type: "purchase",
              key: item.key,
              cost: copper(input.value),
            },
          },
          purchaseKey,
        );
        ++shoppingSequence;
        shoppingRows.delete(rowKey);
        message("Purchase recorded. Inventory and cash updated.");
        await loadState();
        await loadShopping();
      }).finally(() => {
        purchasing = false;
        updateDestination();
      });
    };
    const remove = el("button", "Remove", "secondary");
    remove.onclick = () =>
      action(remove, async () => {
        await mutation(
          "/api/shopping/remove",
          { key: item.key },
          "remove:" + item.key.join(":"),
        );
        ++shoppingSequence;
        shoppingRows.delete(rowKey);
        message("Selection removed.");
        await loadState();
        await loadShopping();
      });
    const controller = {row, updateSource, invalidateEligibility() {
      resaleStale = true;
      updateDestination();
    }, update(current) {
      const choice = destination.select.value;
      const refreshed = destinationControl(choice, candidateName(item.candidate));
      destination.select.replaceChildren(...refreshed.select.options);
      destination.select.value = choice;
      if (!saving && !purchasing) {
        Object.assign(item, current);
        resaleStale = false;
        eligibility.textContent = item.valid ? "Currently eligible; manually verify in game" : item.validation_error;
      }
      updateSource();
      updateDestination();
    }};
    shoppingRows.set(rowKey, controller);
    row.append(info, destinationActions, status, form, remove);
  }
  renderShoppingGroups();
}
function renderInventory() {
  const lots = state?.summary?.inventory || [];
  if (!inventoryTable) {
    inventoryTable = table(["Lot / item", "Units / cost", "Bought on", "Purchased", "Status / destination", "Outcome"]);
    $("inventory").append(inventoryTable.wrap);
  }
  const currentDestination = $("inventory-destination").value;
  $("inventory-destination").replaceChildren(new Option("All destinations", ""));
  for (const value of [...new Set([...state.settings.selling_realms, ...lots.map(l => l.destination)])])
    $("inventory-destination").append(new Option(value, value));
  $("inventory-destination").value = currentDestination || "";
  const ids = new Set(lots.map(l => l.id));
  for (const [id, controller] of inventoryRows) {
    if (!ids.has(id) && !pending.has(`inventory:${id}`) && !controller.busy) inventoryRows.delete(id);
  }
  const ordered = [];
  for (const lot of lots) {
    if (!inventoryRows.has(lot.id)) inventoryRows.set(lot.id, inventoryController(lot));
    const controller = inventoryRows.get(lot.id);
    controller.update(lot);
    ordered.push(controller.row);
  }
  // Keep an unconfirmed action reachable even if a refreshed view omits its lot.
  for (const [id, controller] of inventoryRows) if (!ids.has(id)) {
    controller.missing = true; controller.sync(); ordered.push(controller.row);
  }
  for (const [key, entry] of pending) {
    if (!key.startsWith("inventory:") || inventoryRows.has(Number(key.slice(10)))) continue;
    if (!inventoryRetries.has(key)) {
      const row = el("tr"), cell = addCell(row); cell.setAttribute("colspan", "6");
      const [path, payload] = JSON.parse(entry.fingerprint), event = payload.event;
      const retry = el("button", "Retry saved action"), controller = {row, busy:false};
      cell.append(el("strong", `Lot #${event.purchase_id} • ${event.type} not confirmed`), el("small", "This lot is absent from current holdings. Retry the saved action to confirm its result."), retry);
      retry.onclick = async () => {
        if (controller.busy) return;
        controller.busy = true; retry.disabled = true; stateGeneration++;
        try {await mutation(path, payload, key); stateGeneration++; message("Saved inventory action confirmed."); await loadState();}
        catch (error) {message(error.message, true);}
        finally {controller.busy = false; retry.disabled = false; renderInventory();}
      };
      inventoryRetries.set(key, controller);
    }
  }
  for (const [key, controller] of inventoryRetries) {
    if ((!pending.has(key) && !controller.busy) || inventoryRows.has(Number(key.slice(10)))) inventoryRetries.delete(key);
    else ordered.push(controller.row);
  }
  reconcileShoppingChildren(inventoryTable.body, ordered);
  const query = $("inventory-search").value.trim().toLowerCase();
  let visible = inventoryRetries.size;
  for (const controller of inventoryRows.values()) {
    const lot = controller.lot;
    controller.row.hidden = !(pending.has(`inventory:${lot.id}`) || controller.busy) && (
      !`${candidateName(lot.candidate)} ${lot.candidate.auction.item_id} #${lot.id}`.toLowerCase().includes(query) ||
      (!!$("inventory-status").value && (lot.status || "held") !== $("inventory-status").value) ||
      (!!$("inventory-destination").value && lot.destination !== $("inventory-destination").value));
    if (!controller.row.hidden) visible++;
  }
  $("inventory-empty").hidden = visible > 0;
  $("inventory-empty").textContent = lots.length ? "No lots match these filters." : "No held inventory. Record a purchase from Shopping list.";
}
function inventoryController(initial) {
  const controller = {lot: initial, busy: false, missing: false, refreshRequired: false}, row = el("tr");
  controller.row = row;
  const identity = addCell(row), basis = addCell(row), source = addCell(row), date = addCell(row), management = addCell(row), outcomes = addCell(row);
  const statusLabel = el("label", "Status"), status = el("select");
  status.setAttribute("aria-label", `Status for lot #${initial.id}`);
  for (const [value, label] of [["held", "Held"], ["ready", "Ready to sell"], ["listed", "Listed"]]) status.append(new Option(label, value));
  status.value = initial.status || "held"; statusLabel.append(status);
  const destination = destinationControl(initial.destination, `lot #${initial.id}`);
  const saveStatus = el("button", "Save status"), saveDestination = el("button", "Save destination"), notice = el("small");
  management.append(statusLabel, saveStatus, destination.label, saveDestination, notice);
  const detail = el("details"), summary = el("summary", "Record outcome"), form = el("form"), typeLabel = el("label", "Outcome"), type = el("select");
  for (const [value,label] of [["sale", "Partial / full sale"], ["expiration", "Expired listing"], ["fee", "Additional fee"], ["cancellation", "Canceled listing"]]) type.append(new Option(label,value));
  type.value = "sale"; typeLabel.append(type);
  const quantityLabel = el("label", "Units sold"), quantity = el("input");
  quantity.type = "number"; quantity.min = "1"; quantity.step = "1"; quantity.value = String(initial.remaining_quantity); quantityLabel.append(quantity);
  const amountLabel = el("label"), amountCaption = el("span", "Net proceeds (gold, after auction cut)"), amount = el("input");
  amount.inputMode = "decimal"; amount.value = ""; amountLabel.append(amountCaption, amount);
  const noteLabel = el("label", "Note"), note = el("input"); note.maxLength = 500; noteLabel.append(note);
  const record = el("button", "Record outcome"); record.type = "submit";
  form.append(typeLabel, quantityLabel, amountLabel, noteLabel, record); detail.append(summary, form); outcomes.append(detail);
  let statusDirty = false, destinationDirty = false, outcomeDirty = false, resetQuantity = false;
  const key = `inventory:${initial.id}`;
  function pendingEvent() { const entry = pending.get(key); return entry ? JSON.parse(entry.fingerprint)[1].event : null; }
  function fields() {
    quantityLabel.hidden = type.value !== "sale";
    amountLabel.hidden = !["sale", "fee"].includes(type.value);
    amountCaption.textContent = type.value === "fee" ? "Additional fee (gold)" : "Net proceeds (gold, after auction cut)";
    amount.required = !amountLabel.hidden;
  }
  controller.sync = () => {
    const event = pendingEvent(), locked = controller.busy || !!event || controller.refreshRequired;
    for (const input of [status, destination.select, type, quantity, amount, note]) input.disabled = locked;
    if (!locked) destination.select.disabled = !state.settings.selling_realms.length;
    saveStatus.disabled = controller.busy || controller.refreshRequired || (event ? event.type !== "inventory-update" || !event.status : controller.missing || !statusDirty);
    saveDestination.disabled = controller.busy || controller.refreshRequired || (event ? event.type !== "inventory-update" || !event.destination : controller.missing || !destinationDirty || !configuredDestination(destination.select.value));
    record.disabled = controller.busy || controller.refreshRequired || (event ? event.type === "inventory-update" : controller.missing);
    notice.textContent = controller.refreshRequired ? "Outcome recorded. Reload to refresh holdings before another action." : event ? "Action not confirmed. Retry with the saved values." : `Saved: ${controller.lot.status || "held"} • ${controller.lot.destination}`;
    fields();
  };
  controller.update = lot => {
    controller.lot = lot; controller.missing = false;
    identity.replaceChildren(el("strong", candidateName(lot.candidate)), el("small", `#${lot.id} • Item ${lot.candidate.auction.item_id}`));
    basis.replaceChildren(el("strong", `${lot.remaining_quantity} units held`), el("small", `Paid ${gold(lot.cost)} • Remaining basis ${gold(lot.remaining_cost)}`));
    source.replaceChildren(el("span", buyingRealmNames(lot.key[1])), el("small", `Connected auction house ${lot.key[1]}`));
    const purchased = lot.recorded_at, days = Math.max(0, Math.floor((Date.now() - Date.parse(purchased)) / 86400000));
    date.replaceChildren(el("span", purchased ? purchased.slice(0,10) : "Unknown date"), el("small", Number.isFinite(days) ? `${days} days held` : "Age unavailable"));
    if (!statusDirty) status.value = lot.status || "held";
    const choice = destinationDirty ? destination.select.value : lot.destination;
    const options = destinationControl(choice, `lot #${lot.id}`);
    destination.select.replaceChildren(...options.select.children); destination.select.value = choice;
    if (!outcomeDirty || resetQuantity) {quantity.value = String(lot.remaining_quantity); resetQuantity = false;}
    quantity.max = String(lot.remaining_quantity);
    const event = pendingEvent();
    if (event) {
      if (event.status) status.value = event.status;
      if (event.destination) destination.select.value = event.destination;
      if (event.type !== "inventory-update") {type.value = event.type; if (event.quantity) quantity.value = String(event.quantity); if (event.net_proceeds !== undefined || event.amount !== undefined) amount.value = inputGold(event.net_proceeds ?? event.amount); note.value = event.note || "";}
    }
    controller.sync();
  };
  status.onchange = () => {statusDirty = status.value !== (controller.lot.status || "held"); controller.sync();};
  destination.select.onchange = () => {destinationDirty = destination.select.value !== controller.lot.destination; controller.sync();};
  type.onchange = () => {outcomeDirty = true; fields();};
  for (const input of [quantity, amount, note]) input.oninput = () => {outcomeDirty = true;};
  async function write(makeEvent) {
    if (controller.busy || controller.refreshRequired || (controller.missing && !pending.has(key))) return;
    let confirmedEvent;
    try {
      const event = pendingEvent() || makeEvent();
      controller.busy = true; stateGeneration++; controller.sync();
      await mutation("/api/ledger", {event}, key);
      confirmedEvent = event;
      stateGeneration++;
      if (inventoryRows.get(initial.id) !== controller) return;
      if (event.type === "inventory-update") {
        controller.lot = {...controller.lot, ...(event.status ? {status:event.status} : {}), ...(event.destination ? {destination:event.destination} : {})};
        state.summary.inventory = state.summary.inventory.map(lot => lot.id === initial.id ? controller.lot : lot);
        if (event.status) statusDirty = false;
        if (event.destination) destinationDirty = false;
      } else {outcomeDirty = false; resetQuantity = true; amount.value = ""; note.value = "";}
      message("Inventory updated."); await loadState();
    } catch (error) {
      if (confirmedEvent && confirmedEvent.type !== "inventory-update") controller.refreshRequired = true;
      message(confirmedEvent ? "Inventory action recorded, but holdings could not be refreshed. Reload to get the current holdings." : error.message, true);
    }
    finally {controller.busy = false; controller.sync(); renderInventory();}
  }
  saveStatus.onclick = () => saveStatus.disabled ? undefined : write(() => ({type:"inventory-update", purchase_id:initial.id, status:status.value}));
  saveDestination.onclick = () => saveDestination.disabled ? undefined : write(() => ({type:"inventory-update", purchase_id:initial.id, destination:destination.select.value}));
  form.onsubmit = event => {
    event.preventDefault(); if (record.disabled) return;
    return write(() => {
      const entry = {type:type.value, purchase_id:initial.id};
      if (type.value === "sale") {
        entry.quantity = Number(quantity.value);
        if (!Number.isSafeInteger(entry.quantity) || entry.quantity < 1 || entry.quantity > controller.lot.remaining_quantity) throw Error("Enter units sold within the remaining quantity");
        entry.net_proceeds = copper(amount.value);
      }
      if (type.value === "fee") entry.amount = copper(amount.value);
      if (note.value) entry.note = note.value;
      return entry;
    });
  };
  return controller;
}
function renderLedger() {
  const inventory = state.summary.inventory;
  const old = $("purchase-id").value;
  $("purchase-id").replaceChildren(new Option("No specific inventory lot", ""));
  for (const lot of state.history.filter((e) => e.type === "purchase")) {
    const held = inventory.find((i) => i.id === lot.id);
    $("purchase-id").append(
      new Option(
        `#${lot.id} ${candidateName(lot.candidate)} (${held ? held.remaining_quantity : 0} units held)`,
        lot.id,
      ),
    );
  }
  $("purchase-id").value = old;
  const target = $("correction-target"),
    oldTarget = target.value;
  target.replaceChildren();
  for (const event of state.history.filter((e) => e.type !== "correction"))
    target.append(new Option(`#${event.id} ${event.type}`, event.id));
  if ([...target.options].some((o) => o.value === oldTarget))
    target.value = oldTarget;
  renderCorrection();
  $("history").replaceChildren();
  for (const event of [...state.history].reverse()) {
    const div = el("div", undefined, "event");
    div.append(
      el("strong", `#${event.id} ${event.type}`),
      el("small", " • " + event.recorded_at),
    );
    const parts = [];
    for (const [key, value] of Object.entries(event)) {
      if (
        [
          "id",
          "type",
          "recorded_at",
          "candidate",
          "provenance",
          "strategy",
          "key",
        ].includes(key)
      )
        continue;
      if (["cost", "quoted_cost", "net_proceeds", "amount"].includes(key))
        parts.push(`${key.replaceAll("_", " ")}: ${gold(value)}`);
      else if (key === "replacement")
        parts.push(
          value === null
            ? "Original event voided"
            : Object.entries(value)
                .filter(([k]) => k !== "strategy")
                .map(
                  ([k, v]) =>
                    `${k.replaceAll("_", " ")}: ${["cost", "net_proceeds", "amount"].includes(k) ? gold(v) : v}`,
                )
                .join(", "),
        );
      else if (typeof value !== "object")
        parts.push(`${key.replaceAll("_", " ")}: ${value}`);
    }
    div.append(el("p", parts.join(" • ")));
    $("history").append(div);
  }
}
function field(container, id, label, value, type = "text") {
  const node = el("label", label),
    input = el("input");
  input.id = id;
  input.type = type;
  if (type === "number") input.step = "any";
  input.value = value;
  node.append(input);
  container.append(node);
  return input;
}
// Archived shopping and inventory candidates predate the additive result fields.
function petVariant(row) {
  if (row.pet) return row.pet;
  const auction = row.auction || {};
  if (auction.metadata?.pet) return auction.metadata.pet;
  try {
    const variant = typeof auction.variant_key === "string" ? JSON.parse(auction.variant_key) : auction.variant_key;
    if (variant?.pet_species_id) return {species_id: variant.pet_species_id, level: variant.pet_level, quality: variant.pet_quality_id, breed_id: variant.pet_breed_id};
  } catch (_) { /* Legacy uncertain variants stay ordinary inspection evidence. */ }
  return null;
}
function candidateIdentity(row) {
  const pet = petVariant(row);
  return pet ? `pet:${pet.species_id}:${pet.level}:${pet.quality}:${pet.breed_id}` : `item:${row.auction.item_id}`;
}
function petLabel(pet) {
  const quality = ["Poor", "Common", "Uncommon", "Rare", "Epic", "Legendary"][pet.quality] || `Quality ${pet.quality}`;
  return `Species ${pet.species_id} • Level ${pet.level} • ${quality} • Breed ${pet.breed_id}`;
}
function candidateName(row) {
  const pet = petVariant(row);
  return row.valuation.name + (pet ? ` • ${petLabel(pet)}` : "");
}
function overrideTarget(row) {
  return petVariant(row) ? candidateIdentity(row) : row.auction.item_id;
}
function overrideDraftKey(target) {
  return String(target).startsWith("pet:") ? String(target) : `item:${target}`;
}
function effectiveResale(row) {
  return row.assumed_unit_resale ?? row.valuation.avg_sale_price * Number(state.settings.resale_factor);
}
async function saveResaleOverride(itemId, value, clear = false) {
  if (resaleSaving) throw Error("A resale override is being saved. Wait before changing another item.");
  const rawId = String(itemId).trim();
  const isPet = /^pet:[1-9]\d*:(?:[1-9]|1\d|2[0-5]):[0-5]:[1-9]\d*$/.test(rawId) && rawId.slice(4).split(":").every(part => Number.isSafeInteger(Number(part)));
  const id = Number(rawId);
  if (!isPet && (!/^\d+$/.test(rawId) || !Number.isSafeInteger(id) || id <= 0))
    throw Error("Enter a positive whole item ID or exact pet identity (pet:species:level:quality:breed)");
  const payload = isPet ? {identity: rawId} : {item_id: id};
  if (!clear) {
    payload.unit_price = copper(value);
    if (payload.unit_price <= 0) throw Error("Unit resale must be greater than zero");
  }
  resaleSaving = true;
  resaleMutationPending = true;
  ++resaleGeneration;
  retireResults();
  ++shoppingSequence;
  for (const controller of shoppingRows.values()) controller.invalidateEligibility();
  try {
    try {
      await api(clear ? "/api/resale-overrides/remove" : "/api/resale-overrides", payload);
    } catch (error) {
      if (error.message.includes("request ID is preserved"))
        throw Error("Override save was not confirmed. Retry Save or Clear, or reload to inspect active overrides.");
      throw error;
    } finally {
      // A read started while the POST was pending may still contain the old price.
      resaleMutationPending = false;
      ++resaleGeneration;
      ++shoppingSequence;
    }
    resaleDrafts.delete(overrideDraftKey(itemId));
    await loadState();
    renderResaleOverrides();
    await Promise.all([loadResults(true), loadShopping()]);
    message(clear ? "Resale override cleared. Current estimates refreshed." : isPet ? "Resale override saved for this exact pet variant across all realms. Current estimates refreshed." : "Resale override saved for this item across all realms. Current estimates refreshed.");
  } finally {
    resaleSaving = false;
  }
}
function resaleEditor(itemId, price, hasOverride) {
  const form = el("form", undefined, "row-actions");
  const label = el("label", "Unit resale (gold)"), input = el("input");
  input.inputMode = "decimal";
  input.value = resaleDrafts.get(overrideDraftKey(itemId)) ?? inputGold(price);
  input.oninput = () => resaleDrafts.set(overrideDraftKey(itemId), input.value);
  label.append(input);
  const save = el("button", "Save override"), clear = el("button", "Clear override", "secondary"), status = el("span");
  save.type = "submit";
  clear.type = "button";
  clear.disabled = !hasOverride;
  let busy = false;
  const perform = async removing => {
    if (busy || resaleSaving) return;
    busy = true;
    resaleDrafts.set(overrideDraftKey(itemId), input.value);
    input.disabled = save.disabled = clear.disabled = true;
    status.textContent = "Saving…";
    try {
      await saveResaleOverride(itemId, input.value, removing);
      status.textContent = removing ? "Override cleared" : "Override saved";
    } catch (error) {
      status.textContent = error.message;
      message(error.message, true);
    } finally {
      busy = false;
      input.disabled = save.disabled = false;
      clear.disabled = !hasOverride;
    }
  };
  form.onsubmit = event => { event.preventDefault(); return perform(false); };
  save.onclick = () => perform(false);
  clear.onclick = () => perform(true);
  form.append(label, save, clear, status);
  return form;
}
function resaleVerification(row) {
  const box = el("div", undefined, "resale-verification"), v = row.valuation, override = row.resale_override;
  box.append(
    el("h3", "Verify unit resale"),
    el("p", `Raw TSM average sale price: ${gold(v.avg_sale_price)} • Updated ${v.updated_at || "Unknown"} (${age(v.updated_at)})`),
    el("p", `Global resale factor: ${Number(state.settings.resale_factor) * 100}%`),
    el("p", `Effective unit resale: ${gold(effectiveResale(row))}`),
    el("p", override ? `Source: manual override • Saved ${override.updated_at} (${age(override.updated_at)})` : "Source: TSM average sale price × global resale factor"),
    el("p", petVariant(row) ? "TSM price and demand statistics are species aggregate, not variant-specific. No automatic level 25 premium. Compare shows only this exact level, quality and breed. A manual override applies only to this exact pet variant across all realms." : "A manual override sets the final unit resale for this item across all realms until cleared. Source freshness and demand still apply."),
    resaleEditor(overrideTarget(row), effectiveResale(row), !!override),
  );
  return box;
}
function renderResaleOverrides() {
  const list = $("resale-overrides");
  if (!list) return;
  list.replaceChildren();
  const overrides = state.resale_overrides || [];
  if (!overrides.length) list.append(el("p", "No active resale overrides."));
  for (const entry of overrides) {
    const row = el("div", undefined, "shopping-row");
    row.append(el("p", `${entry.pet ? petLabel(entry.pet) : entry.identity?.startsWith("pet:") ? entry.identity : "Item " + entry.item_id} • Unit resale ${gold(entry.unit_price)} • Manual • Saved ${entry.updated_at} (${age(entry.updated_at)})`));
    const edit = el("button", "Edit", "secondary"), clear = el("button", "Clear override", "secondary");
    edit.type = clear.type = "button";
    edit.onclick = () => {
      $("override-item-id").value = String(entry.identity?.startsWith("pet:") ? entry.identity : entry.item_id);
      $("override-unit-price").value = inputGold(entry.unit_price);
    };
    clear.onclick = async () => {
      if (resaleSaving) return;
      clear.disabled = true;
      try { await saveResaleOverride(entry.identity?.startsWith("pet:") ? entry.identity : entry.item_id, null, true); }
      catch (error) { message(error.message, true); }
      finally { clear.disabled = false; }
    };
    row.append(edit, clear);
    list.append(row);
  }
  const form = $("resale-override-form");
  form.onsubmit = async event => {
    event.preventDefault();
    if (resaleSaving) return;
    const controls = form.querySelectorAll("input,button");
    for (const control of controls) control.disabled = true;
    try { await saveResaleOverride($("override-item-id").value, $("override-unit-price").value); }
    catch (error) { message(error.message, true); }
    finally { for (const control of controls) control.disabled = false; }
  };
}
function renderSettings() {
  renderResaleOverrides();
  $("money-settings").replaceChildren();
  for (const [key, label] of Object.entries(moneyFields)) {
    const input = field(
      $("money-settings"),
      "setting-" + key,
      label + " (gold)",
      inputGold(state.settings[key]),
    );
    input.inputMode = "decimal";
    if (key === "capital" && state.history.length) {
      input.disabled = true;
      input.title =
        "Initial capital is frozen. Use a balance adjustment in Ledger.";
    }
  }
  $("filter-settings").replaceChildren();
  for (const [key, label] of Object.entries(percentFields))
    field(
      $("filter-settings"),
      "setting-" + key,
      label,
      Number(state.settings[key]) * 100,
    );
  for (const [key, label] of Object.entries(numericFields))
    field(
      $("filter-settings"),
      "setting-" + key,
      label,
      state.settings[key],
      "number",
    );
  for (const category of ["recipe", "mount", "toy", "decor", "battle-pet"]) {
    const label = el("label"),
      input = el("input");
    input.type = "checkbox";
    input.id = "category-" + category;
    input.checked = state.settings.categories.includes(category);
    label.append(input, document.createTextNode(({recipe:"Recipes", mount:"Mounts", toy:"Toys", decor:"Decor", "battle-pet":"Battle pets"})[category]));
    $("filter-settings").append(label);
  }
  renderRealmSelection();
  $("credential-status").textContent = state.credentials_configured
    ? "Blizzard credentials are configured in the process environment."
    : "Blizzard credentials are not configured. Set the documented environment variables before starting live refresh. Credentials are never entered or returned here.";
}
function renderRealmSelection(preserveDraft = false) {
  const draft = {};
  for (const type of ["buying", "selling"]) {
    draft[type + "_realms"] = preserveDraft ?
      [...$("realm-membership").querySelectorAll(`input[data-membership=${type}]:checked`)].map(n => n.dataset.realm) :
      state.settings[type + "_realms"];
  }
  $("realm-membership").replaceChildren();
  // Keep saved legacy rows visible even if unchecked in this unsaved draft.
  const membership = {buying_realms: [...new Set([...draft.buying_realms, ...state.settings.buying_realms])],
    selling_realms: [...new Set([...draft.selling_realms, ...state.settings.selling_realms])]};
  const realms = realmOptions(state.realms, membership);
  for (const realm of realms) {
    const row = el("div", undefined, "realm-row");
    const siblings = state.realms.filter(r => r.house_id === realm.house_id && r.slug !== realm.slug);
    const membership = realm.house_id == null ? "House not discovered" :
      `Connected house ${realm.house_id}` + (siblings.length ?
        ` • Shared with ${siblings.map(r => r.name || r.slug).join(", ")}` : "");
    row.dataset.search = `${realm.name || realm.slug} ${realm.slug} ${realm.house_id || ""}`.toLowerCase();
    row.append(el("span", `${realm.name || realm.slug} • ${membership}`));
    if (realm.legacy) row.append(el("small", `Previously selected • ${realm.is_auction_realm === false ? "unsupported auction realm" : realm.is_auction_realm !== true ? "auction support unverified" : realm.category || "classification unknown"}. Retained until explicitly cleared.`, "legacy-realm"));
    for (const type of ["buying", "selling"]) {
      const label = el("label"), input = el("input");
      input.type = "checkbox";
      input.dataset.realm = realm.slug;
      input.dataset.membership = type;
      input.checked = draft[type + "_realms"].includes(realm.slug);
      input.disabled = realm.legacy && !state.settings[type + "_realms"].includes(realm.slug);
      if (input.disabled) input.title = "This realm is outside the verified US auction choices; only its existing membership can be retained or removed.";
      label.append(input, document.createTextNode(type === "buying" ? "Buy" : "Sell"));
      row.append(label);
      input.onchange = updateRealmCounts;
    }
    $("realm-membership").append(row);
  }
  $("realm-select-all").disabled = !state.realms.some(selectableUSRealm);
  filterRealms();
  updateRealmCounts();
}
function filterRealms() {
  const query = $("realm-search").value.trim().toLowerCase();
  for (const row of $("realm-membership").children) row.hidden = !row.dataset.search.includes(query);
}
function updateRealmCounts() {
  const selected = type => [...$("realm-membership").querySelectorAll(`input[data-membership=${type}]:checked`)];
  $("realm-count").textContent = `${state.realms.filter(selectableUSRealm).length} US realms discovered • ${selected("buying").length} buying • ${selected("selling").length} selling. Search only changes visibility.`;
}
$("realm-search").oninput = filterRealms;
for (const id of ["inventory-search", "inventory-destination", "inventory-status"]) $(id).oninput = renderInventory;
for (const [id, clear] of [["realm-select-all", false], ["realm-clear", true]]) {
  $(id).onclick = () => {
    const inputs = [...$("realm-membership").querySelectorAll('input[data-membership=buying]')];
    const selected = bulkBuy(state.realms, inputs.filter(n => n.checked).map(n => n.dataset.realm), clear);
    for (const input of inputs) input.checked = selected.includes(input.dataset.realm);
    updateRealmCounts();
  };
}
function outcomeFields() {
  const type = $("event-type").value;
  $("quantity-label").hidden = type !== "sale";
  $("amount-label").hidden = !["sale", "fee", "balance-adjustment"].includes(
    type,
  );
  $("amount-caption").textContent =
    type === "sale"
      ? "Net proceeds (gold, after auction cut)"
      : type === "fee"
        ? "Additional fee (gold)"
        : "Balance change (gold, signed; negative removes cash)";
  $("purchase-id").disabled = type === "balance-adjustment";
}
function renderCorrection(force = false) {
  if (correctionDirty && !force) return;
  if (force) correctionDirty = false;
  let event = state.history.find(
    (e) => e.id === Number($("correction-target").value),
  );
  if (event) {
    const original = event;
    for (const correction of state.history.filter(
      (e) => e.type === "correction" && e.target_id === original.id,
    ))
      event = correction.replacement || original;
  }
  const container = $("correction-fields");
  container.replaceChildren();
  if (!event) return;
  container.hidden = $("correction-action").value === "void";
  const schemas = {
    purchase: ["cost", "quantity", "destination", "note"],
    "inventory-update": ["purchase_id", ...(event.status !== undefined ? ["status"] : []), ...(event.destination !== undefined ? ["destination"] : []), "note"],
    sale: ["purchase_id", "quantity", "net_proceeds", "note"],
    fee: ["purchase_id", "amount", "note"],
    "balance-adjustment": ["amount", "note"],
    expiration: ["purchase_id", "note"],
    cancellation: ["purchase_id", "note"],
    rejection: ["purchase_id", "note"],
  };
  for (const key of schemas[event.type] || []) {
    if (key === "purchase_id") {
      const label = el("label", "Inventory lot"),
        select = el("select");
      select.id = "correct-purchase_id";
      if (event.type !== "sale" && !event.purchase_id)
        select.append(new Option("No specific lot", ""));
      for (const lot of state.history.filter((e) => e.type === "purchase"))
        select.append(
          new Option(`#${lot.id} ${candidateName(lot.candidate)}`, lot.id),
        );
      select.value = event[key] || "";
      label.append(select);
      container.append(label);
    } else if (key === "status") {
      const label = el("label", "Status"), select = el("select"); select.id = "correct-status";
      for (const [value, text] of [["held","Held"],["ready","Ready to sell"],["listed","Listed"]]) select.append(new Option(text,value));
      select.value = event.status; label.append(select); container.append(label);
    } else if (key === "destination") {
      const label = el("label", key),
        select = el("select");
      select.id = "correct-" + key;
      for (const value of [...new Set([event[key], ...state.settings.selling_realms])])
        select.append(new Option(value, value));
      select.setAttribute("data-original", event[key]);
      select.value = event[key];
      label.append(select);
      container.append(label);
    } else
      field(
        container,
        "correct-" + key,
        key.replaceAll("_", " ") +
          (["cost", "quoted_cost", "net_proceeds", "amount"].includes(key)
            ? " (gold)"
            : ""),
        ["cost", "quoted_cost", "net_proceeds", "amount"].includes(key)
          ? inputGold(event[key] || 0)
          : (event[key] ?? ""),
        key === "quantity" ? "number" : "text",
      );
  }
}
$("event-form").onsubmit = (event) => {
  event.preventDefault();
  action(event.submitter, async () => {
    const type = $("event-type").value,
      entry = { type };
    if ($("purchase-id").value && type !== "balance-adjustment")
      entry.purchase_id = Number($("purchase-id").value);
    if (type === "sale") {
      if (!entry.purchase_id)
        throw Error("Choose an inventory lot for the sale");
      entry.quantity = Number($("event-quantity").value);
      entry.net_proceeds = copper($("event-amount").value);
    }
    if (type === "fee" || type === "balance-adjustment")
      entry.amount = copper(
        $("event-amount").value,
        type === "balance-adjustment",
      );
    if ($("event-note").value) entry.note = $("event-note").value;
    await mutation("/api/ledger", { event: entry }, "outcome");
    message("Outcome recorded.");
    await loadState();
  });
};
$("correction-form").onsubmit = (event) => {
  event.preventDefault();
  action(event.submitter, async () => {
    const target = Number($("correction-target").value);
    if (!target) throw Error("Choose an original event");
    let replacement = null;
    if ($("correction-action").value !== "void") {
      replacement = {};
      for (const input of $("correction-fields").querySelectorAll(
        "input,select",
      )) {
        const key = input.id.slice(8);
        const original = state.history.find(e => e.id === target);
        if (original?.type === "inventory-update" && key === "destination" && input.value === input.getAttribute("data-original")) continue;
        if (key === "purchase_id" && !input.value) continue;
        replacement[key] = [
          "cost",
          "quoted_cost",
          "net_proceeds",
          "amount",
        ].includes(key)
          ? copper(input.value, key === "amount")
          : ["quantity", "purchase_id"].includes(key)
            ? Number(input.value)
            : input.value;
      }
    }
    await mutation(
      "/api/ledger",
      { event: { type: "correction", target_id: target, replacement } },
      "correction",
    );
    message("Correction recorded. Original history retained.");
    await loadState();
  });
};
$("settings-form").onsubmit = (event) => {
  event.preventDefault();
  action(event.submitter, async () => {
    const data = {};
    for (const key of Object.keys(moneyFields))
      data[key] = copper($("setting-" + key).value);
    for (const key of Object.keys(percentFields))
      data[key] = fractionPercent($("setting-" + key).value);
    for (const key of Object.keys(numericFields))
      data[key] = Number($("setting-" + key).value);
    for (const type of ["buying", "selling"])
      data[type + "_realms"] = [
        ...$("realm-membership").querySelectorAll(
          `input[data-membership=${type}]:checked`,
        ),
      ].map((n) => n.dataset.realm);
    data.categories = ["recipe", "mount", "toy", "decor", "battle-pet"].filter(
      (c) => $("category-" + c).checked,
    );
    await api("/api/settings", data);
    retireResults();
    message("Settings saved.");
    await loadState(true);
    await loadResults(true);
    await loadShopping();
  });
};
$("import-form").onsubmit = (event) => {
  event.preventDefault();
  action(event.submitter, async () => {
    const file = $("import-file").files[0];
    if (!file) throw Error("Choose a source file");
    const result = await api("/api/import", {
      kind: $("import-kind").value,
      text: await file.text(),
      source_time: $("import-time").value || null,
      house_id: Number($("import-house").value),
    });
    retireResults();
    message(`${result.records} offline records imported. ${result.warning}`);
    await loadState();
    await loadResults(true);
  });
};
$("refresh").onclick = () =>
  action($("refresh"), async () => {
    await api("/api/refresh", {});
    message("Refreshing official sources. Progress appears under Source evidence and refresh progress.");
    await loadState();
  });
for (const button of document.querySelectorAll("nav button"))
  button.onclick = async () => {
    currentTab = button.dataset.tab;
    for (const node of document.querySelectorAll(".tab"))
      node.hidden = node.id !== currentTab;
    for (const node of document.querySelectorAll("nav button"))
      node.classList.toggle("active", node === button);
    try {
      if (currentTab === "shopping") await loadShopping();
      if (currentTab === "settings") renderSettings();
    } catch (error) {
      message(error.message, true);
    }
  };
for (const id of ["sort", "direction", "destination"])
  $(id).onchange = () => {
    resultsPage = 1;
    loadResults().catch((e) => message(e.message, true));
  };
initializeOpportunityCategories();
for (const [prefix, load] of [["result", loadResults], ["exclusion", loadExclusions]]) {
  for (const [direction, step] of [["previous", -1], ["next", 1]]) {
    $(prefix + "-" + direction).onclick = () => {
      if (prefix === "result") resultsPage += step;
      else exclusionsPage += step;
      load().catch((e) => message(e.message, true));
    };
  }
}
$("exclusion-inspection").ontoggle = () => {
  if ($("exclusion-inspection").open) {
    loadExclusions().catch((e) => message(e.message, true));
  } else {
    ++exclusionsSequence;
    $("exclusions").replaceChildren();
    $("exclusion-pagination").hidden = true;
  }
};
let searchTimer;
$("search").oninput = () => {
  clearTimeout(searchTimer);
  resultsRequest?.controller.abort();
  resultsRequest = null;
  ++resultsSequence;
  invalidateResults("Waiting for search…");
  resultsPage = 1;
  searchTimer = setTimeout(
    () => loadResults().catch((e) => message(e.message, true)),
    180,
  );
};
$("event-type").onchange = outcomeFields;
$("correction-target").onchange = () => renderCorrection(true);
$("correction-action").onchange = () => {
  correctionDirty = true;
  $("correction-fields").hidden = $("correction-action").value === "void";
};
$("correction-fields").oninput = () => {
  correctionDirty = true;
};
$("correction-fields").onchange = () => {
  correctionDirty = true;
};
(async () => {
  try {
    await loadState(true);
    showRestoreNotice();
    outcomeFields();
    await loadResults();
    await loadShopping();
    pollMonitor(); startMonitorPolling();
    if (pending.size) {
      message(
        [...pending.keys()].some(key => key.startsWith("inventory:"))
          ? "An inventory action is unconfirmed. Open Inventory to retry its saved values. Other unconfirmed actions can be retried below."
          : "An earlier action is unconfirmed. Retry its exact original values below.",
      );
      for (const [key, entry] of pending) {
        if (key.startsWith("inventory:")) continue;
        const [path, payload] = JSON.parse(entry.fingerprint);
        const button = el(
          "button",
          "Retry unconfirmed " + (payload.event?.type || "selection"),
        );
        button.onclick = () =>
          action(button, async () => {
            await mutation(path, payload, key);
            message("Original action confirmed.");
            await loadState();
            await loadShopping();
          });
        $("message").append(document.createTextNode(" "), button);
      }
    }
  } catch (error) {
    message(error.message, true);
  }
})();
initializeBackupControls();
initializeMonitorControls();
let refreshPollRunning = false;
setInterval(async () => {
  if (!state?.refresh.running || refreshPollRunning) return;
  refreshPollRunning = true;
  try {
    state.refresh = await api("/api/refresh/status");
    renderSources();
    syncBackupControls();
    $("refresh").disabled = state.mode === "offline-demo" || state.refresh.running;
    $("refresh").textContent = state.refresh.running ? "Refreshing sources…" : "Refresh sources";
    if (!state.refresh.running) {
      message("Refresh completed. Updated opportunities are available for review.");
      await pollMonitor();
    }
  } catch (error) {
    message(error.message, true);
  } finally {
    refreshPollRunning = false;
  }
}, 2000);
