# SPDX-License-Identifier: AGPL-3.0-or-later
"""Lokale Sprachmodelle ueber Ollama (127.0.0.1): Einordnung, Glaettung, Rechnungsdaten."""
from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request

from . import invoice

log = logging.getLogger("nopaper")


class OllamaNichtErreichbar(Exception):
    """Voruebergehend – spaeter erneut versuchen."""


class Ollama:
    def __init__(self, url: str, num_ctx: int = 8192, timeout: int = 1800):
        self.url = url.rstrip("/")
        self.num_ctx = num_ctx
        self.timeout = timeout
        self._think_ok: dict[str, bool] = {}

    def _post(self, path: str, payload: dict) -> dict:
        req = urllib.request.Request(self.url + path, data=json.dumps(payload).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError:
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            raise OllamaNichtErreichbar(type(e).__name__) from e

    def chat_json(self, model: str, system: str, user: str, schema: dict,
                  images: list[str] | None = None, num_ctx: int | None = None) -> dict:
        msg = {"role": "user", "content": user}
        if images:
            msg["images"] = images
        payload = {
            "model": model, "stream": False, "format": schema,
            "options": {"temperature": 0, "num_ctx": num_ctx or self.num_ctx},
            "messages": [{"role": "system", "content": system}, msg],
        }
        if self._think_ok.get(model, True):
            payload["think"] = False  # Denkmodus aus: kostet auf CPU nur Zeit
        try:
            resp = self._post("/api/chat", payload)
        except urllib.error.HTTPError as e:
            if e.code == 400 and "think" in payload:  # Modell kennt den Schalter nicht
                self._think_ok[model] = False
                payload.pop("think")
                resp = self._post("/api/chat", payload)
            elif e.code >= 500:
                raise OllamaNichtErreichbar(f"HTTP {e.code}") from e
            else:
                raise
        content = resp.get("message", {}).get("content", "")
        return json.loads(content)

    def modelle(self) -> list[str]:
        req = urllib.request.Request(self.url + "/api/tags")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return [m["name"] for m in json.loads(r.read()).get("models", [])]
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            raise OllamaNichtErreichbar(type(e).__name__) from e


# --------------------------------------------------------------------------- Einordnung

EINORDNUNG_SCHEMA = {
    "type": "object",
    "properties": {
        "typ": {"type": "string", "enum": ["rechnung", "gutschrift", "keine_rechnung"]},
        "sicherheit": {"type": "number", "description": "0 bis 1"},
    },
    "required": ["typ", "sicherheit"],
}

EINORDNUNG_PROMPT = """Du ordnest gescannte Dokumente ein. Antworte nur mit dem JSON-Schema.
"rechnung": eine Rechnung, die an uns gerichtet ist und eine Zahlung verlangt oder bestaetigt
(auch Arzt-, Labor-, Handwerker-, Liquidations- oder Honorarrechnung).
"gutschrift": Gutschrift oder Rechnungskorrektur zu unseren Gunsten.
"keine_rechnung": alles andere (Brief, Bescheid, Vertrag, Angebot, Lieferschein, Mahnung ohne eigene
Rechnungsdaten, Befund, Formular).
sicherheit: wie sicher du bist, 0 bis 1."""


def einordnen(o: Ollama, model: str, text: str) -> tuple[str, float]:
    r = o.chat_json(model, EINORDNUNG_PROMPT, "Dokumenttext:\n<<<\n" + text[:6000] + "\n>>>",
                    EINORDNUNG_SCHEMA)
    typ = r.get("typ") if r.get("typ") in ("rechnung", "gutschrift", "keine_rechnung") else "keine_rechnung"
    try:
        s = max(0.0, min(1.0, float(r.get("sicherheit", 0))))
    except (TypeError, ValueError):
        s = 0.0
    return typ, s


# --------------------------------------------------------------------------- Glaettung

GLAETTUNG_SCHEMA = {
    "type": "object",
    "properties": {"text": {"type": "string"}},
    "required": ["text"],
}

GLAETTUNG_PROMPT = """Du korrigierst den Text einer automatischen Texterkennung (OCR).
Erlaubt:
- offensichtliche Erkennungsfehler in Woertern korrigieren (z.B. "Rechnuug" -> "Rechnung")
- Silbentrennung am Zeilenende aufloesen, auseinandergerissene Woerter zusammenfuegen
- sinnlose Zeichenfolgen (Flecken, Raender, OCR-Muell) entfernen
Verboten:
- umformulieren, zusammenfassen, kuerzen, ergaenzen, uebersetzen, Reihenfolge aendern
- Ziffern, Zahlen, Betraege, Datumsangaben, Nummern, IBAN veraendern – diese bleiben exakt wie sie sind
Behalte Absaetze und Zeilenstruktur bei. Gib nur den korrigierten Text im Feld "text" zurueck."""

ABSCHNITT_ZEICHEN = 2500
GLAETTUNG_KONTEXT = 4096     # reicht fuer einen Abschnitt samt Antwort; spart Grafikspeicher (E74)


def _ziffern(s: str) -> str:
    return "".join(re.findall(r"\d", s))


def _abschnitte(text: str, n: int = ABSCHNITT_ZEICHEN) -> list[str]:
    teile, cur = [], ""
    for absatz in re.split(r"(\n\s*\n)", text):
        if len(cur) + len(absatz) > n and cur.strip():
            teile.append(cur)
            cur = ""
        while len(absatz) > n:  # sehr langer Absatz: an Zeilenumbruechen teilen
            cut = absatz.rfind("\n", 0, n)
            cut = cut if cut > n // 2 else n
            teile.append(absatz[:cut])
            absatz = absatz[cut:]
        cur += absatz
    if cur.strip():
        teile.append(cur)
    return teile


def pruefe_glaettung(roh: str, neu: str) -> str | None:
    """None = akzeptiert, sonst Grund. Harte Regel: Ziffernfolge identisch."""
    if _ziffern(roh) != _ziffern(neu):
        return "ZIFFERN"
    lr, ln = len(roh.strip()), len(neu.strip())
    if lr >= 40 and not (0.7 * lr <= ln <= 1.15 * lr):
        return "LAENGE"
    return None


def _zeilenweise_retten(roh: str, neu: str) -> tuple[str, int]:
    """Uebernimmt die Korrekturen des Modells nur blockweise dort, wo die Ziffern gleich geblieben sind.
    Gibt (Text, Anzahl verworfener Bloecke) zurueck."""
    import difflib
    a, b = roh.splitlines(), neu.splitlines()
    out, verworfen = [], 0
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        alt, ne = a[i1:i2], b[j1:j2]
        if op == "equal":
            out += alt
        elif op == "replace" and len(alt) == len(ne):  # Zeile fuer Zeile entscheiden
            for x, y in zip(alt, ne):
                if _ziffern(x) == _ziffern(y):
                    out.append(y)
                else:
                    out.append(x)
                    verworfen += 1
        elif _ziffern("\n".join(alt)) == _ziffern("\n".join(ne)):
            out += ne
        else:
            out += alt
            verworfen += 1
    return "\n".join(out), verworfen


def glaetten(o: Ollama, model: str, text: str, num_ctx: int | None = None) -> tuple[str, list[str]]:
    """Glaettet abschnittsweise. Aendert das Modell Ziffern, werden nur die betroffenen Zeilen roh gelassen."""
    out, verworfen, teilweise = [], 0, 0
    teile = _abschnitte(text)
    for teil in teile:
        if not teil.strip():
            out.append(teil)
            continue
        try:
            neu = o.chat_json(model, GLAETTUNG_PROMPT, teil, GLAETTUNG_SCHEMA, num_ctx=num_ctx).get("text", "")
        except (json.JSONDecodeError, ValueError):
            neu = ""
        grund = pruefe_glaettung(teil, neu) if neu.strip() else "LEER"
        if grund is None:
            ergebnis = neu.rstrip()
        elif grund == "ZIFFERN":
            ergebnis, n = _zeilenweise_retten(teil.rstrip(), neu.rstrip())
            teilweise += 1 if n else 0
            if pruefe_glaettung(teil, ergebnis) is not None:  # Laenge passt trotzdem nicht
                ergebnis, verworfen = teil.rstrip(), verworfen + 1
        else:
            ergebnis, verworfen = teil.rstrip(), verworfen + 1
        out.append(ergebnis + ("\n\n" if teil.endswith("\n") else ""))
    hinweise = []
    if verworfen and verworfen == len([t for t in teile if t.strip()]):
        hinweise.append("GLAETTUNG_VERWORFEN")
    elif verworfen or teilweise:
        hinweise.append(f"GLAETTUNG_TEILWEISE_{verworfen + teilweise}")
    return "".join(out).strip(), hinweise


# --------------------------------------------------------------------------- Rechnungsdaten

BASIS_ZEICHEN, BASIS_KONTEXT = 12000, 8192
AUSLASSUNG = "\n\n[... Mitte des Dokuments ausgelassen ...]\n\n"


def text_budget(num_ctx: int) -> int:
    """So viele Zeichen Dokumenttext passen neben Prompt und Antwort in den Kontext:
    12.000 je 8.192 Token (modelle.kontext), nie weniger als 12.000."""
    return max(BASIS_ZEICHEN, BASIS_ZEICHEN * int(num_ctx) // BASIS_KONTEXT)


def text_fuer_modell(text: str, max_chars: int, anteil_ende: float = 0.4) -> str:
    """Der ganze Text, wenn er ins Budget passt. Sonst Anfang (Kopfdaten) UND Ende (Summen) statt
    nur des Anfangs; die Mitte entfaellt. Geschnitten wird moeglichst an Zeilengrenzen."""
    if len(text) <= max_chars:
        return text
    platz = max(2, max_chars - len(AUSLASSUNG))
    ende = int(platz * anteil_ende)
    kopf = platz - ende
    a, b = text[:kopf], text[len(text) - ende:]
    i = a.rfind("\n")
    if i > kopf * 0.8:
        a = a[:i]
    j = b.find("\n")
    if 0 <= j < ende * 0.2:
        b = b[j + 1:]
    # nur Kennzahlen protokollieren, nie Inhalt
    log.info("dokumenttext gekuerzt: %d von %d zeichen an das modell (anfang und ende)", len(a) + len(b), len(text))
    return a + AUSLASSUNG + b


TOKEN_JE_SEITENBILD = 3500   # Seitenbild bei 170 dpi: ca. 2.500-3.500 Token
STUFEN = (8192, 16384, 32768, 65536, 131072)


def kontext_stufe(bedarf: int, hoechstens: int) -> int:
    """Kontext fuer EINEN Aufruf (E76): die kleinste Stufe, in die der Bedarf passt, nie mehr als `hoechstens`
    (modelle.kontext). Der Speicher, den Ollama fuer ein Modell belegt, waechst mit dem Kontext; mit festen
    Stufen statt eines genauen Werts bleibt ein Modell geladen, solange die Dokumente aehnlich gross sind."""
    for s in STUFEN:
        if bedarf <= s:
            return min(s, int(hoechstens))
    return int(hoechstens)


def kontext_text(zeichen: int, hoechstens: int) -> int:
    """Stufe fuer einen Textaufruf: Umkehrung von text_budget (12.000 Zeichen je 8.192 Token)."""
    return kontext_stufe(zeichen * BASIS_KONTEXT // BASIS_ZEICHEN, hoechstens)


def kontext_bilder(anzahl: int, hoechstens: int) -> int:
    return kontext_stufe(anzahl * TOKEN_JE_SEITENBILD + 2500, bild_kontext(hoechstens))


def bild_kontext(num_ctx: int) -> int:
    """Kontext fuer Aufrufe mit Seitenbildern: mindestens 16.384 Token."""
    return max(int(num_ctx), 16384)


def seiten_budget(num_ctx: int) -> int:
    """So viele Seitenbilder passen neben Vorlage und Antwort in den Kontext, nie weniger als 3."""
    return max(3, (bild_kontext(num_ctx) - 2000) // TOKEN_JE_SEITENBILD)


def analysieren(o: Ollama, model: str, text: str, max_chars: int | None = None) -> dict:
    """Ein Aufruf fuer Einordnung (kategorie, sicherheit) und Rechnungsdaten."""
    text = text_fuer_modell(text, max_chars or text_budget(o.num_ctx))
    return o.chat_json(model, invoice.EXTRACT_PROMPT,
                       "OCR-Text des Dokuments:\n<<<\n" + text + "\n>>>",
                       invoice.build_schema(False), num_ctx=kontext_text(len(text), o.num_ctx))


rechnungsdaten_text = analysieren


def rechnungsdaten_bild(o: Ollama, model: str, bild_b64: str) -> dict:
    return o.chat_json(model, invoice.EXTRACT_PROMPT,
                       "Lies die Kopfdaten aus diesem Bild der ersten Belegseite. Achte besonders auf "
                       "Summen, Steuersaetze, Datum und Steuernummer.",
                       invoice.build_schema(False), images=[bild_b64], num_ctx=kontext_bilder(1, o.num_ctx))


# --------------------------------------------------------------------------- NuExtract3 (numind)
# Ollama-Modell numind/nuextract3: eigene Nachrichtenrollen "template" und "instructions", Ausgabe JSON.

NUEXTRACT_TEMPLATE = {
    "kategorie": invoice.KATEGORIEN,
    "invoice_number": "verbatim-string",
    "issue_date": "date",
    "delivery_date": "date",
    "due_date": "date",
    "currency": "currency",
    "seller": {
        "name": "verbatim-string",
        "street": "verbatim-string",
        "postcode": "verbatim-string",
        "city": "verbatim-string",
        "country_code": "country",
        "vat_id": "verbatim-string",
        "tax_number": "verbatim-string",
    },
    "payment": {"iban": "verbatim-string", "bic": "verbatim-string", "reference": "verbatim-string"},
    "totals": {"net_total": "number", "tax_total": "number", "gross_total": "number", "prepaid": "number",
               "amount_due": "number"},
    "payment_terms": {"due_days": "integer",
                      "skonto": [{"percent": "number", "days": "integer", "until": "date-time", "amount": "number"}]},
    "vat_breakdown": [{"rate": "number", "basis": "number", "tax": "number", "exemption_reason": "verbatim-string"}],
}

NUEXTRACT_INSTRUCTIONS = """Das Dokument ist ein gescannter deutscher Beleg einer Arztpraxis (Eingangsbeleg).
kategorie: rechnung (Lieferanten-/Dienstleisterrechnung, auch Webshop/Tickets mit Rechnungsnummer), gutschrift,
kassenbon (Supermarkt, Tankstelle, Post), bewirtung (Restaurant), versicherung (Beitragsrechnung), behoerde
(Kostenrechnung/Gebuehrenbescheid von Behoerde oder Gericht), mahnung, bestellbestaetigung, buchungsbestaetigung
(Reise/Hotel ohne Rechnungsnummer), lieferschein (auch Packzettel, Wareneingangsformular), brief, sonstiges.
Bei bestellbestaetigung, buchungsbestaetigung, lieferschein, brief, sonstiges alle anderen Felder leer.
Bei mahnung nur: seller (der Absender), issue_date (Datum des Schreibens), totals.amount_due (der insgesamt noch zu
zahlende Betrag einschliesslich Mahngebuehr), payment.iban, payment.bic, payment.reference; alles andere leer.
seller ist der Rechnungssteller, nie der Empfaenger. Eingangsstempel ignorieren; massgeblich ist das gedruckte Datum.
invoice_number: Rechnungs-, Beleg- oder Bonnummer - nicht Kunden-, Auftrags-, Steuer-, Terminal- oder Kassennummer.
Auch eine von Hand eingetragene Nummer (z.B. hinter "RE-Nr. ......") uebernehmen, genau wie geschrieben.
tax_number = Steuernummer (St.-Nr.), vat_id = USt-IdNr.
Betraege mit dem gedruckten Vorzeichen: steht ein Minus davor oder dahinter (Auszahlung, z.B. Pfandrueckgabe
groesser als der Einkauf, "zu zahlen -0,77"), sind gross_total, net_total, tax_total und vat_breakdown negativ.
gross_total = Endbetrag/Rechnungsbetrag, nicht Zwischensumme. net_total und tax_total sind Summen ueber alle
Steuersaetze; jeder Steuersatz zusaetzlich als Zeile in vat_breakdown. Steuerfrei: rate 0 und Befreiungshinweis.
Versicherungsteuer ist keine Umsatzsteuer: tax_total 0, rate 0. Porto, Versand und Gebuehren sind keine Steuer:
weist der Beleg keine Umsatzsteuer aus, ist tax_total 0 und net_total = gross_total. prepaid = bereits bezahlt (bar, Karte, PayPal),
hoechstens der Endbetrag; bei "gegeben"/"Rueckgeld" ist amount_due 0.
payment.reference = was bei der Ueberweisung als Verwendungszweck anzugeben ist (Kassenzeichen, Aktenzeichen,
Vertrags-, Kunden- oder Rechnungsnummer laut Zahlungshinweis), genau wie gedruckt. Mandatsreferenz und
Glaeubiger-ID sind kein Verwendungszweck; wird per Lastschrift abgebucht, ist payment.reference null.
due_days = Zahlungsfrist in Tagen, nur wenn kein Faelligkeitsdatum genannt ist. skonto: je Skontostufe Prozentsatz,
Frist in Tagen oder Datum, Skontobetrag nur wenn ausgewiesen; ohne Skonto leer.
Keine Positionen, Diagnosen, Patienten- oder Personendaten uebernehmen."""

_LAENDER = {"deutschland": "DE", "germany": "DE", "österreich": "AT", "austria": "AT", "schweiz": "CH",
            "switzerland": "CH", "united states": "US", "usa": "US", "vereinigte staaten": "US",
            "niederlande": "NL", "netherlands": "NL", "frankreich": "FR", "france": "FR", "irland": "IE",
            "ireland": "IE", "luxemburg": "LU", "luxembourg": "LU", "italien": "IT", "italy": "IT"}


def _json_aus_antwort(text: str) -> dict:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        t = t.rsplit("```", 1)[0]
    a, b = t.find("{"), t.rfind("}")
    return json.loads(t[a:b + 1] if a >= 0 and b > a else t)


def analysieren_nuextract(o: Ollama, model: str, text: str | None = None,
                          bilder: list[str] | None = None, max_chars: int | None = None) -> dict:
    """Kategorie + Rechnungsdaten mit NuExtract3, wahlweise aus OCR-Text oder direkt aus Seitenbildern."""
    user = {"role": "user",
            "content": "" if bilder else text_fuer_modell(text or "", max_chars or text_budget(o.num_ctx))}
    if bilder:
        user["images"] = bilder
    payload = {
        "model": model, "stream": False, "think": False,
        "options": {"temperature": 0, "num_ctx": kontext_bilder(len(bilder), o.num_ctx) if bilder
                    else kontext_text(len(user["content"]), o.num_ctx)},
        "messages": [
            {"role": "template", "content": json.dumps(NUEXTRACT_TEMPLATE, ensure_ascii=False, indent=2)},
            {"role": "instructions", "content": NUEXTRACT_INSTRUCTIONS},
            user,
        ],
    }
    try:
        resp = o._post("/api/chat", payload)
    except urllib.error.HTTPError as e:
        if e.code >= 500:
            raise OllamaNichtErreichbar(f"HTTP {e.code}") from e
        raise
    raw = _json_aus_antwort(resp.get("message", {}).get("content", ""))
    # an das interne Format anpassen
    if isinstance(raw.get("kategorie"), list):
        raw["kategorie"] = raw["kategorie"][0] if raw["kategorie"] else None
    seller = raw.get("seller") or {}
    cc = seller.get("country_code")
    if isinstance(cc, str) and len(cc.strip()) > 2:
        seller["country_code"] = _LAENDER.get(cc.strip().lower())
    for k in ("issue_date", "delivery_date", "due_date"):
        if isinstance(raw.get(k), str):
            raw[k] = raw[k][:10]
    pt = raw.get("payment_terms")
    if isinstance(pt, dict):
        if not isinstance(pt.get("skonto"), list):
            pt["skonto"] = []
        for s in pt["skonto"]:
            if isinstance(s, dict) and isinstance(s.get("until"), str):
                s["until"] = s["until"][:10]
    return raw
