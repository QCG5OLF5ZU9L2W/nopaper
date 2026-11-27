"""
title: Praxiskalender (Johm)
author: Praxis
version: 2.0
description: Liest den Nextcloud-Praxiskalender über Johm. Nur lesend, keine Reservierung und keine Buchung.
"""
# SPDX-License-Identifier: AGPL-3.0-or-later

import asyncio
import datetime as dt
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

WEEKDAYS = ("montag", "dienstag", "mittwoch", "donnerstag", "freitag", "samstag", "sonntag")
SHORT = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")


def monday(day):
    return day - dt.timedelta(days=day.weekday())


def period(von, bis, today):
    """Turns what the user said into a first and last day. Accepts JJJJ-MM-TT, TT.MM.(JJJJ) and words such as
    heute, morgen, übermorgen, diese/nächste/übernächste Woche, in 3 Wochen, KW 50, nächsten Monat, Wochentage."""

    def one(text, end=False):
        t = str(text or "").casefold()
        for umlaut, plain in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
            t = t.replace(umlaut, plain)
        t = re.sub(r"^(?:ab|am|an|in der|in den|im|die|der|den|zur|fuer die|fuer)\s+", "", " ".join(t.split()))
        t = re.sub(r"(woche|monat)n?\b", r"\1", t).replace("naechsten woche", "naechste woche")
        if not t:
            return None
        try:
            return dt.date.fromisoformat(t), None
        except ValueError:
            pass
        m = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{2,4})?", t)
        if m:
            year = int(m[3]) if m[3] else today.year
            year += 2000 if year < 100 else 0
            day = dt.date(year, int(m[2]), int(m[1]))
            if not m[3] and day < today:
                day = day.replace(year=year + 1)
            return day, None
        if t == "heute":
            return today, None
        if t == "morgen":
            return today + dt.timedelta(days=1), None
        if t == "uebermorgen":
            return today + dt.timedelta(days=2), None
        steps = {"diese woche": 0, "dieser woche": 0, "naechste woche": 1, "naechster woche": 1, "kommende woche": 1,
                 "kommenden woche": 1, "uebernaechste woche": 2, "uebernaechster woche": 2, "uebernaechsten woche": 2}
        if t in steps:
            start = monday(today) + dt.timedelta(weeks=steps[t])
            return max(start, today), start + dt.timedelta(days=6)
        m = re.fullmatch(r"in (\d{1,2}) wochen?", t)
        if m:
            start = monday(today) + dt.timedelta(weeks=int(m[1]))
            return start, start + dt.timedelta(days=6)
        m = re.fullmatch(r"(?:kw|kalenderwoche) ?(\d{1,2})(?:[ /](\d{4}))?", t)
        if m:
            year = int(m[2]) if m[2] else today.year
            start = dt.date.fromisocalendar(year, int(m[1]), 1)
            if not m[2] and start + dt.timedelta(days=6) < today:
                start = dt.date.fromisocalendar(year + 1, int(m[1]), 1)
            return start, start + dt.timedelta(days=6)
        if t in ("naechster monat", "naechsten monat", "kommender monat"):
            start = (today.replace(day=1) + dt.timedelta(days=32)).replace(day=1)
            following = (start + dt.timedelta(days=32)).replace(day=1)
            return start, following - dt.timedelta(days=1)
        if t in ("dieser monat", "diesen monat"):
            following = (today.replace(day=1) + dt.timedelta(days=32)).replace(day=1)
            return today, following - dt.timedelta(days=1)
        m = re.fullmatch(r"(?:(naechsten|kommenden) )?(" + "|".join(WEEKDAYS) + r")", t)
        if m:
            ahead = (WEEKDAYS.index(m[2]) - today.weekday()) % 7
            if m[1] and ahead == 0:
                ahead = 7
            day = today + dt.timedelta(days=ahead)
            return day, None
        raise ValueError(f"Zeitangabe nicht verstanden: {text}")

    first = one(von)
    if first is None:
        first = (today, None)
    last = one(bis, end=True) if str(bis or "").strip() else None
    start = first[0]
    end = (last[1] or last[0]) if last else (first[1] or start + dt.timedelta(days=6))
    if end < start:
        raise ValueError("Das Ende liegt vor dem Anfang.")
    if (end - start).days > 31:
        end = start + dt.timedelta(days=31)
    return start, end


class Tools:
    class Valves(BaseModel):
        johm_url: str = Field(
            default="http://127.0.0.1:3301",
            description="Adresse des Johm-Mailarbeitsplatzes auf diesem Rechner.",
        )
        johm_key: str = Field(
            default="",
            description="Inhalt der Datei /etc/nopaper-mail/chat-tool.key.",
        )
        zeitzone: str = Field(default="Europe/Berlin", description="Zeitzone der Praxis.")

    def __init__(self):
        self.valves = self.Valves()

    async def praxiskalender(
        self,
        von: str = "",
        bis: str = "",
        arzt: str = "",
        art: str = "",
        nur_frei: bool = True,
        __user__: dict = None,
    ) -> str:
        """
        Liest den Terminkalender der Praxis aus Nextcloud. Für jede Frage nach freien oder belegten Praxisterminen
        dieses Werkzeug verwenden und keinen anderen Kalender. Zeitangaben so übergeben, wie sie gesagt wurden,
        das Werkzeug rechnet sie selbst in Daten um.
        :param von: Beginn: so wie gesagt, z. B. "übernächste Woche", "nächste Woche", "KW 3", "morgen", "Montag", "15.01." oder JJJJ-MM-TT. Leer bedeutet ab heute.
        :param bis: Ende, nur wenn ausdrücklich genannt, sonst leer lassen. Eine Woche als Beginn umfasst schon die ganze Woche.
        :param arzt: Name der Ärztin oder des Arztes, z. B. "Dr. Beispiel" oder nur der Nachname. Leer für alle.
        :param art: Terminraster, wie es im Kalendertitel vor dem Doppelpunkt steht. Leer lassen für alle Arten.
        :param nur_frei: true zeigt nur freie Plätze. false zeigt auch belegte Termine mit ihren Einträgen.
        """
        if not self.valves.johm_key:
            return "Das Kalender-Werkzeug ist nicht eingerichtet: In den Einstellungen des Werkzeugs fehlt der Schlüssel."
        today = dt.datetime.now(ZoneInfo(self.valves.zeitzone)).date()
        try:
            first, last = period(von, bis, today)
        except ValueError as error:
            return f"{error} Bitte ein Datum (TT.MM.JJJJ) oder z. B. „nächste Woche“ angeben."
        user = __user__ or {}
        query = urllib.parse.urlencode({"von": first.isoformat(), "bis": last.isoformat(), "arzt": arzt, "art": art,
                                        "frei": "1" if nur_frei else "0"})
        request = urllib.request.Request(
            self.valves.johm_url.rstrip("/") + "/intern/kalender?" + query,
            headers={
                "X-Johm-Key": self.valves.johm_key,
                "X-Johm-User-Id": str(user.get("id", "")),
                "X-Johm-User-Email": str(user.get("email", "")),
                "X-Johm-User-Role": str(user.get("role", "")),
            },
        )

        def fetch():
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(request, timeout=60) as response:
                return json.load(response)

        try:
            result = await asyncio.to_thread(fetch)
        except urllib.error.HTTPError as error:
            detail = error.read(300).decode("utf-8", "replace")
            return f"Der Praxiskalender konnte nicht gelesen werden ({error.code}): {detail} Es liegt keine Auskunft über Termine vor."
        except (OSError, ValueError):
            return ("Der Praxiskalender ist nicht erreichbar. Es liegt keine Auskunft über Termine vor. "
                    "Läuft Open WebUI in Docker, erreicht es Johm unter 127.0.0.1 nicht.")
        span = f"{SHORT[first.weekday()]} {first:%d.%m.%Y} bis {SHORT[last.weekday()]} {last:%d.%m.%Y}"
        who = ", ".join(result.get("doctors") or []) if arzt else "alle Ärztinnen und Ärzte"
        head = f"Heute ist {result.get('today', today.strftime('%d.%m.%Y'))}. Praxiskalender {span} · {who} · " + (
            "nur freie Plätze" if nur_frei else "freie und belegte Einträge")
        rows = result.get("rows") or []
        if not rows:
            return head + ": keine passenden Einträge."
        lines = []
        if nur_frei:
            # One line per day, doctor and raster: easier to read and to answer from than one line per slot.
            groups = {}
            for r in rows:
                groups.setdefault((r["day"], r["doctor"], r["raster"]), []).append(r["time"])
            for (day, doctor, raster), times in groups.items():
                lines.append(f"{day} · {doctor} · {raster} {', '.join(times)}")
        else:
            for r in rows:
                lines.append(f"{r['day']} {r['time']} ({r['minutes']} Min) · {r['doctor']} · {r['title'] or '(ohne Titel)'} · "
                             + ("frei" if r["free"] else "belegt"))
        note = " Die Liste ist gekürzt, bitte den Zeitraum verkleinern." if result.get("truncated") else ""
        return (f"{head}, {result['count']} Einträge.{note}\n" + "\n".join(lines)
                + "\nNur diese Einträge wiedergeben, nichts ergänzen. Reservieren und buchen geht nur im Mailarbeitsplatz.")
