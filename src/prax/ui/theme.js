/* The chosen theme, applied before the first paint: loaded in <head>, so
   it runs before the body renders and a reload does not flash. Settings
   live in this browser only (localStorage) and never reach the door. */
try {
  var praxSettings = JSON.parse(localStorage.getItem("prax.settings") || "{}");
  if (praxSettings.theme && praxSettings.theme !== "system") {
    document.documentElement.dataset.theme = praxSettings.theme;
  }
} catch (e) { /* no storage: the system theme */ }
