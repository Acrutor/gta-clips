"""
Orquestador de grabaciones: mantiene grabando a los streamers de GTA más vistos hasta llegar al objetivo diario de clips.

Cada pasada (la lanza la tarea programada cada 10 min):
  1. Cuenta los clips APROBADOS hoy (datos\\cola\\decisiones.json).
  2. Si ya se llegó a `objetivo_clips_dia`: pide a los grabadores en marcha que paren (envían su último tramo).
  3. Si no: consulta Twitch (directos de los juegos de config.json en el idioma elegido, por espectadores) y arranca
     grabadores para los mejores hasta tener `max_grabaciones_simultaneas` en marcha.
  4. Limpia los contenedores de grabadores que ya terminaron (su directo acabó), para poder volver a grabarlos otro día.

Config: E:\\YoutubeClips\\directos\\config.json (se relee en cada pasada).
Registro: E:\\YoutubeClips\\datos\\orquestador.log cuando se lanza con pythonw.
Solo biblioteca estándar; usa el CLI de docker del sistema.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

RAIZ = Path(r"E:\YoutubeClips")
sys.path.insert(0, str(RAIZ / "descubrimiento"))
import descubrir as dsc  # noqa: E402  (reutiliza el cliente de Twitch y la lectura del .env)

CONFIG = RAIZ / "directos" / "config.json"
DECISIONES = RAIZ / "datos" / "cola" / "decisiones.json"
DIRECTOS = RAIZ / "datos" / "directos"
DOCKER = shutil.which("docker") or r"C:\Program Files\Docker\Docker\resources\bin\docker.exe"


def docker(*args: str) -> str:
    r = subprocess.run([DOCKER, *args], capture_output=True, text=True, timeout=120,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if r.returncode != 0:
        raise RuntimeError(f"docker {' '.join(args[:2])}: {r.stderr.strip()[:200]}")
    return r.stdout.strip()


def aprobados_hoy() -> int:
    if not DECISIONES.exists():
        return 0
    hoy = dt.date.today()
    n = 0
    for v in json.loads(DECISIONES.read_text(encoding="utf-8")).values():
        if v.get("estado") == "aprobado":
            cuando = dt.datetime.fromisoformat(v["decidido"].replace("Z", "+00:00")).astimezone().date()
            n += cuando == hoy
    return n


def grabadores() -> dict[str, str]:
    """{nombre_contenedor: estado} de todos los grabadores (en marcha o terminados)."""
    salida = docker("ps", "-a", "--filter", "name=grabador-", "--filter", "name=vod-", "--format", "{{.Names}}|{{.State}}")
    return dict(l.split("|", 1) for l in salida.splitlines() if "|" in l)


def pedir_parada(login: str) -> None:
    """Crea PARAR en las carpetas del canal que aún no han terminado (el grabador envía su último tramo y sale)."""
    for d in [DIRECTOS / login, *DIRECTOS.glob(f"{login}_*")]:
        if not d.is_dir():
            continue
        est = d / "estado.json"
        terminado = est.exists() and json.loads(est.read_text(encoding="utf-8")).get("terminado")
        if not terminado:
            (d / "PARAR").touch()


def arrancar(login: str, idioma: str) -> None:
    docker("run", "-d", "--name", f"grabador-{login}", "--network", "openshorts_default",
           "--restart", "on-failure:3", "--no-healthcheck", "-v", "E:/YoutubeClips:/yc", "--entrypoint", "python",
           "openshorts-backend", "-u", "/yc/directos/grabar_directo.py", login, "--idioma", idioma)


ESTADO_ORQ = RAIZ / "datos" / "orquestador_estado.json"
VODS_HECHOS = RAIZ / "datos" / "vods_procesados.json"


def respaldo_vods(cfg, tw, activos, elegibles, faltan, solo, nunca) -> None:
    """Si nadie de GTA está en directo desde hace `min_sin_directos_para_vods` minutos y faltan clips, saca tramos de
    VODs recientes de Twitch (y si no hay, de vídeos largos de YouTube) de los streamers conocidos."""
    est = json.loads(ESTADO_ORQ.read_text(encoding="utf-8")) if ESTADO_ORQ.exists() else {}
    ahora = dt.datetime.now(dt.timezone.utc)
    if elegibles or [n for n in activos if n.startswith("grabador-")]:
        est.pop("sin_directos_desde", None)
        ESTADO_ORQ.write_text(json.dumps(est), encoding="utf-8")
        return
    est.setdefault("sin_directos_desde", ahora.isoformat())
    ESTADO_ORQ.write_text(json.dumps(est), encoding="utf-8")
    espera = (ahora - dt.datetime.fromisoformat(est["sin_directos_desde"])).total_seconds() / 60
    if espera < cfg.get("min_sin_directos_para_vods", 30) or faltan <= 0:
        print(f"sin directos desde hace {espera:.0f} min (respaldo VOD a los {cfg.get('min_sin_directos_para_vods', 30)})")
        return
    if any(n.startswith("vod-") for n in activos):
        return  # ya hay uno procesando: de uno en uno para no saturar la GPU
    hechos = set(json.loads(VODS_HECHOS.read_text(encoding="utf-8"))) if VODS_HECHOS.exists() else set()
    idioma = cfg.get("idioma", "es")
    candidatos = []
    # 1) VODs de Twitch de los streamers que ya conocemos (por audiencia máxima vista), con título de GTA
    sp = RAIZ / "datos" / "descubrimiento" / "streamers.json"
    conocidos = json.loads(sp.read_text(encoding="utf-8")) if sp.exists() else {}
    tw_streamers = sorted((v | {"id": k.split(":", 1)[1]} for k, v in conocidos.items()
                           if k.startswith("twitch:") and v.get("idioma") == idioma), key=lambda v: -v["audiencia_max"])
    for v in tw_streamers[:15]:
        login = v["login"].lower()
        if login in nunca or (solo and login not in solo):
            continue
        for vod in tw.vods(v["id"], 10):
            dur = dsc.duracion_twitch(vod["duration"])
            creado = dt.datetime.fromisoformat(vod["created_at"].replace("Z", "+00:00"))
            if vod["id"] in hechos or dur < 40 * 60 or (ahora - creado).days > 7:
                continue
            if not dsc.PATRON_GTA.search(vod["title"]) or dsc.PATRON_OTROS.search(vod["title"]):
                continue
            # no repetir lo que ya grabamos en directo ese día
            if (DIRECTOS / f"{login}_{creado.astimezone():%Y%m%d}").exists():
                continue
            candidatos.append((vod["view_count"], vod["url"], login, vod["id"]))
    # 2) si no hay VODs: vídeos largos de YouTube de la última búsqueda de descubrimiento
    if not candidatos:
        up = RAIZ / "datos" / "descubrimiento" / "ultimo.json"
        if up.exists():
            for c in json.loads(up.read_text(encoding="utf-8")).get("candidatos", []):
                if c["plataforma"] == "youtube" and c["tipo"] in ("video", "directo_grabado") and c["idioma"] == idioma \
                        and c["id"] not in hechos and (c.get("duracion_s") or 0) >= 15 * 60:
                    candidatos.append((c.get("audiencia", 0), c["url"], re.sub(r"\W+", "", c["creador"]).lower()[:30], c["id"]))
    if not candidatos:
        print("respaldo VOD: no hay vídeos candidatos"); return
    vistas, url, login, vid = max(candidatos)
    tramos = max(2, min(8, faltan))
    docker("run", "-d", "--name", f"vod-{login}-{vid}"[:60], "--network", "openshorts_default",
           "--no-healthcheck", "-v", "E:/YoutubeClips:/yc", "--entrypoint", "python", "openshorts-backend",
           "-u", "/yc/directos/vod_tramos.py", url, "--canal", login, "--idioma", idioma, "--tramos", str(tramos))
    hechos.add(vid)
    VODS_HECHOS.write_text(json.dumps(sorted(hechos), indent=0), encoding="utf-8")
    print(f"respaldo VOD: sin directos desde hace {espera:.0f} min -> {tramos} tramos de {url} ({login}, {vistas} vistas)")


def main() -> int:
    if sys.stdout is None:  # pythonw (tarea programada)
        sys.stdout = sys.stderr = open(RAIZ / "datos" / "orquestador.log", "a", encoding="utf-8")
    print(f"--- {dt.datetime.now():%Y-%m-%d %H:%M}")
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    try:
        estado = grabadores()
    except Exception as e:  # noqa: BLE001 - Docker Desktop apagado: no hay nada que hacer
        print(f"Docker no responde: {e}"); return 1

    # 4) limpiar grabadores terminados
    for nombre, st in estado.items():
        if st in ("exited", "dead"):
            docker("rm", nombre); print(f"limpio {nombre} (terminado)")
    activos = [n for n, st in estado.items() if st in ("running", "restarting", "created")]

    hechos = aprobados_hoy()
    objetivo = int(cfg.get("objetivo_clips_dia", 30))
    print(f"aprobados hoy: {hechos}/{objetivo} | grabando: {', '.join(activos) or 'nadie'}")
    if hechos >= objetivo:
        for n in activos:
            if n.startswith("grabador-"):
                pedir_parada(n.removeprefix("grabador-")); print(f"objetivo cumplido: pido parar {n}")
        return 0

    grab_activos = [n for n in activos if n.startswith("grabador-")]
    hueco = int(cfg.get("max_grabaciones_simultaneas", 3)) - len(grab_activos)
    env = dsc.leer_env(dsc.ENV_PATH)
    tw = dsc.Twitch(env["TWITCH_CLIENT_ID"], env["TWITCH_CLIENT_SECRET"])
    juegos = tw.juegos(cfg.get("juegos", ["Grand Theft Auto VI"]))
    vivos = sorted(tw.directos(list(juegos), cfg.get("idioma", "es")), key=lambda s: -s["viewer_count"])
    solo = {x.lower() for x in cfg.get("solo_estos_streamers", [])}
    nunca = {x.lower() for x in cfg.get("nunca_estos_streamers", [])}
    elegibles = [s for s in vivos if s["viewer_count"] >= cfg.get("min_espectadores", 0)
                 and s["user_login"].lower() not in nunca and (not solo or s["user_login"].lower() in solo)]
    respaldo_vods(cfg, tw, activos, elegibles, objetivo - hechos, solo, nunca)
    if hueco <= 0:
        return 0
    for s in vivos:
        if hueco <= 0:
            break
        login = s["user_login"].lower()
        if s["viewer_count"] < cfg.get("min_espectadores", 0) or login in nunca or (solo and login not in solo):
            continue
        if f"grabador-{login}" in activos:
            continue
        try:
            arrancar(login, cfg.get("idioma", "es"))
            print(f"arranco grabador-{login} ({s['user_name']}, {s['viewer_count']} espectadores, {s['game_name']})")
            hueco -= 1
        except Exception as e:  # noqa: BLE001
            print(f"no pude arrancar {login}: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
