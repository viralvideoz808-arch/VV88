// VV88 UI helpers. No inline scripts (strict CSP): behaviour is attached via data-* attributes.
(function () {
  // <button data-confirm="Really?"> inside a form: ask before submitting.
  document.addEventListener("click", function (e) {
    const el = e.target.closest("[data-confirm]");
    if (el && !window.confirm(el.getAttribute("data-confirm"))) e.preventDefault();
  });

  // <textarea data-maxlen="80"> / <input data-maxlen="30">: live character counter.
  document.querySelectorAll("[data-maxlen]").forEach(function (el) {
    const max = parseInt(el.getAttribute("data-maxlen"), 10);
    const counter = document.createElement("div");
    counter.className = "counter";
    el.insertAdjacentElement("afterend", counter);
    const update = function () {
      const n = Array.from(el.value).length;
      counter.textContent = n + " / " + max;
      counter.classList.toggle("over", n > max);
    };
    el.addEventListener("input", update);
    update();
  });

  // <button data-copy="text"> or data-copy-target="#id": copy to clipboard.
  document.addEventListener("click", function (e) {
    const el = e.target.closest("[data-copy],[data-copy-target]");
    if (!el) return;
    e.preventDefault();
    let text = el.getAttribute("data-copy");
    const target = el.getAttribute("data-copy-target");
    if (target) { const t = document.querySelector(target); text = t ? (t.value || t.textContent) : ""; }
    navigator.clipboard.writeText(text || "").then(function () {
      const old = el.textContent; el.textContent = "Copied"; setTimeout(function () { el.textContent = old; }, 1200);
    });
  });

  // <pre data-poll="/jobs/12/log.json">: live job log; reloads the page when the job finishes.
  const pollEl = document.querySelector("[data-poll]");
  if (pollEl) {
    const url = pollEl.getAttribute("data-poll");
    const tick = function () {
      fetch(url, { credentials: "same-origin", headers: { "Accept": "application/json" } })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) {
          if (!d) return;
          const atBottom = pollEl.scrollTop + pollEl.clientHeight >= pollEl.scrollHeight - 20;
          pollEl.textContent = d.log || "(waiting for output…)";
          if (atBottom) pollEl.scrollTop = pollEl.scrollHeight;
          if (d.status === "running" || d.status === "queued") setTimeout(tick, 2000);
          else setTimeout(function () { window.location.reload(); }, 600);
        })
        .catch(function () { setTimeout(tick, 4000); });
    };
    tick();
  }

  // <select data-platform-hint="#hint">: show where "post after rendering" will post for the chosen option
  // (options carry data-platforms="tiktok youtube" and data-named="…" computed by the server).
  document.querySelectorAll("select[data-platform-hint]").forEach(function (sel) {
    const hint = document.querySelector(sel.getAttribute("data-platform-hint"));
    if (!hint) return;
    const update = function () {
      const opt = sel.selectedOptions[0];
      if (!opt) return;
      const plats = (opt.getAttribute("data-platforms") || "").split(" ").filter(Boolean);
      const named = opt.getAttribute("data-named") || "";
      hint.querySelectorAll("[data-plat]").forEach(function (b) { b.hidden = plats.indexOf(b.getAttribute("data-plat")) < 0; });
      const camp = hint.querySelector("[data-hint-campaign]");
      const none = hint.querySelector("[data-hint-enabled]");
      if (camp) camp.hidden = plats.length > 0 || !named;
      if (none) none.hidden = plats.length > 0 || !!named;
      const n = hint.querySelector("[data-hint-named]");
      if (n) n.textContent = named;
    };
    sel.addEventListener("change", update);
    window.addEventListener("pageshow", update);  // Back/reload restore the select without a change event
    update();
  });

  // <div data-refresh="15">: reload the page every N seconds (dashboard while jobs run).
  const refreshEl = document.querySelector("[data-refresh]");
  if (refreshEl) {
    const s = parseInt(refreshEl.getAttribute("data-refresh"), 10);
    if (s > 0) setTimeout(function () { window.location.reload(); }, s * 1000);
  }
})();
