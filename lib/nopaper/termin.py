# SPDX-License-Identifier: AGPL-3.0-or-later
"""Termin aus einer Ladung (E62): Tag, Uhrzeit mit Saal und Aktenzeichen – nur mit festen Regeln aus dem
erkannten Text, ohne Sprachmodell. Ein Wert entsteht nur, wenn er woertlich und eindeutig im Text steht;
im Zweifel bleibt das Feld leer und der Beleg geht in die Pruefung."""
from __future__ import annotations

import datetime as dt
import re

_MONATE = ["januar", "februar", "märz", "april", "mai", "juni", "juli", "august", "september", "oktober",
           "november", "dezember"]
_MONAT_RE = "|".join(_MONATE + ["maerz"])

# Ist das Schreiben eine Ladung? (Wortanfang, damit "Einladung", "Vorladung" und "Beladung" nicht zaehlen –
# "Vorladung" ist ueber "geladen"/"vorgeladen" trotzdem erfasst, wenn es so im Text steht)
_LADUNG = re.compile(r"(?<![a-zäöüß])(ladung|geladen|vorgeladen|umladung)(?![a-zäöüß])", re.I)
# Woerter, die vor einem Termin stehen
_DAVOR = re.compile(r"termin|verhandlung|geladen|anberaumt|bestimmt|er[öo]rterung|anh[öo]rung|sitzung|verlegt", re.I)
# 15.01.2027 | 15. 1. 27 | 15. Januar 2027
_DATUM = re.compile(
    r"(?<!\d)(?P<t>\d{1,2})\s?\.\s?(?:(?P<m>\d{1,2})\s?\.\s?|(?P<mn>" + _MONAT_RE + r")\s+)(?P<j>\d{4}|\d{2})(?!\d)",
    re.I)
# 11:00 Uhr | 11.00 Uhr | 11 Uhr | um 9.30 Uhr
_ZEIT = re.compile(r"(?<!\d)(?P<h>[01]?\d|2[0-3])(?:\s?[:.]\s?(?P<min>[0-5]\d))?\s?Uhr\b", re.I)
_SAAL = re.compile(r"(?:Sitzungssaal|Saal|Raum|Zimmer)\s+(?:Nr\.?\s*)?[0-9A-Za-zÄÖÜäöü][0-9A-Za-zÄÖÜäöü./-]{0,9}")
_AZ = re.compile(
    r"(?:\bAz\.?|\bAktenzeichen|\bGesch(?:ä|ae)fts(?:zeichen|nummer|-?\s?Nr\.?))\s*:?\s*"
    r"(?P<az>[0-9A-Za-z][0-9A-Za-z .\-]{0,28}?\s?/\s?\d{2,4}(?:\s[A-Z]{1,4}\b)?)")

VOR, NACH = 220, 80   # Zeichen vor dem Datum (Stichwort) und nach dem Datum (Uhrzeit)


def ist_ladung(text: str) -> bool:
    return _LADUNG.search(text) is not None


def _datum(m: re.Match) -> dt.date | None:
    try:
        tag = int(m.group("t"))
        if m.group("m"):
            monat = int(m.group("m"))
        else:
            name = m.group("mn").lower().replace("maerz", "märz")
            monat = _MONATE.index(name) + 1
        jahr = int(m.group("j"))
        if jahr < 100:
            jahr += 2000
        return dt.date(jahr, monat, tag)
    except (ValueError, TypeError):
        return None


def kandidaten(text: str) -> list[dict]:
    """Alle Stellen, an denen ein Datum wie ein Termin steht: davor ein Stichwort (Termin, Verhandlung,
    geladen, anberaumt ...), kurz dahinter eine Uhrzeit. Briefdatum, Fristen und Geburtsdaten haben
    keine Uhrzeit und fallen dadurch heraus."""
    out = []
    for m in _DATUM.finditer(text):
        d = _datum(m)
        if d is None:
            continue
        if not _DAVOR.search(text[max(0, m.start() - VOR):m.start()]):
            continue
        rest = text[m.end():m.end() + NACH]
        z = _ZEIT.search(rest)
        if z is None:
            continue
        zeit = f"{int(z.group('h')):02d}:{z.group('min') or '00'} Uhr"
        s = _SAAL.search(text[m.end() + z.end():m.end() + z.end() + NACH])
        saal = re.sub(r"\s+", " ", s.group(0)).rstrip(".,;") if s else ""
        out.append({"datum": d, "zeit": zeit + (f", {saal}" if saal else "")})
    return out


def aktenzeichen(text: str) -> str | None:
    """Das Aktenzeichen, wenn genau eines mit Bezeichnung im Text steht (Az., Aktenzeichen, Geschaeftszeichen)."""
    werte = {re.sub(r"\s*/\s*", "/", re.sub(r"\s+", " ", m.group("az"))).strip() for m in _AZ.finditer(text)}
    return werte.pop() if len(werte) == 1 else None


def erkennen(text: str) -> tuple[dict, list[str]]:
    """Liefert ({termin: date, terminzeit: str, aktenzeichen: str}, Codes). Leer, wenn das Schreiben keine
    Ladung ist. Codes: TERMIN_ERKANNT | TERMIN_NICHT_ERKANNT | TERMIN_MEHRDEUTIG (die letzten beiden
    bedeuten: Ladung, aber kein sicherer Termin -> ein Mensch muss hinsehen)."""
    if not ist_ladung(text):
        return {}, []
    werte: dict = {}
    az = aktenzeichen(text)
    if az:
        werte["aktenzeichen"] = az
    kand = kandidaten(text)
    tage = {k["datum"] for k in kand}
    if not tage:
        return werte, ["TERMIN_NICHT_ERKANNT"]
    if len(tage) > 1:
        # z.B. Umladung: alter und neuer Termin stehen beide da – nicht raten
        return werte, ["TERMIN_MEHRDEUTIG"]
    werte["termin"] = kand[0]["datum"]
    zeiten = {k["zeit"] for k in kand}
    if len(zeiten) == 1 or len({z.split(",")[0] for z in zeiten}) == 1:
        werte["terminzeit"] = max(zeiten, key=len)
    return werte, ["TERMIN_ERKANNT"]
