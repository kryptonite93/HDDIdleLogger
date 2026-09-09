"use strict";
const page = document.body.dataset.page;
const device = document.body.dataset.device;
const $ = id => document.getElementById(id);
const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const num = value => Number(value || 0).toLocaleString(undefined, {maximumFractionDigits:2});
function duration(value) {
  if (value == null) return "—";
  if (value < 60) return `${Math.floor(value)}s`;
  if (value < 3600) return `${Math.floor(value/60)}m`;
  if (value < 86400) return `${Math.floor(value/3600)}h ${Math.floor(value%3600/60)}m`;
  return `${Math.floor(value/86400)}d ${Math.floor(value%86400/3600)}h`;
}
let timezone, diskData = [], sortKey = "device_name", sortDirection = 1, eventOffset = 0;
function timestamp(value) {
  return value == null ? "Not yet observed" : new Date(value*1000).toLocaleString(undefined, {timeZone:timezone});
}
async function api(path, options) {
  const response = await fetch(`/api/${path}`, options);
  if (!response.ok) {
    let message = `Request failed (${response.status}).`;
    try {const body = await response.json(); message = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail || body.error || message);} catch {}
    throw new Error(message);
  }
  return response.json();
}
const metric = (title, value, note="") => `<dl><dt>${esc(title)}</dt><dd>${esc(value)}</dd><small>${esc(note)}</small></dl>`;
const recommendation = d => d.recommendation_minutes == null ? "No suggestion yet" : `${num(d.recommendation_minutes)} min${d.preliminary ? " · Preliminary" : ""}`;
const stateClass = state => state === "Idle" ? "good" : state === "Active recently" ? "warning" : "muted";
function table(headers, rows, caption="") {
  return `<table>${caption ? `<caption>${esc(caption)}</caption>` : ""}<thead><tr>${headers.map(h=>`<th scope="col">${esc(h)}</th>`).join("")}</tr></thead><tbody>${rows.join("") || `<tr><td colspan="${headers.length}">No observations yet.</td></tr>`}</tbody></table>`;
}
function bars(id, rows, valueKey, labelKey="timeout_minutes", suffix=" min") {
  const max = Math.max(0, ...rows.map(r=>r[valueKey]));
  $(id).innerHTML = rows.map(row => `<div class="bar-row"><span>${esc(row[labelKey])}${suffix}</span><div class="bar-track" aria-hidden="true"><div class="bar-fill" style="width:${max ? row[valueKey]/max*100 : 0}%"></div></div><span class="bar-value">${num(row[valueKey])}</span></div>`).join("");
}
const timerValue = (d, minutes) => d.timeouts.find(t => t.timeout_minutes === minutes)?.spin_ups_per_day ?? null;
const longestValue = d => `${d.longest_idle_is_lower_bound ? "≥ " : ""}${duration(d.longest_idle_seconds)}`;
const longestNote = d => d.longest_idle_is_ongoing ? "Ongoing" : d.longest_idle_is_lower_bound ? "Partial observation" : d.longest_idle_is_estimated ? "Estimated from older history" : "Completed quiet period";
const medianNote = d => d.estimated_quiet_interval_count ? "Includes older estimates" : "Observed quiet polls required";
const columns = [
  ["device_name","Disk",d=>`<a class="disk-link" href="/disks/${encodeURIComponent(d.device_name)}">${esc(d.device_name)}</a>`,d=>d.device_name],
  ["model","Model / serial",d=>`<div class="model">${esc(d.model || "Model unavailable")}<small>${esc(d.serial || (d.rotational == null ? "Rotational status unknown" : ""))}</small></div>`,d=>d.model || ""],
  ["state","State",d=>`<span class="status ${stateClass(d.state)}">${esc(d.state)}</span>`,d=>d.state],
  ["current_idle_seconds","Current idle",d=>`${d.current_idle_is_censored ? "≥ " : ""}${duration(d.current_idle_seconds)}`,d=>d.current_idle_seconds],
  ["last_activity_at","Last activity",d=>esc(timestamp(d.last_activity_at)),d=>d.last_activity_at],
  ["longest_idle_seconds","Longest observed",d=>`${longestValue(d)}<small>${d.longest_idle_seconds == null ? "No quiet period yet" : longestNote(d)}</small>`,d=>d.longest_idle_seconds],
  ["median_idle_seconds","Median quiet",d=>`${duration(d.median_idle_seconds)}<small>${d.median_idle_seconds == null ? "No completed quiet period" : medianNote(d)}</small>`,d=>d.median_idle_seconds],
  ["cycles30","30m cycles/day",d=>timerValue(d,30)==null ? "Not tested" : num(timerValue(d,30)),d=>timerValue(d,30)],
  ["cycles60","60m cycles/day",d=>timerValue(d,60)==null ? "Not tested" : num(timerValue(d,60)),d=>timerValue(d,60)],
  ["recommendation_minutes","Suggested timer",d=>`${esc(recommendation(d))}<small>${duration(d.valid_observation_seconds)} observed</small>`,d=>d.recommendation_minutes]
];
function renderDisks() {
  const column = columns.find(c=>c[0]===sortKey);
  const sorted = [...diskData].sort((a,b)=> {
    const av = column[3](a), bv = column[3](b);
    if (av == null) return bv == null ? 0 : 1;
    if (bv == null) return -1;
    return sortDirection * (typeof av === "number" ? av-bv : String(av).localeCompare(String(bv),undefined,{numeric:true}));
  });
  $("disk-table").innerHTML = `<table><thead><tr>${columns.map(c=>`<th scope="col" aria-sort="${sortKey === c[0] ? sortDirection===1 ? "ascending" : "descending" : "none"}"><button data-sort="${c[0]}">${esc(c[1])}${sortKey===c[0] ? sortDirection===1 ? " ↑" : " ↓" : ""}</button></th>`).join("")}</tr></thead><tbody>${sorted.map(d=>`<tr>${columns.map(c=>`<td>${c[2](d)}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
  $("empty").hidden = diskData.length > 0;
  $("disk-table").hidden = !diskData.length;
}
function timeoutTable(d) {
  return table(["Delay","Down opportunities","Completed spin-ups","Spin-ups / day","Total hours down","Hours down / day","Valid time down","Current potential hours"], d.timeouts.map(t=>`<tr><td>${num(t.timeout_minutes)} min</td><td class="number">${num(t.spin_down_opportunities)}</td><td class="number">${num(t.completed_spin_ups)}</td><td class="number">${num(t.spin_ups_per_day)}</td><td class="number">${num(t.spun_down_hours)}</td><td class="number">${num(t.spun_down_hours_per_day)}</td><td class="number">${num(t.percent_valid_time)}%</td><td class="number">${num(t.current_potential_spun_down_hours)}</td></tr>`));
}
async function renderEvents() {
  const data = await api(`disks/${encodeURIComponent(device)}/events?offset=${eventOffset}&limit=25`);
  const rows = [
    ...data.activity.map(e=>({time:e.observed_at,type:"Activity",detail:`${num(e.reads_delta)} reads · ${num(e.writes_delta)} writes · ${num(e.bytes_read_delta)} B read · ${num(e.bytes_written_delta)} B written · ${num(e.discards_delta)} discards · ${num(e.flushes_delta)} flushes`})),
    ...data.idle.map(e=>({time:e.ended_at,type:e.quiet_sample_observed === 0 ? "Active-sample gap" : e.quiet_sample_observed === 1 ? "Quiet period" : "Historical I/O gap",detail:`${duration(e.duration_seconds)}${e.start_is_censored ? " · Unknown beginning; excluded from advice" : ""}${e.end_is_censored ? " · Observation ended; no spin-up counted" : ""}`})),
    ...data.collector.map(e=>({time:e.occurred_at,type:"Collection",detail:e.event_type.replaceAll("_"," ")}))
  ].sort((a,b)=>b.time-a.time);
  $("events").innerHTML = table(["Observed at","Kind","Details"], rows.map(r=>`<tr><td>${esc(timestamp(r.time))}</td><td>${esc(r.type)}</td><td>${esc(r.detail)}</td></tr>`),`Page ${eventOffset/25+1}: up to 25 entries of each kind`);
  $("events-prev").disabled = eventOffset === 0;
  $("events-next").disabled = [data.activity,data.idle,data.collector].every(a=>a.length<25);
}
async function refresh() {
  try {
    const status = await api("status");
    timezone = status.timezone;
    $("collector-status").textContent = status.healthy ? "Collecting" : "Needs attention";
    $("collector-status").className = `status ${status.healthy ? "good" : "bad"}`;
    $("connection-error").hidden = status.healthy && !status.error;
    $("connection-error").textContent = status.error || "Waiting for a successful diskstats reading. Check the host mount and permissions. Values stop at the last successful sample.";
    $("updated-at").textContent = `Last successful reading: ${timestamp(status.last_success_at)} · ${status.sample_interval_seconds}s samples · ${timezone}`;
    if (page === "dashboard") {
      $("summary").innerHTML = metric("Collection uptime",duration(status.uptime_seconds),status.healthy ? "Collector running" : "Check collector status") + metric("Valid observation",duration(status.minimum_valid_observation_seconds),"Shortest record among selected disks") + metric("Disks selected",`${status.enabled_disks} / ${status.discovered_disks}`,"Enabled, eligible and present") + metric("Idle beyond 30 minutes",status.idle_over_30_minutes,"At the last successful reading") + metric("Array timer",recommendation(status),"Manually applied in Unraid");
      diskData = await api("disks");
      $("disk-count").textContent = `${diskData.length} discovered · Sort any column to compare workloads`;
      const focused = document.activeElement?.dataset.sort;
      const focusedDisk = document.activeElement?.closest('#disk-table a')?.getAttribute('href');
      renderDisks();
      if (focused) document.querySelector(`[data-sort="${focused}"]`)?.focus({preventScroll:true});
      if (focusedDisk) [...$("disk-table").querySelectorAll('a')].find(a=>a.getAttribute('href')===focusedDisk)?.focus({preventScroll:true});
    } else if (page === "disk") {
      const d = await api(`disks/${encodeURIComponent(device)}`);
      $("disk-model").textContent = [d.model || "Model unavailable",d.serial,d.rotational == null ? "Rotational status unknown" : ""].filter(Boolean).join(" · ");
      $("disk-state").textContent = d.state;
      $("disk-state").className = `status ${stateClass(d.state)}`;
      $("disk-summary").innerHTML = metric("Current observed idle",`${d.current_idle_is_censored ? "≥ " : ""}${duration(d.current_idle_seconds)}`,d.current_idle_is_censored ? "Earlier activity is unknown" : "Since the last observed I/O") + metric("Longest observed",longestValue(d),d.longest_idle_seconds == null ? "No quiet period yet" : longestNote(d)) + metric("Median quiet",duration(d.median_idle_seconds),`${d.completed_interval_count} completed quiet periods · ${medianNote(d)}`) + metric("75th / 90th percentile",`${duration(d.p75_idle_seconds)} / ${duration(d.p90_idle_seconds)}`,"Completed quiet periods only") + metric("Valid observation",duration(d.valid_observation_seconds),`Last activity: ${timestamp(d.last_activity_at)}`);
      $("recommendation").textContent = `${recommendation(d)}. ${d.recommendation_reason}.`;
      $("timeout-table").innerHTML = timeoutTable(d);
      bars("down-chart",d.timeouts,"spun_down_hours_per_day"); bars("cycles-chart",d.timeouts,"spin_ups_per_day"); bars("histogram",d.histogram,"count","label","");
      $("censor-note").textContent = `${d.excluded_initial_intervals} completed initial intervals excluded because their beginning is unknown. ${d.right_censored_intervals} known intervals ended with an observation boundary; only their proven portion contributes to modeled down time, with no completed spin-up. Open intervals contribute current potential down time only. Collector downtime is never bridged. Last I/O: ${d.last_io ? `${num(d.last_io.bytes_read_delta)} B read, ${num(d.last_io.bytes_written_delta)} B written, ${num(d.last_io.flushes_delta)} flushes.` : "not yet observed."}`;
      await renderEvents();
      $("censor-note").textContent = `Quiet statistics require at least one reading with unchanged counters between activity readings. ${d.excluded_active_gaps} active-sample gaps excluded from the median and histogram. ${d.estimated_quiet_interval_count} completed quiet periods estimated from older history using the session's sampling interval; delayed older polls can affect these estimates. Longest observed includes ongoing and partial quiet periods, marked ≥. ` + $("censor-note").textContent;
    } else if (page === "analysis") {
      const a = await api("analysis");
      $("array-summary").innerHTML = metric("Array timer",recommendation(a),"Meets thresholds on every included disk") + metric("Disks in analysis",a.enabled_disks,"Selected, eligible and present") + metric("Minimum valid record",duration(a.minimum_valid_observation_seconds),"72 hours required on every included disk");
      $("array-table").innerHTML = table(["Delay","Spin-ups / day","Disk-hours down / day","Disks benefiting","Above cycling limit"], a.timeouts.map(t=>`<tr><td>${num(t.timeout_minutes)} min</td><td class="number">${num(t.spin_ups_per_day)}</td><td class="number">${num(t.spun_down_disk_hours_per_day)}</td><td class="number">${t.benefiting_disks}</td><td class="number ${t.high_cycling_disks ? "warning" : ""}">${t.high_cycling_disks}</td></tr>`));
      bars("array-down-chart",a.timeouts,"spun_down_disk_hours_per_day"); bars("array-cycles-chart",a.timeouts,"spin_ups_per_day");
    } else if (page === "settings") {
      updateDiskSelection(await api("disks"));
    }
  } catch (error) {
    $("connection-error").hidden = false;
    $("connection-error").textContent = `${error.message} Displayed observations may be stale. Retrying automatically.`;
    $("collector-status").textContent = "Disconnected"; $("collector-status").className = "status bad";
  }
}
async function loadSettings() {
  const [settings, disks] = await Promise.all([api("settings"),api("disks")]);
  const form = $("settings-form");
  for (const [key,value] of Object.entries(settings)) if (form.elements[key]) form.elements[key].value = Array.isArray(value) ? value.join(", ") : value;
  form.elements.efficiency_percent.value = settings.recommendation_efficiency_threshold*100;
  updateDiskSelection(disks);
}
function updateDiskSelection(disks) {
  const selection=$("disk-selection");
  if (!disks.length) {selection.textContent="No disks discovered yet. Check the collector status and host mounts."; return;}
  if (!selection.querySelector('[data-disk]')) selection.replaceChildren();
  for (const d of disks) {
    let input=[...selection.querySelectorAll('[data-disk]')].find(element=>element.dataset.disk===d.device_name);
    if (!input) {
      selection.insertAdjacentHTML('beforeend',`<label><input type="checkbox" data-disk="${esc(d.device_name)}" ${d.enabled ? "checked" : ""}><span>${esc(d.device_name)}<small></small></span></label>`);
      input=selection.lastElementChild.querySelector('input');
    }
    // Preserve unsaved checked states and focused elements while discovery catches up.
    input.disabled=!d.eligible;
    input.closest('label').querySelector('small').textContent=`${d.model || 'Model unavailable'} · ${!d.present ? 'Missing' : !d.eligible ? 'Excluded by filter / device type' : d.rotational == null ? 'Rotational status unknown' : 'HDD'}`;
  }
}
if (page === "dashboard") $("disk-table").addEventListener("click", event=>{
  const button = event.target.closest("button[data-sort]"); if (!button) return;
  sortDirection = sortKey===button.dataset.sort ? -sortDirection : 1; sortKey=button.dataset.sort; renderDisks();
  document.querySelector(`[data-sort="${sortKey}"]`).focus({preventScroll:true});
});
if (page === "disk") for (const [id, delta] of [["events-prev",-25],["events-next",25]]) $(id).addEventListener("click",async()=>{
  eventOffset=Math.max(0,eventOffset+delta); try {await renderEvents();} catch(error) {$("connection-error").hidden=false; $("connection-error").textContent=error.message;}
});
if (page === "settings") {
  $("save-settings").disabled = true;
  loadSettings().then(()=>$("save-settings").disabled=false).catch(error=>{$("save-result").textContent=`Could not load settings: ${error.message}. Reload to retry.`;});
  $("settings-form").addEventListener("submit",async event=>{
    event.preventDefault(); const f=event.currentTarget.elements;
    const settings={sample_interval_seconds:Number(f.sample_interval_seconds.value),include_device_regex:f.include_device_regex.value,exclude_device_regex:f.exclude_device_regex.value,timezone:f.timezone.value.trim(),timeouts_minutes:f.timeouts_minutes.value.split(",").map(s=>Number(s.trim())),cycling_warning_threshold:Number(f.cycling_warning_threshold.value),recommendation_efficiency_threshold:Number(f.efficiency_percent.value)/100};
    const enabled_disks=Object.fromEntries([...document.querySelectorAll("[data-disk]")].map(input=>[input.dataset.disk,input.checked]));
    $("save-settings").disabled=true; $("save-result").textContent="Saving…";
    try {await api("settings",{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({settings,enabled_disks})}); $("save-result").textContent="Saved. Collection changes take effect at the next sample."; await refresh();}
    catch(error) {$("save-result").textContent=error.message;}
    finally {$("save-settings").disabled=false;}
  });
  $("clear-form").addEventListener("submit",async event=>{
    event.preventDefault(); const button=event.currentTarget.querySelector("button"); button.disabled=true;
    try {const result=await api("data/clear",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({confirmation:$("clear-confirm").value})}); $("clear-result").textContent=result.message; $("clear-confirm").value=""; await refresh();}
    catch(error) {$("clear-result").textContent=error.message;}
    finally {button.disabled=false;}
  });
}
async function tick() {await refresh(); setTimeout(tick,10000);}
tick();
