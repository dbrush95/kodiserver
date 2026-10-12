"use strict";
const $ = id => document.getElementById(id);
const titles = {overview:"Your media, at home.",backups:"A safe home for your setup.",files:"Files on this Pi.",plex:"Movies on Plex. Live TV from the house list.",tv:"Live TV, from one place.",clients:"The same home on every screen.",terminal:"Your Pi. Your command.",guide:"Set up once. Keep it safe."};
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
  if(page==="files") loadFiles($("file-path").dataset.path||"");
}
function fileSize(n) {
  if(!Number.isFinite(n)) return "—";
  if(n<1024) return n+" B";
  if(n<1048576) return Math.max(1,Math.round(n/1024))+" KB";
  return bytes(n);
}
let filesLoading=false;
async function loadFiles(path) {
  if(filesLoading) return;
  filesLoading=true;
  const list=$("file-list");
  $("file-preview").hidden=true;
  try {
    const data=await api("files",path?{path}: {});
    $("file-path").dataset.path=data.path;
    $("file-path").textContent=data.path;
    $("file-up").disabled=!data.parent;
    $("file-up").dataset.path=data.parent||"";
    const places=$("file-places"); places.replaceChildren();
    for(const place of data.places||[]) {
      const button=document.createElement("button");
      button.type="button"; button.className="secondary"; button.textContent=place.label;
      button.addEventListener("click",()=>loadFiles(place.path));
      places.append(button);
    }
    list.replaceChildren();
    if(!(data.entries||[]).length) {
      const empty=document.createElement("p"); empty.className="empty"; empty.textContent="This folder is empty."; list.append(empty);
    }
    for(const entry of data.entries||[]) {
      const row=document.createElement("article"); row.className="file-row";
      const name=document.createElement("button"); name.type="button"; name.className="file-name"; name.textContent=entry.name;
      if(entry.kind==="dir") name.addEventListener("click",()=>loadFiles(data.path.replace(/\/$/,"")+"/"+entry.name));
      else name.disabled=entry.kind!=="file";
      const meta=document.createElement("p");
      const when=entry.modified?new Date(entry.modified).toLocaleString():"";
      meta.textContent=(entry.kind==="dir"?"Folder":entry.kind==="link"?"Link":"File")+" · "+(entry.kind==="file"?fileSize(entry.size):"—")+(when?" · "+when:"");
      const actions=document.createElement("div"); actions.className="file-actions";
      if(entry.kind==="file") {
        const preview=document.createElement("button"); preview.type="button"; preview.className="secondary"; preview.textContent="Preview";
        preview.addEventListener("click",()=>previewFile(data.path,entry.name));
        actions.append(preview);
      }
      if(entry.kind==="file"||entry.kind==="dir") {
        const rename=document.createElement("button"); rename.type="button"; rename.className="secondary"; rename.textContent="Rename";
        rename.addEventListener("click",()=>renameEntry(data.path,entry));
        const remove=document.createElement("button"); remove.type="button"; remove.className="secondary"; remove.textContent="Delete";
        remove.addEventListener("click",()=>deleteEntry(data.path,entry));
        actions.append(rename,remove);
      }
      row.append(name,meta,actions); list.append(row);
    }
    $("file-note").textContent=data.truncated?"This folder has more than 2,000 entries. The first 2,000 are shown.":"";
  } catch(error) { notice(error.message||String(error)); list.replaceChildren(); }
  finally { filesLoading=false; }
}
async function previewFile(path,name) {
  notice(""); $("file-preview").hidden=false; $("file-preview").textContent="Loading…";
  try {
    const result=await api("file-read",{path,name});
    $("file-preview").textContent=result.text;
  } catch(error) { $("file-preview").hidden=true; notice(error.message||String(error)); }
}
async function renameEntry(path,entry) {
  const next=prompt("New name",entry.name);
  if(!next||next===entry.name) return;
  notice("");
  try { await api("file-rename",{path,name:entry.name,to:next}); await loadFiles(path); notice("Renamed."); }
  catch(error) { notice(error.message||String(error)); }
}
async function deleteEntry(path,entry) {
  const warning=entry.kind==="dir"?"Delete the folder "+entry.name+" and everything inside it? This cannot be undone.":"Delete "+entry.name+"? This cannot be undone.";
  if(!confirm(warning)) return;
  notice("");
  try { await api("file-delete",{path,name:entry.name}); $("file-preview").hidden=true; await loadFiles(path); notice("Deleted."); }
  catch(error) { notice(error.message||String(error)); }
}
function renderBackups(data) {
  $("backup-location").textContent=data.path;
  const list=$("backup-list"); list.replaceChildren();
  if(!data.items.length) {
    const e=document.createElement("div"); e.className="empty";
    e.textContent="No recovery copies yet. A finished Shield backup in KodiBackups or on the USB stick is copied here on its own.";
    list.append(e);
  }
  for(const item of data.items) {
    const row=document.createElement("article"); row.className="backup-row";
    const heading=document.createElement("h3"); heading.textContent=item.label;
    const detail=document.createElement("p"); detail.textContent=new Date(item.imported).toLocaleString()+" · "+bytes(item.size)+" · "+item.files+" files · "+(item.archive?"ZIP archive":"Backup folder");
    const hint=document.createElement("p"); hint.textContent="Restore source (copy this folder or ZIP to your client’s backup destination):";
    const path=document.createElement("code"); path.textContent=item.path;
    row.append(heading,detail,hint,path); list.append(row);
  }
  const attached=$("attached-list");
  if(!attached) return;
  attached.replaceChildren();
  for(const item of data.attached||[]) {
    const row=document.createElement("article"); row.className="backup-row";
    const heading=document.createElement("h3"); heading.textContent="Found on a connected drive · "+item.name;
    const detail=document.createElement("p"); detail.textContent="This folder is copied into the recovery library after it stops changing.";
    const path=document.createElement("code"); path.textContent=item.path;
    row.append(heading,detail,path); attached.append(row);
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
function renderCatalog(catalog) {
  const active=catalog.active!=null ? " · "+catalog.active+" active" : "";
  const held=catalog.inactive ? " · "+catalog.inactive+" held as duplicates" : "";
  const ready=catalog.published ? catalog.direct_candidates+" free channels"+active+held+" · "+(catalog.lean||0)+" on Tvheadend for fast Kodi startup · guide "+(catalog.epg?"ready":"still loading") : "The Pi is building the house playlist.";
  $("playlist-counts").textContent=ready+" Playlist host is "+(catalog.host||"unavailable")+".";
  $("apple-url").textContent=catalog.apple_playlist||"";
  $("kodi-url").textContent=catalog.kodi_playlist||"";
  $("epg-url").textContent=catalog.epg_url||"";
  const tvh=$("tvh-url"); if(tvh) tvh.textContent=catalog.tvh_playlist||"";
  const weather=$("weather-line");
  if(weather){
    const report=catalog.weather||{};
    weather.textContent=report.temperature==null ? "Dayton, OH weather is published from this Pi for every Kodi." : report.location+" · "+Math.round(report.temperature)+"°"+report.unit+" · "+report.condition;
  }
  const sources=$("source-list");
  if(sources){
    sources.replaceChildren();
    for(const source of catalog.sources||[]){
      const row=document.createElement("p");
      row.textContent=source.name+" · "+(source.ok?source.channels+" channels":"waiting")+(source.detail?" · "+source.detail:"");
      sources.append(row);
    }
  }
  const repos=$("hosted-repos"); repos.replaceChildren();
  if(!(catalog.files||[]).length) { const empty=document.createElement("p"); empty.textContent="Repository packages appear here after you capture a Shield backup."; repos.append(empty); return; }
  for(const file of catalog.files) { const row=document.createElement("p"); const label=document.createElement("span"); label.textContent=file.name+" · "; const link=document.createElement("code"); link.textContent=file.url; row.append(label, link); repos.append(row); }
}
let lineupData=null;
function renderLineup(data) {
  lineupData=data||{held:[],tuners:[],active:0,inactive:0};
  const summary=$("lineup-summary");
  if(summary){
    const resident=data.guide_resident ? "Guide is loaded in memory ("+bytes(data.guide_bytes)+")." : "Guide is still loading into memory.";
    summary.textContent=(data.active||0)+" active channels. "+(data.inactive||0)+" duplicates held. Sorted by "+(data.sort||"source, then name")+". "+resident;
  }
  const tuners=$("tuner-list");
  if(tuners){
    tuners.replaceChildren();
    const rows=data.tuners||[];
    if(!rows.length){
      const empty=document.createElement("p");
      empty.textContent="Tuners appear here after the lineup is published.";
      tuners.append(empty);
    }
    for(const tuner of rows){
      const row=document.createElement("p");
      row.textContent="Tuner "+tuner.index+" · "+tuner.channels+" channels · 192.168.1.120 port "+tuner.port;
      tuners.append(row);
    }
  }
  paintHeld();
}
function paintHeld() {
  const list=$("held-list");
  if(!list||!lineupData) return;
  const query=($("held-filter")&&$("held-filter").value||"").trim().toLowerCase();
  list.replaceChildren();
  const rows=(lineupData.held||[]).filter(item=>!query||(item.name||"").toLowerCase().includes(query)||(item.source||"").toLowerCase().includes(query));
  if(!rows.length){
    const empty=document.createElement("p");
    empty.className="empty";
    empty.textContent=query?"No held channel matches that name.":"No held duplicates. Every remaining channel is active.";
    list.append(empty);
    return;
  }
  for(const item of rows){
    const row=document.createElement("article");
    row.className="backup-row";
    const heading=document.createElement("h3");
    heading.textContent=item.name+(item.active?" · added back":"");
    const detail=document.createElement("p");
    detail.textContent=item.source+" · "+item.quality+". Kept "+item.kept_name+" from "+item.kept_source+" · "+item.kept_quality+".";
    const guide=document.createElement("p");
    const shows=(item.programmes||[]).map(show=>show.title).filter(Boolean);
    const kept=(item.kept_programmes||[]).map(show=>show.title).filter(Boolean);
    const parts=[];
    if(item.guide_name||shows.length) parts.push("This copy"+(item.guide_name?" ("+item.guide_name+")":"")+": "+(shows.join(" · ")||"no programmes"));
    if(item.kept_guide_name||kept.length) parts.push("Kept copy"+(item.kept_guide_name?" ("+item.kept_guide_name+")":"")+": "+(kept.join(" · ")||"no programmes"));
    guide.textContent=parts.join(" ")||"No guide data for this channel yet.";
    const button=document.createElement("button");
    button.type="button";
    button.className="secondary";
    button.textContent=item.active?"Hold again":"Add this channel back";
    button.addEventListener("click",()=>decideLineup(item.key,!item.active,button));
    row.append(heading,detail,guide,button);
    list.append(row);
  }
}
async function decideLineup(key, active, button) {
  button.disabled=true;
  notice("");
  try{
    renderLineup(await api("lineup-decide",{key,active}));
    notice(active?"Channel added back. The tuners will pick it up.":"Channel held again.");
  }catch(error){
    notice(error.message||String(error));
    button.disabled=false;
  }
}
let refreshing=false;
async function refresh() {
  if(refreshing) return;refreshing=true;$("refresh").disabled=true;
  try {
    const [status,backups,profile,services,catalogData]=await Promise.all([api("status"),api("backups"),api("profile"),api("services"),api("catalog")]);
    $("hostname").textContent=status.hostname; $("uptime").textContent="Up "+Math.floor(status.uptime/3600)+"h "+Math.floor(status.uptime%3600/60)+"m";
    $("memory").textContent=status.memory_total?Math.round(status.memory_used/status.memory_total*100)+"%":"Unavailable";
    $("memory-detail").textContent=bytes(status.memory_used)+" of "+bytes(status.memory_total);
    $("disk").textContent=bytes(status.disk_free); $("disk-detail").textContent=bytes(status.disk_total)+" total";
    $("backup-count").textContent=backups.items.length;renderBackups(backups);renderProfile(profile,backups,services);renderCatalog(catalogData);
    try{renderLineup(await api("lineup"));}catch(error){const summary=$("lineup-summary"); if(summary) summary.textContent=error.message||String(error);}
    if(!$("files").hidden) await loadFiles($("file-path").dataset.path||"");
  } catch(error) { notice(error.message||String(error)); }
  finally { refreshing=false;$("refresh").disabled=false; }
}
document.querySelectorAll("[data-page],[data-go]").forEach(el=>el.addEventListener("click",()=>{location.hash=el.dataset.page||el.dataset.go;}));
window.addEventListener("hashchange",()=>show(location.hash.slice(1)));
$("refresh").addEventListener("click",()=>{notice("");refresh();});
const heldFilter=$("held-filter");
if(heldFilter) heldFilter.addEventListener("input",()=>paintHeld());
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
  try{const r=await api("analyze-playlist",{path:$("playlist-path").value});$("playlist-result").textContent=r.total+" entries: "+r.direct_candidates+" Apple TV candidates, "+r.kodi_dependent+" Kodi-only, "+r.other+" other. Playback must still be tested.";}
  catch(error){$("playlist-result").textContent=error.message||String(error);}finally{button.disabled=false;}
});
$("publish-playlist").addEventListener("click",async ()=>{
  const button=$("publish-playlist");button.disabled=true;notice("");
  try{await api("publish-house",{path:$("playlist-path").value.trim()});await refresh();notice("House playlist refreshed. Tvheadend, Apple TV, and the other Kodi devices read it from this Pi.");}
  catch(error){notice(error.message||String(error));}finally{button.disabled=false;}
});
$("import-form").addEventListener("submit",async event=>{
  event.preventDefault();const button=event.submitter;button.disabled=true;button.textContent="Copying backup…";notice("");
  try{await api("import",{path:$("backup-path").value.trim(),label:$("backup-label").value.trim()});$("import-form").reset();await refresh();notice("Recovery copy imported. Your USB original is unchanged.");}
  catch(error){notice(error.message||String(error));}finally{button.disabled=false;button.textContent="Import backup";}
});
$("file-up").addEventListener("click",()=>{ if($("file-up").dataset.path) loadFiles($("file-up").dataset.path); });
$("file-folder-form").addEventListener("submit",async event=>{
  event.preventDefault(); const name=$("file-folder-name").value.trim(); if(!name) return;
  const button=event.submitter; button.disabled=true; notice("");
  try { await api("file-mkdir",{path:$("file-path").dataset.path,name}); $("file-folder-name").value=""; await loadFiles($("file-path").dataset.path); notice("Folder created."); }
  catch(error){ notice(error.message||String(error)); }
  finally { button.disabled=false; }
});
$("file-upload").addEventListener("change",async event=>{
  const file=event.target.files&&event.target.files[0]; event.target.value="";
  if(!file||!window.cockpit) return;
  if(file.size>8*1024*1024){ notice("Files larger than 8 MB belong on a network share."); return; }
  notice(""); const button=$("file-upload-button"); button.disabled=true; button.textContent="Uploading…";
  try {
    const proc=cockpit.spawn(["/usr/bin/python3","/usr/local/lib/kodiserver/server.py","file-write",JSON.stringify({path:$("file-path").dataset.path,name:file.name})],{superuser:"none",err:"message"});
    proc.input(new Uint8Array(await file.arrayBuffer()));
    const output=await proc; const result=JSON.parse(output);
    if(result.error) throw new Error(result.error);
    await loadFiles($("file-path").dataset.path); notice("Uploaded "+file.name+".");
  } catch(error){ notice(error.message||String(error)); }
  finally { button.disabled=false; button.textContent="Upload"; }
});
$("file-upload-button").addEventListener("click",()=>$("file-upload").click());
show(location.hash.slice(1));refresh();
