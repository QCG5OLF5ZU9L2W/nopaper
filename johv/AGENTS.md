# AGENTS.md – johv (John am Telefon)

Diese Datei hält fest, **was** John am Telefon tun soll und **welche Entscheidungen gefallen sind**.
Abweichungen nur nach Rücksprache mit dem Projektverantwortlichen. Für das ganze Repository gilt die
`AGENTS.md` im Wurzelverzeichnis, vor allem Abschnitt 2 (Rahmenbedingungen) und 9 (Sicherheit und
Datenschutz); für Kalender, Terminwünsche und Journal gelten die Entscheidungen in `johm/AGENTS.md`.

Stand: 2026-11-27. **Nichts gebaut.** Entwurf auf Grundlage der Angaben des Verantwortlichen (V1–V4).

---

## 1. Ziel

Gegenstück zum Mailarbeitsplatz (`johm`): John nimmt Anrufe an der Telefonanlage der Praxis an,
versteht das Anliegen, schreibt es auf und zeigt es der MFA in einer eigenen Oberfläche, **johv**.
Weitergeleitet wird nur, wenn John nicht weiterkommt.

## 2. Entscheidungen

| # | Entscheidung |
|---|---|
| V1 | **Telefonanlage ist FreePBX** (Asterisk). John wird dort als Ziel eines eingehenden Anrufs eingebunden, nicht als Telefon mit eigener SIP-Anmeldung. |
| V2 | **John nimmt jeden Anruf an** und leitet nur weiter, wenn er nicht weiterkommt (V6). |
| V3 | **John schreibt das Anliegen auf.** Abschrift und Zusammenfassung erscheinen in **johv**, einer eigenen Oberfläche für die MFA nach dem Vorbild von johm. |
| V4 | **John reserviert am Telefon** (entschieden 2026-11-27). Er bietet Plätze aus dem Kalender an und reserviert den gewählten wie in johm sichtbar im Kalender (J11, mit Vermerk „Telefon“). Gebucht ist der Termin erst nach einer Bestätigung: durch die MFA in johv oder schriftlich durch die Anruferin (SMS oder Mail, sobald ein Rückkanal besteht). Läuft die Frist ab, wird die Reservierung freigegeben wie in johm. |

## 3. Entwurf (noch nicht entschieden)

**Anbindung.** In FreePBX führt die eingehende Route auf ein eigenes Ziel („Custom Destination“) mit
einem kleinen Wählplan: annehmen, Audio über **AudioSocket** (TCP, Asterisk-Bordmittel) an den
johv-Dienst geben, danach beim Dienst nachfragen, wie es weitergeht – auflegen oder an die
Rufgruppe der MFA. **Ist der Dienst nicht erreichbar oder bricht die Verbindung ab, klingelt das
Telefon bei der MFA wie bisher.** Ein Ausfall von John darf nie einen Anruf verlieren.

**Verarbeitung, alles lokal.** Spracherkennung (Whisper-Familie) auf Telefonqualität 8 kHz,
dasselbe Sprachmodell wie John, Sprachausgabe mit einer lokalen deutschen Stimme. Kein
Sprachdienst im Internet.

**Gesprächsablauf.**
1. Ansage: Praxis, und dass ein KI-System spricht (Art. 50 KI-Verordnung, seit 2. August 2026 anwendbar) und das Anliegen für das Praxisteam aufgeschrieben wird.
2. Anliegen erfragen; bei Terminwunsch dieselben Felder wie im Mail (Anlass, Zeitwunsch, Ärztin, Verlegung).
3. Name und Rückrufnummer (die angezeigte Nummer wird vorgelesen und bestätigt).
4. Bei Terminwunsch (V4): zwei bis drei Plätze vorlesen, den gewählten wiederholen lassen und reservieren; sagen, dass die Praxis den Termin noch bestätigt.
5. Zusammenfassung vorlesen, bestätigen lassen, verabschieden.

**Grenzen der Reservierung (Vorschlag):** keine Reservierung bei angegebener Schwangerschaft (J4) –
aufnehmen und an die MFA. Bei Anzeichen von Dringlichkeit (J17) während der Sprechzeit sofort an
die MFA statt einer Reservierung. Höchstens ein reservierter Platz je Anruf und Anliegen.
Bestätigungsfrist wie in johm (`hold_hours`). Verlegungen: neuer Platz wird reserviert, der
bisherige erst nach Bestätigung freigegeben, weil die Anruferin am Telefon nicht sicher erkannt ist.

**Weiterleitung an die MFA (V2)**, sofort bei: Wunsch nach einem Menschen; Anzeichen eines
Notfalls (vorher Hinweis auf 112); zweimal nicht verstanden; Anrufe von Kliniken, Ärzten, Laboren
und Apotheken; jedem technischen Fehler. Außerhalb der Sprechzeit gibt es niemanden zum
Weiterleiten: dann Hinweis auf 112 und den ärztlichen Bereitschaftsdienst 116117.

**Was John nicht tut:** keine medizinische Auskunft, keine Befunde, keine Auskunft über Termine
anderer, keine Absage oder Verlegung ohne Rückbestätigung (die Anrufernummer beweist nicht,
wer spricht).

**johv.** Liste der Anrufe mit Zeit, Nummer, Name, Zusammenfassung, Abschrift, erkannten Merkmalen
(Schwangerschaft, Dringlichkeit wie J4, J17) und – bei Terminwunsch – vorgesuchten Plätzen aus
dem Kalender wie in johm. Status offen/erledigt. Keine Tonaufnahme wird gespeichert.

## 4. Offene Punkte

1. **Bestätigung der Reservierung (V4):** Bis ein SMS-Versand besteht, bestätigt die MFA in johv. Danach: SMS mit Termin und Bitte um Antwort „JA“? Dafür müsste John SMS-Antworten lesen (johm Abschnitt 8, Punkt 1).
2. **Grafikkarte und Speicher auf `llm`.** Ein Gespräch muss in rund einer Sekunde antworten und konkurriert mit Mail und Chat um dieselbe Karte; Spracherkennung braucht zusätzlichen Speicher. Ggf. eigener Rechner für die Sprache.
3. **Asterisk-Version** der FreePBX und ob AudioSocket geladen ist (`asterisk -rx "core show version"`, `asterisk -rx "module show like audiosocket"`). Netzweg zwischen FreePBX und `llm`.
4. **Wo die Abschriften liegen und wie lange.** Sie sind Gesundheitsdaten. Vorschlag: als Nachricht in einen eigenen Ordner des Praxispostfachs (Sicherung und Rechte wie die Post), oder eigener Speicher auf `llm` mit Löschfrist.
5. **Ziel der Weiterleitung:** Rufgruppe oder Nebenstelle der MFA; Sprechzeiten.
6. **Ansagetext** und Hinweis zur Abschrift: rechtliche Prüfung durch den Verantwortlichen.
7. **Rückkanal:** Antwort an die Anruferin per Rückruf, SMS (Router, johm Abschnitt 8) oder Mail, wenn bekannt.
