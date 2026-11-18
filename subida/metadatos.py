"""
Metadatos de publicación de cada clip aprobado: título con gancho + miniatura con el nombre del streamer.

Para cada ficha de datos\\cola\\{es,en} sin `titulo_final`:
  1. Gemini escribe UN título corto, genérico y reutilizable: gancho emocional + nombre del streamer + juego.
     Reglas (de la skill content-engine): una sola afirmación concreta, nada de relleno ni «increíble/épico/revolucionario»,
     sin clickbait que el clip no cumpla, sin destripar la historia del juego, máx. 1 emoji.
  2. Se renderiza una miniatura vertical 1080x1920: fotograma del clip + nombre del streamer arriba + texto gancho abajo.
  3. La ficha guarda titulo_final, texto_miniatura, miniatura, juego, streamer_nombre.

Se ejecuta dentro de un contenedor (trae Pillow, ffmpeg y las fuentes de OpenShorts). Usar metadatos.ps1.
"""
from __future__ import annotations

import base64
import glob
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

YC = Path(os.environ.get("YC_RAIZ", "/yc"))
COLA = YC / "datos" / "cola"
MINIS = COLA / "miniaturas"
FUENTE = "/app/fonts/Anton-Regular.ttf"
MODELO = os.environ.get("GEMINI_FILTRO_MODELO", "gemini-3.1-flash-lite")
W, H = 1080, 1920
MORADO, AMARILLO = (145, 70, 255), (255, 214, 0)


def clave_gemini() -> str:
    for l in (YC / ".env").read_text(encoding="utf-8-sig").splitlines():
        m = re.match(r"^\s*GEMINI_API_KEY\s*=\s*(.*)$", l)
        if m:
            return m.group(1).strip().strip('"').strip("'")
    raise SystemExit("falta GEMINI_API_KEY")


def host_a_contenedor(ruta_win: str) -> Path:
    return YC / Path(ruta_win.replace("E:\\YoutubeClips\\", "").replace("\\", "/"))


def streamer_y_juego(f: dict) -> tuple[str, str]:
    """Nombre visible del streamer (con mayúsculas reales) y juego (GTA VI / GTA V) según Twitch/YouTube."""
    login = (f.get("creador") or "").lower()
    nombre, juego = f.get("creador") or "Streamer", "GTA"
    sp = YC / "datos" / "descubrimiento" / "streamers.json"
    if sp.exists():
        for v in json.loads(sp.read_text(encoding="utf-8")).values():
            if v.get("login", "").lower() == login:
                nombre = v["creador"]; break
    up = YC / "datos" / "descubrimiento" / "ultimo.json"
    if up.exists():
        for c in json.loads(up.read_text(encoding="utf-8")).get("candidatos", []):
            if c["tipo"] == "directo" and c["creador"].lower() == login:
                juego = "GTA VI" if re.search(r"VI\b", c.get("juego", "")) else "GTA V"; break
    texto = f"{f.get('titulo', '')} {f.get('gancho', '')}"
    if juego == "GTA" and re.search(r"gta\s*(vi|6)\b", texto, re.I):
        juego = "GTA VI"
    return nombre, juego


# Linter de títulos de la skill yt-package (Jakeschincariol/youtube-agent-skill, MIT), instalada en el proyecto.
# Está pensado para inglés: se usa para ORDENAR propuestas, no como veredicto. Se completa con vagos en español.
VAGOS_ES = re.compile(r"\b(incre[ií]ble|[eé]pic[oa]|brutal|locura|loco|bestial|alucinante|impresionante|"
                      r"revolucionari[oa]|tienes que ver|no te lo vas a creer|lo mejor)\b", re.I)


def _linter():
    import importlib.util
    ruta = YC / ".claude" / "skills" / "yt-package" / "title.py"
    if not ruta.exists():
        return None
    spec = importlib.util.spec_from_file_location("yt_title", ruta)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod.check


LINTER = _linter()


def puntuar(titulo: str, miniatura: str, nombre: str, juego: str) -> int:
    base = LINTER(titulo, miniatura)["score"] if LINTER else 70
    if nombre.lower() not in titulo.lower(): base -= 40          # requisito: streamer visible en el título
    if juego.lower() not in titulo.lower(): base -= 10
    if VAGOS_ES.search(titulo): base -= 14
    if titulo.lower().startswith(nombre.lower()): base += 4       # sujeto delante: sobrevive al corte móvil
    if not (25 <= len(titulo) <= 60): base -= 20
    return base


# ---------------------------------------------------------------------------- títulos orgánicos (18-nov-2026)
# Petición del usuario: que no suenen a IA. Se usan plantillas típicas de canales de clips, numeradas por streamer
# (parecen una serie y no se repiten), y a veces un gancho corto escrito como un comentario de un chaval.
PLANTILLAS = {
    "es": ["Mejores momentos de {s} en {j} #{n}", "{g} 💀 {s} en {j}", "Top momentos de {s} jugando {j} #{n}",
           "{s} en {j} 😂 {g}", "Los clips más locos de {s} en {j} #{n}", "{g} | {s} {j}"],
    "en": ["Best of {s} in {j} #{n}", "{g} 💀 {s} {j}", "Top {s} moments in {j} #{n}",
           "{s} in {j} 😂 {g}", "{s}'s funniest {j} clips #{n}", "{g} | {s} {j}"],
}
SUENA_A_IA = re.compile(r"(nadie esperaba|no te lo vas a creer|se queda helad|incre[ií]ble|impactante|sorprendente|"
                        r"detalle|[eé]pic|brutal|alucinante|impresionante|descubre|mira c[oó]mo|lo que pas[oó]|"
                        r"nobody expected|you won.t believe|insane|epic|shocking|incredible|—|:)", re.I)
SERIES = COLA / "series.json"


def siguiente_numero(nombre: str) -> int:
    d = json.loads(SERIES.read_text(encoding="utf-8")) if SERIES.exists() else {}
    d[nombre] = d.get(nombre, 0) + 1
    SERIES.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    return d[nombre]


def gancho_organico(clave: str, f: dict) -> str | None:
    es = f["idioma"] == "es"
    prompt = f"""Clip de un streamer jugando a GTA. Lo que pasa: {f.get('titulo')} / {f.get('gancho')}.
Escribe un gancho MUY corto (2 a 6 palabras) {'en español de España coloquial' if es else 'in casual English'} como lo escribiría
un chaval en un comentario de TikTok: minúsculas, sin adjetivos rimbombantes, sin dos puntos, sin emojis.
Ejemplos del tono: "lo del avión no tiene sentido", "se le va la olla", "el final 💀" (sin el emoji), "no puede ser real", "casi le da algo".
Responde SOLO JSON: {{"gancho": "..."}}"""
    try:
        r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{MODELO}:generateContent",
                          headers={"x-goog-api-key": clave}, timeout=60,
                          json={"contents": [{"parts": [{"text": prompt}]}],
                                "generationConfig": {"responseMimeType": "application/json", "temperature": 0.9}})
        r.raise_for_status()
        d = json.loads(r.json()["candidates"][0]["content"]["parts"][0]["text"])
        d = d[0] if isinstance(d, list) else d
        g = re.sub(r"[\"“”.!¡?¿]+", "", re.sub(r"\s+", " ", d.get("gancho", ""))).strip().lower()
    except Exception:  # noqa: BLE001 - sin gancho se usa una plantilla que no lo necesita
        return None
    if not (2 <= len(g.split()) <= 6) or SUENA_A_IA.search(g) or re.search(r"[\U0001F300-\U0001FAFF]", g):
        return None
    return g


def pedir_titulo(clave: str, f: dict, nombre: str, juego: str) -> dict:
    idioma = f["idioma"] if f["idioma"] in PLANTILLAS else "es"
    n = siguiente_numero(nombre)
    plantillas = PLANTILLAS[idioma]
    # Rotación por número; los clips picantes van con gancho (es lo que más engancha).
    orden = [plantillas[(n - 1 + i) % len(plantillas)] for i in range(len(plantillas))]
    if f.get("picante"):
        orden.sort(key=lambda t: "{g}" not in t)
    g = gancho_organico(clave, f) if any("{g}" in t for t in orden[:2]) else None
    plantilla = next(t for t in orden if g or "{g}" not in t)
    titulo = plantilla.format(s=nombre, j=juego, n=n, g=g or "")
    titulo = re.sub(r"\s+", " ", titulo).strip()
    # Miniatura: el gancho entero si es corto (nunca cortado a medias); si no, un texto genérico de canal de clips.
    genericos = (["MEJORES MOMENTOS", "TOP MOMENTOS", "NO PUEDE SER", "QUÉ ACABA DE PASAR"] if idioma == "es"
                 else ["BEST MOMENTS", "TOP MOMENTS", "NO WAY", "WHAT JUST HAPPENED"])
    palabras_titulo = set(re.findall(r"\w+", titulo.lower()))
    if g and len(g.split()) <= 4 and g not in titulo.lower():
        mini = g.upper()
    else:  # genérico que no repita palabras del título (regla de yt-package: título y miniatura dicen cosas distintas)
        rot = genericos[(n - 1) % len(genericos):] + genericos[:(n - 1) % len(genericos)]
        mini = next((x for x in rot if not set(re.findall(r"\w+", x.lower())) & palabras_titulo), rot[-1])
    return {"titulo_final": titulo[:95], "texto_miniatura": mini, "serie_n": n, "plantilla": plantilla}


def pedir_titulo_ia_antiguo(clave: str, f: dict, nombre: str, juego: str) -> dict:
    """Versión anterior (Gemini escribe el título entero). Sustituida el 18-nov por petición del usuario: sonaba a IA."""
    es = f["idioma"] == "es"
    prompt = f"""Escribe títulos para un YouTube Short de un clip de {juego}. Idioma: {'español de España' if es else 'inglés'}.

Datos del clip:
- Streamer: {nombre}
- Momento (resumen interno, el espectador NO lo conoce): {f.get('titulo')} / gancho original: {f.get('gancho')}
- Motivo por el que es bueno: {(f.get('datos') or {}).get('ia', {}).get('motivo', '')}

Reglas (título y miniatura son UNA pieza):
1. Escribe 8 TÍTULOS distintos de 28 a 58 caracteres. Cada uno incluye "{nombre}" (mejor al principio) y "{juego}".
2. Genéricos y reutilizables: una emoción o tensión que cualquiera entiende (flipa, no se lo cree, se arrepiente, pierde los nervios…) sin depender de detalles que solo entiende quien vio el directo.
3. Un único gancho concreto. Usa un número solo si es real y aparece en el clip; nunca lo inventes.
4. Prohibido: increíble, épico, brutal, locura, revolucionario, "tienes que ver", "no te lo vas a creer".
5. No prometas lo que el clip no muestra. No destripes la historia del juego. Sin política ni insultos.
6. Máximo 1 emoji, al final. Como mucho 1 palabra en mayúsculas.
7. TEXTO_MINIATURA: 2 o 3 palabras en mayúsculas que digan algo que el título NO dice (no repitas palabras del título ni el nombre del streamer).

Responde SOLO con JSON: {{"titulos": ["...", "..."], "texto_miniatura": "..."}}"""
    cuerpo = {"contents": [{"parts": [{"text": prompt}]}],
              "generationConfig": {"responseMimeType": "application/json", "temperature": 0.8}}
    r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{MODELO}:generateContent",
                      headers={"x-goog-api-key": clave}, json=cuerpo, timeout=90)
    r.raise_for_status()
    d = json.loads(r.json()["candidates"][0]["content"]["parts"][0]["text"])
    mini = re.sub(r"\s+", " ", d.get("texto_miniatura", "")).strip().upper()
    mini = " ".join(mini.split()[:3])[:34]
    candidatos = [re.sub(r"\s+", " ", t).strip() for t in d.get("titulos", []) if t and t.strip()]
    if not candidatos:
        raise RuntimeError("Gemini no devolvió títulos")
    ranking = sorted(((puntuar(t, mini, nombre, juego), t) for t in candidatos), reverse=True)
    nota, titulo = ranking[0]
    if nombre.lower() not in titulo.lower():                     # garantía final: el streamer siempre sale
        titulo = f"{nombre}: {titulo}"
    return {"titulo_final": titulo[:95], "titulo_puntuacion": nota, "texto_miniatura": mini,
            "titulos_alternativos": [t for _, t in ranking[1:4]]}


def mejor_fotograma(video: Path, tmp: Path) -> Image.Image:
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(video)],
                               capture_output=True, text=True).stdout.strip() or 20)
    mejor, puntuacion = None, -1.0
    for i, fr in enumerate((0.25, 0.4, 0.55, 0.7)):
        out = tmp / f"fr{i}.png"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{dur * fr:.2f}", "-i", str(video), "-frames:v", "1", str(out)])
        if not out.exists():
            continue
        im = Image.open(out).convert("RGB")
        borde = ImageOps.grayscale(im.resize((270, 480))).filter(ImageFilter.FIND_EDGES)
        datos = borde.get_flattened_data() if hasattr(borde, "get_flattened_data") else borde.getdata()
        p = sum(datos) / (270 * 480)                 # más detalle = fotograma menos vacío/borroso
        if p > puntuacion:
            mejor, puntuacion = im, p
    if mejor is None:
        raise RuntimeError("no se pudo extraer ningún fotograma")
    return ImageOps.fit(mejor, (W, H), Image.LANCZOS)


def texto_borde(d: ImageDraw.ImageDraw, xy, txt, fuente, relleno, borde=10):
    d.text(xy, txt, font=fuente, fill=relleno, stroke_width=borde, stroke_fill=(0, 0, 0), anchor="mm")


def ajustar(d: ImageDraw.ImageDraw, txt: str, ancho: int, max_px: int, min_px: int = 90):
    for px in range(max_px, min_px, -6):
        f = ImageFont.truetype(FUENTE, px)
        lineas, linea = [], ""
        for pal in txt.split():
            prueba = (linea + " " + pal).strip()
            if d.textlength(prueba, font=f) <= ancho:
                linea = prueba
            else:
                lineas.append(linea); linea = pal
        lineas.append(linea)
        if len(lineas) <= 3:
            return f, lineas
    f = ImageFont.truetype(FUENTE, min_px)
    return f, [txt]


def render_miniatura(video: Path, nombre: str, juego: str, gancho: str, destino: Path, y_top: int = 120) -> None:
    """y_top: altura de la caja del nombre. Si el clip lleva la cámara superpuesta arriba a la derecha (camara.py,
    y=190, ~250 px de alto), se baja a 480 para no taparle la cara al streamer."""
    with tempfile.TemporaryDirectory() as td:
        im = mejor_fotograma(video, Path(td))
    im = Image.eval(im, lambda v: int(v * 0.82))                       # oscurece un poco para dar contraste
    degradado = Image.new("L", (1, H))
    for y in range(H):
        degradado.putpixel((0, y), int(190 * max(0, (y - H * 0.55) / (H * 0.45))) if y > H * 0.55 else 0)
    im.paste(Image.new("RGB", (W, H), (0, 0, 0)), (0, 0), degradado.resize((W, H)))
    d = ImageDraw.Draw(im)
    # Nombre del streamer arriba, en caja morada
    f_nom, lin_nom = ajustar(d, nombre.upper(), W - 200, 190, 90)
    alto_linea = f_nom.size * 1.05
    caja_h = int(alto_linea * len(lin_nom) + 70)
    d.rounded_rectangle((60, y_top, W - 60, y_top + caja_h), 38, fill=MORADO, outline=(255, 255, 255), width=8)
    for i, l in enumerate(lin_nom):
        texto_borde(d, (W // 2, y_top + 35 + alto_linea * (i + 0.5)), l, f_nom, (255, 255, 255), 8)
    # Etiqueta del juego
    f_j = ImageFont.truetype(FUENTE, 92)
    ancho_j = d.textlength(juego, font=f_j) + 90
    y_j = y_top + caja_h + 40
    d.rounded_rectangle((W / 2 - ancho_j / 2, y_j, W / 2 + ancho_j / 2, y_j + 130), 30, fill=AMARILLO)
    d.text((W / 2, y_j + 68), juego, font=f_j, fill=(0, 0, 0), anchor="mm")
    # Gancho abajo
    f_g, lin_g = ajustar(d, gancho, W - 140, 210, 100)
    alto_g = f_g.size * 1.1
    y0 = H - 260 - alto_g * len(lin_g)
    for i, l in enumerate(lin_g):
        color = AMARILLO if i == len(lin_g) - 1 else (255, 255, 255)
        texto_borde(d, (W // 2, y0 + alto_g * (i + 0.5)), l, f_g, color, 12)
    destino.parent.mkdir(parents=True, exist_ok=True)
    im.save(destino, "JPEG", quality=88, optimize=True)


def main() -> int:
    clave = None
    n = 0
    for ruta in sorted(glob.glob(str(COLA / "*" / "*.json"))):
        if "/miniaturas/" in ruta.replace("\\", "/"):
            continue
        p = Path(ruta)
        f = json.loads(p.read_text(encoding="utf-8-sig"))  # -sig: tolera fichas editadas a mano en Windows
        if not isinstance(f, dict) or f.get("estado_subida") != "pendiente":
            continue
        if f.get("titulo_final") and f.get("miniatura"):
            continue
        clave = clave or clave_gemini()
        nombre, juego = streamer_y_juego(f)
        try:
            # Con título ya hecho y sin miniatura: solo se rehace la miniatura (no gasta número de la serie).
            t = ({k: f[k] for k in ("titulo_final", "texto_miniatura")} if f.get("titulo_final") and f.get("texto_miniatura")
                 else pedir_titulo(clave, f, nombre, juego))
            mini = MINIS / f"{p.stem}.jpg"
            render_miniatura(host_a_contenedor(f["video"]), nombre, juego, t["texto_miniatura"], mini,
                             y_top=650 if f.get("formato") == "streamer" else (480 if f.get("camara") == "superpuesta" else 120))
        except Exception as e:  # noqa: BLE001 - se reintenta en la próxima pasada
            print(f"[{p.stem}] ERROR: {e}"); continue
        f.update(t, streamer_nombre=nombre, juego=juego, miniatura=str(mini).replace(str(YC), "E:\\YoutubeClips").replace("/", "\\"))
        p.write_text(json.dumps(f, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[{p.stem}] {t['titulo_final']}   |   miniatura: «{t['texto_miniatura']}»")
        n += 1
    print(f"{n} clips con metadatos nuevos")
    return 0


if __name__ == "__main__":
    sys.exit(main())
