import { chromium } from "playwright";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
const browser=await chromium.launch();
try {
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  await page.addInitScript(()=>{
    let settings={plex_url:""};
    window.cockpit={spawn:async args=>{
      const action=args[2],payload=JSON.parse(args[3]);
      if(action==="status") return JSON.stringify({hostname:"media-pi",uptime:4500,memory_total:2e9,memory_used:6e8,disk_total:256e9,disk_free:220e9});
      if(action==="backups") return JSON.stringify({path:"/home/test/.local/share/kodiserver/backups",items:[]});
      if(action==="settings")return JSON.stringify(settings);
      if(action==="save-settings"){settings={plex_url:payload.plex_url};return JSON.stringify(settings);}
      if(action==="check-plex")return '{"reachable":true}';
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
  await page.locator('[data-page="plex"]').click();
  await page.locator("#plex-url").fill("http://192.168.1.50:32400");
  await page.getByRole("button",{name:"Save connection"}).click();
  await page.locator("#plex-open").waitFor({state:"visible"});
  assert.equal(await page.locator("#plex-open").getAttribute("href"),"http://192.168.1.50:32400/web/");
  await page.locator("#plex-check").click();
  await page.waitForFunction(()=>document.getElementById("plex-status").textContent.includes("reachable"));
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
  console.log("Browser fixture checks passed. Real Cockpit terminal and Plex still require Pi validation.");
} finally { await browser.close(); }
