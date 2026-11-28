import json
import re
import sys
import time
from pathlib import Path
from caldav_slots import DAV, D, ET

def probe(config):
    cfg=json.loads(Path(config).read_text())
    password=Path(cfg["password_file"]).read_text().rstrip("\r\n")
    if not password or cfg["mode"] not in ("preview","live"):
        raise ValueError("Modus oder Passwort ungültig")
    if not (1<=cfg["default_days"]<=cfg["max_days_mfa"]<=1460 and 1<=cfg["hold_hours"]<=120 and 1<=cfg["slot_minutes"]<=240):
        raise ValueError("Zeitgrenzen ungültig")
    for rule in [cfg["request_pattern"],*cfg["global_blocks"]]:re.compile(rule,re.I)
    if not cfg["appointment_types"] or not cfg["mailboxes"] or "INBOX" not in cfg["reply_folders"]:
        raise ValueError("Terminarten, Postfächer oder Antwortordner fehlen")
    if not all(t["label"] and t["free_titles"] and set(t["free_titles"])<=set(cfg["free_titles"]) for t in cfg["appointment_types"].values()):
        raise ValueError("Termintitel passen nicht zur Freigabeliste")
    hosts=cfg.get("hosts")
    if not isinstance(hosts,list) or not hosts or not all(isinstance(h,str) and re.fullmatch(r"[A-Za-z0-9.-]{1,253}",h) for h in hosts):
        raise ValueError("hosts fehlt: Hostnamen des Mailarbeitsplatzes in der Konfiguration eintragen")
    if not isinstance(cfg.get("fax_address",""),str):raise ValueError("fax_address ungültig")
    for key,kind in cfg["appointment_types"].items():
        if "hold_hours" in kind and not (type(kind["hold_hours"]) is int and 1<=kind["hold_hours"]<=cfg["hold_hours"]):
            raise ValueError("hold_hours der Terminart "+key+" muss zwischen 1 und dem allgemeinen Wert liegen")
    if sum(1 for kind in cfg["appointment_types"].values() if kind.get("urgent") is True)>1:raise ValueError("Nur eine Terminart darf urgent sein")
    for d in cfg.get("doctors",[]):
        if not (isinstance(d,dict) and isinstance(d.get("name"),str) and d["name"].strip()
                and all(isinstance(d.get(k,[]),list) and all(isinstance(v,str) for v in d.get(k,[])) for k in ("rasters","calendars"))):
            raise ValueError("doctors ungültig: name, rasters und calendars prüfen")
    dav=DAV(cfg,password)
    if not any(not t.get("blocks_only") for t in dav.calendars.values()):raise ValueError("Terminkalender fehlt")
    for key,entry in dav.calendars.items():
        xml=b'<d:propfind xmlns:d="DAV:"><d:prop><d:current-user-privilege-set/><d:resourcetype/></d:prop></d:propfind>'
        raw,_=dav.request("PROPFIND",dav.collection(key),xml,{"Depth":"0","Content-Type":"application/xml"})
        rt=ET.fromstring(raw)
        if rt.find(".//{urn:ietf:params:xml:ns:caldav}calendar") is None:raise ValueError("Kein Kalender: "+key)
        rights={el.tag for pr in rt.iter(D+"privilege") for el in pr}
        if cfg["mode"]=="live" and not entry.get("blocks_only") and not rights & {D+"write",D+"write-content",D+"all"}:
            raise ValueError("Schreibrecht fehlt: "+key)
    now=int(time.time())
    print("Erreichbare Kalender:",len(dav.calendars), "| Freie Raster in den nächsten 24 Stunden:",len(dav.scan(now,now+86400)))

if __name__=="__main__":probe(sys.argv[1])
