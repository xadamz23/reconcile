// Panel (transaction form), keyboard shortcuts, payee autocomplete, service worker.
(() => {
  const panel = () => document.getElementById("panel");
  const panelBody = () => document.getElementById("panel-body");
  let lastFocus = null;
  let emptyPanel = null; // desktop placeholder, restored when the form closes

  function openPanel() {
    const p = panel();
    if (!p) return;
    if (!document.body.classList.contains("panel-open")) lastFocus = document.activeElement;
    document.body.classList.add("panel-open");
    p.setAttribute("aria-hidden", "false");
    const first = p.querySelector("[autofocus]") || p.querySelector("input:not([type=radio]):not([type=hidden])");
    if (first && window.matchMedia("(hover: hover)").matches) first.focus();
  }

  function closePanel() {
    const p = panel();
    if (!p) return;
    document.body.classList.remove("panel-open");
    p.setAttribute("aria-hidden", "true");
    // Wait for the mobile sheet to slide away before clearing it.
    setTimeout(() => {
      if (emptyPanel === null || document.body.classList.contains("panel-open")) return;
      panelBody().innerHTML = emptyPanel;
      window.htmx.process(panelBody());
    }, 260);
    document.querySelectorAll(".txn.is-selected").forEach((el) => el.classList.remove("is-selected"));
    if (lastFocus && document.contains(lastFocus)) lastFocus.focus();
  }

  document.addEventListener("DOMContentLoaded", () => {
    if (panelBody()) emptyPanel = panelBody().innerHTML;
  });

  document.addEventListener("htmx:afterSwap", (e) => {
    // Only loading a form opens the panel; saves fire afterSwap too (with HX-Reswap: none).
    if (e.detail.target.id === "panel-body" && e.detail.requestConfig.verb === "get") openPanel();
  });

  // Highlight the row being edited (desktop side panel).
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
    if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.key === "n" && document.querySelector(".fab")) {
      e.preventDefault();
      document.querySelector(".fab").click();
    } else if (e.key === "/" && document.getElementById("filter-q")) {
      e.preventDefault();
      document.getElementById("filter-q").focus();
    }
  });

  // Cash-register amount entry for inputs with [data-cents]: digits fill in from the
  // right, so typing 1 2 5 0 gives 12.50 and no decimal point key is needed.
  function centsFromDigits(digits) {
    digits = digits.replace(/\D/g, "").replace(/^0+/, "").slice(0, 9);
    if (!digits) return "";
    const padded = digits.padStart(3, "0");
    return `${padded.slice(0, -2)}.${padded.slice(-2)}`;
  }

  function setCents(el, value) {
    el.value = value;
    el.setSelectionRange(value.length, value.length);
  }

  document.addEventListener("input", (e) => {
    if (e.target.matches("input[data-cents]")) setCents(e.target, centsFromDigits(e.target.value));
  });

  // Pasted text like "12.5" or "$1,234.56" is read as a normal amount, not as raw digits.
  document.addEventListener("paste", (e) => {
    if (!e.target.matches("input[data-cents]")) return;
    const m = (e.clipboardData.getData("text") || "").replace(/[$,\s]/g, "").match(/^(\d*)(?:\.(\d{0,2}))?/);
    if (!m || !(m[1] || m[2])) return;
    e.preventDefault();
    setCents(e.target, centsFromDigits((m[1] || "") + (m[2] || "").padEnd(2, "0")));
  });

  // Payee combobox: suggests past payees only. Never fills in the amount.
  document.addEventListener("alpine:init", () => {
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

  if ("serviceWorker" in navigator) {
    window.addEventListener("load", () => navigator.serviceWorker.register("/sw.js").catch(() => {}));
  }
})();
