"use strict";
const $ = id => document.getElementById(id);
const titles = {overview:"Your media, at home.",backups:"A safe home for your setup.",plex:"Your Plex, connected.",terminal:"Your Pi. Your command.",guide:"Set up once. Keep it safe."};
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
function plexLink(url) { const parsed=new URL(url); parsed.pathname="/web/"; parsed.search="";parsed.hash="";return parsed.href; }
function renderPlex(settings) {
  const url=settings.plex_url||""; $("plex-url").value=url; $("plex-open").hidden=!url; $("plex-check").disabled=!url;
  $("plex-tag").textContent=url?"Address saved":"Not configured";
  if(url) {$("plex-open").href=plexLink(url); $("plex-status").textContent="Address saved. Use Check connection to test reachability.";}
}
let refreshing=false;
async function refresh() {
  if(refreshing) return;refreshing=true;$("refresh").disabled=true;
  try {
    const [status,backups,settings]=await Promise.all([api("status"),api("backups"),api("settings")]);
    $("hostname").textContent=status.hostname; $("uptime").textContent="Up "+Math.floor(status.uptime/3600)+"h "+Math.floor(status.uptime%3600/60)+"m";
    $("memory").textContent=status.memory_total?Math.round(status.memory_used/status.memory_total*100)+"%":"Unavailable";
    $("memory-detail").textContent=bytes(status.memory_used)+" of "+bytes(status.memory_total);
    $("disk").textContent=bytes(status.disk_free); $("disk-detail").textContent=bytes(status.disk_total)+" total";
    $("backup-count").textContent=backups.items.length;renderBackups(backups);renderPlex(settings);
  } catch(error) { notice(error.message||String(error)); }
  finally { refreshing=false;$("refresh").disabled=false; }
}
document.querySelectorAll("[data-page],[data-go]").forEach(el=>el.addEventListener("click",()=>{location.hash=el.dataset.page||el.dataset.go;}));
window.addEventListener("hashchange",()=>show(location.hash.slice(1)));
$("refresh").addEventListener("click",()=>{notice("");refresh();});
$("plex-form").addEventListener("submit",async event=>{
  event.preventDefault();const button=event.submitter;button.disabled=true;notice("");
  try { const settings=await api("save-settings",{plex_url:$("plex-url").value.trim()});renderPlex(settings);notice("Plex connection saved."); }
  catch(error){notice(error.message||String(error));} finally{button.disabled=false;}
});
$("plex-check").addEventListener("click",async()=>{
  $("plex-check").disabled=true;$("plex-status").textContent="Checking from the Pi…";
  try{const result=await api("check-plex");$("plex-status").textContent=result.reachable?"Server port is reachable. Open Plex Web to sign in.":"Cannot reach the server. Check its address, power, and network connection.";}
  catch(error){$("plex-status").textContent=error.message||String(error);}finally{$("plex-check").disabled=false;}
});
$("import-form").addEventListener("submit",async event=>{
  event.preventDefault();const button=event.submitter;button.disabled=true;button.textContent="Copying backup…";notice("");
  try{await api("import",{path:$("backup-path").value.trim(),label:$("backup-label").value.trim()});$("import-form").reset();await refresh();notice("Recovery copy imported. Your USB original is unchanged.");}
  catch(error){notice(error.message||String(error));}finally{button.disabled=false;button.textContent="Import backup";}
});
show(location.hash.slice(1));refresh();
