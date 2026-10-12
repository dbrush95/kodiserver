"use strict";
const $ = id => document.getElementById(id);
const titles = {overview:"Your media, at home.",backups:"A safe home for your setup.",plex:"Plex, when you’re ready.",tv:"Live TV, from one place.",clients:"The same home on every screen.",terminal:"Your Pi. Your command.",guide:"Set up once. Keep it safe."};
function notice(message) { $("notice").textContent=message; $("notice").hidden=!message; }
function bytes(n) { if(!Number.isFinite(n)) return "—"; return n>=1073741824 ? (n/1073741824).toFixed(1)+" GB" : Math.round(n/1048576)+" MB"; }
async function api(action, args={}) {
  if(!window.cockpit) throw new Error("Open this portal through Cockpit on your Pi to use server features.");
  const output = await cockpit.spawn(["/usr/bin/python3","/usr/local/lib/kodiserver/server.py",action,JSON.stringify(args)],{superuser:"none",err:"message"});
  const result=JSON.parse(output); if(result.error) throw new Error(result.error); return result;
}
function show(page) {
  if(!titles[page]) page="overview";
  document.querySelectorAll(".page").forEach(el=>el.hidden=el.id!==page);
  document.querySelectorAll("[data-page]").forEach(el=>{ if(el.dataset.page===page) el.setAttribute("aria-current","page"); else el.removeAttribute("aria-current"); });
  $("page-title").textContent=titles[page]; $("breadcrumb").textContent=page.toUpperCase();
  if(page==="terminal" && !$("terminal-slot").querySelector("iframe") && window.cockpit) {
    const frame=document.createElement("iframe"); frame.title="Raspberry Pi terminal";
    frame.src="/cockpit/@localhost/system/terminal.html"; $("terminal-slot").replaceChildren(frame);
  } else if(page==="terminal" && !window.cockpit) $("terminal-slot").textContent="The real terminal is available after installing on your Pi and signing in to Cockpit.";
}
function renderBackups(data) {
  $("backup-location").textContent=data.path;
  const list=$("backup-list"); list.replaceChildren();
  if(!data.items.length) { const e=document.createElement("div"); e.className="empty"; e.textContent="No recovery copies yet. Import your first Shield backup above."; list.append(e); return; }
  for(const item of data.items) {
    const row=document.createElement("article"); row.className="backup-row";
    const heading=document.createElement("h3"); heading.textContent=item.label;
    const detail=document.createElement("p"); detail.textContent=new Date(item.imported).toLocaleString()+" · "+bytes(item.size)+" · "+item.files+" files · "+(item.archive?"ZIP archive":"Backup folder");
    const hint=document.createElement("p"); hint.textContent="Restore source (copy this folder or ZIP to your client’s backup destination):";
    const path=document.createElement("code"); path.textContent=item.path;
    row.append(heading,detail,hint,path); list.append(row);
  }
}
function renderProfile(profile, backups, services) {
  for(const [id,key] of Object.entries({"kodi-major":"kodi_major","skin-id":"skin_id","weather-id":"weather_id","weather-location":"weather_location","temperature-unit":"temperature_unit","repo-hours":"repo_interval_hours","tvh-host":"tvh_host"})) $(id).value=profile[key]??"";
  $("profile-summary").textContent=Object.keys(profile.skin_settings||{}).length+" skin settings · "+Object.keys(profile.weather_settings||{}).length+" weather settings · "+(profile.repositories||[]).length+" repository packages";
  $("clients-location").textContent="Profile folder: "+services.clients;
  $("shared-services").textContent="File sharing: "+services.smbd+" · Library database: "+services.mariadb;
  $("capture-id").replaceChildren();
  for(const item of backups.items.filter(x=>!x.archive)) {const option=document.createElement("option");option.value=item.id;option.textContent=item.label; $("capture-id").append(option);}
  $("capture-form").querySelector("button").disabled=!$("capture-id").options.length;
  $("repo-list").replaceChildren();
  for(const repo of profile.repositories||[]) {const item=document.createElement("div");item.className="repo-item";item.textContent=repo.name+" · "+repo.zip;$("repo-list").append(item);}
  $("tv-status").textContent="Tvheadend: "+services.tvheadend;
  $("tv-tag").textContent=services.tvheadend;
  const address=new URL(location.href);address.protocol="http:";address.port="9981";address.pathname="/";address.search="";address.hash="";
  $("tv-open").href=address.href;
}
let refreshing=false;
async function refresh() {
  if(refreshing) return;refreshing=true;$("refresh").disabled=true;
  try {
    const [status,backups,profile,services]=await Promise.all([api("status"),api("backups"),api("profile"),api("services")]);
    $("hostname").textContent=status.hostname; $("uptime").textContent="Up "+Math.floor(status.uptime/3600)+"h "+Math.floor(status.uptime%3600/60)+"m";
    $("memory").textContent=status.memory_total?Math.round(status.memory_used/status.memory_total*100)+"%":"Unavailable";
    $("memory-detail").textContent=bytes(status.memory_used)+" of "+bytes(status.memory_total);
    $("disk").textContent=bytes(status.disk_free); $("disk-detail").textContent=bytes(status.disk_total)+" total";
    $("backup-count").textContent=backups.items.length;renderBackups(backups);renderProfile(profile,backups,services);
  } catch(error) { notice(error.message||String(error)); }
  finally { refreshing=false;$("refresh").disabled=false; }
}
document.querySelectorAll("[data-page],[data-go]").forEach(el=>el.addEventListener("click",()=>{location.hash=el.dataset.page||el.dataset.go;}));
window.addEventListener("hashchange",()=>show(location.hash.slice(1)));
$("refresh").addEventListener("click",()=>{notice("");refresh();});
$("profile-form").addEventListener("submit",async event=>{
  event.preventDefault();const button=event.submitter;button.disabled=true;notice("");
  const args={};
  for(const [id,key] of Object.entries({"kodi-major":"kodi_major","skin-id":"skin_id","weather-id":"weather_id","weather-location":"weather_location","temperature-unit":"temperature_unit","repo-hours":"repo_interval_hours","tvh-host":"tvh_host"})) args[key]=$(id).value;
  try{await api("save-profile",args);await refresh();notice("Shared profile published. Enrolled clients apply it while idle.");}
  catch(error){notice(error.message||String(error));}finally{button.disabled=false;}
});
$("capture-form").addEventListener("submit",async event=>{
  event.preventDefault();const button=event.submitter;button.disabled=true;
  try{await api("capture-profile",{id:$("capture-id").value});await refresh();notice("Skin/weather settings and repository packages published.");}
  catch(error){notice(error.message||String(error));}finally{button.disabled=false;}
});
$("playlist-form").addEventListener("submit",async event=>{
  event.preventDefault();const button=event.submitter;button.disabled=true;
  try{const r=await api("analyze-playlist",{path:$("playlist-path").value});$("playlist-result").textContent=r.total+" entries: "+r.direct_candidates+" direct candidates, "+r.kodi_dependent+" Kodi-dependent, "+r.other+" other. Playback must still be tested.";}
  catch(error){$("playlist-result").textContent=error.message||String(error);}finally{button.disabled=false;}
});
$("import-form").addEventListener("submit",async event=>{
  event.preventDefault();const button=event.submitter;button.disabled=true;button.textContent="Copying backup…";notice("");
  try{await api("import",{path:$("backup-path").value.trim(),label:$("backup-label").value.trim()});$("import-form").reset();await refresh();notice("Recovery copy imported. Your USB original is unchanged.");}
  catch(error){notice(error.message||String(error));}finally{button.disabled=false;button.textContent="Import backup";}
});
show(location.hash.slice(1));refresh();
