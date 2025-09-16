// ======= State =======
let currentSteps = null;
let currentStepsRight = null;
let currentKind  = "text";
let currentAttr  = null;
let currentDirection = "left_to_right"; 
let currentIssueKind = "gibberish";
let allCleared = false; // set true after Apply confirms no remaining issues
let clearedKinds = new Set(); // track individually cleared kinds

let programmaticScroll = false;
function withProgrammaticScroll(fn, unlockDelay = 160) {
  programmaticScroll = true;
  try { fn(); } finally { setTimeout(() => { programmaticScroll = false; }, unlockDelay); }
}

function computeCenterScrollTop(paneEl, anchorEl) {
  const paneRect = paneEl.getBoundingClientRect();
  const aRect    = anchorEl.getBoundingClientRect();
  const current  = paneEl.scrollTop;
  const delta    = (aRect.top + aRect.height / 2) - (paneRect.top + paneRect.height / 2);
  const target   = current + delta;
  return Math.max(0, Math.min(target, paneEl.scrollHeight - paneEl.clientHeight));
}

function jumpToAnchor(paneEl) {
  if (!paneEl) return;
  const anchor =
    paneEl.querySelector("#focusAnchor") ||
    paneEl.querySelector(".focusTarget") ||
    paneEl.querySelector(".editNewInline") ||
    paneEl.querySelector(".editOldInline");
  if (!anchor) return;

  if (!anchor.hasAttribute("tabindex")) anchor.setAttribute("tabindex", "-1");

  let attempts = 0;
  const MAX_ATTEMPTS = 6;

  const tryCenter = () => {
    attempts += 1;
    paneEl.classList.add("no-smooth");
    const target = computeCenterScrollTop(paneEl, anchor);
    withProgrammaticScroll(() => { paneEl.scrollTop = target; });
    setTimeout(() => paneEl.classList.remove("no-smooth"), 60);

    const paneMid   = paneEl.getBoundingClientRect().top + paneEl.clientHeight / 2;
    const aRect     = anchor.getBoundingClientRect();
    const anchorMid = aRect.top + aRect.height / 2;
    const error     = Math.abs(anchorMid - paneMid);

    if (error > 6 && attempts < MAX_ATTEMPTS) requestAnimationFrame(tryCenter);
    else { try { anchor.focus({ preventScroll: true }); } catch {} }
  };

  requestAnimationFrame(() => requestAnimationFrame(tryCenter));
}

// ======= Render current item =======
async function loadCurrent() {
  const issueType = document.getElementById("issueType").value || "gibberish";
  const r = await fetch(`/render?type=${encodeURIComponent(issueType)}`);
  if (!r.ok) {
    const t = await r.text();
    alert("Render failed: " + t);
    return;
  }
  const data = await r.json();
  if (data) console.log("RENDER →", data);

  const leftPane  = document.getElementById("leftPane");
  const rightPane = document.getElementById("rightPane");

  if ((data.count || 0) === 0) {
    leftPane.textContent = "";
    rightPane.textContent = "";
    document.getElementById("pos").textContent = "0/0";
    currentSteps = null;
    currentStepsRight = null;
    currentKind  = "text";
    currentAttr  = null;
    currentIssueKind = "gibberish";
    currentDirection = "left_to_right";
    return;
  }

  // render panes
  leftPane.innerHTML  = data.left  || "";
  rightPane.innerHTML = data.right || "";
  document.getElementById("pos").textContent = `${data.pos}/${data.count}`;

  // store state (render-kind vs issue-kind)
  currentSteps       = data.steps || null;
  currentStepsRight  = data.steps_right || null;
  currentKind        = data.kind || "text";                 // "text" | "attr" (for rendering)
  currentIssueKind   = data.issue_kind || "gibberish";      // real kind: "duplicate" | "gibberish" | "footnote"
  currentAttr        = data.attr || null;

  // Always copy RIGHT → LEFT for this acceptance
  currentDirection = "right_to_left";

  // focus
  jumpToAnchor(leftPane);
  jumpToAnchor(rightPane);
}

// ======= Helpers =======
function prettyKind(k) {
  return k === "footnote" ? "footnote attrs"
       : k === "duplicate" ? "duplicate"
       : k === "gibberish" ? "gibberish" : "any";
}
function haveBothFiles() {
  const formEl = document.getElementById("uploadForm");
  const a = formEl.querySelector('input[name="original"]')?.files?.[0];
  const b = formEl.querySelector('input[name="modified"]')?.files?.[0];
  return !!(a && b);
}

// Run /diff with only=<kind>, then render
async function runDiff(kind) {
  if (allCleared) {
    // Post-apply: treat everything as clean; just clear panes
    document.getElementById("leftPane").textContent  = "";
    document.getElementById("rightPane").textContent = "";
    document.getElementById("pos").textContent = "0/0";
    currentSteps = null; currentStepsRight = null; currentKind = "text"; currentAttr = null;
    return;
  }
  if (!haveBothFiles()) {
    // No files chosen → try server state via /render to report if nothing remains
    try {
      const r = await fetch(`/render?type=${encodeURIComponent(kind || "gibberish")}`);
      const data = r.ok ? await r.json() : null;
      if (!data || (data.count || 0) === 0) {
        alert(`No ${prettyKind(kind)} issues found.`);
      }
    } catch {}
    document.getElementById("leftPane").textContent  = "";
    document.getElementById("rightPane").textContent = "";
    document.getElementById("pos").textContent = "0/0";
    currentSteps = null; currentStepsRight = null; currentKind = "text"; currentAttr = null;
    return;
  }
  const formEl = document.getElementById("uploadForm");
  const form = new FormData(formEl);
  if (kind && kind !== "all") form.append("only", kind);

  const res = await fetch("/diff", { method: "POST", body: form });
  if (!res.ok) {
    const msg = await res.text();
    alert("Compare failed: " + msg);
    return;
  }
  const info = await res.json();
  if (info) console.log("DIFF →", info);

  if ((info.count || 0) === 0) {
    // Quietly clear when this type has nothing remaining
    document.getElementById("leftPane").textContent  = "";
    document.getElementById("rightPane").textContent = "";
    document.getElementById("pos").textContent = "0/0";
    currentSteps = null; currentStepsRight = null; currentKind = "text"; currentAttr = null;
    return;
  }

  document.getElementById("issueType").value = kind || "all";
  await loadCurrent();
}

// ======= UI wiring =======
document.getElementById("issueType").addEventListener("change", async (e) => {
  const v = e.target.value;
  if (clearedKinds.has(v)) {
    document.getElementById("leftPane").textContent  = "";
    document.getElementById("rightPane").textContent = "";
    document.getElementById("pos").textContent = "0/0";
    currentSteps = null; currentStepsRight = null; currentKind = "text"; currentAttr = null;
    return;
  }
  if (allCleared) {
    document.getElementById("leftPane").textContent  = "";
    document.getElementById("rightPane").textContent = "";
    document.getElementById("pos").textContent = "0/0";
    currentSteps = null; currentStepsRight = null; currentKind = "text"; currentAttr = null;
    return;
  }
  if (!v) {
    document.getElementById("leftPane").textContent  = "";
    document.getElementById("rightPane").textContent = "";
    document.getElementById("pos").textContent = "0/0";
    currentSteps = null; currentStepsRight = null; currentKind = "text"; currentAttr = null;
    return;
  }
  await runDiff(v);
});

// ======= Navigation & actions =======
document.getElementById("nextBtn").onclick = async () => {
  await fetch("/navigate", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ dir: "next" }) });
  await loadCurrent();
};

document.getElementById("prevBtn").onclick = async () => {
  await fetch("/navigate", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ dir: "prev" }) });
  await loadCurrent();
};

// Accept click
document.getElementById("acceptBtn").onclick = async () => {
  if (!currentSteps) return;
  await fetch("/accept", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      steps: currentSteps,
      steps_right: currentStepsRight,
      kind: currentIssueKind,          // 👈 use REAL issue kind
      attr: currentAttr,
      direction: currentDirection
    })
  });
  await loadCurrent(); // count/pos will drop now
};

document.getElementById("rejectBtn").onclick = async () => {
  await fetch("/reject", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({}) });
  await fetch("/navigate", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ dir: "next" }) });
  await loadCurrent();
};

document.getElementById("applyBtn").onclick = async () => {
  const r = await fetch("/apply", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({})
  });
  if (!r.ok) {
    alert("Apply failed");
    return;
  }
  const data = await r.json();

  // 🔁 Even if nothing *new* applied now, still allow download
  if (!data.download_left && !data.download_right) {
    alert("No output produced.");
    return;
  }

  if (data.applied_left === 0 && data.applied_right === 0 && data.note === "already_applied_only") {
    // Optional soft info, no blocking
    console.info("Nothing new to apply; downloading current buffers.");
  }

  // Do not auto-download; just update state. User can click Download.

  // After applying, check current filter vs all
  try {
    const s = await fetch("/stats");
    if (s.ok) {
      const sj = await s.json();
      // mark current filter cleared if it now has zero
      const issueType = document.getElementById("issueType").value || "gibberish";
      const countForKind = issueType === "all" ? (sj.total || 0) : ((sj.byKind || {})[issueType] || 0);
      if (countForKind === 0 && issueType !== "all") clearedKinds.add(issueType);

      if ((sj.total || 0) === 0) {
        alert("No errors found");
        allCleared = true;
      }
    }
  } catch {}
};

// Separate Download button
document.getElementById("downloadBtn").onclick = async () => {
  const r = await fetch("/download/prepare", { method: "POST" });
  if (!r.ok) { alert("Download prep failed"); return; }
  const data = await r.json();
  if (data.download_left)  window.open(data.download_left,  "_blank");
  if (data.download_right) window.open(data.download_right, "_blank");
};
// ======= Splitter =======
(function splitterInit(){
  const panes    = document.getElementById("panes");
  const leftWrap = document.getElementById("leftWrap");
  const rightWrap= document.getElementById("rightWrap");
  const splitter = document.getElementById("splitter");

  let dragging = false, startX = 0, startLeftWidth = 0;
  function pct(total, px) { return Math.max(20, Math.min(80, (px / total) * 100)); }

  splitter.addEventListener("mousedown", (e) => {
    dragging = true; startX = e.clientX;
    startLeftWidth = leftWrap.getBoundingClientRect().width;
    document.body.classList.add("resizing"); e.preventDefault();
  });
  window.addEventListener("mousemove", (e) => {
    if (!dragging) return;
    const dx = e.clientX - startX, total = panes.getBoundingClientRect().width;
    const leftPct = pct(total, startLeftWidth + dx);
    leftWrap.style.flexBasis = leftPct + "%";
    rightWrap.style.flexBasis = (100 - leftPct) + "%";
    splitter.setAttribute("aria-valuenow", Math.round(leftPct));
  });
  window.addEventListener("mouseup", () => {
    if (!dragging) return;
    dragging = false; document.body.classList.remove("resizing");
  });

  splitter.addEventListener("keydown", (e) => {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    const total = panes.getBoundingClientRect().width;
    const leftRect = leftWrap.getBoundingClientRect();
    let leftPct = Math.max(20, Math.min(80, (leftRect.width / total) * 100));
    leftPct += (e.key === "ArrowRight" ? 2 : -2);
    leftPct = Math.max(20, Math.min(80, leftPct));
    leftWrap.style.flexBasis = leftPct + "%";
    rightWrap.style.flexBasis = (100 - leftPct) + "%";
    splitter.setAttribute("aria-valuenow", Math.round(leftPct));
    e.preventDefault();
  });
})();

// ======= Instant preview =======
(function instantPreview(){
  const formEl = document.getElementById("uploadForm");
  if (!formEl) return;

  const leftPane  = document.getElementById("leftPane");
  const rightPane = document.getElementById("rightPane");

  const originalInput = formEl.querySelector('input[name="original"]');
  const modifiedInput = formEl.querySelector('input[name="modified"]');

  function previewFile(inputEl, paneEl) {
    if (!inputEl || !paneEl || !inputEl.files || !inputEl.files[0]) return;
    const file = inputEl.files[0];
    const reader = new FileReader();
    reader.onload = () => {
      paneEl.textContent = reader.result || "";
      document.getElementById("pos").textContent = "—/—";
    };
    reader.readAsText(file);
  }

  originalInput?.addEventListener("change", () => previewFile(originalInput, leftPane));
  modifiedInput?.addEventListener("change", () => previewFile(modifiedInput, rightPane));
})();
