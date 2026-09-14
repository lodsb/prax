/* The chosen theme, applied before the first paint: loaded in <head>, so
   it runs before the body renders and a reload does not flash. Settings
   live in this browser only (localStorage) and never reach the door.
   The themes are docs/design/BRIEF.md's six; "system" is Bindery by day
   and Night when the system prefers dark. Earlier settings' light / dark /
   paper map onto them. */
(function () {
  var THEMES = ["bindery", "dessau", "riso", "cyanotype", "night", "funk"];
  var OLD = { light: "bindery", dark: "night", paper: "bindery" };
  var theme = "system";
  try {
    var s = JSON.parse(localStorage.getItem("prax.settings") || "{}");
    theme = OLD[s.theme] || s.theme || "system";
  } catch (e) { /* no storage: the system theme */ }
  // ?theme=night in the address tries one without saving it (a screenshot, a link)
  var asked = /[?&]theme=([a-z]+)/.exec(location.search);
  if (asked && THEMES.indexOf(asked[1]) >= 0) theme = asked[1];
  if (THEMES.indexOf(theme) < 0) {
    var dark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
    theme = dark ? "night" : "bindery";
  }
  document.documentElement.dataset.praxTheme = theme;
})();
