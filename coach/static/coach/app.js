(function () {
  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
  const csrf = () => ($("[name=csrfmiddlewaretoken]") || {}).value;
  const post = (url, body, headers) =>
    fetch(url, { method: "POST", headers: Object.assign({ "X-CSRFToken": csrf() }, headers || {}), body: body || null });
  const postJSON = (url, data) =>
    post(url, JSON.stringify(data), { "Content-Type": "application/json" }).then((r) => r.json());
  async function copyText(text, btn, okLabel) {
    const original = btn.textContent;
    try { await navigator.clipboard.writeText(text); btn.textContent = okLabel || "Copied"; }
    catch (e) { btn.textContent = "Press Ctrl+C"; }
    setTimeout(() => (btn.textContent = original), 1800);
  }
  function autosize(ta) { ta.style.height = "auto"; ta.style.height = ta.scrollHeight + "px"; }

  // Confirm before destructive form posts
  $$("form[data-confirm]").forEach((f) =>
    f.addEventListener("submit", (e) => { if (!confirm(f.dataset.confirm)) e.preventDefault(); })
  );

  // ----- Today workspace -----
  const work = $(".work");
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
        const d = await (await post(url)).json();
        row.classList.toggle("is-done", d.done);
        row.querySelector(".tick").setAttribute("aria-pressed", d.done);
        const status = pane.querySelector("[data-status]");
        status.textContent = d.done ? "Done" : "To do";
        status.classList.toggle("ok", d.done);
        pane.querySelector("[data-label=done]").textContent = d.done ? "Undo" : "Mark done";
        const seg = $('[data-seg="' + id + '"]');
        if (seg) seg.classList.toggle("on", d.done);
        $("#doneN").textContent = d.done_count;
        $("#totalN").textContent = d.total;
      } catch (e) { /* leave UI unchanged if the request fails */ }
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

    // Schedule post in X: copy the text, open the composer, remind which time to pick
    $$(".sched").forEach((a) => a.addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(a.dataset.text); } catch (e) { /* opening X still works */ }
      const hint = a.closest(".actions").nextElementSibling;
      if (hint && hint.classList.contains("sched-hint")) hint.hidden = false;
    }));
  }

  // ----- A new day: build the plan automatically -----
  const build = $("#buildCard");
  if (build) {
    const msg = $("#buildMsg"), retry = $("#buildRetry"), spin = $("#buildSpin");
    const idle = msg.textContent;
    async function run() {
      retry.hidden = true; spin.hidden = false; msg.textContent = idle; msg.classList.remove("err");
      try {
        const d = await (await post(build.dataset.url, null, { "X-Requested-With": "fetch" })).json();
        if (d.ok) { location.replace(location.pathname); return; }
        msg.textContent = d.error;
      } catch (e) { msg.textContent = "Could not reach the server. Check your connection and try again."; }
      msg.classList.add("err"); spin.hidden = true; retry.hidden = false;
    }
    retry.addEventListener("click", run);
    run();
  }

  // ----- Yesterday's unfinished tasks -----
  const carry = $("#carryCard");
  if (carry) {
    const go = $("#carryGo"), warn = $("#carryWarn"), ticks = $$("[data-carry]", carry);
    const refresh = () => {
      const all = ticks.every((t) => t.getAttribute("aria-pressed") === "true");
      go.textContent = all ? go.dataset.all : go.dataset.skip;
      go.classList.toggle("primary", all);
      warn.hidden = all;
    };
    ticks.forEach((t) => t.addEventListener("click", async () => {
      try {
        const d = await (await post(t.dataset.url)).json();
        t.setAttribute("aria-pressed", d.done);
        refresh();
      } catch (e) { /* ignore */ }
    }));
  }

  // ----- Reply helper -----
  const fetchBtn = $("#fetchBtn");
  if (fetchBtn) {
    const status = $("#fetchStatus"), linkEl = $("#id_link");
    const force = $("#id_force");
    if (force) force.value = "False"; // never carry "reply anyway" into the next request

    function showPreview(d) {
      const bits = [d.likes != null && d.likes + " likes", d.replies != null && d.replies + " replies"].filter(Boolean).join(" · ");
      $("#pvAuthor").textContent = d.author;
      $("#pvMeta").textContent = bits ? " " + bits : "";
      $("#pvText").textContent = d.text.length > 300 ? d.text.slice(0, 300) + "..." : d.text;
      $("#preview").hidden = false;
      $("#id_post_meta").value = bits;
      $("#postEdit").open = false;
    }
    async function doFetch() {
      const link = linkEl.value.trim();
      if (!link) { status.textContent = "Paste a post link first."; return; }
      fetchBtn.disabled = true; status.textContent = "Fetching...";
      try {
        const body = new FormData(); body.append("link", link);
        const d = await (await post(fetchBtn.dataset.url, body)).json();
        if (d.ok) {
          $("#id_post_text").value = d.text;
          $("#id_author").value = d.author;
          $("#id_author_bio").value = d.author_bio;
          showPreview(d);
          status.textContent = "Got it. Check the preview, pick what you want the reply to do, then continue.";
        } else { status.textContent = d.error; $("#postEdit").open = true; }
      } catch (e) { status.textContent = "Could not fetch. Paste the post text instead."; $("#postEdit").open = true; }
      fetchBtn.disabled = false;
    }
    fetchBtn.addEventListener("click", doFetch);
    const pasteBtn = $("#pasteBtn");
    if (pasteBtn) pasteBtn.addEventListener("click", async () => {
      try {
        const text = (await navigator.clipboard.readText()).trim();
        if (!text) { status.textContent = "Your clipboard is empty."; return; }
        linkEl.value = text;
        doFetch();
      } catch (e) { status.textContent = "Could not read the clipboard. Click the box and press Ctrl+V instead."; }
    });
    const forceBtn = $("#forceBtn");
    if (forceBtn) forceBtn.addEventListener("click", () => { force.value = "True"; $("#replyForm").requestSubmit(); });
  }

  const replyCtx = $("#replyCtx"), replyUrl = $("#replyActUrl");
  if (replyCtx && replyUrl) {
    const base = JSON.parse(replyCtx.textContent);
    const approaches = () => $$(".rp .appr").map((p) => p.textContent);
    $$(".rp").forEach((card) => {
      const ta = $(".rp-text", card), cnt = $(".count", card), pill = $(".appr", card), angle = $(".rp-angle", card);
      const recount = () => { cnt.textContent = ta.value.length + "/280"; cnt.classList.toggle("over", ta.value.length > 280); };
      ta.addEventListener("input", () => { recount(); autosize(ta); });
      autosize(ta);
      $(".rp-edit", card).addEventListener("click", () => { ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length); });
      $(".rp-copy", card).addEventListener("click", (e) => copyText(ta.value, e.currentTarget));
      $$(".rp-act", card).forEach((b) => b.addEventListener("click", async () => {
        const original = b.textContent;
        $$(".rp-act", card).forEach((x) => (x.disabled = true));
        b.textContent = "Working...";
        try {
          const d = await postJSON(replyUrl.value, {
            mode: b.dataset.mode, ctx: base.ctx, settings: base.settings, current: ta.value,
            approach: pill.textContent, used: approaches(),
          });
          if (d.ok) {
            ta.value = d.text; pill.textContent = d.approach; angle.textContent = d.angle;
            recount(); autosize(ta);
          } else { b.textContent = (d.error || "Failed").slice(0, 40); await new Promise((r) => setTimeout(r, 2200)); }
        } catch (e) { b.textContent = "Failed"; await new Promise((r) => setTimeout(r, 1800)); }
        b.textContent = original;
        $$(".rp-act", card).forEach((x) => (x.disabled = false));
      }));
    });
  }

  // ----- Bio positioning -----
  const bioInput = $("#bioInput");
  if (bioInput) {
    const count = $("#bioCount");
    const upd = () => { count.textContent = bioInput.value.length + "/160"; count.classList.toggle("over", bioInput.value.length > 160); };
    bioInput.addEventListener("input", upd); upd();
  }
  const bioCtx = $("#bioCtx"), bioUrl = $("#bioActUrl");
  if (bioCtx && bioUrl) {
    const base = JSON.parse(bioCtx.textContent);
    $$(".bv").forEach((card) => {
      const ta = $(".bv-text", card), cnt = $(".count", card), why = $(".bv-why span", card);
      const recount = () => { cnt.textContent = ta.value.length + "/160"; cnt.classList.toggle("over", ta.value.length > 160); };
      ta.addEventListener("input", () => { recount(); autosize(ta); });
      autosize(ta);
      $(".bv-use", card).addEventListener("click", (e) => {
        $$(".bv").forEach((c) => c.classList.toggle("chosen", c === card));
        copyText(ta.value, e.currentTarget, "Copied. Paste it in X: Edit profile");
      });
      $$(".bv-act", card).forEach((b) => b.addEventListener("click", async () => {
        const original = b.textContent;
        $$(".bv-act", card).forEach((x) => (x.disabled = true));
        b.textContent = "Working...";
        try {
          const d = await postJSON(bioUrl.value, { action: b.dataset.action, text: ta.value, direction: card.dataset.direction, ctx: base });
          if (d.ok) { ta.value = d.bio; why.textContent = d.why || why.textContent; recount(); autosize(ta); }
          else { b.textContent = (d.error || "Failed").slice(0, 40); await new Promise((r) => setTimeout(r, 2200)); }
        } catch (e) { b.textContent = "Failed"; await new Promise((r) => setTimeout(r, 1800)); }
        b.textContent = original;
        $$(".bv-act", card).forEach((x) => (x.disabled = false));
      }));
    });
  }

  // ----- Copy buttons (Today drafts) -----
  $$(".copy").forEach((b) => b.addEventListener("click", () => copyText(b.dataset.text, b)));
})();
