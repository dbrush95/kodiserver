import xbmc
import xbmcaddon
import xbmcgui
from common import ADDON_ID, apply_library, apply_shared, profile, save_state, state

def main():
    dialog = xbmcgui.Dialog()
    addon = xbmcaddon.Addon(ADDON_ID)
    if not addon.getSetting("profile_path"):
        path = dialog.browseSingle(1, "Select profile.json in KodiClients", "files", ".json")
        if not path:
            return
        addon.setSetting("profile_path", path)
    try:
        data = profile()
        choice = dialog.select("Kodi Server setup", [
            "Install repository ZIPs from KodiClients/repositories",
            "Install required skin, weather and TV client",
            "Apply shared skin and weather profile",
            "Configure TV client login",
            "Configure local weather",
            "Companion settings", "Connect shared movie/TV library"])
        if choice == 0:
            dialog.ok("Repository setup", "Choose Install from zip, then KodiClients/repositories. Install each listed repository once. Reopen companion setup afterward.")
            xbmc.executebuiltin("InstallFromZip")
        elif choice == 1:
            # Built-in installer displays Kodi's normal dependency/permission prompts.
            for ident in ("pvr.hts", data["skin_id"], data["weather_id"]):
                xbmc.executebuiltin("InstallAddon(" + ident + ")", True)
            dialog.ok("Dependencies requested", "Let installations finish. If a skin is unavailable, install its repository ZIP first. Enable automatic add-on updates in Kodi Settings → System → Add-ons.")
        elif choice == 2:
            if not dialog.yesno("Follow shared profile?", "This replaces this Kodi profile’s skin and weather preferences. Service logins, audio, remote controls, and databases stay local. Continue?"):
                return
            apply_shared(data)
            addon.setSettingBool("enrolled", True)
            saved = state()
            saved["revision"] = data["revision"]
            save_state(saved)
            dialog.ok("Profile applied", "This Kodi profile will follow published settings while idle. Configure weather once if no weather settings were captured yet.")
        elif choice == 3:
            # Set only nonsecret network address; each client uses its own TVH account.
            if data.get("tvh_host"):
                xbmcaddon.Addon("pvr.hts").setSetting("host", data["tvh_host"])
            xbmc.executebuiltin("Addon.OpenSettings(pvr.hts)")
        elif choice == 4:
            dialog.ok("Local weather", "Location: " + (data.get("weather_location") or "Set location in server portal") + ". Choose this location in the weather provider, then capture a fresh backup on the Pi to share its resolved location settings.")
            xbmc.executebuiltin("Addon.OpenSettings(" + data["weather_id"] + ")")
        elif choice == 5:
            addon.openSettings()
        elif choice == 6:
            if dialog.yesno("Connect shared library?", "This changes the movie/music database connection. Existing local databases remain on this device. Network media sources must match on all clients. Continue?"):
                apply_library()
                dialog.ok("Library configured", "Restart Kodi. Configure your network media sources and scan once on the designated scanning client.")
    except Exception as error:
        dialog.ok("Setup needs attention", str(error))

if __name__ == "__main__":
    main()
