#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Offline-Tests. Ein Fake-Server spielt paperless-REST-API und Ollama, OCR wird simuliert.
Es werden ausschliesslich synthetische Musterdaten verwendet."""
import copy
import datetime as dt
from decimal import Decimal
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
import uuid
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
from nopaper import config, invoice, llm, ocr  # noqa: E402
from nopaper.main import _paperless_skript  # noqa: E402
from nopaper.paperless import Voruebergehend  # noqa: E402
from nopaper.pipeline import Nopaper  # noqa: E402

RECHNUNG_TEXT = """Praxis Dr. Beispiel  Hauptstr. 1  10115 Berlin
Steuernummer 12/345/67890
Rechnuug Nr. RE-2026-0815     Datum 03.11.2026
Leistungen lt. Anlage
Summe netto 1.234,56 EUR
Steuerfrei nach § 4 Nr. 14 UStG
Rechnungsbetrag 1.234,56 EUR
IBAN DE89 3704 0044 0532 0130 00"""

BRIEF_TEXT = "Sehr geehrte Damen und Herreu,\nanbei die Unterlagen vom 12.10.2026.\nMit freundlichen Gruessen"

LLM_RECHNUNG = {
    "document_type": "rechnung", "invoice_number": "RE-2026-0815", "issue_date": "2026-11-03",
    "delivery_date": None, "due_date": "2026-12-03", "currency": "EUR",
    "seller": {"name": "Praxis Dr. Beispiel", "street": "Hauptstr. 1", "postcode": "10115", "city": "Berlin",
               "country_code": "DE", "vat_id": None, "tax_number": "12/345/67890"},
    "payment": {"iban": "DE89 3704 0044 0532 0130 00", "bic": None, "reference": "RE-2026-0815"},
    "totals": {"net_total": 1234.56, "tax_total": 0, "gross_total": 1234.56, "prepaid": None,
               "amount_due": 1234.56},
    "vat_breakdown": [{"rate": 0, "basis": 1234.56, "tax": 0,
                       "exemption_reason": "Steuerfrei nach § 4 Nr. 14 UStG"}],
}

TAGS = ["Rechnung", "Eingangsrechnung", "Scan", "nopaper prüfen", "nopaper Rechnung",
        "Bewirtung", "Kassenbon", "Versicherung", "Behörde", "totext"]
FELDER = {"Erkannt: Rechnungsnummer": "string", "Erkannt: Rechnungsdatum": "date", "Zahlungsziel": "date",
          "Erkannt: Rechnungssteller": "string", "Erkannt: Betrag brutto": "monetary",
          "Erkannt: Betrag netto": "monetary", "Erkannt: Steuer_High": "monetary",
          "Erkannt: Steuer_Low": "monetary", "Erkannt: Zahlbetrag": "monetary", "Erkannt: Skonto": "string",
          "IBAN": "string", "Erkannt: Belegart": "string", "Erkannt: Verwendungszweck": "string"}


class Welt:
    """Zustand des Fake-paperless und Steuerung des Fake-Ollama."""
    def reset(self):
        self.tags = {i + 1: n for i, n in enumerate(TAGS)}
        self.felder = {i + 1: (n, t) for i, (n, t) in enumerate(FELDER.items())}
        self.docs, self.tasks, self.next_id = {}, {}, 100
        self.uploads = 0
        self.dateinamen = []
        self.einordnung = "rechnung"
        self.extraktion = LLM_RECHNUNG
        self.bild_extraktion = None
        self.glaettung_kaputt = False
        self.llm_calls = []
        # nachgebautes kumero (Buchhaltung): Verhalten und Mitschnitt
        self.kumero_modus = "ok"          # ok | 503 | 422 | 401 | 404 | 429 | langsam | falscher_hash
        self.kumero_status_modus = "ok"   # ok | 401 | 404 | 503 | nicht_bereit
        self.kumero_anfragen = []         # (Methode, Pfad, Kopfzeilen als dict, Koerper)
        self.kumero_bekannt = {}          # paperless_id -> sha256


W = Welt()


class Handler(BaseHTTPRequestHandler):
    def _send(self, obj, ctype="application/json", code=200):
        data = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass  # der Client hat wegen Zeitueberschreitung schon aufgelegt

    def _body(self):
        return self.rfile.read(int(self.headers.get("Content-Length") or 0))

    def log_message(self, *a):
        pass

    def _kumero(self, methode):
        body = self._body() if methode == "PUT" else b""
        kopf = {k.lower(): v for k, v in self.headers.items()}
        W.kumero_anfragen.append((methode, self.path, kopf, body))
        if kopf.get("authorization") != "Bearer kmr_testtoken":
            return self._send({"detail": "unauthorized"}, code=401)
        if self.path.endswith("/belegeingang/status"):
            m = W.kumero_status_modus
            if m == "ok":
                return self._send({"bereit": True})
            if m == "nicht_bereit":
                return self._send({"bereit": False})
            return self._send({}, code=int(m))
        m = W.kumero_modus
        if m == "langsam":
            time.sleep(1.5)
            m = "ok"
        if m == "503":
            return self._send({}, code=503)
        if m in ("401", "404"):
            return self._send({}, code=int(m))
        if m == "429":
            self.send_response(429)
            self.send_header("Retry-After", "30")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if m == "422":
            return self._send({"type": "about:blank", "code": "sidecar.pflichtangabe"},
                              ctype="application/problem+json", code=422)
        pid = int(self.path.rsplit("/", 1)[1])
        sha = hashlib.sha256(body).hexdigest()
        if m == "falscher_hash":
            return self._send({"ergebnis": "angelegt", "paperless_id": pid, "sha256": "0" * 64}, code=201)
        if W.kumero_bekannt.get(pid) == sha:
            return self._send({"ergebnis": "unveraendert", "paperless_id": pid, "sha256": sha})
        neu = pid not in W.kumero_bekannt
        W.kumero_bekannt[pid] = sha
        self._send({"ergebnis": "angelegt" if neu else "ersetzt", "paperless_id": pid, "sha256": sha},
                   code=201 if neu else 200)

    def do_PUT(self):
        if self.path.startswith("/m/"):
            return self._kumero("PUT")
        self._send({}, code=404)

    def do_GET(self):
        if self.path.startswith("/m/"):
            return self._kumero("GET")
        u = urllib.parse.urlsplit(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        if u.path == "/api/tags":  # Ollama
            return self._send({"models": [{"name": "qwen3.5:9b"}, {"name": "qwen3-vl:8b-instruct"},
                                          {"name": "qwen3.5:4b"}]})
        assert self.headers["Authorization"] == "Token testtoken"
        if u.path == "/api/tags/":
            return self._send({"next": None, "results": [{"id": i, "name": n} for i, n in W.tags.items()]})
        if u.path == "/api/custom_fields/":
            return self._send({"next": None, "results": [{"id": i, "name": n, "data_type": t}
                                                         for i, (n, t) in W.felder.items()]})
        if u.path == "/api/tasks/":
            if "task_id" not in q:
                return self._send(list(W.tasks.values())[-5:])
            t = W.tasks.get(q["task_id"])
            return self._send([t] if t else [])
        if u.path == "/api/documents/":
            tid = int(q["tags__id__all"])
            return self._send({"next": None, "results": [{"id": i} for i, d in sorted(W.docs.items())
                                                         if tid in d["tags"]]})
        p = u.path.rstrip("/").split("/")
        d = W.docs.get(int(p[3]))
        if d is None:
            return self._send({"detail": "not found"}, code=404)
        if len(p) == 4:
            return self._send({k: d[k] for k in ("id", "content", "tags", "custom_fields", "owner")})
        if p[4] == "metadata":
            return self._send({"original_checksum": hashlib.md5(d["original"]).hexdigest(),
                               "archive_checksum": hashlib.md5(d["archive"]).hexdigest(),
                               "has_archive_version": True, "original_mime_type": "application/pdf"})
        if p[4] == "download":
            return self._send(d["original"] if q.get("original") == "true" else d["archive"], "application/pdf")
        self._send({}, code=404)

    def do_POST(self):
        body = self._body()
        if self.path == "/api/chat":
            return self._ollama(json.loads(body))
        assert self.path == "/api/documents/post_document/"
        W.uploads += 1
        boundary = self.headers["Content-Type"].split("boundary=")[1].encode()
        tags, datei = [], None
        for part in body.split(b"--" + boundary):
            if b'name="tags"' in part:
                tags.append(int(part.split(b"\r\n\r\n", 1)[1].strip()))
            if b'name="document"' in part:
                datei = part.split(b"\r\n\r\n", 1)[1].rsplit(b"\r\n", 1)[0]
                W.dateinamen.append(re.search(rb'filename="([^"]*)"', part).group(1).decode("utf-8"))
        task = str(uuid.uuid4())
        md5 = hashlib.md5(datei).hexdigest()
        if any(hashlib.md5(d["original"]).hexdigest() == md5 for d in W.docs.values()):
            W.tasks[task] = {"task_id": task, "status": "FAILURE", "related_document": None,
                             "result": "Not consuming x: It is a duplicate of y"}
        else:
            W.next_id += 1
            W.docs[W.next_id] = {"id": W.next_id, "content": "paperless-ocr", "tags": tags, "custom_fields": [],
                                 "owner": 7, "original": datei, "archive": b"ARCHIV" + datei}
            W.tasks[task] = {"task_id": task, "status": "SUCCESS", "related_document": str(W.next_id), "result": "ok"}
        self._send(task)

    def do_PATCH(self):
        body = json.loads(self._body())
        doc = W.docs[int(self.path.rstrip("/").split("/")[3])]
        doc.update({k: v for k, v in body.items() if k in ("content", "tags", "custom_fields", "owner", "title")})
        self._send({"id": doc["id"]})

    def _ollama(self, req):
        if req["messages"][0]["role"] == "template":  # NuExtract3
            W.llm_calls.append((req["model"], "nuextract", bool(req["messages"][-1].get("images"))))
            ans = copy.deepcopy(W.extraktion)
            ans["kategorie"] = [W.einordnung]
            ans["seller"]["country_code"] = "Deutschland"
            ans["issue_date"] = ans["issue_date"] + "T00:00:00"
            return self._send({"message": {"role": "assistant",
                                           "content": "```json\n" + json.dumps(ans) + "\n```"}})
        system = req["messages"][0]["content"]
        user = req["messages"][1]["content"]
        W.llm_calls.append((req["model"], system[:20], bool(req["messages"][1].get("images"))))
        if system.startswith("Du korrigierst"):
            t = user.replace("Rechnuug", "Rechnung").replace("Herreu", "Herren")
            if W.glaettung_kaputt:
                t = t.replace("1.234,56", "1.234,59")
            ans = {"text": t}
        elif req["messages"][1].get("images"):
            ans = W.bild_extraktion or W.extraktion
        else:  # kombinierte Analyse: Kategorie + Rechnungsdaten
            ans = dict(W.extraktion, kategorie=W.einordnung, sicherheit=0.9)
        self._send({"message": {"role": "assistant", "content": json.dumps(ans)}})


def fake_ocr(original: Path, workdir: Path, sprache: str = "deu") -> str:
    data = original.read_bytes()
    return RECHNUNG_TEXT if b"RECHNUNG" in data else (BRIEF_TEXT if b"BRIEF" in data else "")


class NopaperTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        W.reset()
        self.tmp = Path(tempfile.mkdtemp())
        d = json.loads((ROOT / "config.example.json").read_text())
        d["paperless"]["url"] = self.base
        d["paperless"]["token_datei"] = str(self.tmp / "token")
        (self.tmp / "token").write_text("testtoken\n")
        d["basisverzeichnis"] = str(self.tmp)
        d["eingang"] = {"rechnungen": str(self.tmp / "ein/rechnungen"), "dokumente": str(self.tmp / "ein/dokumente"),
                        "fehler": str(self.tmp / "fehler")}
        d["ausgang"]["rechnungsdaten"] = str(self.tmp / "aus")
        d["empfaenger"] = "Test Klinik GmbH"
        d["modelle"]["ollama_url"] = self.base
        d["eingang_ruhezeit_s"] = 0
        (self.tmp / "config.json").write_text(json.dumps(d))
        for p in ("ein/rechnungen", "ein/dokumente"):
            (self.tmp / p).mkdir(parents=True)
        self.cfg = config.load(self.tmp / "config.json")
        self.np = Nopaper(self.cfg, ocr_text=fake_ocr, seite_als_bild=lambda o, w: "QklMRA==",
                          zweite_lesung=lambda o, w, s="deu": "")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def scan(self, ordner, name, inhalt: bytes):
        f = self.tmp / "ein" / ordner / name
        f.write_bytes(inhalt)
        os.utime(f, (time.time() - 60, time.time() - 60))
        return f

    def tagnamen(self, doc_id):
        return {W.tags[t] for t in W.docs[doc_id]["tags"]}

    def note(self, doc_id):
        root = ET.parse(self.tmp / "aus" / f"{doc_id:07d}.xml").getroot()
        c = root.find(".//{%s}IncludedNote/{%s}Content" % (invoice.NS["ram"], invoice.NS["ram"])).text
        return root, dict(l.split("=", 1) for l in c.splitlines()[1:])

    # ------------------------------------------------------------------ Tests
    def test_rechnung_komplett(self):
        orig = b"%PDF-1.4 RECHNUNG scan 1"
        f = self.scan("rechnungen", "scan_0001.pdf", orig)
        self.np.ein_durchlauf()
        self.assertFalse(f.exists(), "Eingang muss nach Erfolg geloescht sein")
        self.assertEqual(list((self.tmp / "arbeit").glob("*.pdf")), [])
        doc_id = max(W.docs)
        doc = W.docs[doc_id]
        self.assertEqual(doc["original"], orig, "Original muss unveraendert hochgeladen werden")
        self.assertEqual(self.tagnamen(doc_id), {"Rechnung", "Eingangsrechnung"})
        self.assertIn("Rechnung Nr.", doc["content"])            # geglaettet
        self.assertNotIn("Rechnuug", doc["content"])
        self.assertIsNone(doc["owner"])                           # nicht dem API-Benutzer gehoeren lassen
        felder = {W.felder[f["field"]][0]: f["value"] for f in doc["custom_fields"]}
        self.assertEqual(felder["Erkannt: Rechnungsnummer"], "RE-2026-0815")
        self.assertEqual(felder["Erkannt: Betrag brutto"], "EUR1234.56")
        self.assertEqual(felder["Erkannt: Rechnungsdatum"], "2026-11-03")
        self.assertNotIn("Erkannt: Steuer_High", felder)  # steuerfreier Beleg: Felder bleiben leer, nicht 0
        self.assertNotIn("Erkannt: Steuer_Low", felder)
        self.assertEqual(felder["Erkannt: Zahlbetrag"], "EUR1234.56")
        self.assertNotIn("Erkannt: Skonto", felder)
        self.assertEqual(doc["title"], "2026-11-03 Praxis Dr. Beispiel 1234,56 EUR")
        self.assertEqual(W.dateinamen[-1], "2026-11-03 Praxis Dr. Beispiel 1234,56 EUR.pdf")
        root, note = self.note(doc_id)
        self.assertEqual(note["paperless-document-id"], str(doc_id))
        self.assertEqual(note["sha256-original"], hashlib.sha256(orig).hexdigest())
        self.assertEqual(note["sha256-archiv"], hashlib.sha256(b"ARCHIV" + orig).hexdigest())
        self.assertEqual(note["quelle"], "scanner")
        self.assertEqual(note["pruefstatus"], "ok", note.get("pruefhinweise"))
        ram = "{%s}" % invoice.NS["ram"]
        self.assertEqual(root.find(f".//{ram}BuyerTradeParty/{ram}Name").text, "Test Klinik GmbH")
        self.assertEqual(W.uploads, 1)
        # Datenminimierung: Empfaenger wird nicht abgefragt
        self.assertNotIn("buyer", invoice.build_schema(False)["properties"])

    def test_nuextract_bild(self):
        from nopaper import ocr
        alt = ocr.seiten_als_bilder
        ocr.seiten_als_bilder = lambda *a, **k: ["QklMRA=="]
        try:
            self.cfg.extraktor, self.cfg.modell_extraktion = "nuextract3", "numind/nuextract3:Q4_K_M"
            self.cfg.nuextract_eingabe = "bild"
            self.scan("rechnungen", "n.pdf", b"%PDF RECHNUNG nx")
            self.np.ein_durchlauf()
        finally:
            ocr.seiten_als_bilder = alt
        self.assertIn(("numind/nuextract3:Q4_K_M", "nuextract", True), W.llm_calls)
        root, note = self.note(max(W.docs))
        self.assertEqual(note["pruefstatus"], "ok", note.get("pruefhinweise"))
        self.assertEqual(note["extraktion-modell"], "numind/nuextract3:Q4_K_M")
        self.assertEqual(note["extraktion-methode"], "bild")
        ram = "{%s}" % invoice.NS["ram"]
        self.assertEqual(root.find(f".//{ram}CountryID").text, "DE")

    def test_nuextract_text(self):
        self.cfg.extraktor, self.cfg.modell_extraktion = "nuextract3", "numind/nuextract3:Q4_K_M"
        self.cfg.nuextract_eingabe = "text"
        self.scan("rechnungen", "n.pdf", b"%PDF RECHNUNG nt")
        self.np.ein_durchlauf()
        self.assertIn(("numind/nuextract3:Q4_K_M", "nuextract", False), W.llm_calls)
        _, note = self.note(max(W.docs))
        self.assertEqual(note["pruefstatus"], "ok", note.get("pruefhinweise"))

    def test_dokument(self):
        W.einordnung = "brief"
        self.scan("dokumente", "brief.pdf", b"%PDF BRIEF 1")
        self.np.ein_durchlauf()
        doc_id = max(W.docs)
        self.assertEqual(self.tagnamen(doc_id), {"Scan"})
        self.assertIn("Damen und Herren", W.docs[doc_id]["content"])
        self.assertEqual(W.docs[doc_id]["custom_fields"], [])
        self.assertFalse((self.tmp / "aus" / f"{doc_id:07d}.xml").exists())

    def test_widerspruch(self):
        W.einordnung = "brief"
        self.scan("rechnungen", "falsch.pdf", b"%PDF BRIEF 2")
        self.np.ein_durchlauf()
        doc_id = max(W.docs)
        self.assertEqual(self.tagnamen(doc_id), {"Scan", "nopaper prüfen"})
        self.assertFalse((self.tmp / "aus" / f"{doc_id:07d}.xml").exists())

    def test_glaettung_ziffernregel(self):
        W.glaettung_kaputt = True
        self.scan("rechnungen", "r.pdf", b"%PDF RECHNUNG 3")
        self.np.ein_durchlauf()
        doc_id = max(W.docs)
        self.assertIn("1.234,56", W.docs[doc_id]["content"])
        self.assertNotIn("1.234,59", W.docs[doc_id]["content"])
        self.assertIn("Rechnung Nr.", W.docs[doc_id]["content"])  # andere Zeilen bleiben korrigiert
        _, note = self.note(doc_id)
        self.assertIn("GLAETTUNG_TEILWEISE", note["pruefhinweise"])

    def test_brutto_aus_aufschluesselung(self):
        # Modell liest falschen Endbetrag, Aufschluesselung stimmt und ihre Summe steht im Text
        bad = copy.deepcopy(LLM_RECHNUNG)
        bad["totals"]["gross_total"] = 1300.0
        W.extraktion = bad
        self.scan("rechnungen", "r.pdf", b"%PDF RECHNUNG 4b")
        self.np.ein_durchlauf()
        _, note = self.note(max(W.docs))
        self.assertEqual(note["pruefstatus"], "ok", note.get("pruefhinweise"))
        self.assertIn("BRUTTO_AUS_AUFSCHLUESSELUNG", note["pruefhinweise"])
        self.assertEqual(note["extraktion-methode"], "text")

    def test_bild_rueckfall(self):
        bad = copy.deepcopy(LLM_RECHNUNG)
        bad["totals"]["gross_total"] = 1300.0
        bad["vat_breakdown"][0]["basis"] = 1300.0
        W.extraktion = bad
        W.bild_extraktion = LLM_RECHNUNG
        self.scan("rechnungen", "r.pdf", b"%PDF RECHNUNG 4")
        self.np.ein_durchlauf()
        _, note = self.note(max(W.docs))
        self.assertEqual(note["extraktion-methode"], "bild")
        self.assertEqual(note["extraktion-modell"], "qwen3-vl:8b-instruct")
        self.assertIn("BILDMODELL_VERWENDET", note["pruefhinweise"])
        self.assertEqual(note["pruefstatus"], "ok")

    def test_pruefen_bei_fehlern(self):
        bad = copy.deepcopy(LLM_RECHNUNG)
        bad["invoice_number"] = "RE-9999"
        W.extraktion = bad
        self.scan("rechnungen", "r.pdf", b"%PDF RECHNUNG 5")
        self.np.ein_durchlauf()
        doc_id = max(W.docs)
        self.assertIn("nopaper prüfen", self.tagnamen(doc_id))
        _, note = self.note(doc_id)
        self.assertEqual(note["pruefstatus"], "pruefen")
        self.assertIn("RECHNUNGSNUMMER_NICHT_IM_TEXT", note["pruefhinweise"])

    def test_kein_doppelter_upload_nach_absturz(self):
        self.scan("rechnungen", "r.pdf", b"%PDF RECHNUNG 6")
        orig = self.np.pl.aendern
        calls = {"n": 0}

        def flaky(doc_id, data):
            calls["n"] += 1
            if calls["n"] == 1:
                raise Voruebergehend("Netz weg")
            return orig(doc_id, data)
        self.np.pl.aendern = flaky
        self.np.ein_durchlauf()
        self.np.state.db.execute("UPDATE eingang SET nicht_vor=0")
        self.np.ein_durchlauf()
        self.assertEqual(W.uploads, 1)
        self.assertTrue((self.tmp / "aus" / f"{max(W.docs):07d}.xml").exists())

    def test_duplikat_eingang(self):
        self.scan("rechnungen", "a.pdf", b"%PDF RECHNUNG 7")
        self.np.ein_durchlauf()
        self.scan("rechnungen", "b.pdf", b"%PDF RECHNUNG 7")
        self.np.ein_durchlauf()
        self.assertTrue((self.tmp / "fehler" / "b.pdf").exists())
        info = json.loads((self.tmp / "fehler" / "b.pdf.fehler.json").read_text())
        self.assertEqual(info["codes"], ["DUPLIKAT_EINGANG"])
        self.assertEqual(W.uploads, 1)

    def test_duplikat_in_paperless(self):
        W.docs[50] = {"id": 50, "content": "", "tags": [], "custom_fields": [], "owner": None,
                      "original": b"%PDF RECHNUNG 8", "archive": b"x"}
        self.scan("rechnungen", "c.pdf", b"%PDF RECHNUNG 8")
        self.np.ein_durchlauf()
        info = json.loads((self.tmp / "fehler" / "c.pdf.fehler.json").read_text())
        self.assertEqual(info["codes"], ["DUPLIKAT_PAPERLESS"])

    def test_text_erneuern(self):
        t = self.np
        t.ids_laden()
        tid = t.tag_text
        # schlechter alter Text -> wird ersetzt; nur Volltext und Ausloeser-Tag aendern sich
        W.docs[70] = {"id": 70, "content": "R3chn||ng ~~ %% ##", "tags": [3, tid], "title": "alt",
                      "custom_fields": [{"field": 99, "value": "x"}], "owner": 3,
                      "original": b"%PDF BRIEF Sehr geehrte Herreu", "archive": b"A"}
        # guter alter Text, neuer waere viel kuerzer -> bleibt
        lang = "Sehr geehrte Damen und Herren, " * 40
        W.docs[71] = {"id": 71, "content": lang, "tags": [tid], "title": "alt", "custom_fields": [], "owner": None,
                      "original": b"%PDF BRIEF kurz", "archive": b"A"}
        n_up = W.uploads
        t.nachbearbeitung_abfragen()
        d = W.docs[70]
        self.assertNotIn("R3chn", d["content"])
        self.assertEqual(d["tags"], [3])
        self.assertEqual(d["title"], "alt")
        self.assertEqual(d["custom_fields"], [{"field": 99, "value": "x"}])
        self.assertEqual(d["owner"], 3)
        self.assertEqual(W.docs[71]["content"], lang)
        self.assertEqual(W.docs[71]["tags"], [])
        self.assertEqual(W.uploads, n_up)
        self.assertEqual(list(self.cfg.ausgang_rechnungsdaten.glob("*.xml")), [])
        self.assertFalse(ocr.text_nicht_schlechter("guter langer Text mit Inhalt", ""))
        self.assertTrue(ocr.text_nicht_schlechter("", "neuer Text"))

    # ------------------------------------------------------------------ kumero (E56), Messpunkte des Auftrags
    def mit_kumero(self, url=None, token="kmr_testtoken\n", ca=None):
        d = json.loads((self.tmp / "config.json").read_text())
        (self.tmp / "kumero.token").write_text(token)
        d["kumero"] = {"url": url or self.base, "mandant": "praxis", "token_datei": str(self.tmp / "kumero.token")}
        if ca:
            d["kumero"]["ca_datei"] = str(ca)
        d["tags"]["zu_buchen"] = "Scan"   # irgendein vorhandener Tag als "noch zu buchen"
        (self.tmp / "config.json").write_text(json.dumps(d))
        self.cfg = config.load(self.tmp / "config.json")
        self.np = Nopaper(self.cfg, ocr_text=fake_ocr, seite_als_bild=lambda o, w: "QklMRA==",
                          zweite_lesung=lambda o, w, s="deu": "")
        return self.np

    def sidecar_erzeugen(self, name="k1.pdf", inhalt=b"%PDF RECHNUNG kumero 1"):
        """Verarbeitet einen Beleg, waehrend kumero nicht antwortet – der Sidecar bleibt in der Warteschlange."""
        W.kumero_modus = "503"
        self.scan("rechnungen", name, inhalt)
        self.np.ein_durchlauf()
        dateien = sorted((self.tmp / "aus").glob("*.xml"))
        self.assertEqual(len(dateien), 1)
        W.kumero_anfragen.clear()
        self.np.state.db.execute("UPDATE zustellung SET nicht_vor=0")
        return dateien[0]

    def test_kumero_einliefern_und_sidecar_1_1(self):
        np = self.mit_kumero()
        orig = b"%PDF RECHNUNG kumero 1"
        datei = self.sidecar_erzeugen(inhalt=orig)
        inhalt = datei.read_bytes()
        doc_id = int(datei.stem)
        # Messpunkt 8: Kennung 1.1, belegart, md5-original
        self.assertIn(b"SCAN-SIDECAR/1.1", inhalt)
        _, note = self.note(doc_id)
        self.assertEqual(note["belegart"], "rechnung")
        self.assertEqual(note["md5-original"], hashlib.md5(orig).hexdigest())
        self.assertIn("Scan", self.tagnamen(doc_id))                      # Tag "noch zu buchen"
        # Messpunkt 1: PUT mit vier Kopfzeilen, Koerper Byte fuer Byte, Schluessel = SHA-256
        W.kumero_modus = "ok"
        self.assertEqual(np.sidecars_zustellen(), 1)
        methode, pfad, kopf, body = W.kumero_anfragen[-1]
        self.assertEqual(methode, "PUT")
        self.assertEqual(pfad, f"/m/praxis/api/v1/belegeingang/sidecars/{doc_id}")
        self.assertEqual(body, inhalt)
        self.assertEqual(kopf["idempotency-key"], hashlib.sha256(inhalt).hexdigest())
        self.assertEqual(kopf["content-type"], "application/xml")
        self.assertEqual(kopf["accept"], "application/json")
        self.assertEqual(kopf["authorization"], "Bearer kmr_testtoken")
        # Messpunkt 2: nach 201 ist die Datei weg
        self.assertFalse(datei.exists())
        self.assertEqual(np.kumero.uebersicht()["wartend"], 0)

    def test_kumero_unveraendert_und_falscher_hash(self):
        np = self.mit_kumero()
        datei = self.sidecar_erzeugen()
        inhalt = datei.read_bytes()
        W.kumero_bekannt[int(datei.stem)] = hashlib.sha256(inhalt).hexdigest()
        W.kumero_modus = "ok"
        self.assertEqual(np.sidecars_zustellen(), 1)                      # 200 "unveraendert"
        self.assertFalse(datei.exists())
        datei.write_bytes(inhalt)
        W.kumero_modus = "falscher_hash"
        self.assertEqual(np.sidecars_zustellen(), 0)                      # abweichender Hash wie 5xx
        self.assertTrue(datei.exists())

    def test_kumero_voruebergehend_und_zeitueberschreitung(self):
        np = self.mit_kumero()
        datei = self.sidecar_erzeugen()
        schluessel = hashlib.sha256(datei.read_bytes()).hexdigest()
        # Messpunkt 3: nach 503 liegt die Datei noch da ...
        self.assertEqual(np.sidecars_zustellen(), 0)
        self.assertTrue(datei.exists())
        self.assertEqual(len(W.kumero_anfragen), 1)
        self.assertEqual(np.sidecars_zustellen(), 0)                      # Wartezeit: kein zweiter Versuch
        self.assertEqual(len(W.kumero_anfragen), 1)
        row = np.state.zustellung(datei.name)
        self.assertGreaterEqual(row["nicht_vor"] - time.time(), 250)      # zweiter Abstand: 5 min
        # ... ebenso nach einer Zeitueberschreitung
        np.state.db.execute("UPDATE zustellung SET nicht_vor=0")
        np.kumero.timeout = 0.5
        W.kumero_modus = "langsam"
        self.assertEqual(np.sidecars_zustellen(), 0)
        self.assertTrue(datei.exists())
        # ... und wird spaeter mit demselben Schluessel erneut gesendet
        np.state.db.execute("UPDATE zustellung SET nicht_vor=0")
        np.kumero.timeout = 30
        W.kumero_modus = "ok"
        time.sleep(1.6)                                                   # der langsame Aufruf laeuft im Server aus
        self.assertEqual(np.sidecars_zustellen(), 1)
        self.assertEqual({a[2]["idempotency-key"] for a in W.kumero_anfragen if a[0] == "PUT"}, {schluessel})
        self.assertFalse(datei.exists())

    def test_kumero_abgelehnt(self):
        np = self.mit_kumero()
        datei = self.sidecar_erzeugen()
        W.kumero_modus = "422"
        np.sidecars_zustellen()
        # Messpunkt 4: liegt in abgelehnt/ mit Begleitdatei, wird nicht erneut gesendet
        ziel = self.tmp / "aus" / "abgelehnt" / datei.name
        self.assertTrue(ziel.exists() and not datei.exists())
        info = json.loads(ziel.with_name(ziel.name + ".abgelehnt.json").read_text())
        self.assertEqual(sorted(info), ["code", "zeit"])
        self.assertEqual(info["code"], "sidecar.pflichtangabe")
        n = len(W.kumero_anfragen)
        W.kumero_modus = "ok"
        np.sidecars_zustellen()
        self.assertEqual(len(W.kumero_anfragen), n)
        self.assertEqual(np.kumero.uebersicht()["abgelehnt"], 1)

    def test_kumero_token_ungueltig_haelt_an(self):
        np = self.mit_kumero()
        datei = self.sidecar_erzeugen()
        W.kumero_modus = "401"
        np.sidecars_zustellen()
        n = len(W.kumero_anfragen)
        W.kumero_modus = "ok"
        np.sidecars_zustellen()
        # Messpunkt 5: nichts mehr gesendet, Sidecar bleibt, Belegverarbeitung laeuft weiter
        self.assertEqual(len(W.kumero_anfragen), n)
        self.assertTrue(datei.exists())
        self.scan("rechnungen", "k2.pdf", b"%PDF RECHNUNG kumero 2")
        np.ein_durchlauf()
        self.assertEqual(len(W.docs), 2)
        self.assertEqual(len(W.kumero_anfragen), n)
        # nach dem Ersetzen der Token-Datei geht es weiter
        (self.tmp / "kumero.token").write_text("kmr_testtoken\n\n")      # gleiches Token, Datei unveraendert im Inhalt
        np.sidecars_zustellen()
        self.assertEqual(len(W.kumero_anfragen), n)
        (self.tmp / "kumero.token").write_text("kmr_anderes\n")
        np.sidecars_zustellen()                                           # neues Token -> neuer Versuch (hier 401 vom Test-Server)
        self.assertGreater(len(W.kumero_anfragen), n)

    def test_kumero_429_wartet(self):
        np = self.mit_kumero()
        self.sidecar_erzeugen()
        W.kumero_modus = "429"
        t = time.time()
        np.sidecars_zustellen()
        # Messpunkt 6: fruehestens 30 Sekunden Pause
        self.assertGreaterEqual(np.kumero._pause_bis - t, 29.5)
        n = len(W.kumero_anfragen)
        W.kumero_modus = "ok"
        np.sidecars_zustellen()
        self.assertEqual(len(W.kumero_anfragen), n)

    def test_kumero_ohne_abschnitt_wie_bisher(self):
        # Messpunkt 7: ohne Abschnitt kumero bleibt der Sidecar im Abholordner, es gibt keinen Aufruf
        self.assertIsNone(self.np.kumero)
        self.scan("rechnungen", "k3.pdf", b"%PDF RECHNUNG ohne kumero")
        self.np.ein_durchlauf()
        self.assertEqual(len(list((self.tmp / "aus").glob("*.xml"))), 1)
        self.assertEqual(W.kumero_anfragen, [])

    def test_kumero_pruefen_und_keine_geheimnisse_im_log(self):
        np = self.mit_kumero()
        # Messpunkt 10: die vier Fehlerfaelle von `check`
        self.assertIsNone(np.kumero.pruefen())
        for modus, code in (("401", "KUMERO_TOKEN"), ("404", "KUMERO_ZIEL"), ("nicht_bereit", "KUMERO_ZIEL"),
                            ("503", "KUMERO_NICHT_ERREICHBAR")):
            W.kumero_status_modus = modus
            self.assertEqual(np.kumero.pruefen(), code)
        W.kumero_status_modus = "ok"
        (self.tmp / "kumero.token").write_text("")
        self.assertEqual(np.kumero.pruefen(), "KUMERO_TOKEN")
        tot = self.mit_kumero(url="http://127.0.0.1:9")                   # nichts lauscht dort
        self.assertEqual(tot.kumero.pruefen(), "KUMERO_NICHT_ERREICHBAR")
        # Befehle `check` und `status` zeigen kumero an, ohne Token
        import contextlib
        import io
        from nopaper.main import main as cli
        self.mit_kumero()
        aus = io.StringIO()
        with contextlib.redirect_stdout(aus):
            cli(["--config", str(self.tmp / "config.json"), "check"])
            W.kumero_status_modus = "503"
            cli(["--config", str(self.tmp / "config.json"), "check"])
            W.kumero_status_modus = "ok"
            cli(["--config", str(self.tmp / "config.json"), "status"])
        text = aus.getvalue()
        self.assertIn("ok   kumero", text)
        self.assertIn("WARN kumero: KUMERO_NICHT_ERREICHBAR", text)
        self.assertIn("kumero  wartend 0, abgelehnt 0", text)
        self.assertNotIn("kmr_testtoken", text)
        # Messpunkt 11a: http:// ausserhalb von localhost wird beim Start abgelehnt
        d = json.loads((self.tmp / "config.json").read_text())
        d["kumero"]["url"] = "http://kumero.example.org"
        (self.tmp / "config.json").write_text(json.dumps(d))
        with self.assertRaises(config.ConfigError) as e:
            config.load(self.tmp / "config.json")
        self.assertIn("KUMERO_URL", str(e.exception))
        # Messpunkt 9: in keinem Log stehen Token oder Belegangaben
        np = self.mit_kumero()
        self.sidecar_erzeugen()
        with self.assertLogs("nopaper", level="INFO") as lg:
            for modus in ("503", "429", "422"):
                W.kumero_modus = modus
                np.kumero._pause_bis = 0
                np.state.db.execute("UPDATE zustellung SET nicht_vor=0")
                np.sidecars_zustellen()
            self.scan("rechnungen", "k4.pdf", b"%PDF RECHNUNG kumero 4")
            W.kumero_modus = "401"
            np.kumero._pause_bis = 0
            np.ein_durchlauf()
        alles = "\n".join(lg.output)
        self.assertIn("kumero:", alles)
        for geheim in ("kmr_testtoken", "RE-2026-0815", "1234.56", "1.234,56", "Praxis Dr. Beispiel",
                       "DE89370400440532013000"):
            self.assertNotIn(geheim, alles)

    def test_kumero_ungueltiges_zertifikat(self):
        # Messpunkt 11b: ein nicht vertrauenswuerdiges Zertifikat wird abgelehnt (nichts wird gesendet)
        import ssl
        import subprocess
        if not shutil.which("openssl"):
            self.skipTest("openssl fehlt")
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                        "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost",
                        "-keyout", str(self.tmp / "k.pem"), "-out", str(self.tmp / "c.pem")],
                       check=True, capture_output=True)
        srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(str(self.tmp / "c.pem"), str(self.tmp / "k.pem"))
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            np = self.mit_kumero(url=f"https://localhost:{srv.server_port}")
            self.assertEqual(np.kumero.pruefen(), "KUMERO_NICHT_ERREICHBAR")
            datei = self.sidecar_erzeugen()
            W.kumero_modus = "ok"
            self.assertEqual(np.sidecars_zustellen(), 0)
            self.assertTrue(datei.exists())
            self.assertEqual(W.kumero_anfragen, [])
            # Dasselbe selbst signierte Zertifikat, ausdruecklich als vertrauenswuerdig hinterlegt: zulaessig
            np = self.mit_kumero(url=f"https://localhost:{srv.server_port}", ca=self.tmp / "c.pem")
            self.assertIsNone(np.kumero.pruefen())
            np.state.db.execute("UPDATE zustellung SET nicht_vor=0")   # Wartezeit des Fehlversuchs ueberspringen
            self.assertEqual(np.sidecars_zustellen(), 1)
            self.assertFalse(datei.exists())
            # ... aber nur fuer den Namen, auf den es ausgestellt ist
            np = self.mit_kumero(url=f"https://127.0.0.1:{srv.server_port}", ca=self.tmp / "c.pem")
            self.assertEqual(np.kumero.pruefen(), "KUMERO_NICHT_ERREICHBAR")
            # eine fehlende Zertifikatsdatei wird beim Start gemeldet
            with self.assertRaises(config.ConfigError):
                self.mit_kumero(ca=self.tmp / "gibt-es-nicht.pem")
        finally:
            srv.shutdown()

    def test_nachbearbeitung(self):
        W.docs[60] = {"id": 60, "content": "alt", "tags": [3, 5], "custom_fields": [{"field": 99, "value": "x"}],
                      "owner": 3, "original": b"%PDF RECHNUNG alt", "archive": b"A"}
        self.np.nachbearbeitung_abfragen()
        d = W.docs[60]
        self.assertEqual(self.tagnamen(60), {"Scan", "Rechnung", "Eingangsrechnung"})
        self.assertEqual(d["owner"], 3, "Besitzer vorhandener Dokumente bleibt unveraendert")
        self.assertIn({"field": 99, "value": "x"}, d["custom_fields"])  # fremde Felder bleiben
        _, note = self.note(60)
        self.assertEqual(note["quelle"], "nachbearbeitung")
        self.assertEqual(note["sha256-original"], hashlib.sha256(b"%PDF RECHNUNG alt").hexdigest())
        self.assertEqual(list((self.tmp / "arbeit").iterdir()), [])  # nichts liegen geblieben

    def test_ollama_weg(self):
        self.np.ki.url = "http://127.0.0.1:1"
        self.scan("rechnungen", "r.pdf", b"%PDF RECHNUNG 9")
        self.np.ein_durchlauf()
        row = self.np.state.db.execute("SELECT * FROM eingang").fetchone()
        self.assertEqual(row["status"], "wartend")
        self.assertEqual(row["fehler"], "VORUEBERGEHEND")
        self.assertEqual(W.uploads, 0)

    def test_konfig_pruefungen(self):
        d = json.loads((self.tmp / "config.json").read_text())
        for patch, msg in (({"paperless": {**d["paperless"], "url": "http://paperless.intern"}}, "https"),
                           ({"empfaenger": "Muster GmbH"}, "Platzhalter"),
                           ({"felder": {"unsinn": "X"}}, "Unbekannte"),
                           ({"modelle": {**d["modelle"], "ollama_url": "http://10.0.0.5:11434"}}, "localhost")):
            p = self.tmp / "c2.json"
            p.write_text(json.dumps({**d, **patch}))
            with self.assertRaises(config.ConfigError) as cm:
                config.load(p)
            self.assertIn(msg, str(cm.exception))

    def test_paperless_skript_ist_python(self):
        src = _paperless_skript(self.cfg, "nopaper")
        compile(src, "nopaper-setup.py", "exec")
        self.assertIn('"Zahlungsziel": "date"', src)
        self.assertIn("nopaper Rechnung", src)

    def test_glaettung_regeln(self):
        self.assertIsNone(llm.pruefe_glaettung("Betrag 12 345,00", "Betrag 12345,00"))
        self.assertEqual(llm.pruefe_glaettung("Betrag 12,00", "Betrag 13,00"), "ZIFFERN")
        self.assertEqual(llm.pruefe_glaettung("x" * 100, "x" * 50), "LAENGE")
        teile = llm._abschnitte(("Absatz " * 100 + "\n\n") * 10)
        self.assertTrue(all(len(t) <= llm.ABSCHNITT_ZEICHEN + 800 for t in teile))
        self.assertEqual("".join(teile), ("Absatz " * 100 + "\n\n") * 10)

    def test_testlauf_ohne_paperless(self):
        from nopaper import testlauf
        d = json.loads((self.tmp / "config.json").read_text())
        d["paperless"]["url"] = "https://paperless.beispiel.intern"   # Platzhalter erlaubt
        d["paperless"]["token_datei"] = "/gibt/es/nicht"
        d["empfaenger"] = "Muster GmbH"
        (self.tmp / "t.json").write_text(json.dumps(d))
        cfg = config.load(self.tmp / "t.json", read_token=False, testlauf=True)
        ein = cfg.test / "eingang"
        ein.mkdir(parents=True)
        (cfg.arbeit).mkdir(parents=True, exist_ok=True)
        (ein / "rechnung1.pdf").write_bytes(b"%PDF RECHNUNG t1")
        (ein / "rechnung1.soll.json").write_text(json.dumps({
            "art": "rechnung", "kategorie": "rechnung", "invoice_number": "RE-2026-0815", "issue_date": "2026-11-03",
            "seller": "Praxis Dr. Beispiel", "tax_number": "12/345/67890", "gross": "1234.56", "net": "1234.56",
            "tax": "0", "iban": ["DE89370400440532013000"]}))
        W.einordnung = "rechnung"
        ki = llm.Ollama(self.base)
        ziel = testlauf.testlauf(cfg, "auto", ki=ki, ocr_text=fake_ocr, seite_als_bild=lambda o, w: "x")
        erg = json.loads((ziel / "rechnung1" / "ergebnis.json").read_text())
        self.assertEqual(erg["pruefstatus"], "ok", erg)
        self.assertEqual(erg["rechnungsdaten"]["invoice_number"], "RE-2026-0815")
        self.assertTrue((ziel / "rechnung1" / "sidecar.xml").exists())
        self.assertIn("Rechnung Nr.", (ziel / "rechnung1" / "volltext.txt").read_text())
        self.assertIn("rechnung1.pdf;rechnung", (ziel / "zusammenfassung.csv").read_text())
        self.assertTrue(all(v in (True, None) for v in erg["soll_vergleich"].values()), erg["soll_vergleich"])
        self.assertIn("datum;1;1", (ziel / "trefferquote.csv").read_text())
        self.assertTrue((ein / "rechnung1.pdf").exists(), "Testeingang bleibt erhalten")
        self.assertEqual(W.uploads, 0)
        testlauf.aufraeumen(cfg)
        self.assertEqual(list(ein.iterdir()), [])


class RegelTest(unittest.TestCase):
    """Regeln aus den echten Testbelegen (Nov. 2026)."""

    def inv(self, **k):
        raw = copy.deepcopy(LLM_RECHNUNG)
        raw.update(k.pop("raw", {}))
        i = invoice.normalize(raw, "Test GmbH")
        i.update(k)
        return i

    def test_summen_aus_aufschluesselung(self):
        # Fall aus einem Testlauf, Bewirtungsbeleg: Modell setzte Netto=47,66 (7%-Zeile) und Steuer=14,29 (19%-Netto)
        raw = {"totals": {"net_total": 47.66, "tax_total": 14.29, "gross_total": 68.0, "prepaid": None,
                          "amount_due": None},
               "vat_breakdown": [{"rate": 7, "basis": 47.66, "tax": 3.34, "exemption_reason": None},
                                 {"rate": 19, "basis": 14.29, "tax": 2.71, "exemption_reason": None}]}
        i = self.inv(raw=raw)
        notes = invoice.complete_vat(i, "RECHNUNGSBETRAG 68,00")
        self.assertIn("SUMMEN_AUS_AUFSCHLUESSELUNG", notes)
        self.assertEqual((i["net"], i["tax"]), (Decimal("61.95"), Decimal("6.05")))

    def test_keine_reparatur_wenn_brutto_nicht_passt(self):
        raw = {"totals": {"net_total": 47.66, "tax_total": 14.29, "gross_total": 58.0, "prepaid": None,
                          "amount_due": None},
               "vat_breakdown": [{"rate": 7, "basis": 47.66, "tax": 3.34, "exemption_reason": None},
                                 {"rate": 19, "basis": 14.29, "tax": 2.71, "exemption_reason": None}]}
        i = self.inv(raw=raw)
        invoice.complete_vat(i)
        self.assertEqual(i["net"], Decimal("47.66"))
        self.assertIn("SUMME_NETTO_STEUER_BRUTTO", invoice.validate(i, "58,00 47,66 14,29"))

    def test_kleinbetrag_kassenbon(self):
        raw = {"kategorie": "kassenbon", "invoice_number": None,
               "seller": {**LLM_RECHNUNG["seller"], "tax_number": None, "vat_id": None},
               "totals": {"net_total": 8.19, "tax_total": 0.83, "gross_total": 9.02, "prepaid": 9.02,
                          "amount_due": 0},
               "vat_breakdown": [{"rate": 7, "basis": 6.10, "tax": 0.43, "exemption_reason": None},
                                 {"rate": 19, "basis": 2.09, "tax": 0.40, "exemption_reason": None}],
               "payment": {"iban": None, "bic": None, "reference": None}}
        i = invoice.normalize({**copy.deepcopy(LLM_RECHNUNG), **raw}, None)
        text = "Praxis Dr. Beispiel\nZU ZAHLEN 9,02 EUR\nDatum 03.11.26"
        v = invoice.validate(i, text)
        self.assertNotIn("FEHLT_RECHNUNGSNUMMER", v)
        self.assertNotIn("FEHLT_STEUERNUMMER_UND_USTID", v)
        self.assertNotIn("FEHLT_RECHNUNGSEMPFAENGER", v)
        self.assertEqual(v, [], v)

    def test_versicherungsteuer_ist_keine_ust(self):
        raw = {"kategorie": "versicherung",
               "totals": {"net_total": 550.60, "tax_total": 104.61, "gross_total": 655.21, "prepaid": None,
                          "amount_due": None},
               "vat_breakdown": [{"rate": 19, "basis": 550.60, "tax": 104.61, "exemption_reason": None}]}
        i = self.inv(raw=raw)
        v = invoice.validate(i, "Jahresbeitrag inkl. 19 % Versicherungsteuer von 104,61 EUR 655,21")
        self.assertIn("VERSICHERUNGSTEUER_ALS_UST", v)

    def test_ocr_verwechslung_in_betrag(self):
        i = self.inv(raw={"totals": {"net_total": 61.95, "tax_total": 6.05, "gross_total": 68.0, "prepaid": None,
                                     "amount_due": None}})
        self.assertNotIn("BRUTTO_NICHT_IM_TEXT", invoice.validate(i, "RECHNUNGSBETRAG 68,DO"))
        self.assertIn("BRUTTO_NICHT_IM_TEXT", invoice.validate(i, "RECHNUNGSBETRAG 63,00"))

    def test_datum_mehrdeutig_und_verankert(self):
        d = dt.date(2026, 8, 3)
        self.assertTrue(invoice.datum_mehrdeutig(d, "Date Added: 08/03/2026"))
        self.assertFalse(invoice.datum_mehrdeutig(d, "Datum 03.08.2026"))
        self.assertTrue(invoice.datum_im_text(dt.date(2026, 7, 17), "Datum 17.07.26 14:22 Uhr"))
        self.assertTrue(invoice.datum_im_text(dt.date(2026, 5, 28), "vom 28. 05.2026"))
        self.assertFalse(invoice.datum_im_text(dt.date(2026, 1, 16), "Beleg 15.01.2026"))

    def test_rechnungssteller_unscharf(self):
        self.assertGreaterEqual(invoice.name_im_text("Trattoria Esempio", "Trattorla Esernpio\nMusterchaussee"), 0.6)
        self.assertLess(invoice.name_im_text("Deutsche Telekom AG", "Trattoria Esempio Musterchaussee"), 0.6)

    def test_auslaendischer_rechnungssteller(self):
        raw = {"currency": "USD", "seller": {**LLM_RECHNUNG["seller"], "country_code": "US", "vat_id": "81-1807778",
                                             "tax_number": None}}
        v = invoice.validate(self.inv(raw=raw), "")
        self.assertNotIn("USTID_FORMAT", v)
        self.assertNotIn("FEHLT_STEUERNUMMER_UND_USTID", v)

    def test_kategorien(self):
        self.assertTrue(invoice.ist_rechnungsartig("bewirtung"))
        self.assertFalse(invoice.ist_rechnungsartig("mahnung"))
        i = invoice.normalize({"kategorie": "mahnung"}, None)
        self.assertEqual(i["document_type"], "keine_rechnung")

    def test_muellfilter_handschrift(self):
        from nopaper import ocr
        seite1 = "Trattorla Esempıo\nMusterchaussee 2\n12345 Musterdorf/Nordbahn\nRECHNUNGSBETRAG 68,00"
        seite2 = "Bewirtete Person(en)\nu\nkei r ve ie ir ı Kl I\nTe u Hi\nar"
        text, n = ocr.fuer_ki(seite1 + "\f" + seite2)
        self.assertIn("Musterdorf", text)
        self.assertNotIn("Te u Hi", text)
        self.assertEqual(n, 5)  # Seite 2 ueberwiegend unlesbar -> ganz entfernt

    def test_versicherung_nullsatz(self):
        raw = {"kategorie": "versicherung",
               "totals": {"net_total": 550.60, "tax_total": 104.61, "gross_total": 655.21, "prepaid": None,
                          "amount_due": None},
               "vat_breakdown": [{"rate": 0, "basis": 655.21, "tax": 104.61, "exemption_reason": "x"}]}
        i = self.inv(raw=raw)
        text = "inkl. 19 % Versicherungsteuer von 104,61 € 655,21 umsatzsteuerfrei nach § 4 Nr. 10 a UStG"
        notes = invoice.complete_vat(i, text)
        self.assertIn("VERSICHERUNGSTEUER_KORRIGIERT", notes)
        self.assertEqual((i["net"], i["tax"]), (Decimal("655.21"), Decimal("0")))
        self.assertNotIn("VERSICHERUNGSTEUER_ALS_UST", invoice.validate(i, text))

    def test_versicherungsteuer_mit_19_prozent(self):
        # Fall aus einem Testlauf: Versicherung mit rate 19 / tax 104,61 (Versicherungsteuer) ausgelesen
        raw = {"kategorie": "versicherung",
               "totals": {"net_total": 550.5, "tax_total": 104.61, "gross_total": 655.21, "prepaid": None,
                          "amount_due": None},
               "vat_breakdown": [{"rate": 19, "basis": 550.5, "tax": 104.61, "exemption_reason": None}]}
        i = self.inv(raw=raw)
        text = "Jahresbeitrag inkl. 19 % Versicherungsteuer von 104,61 € 655,21 €"
        self.assertIn("VERSICHERUNGSTEUER_KORRIGIERT", invoice.complete_vat(i, text))
        v = invoice.validate(i, text)
        for c in ("VERSICHERUNGSTEUER_ALS_UST", "SUMME_NETTO_STEUER_BRUTTO", "UST_BEFREIUNG_OHNE_GRUND"):
            self.assertNotIn(c, v)

    def test_brutto_ist_zwischensumme(self):
        # Fall aus einem Testlauf: mehrseitige Rechnung, nur Seiten 1-2 gelesen -> Uebertrag als Brutto
        text = "Übertrag 1.199,90\nNettobetrag 1.239,32\nUmsatzsteuer 19,00 % 235,47\nEndbetrag 1.474,79"
        self.assertTrue(invoice.brutto_ist_zwischensumme(Decimal("1199.90"), text))
        self.assertFalse(invoice.brutto_ist_zwischensumme(Decimal("1474.79"), text))
        self.assertFalse(invoice.brutto_ist_zwischensumme(Decimal("344.00"),
                                                          "Zwischensumme 344,00 €\nSumme Positionen: 344,00 €"))
        from nopaper import ocr
        self.assertEqual(ocr.seiten_auswahl(6, 3), [1, 2, 6])
        self.assertEqual(ocr.seiten_auswahl(2, 3), [1, 2])

    def test_langer_text_fuer_das_modell(self):
        from nopaper import llm
        # Budget waechst mit modelle.kontext, faellt aber nie unter den bisherigen Wert
        self.assertEqual(llm.text_budget(8192), 12000)
        self.assertEqual(llm.text_budget(4096), 12000)
        self.assertEqual(llm.text_budget(32768), 48000)
        self.assertEqual(llm.text_budget(65536), 96000)
        # Seitenbilder: Standard (3 Seiten bei 8k) bleibt, grosser Kontext erlaubt die ganze Rechnung
        self.assertGreaterEqual(llm.seiten_budget(8192), 3)
        self.assertEqual(llm.seiten_budget(4096), llm.seiten_budget(16384))
        self.assertEqual(llm.seiten_budget(65536), 18)
        # passt der Text, geht er unveraendert und vollstaendig an das Modell
        kurz = "Kopf\n" + "Position 1,00\n" * 500 + "Endbetrag 500,00\n"
        self.assertEqual(llm.text_fuer_modell(kurz, 12000), kurz)
        # mehrseitige Rechnung: mit grossem Kontext vollstaendig, Anfang bis Ende
        lang = "RECHNUNG Nr. 4711\n" + "Artikel 0815 Muster 1,00 A\n" * 2000 + "Endbetrag 2.000,00 EUR\n"
        self.assertGreater(len(lang), 48000)
        self.assertEqual(llm.text_fuer_modell(lang, llm.text_budget(65536)), lang)
        # passt er nicht: Anfang UND Ende, nie nur der Anfang
        t = llm.text_fuer_modell(lang, 12000)
        self.assertLessEqual(len(t), 12000)
        self.assertTrue(t.startswith("RECHNUNG Nr. 4711\n"))
        self.assertTrue(t.endswith("Endbetrag 2.000,00 EUR\n"))
        self.assertEqual(t.count(llm.AUSLASSUNG), 1)
        for teil in t.split(llm.AUSLASSUNG):      # an Zeilengrenzen geschnitten, keine halben Zeilen
            for zeile in teil.strip("\n").split("\n")[1:-1]:
                self.assertEqual(zeile, "Artikel 0815 Muster 1,00 A")
        self.assertTrue(t.split(llm.AUSLASSUNG)[0].endswith("1,00 A"))
        self.assertTrue(t.split(llm.AUSLASSUNG)[1].startswith("Artikel"))

    def test_analyse_bekommt_den_ganzen_text(self):
        from nopaper import llm
        gesehen = []

        class Mitschnitt(llm.Ollama):
            def chat_json(self, model, system, user, schema, images=None):
                gesehen.append(user)
                return {}

        lang = "RECHNUNG Nr. 4711\n" + "Artikel 0815 Muster 1,00 A\n" * 2000 + "Endbetrag 2.000,00 EUR\n"
        llm.analysieren(Mitschnitt("http://127.0.0.1:1", num_ctx=65536), "m", lang)
        self.assertIn(lang, gesehen[-1])                       # grosser Kontext: alles
        llm.analysieren(Mitschnitt("http://127.0.0.1:1", num_ctx=8192), "m", lang)
        self.assertIn("RECHNUNG Nr. 4711", gesehen[-1])        # kleiner Kontext: Anfang und Ende
        self.assertIn("Endbetrag 2.000,00 EUR", gesehen[-1])
        self.assertLess(len(gesehen[-1]), 12100)

    def test_steuersatz_aus_betraegen(self):
        # abgeschnittener Supermarkt-Bon: Satz fehlt, Modell setzt 0 %
        raw = {"kategorie": "kassenbon", "totals": {"net_total": 5.60, "tax_total": 0.39, "gross_total": 5.99,
                                                    "prepaid": 5.99, "amount_due": 0},
               "vat_breakdown": [{"rate": 0, "basis": 5.60, "tax": 0.39, "exemption_reason": "Kassenbon"}]}
        i = self.inv(raw=raw)
        self.assertIn("UST_SATZ_BERECHNET", invoice.complete_vat(i, "5,60 0,39 5,99"))
        self.assertEqual(i["vat"][0]["rate"], Decimal("7"))

    def test_steuernummer_formate(self):
        i = self.inv(raw={"invoice_number": "12/345/006", "seller": {**LLM_RECHNUNG["seller"],
                                                                     "tax_number": "IK330313304"}})
        notes = invoice.complete_vat(i, "")
        self.assertIsNone(i["invoice_number"])
        self.assertIsNone(i["seller"]["tax_number"])
        self.assertIn("RECHNUNGSNUMMER_VERWORFEN", notes)
        self.assertIn("STEUERNUMMER_VERWORFEN", notes)
        self.assertTrue(invoice.steuernummer_ok("346/5871/1049"))
        self.assertTrue(invoice.steuernummer_ok("5205/577711510"))

    def test_rueckgeld_und_zahlungsvermerk(self):
        i = self.inv(raw={"kategorie": "kassenbon", "totals": {**LLM_RECHNUNG["totals"], "prepaid": 2000.0,
                                                               "amount_due": 525.21}})
        notes = invoice.complete_vat(i, "gegeben 2000,00 EUR\nRückgabe 525,21 EUR")
        self.assertIn("RUECKGELD_ERKANNT", notes)
        self.assertEqual(i["prepaid"], i["gross"])
        self.assertEqual(i["due"], 0)
        i = self.inv(raw={"kategorie": "kassenbon", "totals": {**LLM_RECHNUNG["totals"], "prepaid": None,
                                                               "amount_due": None}})
        self.assertIn("ZAHLUNG_AUS_TEXT", invoice.complete_vat(i, "Bezahlung VISA\nZahlung erfolgt"))
        self.assertEqual(i["due"], 0)
        i = self.inv(raw={"totals": {**LLM_RECHNUNG["totals"], "prepaid": None, "amount_due": None}})
        invoice.complete_vat(i, "Bitte ueberweisen Sie den Betrag. Zahlung erfolgt per Ueberweisung.")
        self.assertEqual(i["due"], i["gross"])  # Rechnung: kein Rueckschluss aus Woertern

    def test_belegnummer_im_steuernummernformat(self):
        i = self.inv(raw={"kategorie": "kassenbon", "invoice_number": "318/004/00007"})
        notes = invoice.complete_vat(i, "Bzleg-Nr. 318/004/00007 05.11.2026 14:14\nMUSTER AG   12/345/00678-1")
        self.assertEqual(i["invoice_number"], "318/004/00007")
        self.assertNotIn("RECHNUNGSNUMMER_VERWORFEN", notes)
        i = self.inv(raw={"invoice_number": "12/345/00678"})
        self.assertIn("RECHNUNGSNUMMER_VERWORFEN", invoice.complete_vat(i, "Steuer-Nr. 12/345/00678"))
        # Bildmodell liest 919, Texterkennung 319: Wert bleibt, Beleg geht in die Pruefung
        text = "Bzleg-Nr. 318/004/00007 05.11.2026 14:14\nMUSTER AG   12/345/00678-1"
        i = self.inv(raw={"kategorie": "kassenbon", "invoice_number": "918/004/00007"})
        notes = invoice.complete_vat(i, text)
        self.assertEqual(i["invoice_number"], "918/004/00007")
        self.assertIn("RECHNUNGSNUMMER_ABWEICHEND", notes)
        self.assertIn("RECHNUNGSNUMMER_NICHT_IM_TEXT", invoice.validate(i, text))
        i = self.inv(raw={"kategorie": "kassenbon", "invoice_number": "12/345/00679"})
        self.assertIn("RECHNUNGSNUMMER_VERWORFEN", invoice.complete_vat(i, "Steuer-Nr. 12/345/00678"))
        i = self.inv(raw={"kategorie": "kassenbon", "invoice_number": "918/004/00008"})  # zwei Ziffern anders
        self.assertIn("RECHNUNGSNUMMER_VERWORFEN", invoice.complete_vat(i, text))

    def test_skonto_faelligkeit_steuerklassen(self):
        raw = {"due_date": None, "payment_terms": {"due_days": 30, "skonto": [
            {"percent": 2, "days": 10, "until": None, "amount": None},
            {"percent": 5, "days": 3, "until": None, "amount": None}]}}
        i = self.inv(raw=raw)
        text = "Zahlbar innerhalb 30 Tagen netto, bei Zahlung innerhalb 10 Tagen 2 % Skonto."
        notes = invoice.complete_vat(i, text)
        self.assertIn("FAELLIGKEIT_BERECHNET", notes)
        self.assertEqual(i["due_date"], i["issue_date"] + dt.timedelta(days=30))
        self.assertIn("SKONTO_VERWORFEN", notes)  # 5 % steht nicht im Text
        self.assertEqual(invoice.skonto_text(i), f"2:{(i['issue_date'] + dt.timedelta(days=10)).isoformat()}:")
        self.assertIn(b"#SKONTO#TAGE=10#PROZENT=2.00#", invoice.build_cii(i, {}))
        i = self.inv(raw=raw)
        invoice.complete_vat(i, "Zahlbar sofort ohne Abzug.")
        self.assertIsNone(i["due_date"])
        self.assertIsNone(invoice.skonto_text(i))
        i = self.inv(raw={"vat_breakdown": [{"rate": 7, "basis": 5.59, "tax": 0.39, "exemption_reason": None},
                                            {"rate": 19, "basis": 8.01, "tax": 1.52, "exemption_reason": None}]})
        self.assertEqual(invoice.steuer_nach_klasse(i), (Decimal("1.52"), Decimal("0.39")))
        i = self.inv(raw={"vat_breakdown": [{"rate": 7, "basis": 5.59, "tax": 0.39, "exemption_reason": None}]})
        self.assertEqual(invoice.steuer_nach_klasse(i), (None, Decimal("0.39")))

    def test_aufgabenformate_paperless(self):
        from nopaper.paperless import Paperless, PaperlessFehler
        lesen = Paperless._task_lesen
        self.assertEqual(lesen({"status": "SUCCESS", "related_document": "42", "result": "ok"})[:2], ("SUCCESS", 42))
        neu = {"task_id": "x", "task_type": "consume_file", "status": "success", "result_data": None,
               "related_document_ids": [4711], "input_data": {}}
        self.assertEqual(lesen(neu)[:2], ("SUCCESS", 4711))
        self.assertEqual(lesen({"status": "success", "related_document_ids": [],
                                "result_data": {"document_id": 9}})[:2], ("SUCCESS", 9))
        self.assertEqual(lesen({"status": "failure", "result_data": {"error": "is a duplicate of #3"}})[0], "FAILURE")
        self.assertIn("duplicate", lesen({"status": "failed", "result_data": {"error": "is a duplicate"}})[2])
        # paperless ignoriert den Filter und liefert eine fremde, fertige Aufgabe: nicht verwechseln, klar abbrechen
        pl = Paperless("http://127.0.0.1:1", "t")
        pl.json = lambda *a, **k: [{"task_id": "fremd", "status": "success", "related_document_ids": []}]
        with self.assertRaises(PaperlessFehler) as e:
            pl.warte_auf_dokument("meine", timeout_s=5, intervall=0.01, ohne_antwort_s=0.05)
        self.assertEqual(e.exception.code, "PAPERLESS_AUFGABE_NICHT_GEFUNDEN")
        pl.json = lambda *a, **k: {"results": [{"task_id": "meine", "status": "success", "related_document_ids": [7]}]}
        self.assertEqual(pl.warte_auf_dokument("meine", timeout_s=5, intervall=0.01), 7)

    def test_pruefsumme_md5_oder_sha256(self):
        from nopaper.pipeline import pruefsumme_passt
        d = b"%PDF original"
        sha, md5 = hashlib.sha256(d).hexdigest(), hashlib.md5(d).hexdigest()
        self.assertTrue(pruefsumme_passt(md5, sha, md5))            # paperless 2.x
        self.assertTrue(pruefsumme_passt(sha.upper(), sha, md5))    # paperless 3.x
        self.assertTrue(pruefsumme_passt(None, sha, md5))
        self.assertFalse(pruefsumme_passt(hashlib.sha256(b"x").hexdigest(), sha, md5))
        self.assertFalse(pruefsumme_passt(hashlib.md5(b"x").hexdigest(), sha, md5))

    def test_titel(self):
        i = self.inv()
        t = invoice.titel(i, "{datum} {lieferant} {betrag}")
        self.assertEqual(t, f"{i['issue_date'].isoformat()} {i['seller']['name']} 1234,56 EUR")
        self.assertIsNone(invoice.titel(i, ""))
        i["issue_date"] = None
        self.assertEqual(invoice.titel(i, "{datum} {lieferant} {betrag}"), f"{i['seller']['name']} 1234,56 EUR")
        i["seller"]["name"] = None
        self.assertIsNone(invoice.titel(i, "{datum} {lieferant} {betrag}"))
        self.assertIsNone(invoice.titel(self.inv(), "{unbekannt}"))

    def test_verwendungszweck(self):
        text = "* K A S S E N Z E I C H E N *\n   1234500067890\nunter Angabe des Kassenzeichens zu überweisen"
        i = self.inv(raw={"kategorie": "behoerde", "payment": {"iban": None, "bic": None, "reference": None}})
        self.assertIn("VERWENDUNGSZWECK_AUS_TEXT", invoice.complete_vat(i, text))
        self.assertEqual(i["payment_reference"], "1234500067890")
        i = self.inv(raw={"payment": {"iban": None, "bic": None, "reference": "1234500067897"}})
        notes = invoice.complete_vat(i, text)                      # Modell hat eine Ziffer falsch
        self.assertIn("VERWENDUNGSZWECK_VERWORFEN", notes)
        self.assertEqual(i["payment_reference"], "1234500067890")
        i = self.inv(raw={"payment": {"iban": None, "bic": None, "reference": "RE 2026-0815"}})
        invoice.complete_vat(i, "Bitte angeben: RE2026-0815")
        self.assertEqual(i["payment_reference"], "RE 2026-0815")
        i = self.inv(raw={"payment": {"iban": None, "bic": None, "reference": "erfunden 4711"}})
        invoice.complete_vat(i, "kein Zahlungshinweis")
        self.assertIsNone(i["payment_reference"])
        self.assertIsNone(invoice.kassenzeichen_im_text("Kassenzeichen 12345678\nKassenzeichen 87654321"))

    def test_rechnungsnummer_bezeichnung(self):
        text = "Rechnungsnummer   1000051858\n   Kundennummer:   39386258\nRe-Nr.: RE2026-127426 (Auftrag-Nr.: AU199491)"
        self.assertTrue(invoice.nummer_bezeichnung_falsch("39386258", text))
        self.assertTrue(invoice.nummer_bezeichnung_falsch("AU199491", text))
        self.assertFalse(invoice.nummer_bezeichnung_falsch("1000051858", text))
        self.assertFalse(invoice.nummer_bezeichnung_falsch("40312", "RECHNUNG NR. 40312 vom 14.03.2026"))

    def test_zeilenweise_glaettung(self):
        roh = "RECHNUNG NR. 40312 vom 14. 03.2026\nBAR EURO 568, DO\nTrattorla Esemplo"
        neu = "RECHNUNG NR. 40312 vom 14.03.2026\nBAR EURO 68,00\nTrattoria Esempio"
        text, n = llm._zeilenweise_retten(roh, neu)
        self.assertEqual(text, "RECHNUNG NR. 40312 vom 14.03.2026\nBAR EURO 568, DO\nTrattoria Esempio")
        self.assertEqual(n, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
