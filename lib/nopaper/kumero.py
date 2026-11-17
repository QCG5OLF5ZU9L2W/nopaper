# SPDX-License-Identifier: AGPL-3.0-or-later
"""Einlieferung der Sidecars an die Buchhaltung kumero (E56).

Genau ein ausgehender Aufruf: PUT {url}/m/{mandant}/api/v1/belegeingang/sidecars/{paperless_id}
mit dem Sidecar Byte fuer Byte, dazu GET …/status fuer `nopaper check`.

Der Ausgangsordner ist die Warteschlange: Eine Datei bleibt liegen, bis kumero sie angenommen hat.
Die Einlieferung darf die Belegverarbeitung nie aufhalten – jeder Fehler endet hier.
Logs und Meldungen enthalten nur paperless-ID, HTTP-Status, Code und Zaehler, nie Token oder Belegangaben.
"""
from __future__ import annotations

import datetime as dt
import email.utils
import hashlib
import json
import logging
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path

from .config import Config
from .invoice import atomic_write
from .state import State

log = logging.getLogger("nopaper")

ZEITLIMIT_S = 30
MAX_BYTES = 1024 * 1024
ABSTAENDE_S = (60, 300, 900, 3600)      # 1 min, 5 min, 15 min, dann stuendlich
WARNUNG_ALTER_S = 24 * 3600


class Kumero:
    def __init__(self, cfg: Config, state: State, timeout: int = ZEITLIMIT_S):
        self.cfg = cfg
        self.state = state
        self.timeout = timeout
        self.basis = f"{cfg.kumero_url}/m/{cfg.kumero_mandant}/api/v1/belegeingang"
        self.ordner = cfg.ausgang_rechnungsdaten
        self.abgelehnt = self.ordner / "abgelehnt"
        self.ctx = (ssl.create_default_context(cafile=cfg.kumero_ca_datei or None)
                    if cfg.kumero_url.startswith("https") else None)
        self._angehalten: tuple[str, str] | None = None   # (Code, Kennung der Token-Datei beim Anhalten)
        self._pause_bis = 0.0                              # 429: fruehestens dann wieder senden
        self._letzte_warnung = 0.0

    # ------------------------------------------------------------------ Token
    def _token(self) -> str | None:
        try:
            t = Path(self.cfg.kumero_token_datei).read_text(encoding="utf-8").strip()
        except OSError:
            return None
        return t or None

    def _token_kennung(self) -> str:
        """Aendert sich, sobald die Token-Datei ersetzt wurde (ohne das Token selbst aufzubewahren)."""
        t = self._token()
        return hashlib.sha256(t.encode()).hexdigest() if t else ""

    # ------------------------------------------------------------------ HTTP
    def _anfrage(self, methode: str, url: str, token: str, body: bytes | None = None,
                 kopf: dict | None = None) -> tuple[int, dict, dict]:
        """(Status, Antwort als dict, Kopfzeilen). Status 0 = keine Verbindung/Zeitueberschreitung/TLS-Fehler."""
        req = urllib.request.Request(url, data=body, method=methode, headers={
            "Authorization": f"Bearer {token}", "Accept": "application/json", **(kopf or {})})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=self.ctx) as r:
                status, roh, hdr = r.status, r.read(65536), dict(r.headers)
        except urllib.error.HTTPError as e:
            status, roh, hdr = e.code, e.read(65536), dict(e.headers or {})
        except (urllib.error.URLError, TimeoutError, ConnectionError, ssl.SSLError, OSError):
            return 0, {}, {}
        try:
            daten = json.loads(roh.decode("utf-8")) if roh else {}
        except (ValueError, UnicodeDecodeError):
            daten = {}
        return status, daten if isinstance(daten, dict) else {}, hdr

    # ------------------------------------------------------------------ Warteschlange
    def wartende(self) -> list[Path]:
        if not self.ordner.is_dir():
            return []
        dateien = [p for p in self.ordner.glob("*.xml") if p.is_file() and not p.name.startswith(".")]
        return sorted(dateien, key=lambda p: (p.stat().st_mtime, p.name))

    @staticmethod
    def _wartezeit(hdr: dict) -> float:
        wert = next((v for k, v in hdr.items() if k.lower() == "retry-after"), "")
        try:
            return max(0.0, float(wert))
        except (TypeError, ValueError):
            pass
        try:
            ziel = email.utils.parsedate_to_datetime(wert)
            return max(0.0, (ziel - dt.datetime.now(ziel.tzinfo)).total_seconds())
        except (TypeError, ValueError):
            return 60.0

    def _ablehnen(self, datei: Path, code: str) -> None:
        self.abgelehnt.mkdir(mode=0o750, exist_ok=True)
        ziel = self.abgelehnt / datei.name
        datei.replace(ziel)
        info = {"code": code, "zeit": dt.datetime.now().astimezone().isoformat(timespec="seconds")}
        atomic_write(ziel.with_name(ziel.name + ".abgelehnt.json"),
                     json.dumps(info, ensure_ascii=False, indent=1).encode("utf-8"))

    def zustellen(self) -> int:
        """Sendet die wartenden Sidecars als Stapel. Bricht den Stapel beim ersten voruebergehenden
        Fehler ab (kumero ist dann vermutlich nicht erreichbar). Gibt die Zahl der zugestellten zurueck."""
        jetzt = time.time()
        if self._angehalten:
            code, kennung = self._angehalten
            if self._token_kennung() == kennung:
                return 0
            log.info("kumero: Token-Datei geaendert, Einlieferung wird fortgesetzt (war %s)", code)
            self._angehalten = None
        if jetzt < self._pause_bis:
            return 0
        dateien = self.wartende()
        if not dateien:
            return 0
        aeltester = jetzt - dateien[0].stat().st_mtime
        if aeltester > WARNUNG_ALTER_S and jetzt - self._letzte_warnung > 3600:
            self._letzte_warnung = jetzt
            log.warning("kumero: %d Sidecar(s) warten, der aelteste seit %.0f h", len(dateien), aeltester / 3600)
        token = self._token()
        if not token:
            self._angehalten = ("KUMERO_TOKEN", "")
            log.error("kumero: Einlieferung angehalten (KUMERO_TOKEN: Token-Datei fehlt oder ist leer)")
            return 0

        n = 0
        for datei in dateien:
            try:
                doc_id = int(datei.stem)
            except ValueError:
                continue                      # keine Sidecar-Datei von nopaper
            try:
                body = datei.read_bytes()
            except OSError:
                continue
            sha = hashlib.sha256(body).hexdigest()
            row = self.state.zustellung(datei.name)
            if row and row["sha256"] == sha and row["nicht_vor"] > jetzt:
                continue
            versuche = row["versuche"] if row and row["sha256"] == sha else 0
            if len(body) > MAX_BYTES:
                self._ablehnen(datei, "sidecar.zu_gross")
                self.state.zustellung_loeschen(datei.name)
                log.error("kumero: doc=%s abgelehnt (sidecar.zu_gross)", doc_id)
                continue

            status, daten, hdr = self._anfrage("PUT", f"{self.basis}/sidecars/{doc_id}", token, body, {
                "Content-Type": "application/xml", "Idempotency-Key": sha})

            if status in (200, 201) and str(daten.get("sha256", "")).lower() == sha:
                datei.unlink(missing_ok=True)
                self.state.zustellung_loeschen(datei.name)
                n += 1
                log.info("kumero: doc=%s zugestellt (%s, %s)", doc_id, status, daten.get("ergebnis") or "-")
                continue
            if status in (401, 403, 404):
                code = "KUMERO_ZIEL" if status == 404 else "KUMERO_TOKEN"
                self._angehalten = (code, self._token_kennung())
                log.error("kumero: Einlieferung angehalten (%s, HTTP %s). Sidecars bleiben liegen; "
                          "nach dem Ersetzen der Token-Datei geht es weiter.", code, status)
                break
            if status in (413, 422):
                code = str(daten.get("code") or ("sidecar.zu_gross" if status == 413 else "sidecar.abgelehnt"))[:80]
                self._ablehnen(datei, code)
                self.state.zustellung_loeschen(datei.name)
                log.error("kumero: doc=%s abgelehnt (HTTP %s, %s)", doc_id, status, code)
                continue
            if status == 429:
                warten = self._wartezeit(hdr)
                self._pause_bis = time.time() + warten
                log.warning("kumero: zu viele Anfragen, Pause %.0f s", warten)
                break
            # 5xx, Zeitueberschreitung, keine Verbindung, unerwartete Antwort, abweichender Hash
            versuche += 1
            abstand = ABSTAENDE_S[min(versuche, len(ABSTAENDE_S)) - 1]
            self.state.zustellung_setzen(datei.name, sha, versuche, time.time() + abstand)
            grund = "Hash der Antwort weicht ab" if status in (200, 201) else (
                f"HTTP {status}" if status else "keine Verbindung")
            log.warning("kumero: doc=%s nicht zugestellt (%s), Versuch %d, naechster in %d s",
                        doc_id, grund, versuche, abstand)
            break
        return n

    # ------------------------------------------------------------------ Uebersicht und Pruefung
    def uebersicht(self) -> dict:
        dateien = self.wartende()
        abgelehnt = len(list(self.abgelehnt.glob("*.xml"))) if self.abgelehnt.is_dir() else 0
        return {
            "wartend": len(dateien),
            "aeltester_s": int(time.time() - dateien[0].stat().st_mtime) if dateien else 0,
            "abgelehnt": abgelehnt,
            "angehalten": self._angehalten[0] if self._angehalten else None,
        }

    def pruefen(self) -> str | None:
        """None = bereit; sonst einer der Codes KUMERO_URL, KUMERO_TOKEN, KUMERO_ZIEL, KUMERO_NICHT_ERREICHBAR."""
        if not self.cfg.kumero_url.startswith("https://") and "//127.0.0.1" not in self.cfg.kumero_url \
                and "//localhost" not in self.cfg.kumero_url:
            return "KUMERO_URL"
        token = self._token()
        if not token:
            return "KUMERO_TOKEN"
        status, daten, _ = self._anfrage("GET", f"{self.basis}/status", token)
        if status == 0 or status >= 500 or status == 429:
            return "KUMERO_NICHT_ERREICHBAR"
        if status in (401, 403):
            return "KUMERO_TOKEN"
        if status != 200 or daten.get("bereit") is not True:
            return "KUMERO_ZIEL"
        return None
