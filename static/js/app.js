// Panel (transaction form), keyboard shortcuts, payee autocomplete, service worker.
(() => {
  const panel = () => document.getElementById("panel");
  const panelBody = () => document.getElementById("panel-body");
  let lastFocus = null;

  function openPanel() {
    const p = panel();
    if (!p) return;
    const wasOpen = document.body.classList.contains("panel-open");
    if (!wasOpen) lastFocus = document.activeElement;
    document.body.classList.add("panel-open");
    p.setAttribute("aria-hidden", "false");
    // A new transaction's amount takes focus (data-autofocus, not autofocus, which htmx
    // would focus too early); on a pointer device an edit form's first field does too.
    const first = p.querySelector("[data-autofocus]") ||
      (window.matchMedia("(hover: hover)").matches && p.querySelector("input:not([type=radio]):not([type=hidden])"));
    // The phone sheet appears in place (no slide), so the field can take focus at once
    // without Chrome panning to a field that's still moving.
    if (first) first.focus({ preventScroll: true });
  }

  function closePanel() {
    const p = panel();
    if (!p) return;
    document.body.classList.remove("panel-open");
    p.setAttribute("aria-hidden", "true");
    // Wait for the desktop drawer to slide away before clearing it.
    setTimeout(() => {
      if (!document.body.classList.contains("panel-open")) panelBody().innerHTML = "";
    }, 260);
    document.querySelectorAll(".txn.is-selected").forEach((el) => el.classList.remove("is-selected"));
    if (lastFocus && document.contains(lastFocus)) lastFocus.focus();
  }

  document.addEventListener("htmx:afterSwap", (e) => {
    // Only loading a form opens the panel; saves fire afterSwap too (with HX-Reswap: none).
    if (e.detail.target.id === "panel-body" && e.detail.requestConfig.verb === "get") openPanel();
  });

  // Highlight the row being edited (beside the desktop drawer).
  document.addEventListener("htmx:beforeRequest", (e) => {
    const row = e.detail.elt.closest && e.detail.elt.closest(".txn-open");
    if (row) {
      document.querySelectorAll(".txn.is-selected").forEach((el) => el.classList.remove("is-selected"));
      row.parentElement.classList.add("is-selected");
    }
  });

  document.addEventListener("panel-close", closePanel);
  document.addEventListener("click", (e) => {
    if (e.target.closest("[data-panel-close]")) closePanel();
  });

  document.addEventListener("keydown", (e) => {
    const typing = e.target.closest("input, textarea, select, [contenteditable]");
    if (e.key === "Escape" && document.body.classList.contains("panel-open")) {
      closePanel();
      return;
    }
    // Otherwise Esc leaves search (unless it's closing the masthead menu).
    if (e.key === "Escape" && !document.querySelector('.menu [aria-expanded="true"]')) {
      window.dispatchEvent(new CustomEvent("filters-escape"));
      return;
    }
    if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
    // The desktop New transaction button or the tab bar's + (one is always hidden,
    // but click() still works).
    const add = document.querySelector("[data-new-txn]");
    if (e.key === "n" && add) {
      e.preventDefault();
      add.click();
    } else if (e.key === "/" && document.getElementById("filter-q")) {
      e.preventDefault();
      document.getElementById("filter-q").focus();
    }
  });

  // Cash-register amount entry for inputs with [data-cents]: digits fill in from the
  // right, so typing 1 2 5 0 gives 12.50 and no decimal point key is needed.
  // data-cents="signed" (the statement balance, which can be overdrawn) also takes a
  // minus: typing "-" flips the sign.
  function centsFromDigits(digits, negative = false) {
    const sign = negative ? "-" : "";
    digits = digits.replace(/\D/g, "").replace(/^0+/, "").slice(0, 9);
    if (!digits) return sign;
    const padded = digits.padStart(3, "0");
    return `${sign}${padded.slice(0, -2)}.${padded.slice(-2)}`;
  }

  const signed = (el) => el.dataset.cents === "signed";

  function setCents(el, value) {
    el.value = value;
    el.setSelectionRange(value.length, value.length);
  }

  document.addEventListener("input", (e) => {
    if (!e.target.matches("input[data-cents]")) return;
    // An odd number of minus signs means the latest "-" turned it negative.
    const negative = signed(e.target) && (e.target.value.match(/-/g) || []).length % 2 === 1;
    setCents(e.target, centsFromDigits(e.target.value, negative));
  });

  // Pasted text like "12.5" or "$1,234.56" is read as a normal amount, not as raw digits.
  document.addEventListener("paste", (e) => {
    if (!e.target.matches("input[data-cents]")) return;
    const m = (e.clipboardData.getData("text") || "").replace(/[$,\s]/g, "").match(/^(-)?(\d*)(?:\.(\d{0,2}))?/);
    if (!m || !(m[2] || m[3])) return;
    e.preventDefault();
    const negative = signed(e.target) && Boolean(m[1]);
    setCents(e.target, centsFromDigits((m[2] || "") + (m[3] || "").padEnd(2, "0"), negative));
  });

  // Clicking Payment/Deposit leaves focus where it was (usually the amount), so typing
  // carries on. A label click would otherwise move focus to its hidden radio.
  document.addEventListener("mousedown", (e) => {
    if (e.target.closest(".sign-opt")) e.preventDefault();
  });
  document.addEventListener("click", (e) => {
    const opt = e.target.closest(".sign-opt");
    if (!opt || e.target.matches("input")) return; // Space on the radio itself clicks the input
    e.preventDefault();
    const radio = opt.querySelector("input");
    if (!radio.matches(":disabled")) radio.checked = true;
  });

  // Reconcile: a tick or untick moves the row between the To check and On statement
  // groups. The row collapses while the request runs (the swap waits for it with
  // swap:180ms), taps on the list are ignored until the new list has settled, so a
  // quick second tap can't land on the row that slid into place, and keyboard focus
  // moves to the row that took the old one's place.
  let recFocus = null;
  document.addEventListener("htmx:beforeRequest", (e) => {
    const btn = e.detail.elt;
    if (!btn.matches?.("[data-rec-tick]") || document.documentElement.dataset.savedAt) return;
    const row = btn.closest(".txn-rec");
    const marks = [...row.parentElement.querySelectorAll("[data-rec-tick]")];
    recFocus = document.activeElement === btn
      ? { ticked: row.parentElement.classList.contains("rec-ticked"), index: marks.indexOf(btn) }
      : null;
    document.getElementById("rec-body").classList.add("is-busy");
    row.style.height = `${row.offsetHeight}px`;
    void row.offsetHeight; // start the transition from the measured height
    row.classList.add("is-leaving");
    row.style.height = "0px";
  });
  document.addEventListener("htmx:afterRequest", (e) => {
    if (e.detail.successful || !e.detail.elt.matches?.("[data-rec-tick]")) return;
    const row = e.detail.elt.closest(".txn-rec");
    row.classList.remove("is-leaving");
    row.style.height = "";
    document.getElementById("rec-body").classList.remove("is-busy");
  });
  document.addEventListener("htmx:afterSettle", (e) => {
    const body = e.detail.target;
    if (body.id !== "rec-body") return;
    setTimeout(() => body.classList.remove("is-busy"), 250);
    if (!recFocus) return;
    const group = body.querySelector(recFocus.ticked ? ".rec-ticked" : ".rec-list:not(.rec-ticked)");
    const marks = group ? group.querySelectorAll("[data-rec-tick]") : [];
    const next = marks[Math.min(recFocus.index, marks.length - 1)] ||
      body.querySelector("[data-rec-tick]") || body.querySelector(".rec-summary button");
    if (next) next.focus({ preventScroll: true });
    recFocus = null;
  });

  document.addEventListener("alpine:init", () => {
    // Register filter bar. `searching` shows the bar on mobile (the search line is
    // always shown on desktop). Reconciled items are on while searching and off
    // otherwise, so entering search ticks the switch.
    window.Alpine.data("filterBar", (filtering) => ({
      searching: filtering,
      tray: false,
      kind: "all",
      rec: false,
      init() {
        this.sync();
      },
      // Filters that differ from the search defaults.
      get count() {
        return this.searching ? (this.kind !== "all") + !this.rec : 0;
      },
      sync() {
        const f = this.$refs.filters;
        this.kind = f.kind.value;
        this.rec = f.show_reconciled.checked;
      },
      refetch() {
        this.sync();
        window.htmx.trigger(this.$refs.filters, "change"); // one refetch for the whole change
      },
      enter() {
        if (this.searching) return;
        this.searching = true;
        this.$refs.filters.show_reconciled.checked = true;
        this.refetch();
      },
      // From the mobile tab bar too, which may be tapped far down the list.
      open() {
        this.enter();
        window.scrollTo({ top: 0 });
        this.$nextTick(() => this.$refs.q.focus());
      },
      toggleTray() {
        this.enter();
        this.tray = !this.tray;
      },
      dropKind() {
        this.$refs.filters.kind.value = "all";
        this.refetch();
      },
      showReconciled() {
        this.$refs.filters.show_reconciled.checked = true;
        this.refetch();
      },
      // Desktop's masthead close button and Esc: clear the filters and leave search.
      // On mobile the tab bar's Register link does this by reloading.
      cancel() {
        if (!this.searching) return;
        const f = this.$refs.filters;
        f.q.value = "";
        f.amount.value = "";
        f.kind.value = "all";
        f.show_reconciled.checked = false;
        this.searching = false;
        this.tray = false;
        if (f.contains(document.activeElement)) document.activeElement.blur();
        this.refetch();
      },
    }));

    // Overflow menu. A tap or click outside only closes it: it never reaches what's
    // underneath. Capture-phase listeners on window run before anything else (htmx,
    // Alpine, links). Cancelling touchstart stops the click and the focus (no keyboard)
    // on touch; with a mouse, mousedown is cancelled (no focus) and the click swallowed.
    window.Alpine.data("menu", () => ({
      open: false,
      init() {
        let swallow = false;
        const outside = (e) => this.open && !this.$el.contains(e.target);
        const onTouch = (e) => {
          if (!outside(e)) return;
          e.preventDefault();
          this.open = false;
        };
        const onDown = (e) => {
          swallow = outside(e); // reset each press, in case the last one never clicked
          if (!swallow) return;
          e.preventDefault();
          this.open = false;
        };
        const onClick = (e) => {
          if (!swallow) return;
          swallow = false;
          e.preventDefault();
          e.stopImmediatePropagation();
        };
        window.addEventListener("touchstart", onTouch, { capture: true, passive: false });
        window.addEventListener("mousedown", onDown, true);
        window.addEventListener("click", onClick, true);
        // A boosted page swaps the masthead out, so take these off with the old menu.
        this.removeListeners = () => {
          window.removeEventListener("touchstart", onTouch, { capture: true });
          window.removeEventListener("mousedown", onDown, true);
          window.removeEventListener("click", onClick, true);
        };
      },
      destroy() {
        this.removeListeners?.();
      },
    }));

    // Reconcile's Read statement strip. Choosing a file sends it at once, and while Claude
    // reads (usually about 20 seconds) the strip shows how long it's been. Past SLOW_S it
    // says it's slow, so a slow read doesn't look stuck; the server gives up at 90s.
    const SLOW_S = 45;
    window.Alpine.data("statementRead", () => ({
      reading: false,
      name: "",
      secs: 0,
      timer: null,
      init() {
        // Back to this page from the result (bfcache) shouldn't show it still reading.
        const onShow = (e) => { if (e.persisted) this.stop(); };
        window.addEventListener("pageshow", onShow);
        this.removeListeners = () => window.removeEventListener("pageshow", onShow);
      },
      choose(input) {
        if (!input.files.length) return;
        this.name = input.files[0].name;
        this.reading = true;
        const started = Date.now();
        this.secs = 0;
        this.timer = setInterval(() => { this.secs = Math.floor((Date.now() - started) / 1000); }, 250);
        input.form.requestSubmit();
      },
      stop() {
        clearInterval(this.timer);
        this.reading = false;
      },
      get elapsed() {
        return `${Math.floor(this.secs / 60)}:${String(this.secs % 60).padStart(2, "0")}`;
      },
      get slow() {
        return this.secs >= SLOW_S;
      },
      destroy() {
        clearInterval(this.timer);
        this.removeListeners?.();
      },
    }));

    // Payee combobox: suggests past payees only. Never fills in the amount.
    window.Alpine.data("payeeBox", (initial) => ({
      value: initial,
      items: [],
      open: false,
      active: -1,
      seq: 0,
      async fetch() {
        const q = this.value.trim();
        const seq = ++this.seq;
        if (!q) return this.close();
        const res = await window.fetch(`/payees?q=${encodeURIComponent(q)}`);
        if (!res.ok || seq !== this.seq) return; // a newer keystroke won
        const items = await res.json();
        // Hide the list when the only suggestion is exactly what's typed.
        this.items = items.filter((p) => p.toLowerCase() !== this.value.trim().toLowerCase() || items.length > 1);
        this.active = -1;
        this.open = this.items.length > 0 && document.activeElement === this.$root.querySelector("input");
      },
      move(step) {
        if (!this.open) return this.fetch();
        this.active = (this.active + step + this.items.length) % this.items.length;
      },
      pick(item) {
        this.value = item;
        this.close();
      },
      close() {
        this.open = false;
        this.active = -1;
      },
    }));
  });

  // Dismissing the Android keyboard (back gesture, down arrow) keeps the field
  // focused, and focus is what hides the tab bar. The keyboard resizes the page
  // (interactive-widget=resizes-content), so blur once the page is back to full
  // height. Any keyboard is far taller than the margin, which only has to allow for
  // the browser's address bar, so switching keyboards between fields doesn't count.
  let fullHeight = window.innerHeight;
  let fullWidth = window.innerWidth;
  let keyboardOpen = false;
  window.addEventListener("resize", () => {
    if (window.innerWidth !== fullWidth) { // rotated
      fullWidth = window.innerWidth;
      fullHeight = window.innerHeight;
      keyboardOpen = false;
      return;
    }
    fullHeight = Math.max(fullHeight, window.innerHeight);
    const open = window.innerHeight < fullHeight - 120;
    if (keyboardOpen && !open && document.activeElement?.matches("input, textarea")) document.activeElement.blur();
    keyboardOpen = open;
  });

  // The Overview's balance chart: a crosshair and readout for the nearest day under the
  // pointer, or stepped with the arrow keys. data-points holds [x%, y%, date, amount,
  // projected] per day, as laid out by routes/overview.py. Run on load and after each
  // boosted page swap; data-ready stops a chart being set up twice.
  const initCharts = () => document.querySelectorAll(".bal-chart:not([data-ready])").forEach((chart) => {
    chart.dataset.ready = "";
    const points = JSON.parse(chart.dataset.points);
    const plot = chart.querySelector(".bal-plot");
    const cross = chart.querySelector(".bal-cross");
    const dot = chart.querySelector(".bal-hover");
    const tip = chart.querySelector(".bal-tip");
    let current = points.findLastIndex((p) => !p[4]); // today
    function show(i) {
      current = Math.max(0, Math.min(points.length - 1, i));
      const [x, y, day, amount, projected] = points[current];
      chart.classList.add("is-reading");
      cross.style.left = dot.style.left = `${x}%`;
      dot.style.top = `${y}%`;
      dot.classList.toggle("is-proj", Boolean(projected));
      // Keep the readout inside the plot near either edge.
      const width = plot.clientWidth;
      const px = Math.min(Math.max((x / 100) * width, 64), width - 64);
      tip.style.left = `${px}px`;
      tip.style.top = `${y}%`;
      tip.querySelector("b").textContent = amount;
      tip.querySelector("span").textContent = projected ? `${day} · after bills` : day;
    }
    const hide = () => chart.classList.remove("is-reading");
    plot.addEventListener("pointermove", (e) => {
      const r = plot.getBoundingClientRect();
      show(Math.round(((e.clientX - r.left) / r.width) * (points.length - 1)));
    });
    plot.addEventListener("pointerleave", () => { if (document.activeElement !== chart) hide(); });
    chart.addEventListener("focus", () => show(current));
    chart.addEventListener("blur", hide);
    chart.addEventListener("keydown", (e) => {
      const step = { ArrowLeft: -1, ArrowRight: 1, Home: -points.length, End: points.length }[e.key];
      if (step === undefined) return;
      e.preventDefault();
      show(current + step);
    });
  });
  initCharts();

  // The tab bar on other pages links to /register#search and /register#new: act on them once the
  // register has loaded, then drop the hash so a reload doesn't do it again. history.state is
  // kept, since htmx marks its own entries there (Back from a boosted page needs the mark).
  function actOnHash(hash) {
    if (hash !== "#search" && hash !== "#new") return;
    history.replaceState(history.state, "", location.pathname + location.search);
    if (hash === "#search") window.dispatchEvent(new CustomEvent("filters-open"));
    else document.querySelector("[data-new-txn]")?.click();
  }
  window.addEventListener("load", () => actOnHash(location.hash));

  // Boosted nav links (hx-boost on the tab bar, sidebar and masthead links) swap in the new
  // page's body instead of loading it, so the scripts above don't run again. Carry over what
  // a load would have done. Events fire on each swapped-in element too, so only the body's
  // own event counts.
  document.addEventListener("htmx:afterSwap", (e) => {
    if (!e.detail.boosted || e.target !== document.body) return;
    const m = e.detail.xhr.responseText.match(/<body\b[^>]*\bclass="([^"]*)"/);
    document.body.className = m ? m[1] : ""; // the page's class, and drops panel-open
  });
  document.addEventListener("htmx:afterSettle", (e) => {
    if (!e.detail.boosted || e.target !== document.body) return;
    initCharts();
    // After Alpine has set up the new page's components (its observer runs before this).
    const hash = new URL(e.detail.pathInfo.requestPath, location.href).hash;
    setTimeout(() => actOnHash(hash));
  });
  // A boosted load that fails (no connection, the server down, an error page, an expired
  // Cloudflare Access login) falls back to a normal load, which gets the service worker's
  // saved copy or shows the error just as before.
  const loadNormally = (e) => {
    if (e.detail.requestConfig?.boosted) location.assign(e.detail.requestConfig.path);
  };
  document.addEventListener("htmx:sendError", loadNormally);
  document.addEventListener("htmx:responseError", loadNormally);
  document.addEventListener("htmx:timeout", loadNormally);

  // Prefetch: as a finger or mouse button goes down on a boosted link, ask the service worker
  // to start loading its page, so it's on its way before the tap ends (see sw.js). Only when
  // the worker is in charge (without it this would just be a second request), and not on a
  // saved offline page. A tap fires touchstart and then mousedown: one request is enough.
  let lastPrefetch = { href: "", at: 0 };
  function prefetch(e) {
    const link = e.target.closest?.("a[href]");
    if (!link || link.hasAttribute("download") || !navigator.serviceWorker?.controller) return;
    if (document.documentElement.dataset.savedAt) return;
    if (link.closest("[hx-boost]")?.getAttribute("hx-boost") !== "true") return;
    const url = new URL(link.href);
    url.hash = "";
    if (url.origin !== location.origin) return;
    if (url.href === lastPrefetch.href && Date.now() - lastPrefetch.at < 2000) return;
    lastPrefetch = { href: url.href, at: Date.now() };
    fetch(url, { headers: { "HX-Request": "true", "HX-Boosted": "true", "X-Prefetch": "1" } }).catch(() => {});
  }
  document.addEventListener("touchstart", prefetch, { passive: true });
  document.addEventListener("mousedown", prefetch);

  // Back and Forward reload the page instead of using htmx's snapshots (historyCacheSize is
  // 0 in base.html). Clear any snapshots older versions saved, so none is ever restored.
  try { localStorage.removeItem("htmx-history-cache"); } catch {}

  // Offline. The service worker saves each main page and serves that copy when there's no
  // connection or the server can't be reached, with <html data-offline> saying which
  // ("offline" or "down") and data-saved-at its time. A saved page is read-only: a note
  // says when it's from, and anything that needs the server is stopped.
  const savedAt = document.documentElement.dataset.savedAt;
  const down = document.documentElement.dataset.offline === "down";
  function when(ms) {
    const d = new Date(Number(ms));
    const time = d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    if (d.toDateString() === new Date().toDateString()) return time;
    return `${d.toLocaleDateString([], { month: "short", day: "numeric" })}, ${time}`;
  }
  if (savedAt) {
    const note = document.createElement("div");
    note.className = "toast offline-note glass-strong is-popover";
    note.setAttribute("role", "status");
    // Two lines (what's wrong, then when the copy is from), so Retry fits on a phone.
    note.innerHTML = '<span><b></b><small></small></span><button type="button">Retry</button>';
    const text = note.querySelector("b");
    note.querySelector("small").textContent = `Saved ${when(savedAt)}`;
    const say = () => { text.textContent = down ? "Can't reach the server" : "Offline"; };
    say();
    note.lastChild.addEventListener("click", () => location.reload());
    document.body.append(note); // outside main (see .toast)
    let timer;
    const refuse = () => {
      text.textContent = "Reconnect to make changes";
      clearTimeout(timer);
      timer = setTimeout(say, 3000);
    };
    document.addEventListener("htmx:beforeRequest", (e) => {
      e.preventDefault();
      // A nav link loads normally, so the service worker can serve that page's saved copy.
      if (e.detail.requestConfig?.boosted) location.assign(e.detail.requestConfig.path);
      else if (e.detail.elt.matches?.(".ledger-more")) e.detail.elt.textContent = "Reconnect to load more";
      else refuse();
    });
    // Capturing on window gets in before htmx's own submit handling.
    window.addEventListener("submit", (e) => {
      e.preventDefault();
      e.stopImmediatePropagation();
      refuse();
    }, true);
    window.addEventListener("online", () => location.reload());
  }

  // The last totals seen online, for /offline when no page has been saved yet.
  const TOTALS = "reconcile:totals";
  function saveTotals() {
    const t = document.getElementById("side-totals") || document.getElementById("totals");
    if (!t) return;
    const bal = t.querySelector(".total-balance dd");
    const last = t.querySelector(".total-last");
    const snap = {
      at: Date.now(),
      balance: bal.textContent.trim(), neg: bal.classList.contains("neg"),
      lastLabel: last.querySelector("dt").textContent.trim(),
      lastValue: last.querySelector("dd").textContent.trim(),
      lastNeg: last.querySelector("dd").classList.contains("neg"),
    };
    try { localStorage.setItem(TOTALS, JSON.stringify(snap)); } catch {}
  }
  if (!savedAt) {
    saveTotals();
    document.addEventListener("htmx:afterSettle", saveTotals); // saves swap the totals out of band
  }
  // /offline served because the server is down, not the phone.
  if (down && !savedAt) {
    const h1 = document.querySelector(".setup h1");
    const lede = document.querySelector(".setup-lede");
    document.title = "Can't reach the server";
    if (h1) h1.textContent = "Can't reach the server";
    if (lede) lede.textContent = "Your connection is fine, but Reconcile isn't answering, and this page hasn't been saved on this device yet. Try again in a moment.";
  }
  const offlineTotals = document.querySelector("[data-offline-totals]");
  if (offlineTotals) {
    let snap = null;
    try { snap = JSON.parse(localStorage.getItem(TOTALS)); } catch {}
    if (snap) {
      const set = (k, value, neg) => {
        const el = offlineTotals.querySelector(`[data-k="${k}"]`);
        el.textContent = value;
        el.classList.toggle("neg", Boolean(neg));
      };
      set("at", when(snap.at));
      set("balance", snap.balance, snap.neg);
      set("lastLabel", snap.lastLabel);
      set("lastValue", snap.lastValue, snap.lastNeg);
      offlineTotals.hidden = false;
    }
  }

  // Bills' Plan button opens the worksheet as a small window of its own, or brings it to
  // the front if it's already open. Opening by name with no URL returns an open Plan
  // window as it is (no reload); a new one starts blank and is then sent to /plan. If
  // popups are blocked, the link opens it in a tab instead.
  document.addEventListener("click", (e) => {
    const link = e.target.closest("[data-plan]");
    if (!link || e.ctrlKey || e.metaKey || e.shiftKey) return;
    const w = 700;
    const left = Math.max(0, window.screenX + window.outerWidth - w - 40);
    const win = window.open("", link.target, `popup,width=${w},height=640,left=${left},top=${window.screenY + 80}`);
    if (!win) return;
    e.preventDefault();
    if (win.location.href === "about:blank") win.location.replace(link.href);
    win.focus();
  });

  if ("serviceWorker" in navigator) {
    window.addEventListener("load", () => navigator.serviceWorker.register("/sw.js").catch(() => {}));
  }
})();
