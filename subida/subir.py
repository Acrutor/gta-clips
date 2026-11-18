"""
Sube a YouTube los clips APROBADOS por el filtro (cola), respetando la cuota diaria.

Uso:
  python subir.py                   # SIMULACIÓN: muestra qué subiría y cuánta cuota quedaría (no sube nada)
  python subir.py --subir           # sube de verdad
  python subir.py --subir --idioma es --max-dia 6 --privacidad private

Reglas:
  - Orden: de mayor a menor puntuación, alternando directos para no llenar el día con un solo streamer.
  - Tope diario: --max-dia (6 por defecto = 6 x 1600 = 9.600 de las 10.000 unidades). El día se cuenta en hora del
    Pacífico, que es cuando YouTube reinicia la cuota. Se cuenta TODO lo subido hoy, de cualquier idioma.
  - Privacidad: 'private' por defecto. Mientras la app no esté auditada, YouTube fuerza privado igualmente.
  - La descripción cita el directo de origen (crédito al creador) y añade #Shorts.
  - Si YouTube responde quotaExceeded, se para y se anota; los clips siguen «pendientes» para mañana.
  - Registro: datos\\cola\\subidas.jsonl (una línea por subida) y la ficha del clip pasa a estado_subida=subido.

Solo biblioteca estándar. Los tokens se leen de E:\\YoutubeClips\\secretos y nunca se imprimen.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

RAIZ = Path(r"E:\YoutubeClips")
SECRETOS = RAIZ / "secretos"
COLA = RAIZ / "datos" / "cola"
COSTE_SUBIDA = 1600
CUOTA_DIA = 10000
CATEGORIA_GAMING = "20"


def hoy_pacifico() -> str:
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("America/Los_Angeles")
    except Exception:  # noqa: BLE001 - Windows sin tzdata
        tz = dt.timezone(dt.timedelta(hours=-8))  # PST (nov-mar); en verano habría 1 h de desfase
    return dt.datetime.now(tz).strftime("%Y-%m-%d")


def pacifico_de(iso: str) -> str:
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("America/Los_Angeles")
    except Exception:  # noqa: BLE001
        tz = dt.timezone(dt.timedelta(hours=-8))
    return dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(tz).strftime("%Y-%m-%d")


def subidas_hoy() -> int:
    p = COLA / "subidas.jsonl"
    if not p.exists():
        return 0
    hoy = hoy_pacifico()
    return sum(1 for l in p.read_text(encoding="utf-8").splitlines()
               if l.strip() and json.loads(l).get("ok") and pacifico_de(json.loads(l)["fecha"]) == hoy)


def http(req: urllib.request.Request, timeout: int = 60):
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def access_token(idioma: str) -> tuple[str, dict]:
    t = json.loads((SECRETOS / f"youtube_token_{idioma}.json").read_text(encoding="utf-8"))
    c = json.loads((SECRETOS / "youtube_client_secret.json").read_text(encoding="utf-8"))["installed"]
    req = urllib.request.Request(c["token_uri"], method="POST", data=urllib.parse.urlencode({
        "client_id": c["client_id"], "client_secret": c["client_secret"],
        "refresh_token": t["refresh_token"], "grant_type": "refresh_token"}).encode())
    st, _, cuerpo = http(req, 30)
    if st != 200:
        raise SystemExit(f"No se pudo renovar el token de {idioma.upper()} (HTTP {st}): {cuerpo.decode()[:200]}. "
                         "Repite subida\\autorizar_youtube.py.")
    return json.loads(cuerpo)["access_token"], t


def construir_metadatos(f: dict, privacidad: str) -> dict:
    titulo = re.sub(r"\s+", " ", (f.get("titulo_final") or f.get("titulo") or "Clip de GTA")).strip()
    if len(titulo) > 92:
        titulo = titulo[:91].rstrip() + "…"
    desc = (f.get("descripcion_youtube") or f.get("descripcion_tiktok") or "").strip()
    desc = re.sub(r"#\w+", "", desc).strip()  # los hashtags de Gemini repiten; añadimos los nuestros
    nombre = f.get("streamer_nombre") or f.get("creador") or ""
    credito = ""
    if f.get("fuente_url"):
        credito = (f"\n\nClip del directo de {nombre}: {f['fuente_url']}" if f["idioma"] == "es"
                   else f"\n\nClip from {nombre}'s stream: {f['fuente_url']}")
    juego = f.get("juego") or ("GTA VI" if re.search(r"gta\s*(vi|6)\b", f.get("titulo", "") + " " + (f.get("gancho") or ""), re.I) else "GTA")
    etiquetas = [t for t in (nombre, juego, "GTA", "Rockstar", "gameplay", "clips") if t]
    hashtags = f"\n\n#Shorts #{juego.replace(' ', '')} #GTA" + (f" #{re.sub(r'[^A-Za-z0-9_]', '', nombre)}" if nombre else "")
    return {
        "snippet": {"title": titulo, "description": (desc + credito + hashtags)[:4900], "tags": etiquetas,
                    "categoryId": CATEGORIA_GAMING,
                    "defaultLanguage": f["idioma"], "defaultAudioLanguage": f["idioma"]},
        "status": {"privacyStatus": privacidad, "selfDeclaredMadeForKids": False, "embeddable": True},
    }


def subir_video(token: str, ruta: Path, meta: dict) -> tuple[int, dict]:
    tam = ruta.stat().st_size
    ini = urllib.request.Request(
        "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
        method="POST", data=json.dumps(meta).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8",
                 "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(tam)})
    st, hdr, cuerpo = http(ini, 60)
    if st != 200:
        return st, json.loads(cuerpo.decode() or "{}")
    destino = next(v for k, v in hdr.items() if k.lower() == "location")
    put = urllib.request.Request(destino, method="PUT", data=ruta.read_bytes(),
                                 headers={"Content-Type": "video/mp4", "Content-Length": str(tam)})
    st, _, cuerpo = http(put, 900)
    return st, json.loads(cuerpo.decode() or "{}")


def poner_miniatura(token: str, video_id: str, imagen: Path) -> tuple[bool, str]:
    """thumbnails.set (50 unidades). Requiere canal verificado por teléfono; si falla, el vídeo queda igual."""
    req = urllib.request.Request(
        f"https://www.googleapis.com/upload/youtube/v3/thumbnails/set?videoId={video_id}&uploadType=media",
        method="POST", data=imagen.read_bytes(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "image/jpeg"})
    st, _, cuerpo = http(req, 120)
    if st == 200:
        return True, "ok"
    try:
        motivo = (json.loads(cuerpo.decode()).get("error", {}).get("errors") or [{}])[0].get("reason", "")
    except Exception:  # noqa: BLE001
        motivo = cuerpo.decode()[:150]
    return False, f"HTTP {st} {motivo}"


def elegir(pendientes: list[tuple[Path, dict]], n: int) -> list[tuple[Path, dict]]:
    """Prioridad = nota + 15 si es contenido picante; se reparte entre streamers (round-robin por prioridad)."""
    def prio(f: dict) -> int:
        return f.get("score", 0) + (15 if f.get("picante") else 0)
    por_directo: dict[str, list] = {}
    for p, f in sorted(pendientes, key=lambda t: -prio(t[1])):
        por_directo.setdefault(f.get("creador", ""), []).append((p, f))
    cola, elegidos = list(por_directo.values()), []
    while cola and len(elegidos) < n:
        cola.sort(key=lambda l: -prio(l[0][1]))
        elegidos.append(cola[0].pop(0))
        cola = [l for l in cola if l]
    return elegidos


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--subir", action="store_true", help="subir de verdad (sin esto solo simula)")
    ap.add_argument("--idioma", choices=["es", "en"], default="es")
    ap.add_argument("--max-dia", type=int, default=6)
    ap.add_argument("--privacidad", choices=["private", "unlisted", "public"], default="private")
    ap.add_argument("--max-por-pasada", type=int, default=0, help="como mucho N subidas en esta ejecución (0 = sin límite)")
    ap.add_argument("--min-intervalo", type=int, default=0,
                    help="minutos mínimos desde la última subida (se añade un margen aleatorio de 0-60 min)")
    a = ap.parse_args()
    if sys.stdout is None:  # pythonw (tarea programada)
        sys.stdout = sys.stderr = open(RAIZ / "datos" / "subir.log", "a", encoding="utf-8")
        print(f"--- {dt.datetime.now():%Y-%m-%d %H:%M}")
    for flujo in (sys.stdout, sys.stderr):
        try: flujo.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError: pass

    if a.min_intervalo and (COLA / "subidas.jsonl").exists():
        import random
        oks = [json.loads(l) for l in (COLA / "subidas.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
        oks = [o for o in oks if o.get("ok")]
        if oks:
            ultima = dt.datetime.fromisoformat(oks[-1]["fecha"].replace("Z", "+00:00"))
            espera = a.min_intervalo + random.randint(0, 60)   # horas de subida distintas cada día
            pasados = (dt.datetime.now(dt.timezone.utc) - ultima).total_seconds() / 60
            if pasados < espera:
                print(f"Última subida hace {pasados:.0f} min (espera {espera}): nada que hacer en esta pasada.")
                return 0

    hechas = subidas_hoy()
    hueco = max(0, a.max_dia - hechas)
    if a.max_por_pasada:
        hueco = min(hueco, a.max_por_pasada)
    pendientes = []
    for p in sorted((COLA / a.idioma).glob("*.json")):
        f = json.loads(p.read_text(encoding="utf-8-sig"))
        if f.get("estado_subida") != "pendiente":
            continue
        if not f.get("formato") or not f.get("titulo_final") or not f.get("miniatura"):
            continue  # aún no montado (plantilla streamer) o sin título/miniatura: lo termina el bucle continuo
        # OpenShorts borra sus archivos de E: a las 24 h: si ya no están, se sube la copia archivada en Google Drive.
        if not Path(f["video"]).exists() and f.get("video_drive") and Path(f["video_drive"]).exists():
            f["video"] = f["video_drive"]
            if f.get("miniatura_drive") and not Path(f.get("miniatura") or "").exists():
                f["miniatura"] = f["miniatura_drive"]
        if Path(f["video"]).exists():
            pendientes.append((p, f))
    print(f"Hoy (hora del Pacífico, {hoy_pacifico()}): {hechas} subidas hechas, tope {a.max_dia} -> hueco {hueco}. "
          f"Cuota estimada gastada: {hechas * COSTE_SUBIDA}/{CUOTA_DIA}.")
    print(f"Clips aprobados pendientes en {a.idioma.upper()}: {len(pendientes)}")
    elegidos = elegir(pendientes, hueco)
    if not elegidos:
        print("Nada que subir ahora."); return 0
    if not a.subir:
        print("\nSIMULACIÓN (añade --subir para subir de verdad):")
    token = None
    if a.subir:
        token, tinfo = access_token(a.idioma)
        print(f"Canal destino: {tinfo.get('canal_titulo')} {tinfo.get('canal_handle') or ''}")
    for p, f in elegidos:
        meta = construir_metadatos(f, a.privacidad)
        print(f"\n- [{f['score']}] {meta['snippet']['title']}  ({a.privacidad}, {Path(f['video']).stat().st_size >> 20} MB)")
        if not a.subir:
            continue
        st, resp = subir_video(token, Path(f["video"]), meta)
        reg = {"fecha": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "id": f["id"],
               "idioma": a.idioma, "ok": st in (200, 201), "http": st}
        if reg["ok"]:
            reg.update({"video_id": resp["id"], "url": f"https://www.youtube.com/shorts/{resp['id']}",
                        "privacidad_pedida": a.privacidad, "privacidad_real": resp.get("status", {}).get("privacyStatus")})
            f.update({"estado_subida": "subido", "youtube_id": resp["id"], "subido": reg["fecha"],
                      "titulo_publicado": meta["snippet"]["title"]})
            print(f"  subido: {reg['url']}  (privacidad real: {reg['privacidad_real']})")
            if f.get("miniatura") and Path(f["miniatura"]).exists():
                ok, info = poner_miniatura(token, resp["id"], Path(f["miniatura"]))
                reg["miniatura"] = info
                f["miniatura_puesta"] = ok
                print(f"  miniatura: {'puesta' if ok else 'NO puesta -> ' + info}")
            p.write_text(json.dumps(f, ensure_ascii=False, indent=2), encoding="utf-8")
        else:
            motivo = (resp.get("error", {}).get("errors") or [{}])[0].get("reason", "desconocido")
            reg["error"] = motivo
            print(f"  ERROR HTTP {st}: {motivo}")
        with open(COLA / "subidas.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(reg, ensure_ascii=False) + "\n")
        if not reg["ok"] and reg.get("error") in ("quotaExceeded", "dailyLimitExceeded", "uploadLimitExceeded"):
            print("  Cuota o límite de subidas agotado: paro; los clips siguen pendientes."); return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
