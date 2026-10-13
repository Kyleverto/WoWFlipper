"use strict";
// Blizzard's realm detail category is the source of membership, never locale.
function realmOptions(realms, settings) {
  const selected = new Set([...settings.buying_realms, ...settings.selling_realms]);
  const options = realms.filter(r => r.category === "United States" || selected.has(r.slug))
    .map(r => ({...r, legacy: r.category !== "United States"}));
  const discovered = new Set(realms.map(r => r.slug));
  for (const slug of selected) if (!discovered.has(slug))
    options.push({slug, name: slug, legacy: true});
  return options;
}
function bulkBuy(realms, selected, clear = false) {
  return clear ? [] : [...new Set([...selected,
    ...realms.filter(r => r.category === "United States").map(r => r.slug)])];
}
if (typeof module !== "undefined") module.exports = {realmOptions, bulkBuy};
