#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Einmal-Werkzeug: Serientermine eines Nextcloud-Kalenders in Einzeltermine auflösen.

Je Quellkalender entsteht ein NEUER Kalender. Der alte Kalender wird nur gelesen
und nie verändert oder gelöscht.

  - Einzeltermine und Aufgaben werden unverändert kopiert.
  - Serien werden von Nextcloud selbst aufgelöst (CalDAV "expand"); jedes
    Vorkommen wird als eigener Termin mit allen Angaben eingetragen
    (Titel, Beschreibung, Ort, Farbe, Kategorien, Erinnerungen, Status ...).
  - Name, Farbe, Reihenfolge, Beschreibung und Zeitzone des Kalenders werden übernommen.

Zweite Aufgabe (--neues-jahr): einen leeren Kalender für ein neues Jahr anlegen.
Vorlage ist eine Woche eines bestehenden Kalenders (--vorlage JJJJ-KW). Übernommen
wird nur das Raster: Einträge, deren Titel ein freies Raster aus der Konfiguration
ist; vergebene Plätze werden auf ihr Raster zurückgesetzt, Namen und Notizen
entfallen. Dazu feste Einträge nach Titel (--auch, Standard: Mittagspause).
Feiertage und die Tage aus extra_holidays bleiben frei.

Ohne --ausfuehren wird nur gezählt und nichts geschrieben.

Aufruf:
  sudo python3 serien-aufloesen.py                      Kalender auflisten
  sudo python3 serien-aufloesen.py NAME [NAME ...]      Probelauf
  sudo python3 serien-aufloesen.py --ausfuehren NAME    schreiben
  sudo python3 serien-aufloesen.py --neues-jahr 2028 --vorlage 2027-11 NAME

Ausgegeben werden nur Zahlen und technische Kennungen, nie Titel oder Namen.
"""
import argparse
import base64
import hashlib
import json
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

D = "{DAV:}"
C = "{urn:ietf:params:xml:ns:caldav}"
A = "{http://apple.com/ns/ical/}"
NS = 'xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav" xmlns:a="http://apple.com/ns/ical/"'
SERIES_PROPS = ("RRULE", "RDATE", "EXDATE", "EXRULE", "RECURRENCE-ID")
UID_SPACE = uuid.UUID("6a1f0c52-7d0e-4c3b-9d7e-2f5b8c1a9e40")


# ---------------------------------------------------------------- iCalendar als Text

def unfold(text):
    """Zeilen eines iCalendar-Textes, Fortsetzungszeilen zusammengefügt."""
    lines = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        elif raw:
            lines.append(raw)
    return lines


def fold(lines):
    """Zeilen auf 75 Oktette umbrechen, ohne ein UTF-8-Zeichen zu teilen."""
    out = []
    for line in lines:
        data = line.encode("utf-8")
        first = True
        while True:
            limit = 75 if first else 74
            if len(data) <= limit:
                out.append((b"" if first else b" ") + data)
                break
            cut = limit
            while cut > 0 and (data[cut] & 0xC0) == 0x80:
                cut -= 1
            out.append((b"" if first else b" ") + data[:cut])
            data = data[cut:]
            first = False
    return b"\r\n".join(out) + b"\r\n"


def name_of(line):
    return re.split(r"[;:]", line, maxsplit=1)[0].upper()


def split_line(line):
    """(NAME, Parameterteil mit führendem ';' oder '', Wert). Beachtet Anführungszeichen."""
    quoted = False
    for pos, ch in enumerate(line):
        if ch == '"':
            quoted = not quoted
        elif ch == ":" and not quoted:
            head, value = line[:pos], line[pos + 1:]
            name, _, params = head.partition(";")
            return name.upper(), (";" + params if params else ""), value
    return line.upper(), "", ""


def components(lines):
    """Zerlegt den Inhalt eines VCALENDAR: (Kopfzeilen, [(Typ, Zeilen des Blocks)])."""
    head, blocks, depth, current = [], [], 0, None
    for line in lines:
        upper = line.upper()
        if upper.startswith("BEGIN:"):
            depth += 1
            if depth == 2:
                current = [line]
                continue
        elif upper.startswith("END:"):
            depth -= 1
            if depth == 1 and current is not None:
                current.append(line)
                blocks.append((current[0][6:].strip().upper(), current))
                current = None
                continue
        if depth >= 2 and current is not None:
            current.append(line)
        elif depth == 1 and not upper.startswith("BEGIN:"):
            head.append(line)
    return head, blocks


def own_lines(block):
    """Zeilen, die dem Block selbst gehören (nicht einem Unterblock wie VALARM)."""
    depth = 0
    for index, line in enumerate(block):
        upper = line.upper()
        if upper.startswith("BEGIN:"):
            depth += 1
            continue
        if upper.startswith("END:"):
            depth -= 1
            continue
        if depth == 1:
            yield index, line


def prop(block, wanted):
    for _, line in own_lines(block):
        name, params, value = split_line(line)
        if name == wanted:
            return params, value
    return None


def to_local(value, zone):
    """'20261127T073000Z' -> '20261127T083000' in der angegebenen Zone."""
    stamp = datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    return stamp.astimezone(zone).strftime("%Y%m%dT%H%M%S")


def single_event(block, new_uid, tzid):
    """Aus einem von Nextcloud aufgelösten Vorkommen wird ein eigenständiger Termin."""
    zone = None
    if tzid:
        try:
            zone = ZoneInfo(tzid)
        except Exception:
            zone = None
    mine = dict(own_lines(block))
    out = []
    for index, line in enumerate(block):
        if index not in mine:
            out.append(line)
            continue
        name, params, value = split_line(line)
        if name in SERIES_PROPS:
            continue
        if name == "UID":
            out.append("UID:" + new_uid)
            continue
        if name in ("DTSTART", "DTEND") and zone and re.fullmatch(r"\d{8}T\d{6}Z", value):
            out.append(f"{name};TZID={tzid}:{to_local(value, zone)}")
            continue
        out.append(line)
    return out, bool(tzid and zone is None)


def calendar_text(head, timezones, event):
    lines = ["BEGIN:VCALENDAR"]
    seen = {name_of(line) for line in head}
    lines += [line for line in head if name_of(line) not in ("METHOD",)]
    if "VERSION" not in seen:
        lines.append("VERSION:2.0")
    if "PRODID" not in seen:
        lines.append("PRODID:-//nopaper//serien-aufloesen//DE")
    for block in timezones:
        lines += block
    lines += event
    lines.append("END:VCALENDAR")
    return fold(lines)


def silence_attendees(lines):
    """SCHEDULE-AGENT=CLIENT: Der Server soll für diesen Eintrag keine Einladungen verschicken."""
    out = []
    for line in lines:
        name, params, value = split_line(line)
        if name in ("ATTENDEE", "ORGANIZER") and "SCHEDULE-AGENT=" not in params.upper():
            line = f"{name}{params};SCHEDULE-AGENT=CLIENT:{value}"
        out.append(line)
    return out


# ---------------------------------------------------------------- CalDAV

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class Dav:
    def __init__(self, home, user, password, ca=None):
        self.home = home.rstrip("/") + "/"
        parsed = urllib.parse.urlsplit(self.home)
        if parsed.scheme != "https" or not parsed.hostname:
            raise SystemExit("calendar_home muss eine https-Adresse sein.")
        self.base = parsed.scheme + "://" + parsed.netloc
        token = base64.b64encode(f"{user}:{password}".encode()).decode()
        self.headers = {"Authorization": "Basic " + token}
        self.http = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), NoRedirect(),
            urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=ca)))

    def call(self, method, url, body=None, headers=None, ok=(200, 201, 204, 207)):
        request = urllib.request.Request(url, data=body, method=method, headers={**self.headers, **(headers or {})})
        try:
            with self.http.open(request, timeout=120) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            error.read()
            if error.code in ok:
                return error.code, b""
            return error.code, None

    def xml(self, method, url, body, depth):
        status, raw = self.call(method, url, body.encode(), {"Depth": depth, "Content-Type": "application/xml; charset=utf-8"})
        if raw is None or status != 207:
            raise SystemExit(f"{method} fehlgeschlagen (HTTP {status}).")
        return ET.fromstring(raw)

    def absolute(self, href):
        return urllib.parse.urljoin(self.base, href)

    def calendars(self):
        body = (f'<d:propfind {NS}><d:prop><d:resourcetype/><d:displayname/><a:calendar-color/><a:calendar-order/>'
                '<c:calendar-description/><c:calendar-timezone/><c:supported-calendar-component-set/></d:prop></d:propfind>')
        found = []
        for row in self.xml("PROPFIND", self.home, body, "1").findall(D + "response"):
            url = self.absolute(row.findtext(D + "href") or "")
            if url.rstrip("/") == self.home.rstrip("/"):
                continue
            for ps in row.findall(D + "propstat"):
                if " 200 " not in (ps.findtext(D + "status") or ""):
                    continue
                p = ps.find(D + "prop")
                kind = p.find(D + "resourcetype")
                if kind is None or kind.find(C + "calendar") is None:
                    continue
                comps = [c.get("name") for c in p.findall(C + "supported-calendar-component-set/" + C + "comp")]
                found.append({
                    "slug": urllib.parse.unquote(url.rstrip("/").rsplit("/", 1)[1]), "url": url.rstrip("/") + "/",
                    "name": p.findtext(D + "displayname") or "", "color": p.findtext(A + "calendar-color"),
                    "order": p.findtext(A + "calendar-order"), "description": p.findtext(C + "calendar-description"),
                    "timezone": p.findtext(C + "calendar-timezone"), "components": comps or ["VEVENT"]})
        return found

    def hrefs(self, url):
        body = f'<d:propfind {NS}><d:prop><d:getetag/></d:prop></d:propfind>'
        out = []
        for row in self.xml("PROPFIND", url, body, "1").findall(D + "response"):
            href = self.absolute(row.findtext(D + "href") or "")
            if href.rstrip("/") != url.rstrip("/") and not href.endswith("/"):
                out.append(href)
        return out

    def objects(self, url, hrefs, step=50):
        """Rohdaten aller Einträge: {href: Text}."""
        out = {}
        for start in range(0, len(hrefs), step):
            part = hrefs[start:start + step]
            listed = "".join(f"<d:href>{escape(urllib.parse.urlsplit(h).path)}</d:href>" for h in part)
            body = f'<c:calendar-multiget {NS}><d:prop><c:calendar-data/></d:prop>{listed}</c:calendar-multiget>'
            for row in self.xml("REPORT", url, body, "1").findall(D + "response"):
                data = row.findtext(D + "propstat/" + D + "prop/" + C + "calendar-data")
                if data:
                    out[self.absolute(row.findtext(D + "href"))] = data
        missing = [h for h in hrefs if h not in out]
        if missing:
            raise SystemExit(f"{len(missing)} Einträge konnten nicht gelesen werden. Abbruch, nichts geschrieben.")
        return out

    def expanded(self, url, start, end):
        """Von Nextcloud aufgelöste Termine im Zeitraum: Liste von VEVENT-Blöcken."""
        limits = f'start="{start:%Y%m%dT000000Z}" end="{end:%Y%m%dT000000Z}"'
        body = (f'<c:calendar-query {NS}><d:prop><c:calendar-data><c:expand {limits}/></c:calendar-data></d:prop>'
                f'<c:filter><c:comp-filter name="VCALENDAR"><c:comp-filter name="VEVENT"><c:time-range {limits}/>'
                '</c:comp-filter></c:comp-filter></c:filter></c:calendar-query>')
        events = []
        for row in self.xml("REPORT", url, body, "1").findall(D + "response"):
            data = row.findtext(D + "propstat/" + D + "prop/" + C + "calendar-data")
            if not data:
                raise SystemExit("Nextcloud hat einen Eintrag ohne Daten geliefert. Abbruch.")
            _, blocks = components(unfold(data))
            events += [block for kind, block in blocks if kind == "VEVENT"]
        return events

    def create(self, url, source, name):
        props = [f"<d:displayname>{escape(name)}</d:displayname>"]
        if source["color"]:
            props.append(f"<a:calendar-color>{escape(source['color'])}</a:calendar-color>")
        if source["order"]:
            props.append(f"<a:calendar-order>{escape(source['order'])}</a:calendar-order>")
        if source["description"]:
            props.append(f"<c:calendar-description>{escape(source['description'])}</c:calendar-description>")
        if source["timezone"]:
            props.append(f"<c:calendar-timezone>{escape(source['timezone'])}</c:calendar-timezone>")
        comps = "".join(f'<c:comp name="{escape(c)}"/>' for c in source["components"])
        props.append(f"<c:supported-calendar-component-set>{comps}</c:supported-calendar-component-set>")
        body = f'<c:mkcalendar {NS}><d:set><d:prop>{"".join(props)}</d:prop></d:set></c:mkcalendar>'
        status, raw = self.call("MKCALENDAR", url, body.encode(), {"Content-Type": "application/xml; charset=utf-8"})
        if raw is None and status != 405:  # 405: Der Kalender besteht schon (Wiederholung eines Laufs).
            raise SystemExit(f"Neuer Kalender konnte nicht angelegt werden (HTTP {status}).")

    def put(self, url, data):
        """'neu', 'vorhanden' oder der HTTP-Status als Zahl."""
        status, raw = self.call("PUT", url, data, {"Content-Type": "text/calendar; charset=utf-8", "If-None-Match": "*"})
        if raw is not None:
            return "neu"
        return "vorhanden" if status == 412 else status


# ---------------------------------------------------------------- Ablauf

def day(value):
    match = re.match(r"(\d{4})(\d{2})(\d{2})", value or "")
    return date(int(match[1]), int(match[2]), int(match[3])) if match else None


def months(start, end):
    current = date(start.year, start.month, 1)
    while current < end:
        following = date(current.year + (current.month == 12), current.month % 12 + 1, 1)
        yield current, following
        current = following


def analyse(raw):
    """Teilt die Rohdaten in unverändert zu kopierende Einträge und Serien."""
    plain, series, first, last, open_end = {}, {}, None, None, 0
    for href, text in raw.items():
        head, blocks = components(unfold(text))
        events = [block for kind, block in blocks if kind == "VEVENT"]
        recurring = any(prop(block, name) for block in events for name in ("RRULE", "RDATE", "RECURRENCE-ID"))
        if not recurring:
            plain[href] = text
            continue
        zones = [block for kind, block in blocks if kind == "VTIMEZONE"]
        for block in events:
            uid = (prop(block, "UID") or ("", ""))[1]
            entry = series.setdefault(uid, {"head": head, "zones": zones, "tzid": None, "href": href})
            start = prop(block, "DTSTART")
            if start:
                when = day(start[1])
                if when:
                    first = min(first or when, when)
                    last = max(last or when, when)
                tzid = re.search(r"TZID=([^;:]+)", start[0], re.I)
                if tzid and (prop(block, "RECURRENCE-ID") is None or not entry["tzid"]):
                    entry["tzid"] = tzid[1].strip('"')
            rule = prop(block, "RRULE")
            if rule:
                until = re.search(r"UNTIL=(\d{8})", rule[1], re.I)
                if until:
                    last = max(last or day(until[1]), day(until[1]))
                else:
                    open_end += 1
    return plain, series, first, last, open_end


def instance_key(block):
    uid = (prop(block, "UID") or ("", ""))[1]
    rid = prop(block, "RECURRENCE-ID") or prop(block, "DTSTART") or ("", "")
    return uid, rid[1]


def has_attendees(lines):
    return any(name_of(line) == "ATTENDEE" for line in lines)


def convert(dav, source, args, horizon):
    target_slug = source["slug"] + args.endung
    target_url = dav.home + urllib.parse.quote(target_slug) + "/"
    print(f"\n== {source['name']} ({source['slug']}) -> {source['name'] + args.namenszusatz} ({target_slug})")
    hrefs = dav.hrefs(source["url"])
    raw = dav.objects(source["url"], hrefs)
    plain, series, first, last, open_end = analyse(raw)
    print(f"   Einträge im alten Kalender: {len(raw)} | davon Serien: {len(raw) - len(plain)} | Einzeltermine/Aufgaben: {len(plain)}")

    work, skipped, bad_zone = [], 0, 0
    for href, text in plain.items():
        lines = unfold(text)
        if has_attendees(lines):
            if not args.mit_teilnehmern:
                skipped += 1
                continue
            lines = silence_attendees(lines)
        work.append((target_url + href.rsplit("/", 1)[1], fold(lines), True))

    instances = {}
    if series:
        end = last + timedelta(days=32)
        if open_end:
            end = max(end, horizon)
        if args.ab:
            first = max(first, args.ab)
        if open_end:
            print(f"   {open_end} Serien ohne Enddatum: aufgelöst bis {horizon - timedelta(days=1):%d.%m.%Y} (--bis ändert das)")
        for start, stop in months(first, end):
            for block in dav.expanded(source["url"], start, stop):
                uid, rid = instance_key(block)
                if uid in series:
                    instances.setdefault((uid, rid), block)
        for (uid, rid), block in sorted(instances.items()):
            info = series[uid]
            new_uid = str(uuid.uuid5(UID_SPACE, uid + "|" + rid))
            event, unknown_zone = single_event(block, new_uid, info["tzid"])
            bad_zone += unknown_zone
            if has_attendees(event):
                if not args.mit_teilnehmern:
                    skipped += 1
                    continue
                event = silence_attendees(event)
            name = hashlib.sha1((uid + "|" + rid).encode()).hexdigest() + ".ics"
            work.append((target_url + name, calendar_text(info["head"], info["zones"], event), False))
        empty = sum(1 for uid in series if not any(key[0] == uid for key in instances))
        print(f"   Serien aufgelöst in {len(instances)} Einzeltermine (ab {first:%d.%m.%Y})")
        if empty:
            print(f"   Hinweis: {empty} Serien haben in diesem Zeitraum kein einziges Vorkommen.")
    if bad_zone:
        print(f"   Hinweis: {bad_zone} Termine mit unbekannter Zeitzone bleiben in UTC (gleicher Zeitpunkt).")
    if skipped:
        print(f"   NICHT übernommen: {skipped} Einträge mit eingeladenen Teilnehmern (siehe --mit-teilnehmern).")
    print(f"   Zu schreiben: {len(work)} Einträge")
    if not args.ausfuehren:
        print("   Probelauf: nichts angelegt, nichts geschrieben.")
        return True

    dav.create(target_url, source, source["name"] + args.namenszusatz)
    return write_all(dav, target_url, work, args.parallel)


def write_all(dav, target_url, work, parallel):
    counts = {"neu": 0, "vorhanden": 0, "fehler": 0}
    failed = []

    def write(item):
        url, data, verbatim = item
        result = dav.put(url, data)
        if result not in ("neu", "vorhanden") and verbatim:
            # Gleiche UID im selben Konto abgelehnt: derselbe Eintrag mit neuer UID.
            lines = [("UID:" + str(uuid.uuid5(UID_SPACE, url)) if name_of(l) == "UID" else l) for l in unfold(data.decode("utf-8"))]
            result = dav.put(url, fold(lines))
        return url, result

    with ThreadPoolExecutor(max_workers=parallel) as pool:
        for number, (url, result) in enumerate(pool.map(write, work), 1):
            if result in counts:
                counts[result] += 1
            else:
                counts["fehler"] += 1
                failed.append((url.rsplit("/", 1)[1], result))
            if number % 500 == 0:
                print(f"   … {number} von {len(work)}", flush=True)
    present = len(dav.hrefs(target_url))
    print(f"   Geschrieben: {counts['neu']} | schon vorhanden: {counts['vorhanden']} | Fehler: {counts['fehler']}")
    print(f"   Im neuen Kalender: {present} Einträge, erwartet: {len(work)}")
    for name, status in failed[:20]:
        print(f"   Fehler HTTP {status}: {name}")
    return counts["fehler"] == 0 and present == len(work)


# ---------------------------------------------------------------- neues Jahr

def norm(text):
    return " ".join(str(text).strip().casefold().split())


def unescape(value):
    return re.sub(r"\\([\\;,nN])", lambda m: "\n" if m[1] in "nN" else m[1], value)


def holidays(year, extra=()):
    """Bundesweite Feiertage und die Tage aus extra_holidays ("JJJJ-MM-TT" oder jährlich "MM-TT")."""
    a, b, c = year % 19, year // 100, year % 100
    d, e, f = b // 4, b % 4, (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    easter = date(year, (h + l - 7 * m + 114) // 31, (h + l - 7 * m + 114) % 31 + 1)
    days = {date(year, 1, 1), easter - timedelta(days=2), easter + timedelta(days=1), date(year, 5, 1),
            easter + timedelta(days=39), easter + timedelta(days=50), date(year, 10, 3), date(year, 12, 25), date(year, 12, 26)}
    for item in extra:
        try:
            days.add(date.fromisoformat(item if len(item) > 5 else f"{year}-{item}"))
        except (ValueError, TypeError):
            pass
    return days


def wall_time(params, value, zone):
    """Ortszeit eines DTSTART/DTEND als datetime ohne Zone; None bei ganztägigen Einträgen."""
    if re.fullmatch(r"\d{8}T\d{6}Z", value):
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).astimezone(zone).replace(tzinfo=None)
    if re.fullmatch(r"\d{8}T\d{6}", value):
        stamp = datetime.strptime(value, "%Y%m%dT%H%M%S")
        tzid = re.search(r"TZID=([^;:]+)", params, re.I)
        if tzid:
            try:
                return stamp.replace(tzinfo=ZoneInfo(tzid[1].strip('"'))).astimezone(zone).replace(tzinfo=None)
            except Exception:
                return None
        return stamp
    return None


PERSONAL = ("DESCRIPTION", "LOCATION", "ATTENDEE", "ORGANIZER", "CONTACT", "COMMENT", "ATTACH", "URL", "GEO")


def raster_of(title, free):
    """(Rastertitel, vergeben?) oder None. 'free' ist nach Länge absteigend sortiert."""
    text = norm(title)
    for original in free:
        key = norm(original)
        if text == key:
            return original, False
        if text.startswith(key) or (text.startswith("reserviert") and text.endswith(key)):
            return original, True
    return None


def template_entry(block, zone, free, also):
    """Aus einem Termin der Vorlagewoche: (Wochentag, Beginn, Dauer, Zeilen ohne Zeitangaben, Art) oder None."""
    start, end, title = prop(block, "DTSTART"), prop(block, "DTEND"), prop(block, "SUMMARY")
    if not start or not end:
        return None
    begin, finish = wall_time(*start, zone), wall_time(*end, zone)
    if begin is None or finish is None or finish <= begin or finish - begin > timedelta(hours=12):
        return None
    summary = unescape(title[1]) if title else ""
    raster = raster_of(summary, free)
    if raster is None and norm(summary) not in also:
        return None
    booked = bool(raster and raster[1])
    mine = dict(own_lines(block))
    lines, depth = [], 0
    for index, line in enumerate(block):
        upper = line.upper()
        if index not in mine:
            if upper.startswith("BEGIN:"):
                depth += 1
            if not (booked and depth > 1):      # Erinnerungen eines vergebenen Platzes entfallen
                lines.append(line)
            if upper.startswith("END:"):
                depth -= 1
            continue
        name = name_of(line)
        if name in SERIES_PROPS or name in ("DTSTART", "DTEND", "DURATION", "UID"):
            continue
        if booked and (name in PERSONAL or name.startswith("X-")):
            continue
        if booked and name == "SUMMARY":
            line = "SUMMARY:" + re.sub(r"([\\;,])", r"\\\1", raster[0])
        lines.append(line)
    kind = "fest" if raster is None else ("vergeben" if booked else "frei")
    return begin.weekday(), begin.time(), finish - begin, lines, kind


def new_year(dav, source, args, cfg, available):
    year = args.neues_jahr
    match = re.fullmatch(r"(\d{4})-(?:KW|W)?(\d{1,2})", args.vorlage or "", re.I)
    if not match:
        raise SystemExit("--vorlage als JJJJ-KW angeben, z. B. 2027-11.")
    monday = date.fromisocalendar(int(match[1]), int(match[2]), 1)
    swap = lambda text: text.replace(match[1], str(year)) if match[1] in text else f"{text}_{year}"
    slug, name = args.zielkennung or swap(source["slug"]), args.zielname or swap(source["name"])
    if not re.fullmatch(r"[\w.-]+", slug) or slug == source["slug"]:
        raise SystemExit("Kennung des neuen Kalenders ungültig oder gleich der Vorlage (--zielkennung).")
    target_url = dav.home + urllib.parse.quote(slug) + "/"
    zone_name = cfg.get("timezone", "Europe/Berlin")
    zone = ZoneInfo(zone_name)
    free = sorted(cfg.get("free_titles", []), key=len, reverse=True)
    also = {norm(item) for item in args.auch}
    print(f"\n== Vorlage {source['name']} ({source['slug']}), Woche ab {monday:%d.%m.%Y} -> {name} ({slug}), Jahr {year}")
    if any(c["slug"] == slug for c in available):
        print("   Hinweis: Der Zielkalender besteht schon; es wird nur Fehlendes ergänzt.")

    blocks = dav.expanded(source["url"], monday, monday + timedelta(days=7))
    week, seen, left = {}, set(), 0
    kinds = {"frei": 0, "vergeben": 0, "fest": 0}
    for block in blocks:
        key = instance_key(block)
        if key in seen:
            continue
        seen.add(key)
        entry = template_entry(block, zone, free, also)
        if entry is None:
            left += 1
            continue
        weekday, begin, length, lines, kind = entry
        kinds[kind] += 1
        week.setdefault(weekday, []).append((begin, length, lines, key))
    names = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
    print("   Vorlage je Wochentag: " + "  ".join(f"{names[d]} {len(week.get(d, []))}" for d in range(7)))
    print(f"   davon freie Raster: {kinds['frei']} | vergebene Plätze, auf Raster zurückgesetzt: {kinds['vergeben']} | feste Einträge: {kinds['fest']}")
    if left:
        print(f"   Nicht übernommen: {left} sonstige Einträge der Vorlagewoche (ganztägig, anderer Titel; siehe --auch).")
    if not week:
        print("   Die Vorlagewoche enthält kein Raster. Nichts zu tun.")
        return False

    zones = []
    for href in dav.hrefs(source["url"])[:20]:
        for kind, block in components(unfold(next(iter(dav.objects(source["url"], [href]).values()))))[1]:
            if kind == "VTIMEZONE" and (prop(block, "TZID") or ("", ""))[1] == zone_name:
                zones = [block]
        if zones:
            break
    head = ["VERSION:2.0", "PRODID:-//nopaper//serien-aufloesen//DE", "CALSCALE:GREGORIAN"]
    free_days = holidays(year, tuple(cfg.get("extra_holidays", [])))
    work, skipped_days, current = [], 0, date(year, 1, 1)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    while current.year == year:
        entries = week.get(current.weekday(), [])
        if entries and current in free_days:
            skipped_days += 1
        elif entries:
            for begin, length, lines, key in entries:
                start = datetime.combine(current, begin)
                ident = f"{slug}|{current.isoformat()}|{key[0]}|{key[1]}"
                times = [f"UID:{uuid.uuid5(UID_SPACE, ident)}",
                         f"DTSTART;TZID={zone_name}:{start:%Y%m%dT%H%M%S}",
                         f"DTEND;TZID={zone_name}:{start + length:%Y%m%dT%H%M%S}"]
                body = [l for l in lines[1:-1] if name_of(l) not in ("DTSTAMP", "CREATED", "LAST-MODIFIED", "SEQUENCE")]
                event = [lines[0], *times, f"DTSTAMP:{stamp}", *body, lines[-1]]
                work.append((target_url + hashlib.sha1(ident.encode()).hexdigest() + ".ics", calendar_text(head, zones, event), False))
        current += timedelta(days=1)
    print(f"   Feiertage und freie Tage ausgelassen: {skipped_days} | zu schreiben: {len(work)} Einträge")
    if not args.ausfuehren:
        print("   Probelauf: nichts angelegt, nichts geschrieben.")
        return True
    dav.create(target_url, source, name)
    return write_all(dav, target_url, work, args.parallel)


def main():
    parser = argparse.ArgumentParser(description="Serientermine in Einzeltermine auflösen (neuer Kalender, alter bleibt unverändert).")
    parser.add_argument("kalender", nargs="*", help="Name oder Kennung der Kalender; ohne Angabe werden sie nur aufgelistet")
    parser.add_argument("--config", default="/etc/nopaper-mail/kalender.json")
    parser.add_argument("--ausfuehren", action="store_true", help="wirklich anlegen und schreiben")
    parser.add_argument("--endung", default="-einzeln", help="Anhang an die Kennung des neuen Kalenders (Standard: -einzeln)")
    parser.add_argument("--namenszusatz", default=" neu", help="Anhang an den Anzeigenamen (Standard: ' neu')")
    parser.add_argument("--ab", type=date.fromisoformat, help="Serien erst ab diesem Tag auflösen (JJJJ-MM-TT); Standard: von Anfang an")
    parser.add_argument("--bis", type=date.fromisoformat, help="Serien ohne Enddatum bis zu diesem Tag auflösen; Standard: Ende des nächsten Jahres")
    parser.add_argument("--mit-teilnehmern", action="store_true", help="auch Einträge mit eingeladenen Teilnehmern übernehmen")
    parser.add_argument("--neues-jahr", type=int, metavar="JAHR", help="leeren Kalender für dieses Jahr aus einer Vorlagewoche anlegen")
    parser.add_argument("--vorlage", metavar="JJJJ-KW", help="Vorlagewoche für --neues-jahr, z. B. 2027-11")
    parser.add_argument("--auch", action="append", default=None, metavar="TITEL", help="fester Eintrag, der mit ins neue Jahr soll (mehrfach möglich; Standard: Mittagspause)")
    parser.add_argument("--zielkennung", help="Kennung des neuen Jahreskalenders (Standard: Jahreszahl der Vorlage ersetzt)")
    parser.add_argument("--zielname", help="Anzeigename des neuen Jahreskalenders")
    parser.add_argument("--parallel", type=int, default=4, choices=range(1, 9), metavar="1-8")
    args = parser.parse_args()
    if not re.fullmatch(r"[\w.-]+", args.endung):
        raise SystemExit("--endung darf nur Buchstaben, Ziffern, Punkt, Binde- und Unterstrich enthalten.")

    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    password = Path(cfg["password_file"]).read_text().rstrip("\r\n")
    dav = Dav(cfg["calendar_home"], cfg["username"], password)
    available = dav.calendars()
    if not args.kalender:
        print("Kalender in diesem Konto (Name | Kennung):")
        for item in available:
            print(f"  {item['name']} | {item['slug']}")
        print("\nAufruf mit einem oder mehreren Namen, z. B.:  serien-aufloesen.py " + (available[0]["slug"] if available else "NAME"))
        return 0
    chosen = []
    for wanted in args.kalender:
        match = [c for c in available if wanted in (c["slug"], c["name"])]
        if len(match) != 1:
            raise SystemExit(f"Kalender nicht eindeutig gefunden: {wanted}")
        if any(c["slug"] == match[0]["slug"] + args.endung for c in available) and not args.ausfuehren:
            print(f"Hinweis: Der Zielkalender zu {wanted} besteht schon; ein Lauf mit --ausfuehren ergänzt nur Fehlendes.")
        chosen.append(match[0])
    if args.neues_jahr:
        if not 2000 < args.neues_jahr < 2100:
            raise SystemExit("--neues-jahr: Jahreszahl prüfen.")
        if len(chosen) > 1 and (args.zielkennung or args.zielname):
            raise SystemExit("--zielkennung und --zielname nur mit einem einzelnen Kalender.")
        args.auch = args.auch if args.auch is not None else ["Mittagspause"]
        results = [new_year(dav, source, args, cfg, available) for source in chosen]
        if args.ausfuehren:
            print("\nFertig." if all(results) else "\nNICHT vollständig – Meldungen oben prüfen, der Lauf kann wiederholt werden.")
        return 0 if all(results) else 1
    horizon = args.bis or date(date.today().year + 1, 12, 31)
    results = [convert(dav, source, args, horizon + timedelta(days=1)) for source in chosen]
    if args.ausfuehren:
        print("\nFertig." if all(results) else "\nNICHT vollständig – Meldungen oben prüfen, der Lauf kann wiederholt werden.")
        print("Die alten Kalender sind unverändert.")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
