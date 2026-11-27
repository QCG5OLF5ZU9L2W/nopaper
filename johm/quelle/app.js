"use strict";
const el=id=>document.getElementById(id);
let current=null, item=null, thread=[], saveKey=null, busy=false, dirty=false;
function notice(text){el("status").textContent=text;}
function reset(){
  clearCalendar();
  for(const id of ["pregflag","translation"])el(id).hidden=true;
  el("pregflag").textContent="";el("translated").textContent="";
  el("faxpanel").hidden=true;el("faxtext").textContent="";el("send").hidden=false;
  el("comparison").hidden=true;el("latest").textContent="";current=null;
  item=null; thread=[]; dirty=false; saveKey=null;
  el("work").hidden=true;
  for(const id of ["subject","address","original","history"]) el(id).replaceChildren();
  for(const id of ["draft","question"]) el(id).value="";
}
async function api(action,body){
  const options={cache:"no-store"};
  if(body){options.method="POST";options.headers={"Content-Type":"application/json"};options.body=JSON.stringify(body);}
  if(action==="list" && typeof globalThis.AbortSignal?.timeout==="function")options.signal=AbortSignal.timeout(60000);
  const response=await fetch("api/"+action,options);
  if(!response.ok){
    if([401,403].includes(response.status)){ reset();el("list").replaceChildren(); }
    const err=new Error(await response.text());err.status=response.status;throw err;
  }
  return response.json();
}
async function task(fn){
  if(busy)return;
  busy=true;
  const fields=[...document.querySelectorAll("button,textarea")];
  fields.forEach(x=>x.disabled=true);
  try{await fn();}catch(e){
    notice(e.name==="TimeoutError"?"Zeitüberschreitung beim Laden der Postfachliste. Bitte erneut aktualisieren.":e.message || "Anfrage fehlgeschlagen. Bitte erneut aktualisieren.");
    if(e.status===409&&item){
      try{
        const fresh=await api("list");
        const candidates=[];
        for(const row of fresh){
          if(row.id===item.id || (row.subject===item.draftSubject && row.to===item.to)) candidates.push(row);
        }
        current=null;
        for(const row of candidates){
          try{const ctx=await api("open",{id:row.id});
            if(ctx.source===item.source){current={...ctx,id:row.id};break;}
          }catch(ignore){}
        }
        el("latest").textContent=current?.draft||"Kein aktueller Entwurf zu dieser Originalmail gefunden.";
        el("adopt").hidden=!current;el("comparison").hidden=false;
      }catch(ignore){}
    }
  }
  finally{busy=false;fields.forEach(x=>x.disabled=false);}
}
function canLeave(){return (!dirty&&!picks.size)||confirm("Ungespeicherte Fassung verwerfen?");}
function refreshAlerts(){
  api("calendar-status").then(alerts=>{el("alerts").textContent=alerts.map(r=>r.state==="note"?"Hinweis · "+r.when+" · "+r.label:(r.state==="sent"?"Im Kalender reserviert bis "+r.until:"Termin manuell prüfen")+" · "+r.label+" · "+r.when+" · Code "+r.code).join("\n");}).catch(()=>{el("alerts").textContent="Kalenderstatus derzeit nicht verfügbar.";});
}
function bindRow(b,row){
    b.onclick=()=>task(async()=>{
      if(!canLeave())return;
      notice("Mail und PDF-Anhänge werden gelesen …");
      const data=await api("open",{id:row.id});reset();item={id:row.id,draftSubject:row.subject,...data};
      el("faxpanel").hidden=!data.fax;el("faxtext").textContent=(data.fax||"")+(data.fax?.length>22000?"\n\nFür den Chat werden die ersten 22.000 Zeichen verwendet.":"");el("send").hidden=data.faxBlocked;
      el("subject").textContent=data.subject;el("original").textContent=data.original;
      el("address").textContent="Von "+data.sender+" · Antwort an "+data.to;
      el("revise").textContent=row.kind==="review"?"Antwort vorschlagen":"Entwurf überarbeiten";
      el("save").textContent=row.kind==="review"?"Ersten Entwurf speichern":"Entwurf übernehmen";
      el("draft").value=data.draft;el("work").hidden=false;
      loadReading(item);
      for(const x of el("list").children)x.setAttribute("aria-current",String(x===b));
      notice("");
    });
}
async function loadList(){
  notice("Postfachliste wird geladen …");
  refreshAlerts();
  const rows=await api("list");el("list").replaceChildren();
  for(const group of ["review","draft"]){
    const subset=rows.filter(row=>(row.kind||"draft")===group);
    if(!subset.length)continue;
    const label=document.createElement("h3");label.textContent=group==="review"?"Posteingang":"Mit Entwurf";el("list").append(label);
    for(const row of subset){
    const b=document.createElement("button");b.textContent=(row.subject||"(ohne Betreff)")+" · "+row.to+" · "+row.status+" · UID "+row.uid;
    b.setAttribute("aria-current",String(row.id===item?.id));
    bindRow(b,row);
    el("list").append(b);
    }
  }
  notice(rows.length===1?"1 Nachricht im Posteingang":rows.length?rows.length+" Nachrichten im Posteingang":"Der Posteingang enthält keine anzuzeigenden Nachrichten.");
}
function pregnancy(state,text){
  const banner=el("pregflag");banner.dataset.state=state;banner.textContent=text;banner.hidden=!text;
}
async function loadReading(source){
  const pane=el("translation"), output=el("translated");
  pane.hidden=false;output.textContent="John prüft die Sprache und übersetzt bei Bedarf …";
  const mention=(source.mentions||[]).join(", ");
  if(mention)pregnancy("uncertain",mention+" im Text erwähnt · Zuordnung wird geprüft.");
  try{
    const info=await api("reading",{id:source.id,revision:source.revision});
    if(item!==source)return;
    output.textContent=info.translation;pane.hidden=!info.translation;
    if(source.original.length>=24000 && info.translation)output.textContent+="\n[Übersetzt wurde der in Johm sichtbare Auszug.]";
    const lines=info.hits.map(h=>{
      const label=h.id==="pregnancy" && h.state==="reported"?"Schwangerschaft oder positiver Test angegeben":h.id==="urgent"?(h.state==="reported"?"Dringendes Anliegen":"Möglicherweise dringend"):h.label+(h.state==="reported"?" angegeben":" · Angabe unklar");
      return label+(h.id==="pregnancy"?" · Termin manuell vergeben.":h.id==="urgent"?" · Bitte sofort prüfen.":"")+"\n„"+h.evidence+"“";
    });
    for(const label of info.unverified||[])lines.push(label+" · Merkmal unklar, bitte Original prüfen.");
    pregnancy(info.hits.some(h=>h.state==="reported")?"reported":"uncertain",lines.join("\n\n"));
    if(source.calendarEnabled && info.pregnancy==="none")loadCalendar(source);
  }catch(error){
    if(item!==source)return;
    output.textContent="Sprach- und Merkmalsprüfung fehlgeschlagen. Bitte Original lesen. "+error.message;
    if(mention)pregnancy("uncertain",mention+" im Text erwähnt · Bitte Original prüfen.");
  }
}
function renderThread(){
  el("history").replaceChildren();
  for(const m of thread){
    const p=document.createElement("p");p.textContent=(m.role==="user"?"MFA: ":"John: ")+m.content;el("history").append(p);
  }
}
function isDoneCommand(){
  return el("question").value.trim().toLowerCase()==="erledigt";
}
async function finishItem(){
  if(!item)return;
  const result=await api("finish",{id:item.id,revision:item.revision});
  reset();
  try{await loadList();notice(result.notice);}
  catch{notice(result.notice+" Liste bitte aktualisieren.");}
}
el("question").addEventListener("keydown",event=>{
  // Enter sends, Shift+Enter starts a new line, Ctrl+Enter asks for a reply draft.
  if(event.key!=="Enter" || event.shiftKey || event.isComposing)return;
  event.preventDefault();
  if(isDoneCommand())task(finishItem);
  else if(el("question").value.trim()){
    const mode=event.ctrlKey||event.metaKey?"revise":"ask";
    task(()=>chat(mode)).then(()=>{if(item)el("question").focus();});
  }
});
async function chat(mode){
  if(isDoneCommand())return finishItem();
  const question=el("question").value.trim() || (mode==="revise" ? "Formuliere einen passenden Antwortvorschlag für die Praxis." : "");if(!item||!question){notice("Bitte eine Frage oder Stichpunkte eingeben.");return;}
  notice("John bearbeitet die Anfrage …");
  const r=await api("chat",{id:item.id,revision:item.revision,question,days:el("calendar").hidden?null:(Number(el("caldays").value)||null),draft:editableBase(),history:thread.slice(-10),mode,override:calOverride,shown:calShown});
  if(draftBlock && (mode==="revise" || r.patientReply)){
    if(r.text.split(slotToken).length!==2)throw new Error("John konnte die Terminauswahl nicht in die Überarbeitung übernehmen. Der bisherige Entwurf enthält die gewählten Termine.");
    r.text=r.text.replace(slotToken,draftBlock);
  }else if(draftBlock){r.text=r.text.replaceAll(slotToken,draftBlock);}
  thread.push({role:"user",content:question},{role:"assistant",content:r.text});renderThread();el("question").value="";
  if("calendar" in r && item.calendarEnabled)loadCalendar(item,Number(el("caldays").value)||undefined,r.calendar);
  if(mode==="revise" || r.patientReply){el("draft").value=r.text;dirty=true;saveKey=null;notice(!picks.size && el("wishes").querySelector(".slots button")?"Vorschlag aktualisiert. Termine bitte unter „Passende Termine“ anklicken, sie werden in den Text eingefügt.":"Vorschlag aktualisiert. Bitte vor dem Speichern prüfen.");}
  else notice("");
}
el("draft").oninput=()=>{dirty=true;saveKey=null;};
el("ask").onclick=()=>task(()=>chat("ask"));el("revise").onclick=()=>task(()=>chat("revise"));
el("refresh").onclick=()=>task(loadList);
el("save").onclick=()=>task(async()=>{
  if(picks.size){notice("Die Terminauswahl ist für den Direktversand. Zum reinen Speichern Auswahl aufheben.");return;}
  if(!item||!dirty){notice("Der Entwurf wurde noch nicht geändert.");return;}
  saveKey??=crypto.randomUUID();
  const result=await api("save",{id:item.id,revision:item.revision,text:el("draft").value,action:saveKey});
  dirty=false;item.id=result.id;const ctx=await api("open",{id:result.id});
  Object.assign(item,ctx);el("save").textContent="Entwurf übernehmen";el("revise").textContent="Entwurf überarbeiten";saveKey=null;el("draft").value=ctx.draft;await loadList();notice("Entwurf in Mailcow aktualisiert.");
});
el("send").onclick=()=>task(async()=>{
  const text=editableBase();
  if([...picks.values()].some(row=>row.preview)){notice("Vorschau: Der markierte Platz kann erst im Livebetrieb reserviert und versendet werden.");return;}
  if(!item || !text.trim()){notice("Bitte zuerst einen Antworttext eingeben.");return;}
  if(!confirm("Antwort jetzt an "+item.to+" senden?"+(picks.size?" Dabei "+picks.size+" Termin(e) reservieren.":"")))return;
  const result=await api("send",{id:item.id,revision:item.revision,text,picks:[...picks.values()]});
  reset();
  notice(result.notice);
  try{await loadList();notice(result.notice);}catch{notice(result.notice+" Liste bitte aktualisieren.");}
});
el("adopt").onclick=()=>{
  if(!current||!canLeave())return;
  item={...current,draftSubject:item.draftSubject};el("draft").value=item.draft;dirty=false;saveKey=null;thread=[];renderThread();
  el("comparison").hidden=true;current=null;notice("Aktueller Mailcow-Stand geladen.");
};
const picks=new Map();
const slotToken="[[JOHM_TERMINE]]";
let draftBlock="";
function editableBase(){
  const text=el("draft").value;
  if(!draftBlock)return text;
  if(text.split(draftBlock).length!==2)throw new Error("Die Terminliste im Text wurde geändert. Bitte die Zeiten über die Terminschaltflächen wählen oder die letzte Textänderung rückgängig machen.");
  return text.replace(draftBlock,slotToken);
}
function applySlots(base,phrases){
  const rows=[...picks.values()].sort((a,b)=>a.index-b.index || a.start-b.start);
  const groups=new Set(rows.map(r=>r.index));
  const list=rows.map(r=>(groups.size>1?"["+String(r.index+1)+"] ":"")+"• "+r.label).join("\n");
  draftBlock=list?phrases.offer+"\n"+list+"\n\n"+phrases.deadline:"";
  let body=base.trim() || phrases.intro;
  if(list && phrases.salutation && /^(?:guten tag|hallo|hello|dear sir or madam)[,!]?(?:\n|$)/i.test(body)){
    body=body.replace(/^[^\n]+/,()=>phrases.salutation);
  }
  if(!body.includes(slotToken) && list){
    if(/(?:Termin|Vorsorge)[^\n]{0,120}:\s*\n+\[VON DER MFA ZU ERGÄNZEN\]/i.test(body)){
      body=body.replace(/^([^\n]*(?:Termin|Vorsorge)[^\n]{0,120}:)\s*\n+\[VON DER MFA ZU ERGÄNZEN\]$/im,slotToken);
    }else if(body.includes(phrases.contact)){
      body=body.replace(phrases.contact,slotToken+"\n\n"+phrases.contact);
    }else body+="\n\n"+slotToken;
  }
  body=body.replace(slotToken,draftBlock).replace(/\n{3,}/g,"\n\n").trim();
  if(list && !body.includes(phrases.contact))body+="\n\n"+phrases.contact;
  if(list && phrases.closing && !body.includes(phrases.closing.split("\n")[0]) && !/^(?:(?:mit |besten |vielen )?freundlichen grüßen|freundliche grüße|beste grüße|herzliche grüße|liebe grüße|viele grüße|kind regards|best regards|sincerely|yours sincerely|best wishes)[,! .]*$/im.test(body))body+="\n\n"+phrases.closing;
  el("draft").value=body;dirty=true;saveKey=null;
}
let calRequest=0, calOverride=null, calShown=null;
function clearCalendar(){
  ++calRequest;picks.clear();draftBlock="";calOverride=null;calShown=null;
  el("caldays").value="";el("calendar").hidden=true;el("offerpreview").hidden=true;el("wishes").replaceChildren();
}
function previewOffer(){
  const rows=[...picks.values()];
  const heading=rows.some(r=>r.preview)?"Vorschau der Terminauswahl":"Terminangebot beim Senden";
  el("offerpreview").textContent=rows.length?heading+":\n"+rows.map(r=>r.person+" · "+r.label).join("\n")+"\n"+rows[0].deadlineLabel:"";
  el("offerpreview").hidden=!rows.length;
}
async function loadCalendar(source,days,override){
  calOverride=override||null;
  if(draftBlock){try{el("draft").value=editableBase().replace(slotToken,"").trim();}catch(e){notice(e.message);return;}}
  const version=++calRequest;picks.clear();draftBlock="";previewOffer();
  el("calendar").hidden=false;el("wishes").replaceChildren();el("more").hidden=true;
  el("calstatus").textContent="Terminanfrage wird geprüft …";
  try{
    const result=await api("appointments",{id:source.id,revision:source.revision,days,override:calOverride});
    if(version!==calRequest || item!==source)return;
    const starts=result.wishes.flatMap(w=>w.options.map(o=>o.start));
    calShown=starts.length?{first:Math.min(...starts),last:Math.max(...starts)}:null;
    if(!result.wishes.length){el("calstatus").textContent="Kein Terminanliegen erkannt. Bitte im Original gegenlesen.";return;}
    el("caldays").value=result.days;el("caldays").max=result.maxDays;el("more").hidden=false;
    el("calstatus").textContent="Suchzeitraum: "+result.days+" Tage. "+(result.live?"Reservierung beim Senden.":"Vorschau · Kalenderbuchung ausgeschaltet.")+(result.skipped?" Bereits angebotene Termine werden nicht erneut vorgeschlagen.":"")+(calOverride?" Suche nach Vorgabe der MFA.":"")+(result.capped?" Gesucht wurden höchstens drei Monate am Stück.":"")+(result.untranslated?" Die Standardtexte ließen sich nicht in die Sprache der Mail übersetzen und stehen auf Englisch.":"");
    result.wishes.forEach((w,index)=>{
      const section=document.createElement("div");
      const heading=document.createElement("h4");
      heading.textContent=w.person+" · "+w.label+(w.hint?(w.hint.startsWith("Vorgabe")?" · ":" · Wunsch: ")+w.hint:"");
      section.append(heading);
      if(w.action==="verschieben"){
        const old=document.createElement("p");
        old.textContent=!w.old?"Bisheriger Termin im Kalender nicht eindeutig gefunden. Bitte von Hand freigeben.":"Bisheriger Termin laut Kalender: "+w.old+(w.old_ref?". Er wird freigegeben, sobald die Patientin den neuen Termin bestätigt.":". Der Name passt nicht eindeutig, bitte nach der Bestätigung von Hand freigeben.");
        section.append(old);
      }
      if(!w.options.length){
        const message=document.createElement("p");
        message.textContent=w.kind==="manuell"?"Bearbeitung durch die MFA.":"Kein passender Platz im Zeitraum.";
        section.append(message);
      }else{
        if(w.fallback){
          const note=document.createElement("p");
          note.textContent="Im Wunschzeitraum ist nichts frei. Nächste freie Termine danach, die zum Wunsch passenden zuerst:";
          section.append(note);
        }
        const name=document.createElement("input");
        name.placeholder="Patientenname, falls bekannt";
        name.setAttribute("aria-label",name.placeholder);
        const buttons=new Map();
        const toggle=option=>{
          if(item!==source || version!==calRequest || busy)return;
          if(picks.has(option.id) && picks.get(option.id).index!==index){
            el("calstatus").textContent="Dieser Platz ist bereits für einen anderen Terminwunsch markiert.";return;
          }
          const selected=!picks.has(option.id);
          if(selected && picks.size>=10){el("calstatus").textContent="Maximal zehn Plätze pro Angebot.";return;}
          let base;
          try{base=editableBase();}catch(e){notice(e.message);return;}
          if(selected)picks.set(option.id,{id:option.id,index,days:result.days,deadline:result.deadline,deadlineLabel:result.draftPhrases.deadline,name:name.value.trim(),label:option.label,start:option.start,person:w.person,kind:w.kind,year:w.year,preview:!result.live,override:calOverride});
          else picks.delete(option.id);
          applySlots(base,result.draftPhrases);
          for(const b of buttons.get(option.id))b.setAttribute("aria-pressed",String(selected));
          name.hidden=!result.live || ![...picks.values()].some(r=>r.index===index);
          previewOffer();
        };
        const chip=(option,text)=>{
          const button=document.createElement("button");
          button.type="button";button.textContent=text;
          button.title=option.label+(option.minutes?" ("+option.minutes+" Min)":"");
          button.setAttribute("aria-pressed","false");
          button.onclick=()=>toggle(option);
          if(!buttons.has(option.id))buttons.set(option.id,[]);
          buttons.get(option.id).push(button);
          return button;
        };
        // The closest matches first, one per day, in the order the server ranked them.
        const best=[];
        for(const option of w.options){
          if(!best.some(o=>o.date===option.date))best.push(option);
          if(best.length===3)break;
        }
        const bestLabel=document.createElement("p");bestLabel.className="hint";
        bestLabel.textContent=w.hint?"Am nächsten am Wunsch":"Nächste freie Termine";
        const bestRow=document.createElement("div");bestRow.className="slots";
        best.forEach(option=>bestRow.append(chip(option,option.label+(option.minutes?" ("+option.minutes+" Min)":""))));
        section.append(bestLabel,bestRow);
        // Filters work on what is already loaded.
        const doctors=[...new Set(w.options.map(o=>o.doctor).filter(Boolean))];
        const short=doctor=>doctor.trim().split(/\s+/).pop().slice(0,2);
        const state={doctor:"",part:""};
        const bar=document.createElement("div");bar.className="filters";
        const weeksBox=document.createElement("div");
        const cells=[];
        const apply=()=>{
          for(const week of weeksBox.children){
            let weekCount=0;
            for(const day of week.querySelectorAll(".day")){
              let dayCount=0;
              for(const band of day.querySelectorAll(".band")){
                let bandCount=0;
                for(const b of band.querySelectorAll("button")){
                  const off=(state.doctor && b.dataset.doctor!==state.doctor) || (state.part!=="" && b.dataset.pm!==state.part);
                  b.hidden=off;if(!off)bandCount++;
                }
                band.hidden=!bandCount;dayCount+=bandCount;
              }
              day.querySelector(".count").textContent=dayCount+" frei";
              day.classList.toggle("empty",!dayCount);weekCount+=dayCount;
            }
            week.querySelector("summary").textContent=week.dataset.label+" · "+weekCount+" frei";
          }
        };
        const filter=(label,key,value)=>{
          const b=document.createElement("button");
          b.type="button";b.textContent=label;b.dataset.key=key;b.dataset.value=value;
          b.setAttribute("aria-pressed",String(state[key]===value));
          b.onclick=()=>{
            state[key]=value;
            for(const x of bar.querySelectorAll("button"))if(x.dataset.key===key)x.setAttribute("aria-pressed",String(x.dataset.value===value));
            apply();
          };
          return b;
        };
        if(doctors.length>1){bar.append(filter("Alle Ärzte","doctor",""));doctors.forEach(d=>bar.append(filter(d,"doctor",d)));}
        bar.append(filter("Ganzer Tag","part",""),filter("Vormittag","part","0"),filter("Nachmittag","part","1"));
        section.append(bar,weeksBox);
        // One block per week, weekdays in fixed columns, mornings above afternoons.
        const weeks=new Map();
        for(const option of [...w.options].sort((x,y)=>x.start-y.start)){
          if(!weeks.has(option.week))weeks.set(option.week,new Map());
          const days=weeks.get(option.week);
          if(!days.has(option.date))days.set(option.date,[]);
          days.get(option.date).push(option);
        }
        const columns=Math.max(5,...w.options.map(o=>o.weekday));
        let opened=false;
        for(const days of weeks.values()){
          const all=[...days.values()];
          const box=document.createElement("details");
          box.open=!opened;opened=true;
          box.dataset.label="KW "+all[0][0].kw+" · "+all[0][0].day+(all.length>1?" bis "+all[all.length-1][0].day:"");
          const summary=document.createElement("summary");
          const grid=document.createElement("div");grid.className="week";
          grid.style.gridTemplateColumns="repeat("+columns+",minmax(0,1fr))";
          for(const list of all){
            const day=document.createElement("div");
            day.className="day"+((w.weekdays||[]).includes(list[0].weekday)?" wished":"");
            day.style.gridColumn=String(list[0].weekday);
            const head=document.createElement("div");head.className="dayhead";
            const title=document.createElement("span");title.textContent=list[0].day;
            const count=document.createElement("span");count.className="count";
            head.append(title,count);day.append(head);
            for(const pm of [false,true]){
              const band=document.createElement("div");band.className="slots band";
              for(const option of list.filter(o=>o.pm===pm)){
                const b=chip(option,option.time+(doctors.length>1?" "+short(option.doctor):""));
                b.dataset.doctor=option.doctor||"";b.dataset.pm=pm?"1":"0";
                band.append(b);
              }
              day.append(band);
            }
            grid.append(day);
          }
          box.append(summary,grid);weeksBox.append(box);
        }
        apply();
        if(result.live){
          name.hidden=true;
          name.oninput=()=>{for(const pick of picks.values())if(pick.index===index)pick.name=name.value.trim();};
          section.append(name);
        }
      }
      el("wishes").append(section);
    });
  }catch(e){if(version===calRequest){el("calstatus").textContent="Kalendersuche nicht verfügbar. "+e.message;}}
}
el("calsearch").onclick=()=>{if(item)loadCalendar(item,Number(el("caldays").value),calOverride);};
window.addEventListener("pagehide",()=>{reset();el("list").replaceChildren();});
window.addEventListener("pageshow",e=>{if(e.persisted)task(loadList);});
for(const button of el("list").querySelectorAll("button[data-id]"))bindRow(button,button.dataset);
el("boot-note").hidden=true;
refreshAlerts();
