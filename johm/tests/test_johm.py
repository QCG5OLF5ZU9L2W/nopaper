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
    FAX_ADDRESS="fax@example.org", mailbody=lambda message: message.get_content())
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
            nachricht.set_content("x")
            return nachricht
        A.ack = bestaetigung
        A._retry.clear()
        with contextlib.closing(A.journal()) as db, db:
            for tabelle in ("offer", "reply_done", "notice"):
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

    def test_verlauf_haelt_jeden_statuswechsel_fest(self):
        self.angebot((self.M, 0))
        self.antwort("Ich bestätige den Termin.", betreff=f"Terminbestätigung [OK {self.M}]")
        A.maintain(self.postfach)
        with contextlib.closing(A.journal()) as db:
            verlauf = [(zeile["was"], zeile["now"]) for zeile in db.execute("SELECT was,now FROM history WHERE code=? ORDER BY rowid", (self.M,))]
        self.assertEqual(verlauf[-3:], [("sent", "confirming"), ("confirming", "ack_pending"), ("ack_pending", "booked")])


if __name__ == "__main__":
    unittest.main()
