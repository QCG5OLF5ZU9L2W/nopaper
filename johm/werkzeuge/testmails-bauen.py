#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Baut eine Seite mit Testmails für die Terminerkennung: je Fall ein Link, der das Mailprogramm mit
fertigem Betreff und Text öffnet, dazu das erwartete Ergebnis. Alle Namen und Anliegen sind erfunden.
    python3 johm/werkzeuge/testmails-bauen.py [zieldatei]"""
import json
import sys

N,S,G,R="Neue Anfrage","Sprache","Grenzfall","Antwort auf Angebot"
C=[
(N,"Deutsch","Terminanfrage Vorsorge","Guten Tag,\nich möchte gern einen Termin zur Vorsorge vereinbaren.\nViele Grüße\nMarta Lindqvist","Terminanliegen erkannt · Vorsorge · nächste freie Plätze"),
(N,"Deutsch","Frage","Hallo,\nwann kann ich denn mal wieder vorbeikommen? Meine letzte Kontrolle ist schon zwei Jahre her.\nLG Jana Oberhuber","Erkannt, obwohl das Wort Termin fehlt · Vorsorge oder allgemeiner Termin · nächste freie Plätze"),
(N,"Deutsch","Re: Rückfrage","Liebes Praxis-Team,\nja, das passt soweit. Ich habe am Montag zwischen 08:30 und 10:00 Uhr Zeit.\nHerzliche Grüße\nSvenja Albrecht","Erkannt · allgemeiner Termin · Wunsch Mo 08:30–10:00 · ist dort nichts frei: nächste freie Plätze ab Montag"),
(N,"Deutsch","Krebsvorsorge","Guten Tag,\nhätten Sie am Donnerstag, 17.12., zwischen 9 und 11 Uhr etwas frei für die Krebsvorsorge?\nMit freundlichen Grüßen\nHenrike Paulsen","Erkannt · Vorsorge · Wunsch Do 17.12. 09:00–11:00"),
(N,"Deutsch","Ueberweisung","Guten Tag ich brauche eine Ueberweisung und einen Temrin zur Abklaerung wegen Eisenmangel.\nMfG Dragan Petrovski","Erkannt trotz Tippfehler und fehlender Umlaute · Anämieabklärung · nächste freie Plätze"),
(N,"Deutsch","Zwei Termine","Hallo,\nich bräuchte einen Vorsorgetermin für mich und einen für meine Mutter, am liebsten donnerstags.\nDanke!\nAylin Demirci","Zwei Einträge (Absenderin und Mutter) · jeweils Vorsorge · Wunsch Do"),
(N,"Deutsch","Bitte um Termin","Sehr geehrte Damen und Herren,\nich bräuchte möglichst bald einen Termin zur Vorsorge.\nFreundliche Grüße\nBeate Krömer","Erkannt · Vorsorge · frühestmögliche Plätze, kein Zeitwunsch"),
(G,"Deutsch","Termin nach Urlaub","Hallo,\nich hätte gern einen Vorsorgetermin irgendwann nach meinem Urlaub, am besten nachmittags.\nGruß\nTabea Wendt","Erkannt · manuelle Vergabe · Hinweis „Zeitangabe im Original prüfen“"),
(G,"Deutsch","Spirale","Guten Tag,\nich möchte einen Termin zur Entfernung der Spirale.\nViele Grüße\nCarla Benedetti","Erkannt · „Manuell vergeben“, weil anderer Anlass · keine Vorschläge"),
(N,"Deutsch","Termin verschieben","Hallo,\nich muss meinen Termin am 3.12. um 10:15 Uhr leider verschieben. Ginge es in der Woche danach?\nDanke\nMiriam Stolze","„Termin verschieben“ · Bearbeitung durch die MFA · keine Vorschläge"),
(N,"Deutsch","Absage","Guten Morgen,\nich kann meinen Termin am Freitag nicht wahrnehmen und sage ihn hiermit ab.\nGruß\nInes Fabritius","„Termin absagen“ · Bearbeitung durch die MFA"),
(G,"Deutsch","Rezept","Guten Tag,\nkönnen Sie mir bitte das Rezept für meine Pille erneut ausstellen? Ich hole es am Empfang ab.\nDanke\nLena Voss","„Kein Terminanliegen erkannt“"),
(G,"Deutsch","Danke","Hallo,\nvielen Dank für den Termin gestern, es ist alles gut verlaufen.\nSchöne Grüße\nRuth Sandner","„Kein Terminanliegen erkannt“, obwohl das Wort Termin vorkommt"),
(G,"Deutsch","Termin","Hallo,\nich habe gestern einen positiven Schwangerschaftstest gemacht und bräuchte einen Termin.\nLiebe Grüße\nNora Feldkamp","Hinweis Schwangerschaft · keine Terminvorschläge, Vergabe durch die MFA"),
(G,"Deutsch","Vorsorge","Guten Tag,\nmeine Schwester ist schwanger und hat mir Ihre Praxis empfohlen. Ich selbst hätte gern einen Termin zur Vorsorge.\nViele Grüße\nPaula Reinders","Kein Schwangerschafts-Treffer für die Absenderin · Vorsorge mit Vorschlägen"),
(S,"Englisch","Appointment request","Hello,\nI would like to book an appointment for my annual check-up. Is anything available on Tuesday or Thursday between 2 and 4 pm?\nKind regards,\nPriya Raman","Deutsche Übersetzung · Vorsorge · Wunsch Di/Do 14:00–16:00 · Antwortentwurf auf Englisch"),
(S,"Türkisch","Randevu","Merhaba,\nyıllık kontrol için randevu almak istiyorum. Cuma günleri sabah 9 ile 11 arası uygunum.\nTeşekkürler,\nElif Karadağ","Übersetzung · Vorsorge · Wunsch Fr 09:00–11:00 · Antwort auf Türkisch"),
(S,"Arabisch","موعد","مرحباً،\nأود حجز موعد للفحص الدوري في أقرب وقت ممكن.\nشكراً،\nليلى حداد","Übersetzung · Vorsorge · frühestmögliche Plätze · Antwort auf Arabisch"),
(S,"Russisch","Запись на приём","Здравствуйте!\nХотела бы записаться на профилактический осмотр. Мне удобно в среду с 15:00 до 17:00.\nС уважением,\nОльга Смирнова","Übersetzung · Vorsorge · Wunsch Mi 15:00–17:00"),
(S,"Ukrainisch","Запис на прийом","Добрий день!\nЧи можна записатися на прийом? Мені підходить вівторок зранку, з 8:30 до 10:00.\nДякую,\nОксана Мельник","Übersetzung · allgemeiner Termin · Wunsch Di 08:30–10:00"),
(S,"Polnisch","Wizyta kontrolna","Dzień dobry,\nchciałabym umówić się na wizytę kontrolną, najlepiej w poniedziałek między 10:00 a 12:00.\nPozdrawiam,\nAgnieszka Wróbel","Übersetzung · Vorsorge oder allgemeiner Termin · Wunsch Mo 10:00–12:00"),
(S,"Französisch","Rendez-vous","Bonjour,\nje ne pourrai malheureusement pas venir à mon rendez-vous de jeudi et je souhaite l'annuler.\nCordialement,\nCamille Morel","Übersetzung · „Termin absagen“ · Bearbeitung durch die MFA"),
(S,"Spanisch","Cita","Hola,\n¿sería posible cambiar mi cita del día 10 de diciembre a otro día?\nGracias,\nLucía Ortega","Übersetzung · „Termin verschieben“ · Bearbeitung durch die MFA"),
(S,"Gemischt","Termin","Hallo, ich brauche appointment für Kontrolle. Montag oder Mittwoch morning 9-11 ist gut.\nDanke\nNguyen Thi Lan","Erkannt · Wunsch Mo/Mi 09:00–11:00"),
(S,"Englisch","First appointment","Hi,\nI just found out I'm pregnant and would like a first appointment.\nThanks,\nHannah Whitfield","Hinweis Schwangerschaft · keine Terminvorschläge"),
(S,"Italienisch","Referto","Buongiorno,\npotete inviarmi il referto dell'ultima visita?\nGrazie,\nGiulia Ferrante","Übersetzung · „Kein Terminanliegen erkannt“"),
(R,"Deutsch",None,"Ja, der erste Termin passt mir gut.\nDaniela Kowalczyk\nTel. 0171 3920042, gern mit SMS-Erinnerung","Früherer Termin gebucht, der andere wieder frei · Name, Telefon und SMS „ja“ im Kalender · Buchungsbestätigung kommt"),
(R,"Deutsch",None,"Den späteren bitte. Danke!","Späterer Termin gebucht · Name aus dem Absender der Mail"),
(R,"Deutsch",None,"Beide Termine passen leider nicht.","Beide Reservierungen freigegeben · Mail erscheint als Terminanliegen · die beiden Zeiten werden nicht erneut vorgeschlagen"),
(R,"Deutsch",None,"Vielleicht, ich muss erst meinen Dienstplan abwarten und melde mich.","Sofort „Termin manuell prüfen“ · keine Buchung"),
(R,"Deutsch",None,"Passt, aber nur, wenn es nicht länger als eine halbe Stunde dauert.","„Termin manuell prüfen“, weil Zusage unter Bedingung"),
(R,"Englisch",None,"The second one works for me.\nThanks,\nPriya Raman","Späterer Termin gebucht"),
(R,"Türkisch",None,"İlk randevu bana uygun, teşekkürler.\nElif Karadağ","Früherer Termin gebucht"),
(R,"Deutsch",None,"Im Angebot auf „Diesen Termin per E-Mail bestätigen“ klicken und die vorbereitete Mail unverändert senden.","Gebucht ohne Modell · Name aus der Vorbelegung · Bestätigung bittet um Telefonnummer"),
(R,"Deutsch",None,"Im Angebot auf „Keiner dieser Termine passt“ klicken, hinter „Besser passen würde mir:“ ergänzen: freitags ab 13 Uhr","Beide Reservierungen freigegeben · neue Vorschläge freitags ab 13:00"),
(R,"Deutsch",None,"Von einer anderen Mailadresse antworten: Ja, der erste Termin passt.","Keine Buchung · Reservierung bleibt bis zur Frist bestehen"),
(R,"Deutsch",None,"Gar nicht antworten und die Frist ablaufen lassen.","Reservierung nach Fristablauf freigegeben · Kalendereintrag heißt wieder „KV:“ bzw. „sKV:“"),
]

data=[{"nr":i,"area":a,"lang":l,"subject":s,"body":b,"expect":e} for i,(a,l,s,b,e) in enumerate(C,1)]
html='''<!DOCTYPE html>
<html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Johm Testmails Terminerkennung</title>
<style>
/* Layout: one column of mail cards, grouped by test area; send controls sit at the foot of each card. */
:root{--bg:#f4f6f5;--card:#ffffff;--ink:#17322e;--muted:#55706a;--line:#cfdcd8;--accent:#1b6156;--accent-ink:#ffffff;--note:#fff3d6;--note-ink:#5a3a00;
--body:"Segoe UI",system-ui,-apple-system,Arial,sans-serif}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){color-scheme:dark;--bg:#101917;--card:#18242180;--ink:#e3eeeb;--muted:#9db5af;--line:#2f433e;--accent:#58b3a2;--accent-ink:#0c1a17;--note:#3b2f12;--note-ink:#f3dfae}}
:root[data-theme="dark"]{color-scheme:dark;--bg:#101917;--card:#182421;--ink:#e3eeeb;--muted:#9db5af;--line:#2f433e;--accent:#58b3a2;--accent-ink:#0c1a17;--note:#3b2f12;--note-ink:#f3dfae}
*{box-sizing:border-box}html,body{margin:0}
body{background:var(--bg);color:var(--ink);font:16px/1.5 var(--body);padding:24px 16px 64px}
main{max-width:860px;margin:0 auto;display:flex;flex-direction:column;gap:20px}
h1{font-size:26px;margin:0;text-wrap:balance}h2{font-size:19px;margin:16px 0 0;text-wrap:balance}
p{margin:0;max-width:65ch}.muted{color:var(--muted)}
.setup{display:flex;flex-wrap:wrap;gap:8px 12px;align-items:center;background:var(--card);border:1px solid var(--line);border-radius:8px;padding:14px 16px}
.setup label{font-weight:600}.setup input{font:inherit;color:inherit;background:transparent;border:1px solid var(--line);border-radius:5px;padding:7px 10px;min-width:0;flex:1 1 260px}
.note{background:var(--note);color:var(--note-ink);border-radius:8px;padding:12px 16px}
.case{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:16px;display:flex;flex-direction:column;gap:10px}
.head{display:flex;flex-wrap:wrap;gap:8px;align-items:baseline}
.nr{font-weight:700;font-variant-numeric:tabular-nums}.lang{border:1px solid var(--line);border-radius:999px;padding:1px 10px;font-size:14px;color:var(--muted)}
.subject{font-weight:600;overflow-wrap:anywhere}
pre{margin:0;white-space:pre-wrap;overflow-wrap:anywhere;font:inherit;border-left:3px solid var(--accent);padding:2px 0 2px 12px}
.expect{color:var(--muted);font-size:15px}.expect b{color:var(--ink)}
.actions{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
a.btn,button{font:inherit;border-radius:5px;padding:7px 14px;cursor:pointer;text-decoration:none;border:1px solid var(--accent)}
a.btn{background:var(--accent);color:var(--accent-ink);font-weight:600}button{background:transparent;color:var(--accent)}
a.btn:focus-visible,button:focus-visible,input:focus-visible{outline:3px solid var(--accent);outline-offset:2px}
.done{font-size:14px;color:var(--muted)}
</style></head><body><main>
<h1>Testmails für die Terminerkennung</h1>
<p class="muted">Alle Namen, Telefonnummern und Anliegen sind erfunden. „Mail öffnen“ startet Ihr Mailprogramm mit fertigem Betreff und Text. Senden Sie die Mail von einer Testadresse und vergleichen Sie in Johm mit dem erwarteten Ergebnis.</p>
<div class="setup"><label for="to">Empfänger</label><input id="to" type="email" value="" placeholder="praxis@example.org" autocomplete="off"></div>
<p class="note">Reagiert „Mail öffnen“ in einer Vorschau nicht, laden Sie diese Datei herunter und öffnen Sie sie direkt im Browser. Betreff und Text lassen sich auch einzeln kopieren.</p>
<div id="list"></div>
</main>
<script>
const CASES=__DATA__;
const list=document.getElementById("list"),to=document.getElementById("to");
list.style.cssText="display:flex;flex-direction:column;gap:12px";
try{to.value=localStorage.getItem("johm-testmails-to")||"";}catch(e){}
to.addEventListener("input",()=>{try{localStorage.setItem("johm-testmails-to",to.value.trim());}catch(e){}});
function link(c){return "mailto:"+encodeURIComponent(to.value.trim()).replace(/%40/g,"@")+"?subject="+encodeURIComponent(c.subject)+"&body="+encodeURIComponent(c.body.replace(/\\n/g,"\\r\\n"));}
async function copy(text,flag){
  try{await navigator.clipboard.writeText(text);}
  catch(e){const t=document.createElement("textarea");t.value=text;document.body.append(t);t.select();try{document.execCommand("copy");}catch(x){}t.remove();}
  flag.textContent="kopiert";setTimeout(()=>flag.textContent="",1800);
}
function el(tag,cls,text){const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;}
let area="";
for(const c of CASES){
  if(c.area!==area){area=c.area;list.append(el("h2","",area));
    if(area==="Antwort auf Angebot")list.append(el("p","muted","Voraussetzung: Johm hat ein Angebot mit zwei Terminen an Ihre Testadresse gesendet. Antworten Sie auf diese Mail mit dem jeweiligen Text."));}
  const card=el("article","case"),head=el("div","head");
  head.append(el("span","nr","Nr. "+c.nr),el("span","lang",c.lang));
  if(c.subject)head.append(el("span","subject","Betreff: "+c.subject));
  card.append(head,el("pre","",c.body));
  const ex=el("p","expect");ex.append(el("b","","Erwartet: "),document.createTextNode(c.expect));card.append(ex);
  const act=el("div","actions"),flag=el("span","done");
  if(c.subject){
    const a=el("a","btn","Mail öffnen");a.href=link(c);a.target="_blank";a.rel="noopener";to.addEventListener("input",()=>a.href=link(c));act.append(a);
    const s=el("button","","Betreff kopieren");s.type="button";s.onclick=()=>copy(c.subject,flag);act.append(s);
  }
  if(!/^(Im Angebot|Von einer anderen|Gar nicht)/.test(c.body)||c.nr===36){
    const b=el("button","","Text kopieren");b.type="button";b.onclick=()=>copy(c.nr===36?"Ja, der erste Termin passt.":c.body,flag);act.append(b);
  }
  act.append(flag);card.append(act);list.append(card);
}
</script></body></html>'''
html=html.replace("--card:#18242180","--card:#182421").replace("__DATA__",json.dumps(data,ensure_ascii=False).replace("</","<\\/"))
ziel=sys.argv[1] if len(sys.argv)>1 else "johm-testmails.html"
open(ziel,"w",encoding="utf-8").write(html)
print("Testmails:",ziel,"("+str(len(data))+" Fälle)")
