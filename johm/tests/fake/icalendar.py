"""Minimal stand-in for the icalendar package, for logic tests only."""
class Comp(dict):
    def __init__(s,name):super().__init__();s.name=name;s.sub=[]
    def add(s,k,v):s[k]=v
    def walk(s,name):return [c for c in s.sub if c.name==name]
    def to_ical(s):
        out=[]
        for c in s.sub:
            out.append("BEGIN:"+c.name);out+= [k+":"+str(v).replace("\n","\\n") for k,v in c.items()];out.append("END:"+c.name)
        return "\r\n".join(out).encode()
class Calendar(Comp):
    @classmethod
    def from_ical(cls,raw):
        cal=cls("VCALENDAR");cur=None
        for line in (raw.decode() if isinstance(raw,bytes) else raw).splitlines():
            if line.startswith("BEGIN:VEVENT"):cur=Comp("VEVENT")
            elif line.startswith("END:VEVENT"):cal.sub.append(cur);cur=None
            elif cur is not None and ":" in line:
                k,v=line.split(":",1);cur[k]=v.replace("\\n","\n")
        return cal
import datetime as _dt
def _decoded(self,key):return _dt.datetime.strptime(self[key],"%Y%m%dT%H%M%S")
Comp.decoded=_decoded
