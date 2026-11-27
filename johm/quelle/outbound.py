"""Explicit staff-initiated submission. The journal contains no addresses or message bodies."""
import appointments
import hashlib
import smtplib
import sqlite3
import ssl
from contextlib import closing
from email import policy
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

import review


def journal():
    db = sqlite3.connect(review.state() / "versand.sqlite3", timeout=30)
    db.execute("""CREATE TABLE IF NOT EXISTS submission (
        account TEXT, uid INTEGER, state TEXT, PRIMARY KEY(account, uid))""")
    return db


def special(c, flag, fallback):
    selectable = [(name, {f.lower() for f in flags}) for flags, _, name in c.list_folders()
                  if b"\\noselect" not in {f.lower() for f in flags}]
    matches = [name for name, flags in selectable if flag in flags]
    if not matches:
        matches = [name for name, _ in selectable if name.casefold() in fallback]
    return matches[0] if len(matches) == 1 else None


def done_folder(c, drafts):
    name = "Erledigt"
    if drafts.casefold() == name.casefold() or not c.has_capability("MOVE"):
        raise ValueError("Für Erledigt werden ein eigener Ordner und IMAP MOVE benötigt.")
    if not c.folder_exists(name):
        c.create_folder(name)
    flags = next(({f.lower() for f in flags} for flags, _, folder in c.list_folders()
                  if folder == name), None)
    if flags is None or flags.intersection({b"\\noselect", b"\\trash", b"\\drafts", b"\\sent", b"\\junk"}):
        raise ValueError("Der Ordner Erledigt ist kein geeigneter Zielordner.")
    return name


def move_original(c, valid, uid, target):
    if c.select_folder("INBOX", readonly=False)[b"UIDVALIDITY"] != valid:
        raise ValueError("Der Posteingang wurde neu aufgebaut. Bitte die Liste aktualisieren.")
    entry = c.fetch([uid], ["FLAGS"]).get(uid)
    if not entry or b"\\Deleted" in entry[b"FLAGS"]:
        raise ValueError("Die Eingangsmail wurde bereits verschoben oder gelöscht. Bitte aktualisieren.")
    c.move([uid], target)
    if uid in c.fetch([uid], ["FLAGS"]):
        raise ValueError("Die Eingangsmail liegt weiterhin im Posteingang. Bitte die Ablage prüfen.")


def submit(c, cfg, folder, item, revision, text, password, context, review_context, Conflict, picks=None):
    from mail import reply_address, FAX_ADDRESS
    is_new = item.startswith("r-")
    old, ctx = review_context(c, cfg, folder, item) if is_new else context(c, folder, item)
    if revision != ctx["revision"]:
        raise Conflict("Die Nachricht wurde geändert. Bitte neu öffnen.")
    if is_new:
        address = reply_address(old)
        ref = review.msgid(old)
    else:
        fields = old.get_all("To", [])
        if len(fields) != 1 or fields[0].defects or len(fields[0].addresses) != 1 or any(
            k in old for k in ("Bcc", "Cc", "Resent-To", "Resent-Cc", "Resent-Bcc")
        ) or any(p.get_content_disposition()=="attachment" or p.get_filename() for p in old.walk()):
            raise ValueError("Entwürfe mit zusätzlichen Empfängern oder Anhängen bitte in Mailcow senden.")
        address = fields[0].addresses[0]
        ref = str(old.get("In-Reply-To", ""))
    if not address or not address.domain or not address.username:
        raise ValueError("Kein eindeutiger Empfänger. Bitte in Mailcow prüfen.")
    if address.addr_spec.casefold()==FAX_ADDRESS:
        raise ValueError("Versand an das Faxgateway ist gesperrt.")
    msg = EmailMessage(policy=policy.SMTP)
    msg["From"], msg["To"] = cfg["sender"], address
    subject = " ".join(str(old.get("Subject", "")).splitlines())
    msg["Subject"] = "Re: "+subject if is_new and not subject.lower().startswith("re:") else subject
    if ref:
        msg["In-Reply-To"] = ref
        refs = str(old.get("References","")).split()[-15:]
        if ref not in refs: refs.append(ref)
        msg["References"] = " ".join(refs)
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=cfg["sender"].rsplit("@",1)[-1])
    msg.set_content(text)
    raw = msg.as_bytes()
    sent = special(c, b"\\sent", {"sent", "gesendet", "sent items", "sent messages"})
    if not sent or sent in (folder,"INBOX"):
        raise ValueError("Gesendet-Ordner nicht eindeutig. Bitte in Mailcow prüfen.")
    valid, uid = map(int,ctx["source"].split("-"))
    key = (review.account(cfg,valid),uid)
    with closing(journal()) as db:
        prev = db.execute("SELECT state FROM submission WHERE account=? AND uid=?",key).fetchone()
        if prev: raise Conflict("Versandstatus: "+prev[0]+". Keine erneute Übermittlung.")
        if c.select_folder("INBOX",readonly=True)[b"UIDVALIDITY"] != valid:
            raise Conflict("Posteingang geändert.")
        flags = c.fetch([uid],["FLAGS"]).get(uid)
        if not flags or {f.lower() for f in flags[b"FLAGS"]} & {b"\\answered", b"\\deleted"}:
            raise Conflict("Original bereits beantwortet oder entfernt.")
        c.select_folder(sent,readonly=True)
        if ref and c.search(["UNDELETED","HEADER","In-Reply-To",ref]):
            raise Conflict("In Gesendet existiert bereits eine Antwort auf diese Mail.")
        target = done_folder(c, folder)
        with closing(smtplib.SMTP(cfg.get("smtp_host") or cfg["host"],587,timeout=40)) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            try:
                smtp.login(cfg["username"],password)
            except smtplib.SMTPAuthenticationError:
                raise ValueError("Mailcow hat die SMTP-Anmeldung abgelehnt. App-Passwort auf SMTP-Freigabe prüfen.") from None
            with appointments.prepare(picks,ctx,msg,address.addr_spec) as accepted:
                raw=msg.as_bytes()
                with db: db.execute("INSERT INTO submission VALUES(?,?,'Versand ungeklärt')",key)
                try:
                    smtp.send_message(msg, from_addr=cfg["sender"], to_addrs=[address.addr_spec])
                except (smtplib.SMTPResponseException, smtplib.SMTPRecipientsRefused, smtplib.SMTPNotSupportedError):
                    with db: db.execute("DELETE FROM submission WHERE account=? AND uid=?",key)
                    raise ValueError("SMTP hat die Nachricht abgelehnt. Adresse und Absenderberechtigung prüfen.") from None
                except OSError:
                    raise appointments.Uncertain("Verbindung beim Senden unterbrochen. Versandstatus unklar. Bitte Mailcow-Protokoll prüfen.") from None
                accepted()
        status = "Versendet · Ablage prüfen"
        with db: db.execute("UPDATE submission SET state=? WHERE account=? AND uid=?",(status,*key))
        try:
            c.append(sent, raw, flags=[b"\\Seen"])
            if c.select_folder("INBOX",readonly=False)[b"UIDVALIDITY"] != valid:
                raise ValueError("Posteingang geändert")
            c.add_flags([uid],[b"\\Answered"],silent=True)
            if not is_new:
                dv, du = map(int,item.split("-"))
                if c.select_folder(folder,readonly=False)[b"UIDVALIDITY"] != dv:
                    raise ValueError("Entwurfsordner geändert")
                entry=c.fetch([du],["BODY.PEEK[]"]).get(du)
                if entry:
                    if hashlib.sha256(entry[b"BODY[]"]).hexdigest()!=revision[:64]:
                        raise ValueError("Entwurf inzwischen geändert")
                    trash=special(c,b"\\trash",{"trash","papierkorb","deleted items"})
                    if not trash or trash in (folder,sent,"INBOX") or not c.has_capability("MOVE"):
                        raise ValueError("Papierkorb nicht verfügbar")
                    c.move([du],trash)
            move_original(c, valid, uid, target)
            status="Versendet"
            with db: db.execute("UPDATE submission SET state=? WHERE account=? AND uid=?",(status,*key))
        except Exception:
            return {"notice":"Mailcow hat die Antwort zum Versand angenommen. Bitte Gesendet, Erledigt und Entwurfsordner prüfen."}
    return {"notice":"Antwort von Mailcow zum Versand angenommen und in Gesendet abgelegt. Original nach Erledigt verschoben."}
