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
    test: Path = Path("/srv/nopaper/test")

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

    tags = _req(d, "tags")
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

    cfg = Config(
        paperless_url=url,
        token=token,
        ca_datei=ca or None,
        eingang_rechnungen=Path(_req(d, "eingang.rechnungen")),
        eingang_dokumente=Path(_req(d, "eingang.dokumente")),
        fehler=Path(d["eingang"].get("fehler", base / "fehler")),
        arbeit=base / "arbeit",
        zustand=base / "zustand",
        ausgang_rechnungsdaten=Path(_req(d, "ausgang.rechnungsdaten")),
        tags_rechnung=_str_list(_req(d, "tags.rechnung"), "tags.rechnung"),
        tags_dokument=_str_list(tags.get("dokument", []), "tags.dokument"),
        tag_pruefen=str(tags.get("pruefen") or "").strip(),
        tag_ausloeser=str(_req(d, "tags.ausloeser_nachbearbeitung")).strip(),
        tag_text=str(tags.get("ausloeser_text") or "").strip(),
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
    )
    if not testlauf and (not cfg.empfaenger or cfg.empfaenger.lower().startswith("muster")):
        raise ConfigError("empfaenger ist noch der Platzhalter – bitte den Namen der eigenen Organisation eintragen")
    if not cfg.tag_ausloeser:
        raise ConfigError("tags.ausloeser_nachbearbeitung darf nicht leer sein")
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
