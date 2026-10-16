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

  // ----- Reply helper: one tap. Paste, read, fit check, write. -----
  const rf = $("#replyForm");
  const mobile = () => window.matchMedia("(max-width: 960px)").matches;

  function initCards(root) {
    $$(".rp", root).forEach((card) => {
      const ta = $(".rp-text", card), cnt = $(".count", card), pill = $(".appr", card), angle = $(".rp-angle", card);
      const recount = () => { cnt.textContent = ta.value.length + "/280"; cnt.classList.toggle("over", ta.value.length > 280); };
      ta.addEventListener("input", () => { recount(); autosize(ta); });
      autosize(ta);
      $(".rp-edit", card).addEventListener("click", () => { ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length); });
      $(".rp-copy", card).addEventListener("click", (e) => copyText(ta.value, e.currentTarget));
      $$(".rp-act", card).forEach((b) => b.addEventListener("click", async () => {
        const menu = b.closest("details.more");
        if (menu) menu.open = false;
        const trigger = $(".more > summary", card);
        const original = trigger.textContent;
        trigger.textContent = "Working...";
        try {
          const base = JSON.parse($("#replyCtx").textContent);
          const d = await postJSON($("#replyActUrl").value, {
            mode: b.dataset.mode, ctx: base.ctx, settings: base.settings, strategy: base.strategy, current: ta.value,
            approach: pill.textContent, used: $$(".rp .appr").map((p) => p.textContent),
          });
          if (d.ok) { ta.value = d.text; pill.textContent = d.approach; angle.textContent = d.angle; recount(); autosize(ta); trigger.textContent = original; }
          else { trigger.textContent = (d.error || "Failed").slice(0, 40); await new Promise((r) => setTimeout(r, 2200)); trigger.textContent = original; }
        } catch (e) { trigger.textContent = "Failed"; await new Promise((r) => setTimeout(r, 1800)); trigger.textContent = original; }
      }));
    });
  }
  // close an open "More" menu when tapping elsewhere
  document.addEventListener("click", (e) => $$("details.more[open]").forEach((d) => { if (!d.contains(e.target)) d.open = false; }));

  if (rf) {
    const linkEl = $("#id_link"), status = $("#fetchStatus"), results = $("#results");
    const fitSlot = $("#fitSlot"), cardsSlot = $("#cardsSlot"), idle = $("#idleMsg"), prog = $("#progress"), err = $("#runError");
    const force = $("#id_force"), textEl = $("#id_post_text");
    if (force) force.value = "False"; // never carry "reply anyway" into the next request
    const LINK = /(?:x|twitter)\.com\/\w{1,15}\/status\/\d{5,}/i;
    let running = false, lastLink = "", lastAnalysis = null, overrideLink = null;

    const order = ["read", "fit", "write"];
    function showProgress(stage) {
      prog.hidden = false;
      const at = order.indexOf(stage);
      $$("li", prog).forEach((li, i) => { li.className = i < at ? "done" : i === at ? "active" : ""; });
    }
    const hideProgress = () => { prog.hidden = true; };
    function showError(msg) { err.textContent = msg; err.hidden = false; hideProgress(); }

    function showPreview(d) {
      const bits = [d.likes != null && d.likes + " likes", d.replies != null && d.replies + " replies"].filter(Boolean).join(" · ");
      $("#pvAuthor").textContent = d.author;
      $("#pvMeta").textContent = bits ? " " + bits : "";
      $("#pvText").textContent = d.text.length > 300 ? d.text.slice(0, 300) + "..." : d.text;
      $("#preview").hidden = false;
      $("#id_post_meta").value = bits;
    }

    // Read the post: fetch from the link unless we already have this link's text
    async function readPost() {
      const link = linkEl.value.trim();
      if (link && (link !== lastLink || !textEl.value.trim())) {
        status.textContent = "Fetching the post...";
        try {
          const body = new FormData(); body.append("link", link);
          const d = await (await post(rf.dataset.fetchUrl, body)).json();
          if (d.ok) {
            textEl.value = d.text; $("#id_author").value = d.author; $("#id_author_bio").value = d.author_bio;
            showPreview(d); lastLink = link;
            status.textContent = "Got the post.";
          } else {
            status.textContent = d.error;
            if (!textEl.value.trim()) { $("#customize").open = true; $("#postEdit").open = true; return false; }
          }
        } catch (e) {
          status.textContent = "Could not fetch. Paste the post text under Customize.";
          if (!textEl.value.trim()) { $("#customize").open = true; $("#postEdit").open = true; return false; }
        }
      }
      if (!textEl.value.trim()) { status.textContent = "Paste a post link first."; return false; }
      return true;
    }

    async function writeStep(a, override) {
      showProgress("write");
      const w = await postJSON(rf.dataset.writeUrl, { ctx: a.ctx, settings: a.settings, analysis: a.analysis, override: !!override });
      if (!w.ok) { showError(w.error); return; }
      cardsSlot.innerHTML = w.html; initCards(cardsSlot); hideProgress();
      // phones: bring the verdict and the first reply into view together, no hunting
      if (mobile()) setTimeout(() => (fitSlot.firstElementChild || cardsSlot).scrollIntoView({ behavior: "smooth", block: "start" }), 60);
    }

    function bindForce() {
      const fb = $("#forceBtn", fitSlot);
      if (fb) fb.addEventListener("click", async () => {
        if (running) return;
        fb.closest(".nothing").hidden = true;
        overrideLink = linkEl.value.trim() || "(text)";
        if (lastAnalysis) {
          running = true; err.hidden = true;
          try { await writeStep(lastAnalysis, true); } catch (e) { showError("Something went wrong. Try again."); }
          running = false;
        } else { run({ override: true }); }
      });
    }

    async function run(opts) {
      // once you chose "Reply anyway" for a post, later rewrites of that same post keep that choice
      const same = overrideLink !== null && (linkEl.value.trim() || "(text)") === overrideLink;
      const override = !!(opts && opts.override) || same;
      if (override) overrideLink = linkEl.value.trim() || "(text)";
      if (running) return;
      running = true; err.hidden = true; idle.hidden = true; fitSlot.innerHTML = ""; cardsSlot.innerHTML = ""; lastAnalysis = null;
      if (mobile()) results.scrollIntoView({ behavior: "smooth", block: "start" });
      try {
        showProgress("read");
        if (!(await readPost())) { hideProgress(); idle.hidden = false; return; }
        showProgress("fit");
        const a = await (await post(rf.dataset.analyseUrl, new FormData(rf))).json();
        if (!a.ok) { showError(a.error); return; }
        lastAnalysis = a; fitSlot.innerHTML = a.html; bindForce();
        if (a.verdict === "skip" && !override) { hideProgress(); return; }
        await writeStep(a, override);
      } catch (e) { showError("Something went wrong. Check your connection and try again."); }
      finally { running = false; }
    }

    // triggers: paste into the box, tap the clipboard icon, Go, Enter, or the customize button
    linkEl.addEventListener("paste", () => setTimeout(() => { if (LINK.test(linkEl.value)) run(); }, 0));
    linkEl.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); run(); } });
    $("#goBtn").addEventListener("click", () => run());
    rf.addEventListener("submit", (e) => { e.preventDefault(); run(); });
    const pasteBtn = $("#pasteBtn");
    if (pasteBtn) pasteBtn.addEventListener("click", async () => {
      try {
        const text = (await navigator.clipboard.readText()).trim();
        if (!text) { status.textContent = "Your clipboard is empty."; return; }
        linkEl.value = text; run();
      } catch (e) { status.textContent = "Could not read the clipboard. Click the box and paste with Ctrl+V or long-press."; }
    });

    // page loaded with server-rendered results (JavaScript fallback path)
    initCards(document);
    bindForce();
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