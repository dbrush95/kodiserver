import random
import time
import xbmc
import xbmcaddon
from common import ADDON_ID, apply_shared, profile, save_state, state

def main():
    monitor = xbmc.Monitor()
    # Never hold up Kodi startup; stagger work across devices.
    if monitor.waitForAbort(120 + random.randrange(180)):
        return
    saved = state()
    cached = None
    last_fetch = 0
    while not monitor.abortRequested():
        try:
            addon = xbmcaddon.Addon(ADDON_ID)
            now = time.time()
            if now - last_fetch >= 900:
                last_fetch = now
                cached = profile()
            if cached and not xbmc.Player().isPlaying():
                if addon.getSettingBool("enrolled") and saved.get("revision") != cached.get("revision"):
                    try:
                        apply_shared(cached)
                        saved["revision"] = cached["revision"]
                        save_state(saved)
                    except Exception:
                        xbmc.log("Kodi Server: shared appearance could not be applied; finish companion setup.", xbmc.LOGWARNING)
                if addon.getSettingBool("library_scanner") and now - saved.get("library_scanned", 0) >= 86400:
                    xbmc.executebuiltin("UpdateLibrary(video)")
                    saved["library_scanned"] = now
                    save_state(saved)
                interval = int(cached["repo_interval_hours"]) * 3600
                if addon.getSettingBool("repo_schedule") and now - saved.get("repo_checked", 0) >= interval:
                    xbmc.executebuiltin("UpdateAddonRepos")
                    saved["repo_checked"] = now
                    save_state(saved)
        except Exception:
            # Avoid logging profile paths, account data, or repeated popups.
            xbmc.log("Kodi Server: profile unavailable or not applicable; open companion setup for details.", xbmc.LOGWARNING)
        if monitor.waitForAbort(60):
            return

if __name__ == "__main__":
    main()
