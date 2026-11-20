# SPDX-License-Identifier: AGPL-3.0-or-later
"""Konfiguration laden und vollstaendig pruefen (/etc/nopaper/config.json)."""
from __future__ import annotations

import json
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_PATH = "/etc/nopaper/config.json"

# Rechnungsdatum -> paperless-Datentyp
FELD_TYPEN = {
    "sidecar": "longtext",          # nur Betriebsart "paperless": Sidecar (CII-XML) als Feld am Dokument
    "barcode": "string",            # Strichcodes der ersten Seiten (E67)
    "qrcode": "longtext",           # QR-Codes der ersten Seiten (E67)
    "termin": "date",               # Ladung: Tag der Verhandlung (E62)
    "terminzeit": "string",         # Ladung: Uhrzeit und Saal
    "aktenzeichen": "string",       # Ladung: Aktenzeichen des Gerichts
    "rechnungsnummer": "string",
    "rechnungsdatum": "date",
    "faelligkeit": "date",
    "leistungsdatum": "date",
    "rechnungssteller": "string",
    "brutto": "monetary",
    "netto": "monetary",
    "steuer": "monetary",
    "steuer_hoch": "monetary",
    "steuer_niedrig": "monetary",
    "skonto": "string",
    "zahlbetrag": "monetary",
    "gebuehren": "monetary",        # Zahlungsbericht: Gebuehren des Zahlungsdienstleisters (E82)
    "iban": "string",
    "verwendungszweck": "string",
    "pruefstatus": "string",
    "belegart": "string",
}


class ConfigError(Exception):
    pass


@dataclass
class Config:
    paperless_url: str
    token: str
    ca_datei: str | None
    eingang_rechnungen: Path
    eingang_dokumente: Path
    fehler: Path
    arbeit: Path
    zustand: Path
    ausgang_rechnungsdaten: Path
    tags_rechnung: list[str]
    tags_dokument: list[str]
    tag_pruefen: str
    tag_ausloeser: str
    felder: dict[str, str]
    empfaenger: str
    modell_text: str
    modell_bild: str
    ollama_url: str
    num_ctx: int = 8192
    ollama_timeout: int = 1800
    abfrage_intervall_s: int = 60
    eingang_ruhezeit_s: int = 15
    max_versuche: int = 5
    sidecar_bei_pruefen: bool = True
    titel_rechnung: str = "{datum} {lieferant} {betrag}"
    besitzer: str | None = None
    ansehen_gruppen: list[str] = field(default_factory=list)
    bearbeiten_gruppen: list[str] = field(default_factory=list)
    ansehen_benutzer: list[str] = field(default_factory=list)
    bearbeiten_benutzer: list[str] = field(default_factory=list)
    ocr_sprache: str = "deu"
    modell_glaettung: str = ""
    extraktor: str = "standard"            # standard | nuextract3
    modell_extraktion: str = ""            # z.B. numind/nuextract3:Q4_K_M
    nuextract_eingabe: str = "bild"        # bild | text
    nuextract_seiten: int = 3
    kategorie_tags: dict[str, list[str]] = field(default_factory=dict)
    tag_text: str = ""
    tag_zu_buchen: str = ""
    tags_aktenzeichen: bool = False        # E70: vorhandene Aktenzeichen-Tags zuordnen
    kumero_url: str = ""
    kumero_mandant: str = ""
    kumero_token_datei: str = ""
    kumero_ca_datei: str = ""
    test: Path = Path("/srv/nopaper/test")
    betriebsart: str = "scanner"           # scanner (bisher) | paperless (E61: erst paperless, zustandslos)
    dokumentdatum_setzen: bool = True      # nur Betriebsart paperless: "Erstellt am" = Rechnungsdatum (E66)
    positionen: bool = False               # E72: Rechnungspositionen lesen und in den Sidecar schreiben
    vorlauf: int = 3                       # E79: so viele Dokumente gleichzeitig holen und erkennen (1-8)

    @property
    def zustandslos(self) -> bool:
        return self.betriebsart == "paperless"

    @property
    def alle_tags(self) -> list[str]:
        seen, out = set(), []
        extra = [t for ts in self.kategorie_tags.values() for t in ts]
        for t in [*self.tags_rechnung, *self.tags_dokument, self.tag_pruefen, self.tag_ausloeser, self.tag_text, *extra]:
            if t and t.lower() not in seen:
                seen.add(t.lower())
                out.append(t)
        return out


def _req(d: dict, path: str):
    cur = d
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            raise ConfigError(f"Pflichtangabe fehlt: {path}")
        cur = cur[part]
    return cur


def _str_list(v, name: str) -> list[str]:
    if not isinstance(v, list) or not all(isinstance(x, str) and x.strip() for x in v):
        raise ConfigError(f"{name} muss eine Liste von Texten sein")
    return [x.strip() for x in v]


def load(path: str | Path = DEFAULT_PATH, read_token: bool = True, testlauf: bool = False) -> Config:
    """testlauf=True: paperless-Angaben und Platzhalter werden nicht geprueft (kein paperless noetig)."""
    p = Path(path)
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"Konfiguration {p} nicht gefunden")
    except json.JSONDecodeError as e:
        raise ConfigError(f"Konfiguration {p} ist kein gueltiges JSON: Zeile {e.lineno}, Spalte {e.colno}")

    url = str(_req(d, "paperless.url")).rstrip("/")
    u = urllib.parse.urlsplit(url)
    if not testlauf:
        if u.scheme not in ("http", "https") or not u.hostname:
            raise ConfigError("paperless.url ist keine gueltige URL")
        if u.scheme != "https" and u.hostname not in ("localhost", "127.0.0.1", "::1"):
            raise ConfigError("paperless.url muss https:// sein (Art.-9-Daten im Netz)")
        if "BEISPIEL" in url.upper() or "EXAMPLE" in url.upper():
            raise ConfigError("paperless.url ist noch der Platzhalter – bitte anpassen")

    token = ""
    if read_token:
        tpath = Path(_req(d, "paperless.token_datei"))
        try:
            token = tpath.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            raise ConfigError(f"Token-Datei {tpath} nicht gefunden")
        except PermissionError:
            raise ConfigError(f"Token-Datei {tpath} nicht lesbar (Rechte?)")
        if not token:
            raise ConfigError(f"Token-Datei {tpath} ist leer")

    ca = d["paperless"].get("ca_datei")
    if ca and not testlauf and not Path(ca).is_file():
        raise ConfigError(f"paperless.ca_datei {ca} nicht gefunden")

    if not (isinstance(d.get("vorlauf", 3), int) and not isinstance(d.get("vorlauf", 3), bool)
            and 1 <= d.get("vorlauf", 3) <= 8):
        raise ConfigError("vorlauf muss eine ganze Zahl von 1 bis 8 sein")
    if not isinstance(d.get("positionen", False), bool):
        raise ConfigError("positionen muss true oder false sein")
    tags = _req(d, "tags")
    if not isinstance(tags.get("aktenzeichen", False), bool):
        raise ConfigError("tags.aktenzeichen muss true oder false sein")
    felder = _req(d, "felder")
    if not isinstance(felder, dict):
        raise ConfigError("felder muss ein Objekt sein")
    unbekannt = set(felder) - set(FELD_TYPEN)
    if unbekannt:
        raise ConfigError(f"Unbekannte Feldschluessel: {', '.join(sorted(unbekannt))}. "
                          f"Erlaubt: {', '.join(FELD_TYPEN)}")
    for k, v in felder.items():
        if not isinstance(v, str) or not v.strip():
            raise ConfigError(f"felder.{k} muss ein Feldname sein")

    rechte = d.get("dokumentrechte", {}) or {}
    from .invoice import KATEGORIEN
    kt = d.get("kategorie_tags", {}) or {}
    if not isinstance(kt, dict) or set(kt) - set(KATEGORIEN):
        raise ConfigError(f"kategorie_tags: erlaubte Schluessel sind {', '.join(KATEGORIEN)}")
    kategorie_tags = {k: _str_list(v, f"kategorie_tags.{k}") for k, v in kt.items()}
    mod = _req(d, "modelle")
    base = Path(d.get("basisverzeichnis", "/srv/nopaper"))
    betriebsart = str(d.get("betriebsart", "scanner")).strip().lower()
    if betriebsart not in ("scanner", "paperless"):
        raise ConfigError("betriebsart muss 'scanner' oder 'paperless' sein")
    ein, aus = d.get("eingang") or {}, d.get("ausgang") or {}
    if betriebsart == "scanner":       # Ordner sind nur fuer den Scanner-Weg Pflicht
        _req(d, "eingang.rechnungen"), _req(d, "eingang.dokumente"), _req(d, "ausgang.rechnungsdaten")

    cfg = Config(
        paperless_url=url,
        token=token,
        ca_datei=ca or None,
        eingang_rechnungen=Path(ein.get("rechnungen", base / "eingang/rechnungen")),
        eingang_dokumente=Path(ein.get("dokumente", base / "eingang/dokumente")),
        fehler=Path(ein.get("fehler", base / "fehler")),
        arbeit=base / "arbeit",
        zustand=base / "zustand",
        ausgang_rechnungsdaten=Path(aus.get("rechnungsdaten", base / "ausgang/rechnungsdaten")),
        tags_rechnung=_str_list(_req(d, "tags.rechnung"), "tags.rechnung"),
        tags_dokument=_str_list(tags.get("dokument", []), "tags.dokument"),
        tag_pruefen=str(tags.get("pruefen") or "").strip(),
        tag_ausloeser=str(_req(d, "tags.ausloeser_nachbearbeitung")).strip(),
        tag_text=str(tags.get("ausloeser_text") or "").strip(),
        tag_zu_buchen=str(tags.get("zu_buchen") or "").strip(),
        tags_aktenzeichen=tags.get("aktenzeichen", False),
        felder={k: v.strip() for k, v in felder.items()},
        empfaenger=str(_req(d, "empfaenger")).strip(),
        modell_text=str(_req(d, "modelle.text")),
        modell_bild=str(mod.get("bild", "")),
        ollama_url=str(mod.get("ollama_url", "http://127.0.0.1:11434")).rstrip("/"),
        num_ctx=int(mod.get("kontext", 8192)),
        ollama_timeout=int(mod.get("timeout_s", 1800)),
        abfrage_intervall_s=int(d.get("abfrage_intervall_s", 60)),
        eingang_ruhezeit_s=int(d.get("eingang_ruhezeit_s", 15)),
        max_versuche=int(d.get("max_versuche", 5)),
        sidecar_bei_pruefen=bool(d.get("sidecar_bei_pruefen", True)),
        titel_rechnung=str(d.get("titel_rechnung", "{datum} {lieferant} {betrag}") or ""),
        besitzer=rechte.get("besitzer") or None,
        ansehen_gruppen=_str_list(rechte.get("ansehen_gruppen", []), "dokumentrechte.ansehen_gruppen"),
        bearbeiten_gruppen=_str_list(rechte.get("bearbeiten_gruppen", []), "dokumentrechte.bearbeiten_gruppen"),
        ansehen_benutzer=_str_list(rechte.get("ansehen_benutzer", []), "dokumentrechte.ansehen_benutzer"),
        bearbeiten_benutzer=_str_list(rechte.get("bearbeiten_benutzer", []), "dokumentrechte.bearbeiten_benutzer"),
        ocr_sprache=str(d.get("ocr_sprache", "deu")),
        test=base / "test",
        modell_glaettung=str(mod.get("glaettung") or ""),
        extraktor=str(mod.get("extraktor", "standard")),
        modell_extraktion=str(mod.get("extraktion") or ""),
        nuextract_eingabe=str(mod.get("nuextract_eingabe", "bild")),
        nuextract_seiten=int(mod.get("nuextract_seiten", 3)),
        kategorie_tags=kategorie_tags,
        betriebsart=betriebsart,
        dokumentdatum_setzen=bool(d.get("dokumentdatum_setzen", True)),
        positionen=d.get("positionen", False),
        vorlauf=d.get("vorlauf", 3),
    )
    if not testlauf and (not cfg.empfaenger or cfg.empfaenger.lower().startswith("muster")):
        raise ConfigError("empfaenger ist noch der Platzhalter – bitte den Namen der eigenen Organisation eintragen")
    if not cfg.tag_ausloeser:
        raise ConfigError("tags.ausloeser_nachbearbeitung darf nicht leer sein")
    # Tag "noch zu buchen" (optional): gehoert zu jedem als Rechnung/Beleg verarbeiteten Dokument
    if cfg.tag_zu_buchen and cfg.tag_zu_buchen.lower() not in {t.lower() for t in cfg.tags_rechnung}:
        cfg.tags_rechnung.append(cfg.tag_zu_buchen)
    if cfg.zustandslos:
        # E61: alles liegt in paperless – der Sidecar als Feld am Dokument, kein Ausgangsordner, keine Einlieferung
        if "sidecar" not in cfg.felder:
            raise ConfigError("betriebsart 'paperless' braucht felder.sidecar (Feld fuer den Sidecar, z.B. "
                              "\"Erkannt: Sidecar\", Datentyp langer Text)")
        if d.get("kumero"):
            raise ConfigError("betriebsart 'paperless': nopaper liefert nicht an kumero ein – kumero holt den "
                              "Sidecar aus dem Feld in paperless. Abschnitt kumero entfernen.")
    elif "sidecar" in cfg.felder:
        raise ConfigError("felder.sidecar gibt es nur in der betriebsart 'paperless'")
    # Einlieferung an die Buchhaltung kumero (optional, E56)
    k = d.get("kumero")
    if k is not None:
        if not isinstance(k, dict):
            raise ConfigError("kumero muss ein Objekt sein (KUMERO_URL)")
        kurl = str(k.get("url") or "").rstrip("/")
        ku = urllib.parse.urlsplit(kurl)
        if ku.scheme not in ("http", "https") or not ku.hostname:
            raise ConfigError("kumero.url fehlt oder ist ungueltig (KUMERO_URL)")
        if ku.scheme != "https" and ku.hostname not in ("localhost", "127.0.0.1", "::1"):
            raise ConfigError("kumero.url muss https:// sein (KUMERO_URL)")
        mandant = str(k.get("mandant") or "").strip()
        if not mandant or not all(c.isalnum() or c in "-_" for c in mandant):
            raise ConfigError("kumero.mandant fehlt oder enthaelt unzulaessige Zeichen (KUMERO_ZIEL)")
        if not str(k.get("token_datei") or "").strip():
            raise ConfigError("kumero.token_datei fehlt (KUMERO_TOKEN)")
        cfg.kumero_url, cfg.kumero_mandant = kurl, mandant
        cfg.kumero_token_datei = str(k["token_datei"]).strip()
        # eigenes Zertifikat der Gegenstelle (selbst signiert oder interne CA): wird als vertrauenswuerdig
        # hinterlegt – die Pruefung selbst wird nie abgeschaltet
        kca = str(k.get("ca_datei") or "").strip()
        if kca and not Path(kca).is_file():
            raise ConfigError(f"kumero.ca_datei {kca} nicht gefunden (KUMERO_URL)")
        cfg.kumero_ca_datei = kca
    if cfg.tag_text and cfg.tag_text.lower() in {t.lower() for t in [cfg.tag_ausloeser, cfg.tag_pruefen,
                                                                       *cfg.tags_rechnung, *cfg.tags_dokument]}:
        raise ConfigError("tags.ausloeser_text muss ein eigener Tag sein")
    if cfg.tag_ausloeser.lower() in {t.lower() for t in cfg.tags_rechnung}:
        raise ConfigError("Der Ausloeser-Tag darf nicht Teil des Rechnungs-Tag-Sets sein")
    if cfg.extraktor not in ("standard", "nuextract3"):
        raise ConfigError("modelle.extraktor muss 'standard' oder 'nuextract3' sein")
    if cfg.extraktor == "nuextract3" and not cfg.modell_extraktion:
        raise ConfigError("modelle.extraktion fehlt (z.B. numind/nuextract3:Q4_K_M)")
    if cfg.nuextract_eingabe not in ("bild", "text"):
        raise ConfigError("modelle.nuextract_eingabe muss 'bild' oder 'text' sein")
    ollama = urllib.parse.urlsplit(cfg.ollama_url).hostname
    if ollama not in ("127.0.0.1", "localhost", "::1"):
        raise ConfigError("modelle.ollama_url muss auf localhost zeigen")
    return cfg
