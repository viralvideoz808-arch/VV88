(function () {
  "use strict";
  document.addEventListener("submit", function (e) {
    var btn = e.submitter, form = e.target;
    var msg = (btn && btn.getAttribute("data-confirm")) || form.getAttribute("data-confirm");
    if (msg && !window.confirm(msg)) { e.preventDefault(); return; }
    if (btn && !btn.hasAttribute("formnovalidate")) setTimeout(function () { btn.disabled = true; btn.textContent = "Working…"; }, 0);
  });
  document.querySelectorAll("[data-autosubmit]").forEach(function (el) {
    el.addEventListener("change", function () { el.form.submit(); });
  });
  document.querySelectorAll("textarea[data-count]").forEach(function (ta) {
    var out = document.getElementById(ta.getAttribute("data-count"));
    var upd = function () { if (out) out.textContent = ta.value.length + " characters"; };
    ta.addEventListener("input", upd); upd();
  });
  var thread = document.getElementById("thread");
  if (thread) thread.scrollTop = thread.scrollHeight;
})();
