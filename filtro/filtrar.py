"""
Filtro automático de calidad de clips de OpenShorts.  Decide, sin intervención humana, qué clips pasan
a la cola de subida de cada canal (ES / EN).

Cada clip pasa por estas puertas (la primera que falla lo rechaza; se guardan TODOS los motivos que se detectan):
  1. duración           10-90 s
  2. puntuación         predicted_score de OpenShorts >= --min-score (70 por defecto)
  3. voz                palabras/segundo del clip >= --min-voz (0.6 por defecto; descarta clips sin habla)
  4. idioma             >= 60 % de las palabras reconocibles en el idioma del canal (ES/EN)
  5. insultos           lista de insultos graves -> rechazo; palabrotas comunes > 3 -> rechazo
  6. duplicados         solape de tiempo > 50 % con otro clip ya aprobado del MISMO directo (gana el de más nota)
  7. IA (Gemini)        mira 3 fotogramas + título/gancho/texto: pantalla de carga o menú, tema político/sensible,
                        contenido no apto, calidad 1-10 (>= --min-calidad). Si Gemini falla, el clip queda
                        «pendiente» y se reintenta en la siguiente pasada (nunca se aprueba a ciegas).

Salida (en /yc/datos/cola = E:\\YoutubeClips\\datos\\cola):
  es/ , en/        un .json por clip aprobado (ruta del vídeo, título, descripciones, fuente, creador, motivo)
  rechazados.jsonl un registro por clip rechazado con sus motivos
  decisiones.json  estado por clip (aprobado / rechazado / pendiente): hace el script idempotente

Se ejecuta dentro de un contenedor (filtrar.ps1). Uso directo:
  python filtrar.py <job_id> [<job_id> ...]      # jobs concretos
  python filtrar.py --todos                      # todos los jobs de los grabadores de directos
"""
from __future__ import annotations

import argparse
import base64
import glob
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests

BACKEND = os.environ.get("OPENSHORTS_URL", "http://openshorts-backend:8000")
YC = Path(os.environ.get("YC_RAIZ", "/yc"))
OUT_JOBS = YC / "openshorts" / "output"
COLA = YC / "datos" / "cola"
DIRECTOS = YC / "datos" / "directos"
# Nota (18-nov-2026): gemini-2.5-* devuelve 404 aunque aparezca en el listado; este es el que usa OpenShorts.
GEMINI_MODELO = os.environ.get("GEMINI_FILTRO_MODELO", "gemini-3.1-flash-lite")

# ----------------------------------------------------------------------------- léxico
STOP = {
    "es": set("el la los las un una unos unas de del al que y en es no se lo me te mi tu su por con para pero como "
              "más muy ya sí si qué este esta eso esto aquí ahí vale tío joder hay he has ha son era fue está estoy "
              "vamos venga bueno entonces porque cuando donde también todo nada algo así yo él ella nos les".split()),
    "en": set("the a an of to and in is it that you i he she we they this that was were are be been have has had do "
              "does did not no yes on for with as at by from or but if so what when where how why can will just "
              "like my your his her our their there here get got go going oh okay yeah dude man bro".split()),
}
# Insultos graves (racistas, homófobos, capacitistas, amenazas): rechazo directo.
GRAVES = re.compile(
    r"\b(n[i1]gg(?:a|er)s?|f[a4]gg?(?:ot)?s?|tr[a4]nn(?:y|ies)|ret[a4]rd(?:ed|s)?|kike|ch[i1]nk|sp[i1]c|"
    r"negrata|sudaca|maric[oó]n(?:es)?|subnormal(?:es)?|mongol(?:o|os)?|puto\s+(?:negro|moro|gitano)|"
    r"kill\s+yourself|kys|su[ií]cidate)\b", re.IGNORECASE)
# Palabrotas comunes: se permiten pocas (YouTube limita la monetización con mucha profanidad).
COMUNES = re.compile(
    r"\b(fuck\w*|shit\w*|bitch\w*|asshole|dick|cunt|bastard|joder|jodid\w*|mierda|co[ñn]o|cabr[oó]n\w*|"
    r"hijo\s?de\s?puta|put[oa]s?|gilipollas|capullo|pollas?|follar|me\s+cago)\b", re.IGNORECASE)
MAX_COMUNES = 3


def palabras(texto: str) -> list[str]:
    return re.findall(r"[a-záéíóúüñ']+", texto.lower())


def idioma_stats(texto: str) -> dict:
    """Porcentaje de palabras reconocibles que pertenecen a cada idioma."""
    ws = palabras(texto)
    es = sum(1 for w in ws if w in STOP["es"])
    en = sum(1 for w in ws if w in STOP["en"])
    tot = es + en
    return {"es": round(es / tot, 2) if tot else 0.0, "en": round(en / tot, 2) if tot else 0.0, "reconocidas": tot}


def texto_del_clip(transcript: dict, ini: float, fin: float) -> tuple[str, int]:
    """Texto y número de palabras de la transcripción dentro de [ini, fin]."""
    trozos, n = [], 0
    for seg in transcript.get("segments", []):
        for w in seg.get("words") or []:
            if w.get("start", 0) >= ini - 0.05 and w.get("end", 0) <= fin + 0.05:
                trozos.append(w["word"].strip()); n += 1
    if n == 0:  # transcripción sin palabras sueltas: usar segmentos enteros
        for seg in transcript.get("segments", []):
            if seg["start"] >= ini - 0.5 and seg["end"] <= fin + 0.5:
                trozos.append(seg["text"].strip()); n += len(palabras(seg["text"]))
    return " ".join(trozos), n


# ----------------------------------------------------------------------------- contexto del job
def fuente_del_job(job_id: str) -> dict | None:
    """Busca a qué directo/tramo pertenece un job (a partir de los estado.json de los grabadores)."""
    for p in glob.glob(str(DIRECTOS / "*" / "estado.json")):
        e = json.loads(Path(p).read_text(encoding="utf-8"))
        for t in e.get("tramos", []):
            if t.get("job_id") == job_id:
                return {"canal": e["canal"], "url": e.get("url"), "idioma": e.get("idioma", "es"),
                        "offset_s": t.get("offset_s", 0), "tramo": t["n"], "directo": Path(p).parent.name}
    return None


def jobs_de_directos() -> list[str]:
    res = []
    for p in glob.glob(str(DIRECTOS / "*" / "estado.json")):
        res += [t["job_id"] for t in json.loads(Path(p).read_text(encoding="utf-8")).get("tramos", []) if t.get("job_id")]
    return res


def cargar_job(job_id: str) -> dict | None:
    r = requests.get(f"{BACKEND}/api/status/{job_id}", timeout=30)
    r.raise_for_status()
    d = r.json()
    if d.get("status") != "completed":
        return None
    meta = glob.glob(str(OUT_JOBS / job_id / "*_metadata.json"))
    transcript = json.loads(Path(meta[0]).read_text(encoding="utf-8")).get("transcript", {}) if meta else {}
    return {"clips": (d.get("result") or {}).get("clips", []), "transcript": transcript}


def ruta_video(video_url: str) -> Path:
    # video_url = /videos/<job>/<archivo>  ->  openshorts/output/<job>/<archivo> (viene urlencoded)
    from urllib.parse import unquote
    partes = unquote(video_url).split("/", 3)
    return OUT_JOBS / partes[2] / partes[3]


# ----------------------------------------------------------------------------- IA (Gemini)
def fotogramas(video: Path, dur: float) -> list[bytes]:
    imgs = []
    with tempfile.TemporaryDirectory() as td:
        for i, f in enumerate((0.15, 0.5, 0.85)):
            out = Path(td) / f"f{i}.jpg"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{dur * f:.2f}", "-i", str(video),
                            "-frames:v", "1", "-vf", "scale=480:-1", "-q:v", "4", str(out)], check=False)
            if out.exists():
                imgs.append(out.read_bytes())
    return imgs


def revisar_con_gemini(clave: str, imgs: list[bytes], clip: dict, texto: str, idioma_canal: str) -> dict:
    prompt = (
        "Eres el control de calidad de un canal de Shorts de GTA (juego). Te paso 3 fotogramas de un clip vertical, "
        f"su título, el gancho y la transcripción. El canal es en {'español' if idioma_canal == 'es' else 'inglés'}.\n"
        f"Título: {clip.get('video_title_for_youtube_short')}\nGancho: {clip.get('viral_hook_text')}\n"
        f"Transcripción: {texto[:1500]}\n\n"
        "Responde SOLO con JSON con estas claves: "
        "pantalla_carga_o_menu (true si se ve sobre todo una pantalla de carga, menú, mapa o pausa), "
        "poca_accion (true si no pasa nada interesante), "
        "tema_politico_o_sensible (true si el título/gancho/texto trata política real, religión, tragedias o violencia REAL "
        "(no la del juego)), insulto_o_odio (true si hay insultos graves, odio o acoso), "
        "picante (true si el momento es sugerente o subido de tono DENTRO del juego: club de striptease, bailes, ligoteo, "
        "citas, bromas sexuales, personajes en ropa interior o bañador; es contenido deseado), "
        "desnudez_explicita (true SOLO si se ven genitales, pezones o un acto sexual explícito), "
        "marca_ajena_visible (true si se ve el logo o URL de otro creador/canal distinto del streamer), "
        "calidad (entero 1-10: ¿es un buen momento para un Short? gracia, acción, claridad), motivo (frase corta)."
    )
    partes = [{"text": prompt}] + [{"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(i).decode()}} for i in imgs]
    cuerpo = {"contents": [{"parts": partes}],
              "generationConfig": {"responseMimeType": "application/json", "temperature": 0.1}}
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODELO}:generateContent"
    for intento in range(3):
        r = requests.post(url, headers={"x-goog-api-key": clave}, json=cuerpo, timeout=90)
        if r.status_code in (429, 500, 503):
            time.sleep(10 * (intento + 1)); continue
        r.raise_for_status()
        txt = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(txt)
    raise RuntimeError(f"Gemini no disponible (HTTP {r.status_code})")


def leer_clave() -> str | None:
    env = YC / ".env"
    if env.exists():
        for linea in env.read_text(encoding="utf-8-sig").splitlines():
            m = re.match(r"^\s*GEMINI_API_KEY\s*=\s*(.*)$", linea)
            if m:
                return m.group(1).strip().strip('"').strip("'")
    return os.environ.get("GEMINI_API_KEY")


# ----------------------------------------------------------------------------- decisión
def evaluar(clip: dict, ctx: dict, args, aprobados_directo: list[dict], clave: str | None) -> dict:
    idioma = ctx["idioma"]
    ini, fin = clip["start"], clip["end"]
    dur = fin - ini
    texto, n_pal = texto_del_clip(ctx["transcript"], ini, fin)
    motivos, avisos, datos = [], [], {"duracion_s": round(dur, 1), "score": clip.get("predicted_score")}

    if not (10 <= dur <= 90):
        motivos.append(f"duracion_{round(dur)}s_fuera_de_10-90")
    if (clip.get("predicted_score") or 0) < args.min_score:
        motivos.append(f"puntuacion_{clip.get('predicted_score')}_<_{args.min_score}")
    pps = n_pal / dur if dur else 0
    datos["palabras_por_s"] = round(pps, 2)
    if pps < args.min_voz:
        motivos.append(f"sin_voz_{round(pps, 2)}_palabras_por_s")
    st = idioma_stats(texto)
    datos["idioma"] = st
    if st["reconocidas"] >= 8 and st[idioma] < 0.6:
        motivos.append(f"idioma_{'EN' if idioma == 'es' else 'ES'}_dominante_({round(st[idioma] * 100)}%_{idioma.upper()})")
    graves = sorted({m.group(0).lower() for m in GRAVES.finditer(texto)})
    if graves:
        motivos.append("insulto_grave")
    comunes = [m.group(0).lower() for m in COMUNES.finditer(texto)]
    datos["palabrotas"] = len(comunes)
    if len(comunes) > MAX_COMUNES:
        motivos.append(f"demasiadas_palabrotas_{len(comunes)}")
    elif comunes:
        avisos.append(f"palabrotas_{len(comunes)}")

    # Duplicados: tiempo absoluto dentro del directo (offset del tramo + inicio del clip en el tramo).
    a_ini, a_fin = ctx["offset_s"] + ini, ctx["offset_s"] + fin
    for o in aprobados_directo:
        inter = min(a_fin, o["abs_fin"]) - max(a_ini, o["abs_ini"])
        if inter > 0.5 * min(a_fin - a_ini, o["abs_fin"] - o["abs_ini"]):
            if (clip.get("predicted_score") or 0) <= o["score"]:
                motivos.append(f"duplicado_de_{o['id']}")
            break
    if motivos:
        return {"estado": "rechazado", "motivos": motivos, "avisos": avisos, "datos": datos, "abs": (a_ini, a_fin)}

    # IA: solo si lo anterior ha pasado (ahorra cuota de Gemini).
    if not args.sin_gemini:
        if not clave:
            return {"estado": "pendiente", "motivos": ["sin_clave_gemini"], "avisos": avisos, "datos": datos, "abs": (a_ini, a_fin)}
        try:
            ia = revisar_con_gemini(clave, fotogramas(ruta_video(clip["video_url"]), dur), clip, texto, idioma)
        except Exception as e:  # noqa: BLE001 - no se aprueba a ciegas: se reintenta luego
            return {"estado": "pendiente", "motivos": [f"gemini_error:{str(e)[:120]}"], "avisos": avisos, "datos": datos, "abs": (a_ini, a_fin)}
        datos["ia"] = ia
        picante = bool(ia.get("picante"))
        datos["picante"] = picante
        if ia.get("desnudez_explicita"):
            # Política de YouTube sobre contenido sexual (aplica también a videojuegos): retirada o restricción de edad,
            # y un Short con restricción de edad apenas se distribuye. Lo sugerente sí pasa (y se prioriza).
            motivos.append("desnudez_o_sexo_explicito")
        if ia.get("pantalla_carga_o_menu"): motivos.append("pantalla_de_carga_o_menu")
        if ia.get("poca_accion") and not picante: motivos.append("poca_accion")
        if ia.get("tema_politico_o_sensible"): motivos.append("tema_politico_o_sensible")
        if ia.get("insulto_o_odio"): motivos.append("insulto_o_odio")
        min_cal = args.min_calidad - (1 if picante else 0)   # prioridad: al contenido picante se le exige un punto menos
        if int(ia.get("calidad") or 0) < min_cal: motivos.append(f"calidad_ia_{ia.get('calidad')}_<_{min_cal}")
        if ia.get("marca_ajena_visible"): avisos.append("marca_ajena_visible")
    return {"estado": "rechazado" if motivos else "aprobado", "motivos": motivos, "avisos": avisos,
            "datos": datos, "abs": (a_ini, a_fin)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("jobs", nargs="*")
    ap.add_argument("--todos", action="store_true")
    ap.add_argument("--min-score", type=int, default=70)
    ap.add_argument("--min-calidad", type=int, default=6)
    ap.add_argument("--min-voz", type=float, default=0.6,
                    help="palabras/segundo mínimas (18-nov: bajado de 1.0 a 0.6 para no perder momentos de acción)")
    ap.add_argument("--sin-gemini", action="store_true")
    ap.add_argument("--idioma", choices=["es", "en"], help="para jobs que no vienen de un grabador")
    ap.add_argument("--creador", default=None)
    ap.add_argument("--reevaluar", action="store_true", help="ignorar decisiones previas")
    args = ap.parse_args()

    jobs = args.jobs or (jobs_de_directos() if args.todos else [])
    if not jobs:
        ap.error("indica job_id o --todos")
    for i in ("es", "en"):
        (COLA / i).mkdir(parents=True, exist_ok=True)
    dec_path = COLA / "decisiones.json"
    decisiones = json.loads(dec_path.read_text(encoding="utf-8")) if dec_path.exists() else {}
    clave = leer_clave()
    resumen = {"aprobado": 0, "rechazado": 0, "pendiente": 0}

    # aprobados previos, por directo, para detectar duplicados entre tramos solapados
    por_directo: dict[str, list[dict]] = {}
    for k, v in decisiones.items():
        if v["estado"] == "aprobado":
            por_directo.setdefault(v["directo"], []).append(
                {"id": k, "abs_ini": v["abs_ini"], "abs_fin": v["abs_fin"], "score": v["score"]})

    for job in jobs:
        try:
            datos = cargar_job(job)
        except Exception as e:  # noqa: BLE001
            print(f"[{job[:8]}] no se pudo leer: {e}"); continue
        if datos is None:
            print(f"[{job[:8]}] aún no ha terminado"); continue
        fuente = fuente_del_job(job) or {"canal": args.creador or "desconocido", "url": None,
                                         "idioma": args.idioma or "es", "offset_s": 0, "tramo": 0, "directo": f"manual-{job[:8]}"}
        if not fuente_del_job(job) and not args.idioma:
            print(f"[{job[:8]}] job manual: se asume --idioma es")
        ctx = {**fuente, "transcript": datos["transcript"]}
        # los clips se evalúan de mayor a menor nota: en un duplicado se queda el mejor
        orden = sorted(enumerate(datos["clips"], 1), key=lambda t: -(t[1].get("predicted_score") or 0))
        for n, clip in orden:
            clave_clip = f"{job}:{n}"
            if clave_clip in decisiones and decisiones[clave_clip]["estado"] != "pendiente" and not args.reevaluar:
                continue
            res = evaluar(clip, ctx, args, por_directo.setdefault(ctx["directo"], []), clave)
            resumen[res["estado"]] += 1
            reg = {"estado": res["estado"], "motivos": res["motivos"], "directo": ctx["directo"], "idioma": ctx["idioma"],
                   "abs_ini": res["abs"][0], "abs_fin": res["abs"][1], "score": clip.get("predicted_score") or 0,
                   "decidido": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
            decisiones[clave_clip] = reg
            marca = {"aprobado": "OK ", "rechazado": "NO ", "pendiente": "?? "}[res["estado"]]
            print(f"{marca}{job[:8]} clip {n} ({clip.get('predicted_score')}) {clip.get('video_title_for_youtube_short', '')[:48]!r}"
                  + (f"  <- {', '.join(res['motivos'])}" if res["motivos"] else "")
                  + (f"  [aviso: {', '.join(res['avisos'])}]" if res["avisos"] else ""))
            if res["estado"] == "aprobado":
                por_directo[ctx["directo"]].append({"id": clave_clip, "abs_ini": res["abs"][0], "abs_fin": res["abs"][1],
                                                   "score": reg["score"]})
                ficha = {
                    "id": clave_clip, "idioma": ctx["idioma"], "creador": ctx["canal"], "fuente_url": ctx["url"],
                    "tramo": ctx["tramo"], "minuto_en_directo": round(res["abs"][0] / 60, 1),
                    "video": str(ruta_video(clip["video_url"])).replace(str(YC), "E:\\YoutubeClips").replace("/", "\\"),
                    "titulo": clip.get("video_title_for_youtube_short"), "gancho": clip.get("viral_hook_text"),
                    "descripcion_youtube": clip.get("video_description_for_instagram"),
                    "descripcion_tiktok": clip.get("video_description_for_tiktok"),
                    "score": reg["score"], "avisos": res["avisos"], "datos": res["datos"],
                    "picante": bool(res["datos"].get("picante")),
                    "estado_subida": "pendiente", "aprobado": reg["decidido"],
                }
                (COLA / ctx["idioma"] / f"{job[:8]}_clip{n}.json").write_text(
                    json.dumps(ficha, ensure_ascii=False, indent=2), encoding="utf-8")
            elif res["estado"] == "rechazado":
                with open(COLA / "rechazados.jsonl", "a", encoding="utf-8") as f:
                    f.write(json.dumps({"id": clave_clip, "titulo": clip.get("video_title_for_youtube_short"),
                                        "motivos": res["motivos"], "datos": res["datos"], **{k: reg[k] for k in ("directo", "idioma", "decidido")}},
                                       ensure_ascii=False) + "\n")
    dec_path.write_text(json.dumps(decisiones, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nResumen: {resumen['aprobado']} aprobados, {resumen['rechazado']} rechazados, {resumen['pendiente']} pendientes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
