const API = "";
const $ = id => document.getElementById(id);
const fmt = (value, digits = 2) => value === null || value === undefined || Number.isNaN(Number(value)) ? "—" : Number(value).toLocaleString("en-IN", {maximumFractionDigits: digits, minimumFractionDigits: digits});
const money = value => value === null || value === undefined ? "—" : `${value < 0 ? "−" : "+"}₹${fmt(Math.abs(value), 0)}`;
const signed = value => value === null || value === undefined ? "—" : `${value < 0 ? "−" : "+"}${fmt(Math.abs(value), 1)}`;
async function api(path, options) { const response = await fetch(API + path, options); const data = await response.json().catch(() => ({})); if (!response.ok) throw new Error(data.detail || "Request failed"); return data; }
let optionChain = [];
let activeTab = "LIVE";
function selectTab(tab) {
  activeTab = tab;
  const isHistory = tab === "HISTORY";
  $("trading-workspace").hidden = isHistory;
  $("history-panel").classList.toggle("active", isHistory);
  document.body.classList.toggle("paper-mode", tab === "PAPER");
  document.querySelectorAll(".primary-tab").forEach(button => button.classList.toggle("active", button.dataset.tab === tab));
  if (!isHistory) {
    $("mode-select").value = tab;
    $("workspace-eyebrow").textContent = `01 / ${tab} POSITION`;
    $("workspace-heading").textContent = tab === "PAPER" ? "Simulate four legs with live Fyers prices" : "Enter your four legs";
    refresh();
  } else refreshHistory();
}
document.querySelectorAll(".primary-tab").forEach(button => button.addEventListener("click", () => selectTab(button.dataset.tab)));
function setChainMessage(message) { document.querySelectorAll('[data-field="strike"]').forEach(select => { select.innerHTML = `<option value="">${message}</option>`; }); }
function clearLegSelections() { document.querySelectorAll('[data-field="symbol"]').forEach(input => { input.value = ""; }); document.querySelectorAll('[data-field="entry_price"]').forEach(input => { input.value = ""; }); setChainMessage("Select an expiry first"); }
function populateChain(chain) {
	optionChain = chain.quotes.filter(quote => quote.symbol).sort((left, right) => left.strike - right.strike);
	document.querySelectorAll('[data-field="strike"]').forEach(select => {
		const type = select.dataset.optionType;
		const quotes = optionChain.filter(quote => quote.option_type === type);
		select.innerHTML = `<option value="">Select ${type === "CE" ? "call" : "put"} strike / price</option>` + quotes.map(quote => `<option value="${quote.strike}">${fmt(quote.strike, 0)} ${type} | LTP ₹${fmt(quote.ltp)}</option>`).join("");
	});
}
async function loadChain(expiry) {
	clearLegSelections();
	if (!expiry) return;
	setChainMessage("Loading option chain...");
	try { populateChain(await api(`/chain?expiry=${encodeURIComponent(expiry)}`)); }
	catch (error) { setChainMessage("Unable to load option chain. Please try again."); $("form-message").textContent = error.message; }
}
async function loadExpiries() {
	try {
		const data = await api("/expiries");
		$("expiry").innerHTML = '<option value="">Select expiry</option>' + data.expiries.map(expiry => `<option value="${expiry.value}">${expiry.label}</option>`).join("");
	} catch (error) { $("expiry").innerHTML = '<option value="">Unable to load expiries</option>'; $("form-message").textContent = error.message; setChainMessage("Unable to load option chain. Please try again."); }
}
$("expiry").addEventListener("change", event => loadChain(event.target.value));
document.querySelectorAll('[data-field="strike"]').forEach(select => select.addEventListener("change", event => {
	const quote = optionChain.find(item => item.option_type === select.dataset.optionType && String(item.strike) === event.target.value);
	const leg = event.target.dataset.leg;
	if (!quote) return;
	document.querySelector(`[data-leg="${leg}"][data-field="symbol"]`).value = quote.symbol;
	document.querySelector(`[data-leg="${leg}"][data-field="entry_price"]`).value = quote.ltp;
}));
$("connect-fyers").addEventListener("click", async () => { try { const result = await api("/fyers/login-url"); window.location.href = result.url; } catch (error) { $("alerts").innerHTML = `<li>⚠ Could not start FYERS login: ${error.message}</li>`; } });
function collectLegs() { return ["CALL BUY", "CALL SELL", "PUT SELL", "PUT BUY"].map(name => { const values = {leg: name}; document.querySelectorAll(`[data-leg="${name}"]`).forEach(input => { values[input.dataset.field] = ["number", "select-one"].includes(input.type) ? Number(input.value) : input.value.trim(); }); return values; }); }
$("setup-form").addEventListener("submit", async event => { event.preventDefault(); $("form-message").textContent = ""; try { const result = await api("/manual-monitor/setup", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({expiry: $("expiry").value, mode: $("mode-select").value, slippage_points: Number($("slippage-points").value || 0.05), call_sl: $("call-sl").value ? Number($("call-sl").value) : null, put_sl: $("put-sl").value ? Number($("put-sl").value) : null, legs: collectLegs()})}); $("form-message").textContent = `${result.mode} position active: ${result.position_id}`; await refresh(); await loadHistory(); } catch (error) { $("form-message").textContent = error.message; } });
$("clear-position").addEventListener("click", async () => { if (!window.confirm("Stop monitoring the active position? Its history will be kept.")) return; await api(`/manual-monitor/setup?mode=${activeTab}`, {method: "DELETE"}); await refresh(); await loadHistory(); });
$("close-position").addEventListener("click", async () => { await api(`/manual-monitor/close?reason=MANUALLY+CLOSED&mode=${activeTab}`); await refresh(); await loadHistory(); });
function setConnection(state) { const el = $("connection"); const connectButton = $("connect-fyers"); const label = state === "LIVE" ? "● LIVE" : state === "RECONNECTING" ? "● RECONNECTING" : "● DISCONNECTED"; el.textContent = label; el.className = `connection ${state.toLowerCase()}`; connectButton.textContent = state === "LIVE" ? "FYERS CONNECTED" : "Connect FYERS"; connectButton.disabled = state === "LIVE"; }
function renderLegs(legs) { $("legs-body").innerHTML = legs.map(leg => `<tr><td>${leg.leg}</td><td>${fmt(leg.strike, 0)}</td><td>₹${fmt(leg.entry_price)}</td><td>${leg.ltp === null ? "—" : `₹${fmt(leg.ltp)}`}</td><td>${fmt(leg.quantity, 0)}</td><td>${leg.bid == null ? "—" : `${fmt(leg.bid)} / ${fmt(leg.ask)}`}</td><td class="${leg.pnl > 0 ? "pos" : leg.pnl < 0 ? "neg" : ""}">${money(leg.pnl)}</td></tr>`).join(""); }
function renderHistory(list) { const target = $("history-list"); if (!Array.isArray(list) || !list.length) { target.innerHTML = '<div class="subtle">No prior positions yet.</div>'; return; } target.innerHTML = list.map(item => `
    <div class="history-item">
      <div class="row"><strong>${item.position_id || "Position"}</strong><span class="mode-pill ${item.mode && item.mode.toLowerCase() === "paper" ? "paper" : "market"}">${item.mode || "MARKET"}</span></div>
      <div class="row"><span class="history-label">${item.expiry || "—"}</span><span>${item.status || "OPEN"}</span></div>
      <div class="row"><span>Entry</span><span>${item.opened_at ? new Date(item.opened_at).toLocaleString("en-IN") : "—"}</span></div>
      <div class="row"><span>Exit</span><span>${item.closed_at ? new Date(item.closed_at).toLocaleString("en-IN") : "—"}</span></div>
      <div class="row"><span>Initial credit</span><span>₹${fmt(item.initial_net_credit, 0)}</span></div>
      <div class="row"><span>Realized</span><span>${money(item.realized_pnl)}</span></div>
      <div class="row"><span>Charges</span><span>₹${fmt(item.charges, 0)}</span></div>
      <div class="row"><span>${item.status === "OPEN" ? "Live P&amp;L" : "Net"}</span><span>${money(item.net_pnl)}</span></div>
      <details class="history-details"><summary>View four legs</summary><div class="history-leg-list">${(item.legs || []).map(leg => `<div><strong>${leg.leg}</strong><span>${fmt(leg.strike, 0)} · Qty ${fmt(leg.quantity, 0)}</span><span>Entry ₹${fmt(leg.entry_price)}${leg.exit_price == null ? "" : ` · Exit ₹${fmt(leg.exit_price)}`}</span></div>`).join("") || "No leg detail saved."}</div>${item.reason_for_closing ? `<p class="subtle">${item.reason_for_closing}</p>` : ""}</details>
    </div>
  `).join(""); }
async function loadHistory() { try { const list = await api("/manual-monitor/history"); renderHistory(list); } catch (error) { $("history-list").innerHTML = `<div class="subtle">Unable to load history: ${error.message}</div>`; } }
async function refreshHistory() {
  for (const mode of ["LIVE", "PAPER"]) {
    try { await api(`/manual-monitor?mode=${mode}`); } catch (error) {}
  }
  await loadHistory();
}
function renderFund(fund, mode) { if (!fund) return; $("fund-mode-tag").textContent = mode || "MARKET"; $("gross-hedge").textContent = `₹${fmt(fund.gross_hedge_requirement, 0)}`; $("short-proceeds").textContent = `₹${fmt(fund.short_leg_proceeds, 0)}`; $("net-cash").textContent = `₹${fmt(fund.net_initial_cash_requirement, 0)}`; $("fund-charges").textContent = `₹${fmt(fund.charges, 0)}`; $("final-funds").textContent = `₹${fmt(fund.final_estimated_fund_requirement, 0)}`; }
function renderLadder(data) { ["lower_hedge", "lower_short", "lower_breakeven", "spot", "upper_breakeven", "upper_short", "upper_hedge"].forEach(key => { const className = key === "spot" ? "spot-mark" : key.replace("_", "-"); const el = document.querySelector(`.${className}`); if (el && data[key] !== undefined) el.textContent = key === "spot" ? "●" : fmt(data[key], 0); }); const spot = document.querySelector(".spot-mark"); const range = Number(data.upper_hedge) - Number(data.lower_hedge); if (spot && Number.isFinite(Number(data.spot)) && range > 0) { const position = Math.max(0, Math.min(162, (Number(data.upper_hedge) - Number(data.spot)) / range * 162)); spot.style.top = `${position}px`; } }
function timeLeft(seconds) { if (seconds === undefined) return "—"; const d = Math.floor(seconds / 86400); const h = Math.floor(seconds % 86400 / 3600); const m = Math.floor(seconds % 3600 / 60); return `${d ? `${String(d).padStart(2, "0")}D ` : ""}${String(h).padStart(2, "0")}H ${String(m).padStart(2, "0")}M`; }
function render(data) { setConnection(data.connection || "DISCONNECTED"); $("last-update").textContent = `Updated ${new Date().toLocaleTimeString()}`; if (!data.configured) return; $("expiry-label").textContent = new Date(`${data.expiry}T00:00:00`).toLocaleDateString("en-IN", {weekday: "long", day: "2-digit", month: "short", year: "numeric"}); if (data.spot === undefined) { renderLegs(data.legs || []); return; } $("spot").textContent = `₹${fmt(data.spot, 1)}`; $("spot-change").textContent = `${signed(data.spot_change)} ${data.spot_change_pct == null ? "" : `(${signed(data.spot_change_pct)}%)`}`; $("time-left").textContent = timeLeft(data.seconds_to_expiry); $("total-pnl").textContent = money(data.total_pnl); $("total-pnl").className = data.total_pnl >= 0 ? "" : "neg"; $("today-pnl").textContent = money(data.total_pnl); renderLegs(data.legs); $("call-credit").textContent = `₹${fmt(data.call_credit)}`; $("put-credit").textContent = `₹${fmt(data.put_credit)}`; $("net-credit").textContent = `₹${fmt(data.net_credit)}`; $("max-profit").textContent = `₹${fmt(data.max_initial_profit, 0)}`; $("call-spread-pnl").textContent = money(data.call_spread_pnl); $("put-spread-pnl").textContent = money(data.put_spread_pnl); $("distance-put").textContent = `${fmt(Math.abs(data.distance_put_short), 0)} pts`; $("distance-call").textContent = `${fmt(Math.abs(data.distance_call_short), 0)} pts`; $("distance-lower-be").textContent = `${fmt(Math.abs(data.distance_lower_breakeven), 0)} pts`; $("distance-upper-be").textContent = `${fmt(Math.abs(data.distance_upper_breakeven), 0)} pts`; $("risk-badge").textContent = data.risk; $("risk-badge").className = `risk-badge ${data.risk === "WARNING" ? "watch" : data.risk.toLowerCase()}`; renderLadder(data); $("call-iv").textContent = data.call_iv == null ? "—" : `${fmt(data.call_iv, 1)}%`; $("put-iv").textContent = data.put_iv == null ? "—" : `${fmt(data.put_iv, 1)}%`; $("average-iv").textContent = data.average_short_iv == null ? "—" : `${fmt(data.average_short_iv, 1)}% (${signed(data.iv_change)})`; $("call-sl-status").textContent = data.call_sl == null ? "Not set" : `₹${fmt(data.call_sl, 0)} / ${money(data.call_spread_pnl)}`; $("put-sl-status").textContent = data.put_sl == null ? "Not set" : `₹${fmt(data.put_sl, 0)} / ${money(data.put_spread_pnl)}`; $("alerts").innerHTML = data.alerts?.length ? data.alerts.map(alert => `<li>⚠ ${alert}</li>`).join("") : `<li class="subtle">No alerts.</li>`; }
async function refresh() { if (activeTab === "HISTORY") return; try { const snapshot = await api(`/manual-monitor?mode=${activeTab}`); render(snapshot); renderFund(snapshot.fund_requirement, snapshot.mode); } catch (error) { setConnection("RECONNECTING"); $("alerts").innerHTML = `<li>⚠ ${error.message}</li>`; } }
loadExpiries(); loadHistory(); refresh(); setInterval(refresh, 3000); setInterval(() => { if (activeTab === "HISTORY") refreshHistory(); }, 3000);
