"""Explicit staff-initiated submission. The journal contains no addresses or message bodies."""
import appointments
import hashlib
import re
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


# ---------------------------------------------------------------- Weiterleiten

FORWARD_LIMIT = 25 * 1024 * 1024
ADDRESS = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}")


def forward_journal():
    db = sqlite3.connect(review.state() / "versand.sqlite3", timeout=30)
    # Only a hash of the recipients, so the journal still holds no addresses.
    db.execute("""CREATE TABLE IF NOT EXISTS forwarded (
        account TEXT, uid INTEGER, target TEXT, state TEXT, PRIMARY KEY(account, uid, target))""")
    return db


def recipients(value):
    """Up to five plain addresses, separated by comma, semicolon or line break. Display names are dropped."""
    from email.utils import getaddresses
    from mail import FAX_ADDRESS
    if not isinstance(value, str) or any(ch in value for ch in "\r\0"):
        raise ValueError("Empfänger fehlt.")
    found = []
    for _, addr in getaddresses([re.sub(r"[;\n]", ",", value)]):
        addr = addr.strip()
        if not ADDRESS.fullmatch(addr):
            raise ValueError("Ungültige Empfängeradresse. Bitte prüfen.")
        if addr.casefold() == FAX_ADDRESS:
            raise ValueError("Weiterleiten an das Faxgateway ist gesperrt.")
        if addr.casefold() not in {a.casefold() for a in found}:
            found.append(addr)
    if not found:
        raise ValueError("Empfänger fehlt.")
    if len(found) > 5:
        raise ValueError("Höchstens fünf Empfänger auf einmal.")
    return found


def seven_bit(raw):
    """The original unchanged in content, with 8-bit parts re-encoded so it can travel as an attached message."""
    import io
    from email.generator import BytesGenerator
    from email.parser import BytesParser
    if raw.isascii():
        return raw
    out = io.BytesIO()
    BytesGenerator(out, policy=policy.compat32.clone(cte_type="7bit", linesep="\r\n")).flatten(
        BytesParser(policy=policy.compat32).parsebytes(raw))
    return out.getvalue()


def forward(c, cfg, folder, item, revision, to, note, password, context, review_context, Conflict):
    """Forward the original incoming mail, complete with its attachments, to any address the MFA enters.
    The original stays in the inbox and is marked as forwarded."""
    from email.parser import BytesParser
    from mail import mailbody
    _, ctx = review_context(c, cfg, folder, item) if item.startswith("r-") else context(c, folder, item)
    if revision != ctx["revision"]:
        raise Conflict("Die Nachricht wurde geändert. Bitte neu öffnen.")
    targets = recipients(to)
    note = (note or "").strip()
    if len(note) > 4000:
        raise ValueError("Notiz zu lang.")
    valid, uid = map(int, ctx["source"].split("-"))
    if c.select_folder("INBOX", readonly=True)[b"UIDVALIDITY"] != valid:
        raise Conflict("Posteingang geändert. Bitte die Liste aktualisieren.")
    info = c.fetch([uid], ["RFC822.SIZE", "FLAGS"]).get(uid)
    if not info or b"\\Deleted" in info[b"FLAGS"]:
        raise Conflict("Originalmail verschoben oder gelöscht. Bitte die Liste aktualisieren.")
    if info[b"RFC822.SIZE"] > FORWARD_LIMIT:
        raise ValueError("Die Mail ist für das Weiterleiten zu groß. Bitte in Mailcow weiterleiten.")
    raw = c.fetch([uid], ["BODY.PEEK[]"]).get(uid, {}).get(b"BODY[]")
    if not raw:
        raise Conflict("Originalmail nicht mehr verfügbar.")
    original = BytesParser(policy=policy.default).parsebytes(raw)
    line = lambda value: " ".join(str(value or "").split())
    subject = line(original.get("Subject", ""))
    try:
        text = mailbody(original) or ""
    except (ValueError, LookupError, TypeError):
        text = ""
    msg = EmailMessage(policy=policy.SMTP)
    msg["From"] = cfg["sender"]
    msg["To"] = ", ".join(targets)
    msg["Subject"] = subject if re.match(r"(?i)\s*(?:wg|fwd?|fw)\s*:", subject) else "WG: " + subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=cfg["sender"].rsplit("@", 1)[-1])
    head = "\n".join(f"{label}: {line(original.get(key, ''))}" for label, key in
                     (("Von", "From"), ("Datum", "Date"), ("Betreff", "Subject"), ("An", "To")) if original.get(key))
    msg.set_content((note + "\n\n" if note else "") + "---------- Weitergeleitete Nachricht ----------\n" + head + "\n\n"
                    + (text[:40000] if text else "[Der Text steht in der angehängten Originalmail.]") + "\n")
    msg.add_attachment(seven_bit(raw), maintype="message", subtype="rfc822", cte="7bit", filename="Originalmail.eml")
    sent = special(c, b"\\sent", {"sent", "gesendet", "sent items", "sent messages"})
    if not sent or sent == "INBOX":
        raise ValueError("Gesendet-Ordner nicht eindeutig. Bitte in Mailcow prüfen.")
    target = hashlib.sha256("\n".join(sorted(a.casefold() for a in targets)).encode()).hexdigest()
    key = (review.account(cfg, valid), uid, target)
    with closing(forward_journal()) as db:
        prev = db.execute("SELECT state FROM forwarded WHERE account=? AND uid=? AND target=?", key).fetchone()
        if prev:
            raise Conflict("An diese Empfänger bereits weitergeleitet (" + prev[0] + ").")
        with closing(smtplib.SMTP(cfg.get("smtp_host") or cfg["host"], 587, timeout=60)) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            try:
                smtp.login(cfg["username"], password)
            except smtplib.SMTPAuthenticationError:
                raise ValueError("Mailcow hat die SMTP-Anmeldung abgelehnt. App-Passwort auf SMTP-Freigabe prüfen.") from None
            out = msg.as_bytes()
            with db:
                db.execute("INSERT INTO forwarded VALUES(?,?,?,'Versand ungeklärt')", key)
            try:
                refused = smtp.sendmail(cfg["sender"], targets, out)
            except smtplib.SMTPRecipientsRefused:
                with db:
                    db.execute("DELETE FROM forwarded WHERE account=? AND uid=? AND target=?", key)
                raise ValueError("Mailcow hat die Empfänger abgelehnt. Adressen prüfen.") from None
            except (smtplib.SMTPResponseException, smtplib.SMTPNotSupportedError):
                with db:
                    db.execute("DELETE FROM forwarded WHERE account=? AND uid=? AND target=?", key)
                raise ValueError("SMTP hat die Nachricht abgelehnt. Größe und Absenderberechtigung prüfen.") from None
            except OSError:
                raise Conflict("Verbindung beim Weiterleiten unterbrochen. Versandstatus unklar. Bitte Gesendet und Mailcow-Protokoll prüfen.") from None
        with db:
            db.execute("UPDATE forwarded SET state=? WHERE account=? AND uid=? AND target=?",
                       ("weitergeleitet" if not refused else "teilweise weitergeleitet", *key))
        missing = len(refused or {})
        done = "An " + str(len(targets) - missing) + " Empfänger weitergeleitet" + (f", {missing} abgelehnt" if missing else "")
        try:
            c.append(sent, out, flags=[b"\\Seen"])
            if c.select_folder("INBOX", readonly=False)[b"UIDVALIDITY"] == valid:
                c.add_flags([uid], [b"$Forwarded"], silent=True)
        except Exception:
            return {"notice": done + ". Ablage in Gesendet bitte in Mailcow prüfen."}
    return {"notice": done + ". Kopie in Gesendet, Original bleibt im Posteingang."}
