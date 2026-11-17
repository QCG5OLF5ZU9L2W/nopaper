# SPDX-License-Identifier: AGPL-3.0-or-later
"""Lokale Texterkennung (ocrmypdf/Tesseract) und Seitenbild fuer den Bild-Rueckfall.

Arbeitet immer auf einer Kopie im Arbeitsverzeichnis; das Original wird nie veraendert.
"""
from __future__ import annotations

import base64
import re
import os
import shutil
import subprocess
from pathlib import Path


class OcrError(Exception):
    pass


def _run(cmd: list[str], timeout: int = 120) -> str:
    r = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
    return r.stdout.decode("utf-8", errors="replace") if r.returncode == 0 else ""


def ist_digital(pdf: Path) -> bool:
    """True, wenn die PDF digital erzeugt ist (echte Textebene, keine seitenfuellenden Scanbilder).
    Scanner-PDFs mit eingebetteter Scanner-OCR gelten als Scan und werden neu erkannt."""
    info = _run(["pdfinfo", "-f", "1", "-l", "9999", str(pdf)])
    groessen = {}
    for m in re.finditer(r"Page\s+(\d+)\s+size:\s+([\d.]+)\s+x\s+([\d.]+)", info):
        groessen[int(m.group(1))] = float(m.group(2)) / 72 * float(m.group(3)) / 72
    if not groessen:
        return False
    for line in _run(["pdfimages", "-list", str(pdf)]).splitlines()[2:]:
        f = line.split()
        try:
            seite, w, h, xppi, yppi = int(f[0]), int(f[3]), int(f[4]), float(f[12]), float(f[13])
        except (IndexError, ValueError):
            continue
        if xppi > 0 and yppi > 0 and (w / xppi) * (h / yppi) >= 0.7 * groessen.get(seite, 1e9):
            return False  # seitenfuellendes Bild = Scan
    zeichen = len(re.sub(r"\s+", "", _run(["pdftotext", str(pdf), "-"])))
    return zeichen >= 50 * len(groessen)


def _layout_text(pdf: Path) -> str:
    """Text mit erhaltener Zeilen-/Spaltenlage (Bezeichnung und Wert bleiben in einer Zeile)."""
    roh = _run(["pdftotext", "-layout", "-enc", "UTF-8", str(pdf), "-"], timeout=300)
    seiten = []
    for seite in roh.split("\f"):
        zeilen = [re.sub(r" {4,}", "   ", z).rstrip() for z in seite.splitlines()]
        seiten.append(re.sub(r"\n{3,}", "\n\n", "\n".join(zeilen)).strip())
    return "\f".join(s for s in seiten if s)


def ocr_text(original: Path, workdir: Path, sprache: str = "deu", timeout: int = 900) -> str:
    """Liefert den Text einer PDF, Seiten durch \\f getrennt.
    Digitale PDFs: vorhandene Textebene. Scans: ocrmypdf (Tesseract) auf einer Kopie, danach Text mit Layout.
    Die erzeugte OCR-PDF wird nur fuer den Text gebraucht und verworfen; das Original bleibt unveraendert."""
    workdir.mkdir(parents=True, exist_ok=True)
    if ist_digital(original):
        text = _layout_text(original)
        if text.strip():
            return text
    src = workdir / "eingang.pdf"
    out = workdir / "ocr.pdf"
    shutil.copyfile(original, src)
    jobs = str(max(1, (os.cpu_count() or 2) - 1))
    cmd = [
        "ocrmypdf", "-l", sprache,
        # kein --clean: unpaper loescht duenne Striche auf Thermobons ("68,00" -> "9,0")
        "--force-ocr", "--rotate-pages", "--deskew",
        "--output-type", "pdf", "--optimize", "0",
        "--jobs", jobs, "--quiet",
        str(src), str(out),
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        raise OcrError("OCR_ZEITUEBERSCHREITUNG")
    if res.returncode not in (0, 4) or not out.exists():  # 4 = Ausgabe erzeugt, PDF/A-Warnung
        raise OcrError(f"OCR_FEHLER_{res.returncode}")
    text = _layout_text(out)
    for f in (src, out):
        try:
            f.unlink()
        except FileNotFoundError:
            pass
    return text


def zweite_lesung(original: Path, workdir: Path, sprache: str = "deu", seiten: int = 3) -> str:
    """Unabhaengige zweite Texterkennung (Graustufen 300 dpi, Tesseract psm 6) der ersten Seiten.
    Dient nur als zusaetzlicher Beleg fuer die Verankerung, wenn die erste Lesung Werte nicht findet."""
    workdir.mkdir(parents=True, exist_ok=True)
    prefix = workdir / "zweit"
    _run(["pdftoppm", "-r", "300", "-gray", "-f", "1", "-l", str(seiten), "-png", str(original), str(prefix)],
         timeout=180)
    teile = []
    for png in sorted(workdir.glob("zweit*.png")):
        teile.append(_run(["tesseract", str(png), "-", "-l", sprache, "--psm", "6"], timeout=300))
        png.unlink()
    return "\f".join(teile)


def seitenzahl(pdf: Path) -> int:
    m = re.search(r"^Pages:\s+(\d+)", _run(["pdfinfo", str(pdf)]), re.M)
    return int(m.group(1)) if m else 1


def seiten_auswahl(n: int, maximal: int) -> list[int]:
    """Bis `maximal` Seiten alle, sonst die ersten (maximal-1) plus die LETZTE Seite (dort stehen die Summen)."""
    if n <= maximal:
        return list(range(1, n + 1))
    return list(range(1, maximal)) + [n]


def seiten_als_bilder(original: Path, workdir: Path, seiten: int = 3, dpi: int = 170) -> list[str]:
    """Seitenbilder als PNG (base64) fuer Modelle, die direkt vom Bild lesen (NuExtract3: 170 dpi empfohlen).
    Bei langen Dokumenten: erste Seiten + letzte Seite."""
    workdir.mkdir(parents=True, exist_ok=True)
    pngs = []
    for nr in seiten_auswahl(seitenzahl(original), max(1, seiten)):
        prefix = workdir / f"bild-{nr:03d}"
        res = subprocess.run(["pdftoppm", "-r", str(dpi), "-f", str(nr), "-l", str(nr), "-png", "-singlefile",
                              str(original), str(prefix)], capture_output=True, timeout=180, check=False)
        png = prefix.with_suffix(".png")
        if res.returncode != 0 or not png.exists():
            raise OcrError("SEITENBILD_FEHLER")
        pngs.append(png)
    out = []
    for png in pngs:
        out.append(base64.b64encode(png.read_bytes()).decode("ascii"))
        png.unlink()
    return out


def seite_als_bild(original: Path, workdir: Path, seite: int = 1, dpi: int = 170) -> str:
    """Rendert eine Seite als PNG und gibt sie base64-kodiert zurueck (fuer das Bildmodell)."""
    workdir.mkdir(parents=True, exist_ok=True)
    prefix = workdir / "seite"
    res = subprocess.run(
        ["pdftoppm", "-r", str(dpi), "-f", str(seite), "-l", str(seite), "-png", "-singlefile",
         str(original), str(prefix)],
        capture_output=True, timeout=120, check=False,
    )
    png = prefix.with_suffix(".png")
    if res.returncode != 0 or not png.exists():
        raise OcrError("SEITENBILD_FEHLER")
    data = base64.b64encode(png.read_bytes()).decode("ascii")
    png.unlink()
    return data


# --------------------------------------------------------------------------- Textqualitaet

_KURZ = {"zu", "im", "in", "an", "ab", "am", "um", "de", "nr", "st", "eg", "ag", "kg", "co", "og", "uk", "mi",
         "da", "du", "er", "es", "ja", "so", "ob", "für", "vom", "der", "die", "das", "und", "von", "per", "mit"}


def _token_gut(t: str) -> bool:
    k = t.strip("()[]{}\"'„“”‚‘.,:;!?*=|-_~`´")
    if not k:
        return False
    if any(c.isdigit() for c in k):
        return True
    buchst = sum(c.isalpha() for c in k)
    if len(k) <= 2:
        return k.lower() in _KURZ
    return buchst >= 3 and buchst / len(k) >= 0.7


def zeilenqualitaet(zeile: str) -> float:
    """Anteil plausibler Woerter/Zahlen an den Zeichen einer Zeile (0..1). OCR-Muell aus Handschrift
    und Flecken liegt typischerweise unter 0.5."""
    toks = zeile.split()
    if not toks:
        return 1.0
    return sum(len(t) for t in toks if _token_gut(t)) / sum(len(t) for t in toks)


def textguete(text: str) -> tuple[int, float]:
    """(Zeichen in plausiblen Woertern/Zahlen, deren Anteil an allen Zeichen). Grobes Mass ohne Woerterbuch:
    erkennt Verlust und Muell, aber keine berichtigten Tippfehler."""
    toks = (text or "").split()
    alle = sum(len(t) for t in toks)
    gut = sum(len(t) for t in toks if _token_gut(t))
    return gut, (gut / alle if alle else 0.0)


def text_nicht_schlechter(alt: str, neu: str) -> bool:
    """True, wenn der neue Volltext den alten ersetzen darf: er hat Inhalt, verliert hoechstens 10 % der
    lesbaren Zeichen und sein Anteil lesbarer Zeichen sinkt um hoechstens 3 Prozentpunkte."""
    g_alt, q_alt = textguete(alt)
    g_neu, q_neu = textguete(neu)
    if g_neu == 0:
        return False
    if g_alt == 0:
        return True
    return g_neu >= 0.9 * g_alt and q_neu >= q_alt - 0.03


def fuer_ki(text: str, schwelle: float = 0.55) -> tuple[str, int]:
    """Entfernt Muellzeilen und ueberwiegend unlesbare Seiten (Handschrift) vor dem Modellaufruf.
    Gibt (Text, Anzahl entfernter Zeilen) zurueck. Der Volltext fuer paperless bleibt davon unberuehrt."""
    seiten, entfernt = [], 0
    for seite in text.split("\f"):
        zeilen = seite.splitlines()
        inhalt = [z for z in zeilen if z.strip()]
        gut = [z for z in zeilen if not z.strip() or zeilenqualitaet(z) >= schwelle]
        n_gut = len([z for z in gut if z.strip()])
        if inhalt and n_gut / len(inhalt) < 0.4:  # Seite ueberwiegend unlesbar
            entfernt += len(inhalt)
            continue
        entfernt += len(inhalt) - n_gut
        seiten.append("\n".join(gut))
    return "\f".join(seiten), entfernt
