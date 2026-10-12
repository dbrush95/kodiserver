import json
import re
import time
import xbmc
import xbmcaddon
import xbmcvfs

ADDON_ID = "service.kodiserver.client"

def rpc(method, params):
    response = json.loads(xbmc.executeJSONRPC(json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})))
    if "error" in response:
        raise ValueError(response["error"].get("message", "Kodi setting failed"))
    if response.get("result") is False:
        raise ValueError("Kodi declined a setting change")
    return response.get("result")

def read_json(path):
    handle = xbmcvfs.File(path)
    try:
        text = handle.read(2097153)
    finally:
        handle.close()
    if len(text) > 2097152:
        raise ValueError("Profile is too large")
    return json.loads(text)

def profile():
    path = xbmcaddon.Addon(ADDON_ID).getSetting("profile_path").strip()
    if not path.lower().startswith("smb://") or "@" in path:
        raise ValueError("Choose an SMB profile path; save network credentials in Kodi, not in this address")
    data = read_json(path)
    current = int(xbmc.getInfoLabel("System.BuildVersion").split(".")[0])
    if int(data["kodi_major"]) != current:
        raise ValueError("Server profile and this device must use the same Kodi major version")
    for key, prefix in (("skin_id", "skin."), ("weather_id", "weather.")):
        if not re.fullmatch(re.escape(prefix) + r"[a-zA-Z0-9_.-]+", data[key]):
            raise ValueError("Invalid addon identifier")
    interval = int(data.get("repo_interval_hours", 24))
    if not 6 <= interval <= 168:
        raise ValueError("Invalid repository interval")
    if data.get("temperature_unit") not in ("F", "C"):
        raise ValueError("Invalid temperature unit")
    return data

def state_path():
    path = xbmcvfs.translatePath(xbmcaddon.Addon(ADDON_ID).getAddonInfo("profile"))
    xbmcvfs.mkdirs(path)
    return path + "state.json"

def state():
    try:
        return read_json(state_path())
    except Exception:
        return {}

def save_state(value):
    handle = xbmcvfs.File(state_path(), "w")
    try:
        handle.write(json.dumps(value))
    finally:
        handle.close()

def apply_shared(data):
    # Only skin and weather settings, never an entire userdata/database restore.
    for field, key in (("skin_settings", "skin_id"), ("weather_settings", "weather_id")):
        addon = xbmcaddon.Addon(data[key])  # fails explicitly if not installed
        for name, value in data.get(field, {}).items():
            if re.search(r"password|token|secret|api.?key|username|login|account", name, re.I):
                continue
            addon.setSetting(name, str(value))
    rpc("Settings.SetSettingValue", {"setting": "weather.addon", "value": data["weather_id"]})
    rpc("Settings.SetSettingValue", {"setting": "locale.temperatureunit", "value": "°" + data["temperature_unit"]})
    rpc("Settings.SetSettingValue", {"setting": "lookandfeel.skin", "value": data["skin_id"]})

def apply_library():
    import xml.etree.ElementTree as ET
    path = xbmcaddon.Addon(ADDON_ID).getSetting("profile_path")
    source = path.rsplit("/", 1)[0] + "/library.xml"
    handle = xbmcvfs.File(source)
    try:
        text = handle.read(65537)
    finally:
        handle.close()
    if len(text) > 65536 or "<!DOCTYPE" in text or "<!ENTITY" in text:
        raise ValueError("Invalid shared library file")
    shared = ET.fromstring(text)
    target = xbmcvfs.translatePath("special://profile/advancedsettings.xml")
    if xbmcvfs.exists(target):
        local = xbmcvfs.File(target)
        try:
            original = local.read()
        finally:
            local.close()
        current = ET.fromstring(original)
        if not xbmcvfs.copy(target, target + ".before-kodiserver"):
            raise ValueError("Could not back up existing advancedsettings.xml")
    else:
        current = ET.Element("advancedsettings")
    for tag in ("videodatabase", "musicdatabase"):
        node = shared.find(tag)
        if node is None or node.findtext("type") != "mysql":
            raise ValueError("Missing shared library settings")
        old = current.find(tag)
        if old is not None:
            current.remove(old)
        current.append(node)
    output = xbmcvfs.File(target, "w")
    try:
        output.write(ET.tostring(current, encoding="unicode"))
    finally:
        output.close()
