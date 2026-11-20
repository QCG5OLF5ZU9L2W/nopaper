# SPDX-License-Identifier: AGPL-3.0-or-later
"""Kommandozeile: nopaper run | once | check | nachbearbeiten <id> | paperless-skript | status"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys

from . import VERSION, config, llm
from .config import FELD_TYPEN, ConfigError
from .paperless import PaperlessFehler, Voruebergehend


def _paperless_skript(cfg: config.Config, benutzer: str) -> str:
    perms = ["add_document", "view_document", "change_document", "view_tag", "view_customfield",
             "view_paperlesstask"]
    auth_perms = []
    if cfg.besitzer or cfg.ansehen_gruppen or cfg.bearbeiten_gruppen or cfg.ansehen_benutzer \
            or cfg.bearbeiten_benutzer:
        auth_perms = ["view_user", "view_group"]
    felder = {name: FELD_TYPEN[key] for key, name in cfg.felder.items()}
    return f'''# Erzeugt von nopaper {VERSION}. Im paperless-Verzeichnis ausfuehren:
#   docker compose exec -T webserver python3 manage.py shell < nopaper-setup.py
# (Bare-Metal: cd <paperless>/src && sudo -u paperless python3 manage.py shell < nopaper-setup.py)
#
# Legt an: API-Benutzer {benutzer!r} (kein Passwort, kein Admin, minimale Rechte),
# Tags (Zuweisung "Keine"), benutzerdefinierte Felder. Gibt das Token aus.
from django.contrib.auth.models import Permission, User
from rest_framework.authtoken.models import Token
from documents.models import CustomField, Tag

BENUTZER = {benutzer!r}
TAGS = {json.dumps(cfg.alle_tags, ensure_ascii=False)}
FELDER = {json.dumps(felder, ensure_ascii=False)}
PERMS_DOCUMENTS = {perms!r}
PERMS_AUTH = {auth_perms!r}

u, neu = User.objects.get_or_create(username=BENUTZER)
u.set_unusable_password(); u.is_staff = False; u.is_superuser = False; u.save()
p = list(Permission.objects.filter(content_type__app_label="documents", codename__in=PERMS_DOCUMENTS))
p += list(Permission.objects.filter(content_type__app_label="auth", codename__in=PERMS_AUTH))
u.user_permissions.set(p)
print("Benutzer", BENUTZER, "angelegt" if neu else "aktualisiert", "-", len(p), "Rechte")

for name in TAGS:
    t, neu = Tag.objects.get_or_create(name=name, defaults={{"matching_algorithm": 0}})
    print("Tag", repr(name), "angelegt" if neu else "vorhanden")

for name, typ in FELDER.items():
    f = CustomField.objects.filter(name__iexact=name).first()
    if f is None:
        CustomField.objects.create(name=name, data_type=typ)
        print("Feld", repr(name), "angelegt (" + typ + ")")
    elif f.data_type != typ:
        print("WARNUNG: Feld", repr(name), "hat Typ", f.data_type, "erwartet", typ)
    else:
        print("Feld", repr(name), "vorhanden")

t, _ = Token.objects.get_or_create(user=u)
print("TOKEN=" + t.key)
'''


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="nopaper", description=f"nopaper {VERSION}")
    ap.add_argument("--config", default=config.DEFAULT_PATH)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="Dauerbetrieb (systemd)")
    sub.add_parser("once", help="ein Durchlauf")
    sub.add_parser("check", help="Konfiguration, Ollama und paperless pruefen")
    sub.add_parser("status", help="Zustand der Warteschlange anzeigen")
    nb = sub.add_parser("nachbearbeiten", help="vorhandene paperless-Dokumente als Rechnung verarbeiten")
    nb.add_argument("ids", nargs="+", type=int)
    tl = sub.add_parser("test", help="Testlauf OHNE paperless mit Musterbelegen aus /srv/nopaper/test/eingang")
    tl.add_argument("--als", choices=["auto", "rechnung", "dokument"], default="auto",
                    help="wie der Scanner-Ordner: rechnung/dokument; auto = nur die KI entscheidet")
    tl.add_argument("--modell-text", help="anderes Textmodell fuer den Vergleich")
    tl.add_argument("--modell-bild", help="anderes Bildmodell fuer den Vergleich (leer = kein Rueckfall)")
    tl.add_argument("--extraktor", choices=["standard", "nuextract3"], help="Extraktionsmodell-Familie")
    tl.add_argument("--modell-extraktion", help="Modell fuer --extraktor nuextract3, z.B. numind/nuextract3:Q4_K_M")
    tl.add_argument("--nuextract-eingabe", choices=["bild", "text"], help="NuExtract liest Seitenbild oder OCR-Text")
    tl.add_argument("--modell-glaettung", help="Glaettungsmodell; 'aus' = keine Glaettung (schnellerer Vergleich)")
    tl.add_argument("--aufraeumen", action="store_true", help="Testeingang und Testergebnisse loeschen")
    ps = sub.add_parser("paperless-skript", help="Setup-Skript fuer die paperless-Django-Shell ausgeben")
    ps.add_argument("--benutzer", default="nopaper")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stdout)
    try:
        cfg = config.load(args.config, read_token=args.cmd not in ("paperless-skript", "test"),
                          testlauf=args.cmd == "test")
    except ConfigError as e:
        print(f"Konfigurationsfehler: {e}", file=sys.stderr)
        return 2

    if args.cmd == "paperless-skript":
        sys.stdout.write(_paperless_skript(cfg, args.benutzer))
        return 0

    if args.cmd == "test":
        from . import testlauf
        if args.aufraeumen:
            testlauf.aufraeumen(cfg)
            return 0
        if args.modell_text:
            cfg.modell_text = args.modell_text
        if args.modell_bild is not None:
            cfg.modell_bild = args.modell_bild
        if args.extraktor:
            cfg.extraktor = args.extraktor
        if args.modell_extraktion:
            cfg.modell_extraktion = args.modell_extraktion
        if args.nuextract_eingabe:
            cfg.nuextract_eingabe = args.nuextract_eingabe
        if args.modell_glaettung is not None:
            cfg.modell_glaettung = args.modell_glaettung
        if cfg.extraktor == "nuextract3" and not cfg.modell_extraktion:
            cfg.modell_extraktion = "numind/nuextract3:Q4_K_M"
        for d in (cfg.test / "eingang", cfg.test / "ergebnis", cfg.arbeit):
            d.mkdir(parents=True, exist_ok=True)
        try:
            testlauf.testlauf(cfg, args.als)
        except llm.OllamaNichtErreichbar:
            print("Ollama nicht erreichbar (systemctl status ollama)", file=sys.stderr)
            return 1
        return 0

    from .pipeline import Nopaper
    np = Nopaper(cfg)

    if args.cmd == "check":
        ok = True
        print(f"     Betriebsart: {cfg.betriebsart}" + (" (erst paperless, zustandslos)" if cfg.zustandslos else ""))
        for d in ((cfg.arbeit,) if cfg.zustandslos else
                  (cfg.eingang_rechnungen, cfg.eingang_dokumente, cfg.fehler, cfg.ausgang_rechnungsdaten,
                   cfg.arbeit, cfg.zustand)):
            w = d.is_dir() and os.access(d, os.W_OK)
            ok &= w
            print(f"{'ok  ' if w else 'FEHL'} Verzeichnis {d}")
        from . import codes as qr
        will_codes = "barcode" in cfg.felder or "qrcode" in cfg.felder
        if qr.verfuegbar():
            print("ok   Strich- und QR-Codes (zbarimg)")
        elif will_codes:
            ok = False
            print("FEHL zbarimg fehlt – felder.barcode/qrcode brauchen das Paket zbar-tools (install.sh erneut starten)")
        else:
            print("     zbarimg fehlt – Strich- und QR-Codes werden nicht gelesen (Paket zbar-tools)")
        try:
            models = np.ki.modelle()
            noetig = [cfg.modell_text, cfg.modell_bild, cfg.modell_glaettung if cfg.modell_glaettung != "aus" else "",
                      cfg.modell_extraktion if cfg.extraktor == "nuextract3" else ""]
            for m in dict.fromkeys(filter(None, noetig)):
                da = any(x == m or x == m + ":latest" for x in models)
                ok &= da
                print(f"{'ok  ' if da else 'FEHL'} Modell {m}")
        except llm.OllamaNichtErreichbar:
            ok = False
            print("FEHL Ollama nicht erreichbar")
        try:
            np.ids_laden()
            print(f"ok   paperless {cfg.paperless_url}: {len(cfg.alle_tags)} Tags, {len(cfg.felder)} Felder")
            np.pl.json("GET", "/api/tasks/?task_id=00000000-0000-0000-0000-000000000000")
            alle = np.pl.json("GET", "/api/tasks/")
            alle = alle if isinstance(alle, list) else (alle.get("results") or [])
            fmt = "keine Aufgabe sichtbar" if not alle else (
                "Format 3.x" if "related_document_ids" in alle[0] else
                "Format 2.x" if "related_document" in alle[0] else "UNBEKANNTES FORMAT")
            ok &= not fmt.startswith("UNBEKANNT")
            print(f"{'FEHL' if fmt.startswith('UNBEKANNT') else 'ok  '} paperless Aufgaben-API ({fmt})")
            if cfg.zustandslos:
                typ = np.felder["sidecar"][1]
                if typ in ("string", "url"):
                    ok = False
                    print(f"FEHL Feld '{cfg.felder['sidecar']}' hat den Datentyp '{typ}' – der ist fuer kurze "
                          "Werte. Der Sidecar braucht ein Feld fuer langen Text.")
                else:
                    print(f"ok   Sidecar-Feld '{cfg.felder['sidecar']}' (Datentyp {typ or 'unbekannt'})")
            print(f"     offene Nachbearbeitungen: {len(np.pl.mit_tag(np.tag_ausloeser))}")
            if np.tag_text:
                print(f"     offene Text-Erneuerungen: {len(np.pl.mit_tag(np.tag_text))}")
        except PaperlessFehler as e:
            ok = False
            print(f"FEHL paperless: {e.code} {e.detail}")
        except Voruebergehend as e:
            ok = False
            print(f"FEHL paperless nicht erreichbar: {e}")
        if np.kumero is not None:
            # kumero nicht erreichbar ist ein Hinweis, kein Startverbot: der Dienst laeuft auch ohne
            code = np.kumero.pruefen()
            if code is None:
                print(f"ok   kumero {cfg.kumero_url} (Mandant {cfg.kumero_mandant}) bereit")
            else:
                print(f"WARN kumero: {code} – Sidecars bleiben in der Warteschlange")
        print("Alles in Ordnung." if ok else "Es gibt Fehler – siehe oben.")
        return 0 if ok else 1

    if args.cmd == "status" and cfg.zustandslos:
        # E61: der Zustand steht in paperless – offen ist, was einen Ausloeser-Tag traegt
        np.ids_laden()
        print(f"offen  Rechnung auslesen   {len(np.pl.mit_tag(np.tag_ausloeser))}")
        if np.tag_text:
            print(f"offen  Volltext erneuern   {len(np.pl.mit_tag(np.tag_text))}")
        if np.tag_pruefen:
            print(f"zur Pruefung markiert      {len(np.pl.mit_tag(np.tag_pruefen))}")
        return 0

    if args.cmd == "status":
        rows = np.state.db.execute("SELECT status, COUNT(*) n FROM eingang GROUP BY status").fetchall()
        for r in rows:
            print(f"eingang {r['status']:12} {r['n']}")
        for r in np.state.db.execute("SELECT sha256, versuche, fehler FROM eingang "
                                     "WHERE status IN ('wartend','hochgeladen') ORDER BY angelegt"):
            print(f"  offen {r['sha256'][:12]} versuche={r['versuche']} fehler={r['fehler'] or '-'}")
        if np.kumero is not None:
            u = np.kumero.uebersicht()
            alt = f", aeltester seit {u['aeltester_s'] // 60} min" if u["wartend"] else ""
            print(f"kumero  wartend {u['wartend']}{alt}, abgelehnt {u['abgelehnt']}")
            if u["wartend"] and u["aeltester_s"] > 24 * 3600:
                print("  WARNUNG: der aelteste Sidecar wartet laenger als einen Tag")
        return 0

    if args.cmd == "nachbearbeiten":
        rc = 0
        for i in args.ids:
            try:
                print(i, np.nachbearbeiten(i, erzwingen=True))   # von Hand: der Mensch hat entschieden
            except (PaperlessFehler, Voruebergehend, llm.OllamaNichtErreichbar) as e:
                print(i, "FEHLER", getattr(e, "code", e))
                rc = 1
        return rc

    if args.cmd == "once":
        np.ein_durchlauf()
        return 0

    np.laufen()
    return 0


if __name__ == "__main__":
    sys.exit(main())
