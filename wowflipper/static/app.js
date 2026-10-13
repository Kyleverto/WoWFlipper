"use strict";
const $ = (id) => document.getElementById(id);
let state,
  rows = [],
  shopping = [],
  csrf,
  currentTab = "opportunities";
let resultsSequence = 0;
let resultsRequest = null;
let resultsPage = 1, exclusionsPage = 1, exclusionsSequence = 0;
let correctionDirty = false;
const pending = new Map(
  JSON.parse(sessionStorage.getItem("pending-ledger-actions") || "[]"),
);
function savePending() {
  sessionStorage.setItem(
    "pending-ledger-actions",
    JSON.stringify([...pending]),
  );
}
const moneyFields = {
  capital: "Initial capital",
  stronger_demand_budget: "Stronger demand allocation",
  longer_term_budget: "Longer term allocation",
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
function params() {
  return new URLSearchParams({
    strategy: $("ranking").value,
    q: $("search").value,
    sort: $("sort").value,
    direction: $("direction").value,
    page: resultsPage,
    page_size: 100,
  });
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
async function loadState(initial = false) {
  state = await api("/api/state");
  csrf = state.csrf;
  $("mode").textContent =
    state.mode === "offline-demo"
      ? "Offline demo • synthetic market"
      : "Local research";
  $("refresh").disabled = state.mode === "offline-demo" || state.refresh.running;
  $("refresh").textContent = state.refresh.running ? "Refreshing sources…" : "Refresh sources";
  renderCapital();
  renderSources();
  renderLedger();
  if (initial) renderSettings();
  else if (currentTab === "settings") renderRealmSelection(true);
  populateDestinations();
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
        "No market evidence yet. Configure credentials, choose realms and refresh, or import your offline files in Settings.",
      ),
    );
  for (const s of state.sources)
    target.append(
      el(
        "p",
        `${s.source}: ${s.mode.startsWith("offline") ? "Offline evidence" : "Source snapshot"} • age ${age(s.source_time)} • source ${s.source_time || "unknown"} • fetched ${s.fetched_at} • snapshot ${s.snapshot_id}`,
      ),
    );
  for (const [source, status] of Object.entries(state.refresh.sources || {}))
    target.append(
      el(
        "p",
        `${source}: ${status.state}${status.state === "failed" ? " — Configure credentials if needed, retry after rate limits, or inspect imported data." : ""}${status.metadata_skipped ? " • Metadata limit reached; refresh again to enrich remaining items." : ""}`,
      ),
    );
  if (state.refresh.running)
    target.append(el("p", "Refreshing sources… Polling progress."));
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
function loadResults(force = false) {
  const query = params().toString();
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
        "No eligible opportunities. Choose buying and selling realms in Settings, refresh sources or import offline evidence. Stale sources, uncertain variants and filters exclude rows.",
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
      el("strong", v.name),
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
    addCell(tr, gold(v.avg_sale_price * Number(state.settings.resale_factor)));
    addCell(tr, gold(row.proceeds));
    addCell(tr, gold(row.profit), "positive");
    addCell(tr, (Number(row.roi) * 100).toFixed(1) + "%");
    addCell(tr, (Number(v.sale_rate) * 100).toFixed(1) + "%");
    addCell(tr, v.sold_per_day);
    addCell(tr, `${gold(v.market_value)} / ${gold(v.historical)}`);
    addCell(
      tr,
      `${age(a.source_time)} auctions / ${age(v.updated_at)} valuation`,
    );
    const controls = el("div", undefined, "row-actions");
    td.append(controls);
    const inspect = el("button", "Compare", "secondary"),
      select = el("button", "Select listing");
    controls.append(inspect, document.createTextNode(" "), select);
    select.disabled = !$("destination").value;
    select.title = select.disabled
      ? "Choose a configured selling realm in Settings"
      : "Reserve this whole listing";
    body.append(tr);
    const details = el("tr", undefined, "details");
    details.hidden = true;
    const detail = addCell(details, "");
    detail.colSpan = 12;
    detail.append(
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
    detail.append(
      comparisons,
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
    select.onclick = () =>
      action(select, async () => {
        await mutation(
          "/api/shopping",
          {
            key: row.key,
            strategy: $("ranking").value,
            destination: $("destination").value,
          },
          "select:" + row.key.join(":"),
        );
        message("Listing added to shopping list.");
        await loadState();
        await loadShopping();
      });
  }
  container.append(wrap);
}
async function loadShopping() {
  shopping = await api("/api/shopping");
  const container = $("shopping-items");
  container.replaceChildren();
  if (!shopping.length) {
    container.append(
      el(
        "div",
        "No selected listings. Select a whole listing from Opportunities.",
        "empty",
      ),
    );
    return;
  }
  for (const item of shopping) {
    const row = el("div", undefined, "shopping-row"),
      info = el("div");
    info.append(
      el("strong", item.candidate.valuation.name),
      el(
        "p",
        `${item.quantity} units • ${gold(item.cost)} quoted • ${item.destination} • ${item.strategy}`,
      ),
      el(
        "p",
        item.valid
          ? "Currently eligible; manually verify in game"
          : item.validation_error,
        "caution",
      ),
    );
    const form = el("form"),
      label = el("label", "Actual total paid (gold)"),
      input = el("input");
    input.value = inputGold(item.cost);
    input.inputMode = "decimal";
    label.append(input);
    const buy = el("button", "Record purchase");
    buy.type = "submit";
    buy.disabled = !item.valid;
    form.append(label, buy);
    form.onsubmit = (event) => {
      event.preventDefault();
      action(buy, async () => {
        await mutation(
          "/api/ledger",
          {
            event: {
              type: "purchase",
              key: item.key,
              cost: copper(input.value),
            },
          },
          "purchase:" + item.key.join(":"),
        );
        message("Purchase recorded. Inventory and cash updated.");
        await loadState();
        await loadShopping();
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
        message("Selection removed.");
        await loadState();
        await loadShopping();
      });
    row.append(info, form, remove);
    container.append(row);
  }
}
function renderLedger() {
  const inventory = state.summary.inventory;
  $("inventory").replaceChildren();
  if (!inventory.length)
    $("inventory").append(
      el(
        "div",
        "No held inventory. Record a purchase from Shopping list.",
        "empty",
      ),
    );
  else {
    const { wrap, body } = table([
      "Lot",
      "Item",
      "Remaining units",
      "Remaining cost basis",
      "Destination / strategy",
    ]);
    for (const lot of inventory) {
      const tr = el("tr");
      addCell(tr, lot.id);
      addCell(tr, lot.candidate.valuation.name);
      addCell(tr, lot.remaining_quantity);
      addCell(tr, gold(lot.remaining_cost));
      addCell(tr, `${lot.destination} / ${lot.strategy}`);
      body.append(tr);
    }
    $("inventory").append(wrap);
  }
  const old = $("purchase-id").value;
  $("purchase-id").replaceChildren(new Option("No specific inventory lot", ""));
  for (const lot of state.history.filter((e) => e.type === "purchase")) {
    const held = inventory.find((i) => i.id === lot.id);
    $("purchase-id").append(
      new Option(
        `#${lot.id} ${lot.candidate.valuation.name} (${held ? held.remaining_quantity : 0} units held)`,
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
function renderSettings() {
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
  for (const category of ["recipe", "mount", "toy"]) {
    const label = el("label"),
      input = el("input");
    input.type = "checkbox";
    input.id = "category-" + category;
    input.checked = state.settings.categories.includes(category);
    label.append(input, document.createTextNode(category));
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
    if (realm.legacy) row.append(el("small", `Previously selected • ${realm.category || "classification unknown"}. Retained until explicitly cleared.`, "legacy-realm"));
    for (const type of ["buying", "selling"]) {
      const label = el("label"), input = el("input");
      input.type = "checkbox";
      input.dataset.realm = realm.slug;
      input.dataset.membership = type;
      input.checked = draft[type + "_realms"].includes(realm.slug);
      input.disabled = realm.legacy && !state.settings[type + "_realms"].includes(realm.slug);
      if (input.disabled) input.title = "This realm is outside the US choices; only its existing membership can be retained or removed.";
      label.append(input, document.createTextNode(type === "buying" ? "Buy" : "Sell"));
      row.append(label);
      input.onchange = updateRealmCounts;
    }
    $("realm-membership").append(row);
  }
  $("realm-select-all").disabled = !state.realms.some(r => r.category === "United States");
  filterRealms();
  updateRealmCounts();
}
function filterRealms() {
  const query = $("realm-search").value.trim().toLowerCase();
  for (const row of $("realm-membership").children) row.hidden = !row.dataset.search.includes(query);
}
function updateRealmCounts() {
  const selected = type => [...$("realm-membership").querySelectorAll(`input[data-membership=${type}]:checked`)];
  $("realm-count").textContent = `${state.realms.filter(r => r.category === "United States").length} US realms discovered • ${selected("buying").length} buying • ${selected("selling").length} selling. Search only changes visibility.`;
}
$("realm-search").oninput = filterRealms;
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
    purchase: ["cost", "quantity", "strategy", "destination", "note"],
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
          new Option(`#${lot.id} ${lot.candidate.valuation.name}`, lot.id),
        );
      select.value = event[key] || "";
      label.append(select);
      container.append(label);
    } else if (key === "strategy" || key === "destination") {
      const label = el("label", key),
        select = el("select");
      select.id = "correct-" + key;
      for (const value of key === "strategy"
        ? ["stronger-demand", "longer-term"]
        : state.settings.selling_realms)
        select.append(new Option(value, value));
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
    data.categories = ["recipe", "mount", "toy"].filter(
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
for (const id of ["ranking", "sort", "direction", "destination"])
  $(id).onchange = () => {
    resultsPage = 1;
    loadResults().catch((e) => message(e.message, true));
  };
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
    outcomeFields();
    await loadResults();
    await loadShopping();
    if (pending.size) {
      message(
        "An earlier action is unconfirmed. Retry its exact original values below.",
      );
      for (const [key, entry] of pending) {
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
let refreshPollRunning = false;
setInterval(async () => {
  if (!state?.refresh.running || refreshPollRunning) return;
  refreshPollRunning = true;
  try {
    await loadState();
    if (!state.refresh.running) {
      retireResults();
      message(
        "Refresh completed. Inspect source errors and source ages before selecting.",
      );
      await loadResults(true);
      if (currentTab === "shopping") await loadShopping();
    }
  } catch (error) {
    message(error.message, true);
  } finally {
    refreshPollRunning = false;
  }
}, 2000);
