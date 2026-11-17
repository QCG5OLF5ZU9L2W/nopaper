# SPDX-License-Identifier: AGPL-3.0-or-later
"""Testlauf ohne paperless: Text, Einordnung, Glaettung, Rechnungsdaten und Sidecar lokal.

Eingang:  /srv/nopaper/test/eingang/*.pdf          (wird nicht veraendert, kann mehrfach getestet werden)
          /srv/nopaper/test/eingang/<name>.soll.json (optional: erwartete Werte -> Trefferquote)
Ergebnis: /srv/nopaper/test/ergebnis/<modell>/<datei>/
            text-roh.txt      erkannter Text (Tesseract oder Textebene digitaler PDFs)
            volltext.txt      geglaetteter Text (das, was paperless als Inhalt bekaeme)
            ergebnis.json     Einordnung, Rechnungsdaten, Pruefcodes, Laufzeiten, Soll-Vergleich
            sidecar.xml       Sidecar mit paperless-document-id=TEST
          /srv/nopaper/test/ergebnis/<modell>/zusammenfassung.csv

NUR MIT MUSTERBELEGEN VERWENDEN – die Ergebnisdateien enthalten die Belegdaten im Klartext.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import logging
import re
import shutil
import tempfile
import time
from decimal import Decimal
from pathlib import Path

from . import VERSION, invoice, llm, ocr
from .config import Config
from .pipeline import (SIDECAR_HINWEIS, _ist_hinweis, analyse, datei_hashes, extraktions_modell,
                       glaetten_wenn_noetig, rechnung_auslesen)

log = logging.getLogger("nopaper")

SOLL_FELDER = ["art", "kategorie", "rechnungsnummer", "datum", "brutto", "netto", "steuer", "steller", "steuerid",
               "iban", "zahlbetrag", "faelligkeit", "skonto"]


def _json(o):
    if isinstance(o, (dt.date, dt.datetime)):
        return o.isoformat()
    if isinstance(o, Decimal):
        return str(o)
    raise TypeError(type(o).__name__)


def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s).strip("_") or "modell"


def _d(x) -> Decimal | None:
    return invoice.dec(x) if x not in (None, "") else None


def soll_vergleich(soll: dict, kat: str, inv: dict | None) -> dict[str, bool | None]:
    """Vergleicht Ergebnis und Soll. None = Feld nicht bewertbar (im Soll nicht angegeben)."""
    r: dict[str, bool | None] = {k: None for k in SOLL_FELDER}
    soll_rechnung = soll.get("art") == "rechnung"
    r["art"] = invoice.ist_rechnungsartig(kat) == soll_rechnung
    if soll.get("kategorie"):
        r["kategorie"] = kat == soll["kategorie"]
    if not soll_rechnung or "duplikat_von" in soll:
        return r
    inv = inv or {}
    seller = inv.get("seller") or {}

    def eq_dec(a, b):
        return a is not None and b is not None and abs(a - b) <= Decimal("0.01")
    if soll.get("invoice_number") or ("invoice_number" in soll and soll.get("kategorie") != "kassenbon"):
        ist = re.sub(r"\s+", "", inv.get("invoice_number") or "").upper()
        r["rechnungsnummer"] = ist == re.sub(r"\s+", "", soll["invoice_number"] or "").upper()
    if "issue_date" in soll:
        r["datum"] = (inv.get("issue_date").isoformat() if inv.get("issue_date") else None) == soll["issue_date"]
    if "due_date" in soll:
        r["faelligkeit"] = (inv.get("due_date").isoformat() if inv.get("due_date") else None) == soll["due_date"]
    if "skonto" in soll:
        r["skonto"] = (invoice.skonto_text(inv) or None) == (soll["skonto"] or None)
    for feld, key in (("brutto", "gross"), ("netto", "net"), ("steuer", "tax"), ("zahlbetrag", "due")):
        if key in soll:
            r[feld] = eq_dec(inv.get(key), _d(soll[key]))
    if soll.get("seller"):
        namen = [soll["seller"]] + soll.get("seller_alternativ", [])
        ist = (seller.get("name") or "").lower()
        r["steller"] = bool(ist) and any(
            invoice.name_im_text(n, ist) >= 0.8 or invoice.name_im_text(ist, n.lower()) >= 0.8 for n in namen)
    if "vat_id" in soll or "tax_number" in soll:
        erwartet = {re.sub(r"[\s.]+", "", x).upper() for x in (soll.get("vat_id"), soll.get("tax_number")) if x}
        ist = {re.sub(r"[\s.]+", "", x).upper() for x in (seller.get("vat_id"), seller.get("tax_number")) if x}
        r["steuerid"] = (ist & erwartet != set()) if erwartet else (not ist)
    if "iban" in soll:
        erwartet = set(soll["iban"] or [])
        r["iban"] = (inv.get("iban") in erwartet) if erwartet else (inv.get("iban") is None)
    return r


def aufraeumen(cfg: Config) -> None:
    for d in (cfg.test / "eingang", cfg.test / "ergebnis"):
        if d.is_dir():
            for p in d.iterdir():
                shutil.rmtree(p) if p.is_dir() else p.unlink()
    print(f"Testdaten in {cfg.test} geloescht.")


def testlauf(cfg: Config, als: str = "auto", ki: llm.Ollama | None = None,
             ocr_text=None, seite_als_bild=None) -> Path:
    ki = ki or llm.Ollama(cfg.ollama_url, cfg.num_ctx, cfg.ollama_timeout)
    ocr_text = ocr_text or ocr.ocr_text
    seite_als_bild = seite_als_bild or ocr.seite_als_bild
    ein = cfg.test / "eingang"
    dateien = sorted(p for p in ein.glob("*") if p.is_file() and p.suffix.lower() == ".pdf"
                     and not p.name.startswith("."))
    if not dateien:
        raise SystemExit(f"Keine PDFs in {ein} – Musterbelege dorthin kopieren "
                         f"(als root: nopaper test /pfad/zu/ordner/)")
    vorhanden = ki.modelle()
    benoetigt = {cfg.modell_bild, cfg.modell_glaettung if cfg.modell_glaettung != "aus" else ""}
    benoetigt.add(extraktions_modell(cfg))
    if cfg.modell_glaettung in ("", None):
        benoetigt.add(cfg.modell_text)
    for m in {x for x in benoetigt if x}:
        if not any(x == m or x == m + ":latest" for x in vorhanden):
            raise SystemExit(f"Modell {m} ist in Ollama nicht installiert (ollama pull {m})")

    glatt_modell = cfg.modell_glaettung or cfg.modell_text
    ext = extraktions_modell(cfg) + (f"_{cfg.nuextract_eingabe}" if cfg.extraktor == "nuextract3" else "")
    lauf = _slug(ext) + ("__" + _slug(cfg.modell_bild) if cfg.modell_bild else "__ohne_bild") + (
        "__g_" + _slug(glatt_modell) if glatt_modell != cfg.modell_text else "")
    ziel = cfg.test / "ergebnis" / lauf
    ziel.mkdir(parents=True, exist_ok=True)
    zeilen, treffer = [], {k: [0, 0] for k in SOLL_FELDER}
    n_soll = sum(1 for f in dateien if f.with_name(f.stem + ".soll.json").exists())
    print(f"Soll-Werte gefunden: {n_soll} von {len(dateien)}" + (
        "  (Dateien <name>.soll.json neben die PDFs legen)" if n_soll < len(dateien) else ""))
    print(f"Testlauf: {len(dateien)} Datei(en), Extraktion={ext}, Bild-Rueckfall={cfg.modell_bild or '-'}, "
          f"Glaettung={glatt_modell}")

    for nr, f in enumerate(dateien, 1):
        out = ziel / _slug(f.stem)
        out.mkdir(exist_ok=True)
        t: dict[str, float] = {}
        erg: dict = {"datei": f.name, "nopaper": VERSION, "modell_text": ext,
                     "modell_bild": cfg.modell_bild, "modell_glaettung": glatt_modell}
        kat, status, methode, inv = "-", "-", "-", None
        try:
            sha, _ = datei_hashes(f)
            erg["sha256"] = sha
            with tempfile.TemporaryDirectory(dir=cfg.arbeit, prefix=".tmp-test-") as tmp:
                w = Path(tmp)
                t0 = time.monotonic()
                erg["textquelle"] = "textebene" if ocr.ist_digital(f) else "ocr"
                roh = ocr_text(f, w, cfg.ocr_sprache)
                t["text"] = time.monotonic() - t0
                (out / "text-roh.txt").write_text(roh, encoding="utf-8")
                codes: list[str] = []
                raw = None
                if not roh.strip():
                    codes.append("KEIN_OCR_TEXT")
                    kat, text = "sonstiges", ""
                else:
                    t0 = time.monotonic()
                    raw = analyse(cfg, ki, roh, f, w)
                    t["analyse"] = time.monotonic() - t0
                    kat = raw.get("kategorie") if raw.get("kategorie") in invoice.KATEGORIEN else "sonstiges"
                    t0 = time.monotonic()
                    text, glatt = glaetten_wenn_noetig(cfg, ki, f, roh)
                    t["glaettung"] = time.monotonic() - t0
                    codes += glatt
                (out / "volltext.txt").write_text(text, encoding="utf-8")
                ki_rechnung = invoice.ist_rechnungsartig(kat)
                if als != "auto" and ki_rechnung != (als == "rechnung"):
                    codes.append("EINORDNUNG_WIDERSPRUCH")
                ist_rechnung = ki_rechnung if als == "auto" else (ki_rechnung and als == "rechnung")
                erg.update({"kategorie": kat, "als_rechnung_verarbeitet": ist_rechnung})

                if ist_rechnung:
                    t0 = time.monotonic()
                    inv, issues, notes, methode, modell = rechnung_auslesen(cfg, ki, seite_als_bild, f, roh, w, raw)
                    t["bildmodell"] = time.monotonic() - t0
                    status = "ok" if not [i for i in issues if not _ist_hinweis(i)] else "pruefen"
                    erg.update({"rechnungsdaten": inv, "pruefstatus": status, "pruefcodes": issues,
                                "hinweise": notes + codes, "methode": methode})
                    prov = {"paperless-document-id": "TEST", "sha256-original": sha, "quelle": "testlauf",
                            "extraktion-modell": modell, "extraktion-methode": methode,
                            "extraktion-software": f"nopaper {VERSION}",
                            "extraktion-zeit": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
                            "pruefstatus": status, "pruefhinweise": ",".join(issues + notes + codes),
                            "hinweis": SIDECAR_HINWEIS}
                    (out / "sidecar.xml").write_bytes(invoice.build_cii(inv, prov))
                else:
                    erg["hinweise"] = codes
        except (ocr.OcrError, llm.OllamaNichtErreichbar, json.JSONDecodeError) as e:
            erg["fehler"] = str(e) or type(e).__name__
            status = "FEHLER"

        soll_datei = f.with_name(f.stem + ".soll.json")
        vergleich = None
        if soll_datei.exists():
            soll = json.loads(soll_datei.read_text(encoding="utf-8"))
            vergleich = soll_vergleich(soll, kat, inv)
            erg["soll_vergleich"] = vergleich
            for k, v in vergleich.items():
                if v is not None:
                    treffer[k][1] += 1
                    treffer[k][0] += int(v)

        erg["sekunden"] = {k: round(v, 1) for k, v in t.items()}
        (out / "ergebnis.json").write_text(json.dumps(erg, default=_json, ensure_ascii=False, indent=2),
                                           encoding="utf-8")
        gesamt = sum(t.values())
        alle = erg.get("pruefcodes", []) + erg.get("hinweise", [])
        falsch = [k for k, v in (vergleich or {}).items() if v is False]
        zeilen.append({"datei": f.name, "kategorie": kat, "pruefstatus": status,
                       "methode": methode, "textquelle": erg.get("textquelle", ""), "codes": " ".join(alle),
                       "soll_falsch": " ".join(falsch) if vergleich else "",
                       **{f"s_{k}": round(v, 1) for k, v in t.items()}, "s_gesamt": round(gesamt, 1),
                       "fehler": erg.get("fehler", "")})
        print(f"[{nr}/{len(dateien)}] {f.name}: {kat} status={status} methode={methode} "
              f"{gesamt:.0f}s  {' '.join(alle)}" + (f"  SOLL-FALSCH: {' '.join(falsch)}" if falsch else ""))

    spalten = ["datei", "kategorie", "pruefstatus", "methode", "textquelle", "codes", "soll_falsch",
               "s_text", "s_analyse", "s_glaettung", "s_bildmodell", "s_gesamt", "fehler"]
    buf = io.StringIO()
    wr = csv.DictWriter(buf, fieldnames=spalten, delimiter=";", extrasaction="ignore")
    wr.writeheader()
    wr.writerows(zeilen)
    (ziel / "zusammenfassung.csv").write_text(buf.getvalue(), encoding="utf-8")
    n = len(zeilen)
    rech = [z for z in zeilen if z["pruefstatus"] in ("ok", "pruefen")]
    ok = sum(1 for z in rech if z["pruefstatus"] == "ok")
    dauer = sum(z["s_gesamt"] for z in zeilen)
    print(f"\nFertig: {n} Datei(en), {len(rech)} als Rechnung, davon {ok} ok / {len(rech) - ok} pruefen, "
          f"{sum(1 for z in zeilen if z['pruefstatus'] == 'FEHLER')} Fehler, "
          f"im Schnitt {dauer / max(n, 1):.0f}s pro Datei")
    bewertet = {k: v for k, v in treffer.items() if v[1]}
    if bewertet:
        print("Trefferquote gegen Soll-Werte:")
        zeilen_tq = []
        for k, (a, b) in bewertet.items():
            print(f"  {k:16} {a:3}/{b:<3} {100 * a / b:5.1f} %")
            zeilen_tq.append(f"{k};{a};{b};{100 * a / b:.1f}")
        (ziel / "trefferquote.csv").write_text("feld;richtig;bewertet;prozent\n" + "\n".join(zeilen_tq) + "\n",
                                               encoding="utf-8")
    print(f"Ergebnisse: {ziel}")
    return ziel
