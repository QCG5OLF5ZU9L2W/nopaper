# Änderungen

Neueste Fassung zuerst. Ältere Fassungen (1.0 bis 1.4.0) sind in `AGENTS.md` unter den Entscheidungen E1–E37 beschrieben.

## 1.8.2

- **Ein Auslöser für jedes Dokument** (E69): Trägt ein Dokument den Tag aus `tags.ausloeser_nachbearbeitung` und ist es keine Rechnung, behandelt nopaper es wie im Volltext-Weg: Volltext erneuern, Termin einer Ladung und Codes in die Felder, Auslöser entfernen. Keine Rechnungs-Tags, kein Sidecar, kein Prüf-Tag für gewöhnliche Schreiben.
- **Schreiben eines Gerichts oder einer Behörde ohne Betrag** gelten nicht mehr als Beleg. Anlass: Eine Ladung wurde als Rechnung der Kategorie Behörde angelegt.
- Sieht ein Dokument nach einem Beleg aus, dessen Betrag nicht gelesen wurde, bekommt es den Prüf-Tag.

## 1.8.1

- **Duplikatsuche in der Betriebsart „erst paperless“ repariert:** paperless lehnte die Feldabfrage mit HTTP 400 ab, weil es den verwendeten Operator nicht kennt; die Duplikatsuche fiel dadurch aus. nopaper fragt jetzt nur noch ein Feld mit einem Operator ab, den paperless dokumentiert, und vergleicht Rechnungsnummer und Rechnungssteller selbst genau.

## 1.8.0

- **Strichcodes und QR-Codes** (E67): nopaper liest die Codes der ersten drei Seiten mit `zbarimg` (Paket `zbar-tools`, wird von `install.sh` mitinstalliert), ohne Sprachmodell.
  - `felder.barcode` (Text) nimmt die Strichcodes auf, `felder.qrcode` (langer Text) die QR-Codes. Beide optional, im Rechnungsweg und im Volltext-Weg.
  - **Überweisungs-QR-Code auf Rechnungen:** IBAN und Verwendungszweck werden aus dem Code übernommen; stimmt sein Betrag mit dem gelesenen überein, gilt der Betrag als belegt, sonst geht der Beleg in die Prüfung.
  - `nopaper check` zeigt, ob der Leser vorhanden ist.
- Noch nicht mit `zbarimg` selbst erprobt.

## 1.7.4

- **Dokumentdatum in paperless** (E66): In der Betriebsart „erst paperless“ setzt nopaper „Erstellt am“ auf das Rechnungsdatum, wenn dieses gesichert ist. paperless nimmt sonst das erste Datum im Text, auf Formularen oft ein Geburtsdatum. Abschaltbar mit `"dokumentdatum_setzen": false`.
- **Ein Geburtsdatum ist nie das Rechnungsdatum:** Ein Datum, das im Text nur als Geburtsdatum bezeichnet ist, wird verworfen (`RECHNUNGSDATUM_VERWORFEN`).
- **Handschriftliches Datum und handschriftlicher Betrag:** Lesen NuExtract am Bild und das Rückfall-Bildmodell denselben Wert, gilt er als belegt (`DATUM_ZWEI_BILDLESUNGEN`, `BRUTTO_ZWEI_BILDLESUNGEN`), auch wenn die Texterkennung ihn nicht lesen kann.

## 1.7.3

- **Steuer nur, wenn der Beleg sie ausweist** (E65): Liefert das Modell einen Steuerbetrag, auf dem Beleg steht aber weder ein Prozentsatz noch ein Steuerwort (oder nur ein Befreiungshinweis), und der rechnerische Satz ist weder 7 noch 19 %, setzt nopaper Steuer 0 und Netto = Brutto (`STEUER_NICHT_AUSGEWIESEN`). Eine vom Modell gelieferte, in sich stimmige Steuerzeile zu 7 oder 19 % bleibt unangetastet. Anlass: Porto wurde als Steuer gelesen.
- Beide Prompts stellen klar, dass Porto, Versand und Gebühren keine Steuer sind. Musterbelege neu messen.

## 1.7.2

- **Von Hand eingetragene Rechnungsnummer** (E64): Steht auf einem Scan eine Bezeichnung wie „RE-Nr.“ und hat die erste Lesung keine Nummer gefunden, fragt nopaper das Bildmodell. Liest nur dieses eine Nummer, kommt sie ins Feld und der Beleg in die Prüfung. Lesen NuExtract und das Rückfall-Bildmodell dieselbe Nummer, gilt sie als belegt.
- Beide Prompts weisen jetzt darauf hin, handschriftliche Nummern zu übernehmen. Musterbelege neu messen.
- Ob die Modelle die Handschrift tatsächlich lesen, ist nicht gemessen.

## 1.7.1

- **Der Rechnungs-Auslöser erzwingt keine Rechnung mehr** (E63): Ist ein Dokument mit dem Tag aus `tags.ausloeser_nachbearbeitung` laut Modell keine Rechnung, entfernt nopaper den Auslöser, setzt den Prüf-Tag und macht mit dem nächsten Dokument weiter. Sonst wird nichts geschrieben. Von Hand erzwingen lässt es sich weiterhin mit `nopaper nachbearbeiten <id>`.
- **Abgelehnte Schreibvorgänge werden nicht mehr fünfmal wiederholt:** Lehnt paperless eine Anfrage ab (HTTP 4xx), oder hat ein Dokument keinen erkennbaren Text oder ein nicht unterstütztes Format, wird sofort der Auslöser entfernt und der Prüf-Tag gesetzt.

## 1.7.0

- **Termin aus einer Ladung** (Entscheidung E62): Im Volltext-Weg (Tag aus `tags.ausloeser_text`) erkennt nopaper eine gerichtliche Ladung und schreibt den Verhandlungstermin in benutzerdefinierte Felder: `felder.termin` (Datum), `felder.terminzeit` (Uhrzeit und Saal), `felder.aktenzeichen`. Jedes Feld ist optional.
  - Erkannt wird mit festen Regeln, ohne Sprachmodell: Es zählt nur ein Datum, das bei „Termin“, „Verhandlung“, „geladen“ oder „anberaumt“ steht und dem eine Uhrzeit folgt.
  - Ist der Termin nicht sicher lesbar oder stehen mehrere im Text (Umladung), bleiben die Felder leer und das Dokument bekommt den Prüf-Tag.
  - Ohne diese Feldschlüssel ändert sich am Volltext-Weg nichts.
- Bisher nur an Musterschreiben geprüft.

## 1.6.0

- **Neue Betriebsart „erst paperless“** (`"betriebsart": "paperless"`, Entscheidung E61): Der Scanner liefert direkt an paperless, nopaper reagiert nur noch auf die Auslöser-Tags und schreibt alles zurück. nopaper ist dabei zustandslos: keine Datenbank, kein Eingangs-, Ausgangs- oder Fehlerordner.
  - Der **Sidecar steht als Feld am Dokument** (`felder.sidecar`, Datentyp für langen Text).
  - Volltext, Tags, Felder, Titel und Sidecar gehen in einem Aufruf an paperless.
  - Duplikate werden über die Erkannt-Felder in paperless gesucht.
  - `nopaper status` zeigt, wie viele Dokumente einen Auslöser-Tag tragen.
  - nopaper liefert in dieser Betriebsart nicht an kumero ein; kumero holt den Sidecar aus paperless.
- **Der bisherige Scanner-Betrieb bleibt der Standard und ist unverändert.** Ohne den Schlüssel `betriebsart` ändert sich nichts.
- Noch nicht gegen ein echtes paperless erprobt.

## 1.5.3

- **Lange Rechnungen vollständig lesen:** Wie viel Text das Modell bekommt, richtet sich jetzt nach `modelle.kontext` (12.000 Zeichen je 8.192 Token, bisher fest 12.000 Zeichen). Mit großem Kontext geht eine mehrseitige Rechnung von Anfang bis Ende an das Modell. Mit dem Standardwert 8192 ändert sich für Dokumente bis 12.000 Zeichen nichts.
- **Passt der Text trotzdem nicht,** bekommt das Modell Anfang und Ende (Kopfdaten und Summen) statt nur den Anfang. Das Protokoll nennt dazu nur die Zeichenzahlen.
- **NuExtract im Bild-Modus:** `modelle.nuextract_seiten` wird auf die Zahl der Seitenbilder begrenzt, die in den Kontext passen (etwa 3.500 Token je Seite). Bisher schnitt Ollama stumm ab, wenn mehr Seiten eingestellt waren.
- Noch nicht an echten langen Rechnungen gemessen.
- **`BRUTTO_IST_ZWISCHENSUMME` schlägt nicht mehr an, wenn die Steuertabelle den Betrag bestätigt:** Steht der Endbetrag im erkannten Text nur neben „Zwischensumme“ (Kassenbon ohne Summenzeile, Zahlzeile falsch gelesen), ergibt aber die USt-Aufschlüsselung aus Beträgen, die im Text stehen, genau diesen Betrag, gilt er als Endbetrag. Der Übertrag einer mehrseitigen Rechnung wird weiterhin gemeldet.

- **Beträge mit dem gedruckten Vorzeichen:** Ein Auszahlungsbon (z. B. Pfandrückgabe größer als der Einkauf, „zu zahlen −0,77“) wird negativ erfasst. Beide Prompts verlangen das jetzt. Neue Prüfregel `BRUTTO_VORZEICHEN`: Widerspricht das Vorzeichen des gelesenen Bruttobetrags dem Druckbild, geht der Beleg in die Prüfung. Weil sich die Prompts geändert haben, Musterbelege neu messen.

- **Der Installer kann aktualisieren:** `install.sh` erkennt eine vorhandene Installation, lässt Konfiguration, Token und Belege unverändert und startet am Ende die Dienste neu, die schon eingeschaltet waren (auch die der Mandanten). Die Schritte der Anleitung lassen sich wiederholen; `git clone` ist dort durch einen Befehl ersetzt, der bei vorhandenem Verzeichnis nur den neuen Stand holt.
- **`nopaper-aktualisieren`:** ein Befehl für beides (neuen Stand holen, `install.sh` starten). `--pruefen` zeigt nur, ob es etwas Neues gibt. Von Hand geänderte Quellen werden nie überschrieben.

## 1.5.2

- **Ubuntu LTS neben Debian:** `install.sh` läuft auch auf Ubuntu 24.04 LTS und 26.04 LTS (amd64). Ältere Ubuntu-Fassungen werden abgelehnt, weil ihnen Python ≥ 3.11 fehlt. Fehlt die Paketquelle `universe` (dort liegen `ocrmypdf`, `unpaper` und `tesseract-ocr-deu`), schaltet der Installer sie ein. Noch nicht auf einem echten Ubuntu erprobt.
- **Netzsperre bei Ubuntu:** Dort geben nopaper und Ollama statt `localhost` (127.0.0.0/8) nur `127.0.0.1` und `::1` frei, weil unter `127.0.0.53` der DNS-Dienst `systemd-resolved` lauscht; der Dienst soll keinen DNS-Server erreichen (E47).
- Auf Debian ändert sich nichts.
- **README:** Installation Schritt für Schritt, vom frisch installierten Server (Anmelden als root, `git clone`) bis zum ersten Beleg und zur späteren Aktualisierung.

## 1.5.1

- **Selbst signierte Zertifikate für kumero:** neuer optionaler Schlüssel `kumero.ca_datei`. Das dort hinterlegte Zertifikat (selbst signiert oder interne CA) gilt als vertrauenswürdig. Die Prüfung bleibt immer an; ein nicht hinterlegtes Zertifikat wird abgelehnt.

## 1.5.0

- **Einlieferung der Sidecars an die Buchhaltung kumero** (optional, Abschnitt `kumero` in der Konfiguration): `PUT` je Sidecar mit `Idempotency-Key`, Warteschlange im Ausgangsordner, Wiederholung bei vorübergehenden Fehlern, Ordner `abgelehnt/` bei abgelehnten Sidecars, Anhalten bei ungültigem Token. Ohne den Abschnitt bleibt alles wie bisher.
- **Sidecar 1.1:** Kennung `SCAN-SIDECAR/1.1`, neue Zeilen `belegart` und `md5-original`.
- **Tag „noch zu buchen“:** optionaler Schlüssel `tags.zu_buchen`.
- `nopaper check` und `nopaper status` zeigen den Zustand der Einlieferung. `nopaper-aktivieren` gibt zusätzlich den kumero-Host frei.

## 1.4.10

- **Mandanten:** Auf derselben Maschine können mehrere Mandanten getrennt laufen. Jeder Mandant hat einen eigenen Dienst (`nopaper@<name>`), einen eigenen Systembenutzer, eigene Ordner unter `/srv/nopaper-<name>`, eine eigene Konfiguration unter `/etc/nopaper-mandanten/<name>` und ein eigenes paperless. Gemeinsam sind nur das Programm und die Sprachmodelle.
  - Anlegen: `nopaper-mandant anlegen <name>`, Übersicht: `nopaper-mandant liste`
  - Bedienen: `nopaper --mandant <name> check|once|status|test …`
  - Starten: `nopaper-aktivieren <name>`, Protokoll: `journalctl -u nopaper@<name> -f`
  - Die bisherige Installation bleibt unverändert als Standard-Instanz bestehen (`nopaper`, `/etc/nopaper`, `/srv/nopaper`).
- **Die Samba-Option des Installers entfällt.** Scanner werden künftig über WebDAV mit HTTPS angebunden (noch nicht enthalten).

## 1.4.9

- **Verwendungszweck für Überweisungen** (Feldschlüssel `verwendungszweck`, z. B. Feld `Erkannt: Verwendungszweck`): Der Wert muss wörtlich im Text stehen, sonst wird er verworfen (`VERWENDUNGSZWECK_VERWORFEN`). Fehlt er, übernimmt nopaper ein ausdrücklich bezeichnetes **Kassenzeichen** aus dem Text (`VERWENDUNGSZWECK_AUS_TEXT`), auch gesperrt gedruckt und auch, wenn die Nummer in der Folgezeile steht. Stehen verschiedene Nummern hinter „Kassenzeichen“, bleibt das Feld leer.

## 1.4.8

- **Dritter Weg – nur den Volltext erneuern:** Dokumente mit dem Tag aus `tags.ausloeser_text` (z. B. `totext`) werden neu erkannt und geglättet; nopaper schreibt **nur den Volltext** zurück und entfernt den Tag. Felder, Titel, übrige Tags, Besitzer bleiben, es entsteht kein Sidecar. Ersetzt wird nur, wenn der neue Text nicht schlechter ist (höchstens 10 % weniger lesbare Zeichen, Anteil lesbarer Zeichen höchstens 3 Punkte niedriger); sonst bleibt der alte Text (`TEXT_BEHALTEN`). Das Protokoll nennt nur die Kennzahlen, keinen Inhalt. Ohne den Schlüssel ist der Weg abgeschaltet.

## 1.4.7

- **Dateiname schon beim Upload:** Rechnungen werden unter dem Namen `Rechnungsdatum Lieferant Betrag.pdf` an paperless übergeben, nicht mehr unter dem Scanner-Namen (`img1.pdf`). Titel und Originaldateiname in paperless stimmen damit von Anfang an. Zeichen, die in Dateinamen nicht erlaubt sind, werden durch `_` ersetzt. Dokumente, die keine Rechnung sind, behalten den Namen aus dem Eingang.

## 1.4.6

- **Titel aus den Rechnungsdaten:** Rechnungen bekommen in paperless den Titel `Rechnungsdatum Lieferant Betrag`, z. B. `2026-11-05 Muster Tankstelle 60,58 EUR`, statt des Scan-Dateinamens. Einstellbar über `titel_rechnung` in der Konfiguration (Platzhalter `{datum}`, `{lieferant}`, `{betrag}`, `{nummer}`, `{belegart}`; leer = Titel nicht ändern). Gilt auch bei der Nachbearbeitung. Sonstige Dokumente behalten ihren Titel.

## 1.4.5

- **Dienst erreichte paperless nicht (`URLError`), obwohl `nopaper check` in Ordnung war:** Die Netzsperre des Dienstes lässt nur localhost und die IP von paperless zu, also auch keinen DNS-Server. `nopaper-aktivieren` trägt den Namen des paperless-Hosts deshalb fest in `/etc/hosts` ein (Zeilen mit `# nopaper-paperless`). Ändert sich die IP von paperless, `nopaper-aktivieren` erneut ausführen.

## 1.4.4

- **Belegnummer, bei der Bildmodell und Texterkennung sich in einer Ziffer widersprechen** (z. B. 919 gegen 319): Steht im Text vor der fast gleichen Nummer eine Belegbezeichnung, bleibt der Wert des Bildmodells erhalten (`RECHNUNGSNUMMER_ABWEICHEND`) und der Beleg geht über `RECHNUNGSNUMMER_NICHT_IM_TEXT` in die Prüfung. Bis 1.4.3 wurde die Nummer verworfen.

## 1.4.3

- **Prüfsumme des Originals:** paperless nennt je nach Version MD5 (32 Zeichen) oder SHA-256 (64 Zeichen). nopaper vergleicht mit dem passenden eigenen Wert. Bis 1.4.2 wurde immer MD5 erwartet (`INTEGRITAET_ORIGINAL` trotz unveränderter Datei).

## 1.4.2

- **paperless-ngx 3.x:** Das Aufgabenformat hat sich geändert (`status` klein geschrieben, `related_document_ids`, `result_data`). nopaper versteht beide Formate. Bis 1.4.1 wartete nopaper nach dem Upload stumm bis zu 30 Minuten.
- Die Warteschleife meldet alle 30 s den Stand und bricht nach 3 Minuten mit `PAPERLESS_AUFGABE_NICHT_GEFUNDEN` oder `PAPERLESS_AUFGABE_OHNE_DOKUMENT` ab, wenn paperless die Aufgabe nicht liefert oder „fertig“ ohne Dokument-ID meldet.
- `nopaper check` zeigt das Aufgabenformat und prüft auch das Extraktions- und das Glättungsmodell.

## 1.4.1

- **Rückgeld:** Ist „gegeben“ größer als der Endbetrag, gilt der Endbetrag als bezahlt, offen bleibt 0 (`RUECKGELD_ERKANNT`). Kassenbons und Bewirtungsbelege mit Zahlungsvermerk im Text gelten als bezahlt (`ZAHLUNG_AUS_TEXT`).
- **Belegnummer im Steuernummern-Format** wird nicht mehr verworfen, wenn eine Beleg-/Bon-/Rechnungsbezeichnung davorsteht.
- **Neue Feldschlüssel** in `felder`: `steuer_hoch` (Steuerbetrag zu Sätzen ab 10 %), `steuer_niedrig` (über 0, unter 10 %), `skonto` (Text `satz:frist:betrag`, Stufen mit `;` getrennt). Ohne Wert bleibt ein Feld leer.
- **Zahlungsziel:** Steht nur eine Frist in Tagen auf dem Beleg, wird das Datum aus dem Rechnungsdatum berechnet (`FAELLIGKEIT_BERECHNET`).
- **Skonto** steht im Sidecar in den Zahlungsbedingungen (`#SKONTO#TAGE=…#PROZENT=…#`).
- **`tags.pruefen` ist optional.** Ohne Prüf-Tag zeigt nur der Sidecar den Prüfstatus. Das Feld `pruefstatus` ist wie alle Felder optional.
- **nopaper legt in paperless nichts an.** Tags und Felder müssen vorhanden sein; `nopaper check` nennt fehlende (`TAG_FEHLT`, `FELD_FEHLT`). `nopaper paperless-skript` ist nur ein Angebot und nicht nötig.
- Soll-Vergleich im Testlauf um `due` (Zahlbetrag), `due_date` und `skonto` erweitert.
