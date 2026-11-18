"""
Monta cada clip APROBADO con una plantilla fija de streamer, en vez de usar el encuadre de OpenShorts.

Por qué: OpenShorts elige diseños distintos por clip ('wide', 'general', 'inset', 'screencast', 'track') y a menudo
deja la webcam cortada o pone una cámara grande mal encuadrada (se ve la habitación, la cara cortada). Para lo que se
publica queremos SIEMPRE lo mismo:

    ┌──────────────┐  0
    │  WEBCAM      │      la webcam del streamer ENTERA (recortada del original 16:9)
    ├──────────────┤  608
    │              │
    │   JUEGO      │      recorte central del juego, evitando la zona de la webcam
    │  subtítulos  │      subtítulos palabra a palabra (de la transcripción), palabra activa en amarillo
    │              │
    └──────────────┘  1920

Para cada ficha aprobada pendiente de subir sin `formato`:
  - localiza la webcam en el original (Gemini, 2 fotogramas que deben coincidir; ver camara.caja_webcam);
  - si hay webcam: compone la plantilla -> <clip>_streamer.mp4 y `formato` = streamer;
  - si no hay webcam: se queda el clip de OpenShorts (`formato` = openshorts).
Corre dentro de un contenedor (ffmpeg, libass, fuentes de OpenShorts).
"""
from __future__ import annotations

import glob
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
from camara import BACKEND, COLA, UPLOADS, a_contenedor, a_host, caja_webcam, clave  # noqa: E402

VW, VH = 1080, 1920
CAM_H = 608                      # webcam a ancho completo en 16:9
JUEGO_H = VH - CAM_H             # 1312
MORADO = "0x9146FF"
SUB_Y = 1480                     # centro de los subtítulos (tercio inferior del juego, por encima de la interfaz de Shorts)
FUENTES = "/app/fonts"


def ass_tiempo(t: float) -> str:
    t = max(0.0, t)
    h, r = divmod(t, 3600); m, s = divmod(r, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def subtitulos_ass(transcript: dict, ini: float, fin: float, destino: Path) -> int:
    """ASS con bloques de hasta 3 palabras; la palabra que suena va en amarillo. Devuelve nº de palabras."""
    palabras = [w for seg in transcript.get("segments", []) for w in (seg.get("words") or [])
                if w.get("start", 0) >= ini - 0.05 and w.get("end", 0) <= fin + 0.05 and w.get("word", "").strip()]
    bloques, actual = [], []
    for w in palabras:
        if actual and (len(actual) >= 3 or w["start"] - actual[-1]["end"] > 0.8):
            bloques.append(actual); actual = []
        actual.append(w)
    if actual:
        bloques.append(actual)
    lineas = []
    for b in bloques:
        textos = [re.sub(r"[{}\\]", "", w["word"].strip().upper()) for w in b]
        for i, w in enumerate(b):
            fin_w = b[i + 1]["start"] if i + 1 < len(b) else w["end"] + 0.15
            txt = " ".join((r"{\c&H00D6FF&}" + t + r"{\c&HFFFFFF&}") if j == i else t for j, t in enumerate(textos))
            lineas.append(f"Dialogue: 0,{ass_tiempo(w['start'] - ini)},{ass_tiempo(fin_w - ini)},S,,0,0,0,,"
                          f"{{\\pos({VW // 2},{SUB_Y})}}{txt}")
    destino.write_text(
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nWrapStyle: 0\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
        "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding\n"
        "Style: S,Anton,138,&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,0,0,0,0,100,100,1,0,1,10,4,5,50,50,0,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        + "\n".join(lineas) + "\n", encoding="utf-8")
    return len(palabras)


def componer(fuente: Path, ini: float, dur: float, caja, ass: Path, salida: Path) -> None:
    sw, sh = map(int, subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                      "stream=width,height", "-of", "csv=p=0", str(fuente)],
                                     capture_output=True, text=True).stdout.strip().split(","))
    x0, y0, x1, y1 = caja
    cx, cy = int(x0 * sw) // 2 * 2, int(y0 * sh) // 2 * 2
    cw, ch = int((x1 - x0) * sw) // 2 * 2, int((y1 - y0) * sh) // 2 * 2
    # Juego: altura completa del original, ancho con la proporción del hueco (1080x1312), centrado
    # y desplazado si se solapa con la webcam.
    gw = int(sh * VW / JUEGO_H) // 2 * 2
    gx = (sw - gw) // 2
    if cx < gx + gw and cx + cw > gx:                       # la webcam cae dentro del recorte del juego
        gx = cx + cw if (cx + cw / 2) < sw / 2 else cx - gw  # apartarse hacia el lado contrario
    gx = max(0, min(sw - gw, gx)) // 2 * 2
    filtro = (
        f"[0:v]split=2[a][b];"
        f"[a]crop={cw}:{ch}:{cx}:{cy},scale={VW}:{CAM_H}:force_original_aspect_ratio=increase,crop={VW}:{CAM_H}[cam];"
        f"[b]crop={gw}:{sh}:{gx}:0,scale={VW}:{JUEGO_H}[juego];"
        f"[cam][juego]vstack=inputs=2,drawbox=x=0:y={CAM_H - 4}:w={VW}:h=8:color={MORADO}:t=fill,"
        f"ass={ass}:fontsdir={FUENTES}[v]"
    )
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{ini:.3f}", "-t", f"{dur:.3f}", "-i", str(fuente),
                    "-filter_complex", filtro, "-map", "[v]", "-map", "0:a?",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(salida)], check=True)


def main() -> int:
    k, hechos = None, 0
    for ruta in sorted(glob.glob(str(COLA / "*" / "*.json"))):
        p = Path(ruta)
        if p.parent.name not in ("es", "en"):
            continue
        f = json.loads(p.read_text(encoding="utf-8-sig"))
        if f.get("estado_subida") != "pendiente" or f.get("formato"):
            continue
        job, n = f["id"].split(":")
        k = k or clave()
        try:
            c = requests.get(f"{BACKEND}/api/status/{job}", timeout=30).json()["result"]["clips"][int(n) - 1]
            fuentes = glob.glob(str(UPLOADS / f"{job}_*.mp4"))
            if not fuentes:
                f["formato"] = "openshorts"; print(f"[{p.stem}] sin tramo original: se usa el clip de OpenShorts")
            else:
                fuente, ini, fin = Path(fuentes[0]), float(c["start"]), float(c["end"])
                caja = caja_webcam(k, fuente, ini, fin)
                if caja is None:
                    f["formato"] = "openshorts"; print(f"[{p.stem}] sin webcam clara: se usa el clip de OpenShorts")
                else:
                    meta = glob.glob(str(a_contenedor(f["video"]).parent / "*_metadata.json"))
                    transcript = json.loads(Path(meta[0]).read_text(encoding="utf-8")).get("transcript", {}) if meta else {}
                    original = f.get("video_sin_camara") or f["video"]
                    salida = a_contenedor(original).with_name(Path(a_contenedor(original)).stem + "_streamer.mp4")
                    with tempfile.TemporaryDirectory() as td:
                        ass = Path(td) / "subs.ass"
                        npal = subtitulos_ass(transcript, ini, fin, ass)
                        componer(fuente, ini, fin - ini, caja, ass, salida)
                    f.update({"video_openshorts": original, "video": a_host(salida), "formato": "streamer",
                              "camara_caja": [round(v, 3) for v in caja]})
                    f.pop("camara", None)
                    f.pop("miniatura", None)  # la miniatura se rehace con el vídeo nuevo
                    print(f"[{p.stem}] plantilla streamer: webcam x {caja[0]:.2f}-{caja[2]:.2f} arriba, "
                          f"juego abajo, {npal} palabras subtituladas -> {salida.name}")
            p.write_text(json.dumps(f, ensure_ascii=False, indent=2), encoding="utf-8")
            hechos += 1
        except Exception as e:  # noqa: BLE001 - se reintenta en la próxima pasada
            print(f"[{p.stem}] ERROR: {str(e)[:300]}")
    print(f"{hechos} clips montados")
    return 0


if __name__ == "__main__":
    sys.exit(main())
