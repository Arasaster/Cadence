(function () {
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
  const csrf = () => (document.querySelector("[name=csrfmiddlewaretoken]") || {}).value;

  // ----- Today workspace -----
  const work = document.querySelector(".work");
  if (work) {
    const rows = $$(".row", work);
    const panes = $$(".pane", work);
    let filter = "all";

    function select(id, scroll) {
      rows.forEach((r) => r.classList.toggle("on", r.dataset.id === String(id)));
      panes.forEach((p) => (p.hidden = p.dataset.pane !== String(id)));
      if (scroll && window.matchMedia("(max-width: 960px)").matches) {
        const pane = panes.find((p) => !p.hidden);
        if (pane) pane.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    }

    function applyFilter() {
      rows.forEach((r) => (r.hidden = filter !== "all" && r.dataset.group !== filter));
      const current = rows.find((r) => r.classList.contains("on") && !r.hidden);
      if (!current) {
        const first = rows.find((r) => !r.hidden);
        if (first) select(first.dataset.id);
      }
    }

    async function toggle(id, url) {
      const row = rows.find((r) => r.dataset.id === String(id));
      const pane = panes.find((p) => p.dataset.pane === String(id));
      try {
        const res = await fetch(url, { method: "POST", headers: { "X-CSRFToken": csrf() } });
        const d = await res.json();
        row.classList.toggle("is-done", d.done);
        row.querySelector(".tick").setAttribute("aria-pressed", d.done);
        const status = pane.querySelector("[data-status]");
        status.textContent = d.done ? "Done" : "To do";
        status.classList.toggle("ok", d.done);
        pane.querySelector("[data-label=done]").textContent = d.done ? "Undo" : "Mark done";
        const seg = document.querySelector('[data-seg="' + id + '"]');
        if (seg) seg.classList.toggle("on", d.done);
        document.getElementById("doneN").textContent = d.done_count;
        document.getElementById("totalN").textContent = d.total;
      } catch (e) {
        /* leave UI unchanged if the request fails */
      }
    }

    $$("[data-select]", work).forEach((b) => b.addEventListener("click", () => select(b.dataset.select, true)));
    $$("[data-toggle]").forEach((b) => b.addEventListener("click", () => toggle(b.dataset.toggle, b.dataset.url)));
    $$("[data-open]").forEach((b) => b.addEventListener("click", () => {
      filter = "all";
      $$(".tab").forEach((t) => t.classList.toggle("on", t.dataset.filter === "all"));
      applyFilter();
      select(b.dataset.open, true);
    }));
    $$(".tab").forEach((t) => t.addEventListener("click", () => {
      filter = t.dataset.filter;
      $$(".tab").forEach((x) => x.classList.toggle("on", x === t));
      applyFilter();
    }));

    if (work.dataset.first) select(work.dataset.first);
  }

  // ----- Copy buttons (Today, Reply helper) -----
  $$(".copy").forEach((b) => {
    b.addEventListener("click", async () => {
      const original = b.textContent;
      try {
        await navigator.clipboard.writeText(b.dataset.text);
        b.textContent = "Copied";
      } catch (e) {
        b.textContent = "Press Ctrl+C";
      }
      setTimeout(() => (b.textContent = original), 1500);
    });
  });
})();
