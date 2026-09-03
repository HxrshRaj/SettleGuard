"use strict";

const state = { rows: [], status: "open", type: "all", triageAvailable: false };

const $ = (sel) => document.querySelector(sel);
const inr = (x) => (x === null || x === undefined) ? "—" : "₹" + Number(x).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

function setStatus(msg, kind) {
  const el = $("#statusbar");
  el.textContent = msg;
  el.className = "statusbar " + (kind || "");
}

async function api(path, opts) {
  const res = await fetch(path, opts);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error || (res.status + " " + res.statusText));
  return body;
}

async function loadConfig() {
  const cfg = await api("/api/config");
  state.triageAvailable = cfg.triage_available;
  $("#config-dump").textContent = JSON.stringify(cfg.rules, null, 2);
  if (!cfg.triage_available) {
    setStatus("Triage layer offline: GEMINI_API_KEY is not set. Reconciliation still works.", "info");
  }
}

async function loadRows() {
  const q = state.status === "all" ? "?all=1" : "";
  state.rows = await api("/api/discrepancies" + q);
  renderTypeFilter();
  renderSummary();
  renderTable();
}

function renderSummary() {
  const s = { total: state.rows.length, open: 0, resolved: 0, triaged: 0 };
  for (const r of state.rows) {
    r.resolved ? s.resolved++ : s.open++;
    if (r.triage_root_cause) s.triaged++;
  }
  const cards = [
    ["Discrepancies", s.total],
    ["Open", s.open],
    ["Resolved", s.resolved],
    ["AI-triaged", s.triaged],
  ];
  $("#summary-cards").innerHTML = cards
    .map(([l, n]) => `<div class="card"><div class="n">${n}</div><div class="l">${l}</div></div>`)
    .join("");
}

function renderTypeFilter() {
  const types = [...new Set(state.rows.map((r) => r.type))].sort();
  const el = $("#filter-type");
  el.innerHTML =
    `<button data-type="all" class="${state.type === "all" ? "active" : ""}">All types</button>` +
    types
      .map(
        (t) =>
          `<button data-type="${t}" class="${state.type === t ? "active" : ""}">${t.replace(/_/g, " ")}</button>`
      )
      .join("");
}

function visibleRows() {
  return state.rows.filter((r) => {
    if (state.status === "open" && r.resolved) return false;
    if (state.status === "resolved" && !r.resolved) return false;
    if (state.type !== "all" && r.type !== state.type) return false;
    return true;
  });
}

function triageCell(r) {
  if (!r.triage_root_cause) {
    return `<div class="triage"><span class="pending">${
      state.triageAvailable ? "triage pending — click Run AI triage" : "triage pending — set GEMINI_API_KEY"
    }</span></div>`;
  }
  return `<div class="triage">
    <div class="rc">${esc(r.triage_root_cause)}</div>
    <div class="na">${esc(r.triage_next_action || "")}</div>
    <div class="model">${esc(r.triage_model || "")} · ${esc((r.triage_generated_at || "").replace("T", " ").replace("Z", ""))}</div>
  </div>`;
}

function sevBadge(r) {
  const sev = r.triage_severity || r.severity_hint;
  const cls = r.triage_severity ? "sev-" + r.triage_severity : "sev-none";
  const label = r.triage_severity ? sev : sev + "?";
  return `<span class="badge ${cls}">${label}</span>`;
}

function renderTable() {
  const rows = visibleRows();
  $("#empty").hidden = rows.length > 0;
  $("#disc-body").innerHTML = rows
    .map((r) => {
      const deltaCls = (r.amount_delta || 0) >= 0 ? "delta-pos" : "delta-neg";
      const statusCell = r.resolved
        ? `<span class="status-resolved">resolved</span>
           <div class="res-notes">${esc(r.resolution_notes || "")}</div>
           <button class="link" data-reopen="${esc(r.discrepancy_id)}">reopen</button>`
        : `<span class="status-open">open</span><br>
           <button class="link" data-resolve="${esc(r.discrepancy_id)}">resolve…</button>`;
      return `<tr data-id="${esc(r.discrepancy_id)}">
        <td><span class="badge t-${r.type}">${r.type.replace(/_/g, " ")}</span></td>
        <td><strong>${esc(r.txn_id)}</strong><div class="merchant">${esc(r.merchant_name || "—")}</div>
            <div class="detail">${esc(r.detail || "")}</div></td>
        <td class="num">${inr(r.platform_amount)}</td>
        <td class="num">${inr(r.bank_amount)}</td>
        <td class="num ${deltaCls}">${r.amount_delta === null ? "—" : (r.amount_delta >= 0 ? "+" : "") + inr(r.amount_delta)}
            ${r.delay_hours ? `<div class="merchant">${r.delay_hours.toFixed(1)}h late</div>` : ""}</td>
        <td>${sevBadge(r)}</td>
        <td>${triageCell(r)}</td>
        <td>${statusCell}</td>
      </tr>`;
    })
    .join("");
}

function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// ---- interactions -------------------------------------------------------- //
$("#btn-reconcile").addEventListener("click", async (e) => {
  e.target.disabled = true;
  setStatus("Reconciling against config/rules.yaml…", "info");
  try {
    const r = await api("/api/reconcile", { method: "POST" });
    let msg = `Reconciled: ${r.found} discrepancies (${r.new} new, ${r.updated} updated, ${r.deactivated} cleared). `;
    msg += r.triage_available ? `AI-triaged ${r.triaged}.` : "Triage offline (no GEMINI_API_KEY).";
    if (r.triage_errors && r.triage_errors.length) msg += ` ${r.triage_errors.length} triage error(s).`;
    setStatus(msg, r.triage_errors && r.triage_errors.length ? "err" : "ok");
    await loadConfig();
    await loadRows();
  } catch (err) {
    setStatus("Reconcile failed: " + err.message, "err");
  } finally {
    e.target.disabled = false;
  }
});

$("#btn-triage").addEventListener("click", async (e) => {
  e.target.disabled = true;
  setStatus("Calling Gemini for pending discrepancies…", "info");
  try {
    const r = await api("/api/triage", { method: "POST" });
    let msg = `Triaged ${r.triaged}/${r.pending} pending.`;
    if (r.errors && r.errors.length) msg += ` Errors: ${r.errors.map((x) => x.error).join("; ")}`;
    setStatus(msg, r.errors && r.errors.length ? "err" : "ok");
    await loadRows();
  } catch (err) {
    setStatus("Triage failed: " + err.message, "err");
  } finally {
    e.target.disabled = false;
  }
});

$("#filter-status").addEventListener("click", (e) => {
  if (e.target.tagName !== "BUTTON") return;
  state.status = e.target.dataset.status;
  [...e.currentTarget.children].forEach((b) => b.classList.toggle("active", b === e.target));
  loadRows();
});

$("#filter-type").addEventListener("click", (e) => {
  if (e.target.tagName !== "BUTTON") return;
  state.type = e.target.dataset.type;
  renderTypeFilter();
  renderTable();
});

$("#disc-body").addEventListener("click", (e) => {
  const resolveId = e.target.dataset.resolve;
  const reopenId = e.target.dataset.reopen;
  if (resolveId) return openResolveBox(resolveId, e.target);
  if (reopenId) return doReopen(reopenId);
});

function openResolveBox(id, btn) {
  if (btn.parentElement.querySelector(".resolve-box")) return;
  const box = document.createElement("div");
  box.className = "resolve-box";
  box.innerHTML = `<textarea placeholder="What did you find / do to resolve this?"></textarea>
    <div class="row"><button class="save">Save resolution</button>
    <button class="link cancel">cancel</button></div>`;
  btn.parentElement.appendChild(box);
  box.querySelector("textarea").focus();
  box.querySelector(".cancel").onclick = () => box.remove();
  box.querySelector(".save").onclick = async () => {
    const notes = box.querySelector("textarea").value.trim();
    if (!notes) { box.querySelector("textarea").focus(); return; }
    try {
      await api("/api/resolve/" + encodeURIComponent(id), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ notes }),
      });
      setStatus("Marked resolved: " + id, "ok");
      await loadRows();
    } catch (err) {
      setStatus("Resolve failed: " + err.message, "err");
    }
  };
}

async function doReopen(id) {
  try {
    await api("/api/reopen/" + encodeURIComponent(id), { method: "POST" });
    setStatus("Reopened: " + id, "info");
    await loadRows();
  } catch (err) {
    setStatus("Reopen failed: " + err.message, "err");
  }
}

// ---- boot -------------------------------------------------------------- //
(async () => {
  try {
    await loadConfig();
    await loadRows();
    if (state.rows.length === 0) {
      setStatus("No discrepancies yet — click “Re-run reconciliation” to run the engine.", "info");
    }
  } catch (err) {
    setStatus("Failed to load: " + err.message, "err");
  }
})();
