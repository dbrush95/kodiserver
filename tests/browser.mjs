import { chromium } from "playwright";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
const browser=await chromium.launch();
try {
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  await page.addInitScript(()=>{
    let shared={kodi_major:21,skin_id:"skin.fentastic",weather_id:"weather.multi",weather_location:"",temperature_unit:"F",repo_interval_hours:24,tvh_host:"",skin_settings:{},weather_settings:{},repositories:[]};
    window.cockpit={spawn:async args=>{
      const action=args[2],payload=JSON.parse(args[3]);
      if(action==="status") return JSON.stringify({hostname:"media-pi",uptime:4500,memory_total:2e9,memory_used:6e8,disk_total:256e9,disk_free:220e9});
      if(action==="backups") return JSON.stringify({path:"/home/test/.local/share/kodiserver/backups",items:[]});
      if(action==="profile")return JSON.stringify(shared);
      if(action==="save-profile"){shared={...shared,...payload};return JSON.stringify(shared);}
      if(action==="services")return JSON.stringify({clients:"/home/test/.local/share/kodiserver/clients",smbd:"active",tvheadend:"active",mariadb:"inactive"});
      if(action==="analyze-playlist")return JSON.stringify({total:3,direct_candidates:1,kodi_dependent:2,other:0});
      if(action==="import")throw new Error("Fixture import failure");
      throw new Error(action);
    }};
  });
  const errors=[];page.on("pageerror",e=>errors.push(e.message));
  await page.goto("http://127.0.0.1:8765/portal/index.html");
  await page.waitForFunction(()=>document.getElementById("hostname").textContent==="media-pi");
  assert.equal(await page.locator("#backup-count").textContent(),"0");
  await fs.mkdir("test-results",{recursive:true});
  await page.screenshot({path:"test-results/overview-desktop.png",fullPage:true});
  await page.locator('[data-page="clients"]').click();
  await page.locator("#weather-location").fill("Example city");
  await page.getByRole("button",{name:"Publish profile",exact:true}).click();
  await page.waitForFunction(()=>document.getElementById("notice").textContent.includes("published"));
  assert.equal(await page.locator("#weather-location").inputValue(),"Example city");
  await page.locator('[data-page="tv"]').click();
  await page.locator("#playlist-path").fill("/media/test/playlist.m3u8");
  await page.getByRole("button",{name:"Inspect playlist"}).click();
  await page.waitForFunction(()=>document.getElementById("playlist-result").textContent.includes("3 entries"));
  assert.equal(await page.locator("#tv-open").getAttribute("href"),"http://127.0.0.1:9981/");
  await page.locator('[data-page="backups"]').click();
  await page.locator("#backup-path").fill("/media/test/backup");
  await page.locator("#backup-label").fill("Shield");
  await page.getByRole("button",{name:"Import backup",exact:true}).click();
  await page.waitForFunction(()=>document.getElementById("notice").textContent.includes("Fixture import failure"));
  assert.equal(await page.getByRole("button",{name:"Import backup",exact:true}).isEnabled(),true);
  await page.locator('[data-page="terminal"]').click();
  assert.equal(await page.locator("#terminal-slot iframe").getAttribute("src"),"/cockpit/@localhost/system/terminal.html");
  await page.locator('[data-page="overview"]').click();
  await page.setViewportSize({width:390,height:844});
  await page.screenshot({path:"test-results/overview-mobile.png",fullPage:true});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,"mobile overflow");
  assert.deepEqual(errors,[]);
  console.log("Browser fixture checks passed. Real Cockpit, TV backend and companion still require device validation.");
} finally { await browser.close(); }
