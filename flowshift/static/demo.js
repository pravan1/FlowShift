"use strict";
const el = id => document.getElementById(id);
let challenge = null;
let csrf = null;

async function api(path, body, csrfToken) {
  const headers = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (csrfToken) headers["X-CSRF-Token"] = csrfToken;
  const response = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers, credentials: "same-origin",
    body: body === undefined ? undefined : JSON.stringify(body)
  });
  if (!response.ok) {
    throw new Error(response.status === 403
      ? "Access unavailable or expired. Review consent to enter again."
      : "Request failed. Check the local server and retry.");
  }
  return response.json();
}

function clearContext(message) {
  el("account-panel").hidden = true;
  el("balances").replaceChildren();
  el("transactions").replaceChildren();
  el("account").replaceChildren();
  csrf = null;
  el("context").replaceChildren();
  el("context").hidden = true;
  el("controls").hidden = true;
  el("status").textContent = message;
}

async function loadContext() {
  clearContext("Checking your session…");
  try {
    const context = await api("/api/session");
    csrf = context.csrf_token;
    const fields = {
      Environment: context.environment.toUpperCase(), Institution: context.institution_id,
      Business: context.business_id, Account: context.account_id,
      Identity: context.actor_id, Role: context.role,
      Permissions: context.permissions.join(", "),
      "Expires at": new Date(context.expires_at * 1000).toLocaleString()
    };
    for (const [key, value] of Object.entries(fields)) {
      const dt = document.createElement("dt"); dt.textContent = key;
      const dd = document.createElement("dd"); dd.textContent = value;
      el("context").append(dt, dd);
    }
    el("context").hidden = false;
    el("controls").hidden = false;
    el("status").textContent = "Authorized synthetic account context. No bank data connected.";
    await loadAccounts();
  } catch (error) { clearContext(error.message); }
}

async function busy(action) {
  const buttons = [...document.querySelectorAll("button")];
  buttons.forEach(button => { button.disabled = true; });
  try { await action(); }
  catch (error) { clearContext(error.message); }
  finally { buttons.forEach(button => { button.disabled = false; }); }
}

el("start").addEventListener("click", () => busy(async () => {
  clearContext("Preparing consent…");
  el("consent").hidden = true;
  const result = await api("/api/demo/start", {profile: el("profile").value});
  challenge = result.challenge;
  el("selection").textContent = el("profile").selectedOptions[0].textContent;
  el("consent").hidden = false;
  el("status").textContent = "Access requires your explicit consent.";
  el("grant").focus();
}));
for (const decision of ["grant", "deny"]) {
  el(decision).addEventListener("click", () => busy(async () => {
    const result = await api("/api/demo/consent", {challenge, decision});
    challenge = null; el("consent").hidden = true;
    if (result.status === "granted") await loadContext();
    else clearContext("Consent denied. No account access was granted.");
  }));
}
el("refresh").addEventListener("click", () => busy(loadContext));

function showData(data) {
  el("balances").replaceChildren(); el("transactions").replaceChildren();
  el("sync-status").textContent = `${data.sync_status}${data.stale ? " · STALE" : ""}. ${data.error || ""}`;
  if (!data.snapshot) {
    el("sync-status").textContent += " No complete snapshot available. Synchronize to load data.";
    return;
  }
  const money = cents => new Intl.NumberFormat("en-US", {style:"currency",currency:"USD"}).format(cents/100);
  const s = data.snapshot;
  const fields = {"Mode":"SYNTHETIC", "Version":data.version, "Currency":s.currency,
    "Available balance":money(s.available_cents), "Ledger balance":money(s.ledger_cents),
    "Holds (already reflected in available)":money(s.holds_cents),
    "Balance as of":new Date(s.as_of*1000).toISOString(), "Balance type":s.balance_type};
  for (const [label,value] of Object.entries(fields)) {
    const dt = document.createElement("dt"); dt.textContent=label;
    const dd = document.createElement("dd"); dd.textContent=value;
    el("balances").append(dt,dd);
  }
  if (!data.events.length) {
    const li=document.createElement("li"); li.textContent="No transactions in this complete synthetic history.";
    el("transactions").append(li);
  }
  for (const event of data.events) {
    const li=document.createElement("li");
    li.textContent=`${event.description} · ${money(event.amount_cents)} · ${event.status} · ${new Date(event.effective_at*1000).toISOString()}`;
    el("transactions").append(li);
  }
}
async function loadAccounts() {
  const result = await api("/api/accounts");
  for (const account of result.accounts) {
    const option=document.createElement("option"); option.value=account.account_id;
    option.textContent=`${account.account_id} · ${account.currency}`;
    el("account").append(option);
  }
  el("account-panel").hidden=false;
  if (!result.accounts.length) { el("sync-status").textContent="No authorized accounts."; return; }
  showData(await api(`/api/accounts/${encodeURIComponent(el("account").value)}/data`));
}
el("sync").addEventListener("click", () => busy(async () => {
  el("sync-status").textContent="Synchronizing…";
  showData(await api(`/api/accounts/${encodeURIComponent(el("account").value)}/sync`,
    {scenario:el("scenario").value},csrf));
}));
for (const [id, path, message] of [
  ["logout", "logout", "Logged out. Review consent to enter again."],
  ["revoke", "revoke-consent", "Consent revoked. Further account access is blocked."]
]) {
  el(id).addEventListener("click", () => busy(async () => {
    await api(`/api/session/${path}`, {}, csrf);
    clearContext(message);
  }));
}
el("profile").addEventListener("change", () => { challenge = null; el("consent").hidden = true; });
(async () => {
  try {
    const result = await api("/api/demo/profiles");
    el("profile").replaceChildren();
    for (const profile of result.profiles) {
      const option = document.createElement("option");
      option.value = profile.id;
      option.textContent = `${profile.business_id} / ${profile.account_id} — ${profile.role}`;
      el("profile").append(option);
    }
    el("profile").disabled = false; el("start").disabled = false;
    await loadContext();
  } catch (error) { clearContext(error.message); }
})();
