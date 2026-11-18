"""
Autoriza un canal de YouTube (ES o EN) y guarda su token en E:\\YoutubeClips\\secretos.

Uso:   python autorizar_youtube.py es      (o: en)

Abre el navegador: inicia sesión con la cuenta dueña del canal y, si Google te deja elegir, el canal
de marca correcto. Como la app no está verificada, verás «Google no ha verificado esta aplicación»:
Avanzado -> Ir a (nombre de la app) -> Continuar.

Solo biblioteca estándar. Flujo OAuth de escritorio con PKCE y redirección a localhost.
No imprime tokens ni secretos. Permisos pedidos:
  - youtube.upload   : subir vídeos
  - youtube.readonly : solo para leer el nombre del canal autorizado (comprobar que es el correcto)
"""
from __future__ import annotations

import base64
import hashlib
import http.server
import json
import os
import secrets
import socketserver
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

SECRETOS = Path(r"E:\YoutubeClips\secretos")
CLIENTE = SECRETOS / "youtube_client_secret.json"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.readonly"]
ESPERA_S = 300


def post(url: str, datos: dict) -> dict:
    req = urllib.request.Request(url, data=urllib.parse.urlencode(datos).encode(), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise SystemExit(f"Google rechazó la petición ({e.code}): {e.read().decode()[:300]}")


def main() -> int:
    if len(sys.argv) not in (2, 3) or sys.argv[1] not in ("es", "en"):
        print(__doc__ + "\nOpcional: añade el @usuario esperado como segundo argumento; si el canal elegido no coincide, no se guarda el token.")
        return 2
    idioma = sys.argv[1]
    esperado = sys.argv[2].lstrip("@") if len(sys.argv) == 3 else None  # @usuario o ID de canal (UC...)
    c = json.loads(CLIENTE.read_text(encoding="utf-8"))["installed"]

    verificador = base64.urlsafe_b64encode(os.urandom(48)).rstrip(b"=").decode()
    reto = base64.urlsafe_b64encode(hashlib.sha256(verificador.encode()).digest()).rstrip(b"=").decode()
    estado = secrets.token_urlsafe(24)
    recibido: dict = {}

    class Manejador(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            if "code" in q or "error" in q:
                recibido.update({k: v[0] for k, v in q.items()})
                cuerpo = "<h2>Listo. Puedes cerrar esta pestaña y volver a la terminal.</h2>" if "code" in q \
                    else "<h2>Autorización cancelada o con error. Vuelve a la terminal.</h2>"
                self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.end_headers()
                self.wfile.write(cuerpo.encode())
            else:
                self.send_response(404); self.end_headers()

        def log_message(self, *a):  # silencio: la URL lleva el código
            pass

    with socketserver.TCPServer(("127.0.0.1", 0), Manejador) as srv:
        puerto = srv.server_address[1]
        redir = f"http://127.0.0.1:{puerto}"
        url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
            "client_id": c["client_id"], "redirect_uri": redir, "response_type": "code",
            "scope": " ".join(SCOPES), "code_challenge": reto, "code_challenge_method": "S256",
            "state": estado, "access_type": "offline", "prompt": "consent select_account",
        })
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        print(f"Autorizando el canal {idioma.upper()}. Se abre el navegador; si no, copia esta dirección:\n{url}\n", flush=True)
        webbrowser.open(url)
        t0 = time.time()
        while not recibido and time.time() - t0 < ESPERA_S:
            time.sleep(0.5)
        srv.shutdown()

    if "code" not in recibido:
        print(f"No se completó la autorización ({recibido.get('error', 'tiempo agotado')})."); return 1
    if recibido.get("state") != estado:
        print("El parámetro state no coincide: abortado por seguridad."); return 1

    tok = post(c["token_uri"], {
        "client_id": c["client_id"], "client_secret": c["client_secret"], "code": recibido["code"],
        "code_verifier": verificador, "redirect_uri": redir, "grant_type": "authorization_code"})
    if "refresh_token" not in tok:
        print("Google no devolvió refresh_token. Revoca el acceso en myaccount.google.com/permissions y repite."); return 1

    concedidos = tok.get("scope", "").split()
    print("Permisos concedidos:", ", ".join(s.rsplit("/", 1)[-1] for s in concedidos) or "(ninguno)")
    for s in SCOPES:
        if s not in concedidos:
            print(f"AVISO: falta el permiso {s.rsplit('/', 1)[-1]} (¿lo desmarcaste en la pantalla de Google?)")

    # El token se guarda ANTES de comprobar el canal: así no hay que repetir el inicio de sesión si falla.
    SECRETOS.mkdir(exist_ok=True)
    destino = SECRETOS / f"youtube_token_{idioma}.json"
    datos = {"idioma": idioma, "canal_id": None, "canal_titulo": None, "refresh_token": tok["refresh_token"],
             "scopes": concedidos, "client_id": c["client_id"],
             "autorizado": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    destino.write_text(json.dumps(datos, indent=2), encoding="utf-8")
    print(f"Token guardado en {destino} (no lo compartas ni lo subas a git).")

    req = urllib.request.Request("https://www.googleapis.com/youtube/v3/channels?part=snippet&mine=true",
                                 headers={"Authorization": f"Bearer {tok['access_token']}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            canales = json.loads(r.read().decode()).get("items", [])
    except urllib.error.HTTPError as e:
        # El cuerpo del error de Google no contiene secretos y explica la causa.
        print(f"No se pudo leer el canal: HTTP {e.code}: {e.read().decode()[:600]}")
        return 1
    if not canales:
        print("Esta cuenta no tiene ningún canal de YouTube. Elige otra cuenta o canal."); return 1
    canal = canales[0]
    handle = canal["snippet"].get("customUrl")
    if esperado and not (canal["id"] == esperado or (handle or "").lstrip("@").lower() == esperado.lower()):
        destino.unlink(missing_ok=True)  # no dejar un token de un canal equivocado
        print(f"NO COINCIDE: elegiste «{canal['snippet']['title']}» {handle or ''}, se esperaba @{esperado}. "
              "Token descartado: repite y elige otra identidad de la lista.")
        return 3
    datos.update({"canal_id": canal["id"], "canal_titulo": canal["snippet"]["title"], "canal_handle": handle})
    destino.write_text(json.dumps(datos, indent=2), encoding="utf-8")
    print(f"OK. Canal autorizado para {idioma.upper()}: «{canal['snippet']['title']}» {handle or ''} ({canal['id']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
