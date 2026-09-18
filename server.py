"""Control remoto web para un Android TV vía ADB.

Uso:  python server.py [--tv IP:5555] [--port 8080] [--lan]
Por defecto escucha solo en 127.0.0.1. Con --lan escucha en toda la red y
exige el token que se imprime al arrancar (?t=TOKEN o cabecera X-Token).
"""
import argparse, gzip, io, json, posixpath, re, secrets, struct, subprocess, sys, tempfile, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote, quote

from PIL import Image

HERE = Path(__file__).parent
TV = ""
TOKEN = ""
REC = {"proc": None}
MAX_UPLOAD = 1024 * 1024 * 1024  # 1 GB

KEYS = {
    "up": 19, "down": 20, "left": 21, "right": 22, "ok": 23, "back": 4,
    "home": 3, "menu": 82, "settings": 176, "search": 84, "backspace": 67,
    "playpause": 85, "next": 87, "prev": 88, "rewind": 89, "forward": 90,
    "volup": 24, "voldown": 25, "mute": 164, "power": 26, "sleep": 223, "wake": 224,
    "hdmi1": 243, "hdmi2": 244, "hdmi3": 245, "tvinput": 178,
    "chup": 166, "chdown": 167, "info": 165,
}
SETTINGS = {
    "general": "android.settings.SETTINGS",
    "wifi": "android.settings.WIFI_SETTINGS",
    "sound": "android.settings.SOUND_SETTINGS",
    "apps": "android.settings.MANAGE_ALL_APPLICATIONS_SETTINGS",
    "storage": "android.settings.INTERNAL_STORAGE_SETTINGS",
    "dev": "android.settings.APPLICATION_DEVELOPMENT_SETTINGS",
    "about": "android.settings.DEVICE_INFO_SETTINGS",
    "update": "android.settings.SYSTEM_UPDATE_SETTINGS",
    "keyboard": "android.settings.INPUT_METHOD_SETTINGS",
    "accessibility": "android.settings.ACCESSIBILITY_SETTINGS",
    "date": "android.settings.DATE_SETTINGS",
    "locale": "android.settings.LOCALE_SETTINGS",
    "privacy": "android.settings.PRIVACY_SETTINGS",
    "home": "android.settings.HOME_SETTINGS",
}
PKG_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+$")

# Paquetes que nunca se desactivan desde la web (el TV dejaría de arrancar o funcionar)
PROTECTED_PREFIXES = (
    "com.mediatek.", "com.android.systemui", "com.android.tv.settings", "com.android.providers.",
    "com.android.vending", "com.google.android.gms", "com.google.android.gsf",
    "com.google.android.webview", "com.google.android.packageinstaller",
    "com.google.android.permissioncontroller", "com.google.android.inputmethod",
    "com.google.android.ext.", "com.xiaomi.mitv.updateservice", "com.xiaomi.android.tvsetup",
    "com.android.shell", "com.android.networkstack", "com.android.bluetooth",
    "com.android.keychain", "com.android.inputdevices", "com.android.externalstorage",
    "com.android.location", "com.android.se", "com.android.certinstaller",
    "com.android.captiveportallogin", "com.android.proxyhandler", "com.android.pacprocessor",
    "com.android.companiondevicemanager", "com.android.localtransport",
    "com.android.settings.intelligence", "com.android.cts", "com.android.modulemetadata",
    "com.android.dynsystem", "com.android.backupconfirm", "com.android.sharedstoragebackup",
    "com.android.wallpaperbackup", "mitv.service", "com.google.android.katniss",
)


def q(s):
    """Escapa un texto para el shell del TV (comillas simples)."""
    return "'" + s.replace("'", "'\\''") + "'"


def _run(cmd, timeout):
    return subprocess.run(cmd, capture_output=True, timeout=timeout)


def adb(*args, binary=False, timeout=20):
    cmd = ["adb"] + (["-s", TV] if TV else []) + list(args)
    r = _run(cmd, timeout)
    if r.returncode != 0 and TV and re.search(rb"offline|not found|no devices|closed", r.stderr, re.I):
        _run(["adb", "connect", TV], 10)  # reconexión automática
        r = _run(cmd, timeout)
    return r.stdout if binary else r.stdout.decode("utf-8", "replace")


def adb_ok(*args, timeout=20):
    cmd = ["adb"] + (["-s", TV] if TV else []) + list(args)
    r = _run(cmd, timeout)
    return r.returncode == 0, (r.stdout + r.stderr).decode("utf-8", "replace").strip()


def sd_path(p):
    """Valida una ruta del almacenamiento compartido del TV."""
    p = posixpath.normpath(p or "/sdcard")
    if "\x00" in p or "\n" in p or not (p == "/sdcard" or p.startswith("/sdcard/")):
        raise ValueError("ruta no permitida")
    return p


# ---------- lectura ----------
def screenshot(scale=0.5, quality=60):
    # screencap crudo + gzip en el TV: ~3x más rápido que codificar PNG allí
    raw = gzip.decompress(adb("exec-out", "screencap | gzip -1", binary=True))
    # el TV antepone texto de depuración; la cabecera real es (ancho, alto, formato)
    m = re.search(rb"(?s)(.{4})(.{4})\x01\x00\x00\x00", raw[:300])
    w, h = struct.unpack("<II", m.group(1) + m.group(2))
    pix = raw[m.end():m.end() + w * h * 4]
    img = Image.frombuffer("RGBA", (w, h), pix, "raw", "RGBA", 0, 1).convert("RGB")
    if scale != 1:
        img = img.resize((int(w * scale), int(h * scale)))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=quality)
    return out.getvalue()


def foreground():
    out = adb("shell", "dumpsys window | grep -E 'mCurrentFocus|mFocusedApp'")
    m = (re.search(r"mFocusedApp=.*? u0 ([\w.]+)/", out)
         or re.search(r"mCurrentFocus=Window\{\S+ u0 ([\w.]+)", out))
    return m.group(1) if m else None


def current_home():
    out = adb("shell", "cmd package resolve-activity -a android.intent.action.MAIN "
                       "-c android.intent.category.HOME")
    m = re.search(r"packageName=(\S+)", out)
    return m.group(1) if m else None


def info():
    out = adb("shell", "cat /proc/uptime; df /data | tail -1; "
                       "dumpsys meminfo | grep -E 'Total RAM|Free RAM|Used RAM'; "
                       "dumpsys power | grep -m1 mWakefulness=; "
                       "settings get global window_animation_scale; "
                       "settings get secure screensaver_enabled; "
                       "getprop ro.build.version.incremental")
    lines = [l.strip() for l in out.splitlines() if l.strip()]
    kb = lambda pat: int(re.sub(r"\D", "", re.search(pat + r":\s+([\d,]+)K", out).group(1)))
    d = {"ok": True}
    try:
        d["uptime_s"] = int(float(lines[0].split()[0]))
        df = next(l for l in lines if l.startswith("/dev/block")).split()
        d["storage"] = {"total": int(df[1]) * 1024, "used": int(df[2]) * 1024, "free": int(df[3]) * 1024}
        d["ram"] = {"total": kb("Total RAM") * 1024, "free": kb("Free RAM") * 1024, "used": kb("Used RAM") * 1024}
        d["wakefulness"] = (re.search(r"mWakefulness=(\w+)", out) or [None, None])[1]
        tail = [l for l in lines if re.fullmatch(r"[\d.]+|null|\d{3,5}", l)][-3:]
        d["anim"] = tail[0] if tail else None
        d["screensaver"] = tail[1] if len(tail) > 1 else None
        d["build"] = lines[-1]
    except Exception:  # noqa: BLE001
        d["ok"] = False
    d["foreground"] = foreground()
    d["recording"] = bool(REC["proc"] and REC["proc"].poll() is None)
    return d


def user_apps():
    out = adb("shell", "cmd package query-activities -a android.intent.action.MAIN "
                       "-c android.intent.category.LAUNCHER")
    launchable = set(re.findall(r"packageName=(\S+)", out))
    third = set(re.findall(r"package:(\S+)", adb("shell", "pm", "list", "packages", "-3")))
    return sorted(launchable & third)


def sizes():
    txt = adb("shell", "dumpsys", "diskstats", timeout=30)
    try:
        g = lambda k: json.loads(re.search(k + r": (\[.*?\])", txt).group(1))
        names, a, d, c = g("Package Names"), g("App Sizes"), g("App Data Sizes"), g("Cache Sizes")
        return {n: x + y + z for n, x, y, z in zip(names, a, d, c)}
    except Exception:  # noqa: BLE001
        return {}


def packages():
    pk = lambda *a: sorted(re.findall(r"package:(\S+)", adb("shell", "pm", "list", "packages", *a)))
    sz = sizes()
    user = pk("-3")
    disabled = pk("-d")
    system = [p for p in pk("-s", "-e") if p not in disabled]
    home = current_home()
    return {
        "user": [{"pkg": p, "size": sz.get(p)} for p in user],
        "system": [{"pkg": p, "size": sz.get(p), "protected": is_protected(p, home)} for p in system],
        "disabled": [{"pkg": p} for p in disabled],
    }


def is_protected(pkg, home=None):
    return pkg.startswith("android") or pkg == (home or current_home()) or pkg.startswith(PROTECTED_PREFIXES)


def list_dir(path):
    out = adb("shell", "ls -la " + q(path))
    rx = re.compile(r"^([\-dlrwxsStT]{10})\s+\d+\s+\S+\s+\S+\s+(\d+)\s+(\d{4}-\d\d-\d\d \d\d:\d\d)\s+(.+)$")
    items = []
    for line in out.splitlines():
        m = rx.match(line.strip())
        if not m or m.group(4) in (".", ".."):
            continue
        name = m.group(4).split(" -> ")[0]
        items.append({"name": name, "dir": m.group(1)[0] in "dl", "size": int(m.group(2)), "mtime": m.group(3)})
    items.sort(key=lambda i: (not i["dir"], i["name"].lower()))
    return {"path": path, "items": items}


# ---------- acciones ----------
def send_text(text):
    words = []
    for w in text.split(" "):
        w = re.sub(r"[^\x21-\x7e]", "", w)  # `input text` solo admite ASCII imprimible
        words.append(w)
    safe = "%s".join(words)
    if safe:
        adb("shell", "input text " + q(safe))
    return safe


def pkg_action(action, pkg):
    if not PKG_RE.match(pkg):
        raise ValueError("paquete inválido")
    if action == "open":
        adb("shell", "monkey", "-p", pkg, "-c", "android.intent.category.LAUNCHER", "1")
    elif action == "stop":
        adb("shell", "am", "force-stop", pkg)
    elif action == "enable":
        adb("shell", "pm", "enable", pkg)
    elif action == "disable":
        if is_protected(pkg):
            raise ValueError("paquete protegido: desactivarlo puede dejar el TV inservible")
        adb("shell", "pm", "disable-user", "--user", "0", pkg)
    elif action == "uninstall":
        if pkg not in set(re.findall(r"package:(\S+)", adb("shell", "pm", "list", "packages", "-3"))):
            raise ValueError("solo se pueden desinstalar apps instaladas por el usuario")
        if pkg == current_home():
            raise ValueError("es el launcher actual")
        ok, msg = adb_ok("uninstall", pkg)
        if not ok:
            raise ValueError(msg)
    else:
        raise ValueError("acción desconocida")


def set_setting(name, value):
    if name == "anim" and value in ("0", "0.5", "1"):
        for k in ("window_animation_scale", "transition_animation_scale", "animator_duration_scale"):
            adb("shell", "settings", "put", "global", k, value)
    elif name == "screensaver" and value in ("0", "1"):
        adb("shell", "settings", "put", "secure", "screensaver_enabled", value)
    else:
        raise ValueError("ajuste no permitido")


def clean():
    before = info().get("ram", {}).get("free")
    adb("shell", "am kill-all; pm trim-caches 999G", timeout=60)
    time.sleep(3)  # dejar que el kernel recupere la memoria antes de medir
    after = info().get("ram", {}).get("free")
    return {"free_before": before, "free_after": after}


def record(action):
    running = REC["proc"] and REC["proc"].poll() is None
    if action == "start" and not running:
        adb("shell", "rm -f /sdcard/tvremote_rec.mp4")
        cmd = ["adb"] + (["-s", TV] if TV else []) + [
            "shell", "screenrecord", "--time-limit", "180", "--size", "1280x720", "/sdcard/tvremote_rec.mp4"]
        REC["proc"] = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    elif action == "stop" and running:
        adb("shell", "pkill -2 screenrecord")
        try:
            REC["proc"].wait(timeout=10)
        except subprocess.TimeoutExpired:
            REC["proc"].kill()
    return {"recording": bool(REC["proc"] and REC["proc"].poll() is None)}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def cors(self):
        origin = self.headers.get("Origin")
        if origin and self.same_host(origin):
            # la UI puede estar en Apache (puerto 80) y la API aquí (8080)
            self.send_header("Access-Control-Allow-Origin", origin)

    def send(self, code, body, ctype="application/json", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.cors()
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path, name, ctype):
        size = Path(path).stat().st_size
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(size))
        self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + quote(name))
        self.cors()
        self.end_headers()
        with open(path, "rb") as f:
            while chunk := f.read(1 << 20):
                self.wfile.write(chunk)

    def same_host(self, origin):
        """Solo se aceptan orígenes con el mismo hostname al que se accede
        (evita que otra web abierta en el navegador controle el TV)."""
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
        return urlparse(origin).hostname == host

    def authorized(self, qs):
        if not TOKEN:
            return True
        return qs.get("t", [""])[0] == TOKEN or self.headers.get("X-Token") == TOKEN

    def save_body(self, suffix):
        n = int(self.headers.get("Content-Length", 0))
        if n <= 0 or n > MAX_UPLOAD:
            raise ValueError("tamaño de archivo no válido")
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        try:
            left = n
            while left:
                chunk = self.rfile.read(min(1 << 20, left))
                if not chunk:
                    raise ValueError("subida interrumpida")
                tmp.write(chunk)
                left -= len(chunk)
        finally:
            tmp.close()
        return tmp.name

    def handle_req(self, method):
        u = urlparse(self.path)
        qs = parse_qs(u.query)
        p = unquote(u.path)
        origin = self.headers.get("Origin")
        if origin and not self.same_host(origin):
            return self.send(403, {"error": "origen no permitido"})
        if not self.authorized(qs):
            return self.send(401, {"error": "token requerido"})
        one = lambda k, d="": qs.get(k, [d])[0]
        try:
            if method == "GET":
                if p == "/":
                    return self.send(200, (HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
                if p == "/shot.jpg":
                    scale = min(1.0, max(0.2, float(one("scale", "0.5"))))
                    return self.send(200, screenshot(scale, 90 if scale == 1 else 60), "image/jpeg")
                if p == "/apps":
                    return self.send(200, user_apps())
                if p == "/info":
                    return self.send(200, info())
                if p == "/health":
                    ok, msg = adb_ok("get-state")
                    if not ok and TV:
                        adb_ok("connect", TV)
                        ok, msg = adb_ok("get-state")
                    return self.send(200, {"connected": ok and "device" in msg})
                if p == "/packages":
                    return self.send(200, packages())
                if p == "/files":
                    return self.send(200, list_dir(sd_path(one("path"))))
                if p == "/download":
                    path = sd_path(one("path"))
                    tmp = tempfile.NamedTemporaryFile(delete=False)
                    tmp.close()
                    ok, msg = adb_ok("pull", path, tmp.name, timeout=600)
                    if not ok:
                        return self.send(404, {"error": msg})
                    try:
                        return self.send_file(tmp.name, posixpath.basename(path), "application/octet-stream")
                    finally:
                        Path(tmp.name).unlink(missing_ok=True)
                if p == "/record/download":
                    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4")
                    tmp.close()
                    ok, msg = adb_ok("pull", "/sdcard/tvremote_rec.mp4", tmp.name, timeout=300)
                    if not ok:
                        return self.send(404, {"error": "no hay grabación"})
                    try:
                        return self.send_file(tmp.name, "tv-grabacion.mp4", "video/mp4")
                    finally:
                        Path(tmp.name).unlink(missing_ok=True)
            elif method == "POST":
                if p.startswith("/key/"):
                    name = p[5:]
                    if name not in KEYS:
                        return self.send(400, {"error": "tecla desconocida"})
                    args = ["shell", "input", "keyevent"] + (["--longpress"] if one("long") == "1" else [])
                    adb(*args, str(KEYS[name]))
                    return self.send(200, {"ok": True})
                if p == "/tap":
                    x, y = float(one("x")), float(one("y"))
                    if not (0 <= x <= 1 and 0 <= y <= 1):
                        raise ValueError("coordenadas fuera de rango")
                    adb("shell", "input", "tap", str(int(x * 1920)), str(int(y * 1080)))
                    return self.send(200, {"ok": True})
                if p == "/text":
                    n = int(self.headers.get("Content-Length", 0))
                    text = json.loads(self.rfile.read(n)).get("text", "")
                    return self.send(200, {"ok": True, "sent": send_text(text)})
                if p.startswith("/launch/"):
                    pkg_action("open", p[8:])
                    return self.send(200, {"ok": True})
                if p.startswith("/settings/"):
                    name = p[10:]
                    if name not in SETTINGS:
                        return self.send(400, {"error": "menú desconocido"})
                    adb("shell", "am", "start", "-a", SETTINGS[name])
                    return self.send(200, {"ok": True})
                if p == "/power/reboot":
                    adb("reboot")
                    return self.send(200, {"ok": True})
                if p == "/clean":
                    return self.send(200, clean())
                if p.startswith("/pkg/"):
                    _, _, action, pkg = p.split("/", 3)
                    pkg_action(action, pkg)
                    return self.send(200, {"ok": True})
                if p.startswith("/setting/"):
                    set_setting(p[9:], one("v"))
                    return self.send(200, {"ok": True})
                if p.startswith("/record/"):
                    return self.send(200, record(p[8:]))
                if p == "/install":
                    tmp = self.save_body(".apk")
                    try:
                        ok, msg = adb_ok("install", "-r", tmp, timeout=600)
                    finally:
                        Path(tmp).unlink(missing_ok=True)
                    return self.send(200 if ok else 400, {"ok": ok, "message": msg})
                if p == "/upload":
                    dest = sd_path(one("path"))
                    name = posixpath.basename(unquote(self.headers.get("X-Filename", "")))
                    if not name or name in (".", ".."):
                        raise ValueError("nombre de archivo no válido")
                    tmp = self.save_body("")
                    try:
                        ok, msg = adb_ok("push", tmp, dest + "/" + name, timeout=600)
                    finally:
                        Path(tmp).unlink(missing_ok=True)
                    return self.send(200 if ok else 400, {"ok": ok, "message": msg})
            self.send(404, {"error": "no encontrado"})
        except ValueError as e:
            self.send(400, {"error": str(e)})
        except Exception as e:  # noqa: BLE001
            self.send(500, {"error": str(e)})

    def do_GET(self):
        self.handle_req("GET")

    def do_POST(self):
        self.handle_req("POST")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--tv", default="")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--lan", action="store_true")
    a = ap.parse_args()
    TV = a.tv
    TOKEN = secrets.token_urlsafe(8) if a.lan else ""
    host = "0.0.0.0" if a.lan else "127.0.0.1"
    srv = ThreadingHTTPServer((host, a.port), Handler)
    url = f"http://{'<IP-de-tu-PC>' if a.lan else '127.0.0.1'}:{a.port}/" + (f"?t={TOKEN}" if TOKEN else "")
    print("Control remoto en", url, file=sys.stderr)
    srv.serve_forever()
