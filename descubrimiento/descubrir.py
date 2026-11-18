"""
Descubrimiento de fuentes de GTA (directos y vídeos largos) para los canales ES y EN.

Fuentes:
  - Twitch (Helix, token de app): directos en vivo de GTA V / GTA VI y VODs recientes
    (type=archive) de los streamers con más espectadores.
  - YouTube Data API v3: vídeos largos recientes y directos en vivo, por idioma.

Salida (en E:\\YoutubeClips\\datos\\descubrimiento):
  - candidatos_AAAAMMDD_HHMM.json : lista ordenada de vídeos/VODs candidatos por idioma.
  - streamers.json                : ranking acumulado de creadores por idioma (se actualiza en cada ejecución).
  - ultimo.json                   : copia del último candidatos_*.json (para que lo lea el resto del flujo).

Coste de cuota YouTube por ejecución (por defecto): 4 búsquedas x 100 = 400 unidades
+ unas pocas unidades de videos.list. El límite diario es 10.000 y cada subida cuesta 1.600:
no subir la frecuencia sin pensarlo.

Uso:
  python descubrir.py                 # ejecución normal
  python descubrir.py --sin-youtube   # solo Twitch (no gasta cuota de YouTube)
  python descubrir.py --dias 3        # ventana de vídeos de YouTube (por defecto 7 días)

Solo usa la biblioteca estándar. Las claves se leen de E:\\YoutubeClips\\.env y nunca se imprimen.
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
ENV_PATH = RAIZ / ".env"
SALIDA = RAIZ / "datos" / "descubrimiento"

IDIOMAS = ("es", "en")
JUEGOS_TWITCH = ("Grand Theft Auto V", "Grand Theft Auto VI")
# Debe aparecer alguno de estos en el título de YouTube para considerarlo de GTA.
PATRON_GTA = re.compile(r"\bgta\s*(v|5|vi|6|online|rp)?\b|grand theft auto", re.IGNORECASE)
# VODs cuyo título nombra otro juego (p. ej. "FIFA ... GTA 6 en 2 días"): fuera.
PATRON_OTROS = re.compile(
    r"\b(fifa|fut|fc\s?2\d|fortnite|warzone|mw\d|zelda|valorant|minecraft|lol|league|cs2|"
    r"apex|rocket league|steelers|nfl|nba|mbappe|potm)\b"
    # Cuenta atrás: el título habla de GTA pero aún no se está jugando.
    r"|\bsoon\b|\btmr\b|\b\d+\s+d[ií]as\b|\b\d+\s+days?\b|\bmenos de\b|\bhoy se descarga\b|\bsale el gta\b"
    r"|\bpreparados para\b|\bcountdown\b", re.IGNORECASE)
# Duración mínima para que merezca la pena sacar clips (segundos).
MIN_DURACION = 20 * 60
# Duración máxima de un VOD/vídeo: maratones de 24-48 h son decenas de GB y horas de proceso.
MAX_DURACION = 8 * 3600
# VODs de Twitch: cuántos streamers top por idioma y cuántos VODs por streamer.
TOP_STREAMERS_TWITCH = 8
VODS_POR_STREAMER = 2
MAX_PAGINAS_TWITCH = 10  # 100 directos por página


# --------------------------------------------------------------------------- utilidades
def leer_env(path: Path) -> dict[str, str]:
    datos: dict[str, str] = {}
    if not path.exists():
        return datos
    for linea in path.read_text(encoding="utf-8-sig").splitlines():
        m = re.match(r"^\s*([A-Za-z0-9_]+)\s*=\s*(.*)$", linea)
        if m:
            datos[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return datos


def http_json(url: str, *, headers: dict | None = None, data: dict | None = None) -> dict:
    cuerpo = urllib.parse.urlencode(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=cuerpo, headers=headers or {})
    req.add_header("User-Agent", "gta-clips-discovery/1.0")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detalle = e.read().decode("utf-8", "replace")[:300]
        # Nunca incluir la URL completa: podría llevar la clave en la query.
        raise RuntimeError(f"HTTP {e.code} en {urllib.parse.urlsplit(url).path}: {detalle}") from None


def duracion_twitch(s: str) -> int:
    """'3h12m5s' -> segundos."""
    total = 0
    for valor, unidad in re.findall(r"(\d+)([hms])", s or ""):
        total += int(valor) * {"h": 3600, "m": 60, "s": 1}[unidad]
    return total


def duracion_iso(s: str) -> int:
    """'PT2H3M4S' -> segundos (P0D en directos -> 0)."""
    m = re.match(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", s or "")
    if not m:
        return 0
    d, h, mi, se = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + se


def ahora_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def idioma_corto(codigo: str | None) -> str | None:
    if not codigo:
        return None
    return codigo.lower().split("-")[0]


# --------------------------------------------------------------------------- Twitch
class Twitch:
    def __init__(self, client_id: str, secret: str):
        tok = http_json(
            "https://id.twitch.tv/oauth2/token",
            data={"client_id": client_id, "client_secret": secret, "grant_type": "client_credentials"},
        )
        self.h = {"Client-Id": client_id, "Authorization": f"Bearer {tok['access_token']}"}

    def get(self, ruta: str, params: list[tuple[str, str]]) -> dict:
        return http_json(f"https://api.twitch.tv/helix/{ruta}?{urllib.parse.urlencode(params)}", headers=self.h)

    def juegos(self, nombres) -> dict[str, str]:
        r = self.get("games", [("name", n) for n in nombres])
        return {g["id"]: g["name"] for g in r.get("data", [])}

    def directos(self, game_ids, idioma: str) -> list[dict]:
        res, cursor = [], None
        for _ in range(MAX_PAGINAS_TWITCH):
            params = [("game_id", g) for g in game_ids] + [("language", idioma), ("first", "100")]
            if cursor:
                params.append(("after", cursor))
            r = self.get("streams", params)
            res.extend(r.get("data", []))
            cursor = r.get("pagination", {}).get("cursor")
            if not cursor or not r.get("data"):
                break
        return res

    def vods(self, user_id: str, n: int) -> list[dict]:
        # Se piden bastantes porque luego se filtran por título (Twitch no da el juego de cada VOD).
        r = self.get("videos", [("user_id", user_id), ("type", "archive"), ("first", "20")])
        return r.get("data", [])


def descubrir_twitch(tw: Twitch) -> tuple[list[dict], list[dict]]:
    juegos = tw.juegos(JUEGOS_TWITCH)
    print(f"[twitch] categorías encontradas: {', '.join(juegos.values()) or 'ninguna'}")
    candidatos, streamers = [], []
    for idioma in IDIOMAS:
        vivos = sorted(tw.directos(list(juegos), idioma), key=lambda s: s["viewer_count"], reverse=True)
        print(f"[twitch] {idioma}: {len(vivos)} directos en vivo")
        for s in vivos:
            streamers.append({
                "plataforma": "twitch", "idioma": idioma, "creador": s["user_name"],
                "creador_id": s["user_id"], "login": s["user_login"],
                "audiencia": s["viewer_count"], "juego": s["game_name"],
            })
            candidatos.append({
                "plataforma": "twitch", "tipo": "directo", "idioma": idioma,
                "creador": s["user_name"], "titulo": s["title"], "juego": s["game_name"],
                "url": f"https://www.twitch.tv/{s['user_login']}",
                "audiencia": s["viewer_count"], "duracion_s": None,
                "fecha": s["started_at"], "id": f"twitch-live-{s['id']}",
            })
        # VODs recientes de los más vistos: es lo que realmente se puede descargar entero.
        for s in vivos[:TOP_STREAMERS_TWITCH]:
            n = 0
            for v in tw.vods(s["user_id"], VODS_POR_STREAMER):
                dur = duracion_twitch(v["duration"])
                if not (MIN_DURACION <= dur <= MAX_DURACION):
                    continue
                # Twitch no dice a qué se jugó en cada VOD: solo se aceptan si el título menciona GTA.
                # (Se pierden VODs de RP con títulos sin "GTA", pero se evitan FIFA/Warzone/etc.)
                if not PATRON_GTA.search(v["title"]) or PATRON_OTROS.search(v["title"]):
                    continue
                # Un VOD que sigue grabándose (directo en curso) cambia de duración: se procesa cuando acabe.
                if v.get("stream_id") == s["id"]:
                    continue
                candidatos.append({
                    "plataforma": "twitch", "tipo": "vod", "idioma": idioma,
                    "creador": v["user_name"], "titulo": v["title"],
                    "juego": "GTA VI" if re.search(r"gta\s*(vi|6)\b", v["title"], re.I) else "GTA",
                    "url": v["url"], "audiencia": v["view_count"], "duracion_s": dur,
                    "fecha": v["created_at"], "id": f"twitch-vod-{v['id']}",
                })
                n += 1
                if n >= VODS_POR_STREAMER:
                    break
    return candidatos, streamers


# --------------------------------------------------------------------------- YouTube
class YouTube:
    def __init__(self, key: str):
        self.h = {"x-goog-api-key": key}  # la clave va en cabecera, no en la URL
        self.unidades = 0

    def get(self, ruta: str, params: dict, coste: int) -> dict:
        self.unidades += coste
        return http_json(f"https://www.googleapis.com/youtube/v3/{ruta}?{urllib.parse.urlencode(params)}", headers=self.h)

    def buscar(self, **params) -> list[str]:
        base = {"part": "id", "type": "video", "maxResults": 50, "q": "GTA V|GTA 5|GTA VI|GTA 6|GTA RP"}
        base.update(params)
        r = self.get("search", base, 100)
        return [i["id"]["videoId"] for i in r.get("items", [])]

    def detalles(self, ids: list[str]) -> list[dict]:
        res = []
        for i in range(0, len(ids), 50):
            r = self.get("videos", {
                "part": "snippet,contentDetails,statistics,liveStreamingDetails",
                "id": ",".join(ids[i:i + 50]),
            }, 1)
            res.extend(r.get("items", []))
        return res


def descubrir_youtube(yt: YouTube, dias: int) -> tuple[list[dict], list[dict]]:
    desde = (ahora_utc() - dt.timedelta(days=dias)).strftime("%Y-%m-%dT%H:%M:%SZ")
    candidatos, streamers = [], []
    for idioma in IDIOMAS:
        largos = yt.buscar(relevanceLanguage=idioma, videoDuration="long", order="viewCount", publishedAfter=desde)
        vivos = yt.buscar(relevanceLanguage=idioma, eventType="live", order="viewCount")
        print(f"[youtube] {idioma}: {len(largos)} vídeos largos, {len(vivos)} directos (antes de filtrar)")
        for v in yt.detalles(list(dict.fromkeys(largos + vivos))):
            sn, st = v["snippet"], v.get("statistics", {})
            titulo = sn.get("title", "")
            if not PATRON_GTA.search(titulo):
                continue
            # relevanceLanguage solo "prioriza": filtramos por el idioma declarado si existe.
            decl = idioma_corto(sn.get("defaultAudioLanguage") or sn.get("defaultLanguage"))
            if decl and decl != idioma:
                continue
            en_vivo = sn.get("liveBroadcastContent") == "live"
            dur = duracion_iso(v.get("contentDetails", {}).get("duration"))
            if not en_vivo and not (MIN_DURACION <= dur <= MAX_DURACION):
                continue
            lsd = v.get("liveStreamingDetails", {})
            audiencia = int(lsd.get("concurrentViewers") or 0) if en_vivo else int(st.get("viewCount") or 0)
            candidatos.append({
                "plataforma": "youtube", "tipo": "directo" if en_vivo else ("directo_grabado" if lsd else "video"),
                "idioma": idioma, "idioma_declarado": decl, "creador": sn.get("channelTitle"),
                "titulo": titulo, "juego": "GTA VI" if re.search(r"gta\s*(vi|6)\b", titulo, re.I) else "GTA",
                "url": f"https://www.youtube.com/watch?v={v['id']}",
                "audiencia": audiencia, "duracion_s": dur or None,
                "fecha": sn.get("publishedAt"), "id": f"youtube-{v['id']}",
            })
            streamers.append({
                "plataforma": "youtube", "idioma": idioma, "creador": sn.get("channelTitle"),
                "creador_id": sn.get("channelId"), "login": sn.get("channelId"),
                "audiencia": audiencia, "juego": "GTA",
            })
    return candidatos, streamers


# --------------------------------------------------------------------------- ranking acumulado
def actualizar_streamers(path: Path, nuevos: list[dict], marca: str) -> dict:
    datos = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    for s in nuevos:
        clave = f"{s['plataforma']}:{s['creador_id']}"
        e = datos.setdefault(clave, {
            "plataforma": s["plataforma"], "creador": s["creador"], "login": s["login"],
            "idioma": s["idioma"], "visto_veces": 0, "audiencia_max": 0, "audiencia_suma": 0,
            "primera_vez": marca,
        })
        e["creador"] = s["creador"]
        e["visto_veces"] += 1
        e["audiencia_max"] = max(e["audiencia_max"], s["audiencia"])
        e["audiencia_suma"] += s["audiencia"]
        e["ultima_vez"] = marca
    path.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")
    return datos


# --------------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sin-youtube", action="store_true", help="no consultar YouTube (ahorra cuota)")
    ap.add_argument("--sin-twitch", action="store_true", help="no consultar Twitch")
    ap.add_argument("--dias", type=int, default=7, help="antigüedad máxima de vídeos de YouTube (días)")
    args = ap.parse_args()
    # La consola de Windows usa cp1252 y revienta con emojis de los títulos.
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            pass

    env = leer_env(ENV_PATH)
    SALIDA.mkdir(parents=True, exist_ok=True)
    marca = ahora_utc().strftime("%Y-%m-%dT%H:%M:%SZ")
    candidatos, streamers, errores = [], [], []

    if not args.sin_twitch:
        if env.get("TWITCH_CLIENT_ID") and env.get("TWITCH_CLIENT_SECRET"):
            try:
                c, s = descubrir_twitch(Twitch(env["TWITCH_CLIENT_ID"], env["TWITCH_CLIENT_SECRET"]))
                candidatos += c; streamers += s
            except Exception as e:  # noqa: BLE001 - una fuente caída no debe tumbar la otra
                errores.append(f"twitch: {e}")
        else:
            errores.append("twitch: faltan TWITCH_CLIENT_ID / TWITCH_CLIENT_SECRET en .env")

    if not args.sin_youtube:
        if env.get("YOUTUBE_API_KEY"):
            yt = YouTube(env["YOUTUBE_API_KEY"])
            try:
                c, s = descubrir_youtube(yt, args.dias)
                candidatos += c; streamers += s
            except Exception as e:  # noqa: BLE001
                errores.append(f"youtube: {e}")
            print(f"[youtube] cuota gastada aprox.: {yt.unidades} unidades")
        else:
            errores.append("youtube: falta YOUTUBE_API_KEY en .env")

    # Sin duplicados; orden: idioma, luego VOD/vídeo (descargables) antes que directos, luego audiencia.
    vistos, unicos = set(), []
    for c in candidatos:
        if c["id"] not in vistos:
            vistos.add(c["id"]); unicos.append(c)
    prioridad = {"vod": 0, "video": 0, "directo_grabado": 0, "directo": 1}
    unicos.sort(key=lambda c: (c["idioma"], prioridad.get(c["tipo"], 2), -(c["audiencia"] or 0)))

    resultado = {
        "generado": marca,
        "resumen": {i: {t: sum(1 for c in unicos if c["idioma"] == i and c["tipo"] == t)
                        for t in ("vod", "video", "directo_grabado", "directo")} for i in IDIOMAS},
        "errores": errores,
        "candidatos": unicos,
    }
    nombre = SALIDA / f"candidatos_{ahora_utc().strftime('%Y%m%d_%H%M')}.json"
    texto = json.dumps(resultado, ensure_ascii=False, indent=2)
    nombre.write_text(texto, encoding="utf-8")
    (SALIDA / "ultimo.json").write_text(texto, encoding="utf-8")
    ranking = actualizar_streamers(SALIDA / "streamers.json", streamers, marca)

    print(f"\nCandidatos: {len(unicos)} -> {nombre}")
    for i in IDIOMAS:
        print(f"  {i.upper()}: {resultado['resumen'][i]}")
        top = sorted((v for v in ranking.values() if v["idioma"] == i), key=lambda v: -v["audiencia_max"])[:5]
        print("    top creadores: " + ", ".join(f"{v['creador']} ({v['plataforma']}, {v['audiencia_max']})" for v in top))
    for e in errores:
        print(f"[ERROR] {e}", file=sys.stderr)
    return 1 if errores and not unicos else 0


if __name__ == "__main__":
    sys.exit(main())
