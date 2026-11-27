#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Offline-Tests der Kalender-Erweiterung. Postfach, Kalender und Sprachmodell sind Stellvertreter.
Es werden ausschliesslich erfundene Daten verwendet."""
import contextlib
import datetime as dt
import json
import os
import sys
import tempfile
import time
import types
import unittest
from email import policy
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
ZONE = ZoneInfo("Europe/Berlin")
TMP = Path(tempfile.mkdtemp(prefix="johm-test-"))
MODEL = {"reply": None, "fail": False, "calls": 0}


class Antwort:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self, *args):
        return json.dumps({"done": True, "message": {"content": json.dumps(MODEL["reply"])}}).encode()


def modell(request, timeout=0):
    MODEL["calls"] += 1
    if MODEL["fail"]:
        raise OSError("nicht erreichbar")
    return Antwort()


def konfiguration():
    cfg = json.loads((ROOT / "quelle" / "calendar.example.json").read_text(encoding="utf-8"))
    cfg["mode"] = "live"
    cfg["hosts"] = ["mail.example.org"]
    cfg["mailboxes"] = ["praxis@example.org"]
    return cfg


CFG = konfiguration()
(TMP / "nc-config").write_text(json.dumps(CFG), encoding="utf-8")
os.environ["CREDENTIALS_DIRECTORY"] = str(TMP)
sys.path[:0] = [str(ROOT / "quelle"), str(ROOT / "tests" / "fake")]
# Stellvertreter für die Teile des Mailarbeitsplatzes, die nicht zu dieser Erweiterung gehören.
sys.modules["reading"] = types.SimpleNamespace(query=None, analyse=None)
sys.modules["boxes"] = types.SimpleNamespace(
    state=lambda: TMP, credential=lambda name: "geheim",
    current=types.SimpleNamespace(get=lambda: {"config": {"username": "praxis@example.org"}}))
sys.modules["mail"] = types.SimpleNamespace(
    HTTP=types.SimpleNamespace(open=modell), API="http://modell.invalid", MODEL="test",
    FAX_ADDRESS="fax@example.org", mailbody=lambda message: (message.get_body(("plain",)) or message).get_content())
sys.modules["outbound"] = types.SimpleNamespace(special=lambda *args: "Gesendet")
import appointments as A  # noqa: E402
import caldav_slots as C  # noqa: E402
import wording  # noqa: E402

JETZT = dt.datetime(2026, 11, 27, 9, 0, tzinfo=ZONE)


class FesteZeit(dt.datetime):
    @classmethod
    def now(cls, tz=None):
        return JETZT


def stempel(*teile):
    return int(dt.datetime(*teile, tzinfo=ZONE).timestamp())


def platz(titel, *teile, kalender="A"):
    start = stempel(*teile)
    return dict(id=f"{titel}{kalender}{start}", cal="termine_" + kalender.lower(), label=kalender, start=start, end=start + 900, title=titel)


class Suche(unittest.TestCase):
    """Welche Plätze werden vorgeschlagen, und in welcher Reihenfolge."""

    def setUp(self):
        self.plaetze, self.merkmale, self.schwanger, self.wuensche = [], [], "none", []
        self.alt_dt, A.dt = A.dt, types.SimpleNamespace(datetime=FesteZeit, timedelta=dt.timedelta, date=dt.date, time=dt.time)
        self.alt = {name: getattr(A, name) for name in ("client", "wishes", "reading", "wording", "offered_before", "time")}
        A.client = lambda: types.SimpleNamespace(
            zone=ZONE, cfg=CFG, agenda=lambda a, b: [],
            scan=lambda a, b: sorted((dict(p) for p in self.plaetze if a <= p["start"] < b), key=lambda p: p["start"]))
        A.wishes = lambda *args: [dict(w) for w in self.wuensche]
        A.reading = types.SimpleNamespace(analyse=lambda ctx: {
            "pregnancy": self.schwanger, "translation": "", "lang": "de", "appointment": "new", "hits": self.merkmale})
        A.wording = types.SimpleNamespace(
            FALLBACK=set(), draft_phrases=lambda lang: {"deadline": "{deadline}", "intro": "Guten Tag,"},
            salutation=lambda *args: "Guten Tag,", addressed=lambda text, *args: text)
        A.offered_before = lambda ctx: set()
        A.time = types.SimpleNamespace(time=JETZT.timestamp, monotonic=time.monotonic)

    def tearDown(self):
        A.dt = self.alt_dt
        for name, wert in self.alt.items():
            setattr(A, name, wert)

    def suche(self, vorgabe=None, tage=90):
        ctx = {"subject": "Termin", "original": "…", "sender": "Erika Beispiel <e@example.org>", "to": "Erika Beispiel <e@example.org>"}
        return A.search(ctx, tage, vorgabe)

    def wunsch(self, **werte):
        return dict(A.blank_wish("Absenderin"), kind="vorsorge", **werte)

    def labels(self, ergebnis):
        return [option["label"] for option in ergebnis["wishes"][0]["options"]]

    def test_wunschfenster_zuerst_dann_ausweichtermine(self):
        self.wuensche = [self.wunsch(weekdays=[1], **{"from": "08:30", "to": "10:00"})]
        self.plaetze = [platz("termin:", 2026, 11, 30, 9, 0), platz("termin:", 2026, 11, 30, 11, 0), platz("termin:", 2026, 12, 1, 9, 0)]
        wunsch = self.suche()["wishes"][0]
        self.assertEqual([o["time"] for o in wunsch["options"]], ["09:00"])
        self.assertFalse(wunsch["fallback"])
        self.plaetze = self.plaetze[1:]
        wunsch = self.suche()["wishes"][0]
        self.assertTrue(wunsch["fallback"])
        # Gleiche Tageszeit an einem anderen Tag steht vor dem Wunschtag zur falschen Zeit.
        self.assertEqual([o["day"] for o in wunsch["options"]], ["Di 01.12.", "Mo 30.11."])

    def test_bevorzugter_wochentag_steht_vorn_andere_tage_folgen(self):
        self.wuensche = [self.wunsch(weekdays=[4], weekdays_soft=True)]
        self.plaetze = [platz("termin:", 2026, 12, 1, 9, 0), platz("termin:", 2026, 12, 3, 9, 0), platz("termin:", 2026, 12, 4, 9, 0)]
        wunsch = self.suche()["wishes"][0]
        self.assertEqual([o["day"] for o in wunsch["options"]], ["Do 03.12.", "Di 01.12.", "Fr 04.12."])
        self.assertFalse(wunsch["fallback"])

    def test_arzt_richtet_sich_nach_dem_raster_nicht_nach_dem_kalender(self):
        self.wuensche = [self.wunsch(doctors=["Dr. Muster"])]
        self.plaetze = [platz("termin:", 2026, 12, 1, 9, 0), platz("stermin:", 2026, 12, 1, 9, 0)]
        wunsch = self.suche()["wishes"][0]
        self.assertEqual([(o["time"], o["doctor"]) for o in wunsch["options"]], [("09:00", "Dr. Muster")])

    def test_schwangerschaft_sperrt_jeden_vorschlag_auch_bei_suche_der_mfa(self):
        self.schwanger = "reported"
        self.wuensche = [self.wunsch()]
        self.plaetze = [platz("termin:", 2026, 12, 1, 9, 0)]
        self.assertEqual(self.suche()["wishes"], [])
        self.assertEqual(self.suche({"doctors": ["Dr. Beispiel"]})["wishes"], [])

    def test_suche_der_mfa_ersetzt_den_wunsch_aus_der_mail(self):
        self.wuensche = [dict(self.wunsch(weekdays=[4]), kind="manuell", action="verschieben")]
        self.plaetze = [platz("termin:", 2026, 12, 1, 9, 0), platz("termin:", 2026, 12, 3, 9, 0), platz("stermin:", 2026, 12, 2, 9, 0)]
        self.assertEqual(self.suche()["wishes"][0]["options"], [])
        wunsch = self.suche({"doctors": ["Dr. Beispiel"]})["wishes"][0]
        self.assertEqual([o["day"] for o in wunsch["options"]], ["Di 01.12.", "Do 03.12."])
        self.assertTrue(wunsch["hint"].startswith("Vorgabe der MFA"))

    def test_zeitraum_der_mfa_gilt_auch_jenseits_des_suchzeitraums(self):
        self.plaetze = [platz("termin:", 2027, 2, 4, 8, 0), platz("termin:", 2027, 2, 4, 8, 15), platz("termin:", 2027, 2, 5, 8, 0)]
        wunsch = self.suche({"date_from": "2027-02-04", "date_to": "2027-02-04"}, tage=30)["wishes"][0]
        self.assertEqual([o["time"] for o in wunsch["options"]], ["08:00", "08:15"])

    def test_dringend_waehlt_die_akutplaetze_mit_eigener_frist(self):
        self.merkmale = [{"id": "urgent", "state": "reported", "label": "Dringend", "evidence": "starke Schmerzen"}]
        self.wuensche = []
        self.plaetze = [platz("akut:", 2026, 11, 27, 10, 30), platz("akut:", 2026, 11, 27, 13, 0), platz("termin:", 2026, 11, 30, 9, 0), platz("akut:", 2026, 11, 30, 13, 0)]
        ergebnis = self.suche()
        wunsch = ergebnis["wishes"][0]
        self.assertEqual(wunsch["label"], "Akuttermin")
        # Der Platz um 10:30 beginnt vor dem Ende der Bestätigungsfrist von drei Stunden.
        self.assertEqual([(o["day"], o["time"]) for o in wunsch["options"]], [("Fr 27.11.", "13:00"), ("Mo 30.11.", "13:00")])
        self.assertEqual(ergebnis["deadline"], stempel(2026, 11, 27, 12, 0))

    def test_farbe_aus_dem_kalender_und_akutfarbe(self):
        self.merkmale = [{"id": "urgent", "state": "reported", "label": "Dringend", "evidence": "starke Schmerzen"}]
        self.plaetze = [dict(platz("akut:", 2026, 11, 30, 13, 0), color="#cc0000"), dict(platz("termin:", 2026, 11, 30, 9, 0), color="green")]
        ergebnis = self.suche()
        self.assertEqual((ergebnis["urgent"], ergebnis["urgentColor"]), (True, "#cc0000"))
        self.assertEqual([o["color"] for o in ergebnis["wishes"][0]["options"]], ["#cc0000"])
        self.assertEqual((C.tint("#1a2B3c"), C.tint("red"), C.tint("red;background:url(x)"), C.tint("<b>")), ("#1a2B3c", "red", "", ""))

    # --- Bisherigen Termin finden

    def alter_termin(self, eintraege, **wunsch):
        zeilen = [dict(start=stempel(*teile), end=stempel(*teile) + 900, title=titel, calendar="A", raster=True, free=titel.endswith(":"), path="termine_a", href=f"/{nr}.ics")
                  for nr, (titel, teile) in enumerate(eintraege)]
        abfragen = []
        dav = types.SimpleNamespace(zone=ZONE, agenda=lambda a, b: abfragen.append((a, b)) or [dict(z) for z in zeilen if a <= z["start"] < b])
        A._old.clear()
        gefunden = A.find_old(dav, dict(A.blank_wish("Absenderin"), action="verschieben", **wunsch), "Erika Beispiel <e@example.org>")
        return [(z["title"], z["sure"]) for z in gefunden], abfragen

    def test_alter_termin_ohne_datum_wird_ueber_sechs_monate_gesucht(self):
        gefunden, abfragen = self.alter_termin([("Termin: Erika Beispiel", (2027, 3, 9, 10, 0)), ("Termin: Max Muster", (2026, 12, 1, 9, 0)), ("Termin:", (2026, 12, 1, 9, 15))])
        self.assertEqual(gefunden, [("Termin: Erika Beispiel", True)])
        self.assertEqual(len(abfragen), 3)
        self.assertGreaterEqual(abfragen[-1][1] - abfragen[0][0], 180 * 86400)

    def test_alter_termin_mehrere_treffer_keiner_vorgewaehlt(self):
        gefunden, _ = self.alter_termin([("Termin: Erika Beispiel", (2026, 12, 9, 10, 0)), ("Termin: Beispiel, Erika", (2027, 1, 12, 8, 0))])
        self.assertEqual(gefunden, [("Termin: Erika Beispiel", False), ("Termin: Beispiel, Erika", False)])

    def test_alter_termin_nur_nachname_ist_nur_ein_hinweis(self):
        gefunden, _ = self.alter_termin([("Termin: Frau Beispiel", (2026, 12, 9, 10, 0)), ("Termin: Beispielmann", (2026, 12, 10, 10, 0))])
        self.assertEqual(gefunden, [("Termin: Frau Beispiel", False)])

    def test_alter_termin_mit_datum_und_falschem_tag_sucht_weiter(self):
        eintraege = [("Termin: Erika Beispiel", (2026, 12, 9, 10, 0)), ("Termin: Max Muster", (2026, 12, 8, 10, 0))]
        self.assertEqual(self.alter_termin(eintraege, old_date="2026-12-09")[0], [("Termin: Erika Beispiel", True)])
        self.assertEqual(self.alter_termin(eintraege, old_date="2026-12-08")[0], [("Termin: Erika Beispiel", True)])
        self.assertEqual(self.alter_termin(eintraege[1:], old_date="2026-12-08", old_time="10:00")[0], [("Termin: Max Muster", False)])

    def test_ungueltige_vorgaben_werden_verworfen(self):
        sauber = A.clean_override({"doctors": ["Dr. Beispiel", "Dr. Niemand"], "weekdays": [1, 9, True], "from": "25:00", "to": "10:00",
                                   "date_from": "2027-13-01", "date_to": "2027-01-31", "kind": "gibt-es-nicht"}, CFG)
        self.assertEqual(sauber["doctors"], ["Dr. Beispiel"])
        self.assertEqual(sauber["weekdays"], [1])
        self.assertEqual((sauber["from"], sauber["date_from"], sauber["kind"]), ("", "", ""))


class Kalender(unittest.TestCase):
    """Schreibzugriffe auf Kalendereinträge, mit einem Stellvertreter für die Bibliothek icalendar."""

    def setUp(self):
        self.dav = C.DAV(CFG, "geheim")
        self.ablage = {}
        self.url = self.dav.collection("termine_a") + "a.ics"
        self.href = "/remote.php/dav/calendars/praxis/termine_a/a.ics"

        def anfrage(methode, url, body=None, headers=None):
            if methode == "GET":
                return self.ablage[url], {"ETag": '"1"'}
            self.ablage[url] = body
            return b"", {}
        self.dav.request = anfrage

    def eintrag(self, *zeilen):
        self.ablage[self.url] = ("BEGIN:VEVENT\r\n" + "\r\n".join(zeilen) + "\r\nEND:VEVENT").encode()

    def gelesen(self):
        return [(e.get("SUMMARY"), e.get("DESCRIPTION")) for e in C.Calendar.from_ical(self.ablage[self.url]).walk("VEVENT")]

    def test_feiertage(self):
        self.assertIn(dt.date(2027, 1, 1), C.holidays(2027))
        self.assertIn(dt.date(2027, 3, 26), C.holidays(2027))  # Karfreitag
        self.assertIn(dt.date(2027, 3, 29), C.holidays(2027))  # Ostermontag
        self.assertIn(dt.date(2026, 12, 24), C.holidays(2026, ("12-24",)))
        self.assertNotIn(dt.date(2026, 12, 24), C.holidays(2026))

    def test_absage_im_titel_setzt_den_eintrag_auf_sein_raster_zurueck(self):
        self.eintrag("SUMMARY:sTermin: Erika Beispiel Absage", "DESCRIPTION:Notiz der Praxis\\nTelefon: 030 23125 117\\nSMS-Erinnerung: nein")
        self.assertEqual([e["raster"] for e in self.dav.reset_cancelled("termine_a", self.href)], ["sTermin:"])
        self.assertEqual(self.gelesen(), [("sTermin:", "Notiz der Praxis")])

    def test_absage_ohne_leerzeichen_nach_dem_raster(self):
        self.eintrag("SUMMARY:Termin:Erika Beispiel abgesagt")
        self.dav.reset_cancelled("termine_a", self.href)
        self.assertEqual(self.gelesen(), [("Termin:", None)])

    def test_serie_und_eintrag_ohne_markierung_bleiben_unberuehrt(self):
        self.eintrag("SUMMARY:Termin: Absage", "RRULE:FREQ=WEEKLY")
        self.assertEqual(self.dav.reset_cancelled("termine_a", self.href), [])
        self.eintrag("SUMMARY:Termin: Erika Beispiel")
        self.assertEqual(self.dav.reset_cancelled("termine_a", self.href), [])
        self.assertEqual(self.gelesen(), [("Termin: Erika Beispiel", None)])

    def test_absage_der_patientin_nur_wenn_der_eintrag_wie_gebucht_aussieht(self):
        start = C.instant(dt.datetime(2027, 2, 4, 10, 15), self.dav.zone)
        slot = {"cal": "termine_a", "href": self.href, "start": start, "end": start + 900, "title": "termin:"}
        self.eintrag("SUMMARY:Termin: Erika Beispiel", "DTSTART:20270204T101500", "DTEND:20270204T103000",
                     "DESCRIPTION:Telefon: 030 000000\\nSMS-Erinnerung: ja\\nNotiz der Praxis")
        self.assertFalse(self.dav.cancel_entry(slot, "Max Muster"))
        self.assertTrue(self.dav.cancel_entry(slot, "Erika Beispiel"))
        self.assertEqual(self.gelesen(), [("Termin:", "Notiz der Praxis")])
        self.assertFalse(self.dav.cancel_entry(slot, "Erika Beispiel"))

    def test_alter_termin_wird_nur_unveraendert_freigegeben(self):
        start = C.instant(dt.datetime(2027, 2, 4, 10, 15), self.dav.zone)
        verweis = {"cal": "termine_a", "href": self.href, "start": start, "title": "Termin: Erika Beispiel"}
        self.eintrag("SUMMARY:Termin: Erika Beispiel", "DTSTART:20270204T101500", "DTEND:20270204T103000")
        self.assertTrue(self.dav.free_entry(verweis))
        self.assertEqual(self.gelesen()[0][0], "Termin:")
        self.eintrag("SUMMARY:Termin: Andere Person", "DTSTART:20270204T101500", "DTEND:20270204T103000")
        self.assertFalse(self.dav.free_entry(verweis))
        self.assertEqual(self.gelesen()[0][0], "Termin: Andere Person")


class Texte(unittest.TestCase):
    def test_anrede_ohne_platzhalter(self):
        self.assertEqual(wording.addressed("Guten Tag [Vorname Nachname],\n\nvielen Dank.", "de", "e@example.org").split("\n")[0], "Guten Tag,")
        self.assertEqual(wording.addressed("vielen Dank.", "de", "Erika Beispiel <e@example.org>").split("\n")[0], "Guten Tag Erika Beispiel,")
        self.assertEqual(wording.addressed("Sehr geehrte Frau Beispiel,\n\nvielen Dank.", "de", "e@example.org").split("\n")[0], "Sehr geehrte Frau Beispiel,")

    def test_uebersetzung_faellt_auf_englisch_zurueck(self):
        alt, wording.query = wording.query, lambda *args: {"falsch": "x"}
        try:
            wording._draft_phrases.cache_clear()
            self.assertEqual(wording.draft_phrases("ar")["offer"], "We can offer you the following appointments:")
            self.assertIn("ar", wording.FALLBACK)
        finally:
            wording.query = alt
            wording._draft_phrases.cache_clear()
            wording.FALLBACK.clear()

    def test_formular_der_vorbereiteten_mail(self):
        felder = {"name": "Vor- und Nachname", "phone": "Telefon", "sms": "SMS-Erinnerung (ja/nein)"}
        text = "Ich bestätige den Termin.\n\nVor- und Nachname: Erika Beispiel\nTelefon: 030 23125 117\nSMS-Erinnerung (ja/nein): ja\n"
        self.assertEqual(A.reply_fields(text, felder), {"name": "Erika Beispiel", "phone": "030 23125 117", "sms": "ja"})
        self.assertEqual(A.reply_fields("Vor- und Nachname: \nTelefon: \nSMS-Erinnerung (ja/nein): ja", felder), {"name": "", "phone": "", "sms": "offen"})

    def test_entscheidung_im_betreff(self):
        self.assertEqual(A.subject_commands("Terminbestätigung [OK AAAAAAAAAA]"), {"AAAAAAAAAA": "OK"})
        self.assertEqual(A.subject_commands("Terminabsage [NO AAAAAAAAAA BBBBBBBBBB]"), {"AAAAAAAAAA": "NO", "BBBBBBBBBB": "NO"})
        self.assertEqual(A.subject_commands("Re: Ihr Termin"), {})


class Antworten(unittest.TestCase):
    """Die Bearbeitung von Antworten auf ein Angebot, mit nachgestelltem Postfach und Kalender."""
    ANGEBOT = "<angebot@example.org>"
    M, T = "AAAAAAAAAA", "BBBBBBBBBB"

    def setUp(self):
        self.kalender, self.bestaetigungen, self.post = [], [], {}
        self.freigebbar = True
        MODEL.update(reply=None, fail=False, calls=0)
        test = self

        class Dav:
            zone = ZONE
            cfg = {"reply_folders": ["INBOX"]}

            def held_available(self, slot, code, fingerprint):
                return True

            def change(self, slot, code, action, **werte):
                test.kalender.append((code, action, werte.get("name"), werte.get("phone"), werte.get("sms")))

            def cancel_entry(self, slot, name):
                test.kalender.append(("ABSAGE", name))
                return test.freigebbar

            def free_entry(self, verweis):
                test.kalender.append(("ALT", verweis["title"]))
                return test.freigebbar

        class Postfach:
            def select_folder(self, *args, **kw):
                return {}

            def folder_exists(self, folder):
                return True

            def search(self, kriterien):
                return list(test.post)

            def fetch(self, uids, teile):
                return {uid: test.post[uid] for uid in uids if uid in test.post}

            def append(self, *args, **kw):
                pass

        @contextlib.contextmanager
        def postfach():
            yield Postfach(), {"sender": "praxis@example.org", "host": "mail.example.org", "username": "praxis@example.org"}, "Entwürfe"
        self.postfach = postfach
        self.alt = (A.client, A.ack)
        A.client = lambda: Dav()

        def bestaetigung(cfg, password, recipient, mid, lines, words):
            test.bestaetigungen.append(lines)
            nachricht = EmailMessage()
            nachricht["Message-ID"] = "<quittung@example.org>"
            nachricht.set_content("x")
            return nachricht
        A.ack = bestaetigung
        A._retry.clear()
        A._cancel.update(next=0, seen=set())
        with contextlib.closing(A.journal()) as db, db:
            for tabelle in ("offer", "reply_done", "notice", "moved", "receipt"):
                db.execute("DELETE FROM " + tabelle)

    def tearDown(self):
        A.client, A.ack = self.alt

    def angebot(self, *zeilen, alt=False):
        jetzt = int(time.time())
        with contextlib.closing(A.journal()) as db, db:
            for code, gruppe in zeilen:
                slot = {"start": stempel(2027, 1, 11 + gruppe, 9, 0), "end": 0, "label": "A", "wish_index": 0, "receipt": dict(wording.DE),
                        "fields": {"name": "Vor- und Nachname", "phone": "Telefon", "sms": "SMS-Erinnerung (ja/nein)"}}
                if alt:
                    slot["old"] = {"cal": "termine_a", "href": "/x.ics", "start": stempel(2027, 2, 4, 10, 15), "title": "Termin: Erika Beispiel"}
                db.execute("INSERT INTO offer VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                           (code, "1-1", json.dumps(slot), "Absenderin – Vorsorge", "", "patientin@example.org", self.ANGEBOT, jetzt - 100, jetzt + 3600, "sent", "fp"))

    def antwort(self, text, absender="Erika Beispiel <patientin@example.org>", betreff=None):
        nachricht = EmailMessage(policy=policy.default)
        nachricht["From"], nachricht["To"], nachricht["Message-ID"] = absender, "praxis@example.org", "<antwort@example.org>"
        if betreff:
            nachricht["Subject"] = betreff
        else:
            nachricht["In-Reply-To"] = self.ANGEBOT
        nachricht.set_content(text)
        roh = nachricht.as_bytes()
        self.post[1] = {b"INTERNALDATE": dt.datetime.now(), b"RFC822.SIZE": len(roh), b"BODY[]": roh}

    def stand(self):
        with contextlib.closing(A.journal()) as db:
            return {zeile["code"]: zeile["state"] for zeile in db.execute("SELECT code,state FROM offer")}

    def hinweise(self):
        with contextlib.closing(A.journal()) as db:
            return [zeile["text"] for zeile in db.execute("SELECT text FROM notice")]

    def test_klare_zusage_wird_gebucht_und_nur_einmal_gelesen(self):
        self.angebot((self.M, 0))
        self.antwort("Ja, der Termin passt. Tel. 030 23125 117, gern mit SMS.\nErika Beispiel")
        MODEL["reply"] = {"klar": True, "entscheidungen": [{"code": self.M, "aktion": "bestaetigen"}], "name": "Erika Beispiel", "telefon": "030 23125 117", "sms": "ja"}
        A.maintain(self.postfach)
        A.maintain(self.postfach)
        self.assertEqual(self.stand(), {self.M: "booked"})
        self.assertEqual(self.kalender, [(self.M, "book", "Erika Beispiel", "030 23125 117", "ja")])
        self.assertEqual((MODEL["calls"], len(self.bestaetigungen)), (1, 1))

    def test_eine_alternative_gewaehlt_die_andere_wird_frei(self):
        self.angebot((self.M, 0), (self.T, 1))
        self.antwort("Den zweiten bitte. Erika Beispiel")
        MODEL["reply"] = {"klar": True, "entscheidungen": [{"code": self.T, "aktion": "bestaetigen"}, {"code": self.M, "aktion": "offen"}], "name": "Erika Beispiel", "telefon": "", "sms": "offen"}
        A.maintain(self.postfach)
        self.assertEqual(self.stand(), {self.M: "released", self.T: "booked"})

    def test_unklare_antwort_geht_sofort_an_die_mfa(self):
        self.angebot((self.M, 0))
        self.antwort("Vielleicht, ich melde mich noch.")
        MODEL["reply"] = {"klar": False, "entscheidungen": [], "name": "", "telefon": "", "sms": "offen"}
        A.maintain(self.postfach)
        self.assertEqual((self.stand(), self.kalender), ({self.M: "manual"}, []))

    def test_erfundener_name_und_erfundene_nummer_werden_verworfen(self):
        self.angebot((self.M, 0))
        self.antwort("Passt, danke!")
        MODEL["reply"] = {"klar": True, "entscheidungen": [{"code": self.M, "aktion": "bestaetigen"}], "name": "Frieda Erfunden", "telefon": "030 999999", "sms": "ja"}
        A.maintain(self.postfach)
        self.assertEqual(self.kalender, [(self.M, "book", "Erika Beispiel", "", "offen")])

    def test_fremder_code_des_modells_fuehrt_zur_mfa(self):
        self.angebot((self.M, 0))
        self.antwort("Passt, danke!")
        MODEL["reply"] = {"klar": True, "entscheidungen": [{"code": "CCCCCCCCCC", "aktion": "bestaetigen"}], "name": "", "telefon": "", "sms": "offen"}
        A.maintain(self.postfach)
        self.assertEqual((self.stand(), self.kalender), ({self.M: "manual"}, []))

    def test_modell_nicht_erreichbar_reservierung_bleibt(self):
        self.angebot((self.M, 0))
        self.antwort("Passt, danke!")
        MODEL["fail"] = True
        A.maintain(self.postfach)
        A.maintain(self.postfach)
        self.assertEqual((self.stand(), MODEL["calls"]), ({self.M: "sent"}, 1))

    def test_mail_link_bucht_ohne_modell(self):
        self.angebot((self.M, 0), (self.T, 1))
        self.antwort("Ich bestätige den Termin.\n\nVor- und Nachname: Erika Beispiel\nTelefon: 030 23125 117\nSMS-Erinnerung (ja/nein): ja\n",
                     betreff=f"Terminbestätigung [OK {self.T}]")
        A.maintain(self.postfach)
        self.assertEqual(self.stand(), {self.M: "released", self.T: "booked"})
        self.assertEqual((self.kalender[0], MODEL["calls"]), ((self.T, "book", "Erika Beispiel", "030 23125 117", "ja"), 0))

    def test_mail_link_ohne_namen_geht_an_die_mfa(self):
        self.angebot((self.M, 0))
        self.antwort("Ich bestätige den Termin.", absender="patientin@example.org", betreff=f"Terminbestätigung [OK {self.M}]")
        A.maintain(self.postfach)
        self.assertEqual((self.stand(), self.kalender), ({self.M: "manual"}, []))

    def test_mail_link_keiner_passt_gibt_alles_frei(self):
        self.angebot((self.M, 0), (self.T, 1))
        self.antwort("Leider passt keiner.", betreff=f"Terminabsage [NO {self.M} {self.T}]")
        A.maintain(self.postfach)
        self.assertEqual(self.stand(), {self.M: "released", self.T: "released"})

    def test_mail_link_von_fremder_adresse_wird_uebergangen(self):
        self.angebot((self.M, 0))
        self.antwort("Ich bestätige den Termin.", absender="Fremd <fremd@example.org>", betreff=f"Terminbestätigung [OK {self.M}]")
        A.maintain(self.postfach)
        self.assertEqual((self.stand(), self.kalender), ({self.M: "sent"}, []))

    def test_bestaetigte_verlegung_gibt_den_alten_termin_frei(self):
        self.angebot((self.M, 0), alt=True)
        self.antwort("Ich bestätige den Termin.", betreff=f"Terminbestätigung [OK {self.M}]")
        A.maintain(self.postfach)
        self.assertEqual(self.kalender[-1], ("ALT", "Termin: Erika Beispiel"))
        self.assertIn("freigegeben", self.hinweise()[0])
        self.assertTrue(any("entfällt" in zeile for zeile in self.bestaetigungen[0]))

    def test_verlegung_alter_termin_nicht_freigebbar_wird_gemeldet(self):
        self.freigebbar = False
        self.angebot((self.M, 0), alt=True)
        self.antwort("Ich bestätige den Termin.", betreff=f"Terminbestätigung [OK {self.M}]")
        A.maintain(self.postfach)
        self.assertEqual(self.stand(), {self.M: "booked"})
        self.assertIn("konnte nicht freigegeben werden", self.hinweise()[0])
        self.assertFalse(any("entfällt" in zeile for zeile in self.bestaetigungen[0]))

    def test_abgelehnte_verlegung_laesst_den_alten_termin_stehen(self):
        self.angebot((self.M, 0), alt=True)
        self.antwort("Passt leider nicht.", betreff=f"Terminabsage [NO {self.M}]")
        A.maintain(self.postfach)
        self.assertEqual((self.stand(), self.hinweise()), ({self.M: "released"}, []))
        self.assertNotIn(("ALT", "Termin: Erika Beispiel"), self.kalender)

    # --- Absage als Antwort auf die Buchungsbestätigung

    def gebucht_und_absage(self, text="Guten Tag! Ich muss diesen Termin stornieren.", absender="Erika Beispiel <patientin@example.org>",
                           modell=None, bezug="<quittung@example.org>"):
        self.angebot((self.M, 0))
        self.antwort("Ich bestätige den Termin.\n\nVor- und Nachname: Erika Beispiel\nTelefon: 030 23125 117\n", betreff=f"Terminbestätigung [OK {self.M}]")
        A.maintain(self.postfach)
        self.assertEqual(self.stand(), {self.M: "booked"})
        nachricht = EmailMessage(policy=policy.default)
        nachricht["From"], nachricht["To"], nachricht["Message-ID"], nachricht["In-Reply-To"] = absender, "praxis@example.org", "<absage@example.org>", bezug
        nachricht.set_content(text + "\n\n> Wir bestätigen den Termin für Erika Beispiel am 11.01.2027 09:00.")
        roh = nachricht.as_bytes()
        self.post[2] = {b"INTERNALDATE": dt.datetime.now(), b"RFC822.SIZE": len(roh), b"BODY[]": roh}
        MODEL["reply"] = modell or {"anliegen": "absagen", "beleg": "Ich muss diesen Termin stornieren."}
        MODEL["calls"] = 0
        self.bestaetigungen.clear()
        A._cancel["next"] = 0
        A.maintain(self.postfach)

    def test_absage_auf_die_bestaetigung_gibt_den_termin_frei(self):
        self.gebucht_und_absage()
        self.assertEqual(self.stand(), {self.M: "cancelled"})
        self.assertEqual(self.kalender[-1], ("ABSAGE", "Erika Beispiel"))
        self.assertEqual(self.bestaetigungen, [["Wir haben Ihren Termin am 11.01.2027 09:00 wie gewünscht abgesagt."]])
        self.assertIn("Absage per Mail: Termin am 11.01.2027 09:00", self.hinweise()[-1])
        A._cancel["next"] = 0
        A.maintain(self.postfach)                                      # wird nur einmal bearbeitet
        self.assertEqual((MODEL["calls"], len(self.bestaetigungen)), (1, 1))

    def test_absage_andere_mitteilung_aendert_nichts(self):
        self.gebucht_und_absage(text="Vielen Dank, meine Nummer ist 030 000000.", modell={"anliegen": "anderes", "beleg": "Vielen Dank"})
        self.assertEqual((self.stand(), self.bestaetigungen), ({self.M: "booked"}, []))
        self.assertNotIn(("ABSAGE", "Erika Beispiel"), self.kalender)

    def test_absage_beleg_steht_nicht_in_der_antwort(self):
        self.gebucht_und_absage(text="Kann ich auch später kommen?", modell={"anliegen": "absagen", "beleg": "Ich sage den Termin ab"})
        self.assertEqual(self.stand(), {self.M: "booked"})

    def test_absage_steht_nur_im_zitat(self):
        self.gebucht_und_absage(text="Danke!\n\n> Ich muss diesen Termin stornieren.")
        self.assertEqual(self.stand(), {self.M: "booked"})

    def test_absage_von_fremder_adresse_oder_ohne_bezug_wird_uebergangen(self):
        self.gebucht_und_absage(absender="Fremd <fremd@example.org>")
        self.assertEqual((self.stand(), MODEL["calls"]), ({self.M: "booked"}, 0))

    def test_absage_ohne_bezug_zur_bestaetigung_wird_uebergangen(self):
        self.gebucht_und_absage(bezug="<etwas-anderes@example.org>")
        self.assertEqual((self.stand(), MODEL["calls"]), ({self.M: "booked"}, 0))

    def test_absage_eintrag_geaendert_geht_an_die_mfa(self):
        self.freigebbar = False
        self.gebucht_und_absage()
        self.assertEqual((self.stand(), self.bestaetigungen), ({self.M: "booked"}, []))
        self.assertIn("konnte nicht freigegeben werden", self.hinweise()[-1])

    def test_absage_modell_nicht_erreichbar_wird_spaeter_gelesen(self):
        MODEL["fail"] = False
        self.angebot((self.M, 0))
        self.antwort("Ich bestätige den Termin.\n\nVor- und Nachname: Erika Beispiel\n", betreff=f"Terminbestätigung [OK {self.M}]")
        A.maintain(self.postfach)
        nachricht = EmailMessage(policy=policy.default)
        nachricht["From"], nachricht["Message-ID"], nachricht["In-Reply-To"] = "Erika Beispiel <patientin@example.org>", "<absage@example.org>", "<quittung@example.org>"
        nachricht.set_content("Ich sage den Termin ab.")
        roh = nachricht.as_bytes()
        self.post[2] = {b"INTERNALDATE": dt.datetime.now(), b"RFC822.SIZE": len(roh), b"BODY[]": roh}
        MODEL.update(fail=True)
        A._cancel["next"] = 0
        A.maintain(self.postfach)
        self.assertEqual(self.stand(), {self.M: "booked"})
        MODEL.update(fail=False, reply={"anliegen": "absagen", "beleg": "Ich sage den Termin ab."})
        A._cancel["next"] = 0
        A.maintain(self.postfach)
        self.assertEqual(self.stand(), {self.M: "cancelled"})

    def test_gebuchter_termin_bleibt_bis_zum_termintag_bekannt(self):
        self.angebot((self.M, 0), (self.T, 1))
        with contextlib.closing(A.journal()) as db, db:
            db.execute("UPDATE offer SET created=created-30*86400, state=CASE code WHEN ? THEN 'booked' ELSE 'released' END", (self.M,))
        A.maintain(self.postfach)
        self.assertEqual(self.stand(), {self.M: "booked"})

    # --- Verlegung: Der bisherige Termin wird beim Senden des Angebots freigegeben.

    def senden(self, alt="alt1", absenden=True):
        """Stellt den Versand eines Angebots nach: ein gewählter Platz, dazu die Wahl des bisherigen Termins."""
        test = self
        start = stempel(2027, 1, 12, 9, 0)
        platz_neu = dict(id="neu1", cal="termine_a", label="A", start=start, end=start + 900, title="termin:", href="/n.ics", uid="u", rid=start, etag='"e"')
        dav = A.client()
        dav.cfg = dict(CFG)
        dav.scan = lambda a, b: [dict(platz_neu)]
        A.client = lambda: dav
        wunsch = dict(A.blank_wish("Absenderin"), kind="vorsorge", label="Vorsorge", action="verschieben",
                      options=[{"id": "neu1", "start": start}], old_options=[{"id": "alt1", "label": "…"}], old_pick="alt1",
                      old_refs={"alt1": {"cal": "termine_a", "href": "/x.ics", "start": stempel(2027, 2, 4, 10, 15), "title": "Termin: Erika Beispiel"}})
        frist = int(time.time()) // 60 * 60 + 3600
        alt_suche, A.search = A.search, lambda ctx, days, override: {"wishes": [wunsch], "lang": "de"}
        alt_wechsel, type(dav).change = type(dav).change, lambda self, slot, code, action, **w: test.kalender.append((code, action)) or "fp"
        nachricht = EmailMessage(policy=policy.default)
        nachricht["From"], nachricht["To"], nachricht["Message-ID"] = "praxis@example.org", "Erika Beispiel <patientin@example.org>", "<neu@example.org>"
        nachricht.set_content("Guten Tag Frau Beispiel,\n\n[[JOHM_TERMINE]]\n\nFreundliche Grüße\nIhr Praxisteam")
        wahl = [{"id": "neu1", "index": 0, "days": 60, "deadline": frist, "person": "Absenderin", "kind": "vorsorge", "year": 0, "name": "", "old": alt}]
        try:
            with A.prepare(wahl, {"source": "9-9", "sender": "Erika Beispiel <patientin@example.org>"}, nachricht, "patientin@example.org") as angenommen:
                vorher = [eintrag for eintrag in self.kalender if eintrag[0] == "ALT"]
                if absenden:
                    angenommen()
        finally:
            A.search, type(dav).change = alt_suche, alt_wechsel
        return nachricht, vorher

    def test_verlegung_gibt_den_alten_termin_beim_senden_frei(self):
        nachricht, vorher = self.senden()
        self.assertEqual(vorher, [])                                   # nicht vor dem Versand
        self.assertIn(("ALT", "Termin: Erika Beispiel"), self.kalender)
        text = nachricht.get_body(("plain",)).get_content()
        self.assertIn("Ihren bisherigen Termin am 04.02.2027 10:15 haben wir wie gewünscht freigegeben.", text)
        self.assertIn("freigegeben", nachricht.get_body(("html",)).get_content())
        self.assertEqual(self.hinweise(), ["Verlegung: bisheriger Termin am 04.02.2027 10:15 freigegeben."])
        with contextlib.closing(A.journal()) as db:
            slot = json.loads(db.execute("SELECT slot FROM offer").fetchone()["slot"])
        self.assertNotIn("old", slot)                                  # bei der Bestätigung wird nichts mehr freigegeben

    def test_verlegung_ohne_wahl_laesst_den_alten_termin_stehen(self):
        nachricht, _ = self.senden(alt="")
        self.assertNotIn(("ALT", "Termin: Erika Beispiel"), self.kalender)
        self.assertNotIn("bisherigen Termin", nachricht.get_body(("plain",)).get_content())
        self.assertEqual(self.hinweise(), [])

    def test_verlegung_unbekannte_wahl_aus_dem_browser_wird_abgelehnt(self):
        with self.assertRaisesRegex(ValueError, "Bisheriger Termin"):
            self.senden(alt="erfunden")
        self.assertEqual(self.kalender, [])

    def test_verlegung_versand_gescheitert_alter_termin_bleibt(self):
        self.senden(absenden=False)
        self.assertNotIn(("ALT", "Termin: Erika Beispiel"), self.kalender)
        self.assertEqual(list(self.stand().values()), ["released"])

    def test_verlegung_freigabe_gescheitert_wird_gemeldet(self):
        self.freigebbar = False
        self.senden()
        self.assertIn("konnte nicht freigegeben werden", self.hinweise()[0])

    def test_verlegung_ohne_bestaetigung_meldet_fehlenden_termin(self):
        self.senden()
        A.maintain(self.postfach)
        self.assertEqual(len(self.hinweise()), 1)                      # Angebot läuft noch
        with contextlib.closing(A.journal()) as db, db:
            db.execute("UPDATE offer SET state='released'")
        A.maintain(self.postfach)
        A.maintain(self.postfach)
        self.assertEqual(len(self.hinweise()), 2)                      # genau ein weiterer Hinweis
        self.assertIn("Verlegung ohne neuen Termin", self.hinweise()[1])

    def test_verlegung_mit_buchung_meldet_nichts_weiter(self):
        self.senden()
        with contextlib.closing(A.journal()) as db, db:
            db.execute("UPDATE offer SET state='booked'")
        A.maintain(self.postfach)
        self.assertEqual(len(self.hinweise()), 1)

    def test_verlauf_haelt_jeden_statuswechsel_fest(self):
        self.angebot((self.M, 0))
        self.antwort("Ich bestätige den Termin.", betreff=f"Terminbestätigung [OK {self.M}]")
        A.maintain(self.postfach)
        with contextlib.closing(A.journal()) as db:
            verlauf = [(zeile["was"], zeile["now"]) for zeile in db.execute("SELECT was,now FROM history WHERE code=? ORDER BY rowid", (self.M,))]
        self.assertEqual(verlauf[-3:], [("sent", "confirming"), ("confirming", "ack_pending"), ("ack_pending", "booked")])



class Weiterleiten(unittest.TestCase):
    """Weiterleiten der Originalmail an beliebige Adressen, mit nachgestelltem Postfach und SMTP."""

    def setUp(self):
        import importlib.util
        sys.modules["review"] = types.SimpleNamespace(state=lambda: TMP, account=lambda cfg, valid: "praxis@example.org/" + str(valid))
        spec = importlib.util.spec_from_file_location("outbound_echt", ROOT / "quelle" / "outbound.py")
        self.O = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.O)
        self.gesendet, self.abgelegt, self.markiert = [], [], []
        self.ablehnen = set()
        test = self
        original = EmailMessage(policy=policy.default)
        original["From"], original["To"], original["Subject"] = "Erika Beispiel <patientin@example.org>", "praxis@example.org", "Befund für Größe"
        original["Date"] = "Fri, 27 Nov 2026 08:00:00 +0100"
        original.set_content("Guten Tag, anbei mein Befund. Grüße")
        original.add_attachment(b"%PDF-1.4 erfunden", maintype="application", subtype="pdf", filename="befund.pdf")
        self.roh = original.as_bytes()

        class Postfach:
            def select_folder(self, name, readonly=True):
                return {b"UIDVALIDITY": 7}

            def fetch(self, uids, teile):
                if "BODY.PEEK[]" in teile:
                    return {5: {b"BODY[]": test.roh}}
                return {5: {b"RFC822.SIZE": len(test.roh), b"FLAGS": ()}}

            def list_folders(self):
                return [((b"\\Sent",), b"/", "Gesendet"), ((), b"/", "INBOX")]

            def append(self, ordner, roh, flags=()):
                test.abgelegt.append((ordner, roh))

            def add_flags(self, uids, flags, silent=False):
                test.markiert.append((uids, flags))

        class SMTP:
            def __init__(self, *args, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def close(self):
                pass

            def starttls(self, **kw):
                pass

            def login(self, *args):
                pass

            def sendmail(self, absender, an, roh):
                test.gesendet.append((absender, list(an), roh))
                return {a: (550, b"nein") for a in an if a in test.ablehnen}

        self.O.smtplib = types.SimpleNamespace(SMTP=SMTP, SMTPAuthenticationError=OSError, SMTPRecipientsRefused=KeyError,
                                               SMTPResponseException=LookupError, SMTPNotSupportedError=LookupError)
        self.postfach = Postfach()
        with contextlib.closing(self.O.forward_journal()) as db, db:
            db.execute("DELETE FROM forwarded")

    def tearDown(self):
        sys.modules.pop("review", None)

    def weiter(self, an, notiz="Bitte ansehen.", revision="r1"):
        ctx = {"revision": "r1", "source": "7-5"}
        return self.O.forward(self.postfach, {"sender": "praxis@example.org", "host": "mail.example.org", "username": "praxis@example.org"},
                              "Entwürfe", "r-7-5", revision, an, notiz, "geheim", None, lambda *a: (None, ctx), RuntimeError)

    def test_weiterleiten_an_beliebige_adressen_mit_originalmail(self):
        antwort = self.weiter("Dr. Muster <muster@praxis-beispiel.de>; labor@labor-beispiel.de")
        self.assertIn("An 2 Empfänger weitergeleitet", antwort["notice"])
        absender, an, roh = self.gesendet[0]
        self.assertEqual(an, ["muster@praxis-beispiel.de", "labor@labor-beispiel.de"])
        from email.parser import BytesParser
        mail = BytesParser(policy=policy.default).parsebytes(roh)
        self.assertEqual(mail["Subject"], "WG: Befund für Größe")
        text = mail.get_body(("plain",)).get_content()
        self.assertTrue(text.startswith("Bitte ansehen."))
        self.assertIn("Von: Erika Beispiel <patientin@example.org>", text)
        self.assertIn("anbei mein Befund", text)
        innen = [teil for teil in mail.iter_attachments() if teil.get_content_type() == "message/rfc822"][0].get_content()
        self.assertEqual([a.get_filename() for a in innen.iter_attachments()], ["befund.pdf"])
        self.assertEqual(self.abgelegt[0][0], "Gesendet")
        self.assertEqual(self.markiert, [([5], [b"$Forwarded"])])

    def test_gleiche_empfaenger_nur_einmal_andere_ja(self):
        self.weiter("labor@labor-beispiel.de")
        with self.assertRaisesRegex(RuntimeError, "bereits weitergeleitet"):
            self.weiter("LABOR@labor-beispiel.de")
        self.weiter("muster@praxis-beispiel.de")
        self.assertEqual(len(self.gesendet), 2)

    def test_ungueltige_adressen_und_faxgateway_werden_abgelehnt(self):
        for falsch in ("", "labor", "labor@beispiel", "a@b.de\r\nBcc: x@y.de", "fax@example.org", ",".join(f"a{i}@b.de" for i in range(6))):
            with self.assertRaises(ValueError):
                self.weiter(falsch)
        self.assertEqual(self.gesendet, [])

    def test_veraenderte_mail_wird_nicht_weitergeleitet(self):
        with self.assertRaisesRegex(RuntimeError, "geändert"):
            self.weiter("labor@labor-beispiel.de", revision="alt")
        self.assertEqual(self.gesendet, [])

    def test_teilweise_abgelehnt_wird_gemeldet(self):
        self.ablehnen = {"labor@labor-beispiel.de"}
        antwort = self.weiter("muster@praxis-beispiel.de, labor@labor-beispiel.de")
        self.assertIn("An 1 Empfänger weitergeleitet, 1 abgelehnt", antwort["notice"])

    def test_ohne_notiz(self):
        self.weiter("labor@labor-beispiel.de", notiz="")
        from email.parser import BytesParser
        text = BytesParser(policy=policy.default).parsebytes(self.gesendet[0][2]).get_body(("plain",)).get_content()
        self.assertTrue(text.startswith("---------- Weitergeleitete Nachricht"))


class ChatWerkzeug(unittest.TestCase):
    """Zeitangaben im Kalender-Werkzeug für Open WebUI."""

    @classmethod
    def setUpClass(cls):
        import importlib.util
        if "pydantic" not in sys.modules:
            try:
                import pydantic  # noqa: F401
            except ImportError:
                sys.modules["pydantic"] = types.SimpleNamespace(BaseModel=object, Field=lambda default=None, **kw: default)
        spec = importlib.util.spec_from_file_location("johm_kalender_tool", ROOT / "werkzeuge" / "johm_kalender_tool.py")
        cls.W = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.W)

    def zeit(self, von, bis=""):
        a, b = self.W.period(von, bis, dt.date(2026, 11, 27))  # Freitag
        return a.isoformat(), b.isoformat()

    def test_wochen(self):
        self.assertEqual(self.zeit("übernächste Woche"), ("2026-12-07", "2026-12-13"))
        self.assertEqual(self.zeit("in der übernächsten Woche"), ("2026-12-07", "2026-12-13"))
        self.assertEqual(self.zeit("nächste Woche"), ("2026-11-30", "2026-12-06"))
        self.assertEqual(self.zeit("diese Woche"), ("2026-11-27", "2026-11-29"))
        self.assertEqual(self.zeit("in 3 Wochen"), ("2026-12-14", "2026-12-20"))
        self.assertEqual(self.zeit("KW 2"), ("2027-01-11", "2027-01-17"))
        self.assertEqual(self.zeit("KW 50"), ("2026-12-07", "2026-12-13"))

    def test_tage_und_daten(self):
        self.assertEqual(self.zeit("morgen"), ("2026-11-28", "2026-12-04"))
        self.assertEqual(self.zeit("Montag", "Montag"), ("2026-11-30", "2026-11-30"))
        self.assertEqual(self.zeit("15.01.", "22.01."), ("2027-01-15", "2027-01-22"))
        self.assertEqual(self.zeit("2026-12-01", "2026-12-03"), ("2026-12-01", "2026-12-03"))
        self.assertEqual(self.zeit(""), ("2026-11-27", "2026-12-03"))
        self.assertEqual(self.zeit("nächsten Monat"), ("2026-12-01", "2026-12-31"))

    def test_eintrag_tag_und_uhrzeit(self):
        heute = dt.date(2026, 11, 27)
        self.assertEqual(self.W.one_day("14.01.26", heute).isoformat(), "2026-01-14")
        self.assertEqual(self.W.one_day("14.01.2027", heute).isoformat(), "2027-01-14")
        self.assertEqual(self.W.one_day("Montag", heute).isoformat(), "2026-11-30")
        with self.assertRaises(ValueError):
            self.W.one_day("nächste Woche", heute)
        self.assertEqual([self.W.clock(t) for t in ("13:00", "13 Uhr", "9.30", "8")], ["13:00", "13:00", "09:30", "08:00"])
        with self.assertRaises(ValueError):
            self.W.clock("25:00")

    def test_grenzen(self):
        self.assertEqual(self.zeit("2026-12-01", "2027-03-01")[1], "2027-01-01")
        with self.assertRaises(ValueError):
            self.zeit("irgendwann")
        with self.assertRaises(ValueError):
            self.zeit("2026-12-10", "2026-12-01")


class ChatEintragen(unittest.TestCase):
    """Eintragen aus dem Chat: Prüfung, Bestätigung, Schreiben in einen freien Platz."""

    def setUp(self):
        import ast
        quelle = (ROOT / "quelle" / "desk.py").read_text(encoding="utf-8")
        baum = ast.parse(quelle)
        teile = [n for n in baum.body if isinstance(n, ast.FunctionDef) and n.name in ("flat_name", "calendar_enter", "calendar_remove")]
        self.schreiben = []
        test = self
        start = stempel(2027, 1, 14, 13, 0)
        self.freigebbar = True
        self.belegt = [("Termin: Erika Beispiel Kontrolle", start + 7200, "A"), ("sTermin: Max Muster", start + 7200, "B"),
                       ("Termin: Erika Beispielmann", start + 8100, "A"), ("RESERVIERT für Erika Beispiel · bis morgen · Termin:", start + 9000, "A")]

        plaetze = [dict(id="p0", title="schwanger:", label="A", start=start - 3600, end=start - 2700, cal="termine_a"),
                   dict(id="p1", title="termin:", label="A", start=start, end=start + 900, cal="termine_a"),
                   dict(id="p2", title="termin:", label="A", start=start + 900, end=start + 1800, cal="termine_a"),
                   dict(id="p3", title="stermin:", label="B", start=start + 3600, end=start + 4500, cal="termine_b")]

        class Dav:
            zone = ZONE
            cfg = dict(CFG, mode="live")

            def agenda(self, a, b):
                return [{"title": "Schwanger:" if p["id"] == "p0" else p["title"].capitalize().replace("Stermin", "sTermin"),
                         "free": True, "raster": True, "start": p["start"]} for p in plaetze] + [
                    dict(title=t, free=False, raster=True, start=st, end=st + 900, calendar=c, path="termine_" + c.lower(), href=f"/{i}.ics")
                    for i, (t, st, c) in enumerate(test.belegt)]

            def free_entry(self, verweis):
                test.schreiben.append(("frei", verweis["title"], verweis["start"]))
                return test.freigebbar

            def scan(self, a, b):
                erlaubt = {t.strip().casefold() for t in self.cfg["free_titles"]}
                return [dict(p) for p in plaetze if p["title"] in erlaubt]

            def change(self, slot, code, action, **werte):
                test.schreiben.append((slot["id"], action, werte.get("name"), werte.get("phone"), werte.get("note", "")))
                return "fp"

        cfg = dict(CFG, mode="live")
        self.ns = {"re": __import__("re"), "time": types.SimpleNamespace(time=lambda: JETZT.timestamp()), "wording": wording,
                   "hashlib": __import__("hashlib"), "json": json,
                   "web": types.SimpleNamespace(HTTPForbidden=PermissionError),
                   "boxes": types.SimpleNamespace(catalog=lambda: {"1": {"config": {"username": "praxis@example.org"}}}, allowed=lambda u, b: True),
                   "appointments": types.SimpleNamespace(settings=lambda: cfg, client=lambda: Dav(), doctors=A.doctors, doctor_of=A.doctor_of),
                   "print": lambda *a, **k: None}
        exec(compile(ast.Module(body=teile, type_ignores=[]), "desk.py", "exec"), self.ns)

    def eintragen(self, **werte):
        anfrage = dict({"name": "Erika Beispiel", "datum": "2027-01-14", "uhrzeit": "13:00"}, **werte)
        return self.ns["calendar_enter"](anfrage, {"email": "mfa@example.org"})

    def test_pruefen_schreibt_nichts_eintragen_bucht(self):
        gefunden = self.eintragen()
        self.assertEqual((gefunden["status"], gefunden["id"], gefunden["titel"]), ("frei", "p1", "Termin: Erika Beispiel"))
        self.assertIn("Do 14.01.2027 13:00–13:15 · Dr. Beispiel", gefunden["text"])
        self.assertEqual(self.schreiben, [])
        fertig = self.eintragen(schritt="eintragen", id="p1", telefon="030 000000")
        self.assertEqual(fertig["status"], "eingetragen")
        self.assertEqual([(s[0], s[1]) for s in self.schreiben], [("p1", "hold"), ("p1", "book")])
        self.assertEqual(self.schreiben[1][2:4], ("Erika Beispiel", "030 000000"))
        self.assertIn("Eingetragen im Chat von mfa@example.org", self.schreiben[1][4])

    def test_falsche_kennung_wird_nicht_gebucht(self):
        with self.assertRaisesRegex(ValueError, "nicht mehr frei"):
            self.eintragen(schritt="eintragen", id="p2")
        self.assertEqual(self.schreiben, [])

    def test_belegte_zeit_nennt_freie_plaetze_des_tages(self):
        antwort = self.eintragen(uhrzeit="11:00")
        self.assertEqual(antwort["status"], "belegt")
        self.assertEqual(antwort["frei_am_tag"], ["12:00 Schwanger: (Dr. Beispiel)", "13:00 Termin: (Dr. Beispiel)", "13:15 Termin: (Dr. Beispiel)", "14:00 sTermin: (Dr. Muster)"])

    def test_jedes_freie_raster_des_tages_ist_eintragbar(self):
        # "Schwanger:" steht nicht in free_titles, ist aber ein unberührter Rasterplatz.
        gefunden = self.eintragen(uhrzeit="12:00")
        self.assertEqual((gefunden["status"], gefunden["titel"]), ("frei", "Schwanger: Erika Beispiel"))

    def test_arzt_filter_und_unbekannter_arzt(self):
        self.assertEqual(self.eintragen(uhrzeit="13:00", arzt="Muster")["status"], "belegt")
        with self.assertRaisesRegex(ValueError, "Bekannt sind: Dr. Beispiel, Dr. Muster"):
            self.eintragen(arzt="Dr. Niemand")

    def entfernen(self, **werte):
        anfrage = dict({"name": "Erika Beispiel", "datum": "2027-01-14"}, **werte)
        return self.ns["calendar_remove"](anfrage, {"email": "mfa@example.org"})

    def test_entfernen_findet_den_termin_und_gibt_ihn_nach_bestaetigung_frei(self):
        gefunden = self.entfernen(uhrzeit="15:00")
        self.assertEqual((gefunden["status"], gefunden["raster"]), ("gefunden", "Termin:"))
        self.assertIn("15:00–15:15 · Dr. Beispiel · Termin: Erika Beispiel Kontrolle", gefunden["text"])
        self.assertEqual(self.schreiben, [])
        fertig = self.entfernen(uhrzeit="15:00", schritt="entfernen", id=gefunden["id"])
        self.assertEqual(fertig["status"], "entfernt")
        self.assertEqual(self.schreiben[0][:2], ("frei", "Termin: Erika Beispiel Kontrolle"))

    def test_entfernen_nur_ganze_namen_und_keine_reservierung(self):
        # "Beispielmann" ist ein anderer Name, die Reservierung eines Angebots wird hier nicht angefasst.
        self.assertEqual(self.entfernen()["status"], "gefunden")
        self.assertEqual(self.entfernen(name="Max Muster", arzt="Dr. Beispiel")["status"], "nicht_gefunden")
        self.assertEqual(self.entfernen(name="Max Muster")["status"], "gefunden")

    def test_entfernen_mit_falscher_kennung_oder_abgelehnt(self):
        with self.assertRaisesRegex(ValueError, "inzwischen geändert"):
            self.entfernen(schritt="entfernen", id="falsch")
        self.freigebbar = False
        gefunden = self.entfernen()
        with self.assertRaisesRegex(ValueError, "Freigabe abgelehnt"):
            self.entfernen(schritt="entfernen", id=gefunden["id"])

    def test_vergangenheit_und_name_werden_geprueft(self):
        with self.assertRaisesRegex(ValueError, "Vergangenheit. Gemeint ist vielleicht Do 14.01.2027"):
            self.eintragen(datum="2026-01-14")
        with self.assertRaisesRegex(ValueError, "Vor- und Nachnamen"):
            self.eintragen(name="Erika")

if __name__ == "__main__":
    unittest.main()
