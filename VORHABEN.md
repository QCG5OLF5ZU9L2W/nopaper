# Vorhaben – lokales Sprachmodell in der Praxis

Stand: 2026-11-28. Diese Datei sammelt, was ein lokales Sprachmodell in einer Arztpraxis übernehmen
kann, was davon gebaut ist und was als Nächstes kommen könnte. **Nichts in Abschnitt 2 bis 4 ist
entschieden oder gebaut**, solange es nicht in die `AGENTS.md` des jeweiligen Vorhabens als
Entscheidung übernommen wurde. Es gelten die Rahmenbedingungen der Wurzel-`AGENTS.md`
(Abschnitt 2 und 9): alles lokal, nur erfundene Daten in Repository und Tests, Protokolle nur mit
Metadaten.

---

## 1. Gebaut oder in Arbeit

| Bereich | Was | Wo |
|---|---|---|
| Papier | Scans lesen, Rechnungen auslesen und prüfen, Volltext für paperless | nopaper (`AGENTS.md`) |
| Post | Mails lesen, übersetzen, Anliegen und Merkmale erkennen, Antworten entwerfen, weiterleiten | `johm/` (J1–J32) |
| Termine | Wünsche verstehen, Plätze vorschlagen, reservieren, buchen, Absagen und Verlegungen | `johm/` |
| Chat | Praxiskalender lesen, eintragen und entfernen aus Open WebUI, mit Bestätigungsdialog | `johm/` (J23, J33, J34) |
| Kalenderpflege | Serien in Einzeltermine auflösen, leere Jahre aus einer Vorlagewoche anlegen | `johm/werkzeuge/serien-aufloesen.py` |
| Telefon | Anrufe annehmen, aufschreiben, reservieren | `johv/` (entworfen, nicht gebaut) |
| SMS | Terminerinnerung über den eigenen LTE-Router | `johm/AGENTS.md` Abschnitt 8 (nicht gebaut) |

## 2. Grundsätze für alles Weitere

1. **Das Modell bereitet vor, ein Mensch entscheidet.** Selbstständig handelt ein Werkzeug nur in eng umrissenen Fällen mit festen, im Code geprüften Regeln (Beispiel: Absagebestätigung, J31).
2. **Keine medizinischen Entscheidungen:** keine Diagnose, keine Therapieempfehlung, keine Triage. Software, die das tut, ist ein Medizinprodukt. Erlaubt sind Merkmale mit wörtlichem Beleg, die ein Mensch prüft (Muster: Schwangerschaft, Dringlichkeit).
3. **Nichts geht ohne Menschen nach außen**, außer in den Fällen von Punkt 1.
4. **Schreiben nur mit Bestätigung.** Was in Kalender, Website oder Praxissoftware schreibt, zeigt vorher genau das, was geschrieben wird, und wartet auf OK (Muster: J33).
5. **Die Quelle bleibt die Wahrheit.** Kalender, paperless und Praxissoftware führen die Daten; die Werkzeuge halten keinen zweiten Bestand.

## 3. Mögliche nächste Vorhaben

| # | Vorhaben | Nutzen | Voraussetzung |
|---|---|---|---|
| V-1 | **Rückrufliste aus allen Kanälen** – Anliegen aus Mail, Telefon (johv) und Fax an einem Ort, mit Dringlichkeit und Status | entlastet die Anmeldung, nichts bleibt liegen | johm; johv für das Telefon |
| V-2 | **Eingangsbefunde vorsortieren** – Arztbriefe und Befunde zusammenfassen, Patientin, Absender, auffällig markierte Stellen mit Beleg | spart Lesezeit beim Eingang | nopaper und paperless; Zuordnung zur Patientin (nopaper Abschnitt 12, 16 k) |
| V-3 | **Rezept- und Überweisungswünsche** aus Mail, Fax und Telefon als Liste: wer, was, wofür | Vorbereitung, ausgestellt wird in der Praxissoftware | johm; besser mit Schnittstelle zur Praxissoftware |
| V-4 | **Standardschreiben** – Bescheinigungen, Antworten an Krankenkassen, Wiedereinbestellungen als Entwurf | weniger Tipparbeit | Vorlagen der Praxis |
| V-5 | **Recall** – fällige Vorsorgen erkennen, Einladungen vorbereiten | weniger verpasste Termine | Daten aus der Praxissoftware |
| V-6 | **Patienteninformation in Fremdsprachen** – Merkblätter und Aufklärungstexte übersetzen, von einem Menschen gegengelesen | Verständigung | – |
| V-7 | **Interne Fragen im Chat** – Antworten aus eigenen Dokumenten (Anleitungen, Abläufe, Regeln) | Einarbeitung, Nachschlagen | Wissensspeicher in Open WebUI, nur Praxisdokumente |
| V-8 | **Website** – Sprechzeiten, Urlaubshinweis, Aktuelles (Abschnitt 4) | Pflege ohne WordPress-Kenntnisse | WordPress-Zugang |
| V-9 | **Abrechnung vorbereiten** – Plausibilität von Leistungsketten prüfen, Hinweise geben, nicht selbst abrechnen | weniger Rückfragen | Daten aus der Praxissoftware |

**Vorgeschlagene Reihenfolge:** V-1, dann V-2, dann V-3. Für V-3, V-5 und V-9 entscheidet die Schnittstelle der Praxissoftware, wie weit sie gehen können.

## 4. Website (WordPress) – Entwurf

Anlass: Frage vom 2026-11-27, ob das Modell mit Zugang zu WordPress z. B. die Sprechzeiten pflegen kann.

**Vorschlag: eigenes, eng gefasstes Werkzeug für Open WebUI** nach dem Muster des Kalender-Werkzeugs.
- Zugang über die REST-API von WordPress mit einem **eigenen WordPress-Benutzer** und **App-Passwort**; das Passwort steht nur in den Einstellungen des Werkzeugs (nur für Admins sichtbar).
- Nur festgelegte Aufgaben, z. B. „Sprechzeiten ändern“, „Hinweis Praxis geschlossen setzen/entfernen“, „Aktuelles als Entwurf anlegen“. Keine Plugins, keine Benutzer, keine anderen Seiten.
- Vor dem Veröffentlichen ein **Bestätigungsfenster mit genau dem Text, der online geht**, oder nur Entwurf.
- Das Werkzeug schreibt nur Texte für die Website, **nie etwas aus Patientenmails oder Kalendereinträgen**.
- Ersetzt wird nur ein eigener, markierter Block (z. B. die Sprechzeiten), nie der Rest einer Seite.

**Verworfen als erster Weg:** der offizielle WordPress MCP Adapter. Er erschließt viele Fähigkeiten auf einmal; für eine Praxiswebsite ist die Freigabe zu breit und schwer zu begrenzen.

**Offen:** welche Inhalte; direkt veröffentlichen oder nur Entwurf; wo WordPress läuft (Hoster oder Praxisnetz) und ob `llm` es erreicht; ob die Sprechzeiten ein eigener Block sind.

## 5. Offene Frage für alles mit Patientendaten

**Welche Praxissoftware und welche Schnittstelle?** Patientenstamm, Diagnosen und Abrechnung liegen dort. Ohne Schnittstelle bleiben V-3, V-5 und V-9 Listen zum Abtippen.
