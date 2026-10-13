"use strict";
// Mirror backend selectable_us_realm: US category plus canonical house membership.
function selectableUSRealm(realm) {
  return realm.category === "United States" && realm.is_auction_realm === true;
}
function realmOptions(realms, settings) {
  const selected = new Set([...settings.buying_realms, ...settings.selling_realms]);
  const options = realms.filter(r => selectableUSRealm(r) || selected.has(r.slug))
    .map(r => ({...r, legacy: !selectableUSRealm(r)}));
  const discovered = new Set(realms.map(r => r.slug));
  for (const slug of selected) if (!discovered.has(slug))
    options.push({slug, name: slug, legacy: true});
  return options;
}
function bulkBuy(realms, selected, clear = false) {
  return clear ? [] : [...new Set([...selected,
    ...realms.filter(selectableUSRealm).map(r => r.slug)])];
}
if (typeof module !== "undefined") module.exports = {selectableUSRealm, realmOptions, bulkBuy};
