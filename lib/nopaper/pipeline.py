# SPDX-License-Identifier: AGPL-3.0-or-later
"""Ablaufsteuerung: Eingang -> OCR -> Einordnung -> Glaettung -> Rechnungsdaten -> paperless -> Sidecar.

Protokolliert ausschliesslich Metadaten (Hash-Praefix, Dokument-ID, Status, Codes).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import os
import shutil
import tempfile
import time
from pathlib import Path

from . import VERSION, invoice, llm, ocr
from .config import FELD_TYPEN, Config
from .kumero import Kumero
from .paperless import Paperless, PaperlessFehler, Voruebergehend
from .state import State

log = logging.getLogger("nopaper")

HINWEIS_CODES = {"UST_AUFSCHLUESSELUNG_BERECHNET", "NETTO_BERECHNET", "BILDMODELL_VERWENDET",
                 "GLAETTUNG_VERWORFEN", "SUMMEN_AUS_AUFSCHLUESSELUNG", "EINORDNUNG_UEBERSTIMMT",
                 "BRUTTO_AUS_AUFSCHLUESSELUNG", "UST_NULLSATZ_KORRIGIERT", "UST_SATZ_BERECHNET",
                 "BEFREIUNGSGRUND_ERSETZT", "STEUERNUMMER_VERWORFEN", "RECHNUNGSNUMMER_VERWORFEN", "RUECKGELD_ERKANNT", "VERWENDUNGSZWECK_VERWORFEN", "VERWENDUNGSZWECK_AUS_TEXT", "RECHNUNGSNUMMER_ABWEICHEND", "ZAHLUNG_AUS_TEXT", "FAELLIGKEIT_BERECHNET", "SKONTO_VERWORFEN", "ZWEITE_LESUNG", "GLAETTUNG_UEBERSPRUNGEN",
                 "VERSICHERUNGSTEUER_KORRIGIERT"}
SIDECAR_HINWEIS = ("Maschinell aus Papierbeleg extrahiert. Keine E-Rechnung im Sinne von § 14 UStG; "
                   "massgeblich ist der Originalbeleg mit dem angegebenen SHA-256.")


def _ist_hinweis(code: str) -> bool:
    return code in HINWEIS_CODES or code.startswith("GLAETTUNG_TEILWEISE_")


def analyse(c: Config, ki: llm.Ollama, roh: str, original: Path | None = None,
            workdir: Path | None = None) -> dict:
    """Ein Modellaufruf: Kategorie + Rechnungsdaten. Muellzeilen (Handschrift, Flecken) werden vorher entfernt.
    Mit extraktor=nuextract3 liest NuExtract3 wahlweise den Text oder direkt die Seitenbilder."""
    text_ki, _ = ocr.fuer_ki(roh)
    if c.extraktor == "nuextract3":
        if c.nuextract_eingabe == "bild" and original is not None and workdir is not None \
                and original.suffix.lower() == ".pdf" and not ocr.ist_digital(original):
            # nie mehr Seitenbilder, als in den Kontext passen (sonst schneidet Ollama stumm ab)
            seiten = min(c.nuextract_seiten, llm.seiten_budget(c.num_ctx))
            if seiten < c.nuextract_seiten:
                log.info("nuextract_seiten=%d passt nicht in modelle.kontext=%d, verwende %d seiten",
                         c.nuextract_seiten, c.num_ctx, seiten)
            bilder = ocr.seiten_als_bilder(original, workdir, seiten)
            return llm.analysieren_nuextract(ki, c.modell_extraktion, bilder=bilder)
        return llm.analysieren_nuextract(ki, c.modell_extraktion, text=text_ki or roh)
    return llm.analysieren(ki, c.modell_text, text_ki or roh)


def extraktions_modell(c: Config) -> str:
    return c.modell_extraktion if c.extraktor == "nuextract3" else c.modell_text


def rechnung_auslesen(c: Config, ki: llm.Ollama, seite_als_bild, original: Path, text: str, workdir: Path,
                      raw: dict | None = None, kategorie: str | None = None, zweite_lesung=None):
    """Rechnungsdaten aus der Analyse. Bei roten Pruefregeln:
    1. zweite, unabhaengige Texterkennung als zusaetzlicher Beleg fuer die Verankerung,
    2. Rueckfall auf das Bildmodell (Seite 1).
    `kategorie` erzwingt eine Kategorie (Nachbearbeitung: der Mensch hat 'Rechnung' entschieden)."""
    zweite_lesung = zweite_lesung or ocr.zweite_lesung
    if raw is None:
        raw = analyse(c, ki, text, original, workdir)

    def auswerten(r: dict, beleg: str):
        inv = invoice.normalize(r, c.empfaenger)
        notes: list[str] = []
        if kategorie and not invoice.ist_rechnungsartig(inv["kategorie"]):
            inv["kategorie"], inv["document_type"] = kategorie, "rechnung"
            notes.append("EINORDNUNG_UEBERSTIMMT")
        notes += invoice.complete_vat(inv, beleg)
        return inv, invoice.validate(inv, beleg), notes

    def rot(issues):
        return [i for i in issues if not _ist_hinweis(i)]

    beleg = text
    inv, issues, notes = auswerten(raw, beleg)
    methode = "bild" if (c.extraktor == "nuextract3" and c.nuextract_eingabe == "bild"
                         and original.suffix.lower() == ".pdf" and not ocr.ist_digital(original)) else "text"
    modell = extraktions_modell(c)
    if rot(issues) and original.suffix.lower() == ".pdf" and not ocr.ist_digital(original):
        zweit = zweite_lesung(original, workdir, c.ocr_sprache)
        if zweit.strip():
            beleg = text + "\f" + zweit
            inv, issues, notes = auswerten(raw, beleg)
            notes.append("ZWEITE_LESUNG")
    if rot(issues) and c.modell_bild:
        try:
            bild = seite_als_bild(original, workdir)
            raw2 = llm.rechnungsdaten_bild(ki, c.modell_bild, bild)
            raw2["kategorie"] = inv["kategorie"]
            inv2, issues2, notes2 = auswerten(raw2, beleg)
            if len(rot(issues2)) < len(rot(issues)):
                inv, notes, issues = inv2, notes2 + ["BILDMODELL_VERWENDET"], issues2
                methode, modell = "bild", c.modell_bild
        except ocr.OcrError as e:
            log.warning("bild-rueckfall nicht moeglich (%s)", e)
    if "ZWEITE_LESUNG" not in notes and beleg is not text:
        notes.append("ZWEITE_LESUNG")
    return inv, issues, notes, methode, modell


def glaetten_wenn_noetig(c: Config, ki: llm.Ollama, original: Path, roh: str) -> tuple[str, list[str]]:
    """Digitale PDFs haben eine fehlerfreie Textebene - dort waere die Glaettung nur Rechenzeit.
    modelle.glaettung = "aus" schaltet die Glaettung ganz ab."""
    if c.modell_glaettung == "aus" or (original.suffix.lower() == ".pdf" and ocr.ist_digital(original)):
        return roh, ["GLAETTUNG_UEBERSPRUNGEN"]
    return llm.glaetten(ki, c.modell_glaettung or c.modell_text, roh)


def pruefsumme_passt(paperless_wert, sha256: str, md5: str) -> bool:
    """paperless bis 2.x fuehrt MD5 (32 Hex-Zeichen), 3.x SHA-256 (64). Verglichen wird mit dem passenden
    eigenen Wert. Ohne Angabe von paperless gilt die Pruefung als bestanden (wie bisher)."""
    w = str(paperless_wert or "").strip().lower()
    if not w:
        return True
    return w == (sha256 if len(w) == 64 else md5).lower()


def datei_hashes(p: Path) -> tuple[str, str]:
    sha, md5 = hashlib.sha256(), hashlib.md5()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            sha.update(chunk)
            md5.update(chunk)
    return sha.hexdigest(), md5.hexdigest()


class Merker:
    """Betriebsart "paperless" (E61): nopaper haelt nichts auf der Platte. Was offen ist, sagt der
    Ausloeser-Tag in paperless. Hier stehen nur Versuchszaehler und Wartezeiten – im Arbeitsspeicher,
    nach einem Neustart beginnt die Zaehlung von vorn."""
    def __init__(self):
        self._nach: dict[int, dict] = {}

    def nach(self, doc_id: int):
        return self._nach.get(doc_id)

    def nach_setzen(self, doc_id: int, versuche: int, nicht_vor: float, fehler: str | None) -> None:
        self._nach[doc_id] = {"versuche": versuche, "nicht_vor": nicht_vor, "fehler": fehler}

    def nach_loeschen(self, doc_id: int) -> None:
        self._nach.pop(doc_id, None)


class Nopaper:
    def __init__(self, cfg: Config, pl: Paperless | None = None, ki: llm.Ollama | None = None,
                 state: State | None = None, ocr_text=None, seite_als_bild=None, zweite_lesung=None):
        self.cfg = cfg
        self.pl = pl or Paperless(cfg.paperless_url, cfg.token, cfg.ca_datei)
        self.ki = ki or llm.Ollama(cfg.ollama_url, cfg.num_ctx, cfg.ollama_timeout)
        if cfg.zustandslos:
            self.state = state or Merker()
            self.kumero = None
        else:
            self.state = state or State(cfg.zustand / "nopaper.db")
            self.kumero = Kumero(cfg, self.state) if cfg.kumero_url else None
        self.ocr_text = ocr_text or ocr.ocr_text
        self.seite_als_bild = seite_als_bild or ocr.seite_als_bild
        self.zweite_lesung = zweite_lesung or ocr.zweite_lesung
        self._ids_ok = False
        self._letzte_abfrage = 0.0
        # zustandslos: nur das Arbeitsverzeichnis fuer kurzlebige Dateien eines Durchlaufs
        for d in ((cfg.arbeit,) if cfg.zustandslos else
                  (cfg.arbeit, cfg.fehler, cfg.ausgang_rechnungsdaten, cfg.zustand)):
            d.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ IDs aus paperless
    def ids_laden(self) -> None:
        if self._ids_ok:
            return
        pl, c = self.pl, self.cfg
        pl.cache_leeren()
        self.tag_rechnung = pl.tag_ids(c.tags_rechnung)
        self.tag_dokument = pl.tag_ids(c.tags_dokument)
        self.tag_pruefen = pl.tag_ids([c.tag_pruefen])[0] if c.tag_pruefen else None
        self.tag_ausloeser = pl.tag_ids([c.tag_ausloeser])[0]
        self.tag_text = pl.tag_ids([c.tag_text])[0] if c.tag_text else None
        self.felder = pl.feld_ids(c.felder)
        self.tag_kategorie = {k: pl.tag_ids(v) for k, v in c.kategorie_tags.items()}
        self.rechte = None
        if c.besitzer or c.ansehen_gruppen or c.bearbeiten_gruppen or c.ansehen_benutzer or c.bearbeiten_benutzer:
            def users(names):
                out = []
                for n in names:
                    i = pl.id_von("users", n, key="username")
                    if not i:
                        raise PaperlessFehler("BENUTZER_FEHLT", n)
                    out.append(i)
                return out

            def groups(names):
                out = []
                for n in names:
                    i = pl.id_von("groups", n)
                    if not i:
                        raise PaperlessFehler("GRUPPE_FEHLT", n)
                    out.append(i)
                return out
            self.rechte = {
                "owner": users([c.besitzer])[0] if c.besitzer else None,
                "set_permissions": {
                    "view": {"users": users(c.ansehen_benutzer), "groups": groups(c.ansehen_gruppen)},
                    "change": {"users": users(c.bearbeiten_benutzer), "groups": groups(c.bearbeiten_gruppen)},
                },
            }
        self._ids_ok = True

    # ------------------------------------------------------------------ Eingang
    def eingang_scannen(self) -> int:
        neu = 0
        jetzt = time.time()
        for ordner, pfad in (("rechnungen", self.cfg.eingang_rechnungen),
                             ("dokumente", self.cfg.eingang_dokumente)):
            if not pfad.is_dir():
                continue
            for f in sorted(pfad.iterdir()):
                if not f.is_file() or f.name.startswith("."):
                    continue
                st = f.stat()
                if st.st_size == 0 or jetzt - st.st_mtime < self.cfg.eingang_ruhezeit_s:
                    continue  # Datei wird evtl. noch geschrieben
                if f.suffix.lower() != ".pdf":
                    self._in_fehler(f, f.name, ["FORMAT_NICHT_UNTERSTUETZT"])
                    continue
                sha, _ = datei_hashes(f)
                if self.state.eingang(sha):
                    self._in_fehler(f, f.name, ["DUPLIKAT_EINGANG"])
                    log.warning("eingang %s bereits bekannt -> fehler/", sha[:12])
                    continue
                ziel = self.cfg.arbeit / f"{sha}.pdf"
                shutil.move(str(f), str(ziel))
                self.state.eingang_neu(sha, ordner, f.name)
                log.info("eingang %s ordner=%s", sha[:12], ordner)
                neu += 1
        return neu

    def _in_fehler(self, quelle: Path, name: str, codes: list[str], doc_id: int | None = None) -> None:
        ziel = self.cfg.fehler / name
        if ziel.exists():
            ziel = self.cfg.fehler / f"{Path(name).stem}-{int(time.time())}{Path(name).suffix}"
        if quelle.exists():
            shutil.move(str(quelle), str(ziel))
        info = {"codes": codes, "zeit": dt.datetime.now().astimezone().isoformat(timespec="seconds")}
        if doc_id:
            info["paperless-document-id"] = doc_id
        invoice.atomic_write(ziel.with_name(ziel.name + ".fehler.json"),
                             json.dumps(info, ensure_ascii=False, indent=1).encode("utf-8"))

    # ------------------------------------------------------------------ Analyse
    def _rechnung_auslesen(self, original: Path, text: str, workdir: Path, raw=None, kategorie=None):
        return rechnung_auslesen(self.cfg, self.ki, self.seite_als_bild, original, text, workdir, raw, kategorie,
                                 self.zweite_lesung)

    def _feldwerte(self, inv: dict, status: str) -> dict[str, object]:
        cur = inv["currency"] or "EUR"
        st_hoch, st_niedrig = invoice.steuer_nach_klasse(inv)
        werte = {
            "steuer_hoch": st_hoch,
            "steuer_niedrig": st_niedrig,
            "skonto": invoice.skonto_text(inv),
            "rechnungsnummer": inv["invoice_number"],
            "rechnungsdatum": inv["issue_date"],
            "faelligkeit": inv["due_date"],
            "leistungsdatum": inv["delivery_date"],
            "rechnungssteller": inv["seller"]["name"],
            "brutto": inv["gross"],
            "netto": inv["net"],
            "steuer": inv["tax"],
            "zahlbetrag": inv["due"],
            "iban": inv["iban"],
            "verwendungszweck": inv["payment_reference"],
            "pruefstatus": "ok" if status == "ok" else "prüfen",
            "belegart": inv.get("kategorie"),
        }
        out = {}
        for key, (fid, typ) in self.felder.items():
            v = werte.get(key)
            if v is None:
                continue
            typ = typ or FELD_TYPEN[key]
            if isinstance(v, dt.date):
                v = v.isoformat()
            elif hasattr(v, "quantize"):  # Decimal
                v = f"{cur}{invoice.money(v)}" if typ == "monetary" else (
                    float(v) if typ == "float" else invoice.money(v))
            out[fid] = v
        return out

    @staticmethod
    def _felder_mischen(bestehend: list[dict], neu: dict[int, object]) -> list[dict]:
        rest = [{"field": f["field"], "value": f.get("value")} for f in bestehend or []
                if f.get("field") not in neu]
        return rest + [{"field": k, "value": v} for k, v in neu.items()]

    def _sidecar(self, doc_id: int, inv: dict, issues: list[str], notes: list[str], quelle: str,
                 sha_o: str, sha_a: str | None, methode: str, modell: str, md5_o: str | None = None) -> None:
        xml = self._sidecar_xml(doc_id, inv, issues, notes, quelle, sha_o, sha_a, methode, modell, md5_o)
        if xml is not None:
            invoice.atomic_write(self.cfg.ausgang_rechnungsdaten / f"{doc_id:07d}.xml", xml)

    def _sidecar_xml(self, doc_id: int, inv: dict, issues: list[str], notes: list[str], quelle: str,
                     sha_o: str, sha_a: str | None, methode: str, modell: str,
                     md5_o: str | None = None) -> bytes | None:
        status = "ok" if not issues else "pruefen"
        if status != "ok" and not self.cfg.sidecar_bei_pruefen:
            return None
        prov = {
            "paperless-document-id": doc_id,
            "paperless-url": f"{self.cfg.paperless_url}/documents/{doc_id}/details",
            "belegart": inv.get("kategorie"),
            "sha256-original": sha_o,
            "md5-original": md5_o,
            "sha256-archiv": sha_a,
            "quelle": quelle,
            "extraktion-modell": modell,
            "extraktion-methode": methode,
            "extraktion-software": f"nopaper {VERSION}",
            "extraktion-zeit": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "pruefstatus": status,
            "pruefhinweise": ",".join(issues + notes),
            "hinweis": SIDECAR_HINWEIS,
        }
        return invoice.build_cii(inv, prov)

    def _duplikat(self, inv: dict, doc_id: int) -> int | None:
        """Gleicher Absender und gleiche Rechnungsnummer schon vorhanden? Liefert die ID des anderen Dokuments.
        Scanner-Betrieb: eigener Index (nur Hashes). Zustandslos: Abfrage der Erkannt-Felder in paperless."""
        if not inv["invoice_number"]:
            return None
        if not self.cfg.zustandslos:
            absender = inv["seller"]["vat_id"] or inv["seller"]["tax_number"] or inv["seller"]["name"] or ""
            return self.state.duplikat(absender, inv["invoice_number"], doc_id)
        f_nr, f_st = self.felder.get("rechnungsnummer"), self.felder.get("rechnungssteller")
        name = inv["seller"]["name"]
        if not (f_nr and f_st and name):
            return None   # ohne beide Felder ist ein Duplikat in paperless nicht erkennbar
        try:
            andere = [i for i in self.pl.mit_feldwerten([(f_nr[0], inv["invoice_number"]), (f_st[0], name)])
                      if i != doc_id]
        except PaperlessFehler as e:
            # aeltere paperless-Fassung ohne Feldabfrage: Duplikatsuche auslassen, Beleg nicht aufhalten
            log.warning("duplikatsuche in paperless nicht moeglich (%s)", e.code)
            return None
        return min(andere) if andere else None

    def _archiv_sha(self, doc_id: int, meta: dict) -> str | None:
        if not meta.get("has_archive_version"):
            return None
        sha, md5 = self.pl.hashes(doc_id, original=False)
        if not pruefsumme_passt(meta.get("archive_checksum"), sha, md5):
            raise PaperlessFehler("INTEGRITAET_ARCHIV")
        return sha

    # ------------------------------------------------------------------ Weg 1: Eingang
    def eingang_verarbeiten(self, row) -> str:
        self.ids_laden()
        c, sha = self.cfg, row["sha256"]
        original = c.arbeit / f"{sha}.pdf"
        if not original.exists():
            raise FileNotFoundError("ARBEITSDATEI_FEHLT")
        t0 = time.monotonic()
        sha_o, md5_o = datei_hashes(original)
        if sha_o != sha:
            raise PaperlessFehler("INTEGRITAET_EINGANG")

        with tempfile.TemporaryDirectory(dir=c.arbeit, prefix=".tmp-") as tmp:
            workdir = Path(tmp)
            roh = self.ocr_text(original, workdir, c.ocr_sprache)
            codes: list[str] = []
            raw = None
            if roh.strip():
                raw = analyse(c, self.ki, roh, original, workdir)
                kat = raw.get("kategorie") if raw.get("kategorie") in invoice.KATEGORIEN else "sonstiges"
                text, glatt = glaetten_wenn_noetig(c, self.ki, original, roh)
                codes += glatt
            else:
                kat, text = "sonstiges", ""
                codes.append("KEIN_OCR_TEXT")
            ki_rechnung = invoice.ist_rechnungsartig(kat)
            ordner_rechnung = row["ordner"] == "rechnungen"
            widerspruch = ki_rechnung != ordner_rechnung
            ist_rechnung = ki_rechnung and ordner_rechnung
            if widerspruch:
                codes.append("EINORDNUNG_WIDERSPRUCH")

            inv = issues = notes = None
            if ist_rechnung:
                inv, issues, notes, methode, modell = self._rechnung_auslesen(original, roh, workdir, raw)

            # ---- Upload (genau einmal)
            if not row["task_id"]:
                basis = self.tag_rechnung if ist_rechnung else self.tag_dokument
                # Dateiname und Titel schon beim Upload aus den Rechnungsdaten (E48), sonst der Name aus dem Eingang
                titel_neu = invoice.titel(inv, self.cfg.titel_rechnung) if ist_rechnung else None
                titel_up = titel_neu or Path(row["dateiname"]).stem
                task = self.pl.hochladen(original, basis + ([self.tag_pruefen] if widerspruch and self.tag_pruefen else []),
                                         titel=titel_up, dateiname=titel_up + ".pdf")
                self.state.eingang_setzen(sha, task_id=task, status="hochgeladen")
            else:
                task = row["task_id"]
            doc_id = row["doc_id"] or self.pl.warte_auf_dokument(task)
            self.state.eingang_setzen(sha, doc_id=doc_id)

            meta = self.pl.metadaten(doc_id)
            if not pruefsumme_passt(meta.get("original_checksum"), sha_o, md5_o):
                raise PaperlessFehler("INTEGRITAET_ORIGINAL", str(doc_id))
            doc = self.pl.dokument(doc_id)

            tags = set(doc.get("tags") or [])
            tags |= set(self.tag_rechnung if ist_rechnung else self.tag_dokument)
            tags |= set(self.tag_kategorie.get(kat, []))
            patch: dict = {"content": text or doc.get("content", "")}
            if ist_rechnung:
                prev = self._duplikat(inv, doc_id)
                if prev is not None:
                    issues.append(f"DUPLIKAT_VON_DOKUMENT_{prev}")
                status = "ok" if not issues else "pruefen"
                patch["custom_fields"] = self._felder_mischen(doc.get("custom_fields"),
                                                              self._feldwerte(inv, status))
                neuer_titel = invoice.titel(inv, self.cfg.titel_rechnung)
                if neuer_titel:
                    patch["title"] = neuer_titel
                if issues and self.tag_pruefen:
                    tags.add(self.tag_pruefen)
            if widerspruch and self.tag_pruefen:
                tags.add(self.tag_pruefen)
            patch["tags"] = sorted(tags)
            if self.rechte:
                patch.update(self.rechte)
            else:
                patch["owner"] = None  # nicht dem nopaper-API-Benutzer gehoeren lassen
            sha_a = self._archiv_sha(doc_id, meta)
            self.pl.aendern(doc_id, patch)

            if ist_rechnung:
                self._sidecar(doc_id, inv, issues, notes + codes, "scanner", sha_o, sha_a, methode, modell, md5_o)

        original.unlink()
        self.state.eingang_setzen(sha, status="fertig", fehler=None)
        art = kat
        alle = (issues or []) + (notes or []) + codes
        log.info("eingang %s doc=%s art=%s status=%s codes=%s dauer=%.0fs", sha[:12], doc_id, art,
                 "pruefen" if (widerspruch or (issues or [])) else "ok", ",".join(alle) or "-",
                 time.monotonic() - t0)
        return art

    # ------------------------------------------------------------------ Weg 2: Nachbearbeitung
    def nachbearbeiten(self, doc_id: int) -> str:
        self.ids_laden()
        c = self.cfg
        t0 = time.monotonic()
        doc = self.pl.dokument(doc_id)
        meta = self.pl.metadaten(doc_id)
        with tempfile.TemporaryDirectory(dir=c.arbeit, prefix=".tmp-") as tmp:
            workdir = Path(tmp)
            original = workdir / "original.pdf"
            sha_o, md5_o = self.pl.hashes(doc_id, original=True, ziel=original)
            if not pruefsumme_passt(meta.get("original_checksum"), sha_o, md5_o):
                raise PaperlessFehler("INTEGRITAET_ORIGINAL")
            if (meta.get("original_mime_type") or "application/pdf") != "application/pdf":
                raise PaperlessFehler("FORMAT_NICHT_UNTERSTUETZT")
            roh = self.ocr_text(original, workdir, c.ocr_sprache)
            if not roh.strip():
                raise PaperlessFehler("KEIN_OCR_TEXT")
            text, codes = glaetten_wenn_noetig(c, self.ki, original, roh)
            inv, issues, notes, methode, modell = self._rechnung_auslesen(original, roh, workdir,
                                                                          kategorie="rechnung")
            prev = self._duplikat(inv, doc_id)
            if prev is not None:
                issues.append(f"DUPLIKAT_VON_DOKUMENT_{prev}")
            status = "ok" if not issues else "pruefen"
            sha_a = self._archiv_sha(doc_id, meta)

            tags = set(doc.get("tags") or [])
            tags.discard(self.tag_ausloeser)
            tags.discard(self.tag_pruefen)
            tags.discard(self.tag_text)  # der Volltext wird hier ohnehin erneuert
            tags |= set(self.tag_rechnung)
            tags |= set(self.tag_kategorie.get(inv["kategorie"], []))
            if issues and self.tag_pruefen:
                tags.add(self.tag_pruefen)
            aenderung = {
                "content": text,
                "tags": sorted(tags),
                "custom_fields": self._felder_mischen(doc.get("custom_fields"), self._feldwerte(inv, status)),
            }
            neuer_titel = invoice.titel(inv, self.cfg.titel_rechnung)
            if neuer_titel:
                aenderung["title"] = neuer_titel
            if c.zustandslos:
                # E61: Volltext, Tags, Felder, Titel und Sidecar in EINEM Aufruf – entweder alles steht in
                # paperless und der Ausloeser ist weg, oder nichts und der Ausloeser bleibt.
                xml = self._sidecar_xml(doc_id, inv, issues, notes + codes, "nachbearbeitung", sha_o, sha_a,
                                        methode, modell, md5_o)
                if xml is not None:
                    fid = self.felder["sidecar"][0]
                    aenderung["custom_fields"] = [f for f in aenderung["custom_fields"] if f["field"] != fid] + [
                        {"field": fid, "value": xml.decode("utf-8")}]
                self.pl.aendern(doc_id, aenderung)
            else:
                self.pl.aendern(doc_id, aenderung)
                self._sidecar(doc_id, inv, issues, notes + codes, "nachbearbeitung", sha_o, sha_a, methode,
                              modell, md5_o)
        log.info("nachbearbeitung doc=%s status=%s codes=%s dauer=%.0fs", doc_id, status,
                 ",".join(issues + notes + codes) or "-", time.monotonic() - t0)
        return status

    # ------------------------------------------------------------------ Schleife
    def _backoff(self, versuche: int) -> float:
        return time.time() + min(3600, 30 * 2 ** versuche)

    def eingang_abarbeiten(self) -> int:
        n = 0
        for row in self.state.eingang_offen():
            sha = row["sha256"]
            try:
                self.eingang_verarbeiten(row)
                n += 1
                self.sidecars_zustellen()
            except (Voruebergehend, llm.OllamaNichtErreichbar) as e:
                v = row["versuche"] + 1
                self.state.eingang_setzen(sha, versuche=v, nicht_vor=self._backoff(v), fehler="VORUEBERGEHEND")
                log.warning("eingang %s voruebergehender Fehler (%s), neuer Versuch spaeter", sha[:12], e)
                return n  # Dienst weg -> Durchlauf beenden
            except Exception as e:
                code = getattr(e, "code", None) or (str(e) if isinstance(e, (ocr.OcrError, FileNotFoundError))
                                                    else type(e).__name__)
                v = row["versuche"] + 1
                endgueltig = v >= self.cfg.max_versuche or code in ("DUPLIKAT_PAPERLESS", "INTEGRITAET_EINGANG")
                cur = self.state.eingang(sha)
                if endgueltig:
                    self._in_fehler(self.cfg.arbeit / f"{sha}.pdf", row["dateiname"], [code],
                                    cur["doc_id"] if cur else None)
                    self.state.eingang_setzen(sha, status="fehler", versuche=v, fehler=code)
                    log.error("eingang %s endgueltig fehlgeschlagen (%s) -> fehler/", sha[:12], code)
                else:
                    self.state.eingang_setzen(sha, versuche=v, nicht_vor=self._backoff(v), fehler=code)
                    log.warning("eingang %s Versuch %s fehlgeschlagen (%s)", sha[:12], v, code)
        return n

    # ------------------------------------------------------------------ Weg 3: nur Volltext erneuern
    def text_verbessern(self, doc_id: int) -> str:
        """Dokument mit Text-Ausloeser: Text neu erkennen und glaetten, NUR den Volltext zurueckschreiben
        (keine Felder, kein Titel, keine weiteren Tags, kein Sidecar). Ersetzt wird nur, wenn der neue
        Text nicht schlechter ist als der vorhandene."""
        self.ids_laden()
        c = self.cfg
        t0 = time.monotonic()
        doc = self.pl.dokument(doc_id)
        meta = self.pl.metadaten(doc_id)
        with tempfile.TemporaryDirectory(dir=c.arbeit, prefix=".tmp-") as tmp:
            workdir = Path(tmp)
            original = workdir / "original.pdf"
            sha_o, md5_o = self.pl.hashes(doc_id, original=True, ziel=original)
            if not pruefsumme_passt(meta.get("original_checksum"), sha_o, md5_o):
                raise PaperlessFehler("INTEGRITAET_ORIGINAL")
            if (meta.get("original_mime_type") or "application/pdf") != "application/pdf":
                raise PaperlessFehler("FORMAT_NICHT_UNTERSTUETZT")
            roh = self.ocr_text(original, workdir, c.ocr_sprache)
            if not roh.strip():
                raise PaperlessFehler("KEIN_OCR_TEXT")
            text, codes = glaetten_wenn_noetig(c, self.ki, original, roh)
        alt = doc.get("content") or ""
        ersetzen = ocr.text_nicht_schlechter(alt, text)
        tags = set(doc.get("tags") or [])
        tags.discard(self.tag_text)
        aenderung: dict = {"tags": sorted(tags)}
        if ersetzen:
            aenderung["content"] = text
        self.pl.aendern(doc_id, aenderung)
        (g_alt, q_alt), (g_neu, q_neu) = ocr.textguete(alt), ocr.textguete(text)
        ergebnis = "TEXT_ERSETZT" if ersetzen else "TEXT_BEHALTEN"
        log.info("text doc=%s %s lesbar alt=%d (%.0f%%) neu=%d (%.0f%%) codes=%s dauer=%.0fs", doc_id, ergebnis,
                 g_alt, q_alt * 100, g_neu, q_neu * 100, ",".join(codes) or "-", time.monotonic() - t0)
        return ergebnis

    def nachbearbeitung_abfragen(self) -> int:
        self.ids_laden()
        n = 0
        auftraege = [(d, self.tag_ausloeser, self.nachbearbeiten) for d in self.pl.mit_tag(self.tag_ausloeser)]
        if self.tag_text:
            schon = {d for d, _, _ in auftraege}
            auftraege += [(d, self.tag_text, self.text_verbessern) for d in self.pl.mit_tag(self.tag_text)
                          if d not in schon]
        for doc_id, tag_ausloeser, verarbeiten in auftraege:
            row = self.state.nach(doc_id)
            if row and row["nicht_vor"] > time.time():
                continue
            versuche = row["versuche"] if row else 0
            try:
                verarbeiten(doc_id)
                self.state.nach_loeschen(doc_id)
                n += 1
            except (Voruebergehend, llm.OllamaNichtErreichbar) as e:
                self.state.nach_setzen(doc_id, versuche + 1, self._backoff(versuche + 1), "VORUEBERGEHEND")
                log.warning("nachbearbeitung doc=%s voruebergehender Fehler (%s)", doc_id, e)
                return n
            except Exception as e:
                code = getattr(e, "code", None) or type(e).__name__
                v = versuche + 1
                if v >= self.cfg.max_versuche:
                    # Ausloeser entfernen, Pruef-Tag setzen, damit es nicht endlos wiederholt wird
                    try:
                        doc = self.pl.dokument(doc_id)
                        tags = (set(doc.get("tags") or []) - {tag_ausloeser}) | (
                            {self.tag_pruefen} if self.tag_pruefen else set())
                        self.pl.aendern(doc_id, {"tags": sorted(tags)})
                    except Exception:
                        pass
                    self.state.nach_loeschen(doc_id)
                    log.error("nachbearbeitung doc=%s endgueltig fehlgeschlagen (%s)", doc_id, code)
                else:
                    self.state.nach_setzen(doc_id, v, self._backoff(v), code)
                    log.warning("nachbearbeitung doc=%s Versuch %s fehlgeschlagen (%s)", doc_id, v, code)
        return n

    def sidecars_zustellen(self) -> int:
        """Wartende Sidecars an kumero senden (E56). Darf die Belegverarbeitung nie aufhalten oder abbrechen."""
        if self.kumero is None:
            return 0
        try:
            return self.kumero.zustellen()
        except Exception as e:  # noqa: BLE001 – bewusst alles abfangen
            log.error("kumero: Einlieferung fehlgeschlagen (%s)", type(e).__name__)
            return 0

    def ein_durchlauf(self) -> None:
        if self.cfg.zustandslos:
            # E61: kein Eingangsordner, keine Einlieferung – nur die Ausloeser-Tags in paperless
            if time.time() - self._letzte_abfrage >= self.cfg.abfrage_intervall_s:
                self._letzte_abfrage = time.time()
                self.nachbearbeitung_abfragen()
            return
        self.sidecars_zustellen()
        self.eingang_scannen()
        self.eingang_abarbeiten()
        if time.time() - self._letzte_abfrage >= self.cfg.abfrage_intervall_s:
            self._letzte_abfrage = time.time()
            self.nachbearbeitung_abfragen()
        self.sidecars_zustellen()

    def laufen(self) -> None:
        log.info("nopaper %s gestartet (betriebsart=%s, extraktion=%s/%s, bild=%s, glaettung=%s)", VERSION,
                 self.cfg.betriebsart, self.cfg.extraktor,
                 extraktions_modell(self.cfg), self.cfg.modell_bild or "-",
                 self.cfg.modell_glaettung or self.cfg.modell_text)
        while True:
            try:
                self.ein_durchlauf()
            except (Voruebergehend, llm.OllamaNichtErreichbar) as e:
                log.warning("Dienst voruebergehend nicht erreichbar (%s)", e)
            except PaperlessFehler as e:
                log.error("paperless: %s %s", e.code, e.detail)
                self._ids_ok = False
                time.sleep(60)
            time.sleep(5)
