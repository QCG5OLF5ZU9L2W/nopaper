"""CalDAV availability with explicit calendars and conditional updates."""
import base64
import copy
import hashlib
import json
import re
import ssl
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, time, timedelta, timezone
from icalendar import Calendar
from zoneinfo import ZoneInfo

UTC=timezone.utc
D="{DAV:}"
C="{urn:ietf:params:xml:ns:caldav}"

def norm(value):
    return " ".join(str(value).strip().casefold().split())

def instant(value,zone):
    if not isinstance(value,datetime):value=datetime.combine(value,time.min)
    if value.tzinfo is None:value=value.replace(tzinfo=zone)
    return int(value.timestamp())

def interval(event,zone):
    start=event.decoded("DTSTART")
    if "DTEND" in event:end=event.decoded("DTEND")
    elif "DURATION" in event:end=start+event.decoded("DURATION")
    else:end=start if isinstance(start,datetime) else start+timedelta(days=1)
    return instant(start,zone),instant(end,zone)

from functools import lru_cache

@lru_cache(maxsize=16)
def holidays(year,extra=()):
    """Nationwide German public holidays plus configured days ("JJJJ-MM-TT" or yearly "MM-TT")."""
    a,b,c=year%19,year//100,year%100
    d,e,f=b//4,b%4,(b+8)//25
    g=(b-f+1)//3
    h=(19*a+b-d-g+15)%30
    i,k=c//4,c%4
    l=(32+2*e+2*i-h-k)%7
    m=(a+11*h+22*l)//451
    easter=date(year,(h+l-7*m+114)//31,(h+l-7*m+114)%31+1)
    days={date(year,1,1),easter-timedelta(days=2),easter+timedelta(days=1),date(year,5,1),easter+timedelta(days=39),
          easter+timedelta(days=50),date(year,10,3),date(year,12,25),date(year,12,26)}
    for item in extra:
        try:days.add(date.fromisoformat(item if len(item)>5 else f"{year}-{item}"))
        except (ValueError,TypeError):pass
    return days

MARKED=r"(?i)\b(?:absage|abgesagt|storno|storniert)\b"

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None

class DAV:
    def __init__(self,cfg,password):
        self.cfg=cfg
        self.zone=ZoneInfo(cfg["timezone"])
        self.home=cfg["calendar_home"].rstrip("/")+"/"
        parsed=urllib.parse.urlsplit(self.home)
        if parsed.scheme!="https" or not parsed.hostname or parsed.username or parsed.query or parsed.fragment:
            raise ValueError("Ungültige HTTPS-Kalenderadresse.")
        self.base=parsed.scheme+"://"+parsed.netloc
        self.calendars={}
        for entry in cfg["calendars"]:
            name=entry["path"]
            if not re.fullmatch(r"[\w.-]+",name) or name in (".","..") or name in self.calendars:
                raise ValueError("Ungültiger oder doppelter Kalenderpfad.")
            self.calendars[name]=entry
        auth=base64.b64encode((cfg["username"]+":"+password).encode()).decode()
        self.headers={"Authorization":"Basic "+auth}
        self.http=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect(),
            urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    def request(self,method,url,body=None,headers=None):
        req=urllib.request.Request(url,data=body,method=method,headers={**self.headers,**(headers or {})})
        with self.http.open(req,timeout=25) as response:
            raw=response.read(20*1024*1024+1)
            if len(raw)>20*1024*1024:raise ValueError("Kalenderantwort zu groß.")
            return raw,response.headers
    def collection(self,name):
        if name not in self.calendars:raise ValueError("Kalender nicht freigegeben.")
        return self.home+urllib.parse.quote(name)+"/"
    def url(self,name,href):
        url=urllib.parse.urljoin(self.base,href)
        prefix=self.collection(name)
        tail=urllib.parse.unquote(url[len(prefix):]) if url.startswith(prefix) else ""
        if not tail or "/" in tail or tail in (".","..") or any(ch in tail for ch in "?#\\\r\n"):
            raise ValueError("Unerwarteter CalDAV-Ressourcenpfad.")
        return url
    def scan(self,start,end,held=None):
        stamp=lambda t: datetime.fromtimestamp(t,UTC).strftime("%Y%m%dT%H%M%SZ")
        limits=f'start="{stamp(start)}" end="{stamp(end)}"'
        xml=f'<c:calendar-query xmlns:d="DAV:" xmlns:c="{C[1:-1]}"><d:prop><d:getetag/><c:calendar-data><c:expand {limits}/></c:calendar-data></d:prop><c:filter><c:comp-filter name="VCALENDAR"><c:comp-filter name="VEVENT"><c:time-range {limits}/></c:comp-filter></c:comp-filter></c:filter></c:calendar-query>'
        blocked=[]; busy={}; slots=[]
        for name,entry in self.calendars.items():
            busy[name]=[]
            raw,_=self.request("REPORT",self.collection(name),xml.encode(),{"Depth":"1","Content-Type":"application/xml"})
            root=ET.fromstring(raw)
            if root.tag!=D+"multistatus":raise ValueError("Ungültige CalDAV-Antwort.")
            for row in root.findall(D+"response"):
                ok=[ps for ps in row.findall(D+"propstat") if " 200 " in (ps.findtext(D+"status") or "")]
                data=next((ps.findtext(D+"prop/"+C+"calendar-data") for ps in ok if ps.findtext(D+"prop/"+C+"calendar-data")),None)
                if not data:raise ValueError("Kalendereintrag unvollständig. Keine verlässliche Verfügbarkeit.")
                href=row.findtext(D+"href");self.url(name,href)
                for ev in Calendar.from_ical(data).walk("VEVENT"):
                    if norm(ev.get("STATUS",""))=="cancelled":continue
                    a,b=interval(ev,self.zone)
                    title=norm(ev.get("SUMMARY",""))
                    notice=title+" "+norm(ev.get("DESCRIPTION",""))
                    if re.search(r"\bnichts\s+eintragen\b",notice) or any(re.search(rule,notice,re.I) for rule in self.cfg["global_blocks"]):
                        blocked.append((a,b));continue
                    if entry.get("blocks_only"):continue
                    if any(k in ev for k in ("RRULE","RDATE")):raise ValueError("Server hat Serie nicht expandiert.")
                    owned=held and str(ev.get("X-JOHM-CODE",""))==held
                    if (not owned and title not in {norm(k) for k in self.cfg["free_titles"]}) or not 300<=b-a<=14400 or ev.get("ATTENDEE") or ev.get("ORGANIZER"):
                        busy[name].append((a,b));continue
                    rid=instant(ev.decoded("RECURRENCE-ID"),self.zone) if "RECURRENCE-ID" in ev else a
                    slot=dict(title=norm(ev.get("X-JOHM-ORIGINAL",title)) if owned else title,cal=name,href=href,uid=str(ev["UID"]),rid=rid,start=a,end=b,label=entry["label"])
                    slot["id"]=hashlib.sha256(json.dumps(slot,sort_keys=True).encode()).hexdigest()
                    slot["etag"]=next((ps.findtext(D+"prop/"+D+"getetag") for ps in ok if ps.findtext(D+"prop/"+D+"getetag")),None)
                    slots.append(slot)
        candidates_by_day={}
        for item in slots:
            for day in range(item["start"]//86400,(item["end"]-1)//86400+1):
                candidates_by_day.setdefault((item["cal"],day),[]).append(item)
        return [s for s in sorted(slots,key=lambda x:x["start"]) if start<=s["start"]<end and not self.closed(s["start"]) and
                not any(s["start"]<b and a<s["end"] for a,b in blocked+busy[s["cal"]]) and
                not any(other is not s and s["cal"]==other["cal"] and s["start"]<other["end"] and other["start"]<s["end"] for other in candidates_by_day.get((s["cal"],s["start"]//86400),[]))]
    def closed(self,ts):
        day=datetime.fromtimestamp(ts,self.zone).date()
        return day in holidays(day.year,tuple(self.cfg.get("extra_holidays",())))
    def sync_token(self,name):
        xml=b'<d:propfind xmlns:d="DAV:"><d:prop><d:sync-token/></d:prop></d:propfind>'
        raw,_=self.request("PROPFIND",self.collection(name),xml,{"Depth":"0","Content-Type":"application/xml"})
        token=ET.fromstring(raw).findtext(".//"+D+"sync-token")
        if not token:raise ValueError("Kalender liefert kein Sync-Token.")
        return token
    def changed(self,name,token):
        """Entries changed since the token, and the next token."""
        from xml.sax.saxutils import escape
        xml='<d:sync-collection xmlns:d="DAV:"><d:sync-token>'+escape(token)+'</d:sync-token><d:sync-level>1</d:sync-level><d:prop><d:getetag/></d:prop></d:sync-collection>'
        raw,_=self.request("REPORT",self.collection(name),xml.encode(),{"Content-Type":"application/xml"})
        root=ET.fromstring(raw)
        if root.tag!=D+"multistatus":raise ValueError("Ungültige CalDAV-Antwort.")
        hrefs=[row.findtext(D+"href") for row in root.findall(D+"response")
               if any(" 200 " in (ps.findtext(D+"status") or "") for ps in row.findall(D+"propstat"))]
        return [h for h in hrefs if h],root.findtext(D+"sync-token") or token
    def clear_entry(self,ev,raster):
        """Back to the free raster title. Phone and SMS lines go, other notes of the practice stay."""
        kept=[line for line in str(ev.get("DESCRIPTION","")).splitlines() if not re.match(r"\s*(?:Telefon|SMS-Erinnerung):",line)]
        for k in ("SUMMARY","DESCRIPTION","X-JOHM-ORIGINAL","X-JOHM-CODE","DTSTAMP"):ev.pop(k,None)
        ev.add("SUMMARY",raster)
        if any(line.strip() for line in kept):ev.add("DESCRIPTION","\n".join(kept))
        ev.add("DTSTAMP",datetime.now(UTC).replace(microsecond=0))
    def free_entry(self,ref):
        """Free the existing appointment of a confirmed move. Only if it still looks exactly as when it was found."""
        url=self.url(ref["cal"],ref["href"])
        raw,headers=self.request("GET",url)
        tag=headers.get("ETag","")
        if not tag.startswith('"'):return False
        cal=Calendar.from_ical(raw)
        for ev in cal.walk("VEVENT"):
            if any(k in ev for k in ("RRULE","RDATE")):continue
            title=" ".join(str(ev.get("SUMMARY","")).split())
            if interval(ev,self.zone)[0]!=ref["start"] or title!=ref["title"]:continue
            found=re.findall(r"(?<!\w)([A-Za-zÄÖÜäöüß]{1,12}:)",title)
            if not found:return False
            self.clear_entry(ev,found[0])
            self.request("PUT",url,cal.to_ical(),{"If-Match":tag,"Content-Type":"text/calendar; charset=utf-8"})
            return True
        return False
    def reset_cancelled(self,name,href):
        """Staff wrote the word Absage into a title: reset that entry to its free raster title. Never touches a whole series."""
        url=self.url(name,href)
        raw,headers=self.request("GET",url)
        tag=headers.get("ETag","")
        cal=Calendar.from_ical(raw)
        done=[]
        for ev in cal.walk("VEVENT"):
            title=" ".join(str(ev.get("SUMMARY","")).split())
            if not re.search(MARKED,title) or any(k in ev for k in ("RRULE","RDATE")):continue
            raster=" ".join(str(ev.get("X-JOHM-ORIGINAL","")).split())
            if not raster:
                found=[t for t in re.findall(r"(?<!\w)([A-Za-zÄÖÜäöüß]{1,12}:)",title) if not re.search(MARKED,t)]
                raster=found[0] if found else ""
            if not raster:continue
            code=str(ev.get("X-JOHM-CODE",""))
            self.clear_entry(ev,raster)
            done.append({"code":code,"raster":raster})
        if done:
            if not tag.startswith('"'):raise ValueError("Kalender liefert keinen starken ETag.")
            self.request("PUT",url,cal.to_ical(),{"If-Match":tag,"Content-Type":"text/calendar; charset=utf-8"})
        return done
    def agenda(self,start,end):
        """Every entry in the window, for staff questions in the local chat. Read only."""
        stamp=lambda t: datetime.fromtimestamp(t,UTC).strftime("%Y%m%dT%H%M%SZ")
        limits=f'start="{stamp(start)}" end="{stamp(end)}"'
        xml=f'<c:calendar-query xmlns:d="DAV:" xmlns:c="{C[1:-1]}"><d:prop><c:calendar-data><c:expand {limits}/></c:calendar-data></d:prop><c:filter><c:comp-filter name="VCALENDAR"><c:comp-filter name="VEVENT"><c:time-range {limits}/></c:comp-filter></c:comp-filter></c:filter></c:calendar-query>'
        blocked=[]; rows=[]
        for name,entry in self.calendars.items():
            raw,_=self.request("REPORT",self.collection(name),xml.encode(),{"Depth":"1","Content-Type":"application/xml"})
            root=ET.fromstring(raw)
            if root.tag!=D+"multistatus":raise ValueError("Ungültige CalDAV-Antwort.")
            for row in root.findall(D+"response"):
                data=next((ps.findtext(D+"prop/"+C+"calendar-data") for ps in row.findall(D+"propstat") if " 200 " in (ps.findtext(D+"status") or "") and ps.findtext(D+"prop/"+C+"calendar-data")),None)
                if not data:raise ValueError("Kalendereintrag unvollständig.")
                for ev in Calendar.from_ical(data).walk("VEVENT"):
                    if norm(ev.get("STATUS",""))=="cancelled":continue
                    a,b=interval(ev,self.zone)
                    title=" ".join(str(ev.get("SUMMARY","")).split())
                    notice=norm(title)+" "+norm(ev.get("DESCRIPTION",""))
                    if re.search(r"\bnichts\s+eintragen\b",notice) or any(re.search(rule,notice,re.I) for rule in self.cfg["global_blocks"]):
                        blocked.append((a,b))
                    rows.append(dict(start=a,end=b,title=title,calendar=entry["label"],raster=not entry.get("blocks_only"),path=name,href=row.findtext(D+"href")))
        for r in rows:
            # Free means: an untouched raster entry such as "KV:" that no block note covers.
            r["free"]=bool(r["raster"] and re.fullmatch(r"[^\s:]{1,12}:",r["title"]) and not self.closed(r["start"]) and not any(r["start"]<b and a<r["end"] for a,b in blocked))
        return sorted(rows,key=lambda r:(r["start"],r["calendar"]))
    def held_available(self,slot,code,expected):
        possible=self.scan(slot["start"],slot["end"],held=code)
        if not any(s["id"]==slot["id"] for s in possible):return False
        raw,_=self.request("GET",self.url(slot["cal"],slot["href"]))
        return any(str(e.get("X-JOHM-CODE",""))==code and hashlib.sha256(e.to_ical()).hexdigest()==expected
                   for e in Calendar.from_ical(raw).walk("VEVENT"))
    def change(self,slot,code,action,name="",phone="",sms="offen",until="",expected=""):
        url=self.url(slot["cal"],slot["href"])
        raw,headers=self.request("GET",url)
        tag=headers.get("ETag","")
        if not tag.startswith('"'):raise ValueError("Kalender liefert keinen starken ETag.")
        if action=="hold" and tag!=slot["etag"]:raise ValueError("Kalenderobjekt seit Prüfung geändert.")
        cal=Calendar.from_ical(raw)
        events=cal.walk("VEVENT")
        if not events or any(str(e["UID"])!=slot["uid"] for e in events):
            raise ValueError("Kalenderobjekt geändert.")
        master=next((e for e in events if "RECURRENCE-ID" not in e),None)
        ev=next((e for e in events if "RECURRENCE-ID" in e and instant(e.decoded("RECURRENCE-ID"),self.zone)==slot["rid"]),None)
        if ev is None:
            if master is None:raise ValueError("Termin fehlt.")
            ev=master
            if any(k in master for k in ("RRULE","RDATE")):
                if action!="hold":raise ValueError("Reservierte Serienausnahme fehlt.")
                ev=copy.deepcopy(master)
                for k in ("RRULE","RDATE","EXDATE","DTSTART","DTEND","DURATION"):ev.pop(k,None)
                tz=master.decoded("DTSTART").tzinfo or self.zone
                for k,v in [("RECURRENCE-ID","rid"),("DTSTART","start"),("DTEND","end")]:
                    ev.add(k,datetime.fromtimestamp(slot[v],tz))
                cal.add_component(ev)
        if interval(ev,self.zone)!=(slot["start"],slot["end"]) or ev.get("ATTENDEE") or ev.get("ORGANIZER"):
            raise ValueError("Termin wurde geändert oder ist eine Einladung.")
        fingerprint=lambda:hashlib.sha256(ev.to_ical()).hexdigest()
        if action=="hold":
            title=str(ev.get("SUMMARY",""))
            if norm(title) not in {norm(t) for t in self.cfg["free_titles"]} or ev.get("X-JOHM-CODE"):
                raise ValueError("Termin inzwischen belegt.")
            ev.add("X-JOHM-ORIGINAL",title)
            ev.add("X-JOHM-CODE",code)
            ev.pop("SUMMARY",None);ev.add("SUMMARY",f"RESERVIERT für {name or code} · bis {until} · {title}")
        else:
            if str(ev.get("X-JOHM-CODE",""))!=code or fingerprint()!=expected:
                raise ValueError("Reservierung manuell geändert. Bitte prüfen.")
            title=str(ev.pop("X-JOHM-ORIGINAL"))
            ev.pop("X-JOHM-CODE")
            ev.pop("SUMMARY",None);ev.add("SUMMARY",title if action=="release" else f"{title} {name}")
            if action=="book":
                desc=str(ev.get("DESCRIPTION",""))
                ev.pop("DESCRIPTION",None);ev.add("DESCRIPTION",(desc+"\n" if desc else "")+f"Telefon: {phone or 'bitte nachreichen'}\nSMS-Erinnerung: {sms}")
        ev.pop("DTSTAMP",None);ev.add("DTSTAMP",datetime.now(UTC).replace(microsecond=0))
        digest=fingerprint()
        self.request("PUT",url,cal.to_ical(),{"If-Match":tag,"Content-Type":"text/calendar; charset=utf-8"})
        return digest
