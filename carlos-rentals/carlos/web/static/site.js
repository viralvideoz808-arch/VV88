(function () {
  "use strict";
  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };
  var money = function (n) {
    return "$" + Number(n).toLocaleString("en-US", { minimumFractionDigits: n % 1 ? 2 : 0, maximumFractionDigits: 2 });
  };

  // mobile menu
  var toggle = $("[data-toggle=nav]");
  if (toggle) toggle.addEventListener("click", function () {
    var nav = $("#nav"); var open = nav.classList.toggle("open");
    toggle.setAttribute("aria-expanded", open ? "true" : "false");
  });

  // hero search: rent / buy tabs
  var hero = $("#hero-search");
  if (hero) {
    $$(".tabs button", hero).forEach(function (btn) {
      btn.addEventListener("click", function () {
        var mode = btn.getAttribute("data-mode");
        $$(".tabs button", hero).forEach(function (b) { b.classList.toggle("on", b === btn); b.setAttribute("aria-selected", b === btn); });
        $$("[data-for]", hero).forEach(function (row) {
          var on = row.getAttribute("data-for") === mode;
          row.hidden = !on;
          $$("input,select", row).forEach(function (el) { el.disabled = !on; });
        });
        hero.action = mode === "sale" ? "/for-sale" : "/rentals";
        hero.querySelector("[name=available]").disabled = mode === "sale";
      });
    });
    var hs = $("#s-start"), he = $("#s-end");
    if (hs && he) hs.addEventListener("change", function () {
      if (he.value <= hs.value) { var d = new Date(hs.value); d.setDate(d.getDate() + 3); he.value = d.toISOString().slice(0, 10); }
      he.min = hs.value;
    });
  }

  // gallery
  var thumbs = $("#thumbs");
  if (thumbs) thumbs.addEventListener("click", function (e) {
    var btn = e.target.closest("button"); if (!btn) return;
    var img = $("#gallery-main img"); if (img) img.src = btn.getAttribute("data-src");
    $$("button", thumbs).forEach(function (b) { b.classList.toggle("on", b === btn); });
  });

  // live rental quote
  var form = $("#book-form");
  if (form) {
    var box = $("#quote");
    var update = function () {
      var start = form.start.value, end = form.end.value;
      if (!start || !end) { box.hidden = true; return; }
      var params = new URLSearchParams({ car: form.getAttribute("data-car"), start: start, end: end });
      $$("input[name=extras]:checked", form).forEach(function (x) { params.append("extras", x.value); });
      if (form.age.value) params.set("age", form.age.value);
      fetch("/api/quote?" + params.toString()).then(function (r) { return r.json(); }).then(function (q) {
        box.hidden = false;
        box.textContent = "";
        if (q.error) { box.innerHTML = '<div class="line" style="color:var(--bad)"></div>'; box.firstChild.textContent = q.error; return; }
        var rows = [["Rental · " + q.days + " day" + (q.days === 1 ? "" : "s"), money(q.base)]];
        if (q.saved > 0) rows.push(["Weekly discount", "−" + money(q.saved), "save"]);
        q.extras.forEach(function (x) { rows.push([x.label, money(x.amount)]); });
        if (q.fees) rows.push(["Young driver fee", money(q.fees)]);
        rows.push(["Tax", money(q.tax)]);
        rows.forEach(function (r) {
          var line = document.createElement("div"); line.className = "line" + (r[2] ? " " + r[2] : "");
          var a = document.createElement("span"); a.textContent = r[0];
          var b = document.createElement("span"); b.textContent = r[1];
          line.appendChild(a); line.appendChild(b); box.appendChild(line);
        });
        var total = document.createElement("div"); total.className = "line total";
        total.innerHTML = "<span>Total</span><span></span>"; total.lastChild.textContent = money(q.total);
        box.appendChild(total);
        var note = document.createElement("div"); note.className = "line small";
        note.innerHTML = "<span></span><span></span>";
        note.firstChild.textContent = q.available ? "✓ Available for these dates" : "✗ Already booked for these dates";
        note.firstChild.style.color = q.available ? "var(--ok)" : "var(--bad)";
        note.lastChild.textContent = q.deposit ? money(q.deposit) + " refundable deposit" : "";
        box.appendChild(note);
      }).catch(function () { box.hidden = true; });
    };
    form.addEventListener("change", update);
    form.start.addEventListener("change", function () {
      if (form.end.value <= form.start.value) {
        var d = new Date(form.start.value); d.setDate(d.getDate() + 1); form.end.value = d.toISOString().slice(0, 10);
      }
      form.end.min = form.start.value; update();
    });
    update();
  }

  // chat widget
  var fab = $("#chat-fab"), chat = $("#chat");
  if (!fab || !chat) return;
  var log = $("#chat-log"), cform = $("#chat-form"), input = $("#chat-input");
  var token = null, lastId = 0, timer = null, sending = false;
  try { token = localStorage.getItem("carlos_chat"); } catch (e) { token = null; }
  if (!token || !/^[A-Za-z0-9_-]{16,64}$/.test(token)) {
    var bytes = new Uint8Array(18); (window.crypto || {}).getRandomValues ? crypto.getRandomValues(bytes) : bytes.forEach(function (_, i) { bytes[i] = Math.random() * 256; });
    token = Array.prototype.map.call(bytes, function (b) { return ("0" + b.toString(16)).slice(-2); }).join("");
    try { localStorage.setItem("carlos_chat", token); } catch (e) { /* private mode: chat still works this visit */ }
  }
  var linkify = function (el, text) {
    var parts = text.split(/(https?:\/\/[^\s)]+)/g);
    parts.forEach(function (p, i) {
      if (i % 2) { var a = document.createElement("a"); a.href = p; a.textContent = p.replace(/^https?:\/\//, ""); a.rel = "noopener"; el.appendChild(a); }
      else el.appendChild(document.createTextNode(p));
    });
  };
  var bubble = function (who, text, cls) {
    var b = document.createElement("div"); b.className = "bubble " + who + (cls ? " " + cls : "");
    linkify(b, text); log.appendChild(b); log.scrollTop = log.scrollHeight; return b;
  };
  var render = function (messages) {
    messages.forEach(function (m) { if (m.id > lastId) { lastId = m.id; bubble(m.from, m.text); } });
  };
  var poll = function () {
    fetch("/api/chat?token=" + token + "&after=" + lastId).then(function (r) { return r.json(); })
      .then(function (d) { if (d.messages) render(d.messages); }).catch(function () {});
  };
  var greet = function () {
    if (!log.children.length) bubble("assistant", "Hi! 👋 I'm the CAR-LOS virtual assistant. Ask me about prices, availability, cars for sale or selling your car.");
  };
  fab.addEventListener("click", function () {
    var open = chat.classList.toggle("open");
    if (open) { greet(); poll(); input.focus(); timer = setInterval(poll, 6000); } else { clearInterval(timer); }
  });
  $("#chat-close").addEventListener("click", function () { chat.classList.remove("open"); clearInterval(timer); });
  var send = function (text) {
    if (!text || sending) return;
    sending = true;
    var mine = bubble("you", text);
    var typing = bubble("assistant", "typing…", "typing");
    fetch("/api/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ token: token, message: text }) })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        typing.remove(); mine.remove();
        if (d.messages) render(d.messages);
        if (d.reply === null) bubble("assistant", "Thanks! A team member will reply here shortly.");
        else if (d.reply && !d.messages) bubble("assistant", d.reply);
      })
      .catch(function () { typing.remove(); bubble("assistant", "Sorry, I couldn't send that. Please try again."); })
      .then(function () { sending = false; });
  };
  cform.addEventListener("submit", function (e) { e.preventDefault(); var t = input.value.trim(); input.value = ""; send(t); });
  $$("#chat-chips button").forEach(function (b) { b.addEventListener("click", function () { send(b.textContent); }); });
})();
