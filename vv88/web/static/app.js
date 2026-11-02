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

  // <button data-share-src="/media?path=…" data-share-name="x.mp4" data-share-caption="#cap" data-share-title="#t"
  // data-share-status="#msg">: copy the caption, then hand the video to the phone's share sheet (TikTok,
  // Instagram, YouTube, X apps). Needs Chrome on Android over http://127.0.0.1 or localhost (a secure context).
  document.addEventListener("click", function (e) {
    const btn = e.target.closest("[data-share-src]");
    if (!btn) return;
    e.preventDefault();
    const pick = function (attr) { const sel = btn.getAttribute(attr); return sel ? document.querySelector(sel) : null; };
    const status = pick("data-share-status");
    const say = function (msg) { if (status) status.textContent = msg; };
    const capEl = pick("data-share-caption");
    const titleEl = pick("data-share-title");
    const caption = capEl ? capEl.value : "";
    if (caption && navigator.clipboard) navigator.clipboard.writeText(caption).catch(function () {});
    if (!navigator.share || !navigator.canShare) {
      say("This browser can't share videos to apps. Open the website in Chrome on the phone, or tap Download and share the file from your gallery.");
      return;
    }
    const share = function (file) {
      if (!navigator.canShare({ files: [file] })) {
        say("This phone can't share this video from the browser. Tap Download, then share it from your gallery.");
        return;
      }
      const data = { files: [file], text: caption };
      if (titleEl && titleEl.value) data.title = titleEl.value;
      navigator.share(data).then(function () {
        say("Shared. Paste the caption in the app if it's empty (it's copied), post, then tap Mark posted.");
      }, function (err) {
        if (err && err.name === "AbortError") say("Share cancelled.");
        else if (err && err.name === "NotAllowedError") { btn.textContent = "Tap again to share"; say("Video ready: tap the button again."); }
        else say("Couldn't share: " + (err && err.message ? err.message : "unknown error") + ". Tap Download instead.");
      });
    };
    if (btn._vv88File) { share(btn._vv88File); return; }
    const label = btn.textContent;
    btn.disabled = true;
    btn.textContent = "Preparing…";
    fetch(btn.getAttribute("data-share-src"), { credentials: "same-origin" })
      .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.blob(); })
      .then(function (blob) {
        btn._vv88File = new File([blob], btn.getAttribute("data-share-name") || "clip.mp4", { type: "video/mp4" });
        btn.disabled = false;
        btn.textContent = label;
        share(btn._vv88File);  // the tap still counts if loading was quick; otherwise "Tap again"
      })
      .catch(function (err) {
        btn.disabled = false;
        btn.textContent = label;
        say("Couldn't load the video (" + err.message + "). Tap Download instead.");
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
