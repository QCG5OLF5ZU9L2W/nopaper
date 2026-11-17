# SPDX-License-Identifier: AGPL-3.0-or-later
"""Rechnungsdaten: JSON-Schema, Prompt, Normalisierung, Pruefregeln, CII-XML (Factur-X BASIC WL).

Uebernommen aus dem Prototyp rechnung-sidecar. Keine Netz- oder Dateisystemzugriffe ausser atomic_write.
"""
from __future__ import annotations

import datetime as dt
import os
import re
import tempfile
import xml.etree.ElementTree as ET
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

GUIDELINE_ID = "urn:factur-x.eu:1p0:basicwl"
SIDECAR_KENNUNG = "SCAN-SIDECAR/1.0"

# --------------------------------------------------------------------------- Hilfsfunktionen

def atomic_write(path: Path, data: bytes, mode: int = 0o640) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def dec(x) -> Decimal | None:
    if x is None or x == "":
        return None
    try:
        if isinstance(x, str):
            x = x.strip().replace(" ", "")
            if "," in x and "." in x:          # 1.234,56
                x = x.replace(".", "").replace(",", ".")
            elif "," in x:                       # 1234,56
                x = x.replace(",", ".")
        return Decimal(str(x))
    except (InvalidOperation, ValueError):
        return None


def money(d: Decimal) -> str:
    return str(d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def parse_date(s) -> dt.date | None:
    if not s or not isinstance(s, str):
        return None
    try:
        return dt.date.fromisoformat(s.strip()[:10])
    except ValueError:
        return None


def clean(s) -> str | None:
    if s is None:
        return None
    s = re.sub(r"\s+", " ", str(s)).strip()
    return s or None


# --------------------------------------------------------------------------- LLM-Extraktion

# Kategorien der Einordnung. Die ersten sechs sind rechnungsartig (Sidecar + Rechnungs-Tags).
KATEGORIEN_RECHNUNG = ["rechnung", "gutschrift", "kassenbon", "bewirtung", "versicherung", "behoerde"]
KATEGORIEN_SONST = ["mahnung", "bestellbestaetigung", "buchungsbestaetigung", "lieferschein", "brief", "sonstiges"]
KATEGORIEN = KATEGORIEN_RECHNUNG + KATEGORIEN_SONST


def ist_rechnungsartig(kategorie: str | None) -> bool:
    return kategorie in KATEGORIEN_RECHNUNG


def _nullable(t: str, desc: str | None = None) -> dict:
    d = {"type": [t, "null"]}
    if desc:
        d["description"] = desc
    return d


def build_schema(extract_buyer: bool = False) -> dict:
    party = {
        "type": "object",
        "properties": {
            "name": _nullable("string", "Firmenname des Rechnungsstellers"),
            "street": _nullable("string"),
            "postcode": _nullable("string"),
            "city": _nullable("string"),
            "country_code": _nullable("string", "ISO 3166-1 alpha-2, z.B. DE"),
            "vat_id": _nullable("string", "USt-IdNr., z.B. DE123456789"),
            "tax_number": _nullable("string", "Steuernummer, z.B. 30/472/50443"),
        },
        "required": ["name", "street", "postcode", "city", "country_code", "vat_id", "tax_number"],
    }
    props = {
        "kategorie": {"type": "string", "enum": KATEGORIEN},
        "invoice_number": _nullable("string"),
        "issue_date": _nullable("string", "Rechnungsdatum JJJJ-MM-TT"),
        "delivery_date": _nullable("string", "Leistungsdatum JJJJ-MM-TT"),
        "due_date": _nullable("string", "Faelligkeitsdatum JJJJ-MM-TT"),
        "currency": _nullable("string", "ISO 4217, z.B. EUR"),
        "seller": party,
        "payment": {
            "type": "object",
            "properties": {
                "iban": _nullable("string"),
                "bic": _nullable("string"),
                "reference": _nullable("string", "Verwendungszweck, Kassenzeichen, Vertrags- oder Kundenreferenz"),
            },
            "required": ["iban", "bic", "reference"],
        },
        "totals": {
            "type": "object",
            "properties": {
                "net_total": _nullable("number", "Summe aller Nettobetraege"),
                "tax_total": _nullable("number", "Summe aller Umsatzsteuerbetraege"),
                "gross_total": _nullable("number", "Rechnungsbetrag brutto"),
                "prepaid": _nullable("number", "bereits bezahlt (bar, Karte, PayPal, Lastschrift erfolgt)"),
                "amount_due": _nullable("number", "noch zu zahlen"),
            },
            "required": ["net_total", "tax_total", "gross_total", "prepaid", "amount_due"],
        },
        "payment_terms": {
            "type": "object",
            "properties": {
                "due_days": _nullable("number", "Zahlungsfrist in Tagen, wenn kein Faelligkeitsdatum genannt ist"),
                "skonto": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "percent": {"type": "number"},
                            "days": _nullable("number", "Skontofrist in Tagen"),
                            "until": _nullable("string", "Skonto bis Datum JJJJ-MM-TT"),
                            "amount": _nullable("number", "Skontobetrag, nur wenn ausgewiesen"),
                        },
                        "required": ["percent", "days", "until", "amount"],
                    },
                },
            },
            "required": ["due_days", "skonto"],
        },
        "vat_breakdown": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "rate": {"type": "number"},
                    "basis": _nullable("number"),
                    "tax": _nullable("number"),
                    "exemption_reason": _nullable("string"),
                },
                "required": ["rate", "basis", "tax", "exemption_reason"],
            },
        },
    }
    if extract_buyer:
        props["buyer"] = {
            "type": "object",
            "properties": {"name": _nullable("string"), "reference": _nullable("string", "Kundennummer")},
            "required": ["name", "reference"],
        }
    return {"type": "object", "properties": props, "required": list(props)}


EXTRACT_PROMPT = """Du ordnest ein gescanntes Dokument ein und liest bei Rechnungen die Kopfdaten aus.
Gib ausschliesslich die Felder des vorgegebenen JSON-Schemas zurueck.

kategorie:
- "rechnung": Rechnung eines Lieferanten oder Dienstleisters (auch Honorar-, Arzt-, Labor-, Software-, Webshop-Rechnung)
- "gutschrift": Gutschrift oder Rechnungskorrektur zu unseren Gunsten
- "kassenbon": Kassenbon/Quittung (Supermarkt, Tankstelle, Post, Kartenzahlungsbeleg mit Warenliste)
- "bewirtung": Restaurant-/Bewirtungsrechnung
- "versicherung": Beitragsrechnung einer Versicherung
- "behoerde": Kostenrechnung, Gebuehren- oder Kostenbescheid einer Behoerde oder eines Gerichts
- "mahnung": Mahnung oder Zahlungserinnerung (auch wenn Rechnungsnummern darin stehen)
- "bestellbestaetigung": Bestell- oder Auftragsbestaetigung
- "buchungsbestaetigung": Reise- oder Hotelbuchungsbestaetigung OHNE Rechnungsnummer
- "lieferschein": Lieferschein, Packzettel, Wareneingangsformular
- "brief", "sonstiges": alles andere
Steht "Rechnung" mit einer Rechnungsnummer auf dem Dokument, ist es eine Rechnung - auch bei Tickets,
Reisen, Abonnements oder wenn bereits bezahlt wurde.
Ist es keine der ersten sechs Kategorien, setze alle uebrigen Felder auf null bzw. [].

Regeln fuer die Rechnungsdaten:
- Erfinde nichts. Steht ein Wert nicht eindeutig im Text, setze null.
- Korrigiere offensichtliche OCR-Fehler in Namen (z.B. "Trattorla Esernpio" -> "Trattoria Esempio"), aber NIE in Zahlen.
- Datumsangaben als JJJJ-MM-TT. Ignoriere Eingangsstempel (z.B. "08 OKT. 2026" quer oder farbig) - massgeblich ist das
  gedruckte Rechnungs- bzw. Belegdatum. Zweistellige Jahre: "17.07.26" = 2026-07-17. Amerikanische Daten MM/DD/YYYY.
- Betraege als Zahl mit Punkt als Dezimaltrenner, ohne Tausendertrennzeichen und ohne Waehrungszeichen.
- seller ist der Rechnungssteller (Absender), nie der Empfaenger.
- tax_number ist die Steuernummer (auch "St.-Nr.", "Steuer-Nr."), vat_id die USt-IdNr. ("USt-ID", "Ust.-IdNr."),
  Leerzeichen darin entfernen.
- totals.gross_total ist der Endbetrag ("Rechnungsbetrag", "Endbetrag", "Gesamtbetrag", "zu zahlen", "Total"),
  nicht eine Zwischensumme. Versand, Gebuehren und Zuschlaege gehoeren in den Netto-/Endbetrag.
- totals.net_total und totals.tax_total sind die SUMMEN ueber alle Steuersaetze. Bei mehreren "NETTO"-Zeilen
  (z.B. 7 % und 19 %) diese addieren; jede Zeile gehoert zusaetzlich in vat_breakdown.
- vat_breakdown: eine Zeile je Steuersatz. Steuerfrei (z.B. "§ 4 Nr. 14 UStG", "§ 4 Nr. 10a UStG",
  "umsatzsteuerbefreit", Kleinunternehmer § 19 UStG): rate=0 und den Hinweis in exemption_reason.
- Versicherungsteuer ist KEINE Umsatzsteuer: bei Versicherungen tax_total=0 und in vat_breakdown rate=0, tax=0;
  der Bruttobetrag inklusive Versicherungsteuer.
- Kassenbons: invoice_number = Bon-, Beleg- oder Rechnungsnummer, falls vorhanden - NICHT Steuernummer,
  Kassen-, Terminal-, Transaktions- oder Filialnummer; bezahlter Betrag in prepaid.
  "gegeben"/"Rueckgeld": prepaid ist hoechstens der Endbetrag, amount_due dann 0.
- payment_terms: due_days = Zahlungsfrist in Tagen ("zahlbar innerhalb 14 Tagen"), wenn kein Datum genannt ist.
  skonto: eine Zeile je Skontostufe ("2 % Skonto bei Zahlung innerhalb 10 Tagen" -> percent 2, days 10);
  until nur bei genanntem Datum, amount nur bei ausgewiesenem Skontobetrag. Kein Skonto: [].
- exemption_reason nur, wenn ein Befreiungshinweis im Text steht; sonst null.
- Uebernimm KEINE Leistungsbeschreibungen, Diagnosen, Behandlungsangaben, Patienten- oder sonstigen
  Personendaten aus den Positionen. Diese Felder gibt es im Schema nicht."""


# --------------------------------------------------------------------------- Pruefung

TOL = Decimal("0.02")
KLEINBETRAG = Decimal("250")
EU = {"AT", "BE", "BG", "CY", "CZ", "DE", "DK", "EE", "EL", "GR", "ES", "FI", "FR", "HR", "HU", "IE", "IT", "LT",
      "LU", "LV", "MT", "NL", "PL", "PT", "RO", "SE", "SI", "SK", "XI"}
# typische OCR-Verwechslungen in Zahlen (nur fuer die Verankerung)
_OCR_ZIFFER = str.maketrans({"O": "0", "o": "0", "D": "0", "Q": "0", "I": "1", "l": "1", "|": "1", "S": "5",
                             "B": "8", "Z": "2"})


def iban_ok(iban: str) -> bool:
    s = re.sub(r"\s+", "", iban).upper()
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", s):
        return False
    r = s[4:] + s[:4]
    num = "".join(str(int(c, 36)) for c in r)
    return int(num) % 97 == 1


def vat_id_ok(v: str) -> bool:
    s = re.sub(r"[\s.]+", "", v).upper()
    if s.startswith("DE"):
        return re.fullmatch(r"DE\d{9}", s) is not None
    if s[:2] in EU:
        return re.fullmatch(r"[A-Z]{2}[0-9A-Z+*]{2,13}", s) is not None
    return True  # ausserhalb der EU kein festes Format


def steuernummer_ok(v: str) -> bool:
    """Deutsche Steuernummer: Finanzamtsformat (z.B. 30/472/50443, 346/5871/1049) oder 13-stellig (ELSTER)."""
    s = re.sub(r"\s+", "", v)
    return bool(re.fullmatch(r"\d{2,3}/\d{3,4}/\d{4,5}", s) or re.fullmatch(r"\d{10,13}", s)
                or re.fullmatch(r"\d{4}/\d{9}", s))


def amount_variants(d: Decimal) -> set[str]:
    q = abs(d).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    i, f = f"{q:.2f}".split(".")
    grp = f"{int(i):,}"
    return {f"{i},{f}", f"{grp.replace(',', '.')},{f}", f"{i}.{f}", f"{grp},{f}"}


def _zahl_text(text: str) -> str:
    """Text ohne Leerraum, mit OCR-Verwechslungen in Zahlnaehe korrigiert ('68,DO' -> '68,00')."""
    flat = re.sub(r"\s+", "", text)
    return re.sub(r"(?<=[\d,.])[ODQIlSBZo|]+|[ODQIlSBZo|]+(?=[\d,.]\d)",
                  lambda m: m.group(0).translate(_OCR_ZIFFER), flat)


def date_variants(d: dt.date) -> list[str]:
    return [d.strftime("%d.%m.%Y"), d.strftime("%d.%m.%y"), d.isoformat(), d.strftime("%d/%m/%Y"),
            d.strftime("%m/%d/%Y"), d.strftime("%d-%m-%Y"), f"{d.day}.{d.month}.{d.year}",
            d.strftime("%d.%m.%Y").replace(".", ". "), d.strftime("%Y%m%d")]


_MONATE = ["jan", "feb", "m", "apr", "mai", "jun", "jul", "aug", "sep", "okt", "nov", "dez"]


def datum_im_text(d: dt.date, text: str) -> bool:
    flat = re.sub(r"\s+", "", text)
    if any(v.replace(" ", "") in flat for v in date_variants(d)):
        return True
    # "28. Mai 2026", "im Mai 2026"
    low = text.lower()
    mon = ["januar", "februar", "märz", "april", "mai", "juni", "juli", "august", "september", "oktober",
           "november", "dezember"][d.month - 1]
    return re.search(rf"\b0?{d.day}\.?\s*{mon}\s*{d.year}", low) is not None


def datum_mehrdeutig(d: dt.date, text: str) -> bool:
    """Datum steht nur mit Schraegstrichen im Text und Tag/Monat sind vertauschbar (z.B. 08/03/2026)."""
    if d.day > 12 or d.day == d.month:
        return False
    flat = re.sub(r"\s+", "", text)
    eindeutig = [d.strftime("%d.%m.%Y"), d.strftime("%d.%m.%y"), d.isoformat()]
    if any(v in flat for v in eindeutig):
        return False
    return d.strftime("%m/%d/%Y") in flat or d.strftime("%d/%m/%Y") in flat


def name_im_text(name: str, text: str) -> float:
    """Aehnlichkeit 0..1 des besten Textfensters zum Namen (fuer OCR-verrauschte Firmennamen)."""
    n = re.sub(r"\s+", " ", name.lower()).strip()
    if not n:
        return 0.0
    t = re.sub(r"\s+", " ", text.lower())
    if n in t:
        return 1.0
    import difflib
    best, L = 0.0, len(n)
    for i in range(0, max(1, len(t) - L + 1), max(1, L // 4)):
        r = difflib.SequenceMatcher(None, n, t[i:i + L + 4]).ratio()
        if r > best:
            best = r
    return best


_FALSCHE_NR = re.compile(r"kunde|kd\.?-?n|auftrag|bestell|lieferschein|order|vertrag|mandat|ticket|terminal|"
                         r"transakt|kasse|steuer|ust|tel|fax|iban|hrb|hra", re.I)
_RICHTIGE_NR = re.compile(r"rechn|\bre\.?-?nr|beleg|\bbon\b|bon-?nr|invoice|\binv\b|quittung", re.I)


def nummer_bezeichnung_falsch(nummer: str, text: str) -> bool:
    """True, wenn die Nummer im Text nur in Zeilen mit fremder Bezeichnung (Kunden-, Auftrags-, Bestell-Nr. ...)
    steht und nie bei einer Rechnungs-/Belegbezeichnung."""
    n = re.sub(r"\s+", "", nummer).upper()
    if len(n) < 4:
        return False
    zeilen = [z for z in text.splitlines() if n in re.sub(r"\s+", "", z).upper()]
    if not zeilen:
        return False
    for z in zeilen:
        vor = z.upper().split(n[:4])[0] if n[:4] in z.upper() else z
        vor = vor[-28:]  # nur die Bezeichnung unmittelbar vor der Nummer
        if _RICHTIGE_NR.search(vor) or not _FALSCHE_NR.search(vor):
            return False
    return True


_ZWISCHEN = re.compile(r"übertrag|uebertrag|zwischensumme|vortrag|warenwert|nettowarenwert|subtotal|sub-total", re.I)
_ENDBETRAG = re.compile(r"endbetrag|gesamt|rechnungsbetrag|zu\s*zahlen|zahlbetrag|total|brutto|summe\s*eur|"
                        r"betrag|bar\b|karte|mastercard|visa|girocard", re.I)


def brutto_ist_zwischensumme(gross: Decimal, text: str) -> bool:
    """True, wenn der Bruttobetrag nur in Zeilen mit 'Uebertrag'/'Zwischensumme' steht (z.B. Seitenuebertrag
    einer mehrseitigen Rechnung) und nie bei einer Endbetrag-Bezeichnung."""
    varianten = amount_variants(gross)
    zeilen = [z for z in text.splitlines() if any(v in re.sub(r"\s+", "", z) for v in varianten)]
    if not zeilen:
        return False
    return all(_ZWISCHEN.search(z) and not _ENDBETRAG.search(_ZWISCHEN.sub("", z)) for z in zeilen)


def normalize(raw: dict, buyer_name: str | None) -> dict:
    """LLM-Ausgabe in typisierte, bereinigte Struktur ueberfuehren."""
    s = raw.get("seller") or {}
    p = raw.get("payment") or {}
    t = raw.get("totals") or {}
    kat = raw.get("kategorie")
    if kat not in KATEGORIEN:  # aeltere Antworten / Prototyp
        kat = {"gutschrift": "gutschrift", "keine_rechnung": "sonstiges"}.get(raw.get("document_type"), "rechnung")
    inv = {
        "kategorie": kat,
        "document_type": "gutschrift" if kat == "gutschrift" else (
            "rechnung" if ist_rechnungsartig(kat) else "keine_rechnung"),
        "invoice_number": clean(raw.get("invoice_number")),
        "issue_date": parse_date(raw.get("issue_date")),
        "delivery_date": parse_date(raw.get("delivery_date")),
        "due_date": parse_date(raw.get("due_date")),
        "currency": (clean(raw.get("currency")) or "EUR").upper()[:3],
        "seller": {k: clean(s.get(k)) for k in
                   ("name", "street", "postcode", "city", "country_code", "vat_id", "tax_number")},
        "buyer_name": buyer_name or None,
        "buyer_reference": None,
        "iban": re.sub(r"[\s.,]+", "", p.get("iban") or "").upper() or None,
        "bic": re.sub(r"\s+", "", p.get("bic") or "").upper() or None,
        "payment_reference": clean(p.get("reference")),
        "net": dec(t.get("net_total")),
        "tax": dec(t.get("tax_total")),
        "gross": dec(t.get("gross_total")),
        "prepaid": dec(t.get("prepaid")),
        "due": dec(t.get("amount_due")),
        "vat": [],
        "due_days": None,
        "skonto": [],
    }
    pt = raw.get("payment_terms") or {}
    tage = dec(pt.get("due_days"))
    if tage is not None and tage == tage.to_integral_value() and 0 < tage <= 365:
        inv["due_days"] = int(tage)
    for row in pt.get("skonto") or []:
        if not isinstance(row, dict):
            continue
        proz, st = dec(row.get("percent")), dec(row.get("days"))
        if proz is None or not (Decimal("0") < proz <= Decimal("10")):
            continue
        inv["skonto"].append({
            "percent": proz,
            "days": int(st) if st is not None and st == st.to_integral_value() and 0 < st <= 365 else None,
            "until": parse_date(row.get("until")),
            "amount": dec(row.get("amount")),
        })
    if inv["seller"]["country_code"]:
        inv["seller"]["country_code"] = inv["seller"]["country_code"].upper()[:2]
    for k in ("vat_id",):
        if inv["seller"][k]:
            inv["seller"][k] = re.sub(r"[\s.]+", "", inv["seller"][k]).upper()
    if inv["seller"]["tax_number"]:
        inv["seller"]["tax_number"] = re.sub(r"\s+", "", inv["seller"]["tax_number"])
    for row in raw.get("vat_breakdown") or []:
        rate = dec(row.get("rate"))
        if rate is None:
            continue
        inv["vat"].append({
            "rate": rate,
            "basis": dec(row.get("basis")),
            "tax": dec(row.get("tax")),
            "exemption_reason": clean(row.get("exemption_reason")),
        })
    return inv


def ist_kleinbetrag(inv: dict) -> bool:
    return inv["gross"] is not None and inv["currency"] == "EUR" and abs(inv["gross"]) <= KLEINBETRAG


def ist_inland_eu(inv: dict) -> bool:
    cc = inv["seller"]["country_code"]
    vid = inv["seller"]["vat_id"] or ""
    return (cc or vid[:2] or "DE") in EU


def validate(inv: dict, text: str, today: dt.date | None = None) -> list[str]:
    """Liefert Pruefcodes ohne Inhaltsdaten. Regeln je Kategorie:
    - Kassenbon/Bewirtung bis 250 EUR (Kleinbetrag, § 33 UStDV): keine Rechnungsnummer, kein Empfaenger,
      keine Steuernummer erforderlich.
    - Versicherung/Behoerde: keine Steuernummer erforderlich; Referenz (Vertrag/Kassenzeichen) ersetzt die Nummer.
    - Rechnungssteller ausserhalb der EU: keine deutsche Steuernummer/USt-IdNr. erforderlich.
    """
    today = today or dt.date.today()
    issues: list[str] = []
    kat = inv.get("kategorie", "rechnung")
    flat = _zahl_text(text)
    flat_up = re.sub(r"\s+", "", text).upper()
    klein = ist_kleinbetrag(inv)
    amtlich = kat in ("versicherung", "behoerde")
    ausland = not ist_inland_eu(inv)

    if not ist_rechnungsartig(kat):
        issues.append("KEINE_RECHNUNG")

    if inv["invoice_number"] is None and not klein and not amtlich:
        issues.append("FEHLT_RECHNUNGSNUMMER")
    if inv["issue_date"] is None:
        issues.append("FEHLT_RECHNUNGSDATUM")
    if inv["gross"] is None:
        issues.append("FEHLT_BRUTTO")
    if not inv["seller"]["name"]:
        issues.append("FEHLT_RECHNUNGSSTELLER")
    if not inv["buyer_name"] and not klein:
        issues.append("FEHLT_RECHNUNGSEMPFAENGER")
    if not (inv["seller"]["vat_id"] or inv["seller"]["tax_number"]) and not (klein or amtlich or ausland):
        issues.append("FEHLT_STEUERNUMMER_UND_USTID")

    # Verankerung: Kernwerte muessen im OCR-Text vorkommen (Schutz gegen Halluzination)
    if inv["invoice_number"] and re.sub(r"\s+", "", inv["invoice_number"]).upper() not in flat_up:
        issues.append("RECHNUNGSNUMMER_NICHT_IM_TEXT")
    elif inv["invoice_number"] and nummer_bezeichnung_falsch(inv["invoice_number"], text):
        issues.append("RECHNUNGSNUMMER_FALSCHE_BEZEICHNUNG")
    if inv["gross"] is not None and not any(v in flat for v in amount_variants(inv["gross"])):
        issues.append("BRUTTO_NICHT_IM_TEXT")
    if inv["gross"] is not None and brutto_ist_zwischensumme(inv["gross"], text):
        issues.append("BRUTTO_IST_ZWISCHENSUMME")
    if inv["iban"] and inv["iban"] not in re.sub(r"[\s.,]+", "", text).upper():
        issues.append("IBAN_NICHT_IM_TEXT")
    if inv["issue_date"] and not datum_im_text(inv["issue_date"], text):
        issues.append("DATUM_NICHT_IM_TEXT")
    if inv["issue_date"] and datum_mehrdeutig(inv["issue_date"], text):
        issues.append("DATUM_MEHRDEUTIG")
    if inv["seller"]["name"]:
        r = name_im_text(inv["seller"]["name"], text)
        if r < 0.6:
            issues.append("RECHNUNGSSTELLER_NICHT_IM_TEXT")

    # Formate
    if inv["iban"] and not iban_ok(inv["iban"]):
        issues.append("IBAN_PRUEFZIFFER")
    if inv["seller"]["vat_id"] and not vat_id_ok(inv["seller"]["vat_id"]):
        issues.append("USTID_FORMAT")
    if not re.fullmatch(r"[A-Z]{3}", inv["currency"] or ""):
        issues.append("WAEHRUNG_FORMAT")

    # Datum
    if inv["issue_date"] and inv["issue_date"] > today + dt.timedelta(days=1):
        issues.append("RECHNUNGSDATUM_IN_ZUKUNFT")
    if inv["issue_date"] and inv["due_date"] and inv["due_date"] < inv["issue_date"]:
        issues.append("FAELLIGKEIT_VOR_RECHNUNGSDATUM")

    # Versicherungsteuer ist keine Vorsteuer
    if "versicherungsteuer" in text.lower().replace("-", "") and inv["tax"] and inv["tax"] != 0:
        issues.append("VERSICHERUNGSTEUER_ALS_UST")

    # Summen
    net, tax, gross = inv["net"], inv["tax"], inv["gross"]
    if net is not None and tax is not None and gross is not None and abs(net + tax - gross) > TOL:
        issues.append("SUMME_NETTO_STEUER_BRUTTO")
    if inv["vat"]:
        n = len(inv["vat"])
        if net is not None and all(r["basis"] is not None for r in inv["vat"]):
            if abs(sum(r["basis"] for r in inv["vat"]) - net) > TOL * n:
                issues.append("SUMME_UST_BASIS")
        if tax is not None and all(r["tax"] is not None for r in inv["vat"]):
            if abs(sum(r["tax"] for r in inv["vat"]) - tax) > TOL * n:
                issues.append("SUMME_UST_BETRAG")
        for r in inv["vat"]:
            if r["basis"] is not None and r["tax"] is not None:
                # Brutto-Kassen rechnen die Steuer aus dem Bruttobetrag: Toleranz je Satz etwas groesser
                if abs(r["basis"] * r["rate"] / 100 - r["tax"]) > TOL + Decimal("0.01"):
                    issues.append("UST_SATZ_RECHNUNG")
                    break
        if not (klein or ausland):
            for r in inv["vat"]:
                if r["rate"] == 0 and not r["exemption_reason"]:
                    issues.append("UST_BEFREIUNG_OHNE_GRUND")
                    break
    elif tax is not None and tax != 0:
        issues.append("FEHLT_UST_AUFSCHLUESSELUNG")
    return issues


BEFREIUNG_STANDARD = {
    "versicherung": "Versicherungsleistung, umsatzsteuerfrei (§ 4 Nr. 10 UStG)",
    "behoerde": "Hoheitliche Leistung, nicht umsatzsteuerbar",
}


def complete_vat(inv: dict, text: str | None = None) -> list[str]:
    """Rechnerische Ergaenzung/Reparatur – nur wenn das Ergebnis in sich stimmig ist und (wo moeglich)
    durch den Text belegt wird. Jede Reparatur hinterlaesst einen Hinweis-Code."""
    notes: list[str] = []
    rows = inv["vat"]
    kat = inv.get("kategorie", "rechnung")
    flat = _zahl_text(text) if text else None

    # a) Steuersatz aus Basis/Steuer, wenn der Satz nicht passt (z.B. abgeschnittener Bon) und 7/19 % passt
    for r in rows:
        if r["basis"] and r["tax"] and abs(r["basis"] * r["rate"] / 100 - r["tax"]) > TOL + Decimal("0.01"):
            for satz in (Decimal("7"), Decimal("19")):
                if abs(r["basis"] * satz / 100 - r["tax"]) <= Decimal("0.02"):
                    r["rate"] = satz
                    r["exemption_reason"] = None
                    notes.append("UST_SATZ_BERECHNET")
                    break
    # b0) Versicherungsbeitraege sind umsatzsteuerfrei (§ 4 Nr. 10 UStG): ausgewiesene "Steuer" ist
    #     Versicherungsteuer und keine Vorsteuer -> Nullsatz, Netto = Brutto
    if kat == "versicherung" and text and "versicherungsteuer" in text.lower().replace("-", "") \
            and inv["gross"] is not None and (inv["tax"] or any(r["rate"] for r in rows)):
        inv["vat"] = rows = [{"rate": Decimal("0"), "basis": inv["gross"], "tax": Decimal("0"),
                              "exemption_reason": BEFREIUNG_STANDARD["versicherung"]}]
        inv["net"], inv["tax"] = inv["gross"], Decimal("0")
        notes.append("VERSICHERUNGSTEUER_KORRIGIERT")
    # b) Versicherung/Behoerde: Nullsatz ohne Steuer (Versicherungsteuer faelschlich als USt eingetragen)
    if kat in ("versicherung", "behoerde"):
        for r in rows:
            if r["rate"] == 0 and r["tax"] not in (None, Decimal("0")):
                r["tax"] = Decimal("0")
                if inv["gross"] is not None and len(rows) == 1:
                    r["basis"] = inv["gross"]
                notes.append("UST_NULLSATZ_KORRIGIERT")
        if all(r["rate"] == 0 for r in rows) and inv["tax"]:
            inv["tax"] = Decimal("0")
            if inv["gross"] is not None:
                inv["net"] = inv["gross"]
            notes.append("UST_NULLSATZ_KORRIGIERT")
    # c) Befreiungsgrund muss im Text stehen, sonst Standard der Kategorie bzw. leer
    if text:
        for r in rows:
            if r["rate"] == 0 and r["exemption_reason"] and name_im_text(r["exemption_reason"], text) < 0.6:
                r["exemption_reason"] = BEFREIUNG_STANDARD.get(kat)
                notes.append("BEFREIUNGSGRUND_ERSETZT")
    # d) Summen aus einer in sich stimmigen Aufschluesselung; Brutto nur, wenn die Summe im Text steht
    if rows and all(r["basis"] is not None and r["tax"] is not None for r in rows):
        sb = sum(r["basis"] for r in rows)
        st = sum(r["tax"] for r in rows)
        stimmig = all(abs(r["basis"] * r["rate"] / 100 - r["tax"]) <= TOL + Decimal("0.01") for r in rows)
        brutto_belegt = inv["gross"] is not None and flat is not None and \
            any(v in flat for v in amount_variants(inv["gross"]))
        if stimmig and inv["gross"] is not None and abs(sb + st - inv["gross"]) <= TOL:
            if inv["net"] != sb or inv["tax"] != st:
                if inv["net"] is not None or inv["tax"] is not None:
                    notes.append("SUMMEN_AUS_AUFSCHLUESSELUNG")
                inv["net"], inv["tax"] = sb, st
        elif stimmig and flat is not None and not brutto_belegt and \
                any(v in flat for v in amount_variants(sb + st)):
            inv["gross"], inv["net"], inv["tax"] = sb + st, sb, st
            if inv["prepaid"] is not None:
                inv["prepaid"] = None
            inv["due"] = None
            notes.append("BRUTTO_AUS_AUFSCHLUESSELUNG")
    # e) fehlende Aufschluesselung aus Summen ableiten
    if not rows and inv["net"] is not None and inv["tax"] is not None:
        if inv["net"] != 0:
            rate = (inv["tax"] / inv["net"] * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        else:
            rate = Decimal("0")
        if rate in (Decimal("0"), Decimal("7"), Decimal("19")):
            inv["vat"].append({"rate": rate, "basis": inv["net"], "tax": inv["tax"],
                               "exemption_reason": BEFREIUNG_STANDARD.get(kat) if rate == 0 else None})
            notes.append("UST_AUFSCHLUESSELUNG_BERECHNET")
    if inv["net"] is None and inv["gross"] is not None and inv["tax"] is not None:
        inv["net"] = inv["gross"] - inv["tax"]
        notes.append("NETTO_BERECHNET")
    # f) "Rechnungsnummer", die in Wahrheit Steuernummer oder USt-IdNr. ist
    nr = inv.get("invoice_number")
    if nr and (re.fullmatch(r"\d{2,3}/\d{3,4}/\d{3,5}\.?", nr.replace(" ", "")) or
               re.fullmatch(r"[A-Z]{2}\d{8,12}", nr.replace(" ", "").upper())) \
            and not (text and nummer_hat_belegbezeichnung(nr, text)):
        if text and nummer_hat_belegbezeichnung(nr, text, abweichung=1):
            # Bild und Texterkennung widersprechen sich in einer Ziffer: Wert behalten, die Verankerung
            # (RECHNUNGSNUMMER_NICHT_IM_TEXT) setzt den Beleg auf "pruefen"
            notes.append("RECHNUNGSNUMMER_ABWEICHEND")
        else:
            inv["invoice_number"] = None
            notes.append("RECHNUNGSNUMMER_VERWORFEN")
    # g) Steuernummer mit falschem Format (z.B. IK-Nummer, Kassennummer) verwerfen
    tn = inv["seller"].get("tax_number")
    if tn and not steuernummer_ok(tn) and ist_inland_eu(inv):
        inv["seller"]["tax_number"] = None
        notes.append("STEUERNUMMER_VERWORFEN")
    # h) Rueckgeld: "gegeben" groesser als der Endbetrag -> bezahlt ist der Endbetrag, offen bleibt nichts
    if inv["gross"] is not None and inv["gross"] > 0 and inv["prepaid"] is not None \
            and inv["prepaid"] > inv["gross"] + TOL:
        inv["prepaid"], inv["due"] = inv["gross"], Decimal("0")
        notes.append("RUECKGELD_ERKANNT")
    # h2) Kassenbon/Bewirtung mit Zahlungsvermerk im Text, aber ohne "bezahlt"-Angabe des Modells
    if kat in ("kassenbon", "bewirtung") and inv["prepaid"] is None and inv["gross"] is not None \
            and inv["gross"] > 0 and text and _BEZAHLT.search(text):
        inv["prepaid"], inv["due"] = inv["gross"], Decimal("0")
        notes.append("ZAHLUNG_AUS_TEXT")
    if inv["due"] is None and inv["gross"] is not None:
        inv["due"] = inv["gross"] - (inv["prepaid"] or Decimal("0"))
    # h3) Verwendungszweck: muss woertlich im Text stehen (eine falsche Angabe macht eine Ueberweisung
    #     unzuordenbar); sonst ein ausdruecklich bezeichnetes Kassenzeichen aus dem Text
    if text:
        kompakt = re.sub(r"\s+", "", text).upper()
        ref = inv.get("payment_reference")
        if ref and re.sub(r"\s+", "", ref).upper() not in kompakt:
            inv["payment_reference"] = ref = None
            notes.append("VERWENDUNGSZWECK_VERWORFEN")
        if not ref:
            kz = kassenzeichen_im_text(text)
            if kz:
                inv["payment_reference"] = kz
                notes.append("VERWENDUNGSZWECK_AUS_TEXT")
    # i) Faelligkeit aus einer Frist in Tagen, wenn kein Datum genannt ist und die Frist im Text steht
    if inv["due_date"] is None and inv.get("due_days") and inv["issue_date"] and text \
            and _tage_im_text(inv["due_days"], text):
        inv["due_date"] = inv["issue_date"] + dt.timedelta(days=inv["due_days"])
        notes.append("FAELLIGKEIT_BERECHNET")
    # j) Skonto: nur, wenn "Skonto" und der Satz im Text stehen; Frist als Datum ergaenzen
    if inv.get("skonto"):
        behalten = []
        for s in inv["skonto"]:
            if not text or not _skonto_im_text(s["percent"], text):
                continue
            if s["days"] is not None and not _tage_im_text(s["days"], text):
                s["days"] = None
            if s["until"] is not None and not datum_im_text(s["until"], text):
                s["until"] = None
            if s["until"] is None and s["days"] is not None and inv["issue_date"]:
                s["until"] = inv["issue_date"] + dt.timedelta(days=s["days"])
            if s["amount"] is not None and not any(v in _zahl_text(text) for v in amount_variants(s["amount"])):
                s["amount"] = None
            behalten.append(s)
        if len(behalten) != len(inv["skonto"]):
            notes.append("SKONTO_VERWORFEN")
        inv["skonto"] = behalten
    return list(dict.fromkeys(notes))


_BEZAHLT = re.compile(r"zahlung\s+erfolgt|bezahlung|bezahlt|betrag\s+erhalten|gegeben|r(?:ü|ue)ckgeld|\bbar\b|"
                      r"kreditkarte|girocard|ec-?karte|ec-?cash|kartenzahlung|kontaktlos|\bvisa\b|mastercard|maestro",
                      re.I)
_KASSENZEICHEN = re.compile(r"K\s?A\s?S\s?S\s?E\s?N\s?Z\s?E\s?I\s?C\s?H\s?E\s?N[^0-9A-Za-zÄÖÜäöü]{0,40}"
                            r"(\d[\d ]{6,24}\d)", re.I)


def kassenzeichen_im_text(text: str) -> str | None:
    """Nummer, die unmittelbar auf das Wort 'Kassenzeichen' folgt (auch gesperrt gedruckt, auch in der
    naechsten Zeile). Nur eindeutig: stehen verschiedene Nummern dahinter, None."""
    funde = {re.sub(r"\s+", "", m.group(1)) for m in _KASSENZEICHEN.finditer(text)}
    return funde.pop() if len(funde) == 1 else None


_BELEG_LABEL = re.compile(r"(?:be|bz|bc|b)l[ea]g|\bbon\b|bon-?nr|rechn|quittung|invoice", re.I)


def nummer_hat_belegbezeichnung(nummer: str, text: str, abweichung: int = 0) -> bool:
    """True, wenn unmittelbar vor der Nummer eine Beleg-/Bon-/Rechnungsbezeichnung steht
    (auch mit ueblichen OCR-Fehlern wie 'Bzleg-Nr.' oder 'Belag-Nr.').
    abweichung=1: die Nummer im Text darf sich in genau einer Ziffer unterscheiden (gleiche Laenge,
    gleiche Trennzeichen) – fuer den Fall, dass Bildmodell und Texterkennung verschieden lesen."""
    n = re.sub(r"\s+", "", nummer).upper()
    if abweichung:
        for z in text.splitlines():
            for m in re.finditer(r"[0-9A-Z][0-9A-Z/.\-]{3,}", z.upper()):
                k = m.group(0)
                if len(k) != len(n) or k == n:
                    continue
                unterschiede = [(a, b) for a, b in zip(k, n) if a != b]
                if 0 < len(unterschiede) <= abweichung and all(a.isdigit() and b.isdigit() for a, b in unterschiede):
                    vor = z[max(0, m.start() - 28):m.start()]
                    if _BELEG_LABEL.search(vor) and not re.search(r"steuer|ust|st\.?-?nr", vor, re.I):
                        return True
        return False
    for z in text.splitlines():
        zu = z.upper()
        i = re.sub(r"\s+", "", zu).find(n)
        if i < 0:
            continue
        j = zu.find(n[:3])
        vor = z[max(0, j - 28):j] if j >= 0 else z
        if _BELEG_LABEL.search(vor) and not re.search(r"steuer|ust|st\.?-?nr", vor, re.I):
            return True
    return False


def _tage_im_text(tage: int, text: str) -> bool:
    return re.search(rf"(?<!\d){tage}\s*(?:tag|kalendertag|werktag|days)", text, re.I) is not None


def _skonto_im_text(prozent: Decimal, text: str) -> bool:
    if "skonto" not in text.lower():
        return False
    p = format(prozent.normalize(), "f")
    ganz, _, nach = p.partition(".")
    muster = rf"(?<![\d,.]){ganz}" + (rf"[.,]{nach}0*" if nach else r"(?:[.,]0+)?") + r"\s*(?:%|v\.?\s?h|prozent)"
    return re.search(muster, text, re.I) is not None


def steuer_nach_klasse(inv: dict) -> tuple[Decimal | None, Decimal | None]:
    """Steuerbetrag zum Regelsatz (Saetze ab 10 %) und zum ermaessigten Satz (ueber 0, unter 10 %).
    None, wenn der Beleg keinen Betrag dieser Klasse ausweist."""
    hoch = [r["tax"] for r in inv["vat"] if r["rate"] >= 10 and r["tax"] is not None]
    niedrig = [r["tax"] for r in inv["vat"] if 0 < r["rate"] < 10 and r["tax"] is not None]
    return (sum(hoch) if hoch else None, sum(niedrig) if niedrig else None)


def titel(inv: dict, vorlage: str) -> str | None:
    """Dokumenttitel aus den Rechnungsdaten, z.B. '2026-11-05 Muster Tankstelle 60,58 EUR'.
    Platzhalter: {datum} {lieferant} {betrag} {nummer} {belegart}. Fehlende Teile entfallen.
    None, wenn weder Datum noch Lieferant bekannt sind (dann bleibt der bisherige Titel)."""
    if not vorlage.strip() or not (inv.get("issue_date") or inv["seller"].get("name")):
        return None
    betrag = ""
    if inv.get("gross") is not None:
        betrag = money(inv["gross"]).replace(".", ",") + " " + (inv.get("currency") or "EUR")
    teile = {
        "datum": inv["issue_date"].isoformat() if inv.get("issue_date") else "",
        "lieferant": re.sub(r"\s+", " ", inv["seller"].get("name") or "")[:60].strip(),
        "betrag": betrag,
        "nummer": inv.get("invoice_number") or "",
        "belegart": inv.get("kategorie") or "",
    }
    try:
        t = vorlage.format(**teile)
    except (KeyError, IndexError, ValueError):
        return None
    t = re.sub(r"\s+", " ", t).strip(" -_,")
    return t[:127] or None


def skonto_text(inv: dict) -> str | None:
    """Maschinenlesbar je Stufe 'satz:frist:betrag', Stufen mit ';' getrennt (wie Aequa F66).
    Frist als Datum JJJJ-MM-TT, ersatzweise 'T<tage>'; leerer Teil = nicht auf dem Beleg."""
    teile = []
    for s in inv.get("skonto") or []:
        frist = s["until"].isoformat() if s["until"] else (f"T{s['days']}" if s["days"] else "")
        teile.append(f"{format(s['percent'].normalize(), 'f')}:{frist}:{money(s['amount']) if s['amount'] is not None else ''}")
    return ";".join(teile) or None


# --------------------------------------------------------------------------- CII-XML

NS = {
    "rsm": "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100",
    "ram": "urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100",
    "qdt": "urn:un:unece:uncefact:data:standard:QualifiedDataType:100",
    "udt": "urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100",
}
for _p, _u in NS.items():
    ET.register_namespace(_p, _u)


def _q(tag: str) -> str:
    p, n = tag.split(":")
    return f"{{{NS[p]}}}{n}"


def _sub(parent, tag, text=None, **attrs):
    el = ET.SubElement(parent, _q(tag), {k: v for k, v in attrs.items() if v is not None})
    if text is not None:
        el.text = str(text)
    return el


def _date(parent, tag, d: dt.date):
    _sub(_sub(parent, tag), "udt:DateTimeString", d.strftime("%Y%m%d"), format="102")


def build_cii(inv: dict, prov: dict) -> bytes:
    root = ET.Element(_q("rsm:CrossIndustryInvoice"))

    ctx = _sub(root, "rsm:ExchangedDocumentContext")
    _sub(_sub(ctx, "ram:GuidelineSpecifiedDocumentContextParameter"), "ram:ID", GUIDELINE_ID)

    doc = _sub(root, "rsm:ExchangedDocument")
    if inv["invoice_number"]:
        _sub(doc, "ram:ID", inv["invoice_number"])
    _sub(doc, "ram:TypeCode", "381" if inv["document_type"] == "gutschrift" else "380")
    if inv["issue_date"]:
        _date(doc, "ram:IssueDateTime", inv["issue_date"])
    # Herkunftsnachweis als maschinenlesbare Notiz (BT-22), Schluessel=Wert je Zeile
    note_lines = [SIDECAR_KENNUNG] + [f"{k}={v}" for k, v in prov.items() if v not in (None, "")]
    _sub(_sub(doc, "ram:IncludedNote"), "ram:Content", "\n".join(note_lines))

    tx = _sub(root, "rsm:SupplyChainTradeTransaction")

    agr = _sub(tx, "ram:ApplicableHeaderTradeAgreement")
    if inv["buyer_reference"]:
        _sub(agr, "ram:BuyerReference", inv["buyer_reference"])
    s = inv["seller"]
    sp = _sub(agr, "ram:SellerTradeParty")
    if s["name"]:
        _sub(sp, "ram:Name", s["name"])
    if any(s[k] for k in ("street", "postcode", "city", "country_code")):
        addr = _sub(sp, "ram:PostalTradeAddress")
        if s["postcode"]:
            _sub(addr, "ram:PostcodeCode", s["postcode"])
        if s["street"]:
            _sub(addr, "ram:LineOne", s["street"])
        if s["city"]:
            _sub(addr, "ram:CityName", s["city"])
        _sub(addr, "ram:CountryID", s["country_code"] or "DE")
    if s["vat_id"]:
        _sub(_sub(sp, "ram:SpecifiedTaxRegistration"), "ram:ID", s["vat_id"], schemeID="VA")
    if s["tax_number"]:
        _sub(_sub(sp, "ram:SpecifiedTaxRegistration"), "ram:ID", s["tax_number"], schemeID="FC")
    bp = _sub(agr, "ram:BuyerTradeParty")
    if inv["buyer_name"]:
        _sub(bp, "ram:Name", inv["buyer_name"])

    dlv = _sub(tx, "ram:ApplicableHeaderTradeDelivery")
    if inv["delivery_date"]:
        _date(_sub(dlv, "ram:ActualDeliverySupplyChainEvent"), "ram:OccurrenceDateTime", inv["delivery_date"])

    st = _sub(tx, "ram:ApplicableHeaderTradeSettlement")
    if inv["payment_reference"]:
        _sub(st, "ram:PaymentReference", inv["payment_reference"])
    cur = inv["currency"]
    _sub(st, "ram:InvoiceCurrencyCode", cur)
    if inv["iban"]:
        pm = _sub(st, "ram:SpecifiedTradeSettlementPaymentMeans")
        _sub(pm, "ram:TypeCode", "58")  # SEPA-Ueberweisung
        _sub(_sub(pm, "ram:PayeePartyCreditorFinancialAccount"), "ram:IBANID", inv["iban"])
        if inv["bic"]:
            _sub(_sub(pm, "ram:PayeeSpecifiedCreditorFinancialInstitution"), "ram:BICID", inv["bic"])
    for r in inv["vat"]:
        tt = _sub(st, "ram:ApplicableTradeTax")
        if r["tax"] is not None:
            _sub(tt, "ram:CalculatedAmount", money(r["tax"]))
        _sub(tt, "ram:TypeCode", "VAT")
        if r["rate"] == 0:
            _sub(tt, "ram:ExemptionReason", r["exemption_reason"] or "Steuerbefreit")
        if r["basis"] is not None:
            _sub(tt, "ram:BasisAmount", money(r["basis"]))
        _sub(tt, "ram:CategoryCode", "S" if r["rate"] > 0 else "E")
        _sub(tt, "ram:RateApplicablePercent", money(r["rate"]))
    sk = []
    for s in inv.get("skonto") or []:
        tage = s["days"] if s["days"] else (
            (s["until"] - inv["issue_date"]).days if s["until"] and inv["issue_date"] else None)
        if tage and tage > 0:
            sk.append(f"#SKONTO#TAGE={tage}#PROZENT={money(s['percent'])}#")
    if inv["due_date"] or sk:
        pt = _sub(st, "ram:SpecifiedTradePaymentTerms")
        if sk:
            _sub(pt, "ram:Description", "\n".join(sk) + "\n")
        if inv["due_date"]:
            _date(pt, "ram:DueDateDateTime", inv["due_date"])
    ms = _sub(st, "ram:SpecifiedTradeSettlementHeaderMonetarySummation")
    if inv["net"] is not None:
        _sub(ms, "ram:LineTotalAmount", money(inv["net"]))
        _sub(ms, "ram:TaxBasisTotalAmount", money(inv["net"]))
    if inv["tax"] is not None:
        _sub(ms, "ram:TaxTotalAmount", money(inv["tax"]), currencyID=cur)
    if inv["gross"] is not None:
        _sub(ms, "ram:GrandTotalAmount", money(inv["gross"]))
    if inv["prepaid"]:
        _sub(ms, "ram:TotalPrepaidAmount", money(inv["prepaid"]))
    if inv["due"] is not None:
        _sub(ms, "ram:DuePayableAmount", money(inv["due"]))

    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="UTF-8", xml_declaration=True) + b"\n"


