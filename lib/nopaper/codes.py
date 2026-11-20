# SPDX-License-Identifier: AGPL-3.0-or-later
"""Strichcodes und QR-Codes auf den ersten Seiten eines Dokuments (E67).

Gelesen wird mit `zbarimg` (Paket zbar-tools) aus Seitenbildern, ohne Sprachmodell. Ein Code wird entweder
exakt gelesen oder gar nicht. Fehlt das Programm, liefert `lesen` einfach nichts.

Ausgewertet wird:
- jeder Strichcode (Code 128, Code 39, EAN ...) als Text fuer ein Feld,
- jeder QR-Code als Text fuer ein Feld,
- ein Ueberweisungs-QR-Code (EPC-QR, "GiroCode") zusaetzlich mit IBAN, Betrag und Verwendungszweck.
"""
from __future__ import annotations

import base64
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

# zweidimensionale Codes; alles andere gilt als Strichcode
ZWEI_D = ("QR", "DATAMATRIX", "PDF417", "AZTEC", "SQ")


@dataclass
class Code:
    typ: str        # wie von zbar gemeldet, z.B. "CODE-128", "QR-Code", "EAN-13"
    inhalt: str

    @property
    def ist_2d(self) -> bool:
        t = self.typ.upper().replace("-", "").replace("_", "")
        return any(t.startswith(z) for z in ZWEI_D)


def verfuegbar() -> bool:
    return shutil.which("zbarimg") is not None and shutil.which("pdftoppm") is not None


def aus_zbar_xml(xml: str) -> list[Code]:
    """Wertet die Ausgabe von `zbarimg --xml` aus. Doppelte Codes (derselbe Code auf mehreren Seiten) nur einmal."""
    out: list[Code] = []
    gesehen = set()
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return out
    for sym in root.iter():
        if not sym.tag.endswith("symbol"):
            continue
        typ = sym.get("type") or ""
        for d in sym:
            if not d.tag.endswith("data"):
                continue
            text = d.text or ""
            if d.get("format") == "base64":
                try:
                    text = base64.b64decode(text).decode("utf-8", errors="replace")
                except ValueError:
                    continue
            text = text.strip("\r\n")
            if text and (typ, text) not in gesehen:
                gesehen.add((typ, text))
                out.append(Code(typ, text))
    return out


def _seitenbilder(original: Path, ziel: Path, seiten: int, dpi: int, timeout: int, png: bool) -> list[Path]:
    """Graustufenbilder der ersten Seiten. Ohne `png` als PGM: unkomprimiert und damit um ein Mehrfaches
    schneller geschrieben und gelesen – die Kompression war der groesste Posten beim Lesen der Codes (E80)."""
    cmd = ["pdftoppm", "-r", str(dpi), "-gray", *(["-png"] if png else []), "-f", "1", "-l", str(seiten),
           str(original), str(ziel / "s")]
    r = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
    return sorted(ziel.glob("s*.png" if png else "s*.pgm")) if r.returncode == 0 else []


def lesen(original: Path, workdir: Path, seiten: int = 3, dpi: int = 300, timeout: int = 120) -> list[Code]:
    """Codes der ersten `seiten` Seiten. Leere Liste, wenn nichts gefunden wird oder der Leser fehlt –
    das Lesen von Codes darf die Verarbeitung nie aufhalten."""
    if not verfuegbar():
        return []
    ziel = workdir / "codes"
    ziel.mkdir(parents=True, exist_ok=True)
    try:
        for png in (False, True):       # erst das schnelle Format; PNG nur, falls zbarimg es nicht lesen kann
            bilder = _seitenbilder(original, ziel, seiten, dpi, timeout, png)
            if not bilder:
                continue
            # Rueckgabewert 4 heisst nur "kein Code gefunden"; die XML-Ausgabe kommt trotzdem
            z = subprocess.run(["zbarimg", "--quiet", "--xml", *map(str, bilder)], capture_output=True,
                               timeout=timeout, check=False)
            if z.returncode in (0, 4):
                return aus_zbar_xml(z.stdout.decode("utf-8", errors="replace"))
            for b in bilder:
                b.unlink(missing_ok=True)
        return []
    except (subprocess.TimeoutExpired, OSError):
        return []
    finally:
        shutil.rmtree(ziel, ignore_errors=True)


# --------------------------------------------------------------------------- Ueberweisungs-QR-Code

@dataclass
class GiroCode:
    iban: str
    name: str = ""
    betrag: Decimal | None = None
    zweck: str = ""


def girocode(inhalt: str) -> GiroCode | None:
    """EPC-QR-Code (EPC069-12): Zeilen BCD / Version / Zeichensatz / SCT / BIC / Name / IBAN / Betrag /
    Zweckcode / Referenz / Verwendungszweck / Hinweis. None, wenn der Inhalt kein solcher Code ist."""
    z = [x.strip() for x in inhalt.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    if len(z) < 7 or z[0] != "BCD" or z[3] != "SCT":
        return None
    iban = re.sub(r"\s+", "", z[6]).upper()
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", iban):
        return None
    betrag = None
    if len(z) > 7 and z[7]:
        m = re.fullmatch(r"EUR(\d{1,9}(?:\.\d{1,2})?)", z[7].replace(" ", ""))
        if m:
            try:
                betrag = Decimal(m.group(1))
            except InvalidOperation:
                betrag = None
    zweck = (z[9] if len(z) > 9 and z[9] else (z[10] if len(z) > 10 else "")).strip()
    return GiroCode(iban=iban, name=z[5], betrag=betrag, zweck=zweck)


def erster_girocode(codes: list[Code]) -> GiroCode | None:
    """Der Ueberweisungs-Code des Dokuments – nur wenn genau einer drauf ist (bei mehreren nicht raten)."""
    gefunden = [g for g in (girocode(c.inhalt) for c in codes if c.ist_2d) if g is not None]
    return gefunden[0] if len(gefunden) == 1 else None


def feldtext(werte: list[str], grenze: int | None, trenner: str) -> str | None:
    """Mehrere Codes als ein Feldwert. Mit Laengengrenze (kurzes Textfeld) kommen nur so viele ganze Codes
    hinein, wie passen – ein Code wird nie abgeschnitten."""
    out = ""
    for w in werte:
        neu = w if not out else out + trenner + w
        if grenze is not None and len(neu) > grenze:
            continue
        out = neu
    return out or None
