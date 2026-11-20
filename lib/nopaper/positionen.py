# SPDX-License-Identifier: AGPL-3.0-or-later
"""Rechnungspositionen (E72): Artikelnummer, Bezeichnung, Menge, Einheit, Einzel- und Gesamtpreis je Zeile –
gelesen vom Sprachmodell aus dem erkannten Text, geprueft mit festen Regeln. nopaper liefert die Positionen so,
wie sie gedruckt sind (E38); was ein Artikel ist und in welcher Einheit er gefuehrt wird, entscheidet die
Warenwirtschaft. Die Positionen sind eine Zugabe: Sie aendern weder die Kopfdaten noch den Pruefstatus."""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from . import invoice

KATEGORIEN = ("rechnung", "gutschrift", "kassenbon")
ABSCHNITT_ZEICHEN = 4000      # Text je Modellaufruf: klein halten, damit die Antwort vollstaendig bleibt
MAX_ABSCHNITTE = 40
MAX_POSITIONEN = 500

_S = {"type": ["string", "null"]}
SCHEMA = {
    "type": "object",
    "properties": {"positionen": {"type": "array", "items": {
        "type": "object",
        "properties": {"pos": _S, "artikelnummer": _S, "bezeichnung": {"type": "string"}, "menge": _S,
                       "einheit": _S, "inhalt": _S, "einzelpreis": _S, "gesamtpreis": _S, "steuersatz": _S},
        "required": ["pos", "artikelnummer", "bezeichnung", "menge", "einheit", "inhalt", "einzelpreis",
                     "gesamtpreis", "steuersatz"],
    }}},
    "required": ["positionen"],
}

PROMPT = """Du liest die Positionen einer Rechnung oder eines Kassenbons aus dem Text einer Texterkennung.
Antworte nur mit dem JSON-Schema. Gib jede Position genau einmal aus, in der Reihenfolge des Textes.

Eine Position ist eine Zeile der Rechnung mit einem eigenen Betrag, der in die Rechnungssumme eingeht: Waren,
Leistungen, auch Versandkosten, Pfand, Zuschlaege und Rabatte, wenn sie als eigene Zeile mit Betrag stehen.
KEINE Positionen sind: Zwischensummen, Uebertraege, Nettosumme, Mehrwertsteuer, Gesamtbetrag, Zahlungen,
Rueckgeld, Kopf- und Fusszeilen.

Je Position:
pos = Positionsnummer, wenn gedruckt, sonst null
artikelnummer = Artikelnummer des Lieferanten, wenn gedruckt, sonst null. Keine Nummer erfinden.
bezeichnung = die Bezeichnung des Artikels (erste Zeile der Beschreibung), woertlich
menge = die Menge genau so, wie sie gedruckt ist (z.B. "1", "2,5"); null, wenn keine gedruckt ist
einheit = die Einheit genau so, wie sie gedruckt ist (z.B. "Packung", "Karton", "Stück", "kg"), sonst null
inhalt = Inhalt der Einheit, nur wenn er in der Beschreibung steht (z.B. "100 Stück" bei
  "1 Packung = 100 Stück"), sonst null
einzelpreis = Preis je Einheit genau so, wie er gedruckt ist, mit allen Nachkommastellen (z.B. "8,1933")
gesamtpreis = Betrag der Zeile genau so, wie er gedruckt ist, mit Vorzeichen, wenn eines gedruckt ist
steuersatz = Steuersatz der Zeile in Prozent, nur wenn er in der Zeile steht (z.B. "19"), sonst null

Zahlen niemals umrechnen, runden oder ergaenzen. Steht ein Wert nicht da: null. Enthaelt der Text keine
Positionen, antworte mit einer leeren Liste."""

# gedruckte Einheit -> Code nach UN/ECE-Empfehlung 20 (fuer BilledQuantity/@unitCode); unbekannt -> C62 (Stueck)
EINHEITEN = {
    "stück": "C62", "stueck": "C62", "stk": "C62", "st": "C62", "stck": "C62", "x": "C62", "pcs": "C62",
    "packung": "PK", "pack": "PK", "pck": "PK", "pkg": "PK", "pa": "PK", "vpe": "PK", "ve": "PK",
    "karton": "CT", "krt": "CT", "kt": "CT", "kiste": "CS", "ki": "CS", "palette": "PF",
    "rolle": "RO", "rol": "RO", "flasche": "BO", "fl": "BO", "dose": "CA", "ds": "CA", "beutel": "BG",
    "btl": "BG", "sack": "SA", "tube": "TU", "paar": "PR", "set": "SET", "satz": "SET", "bund": "BE",
    "kg": "KGM", "g": "GRM", "l": "LTR", "ltr": "LTR", "liter": "LTR", "ml": "MLT", "m": "MTR", "cm": "CMT",
    "qm": "MTK", "m²": "MTK", "m2": "MTK", "std": "HUR", "h": "HUR", "stunde": "HUR", "stunden": "HUR",
    "tag": "DAY", "tage": "DAY", "monat": "MON", "monate": "MON", "pauschal": "LS", "psch": "LS",
}
_MINUS = "-−–"


def einheit_code(einheit: str | None) -> str:
    e = re.sub(r"[.\s]+", "", (einheit or "").lower())
    return EINHEITEN.get(e, "C62")


def zahl(s) -> Decimal | None:
    """Gedruckte Zahl -> Decimal, ohne zu runden. '1.234,56', '8,1933', '-3,00', '3,00-', '12 €'."""
    if s is None:
        return None
    t = str(s).strip()
    negativ = bool(re.search(rf"^[{_MINUS}]|[{_MINUS}]\s*(?:€|EUR)?\s*$", t))
    t = re.sub(r"[^\d,.]", "", t).strip(".,")
    if not t or not re.search(r"\d", t):
        return None
    if "," in t and "." in t:
        dez = max(t.rfind(","), t.rfind("."))
        t = re.sub(r"[,.]", "", t[:dez]) + "." + t[dez + 1:]
    elif "," in t:
        if t.count(",") > 1:
            return None
        t = t.replace(",", ".")
    elif t.count(".") > 1 or re.fullmatch(r"\d{1,3}\.\d{3}", t):
        t = t.replace(".", "")          # Punkt als Tausendertrenner: 1.000 = eintausend
    try:
        d = Decimal(t)
    except InvalidOperation:
        return None
    return -d if negativ else d


def dezimal(d: Decimal) -> str:
    """Decimal fuer das XML: Punkt, mindestens zwei Nachkommastellen, sonst so genau wie gedruckt."""
    s = format(d, "f")
    if "." not in s:
        return s + ".00"
    ganz, nach = s.split(".")
    return f"{ganz}.{nach.ljust(2, '0')}"


def im_text(d: Decimal, text_flach: str) -> bool:
    """Steht der Betrag woertlich im Text? Verglichen wird mit der gedruckten Genauigkeit (2 bis 4 Stellen)."""
    ganz, nach = dezimal(abs(d)).split(".")
    gruppiert = f"{int(ganz):,}".replace(",", ".")
    return any(f"{g}{t}{nach}" in text_flach for g in {ganz, gruppiert} for t in ",.")


def abschnitte(text: str, max_chars: int = ABSCHNITT_ZEICHEN) -> list[str]:
    """Text seitenweise (\\f) in Stuecke fuer je einen Modellaufruf. Eine Seite, die nicht passt, wird an
    Zeilengrenzen geteilt; nie mitten in einer Zeile."""
    teile: list[str] = []
    for seite in text.split("\f"):
        if not seite.strip():
            continue
        akt = ""
        for z in seite.splitlines(keepends=True):
            if akt and len(akt) + len(z) > max_chars:
                teile.append(akt)
                akt = ""
            akt += z
        if akt.strip():
            teile.append(akt)
    return teile


def normalisieren(roh: list) -> list[dict]:
    out = []
    for p in roh or []:
        if not isinstance(p, dict):
            continue
        name = invoice.clean(p.get("bezeichnung"))
        gesamt = zahl(p.get("gesamtpreis"))
        if not name or gesamt is None:
            continue
        satz = zahl(p.get("steuersatz"))
        out.append({
            "pos": invoice.clean(p.get("pos")),
            "artikelnummer": invoice.clean(p.get("artikelnummer")),
            "bezeichnung": name[:200],
            "menge": zahl(p.get("menge")),
            "einheit": invoice.clean(p.get("einheit")),
            "inhalt": invoice.clean(p.get("inhalt")),
            "einzelpreis": zahl(p.get("einzelpreis")),
            "gesamtpreis": gesamt,
            "steuersatz": satz if satz is not None and 0 <= satz <= 30 else None,
        })
    return out


def lesen(ki, modell: str, text: str, max_chars: int = ABSCHNITT_ZEICHEN) -> list[dict]:
    """Ein Modellaufruf je Abschnitt; die Positionen aller Abschnitte hintereinander."""
    alle: list[dict] = []
    for teil in abschnitte(text, max_chars)[:MAX_ABSCHNITTE]:
        try:
            antwort = ki.chat_json(modell, PROMPT, teil, SCHEMA, num_ctx=min(8192, ki.num_ctx))
        except ValueError:          # keine lesbare Antwort fuer diesen Abschnitt: die Summenprobe faengt es
            continue
        if isinstance(antwort, dict):
            alle.extend(normalisieren(antwort.get("positionen")))
        if len(alle) >= MAX_POSITIONEN:
            break
    return alle[:MAX_POSITIONEN]


def pruefen(pos: list[dict], inv: dict, text: str) -> tuple[str, str | None, list[str]]:
    """Feste Regeln, kein Modell. Liefert (status, preise, codes):
    status 'belegt', wenn jeder Zeilenbetrag woertlich im Text steht, Menge x Einzelpreis je Zeile den
    Zeilenbetrag ergibt und die Summe der Zeilen das Netto (preise='netto') oder das Brutto (preise='brutto')
    der Rechnung trifft. Sonst 'abweichend' mit dem Grund als Code."""
    if not pos:
        return "keine", None, ["POSITIONEN_NICHT_GELESEN"]
    flach = invoice._zahl_text(text)
    gruende = []
    if not all(im_text(p["gesamtpreis"], flach) for p in pos):
        gruende.append("POSITIONEN_BETRAG_NICHT_IM_TEXT")
    cent = Decimal("0.011")
    if any(p["menge"] is not None and p["einzelpreis"] is not None
           and abs(abs(p["menge"] * p["einzelpreis"]) - abs(p["gesamtpreis"])) > cent for p in pos):
        gruende.append("POSITIONEN_ZEILE_RECHNET_NICHT")
    summe = sum((p["gesamtpreis"] for p in pos), Decimal("0"))
    tol = min(Decimal("0.02") + Decimal("0.005") * len(pos), Decimal("0.50"))
    preise = None
    for art, ziel in (("netto", inv.get("net")), ("brutto", inv.get("gross"))):
        if ziel is not None and abs(abs(summe) - abs(ziel)) <= tol:
            preise = art
            break
    if preise is None:
        gruende.append("POSITIONEN_SUMME_WEICHT_AB")
    if gruende:
        return "abweichend", preise, gruende
    return "belegt", preise, ["POSITIONEN_BELEGT"]
