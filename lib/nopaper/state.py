# SPDX-License-Identifier: AGPL-3.0-or-later
"""Zustand in SQLite. Enthaelt nur IDs, Hashes, Status, Zaehler und Fehlercodes – keine Belegdaten."""
from __future__ import annotations

import hashlib
import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS eingang (
    sha256      TEXT PRIMARY KEY,
    ordner      TEXT NOT NULL,          -- rechnungen | dokumente
    dateiname   TEXT NOT NULL,          -- Name im Eingang (fuer Titel/Fehlerordner)
    status      TEXT NOT NULL,          -- wartend | hochgeladen | fertig | fehler
    task_id     TEXT,
    doc_id      INTEGER,
    versuche    INTEGER NOT NULL DEFAULT 0,
    nicht_vor   REAL NOT NULL DEFAULT 0,
    fehler      TEXT,
    angelegt    REAL NOT NULL,
    geaendert   REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS nachbearbeitung (
    doc_id      INTEGER PRIMARY KEY,
    versuche    INTEGER NOT NULL DEFAULT 0,
    nicht_vor   REAL NOT NULL DEFAULT 0,
    fehler      TEXT,
    geaendert   REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS zustellung (
    datei       TEXT PRIMARY KEY,       -- Dateiname des Sidecars in der Warteschlange
    sha256      TEXT NOT NULL,          -- Inhalt, auf den sich die Versuche beziehen
    versuche    INTEGER NOT NULL DEFAULT 0,
    nicht_vor   REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS duplikate (
    schluessel  TEXT PRIMARY KEY,       -- SHA-256(Absender|Rechnungsnummer)
    doc_id      INTEGER NOT NULL
);
"""


class State:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), isolation_level=None, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)

    # ------------------------------------------------------------------ Eingang
    def eingang(self, sha: str):
        return self.db.execute("SELECT * FROM eingang WHERE sha256=?", (sha,)).fetchone()

    def eingang_neu(self, sha: str, ordner: str, dateiname: str) -> None:
        now = time.time()
        self.db.execute("INSERT INTO eingang(sha256, ordner, dateiname, status, angelegt, geaendert) "
                        "VALUES (?,?,?,?,?,?)", (sha, ordner, dateiname, "wartend", now, now))

    def eingang_setzen(self, sha: str, **werte) -> None:
        werte["geaendert"] = time.time()
        cols = ", ".join(f"{k}=?" for k in werte)
        self.db.execute(f"UPDATE eingang SET {cols} WHERE sha256=?", (*werte.values(), sha))

    def eingang_offen(self) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM eingang WHERE status IN ('wartend','hochgeladen') AND nicht_vor<=? "
            "ORDER BY angelegt", (time.time(),)).fetchall()

    # ------------------------------------------------------------------ Nachbearbeitung
    def nach(self, doc_id: int):
        return self.db.execute("SELECT * FROM nachbearbeitung WHERE doc_id=?", (doc_id,)).fetchone()

    def nach_setzen(self, doc_id: int, versuche: int, nicht_vor: float, fehler: str | None) -> None:
        self.db.execute(
            "INSERT INTO nachbearbeitung(doc_id, versuche, nicht_vor, fehler, geaendert) VALUES (?,?,?,?,?) "
            "ON CONFLICT(doc_id) DO UPDATE SET versuche=excluded.versuche, nicht_vor=excluded.nicht_vor, "
            "fehler=excluded.fehler, geaendert=excluded.geaendert",
            (doc_id, versuche, nicht_vor, fehler, time.time()))

    def nach_loeschen(self, doc_id: int) -> None:
        self.db.execute("DELETE FROM nachbearbeitung WHERE doc_id=?", (doc_id,))

    # ------------------------------------------------------------------ Zustellung an kumero
    def zustellung(self, datei: str):
        return self.db.execute("SELECT * FROM zustellung WHERE datei=?", (datei,)).fetchone()

    def zustellung_setzen(self, datei: str, sha: str, versuche: int, nicht_vor: float) -> None:
        self.db.execute(
            "INSERT INTO zustellung(datei, sha256, versuche, nicht_vor) VALUES (?,?,?,?) "
            "ON CONFLICT(datei) DO UPDATE SET sha256=excluded.sha256, versuche=excluded.versuche, "
            "nicht_vor=excluded.nicht_vor", (datei, sha, versuche, nicht_vor))

    def zustellung_loeschen(self, datei: str) -> None:
        self.db.execute("DELETE FROM zustellung WHERE datei=?", (datei,))

    # ------------------------------------------------------------------ Duplikate
    def duplikat(self, absender: str, rechnungsnummer: str, doc_id: int) -> int | None:
        key = hashlib.sha256(f"{absender.upper()}|{rechnungsnummer.upper()}".encode()).hexdigest()
        row = self.db.execute("SELECT doc_id FROM duplikate WHERE schluessel=?", (key,)).fetchone()
        if row is None:
            self.db.execute("INSERT INTO duplikate(schluessel, doc_id) VALUES (?,?)", (key, doc_id))
            return None
        return None if row["doc_id"] == doc_id else int(row["doc_id"])
