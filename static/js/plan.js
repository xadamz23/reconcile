// Plan window (templates/plan.html): lines of what might come out, taken off the register
// balance to show what's left. The whole list is saved on every change (debounced), and
// any unsaved change goes out with keepalive when the window closes, so whatever is left
// in it is there next time.
(() => {
  const MAX_CENTS = 999999999; // app/money.py MAX_CENTS
  const SAVE_MS = 400;
  const RETRY_MS = 5000;

  const data = JSON.parse(document.getElementById("plan-data").textContent);
  let balance = data.balance;
  let lines = data.lines; // [{label, cents (null = not counted), note}]

  const $ = (id) => document.getElementById(id);
  const box = $("lines");

  function money(c) {
    const sign = c < 0 ? "-" : "";
    c = Math.abs(c);
    return `${sign}$${Math.floor(c / 100).toLocaleString("en-US")}.${String(c % 100).padStart(2, "0")}`;
  }
  const amountText = (c) => (c == null ? "" : money(c));
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

  // --- Drawing ---

  function render(focusIndex, focusField) {
    box.innerHTML = lines.map((l, i) => {
      const name = esc(l.label) || "line";
      return `<div class="row line${l.cents == null ? " is-blank" : ""}" data-i="${i}">
        <button class="grip" type="button" draggable="true" aria-label="Move ${name} (Alt+Up or Alt+Down)"><svg class="icon" aria-hidden="true"><use href="#i-grip"/></svg></button>
        <input class="cell label" data-f="label" value="${esc(l.label)}" maxlength="100" placeholder="What's coming out" aria-label="What">
        <div class="amt-col"><input class="cell" data-f="cents" inputmode="numeric" value="${amountText(l.cents)}" placeholder="—" aria-label="Amount for ${name}"></div>
        <input class="cell note" data-f="note" value="${esc(l.note)}" maxlength="200" placeholder="Add a note" aria-label="Note for ${name}">
        <button class="del" type="button" aria-label="Remove ${name}"><svg class="icon" aria-hidden="true"><use href="#i-close"/></svg></button>
      </div>`;
    }).join("");
    if (focusIndex != null) box.children[focusIndex]?.querySelector(`[data-f="${focusField || "label"}"]`)?.focus();
    totals();
    fitWindow();
  }

  function totals() {
    const counted = lines.filter((l) => l.cents != null);
    const out = counted.reduce((sum, l) => sum + l.cents, 0);
    const left = balance - out;
    $("start").textContent = money(balance);
    $("start").classList.toggle("neg", balance < 0);
    $("count").textContent = lines.length
      ? `Coming out, ${counted.length} of ${lines.length} ${lines.length === 1 ? "line" : "lines"}`
      : "Coming out";
    $("out").textContent = "−" + money(out);
    $("left").textContent = money(left);
    $("left").classList.toggle("neg", left < 0);
    box.querySelectorAll(".line").forEach((row, i) => row.classList.toggle("is-blank", lines[i].cents == null));
  }

  // Size the window to the pad. Browsers allow it only for a window a script opened.
  function fitWindow() {
    if (!window.opener) return;
    requestAnimationFrame(() => {
      const chrome = window.outerHeight - window.innerHeight;
      const want = Math.min(document.documentElement.scrollHeight + chrome, screen.availHeight - 40);
      if (Math.abs(want - window.outerHeight) > 4) window.resizeTo(window.outerWidth, want);
    });
  }

  // --- Saving ---

  let dirty = false;
  let inFlight = false;
  let timer;
  let doneTimer;

  function status(state) {
    const el = $("saved");
    clearTimeout(doneTimer);
    el.className = "saved" + (state ? ` is-${state}` : "");
    if (state === "saving") el.textContent = "Saving…";
    else if (state === "error") el.textContent = "Couldn't save, trying again";
    else if (state === "done") {
      el.innerHTML = '<svg class="icon" aria-hidden="true"><use href="#i-check"/></svg>Saved';
      doneTimer = setTimeout(() => status(null), 1800);
    } else el.textContent = "";
  }

  const body = () => JSON.stringify({ lines: lines.map((l) => ({ label: l.label, cents: l.cents, note: l.note })) });

  function changed() {
    dirty = true;
    status("saving");
    clearTimeout(timer);
    timer = setTimeout(save, SAVE_MS);
  }

  async function save() {
    clearTimeout(timer);
    if (!dirty || inFlight) return;
    dirty = false;
    inFlight = true;
    let ok = false;
    try {
      const res = await fetch("/plan", { method: "POST", headers: { "Content-Type": "application/json" }, body: body() });
      ok = res.ok;
    } catch { /* offline or the server is down: retry below */ }
    inFlight = false;
    if (!ok) {
      dirty = true;
      status("error");
      timer = setTimeout(save, RETRY_MS);
    } else if (dirty) {
      timer = setTimeout(save, SAVE_MS); // changed while saving
    } else {
      status("done");
    }
  }

  // Closing the window (or reloading it) sends whatever hasn't been saved yet.
  window.addEventListener("pagehide", () => {
    if (!dirty && !inFlight) return;
    fetch("/plan", { method: "POST", headers: { "Content-Type": "application/json" }, body: body(), keepalive: true });
  });
  window.addEventListener("online", () => { if (dirty) save(); });

  // A payment added in the register changes the balance: catch up when the window is used again.
  window.addEventListener("focus", async () => {
    try {
      const res = await fetch("/plan/balance", { cache: "no-store" });
      if (!res.ok) return;
      balance = (await res.json()).cents;
      totals();
    } catch { /* keep the last balance */ }
  });

  // --- Editing ---

  box.addEventListener("input", (e) => {
    const f = e.target.dataset.f;
    if (f !== "label" && f !== "note") return;
    lines[e.target.closest(".line").dataset.i][f] = e.target.value;
    changed();
  });

  function setCents(input, cents) {
    lines[input.closest(".line").dataset.i].cents = cents;
    input.value = amountText(cents);
    totals();
    changed();
  }

  box.addEventListener("keydown", (e) => {
    const row = e.target.closest(".line");
    if (!row) return;
    const i = +row.dataset.i;
    const f = e.target.dataset.f;

    // Alt+Up / Alt+Down moves the line.
    if (e.altKey && (e.key === "ArrowUp" || e.key === "ArrowDown")) {
      const j = i + (e.key === "ArrowUp" ? -1 : 1);
      if (j < 0 || j >= lines.length) return;
      e.preventDefault();
      [lines[i], lines[j]] = [lines[j], lines[i]];
      render(j, f || "label");
      if (!f) box.children[j].querySelector(".grip").focus();
      changed();
      return;
    }
    // Enter goes down a line, and past the last one adds a line.
    if (e.key === "Enter" && f) {
      e.preventDefault();
      if (i === lines.length - 1) addLine();
      else box.children[i + 1].querySelector(`[data-f="${f}"]`).focus();
      return;
    }
    if (f !== "cents" || e.ctrlKey || e.metaKey || e.altKey) return;
    // Cash-register entry, as in the app's amount fields: digits fill from the right,
    // Backspace takes the last one off, and emptying it leaves the line blank (not counted).
    const cents = lines[i].cents;
    if (/^\d$/.test(e.key)) {
      e.preventDefault();
      const next = (cents ?? 0) * 10 + Number(e.key);
      if (next <= MAX_CENTS) setCents(e.target, next === 0 && cents == null ? null : next);
    } else if (e.key === "Backspace") {
      e.preventDefault();
      setCents(e.target, Math.floor((cents ?? 0) / 10) || null);
    } else if (e.key === "Delete") {
      e.preventDefault();
      setCents(e.target, null);
    }
  });

  // Pasting an amount ("$1,450.00", "55") into the amount field.
  box.addEventListener("paste", (e) => {
    if (e.target.dataset.f !== "cents") return;
    e.preventDefault();
    const text = e.clipboardData.getData("text").replace(/[$,\s]/g, "");
    if (!/^\d*(\.\d{0,2})?$/.test(text) || !/\d/.test(text)) return;
    const [whole, frac = ""] = text.split(".");
    const cents = Number(whole || 0) * 100 + Number(frac.padEnd(2, "0"));
    if (cents <= MAX_CENTS) setCents(e.target, cents || null);
  });

  // The amount field only ever holds what setCents put there (typing is handled above).
  box.addEventListener("beforeinput", (e) => {
    if (e.target.dataset.f === "cents" && e.inputType !== "insertFromPaste") e.preventDefault();
  });

  // --- Adding, removing, undo ---

  let undoLines = null;
  let toastTimer;

  function toast(text) {
    $("toast-text").textContent = text;
    $("toast").hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { $("toast").hidden = true; }, 5000);
  }

  function addLine() {
    lines.push({ label: "", cents: null, note: "" });
    render(lines.length - 1, "label");
    changed();
  }
  $("add").addEventListener("click", addLine);

  box.addEventListener("click", (e) => {
    const del = e.target.closest(".del");
    if (!del) return;
    const i = +del.closest(".line").dataset.i;
    undoLines = structuredClone(lines);
    const name = lines[i].label || "line";
    lines.splice(i, 1);
    render(lines.length ? Math.min(i, lines.length - 1) : null);
    if (!lines.length) $("add").focus();
    changed();
    toast(`Removed ${name}`);
  });

  $("undo").addEventListener("click", () => {
    if (!undoLines) return;
    lines = undoLines;
    undoLines = null;
    $("toast").hidden = true;
    render();
    changed();
  });

  // --- Drag to reorder, by the grip ---

  let dragFrom = null;
  const clearDrop = () => box.querySelectorAll(".drop-before, .drop-after").forEach((r) => r.classList.remove("drop-before", "drop-after"));

  box.addEventListener("dragstart", (e) => {
    const row = e.target.closest(".line");
    if (!row) return;
    dragFrom = +row.dataset.i;
    row.classList.add("is-dragging");
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setDragImage(row, 20, 20);
  });
  box.addEventListener("dragover", (e) => {
    if (dragFrom == null) return;
    e.preventDefault();
    clearDrop();
    const row = e.target.closest(".line");
    if (!row) return;
    const r = row.getBoundingClientRect();
    row.classList.add(e.clientY < r.top + r.height / 2 ? "drop-before" : "drop-after");
  });
  box.addEventListener("drop", (e) => {
    e.preventDefault();
    const row = e.target.closest(".line");
    if (!row || dragFrom == null) return;
    let to = +row.dataset.i + (row.classList.contains("drop-after") ? 1 : 0);
    const [moved] = lines.splice(dragFrom, 1);
    if (to > dragFrom) to--;
    lines.splice(to, 0, moved);
    dragFrom = null;
    render();
    changed();
  });
  box.addEventListener("dragend", () => {
    if (dragFrom == null) return; // dropped: already drawn
    dragFrom = null;
    clearDrop();
    box.querySelectorAll(".is-dragging").forEach((r) => r.classList.remove("is-dragging"));
  });

  render();
})();
