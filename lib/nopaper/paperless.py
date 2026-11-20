# SPDX-License-Identifier: AGPL-3.0-or-later
"""Client fuer die REST-API eines bestehenden paperless-ngx (nur HTTPS, Zertifikat wird geprueft)."""
from __future__ import annotations

import hashlib
import json
import logging
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

log = logging.getLogger("nopaper")


class Voruebergehend(Exception):
    """Netz, 5xx, 429 – spaeter erneut versuchen."""


class PaperlessFehler(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(code)
        self.code = code
        self.detail = detail


class Paperless:
    def __init__(self, url: str, token: str, ca_datei: str | None = None, timeout: int = 60):
        self.url = url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.ctx = ssl.create_default_context(cafile=ca_datei) if self.url.startswith("https") else None
        self._cache: dict[str, dict[str, int]] = {}

    # ------------------------------------------------------------------ Grundlagen
    def _open(self, method: str, path: str, data: bytes | None = None,
              ctype: str = "application/json", timeout: int | None = None):
        req = urllib.request.Request(
            self.url + path, data=data, method=method,
            headers={"Authorization": f"Token {self.token}", "Accept": "application/json",
                     "Content-Type": ctype},
        )
        try:
            return urllib.request.urlopen(req, timeout=timeout or self.timeout, context=self.ctx)
        except urllib.error.HTTPError as e:
            if e.code >= 500 or e.code == 429:
                raise Voruebergehend(f"HTTP {e.code}") from e
            if e.code in (401, 403):
                raise PaperlessFehler("PAPERLESS_KEINE_BERECHTIGUNG", f"{method} {path.split('?')[0]}") from e
            if e.code == 404:
                raise PaperlessFehler("PAPERLESS_NICHT_GEFUNDEN", path.split("?")[0]) from e
            raise PaperlessFehler(f"PAPERLESS_HTTP_{e.code}", path.split("?")[0]) from e
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            raise Voruebergehend(type(e).__name__) from e

    def json(self, method: str, path: str, body=None):
        data = json.dumps(body).encode() if body is not None else None
        with self._open(method, path, data) as r:
            raw = r.read().decode() or "null"
        return json.loads(raw)

    def alle(self, endpoint: str, params: dict | None = None) -> list[dict]:
        out, page = [], 1
        params = dict(params or {})
        params.setdefault("page_size", "100")
        while True:
            params["page"] = str(page)
            res = self.json("GET", f"/api/{endpoint}/?" + urllib.parse.urlencode(params))
            out.extend(res.get("results") or [])
            if not res.get("next"):
                return out
            page += 1

    # ------------------------------------------------------------------ Namen -> IDs
    def _ids(self, endpoint: str, key: str = "name") -> dict[str, int]:
        if endpoint not in self._cache:
            self._cache[endpoint] = {str(o[key]).lower(): int(o["id"]) for o in self.alle(endpoint)}
        return self._cache[endpoint]

    def id_von(self, endpoint: str, name: str, key: str = "name") -> int | None:
        return self._ids(endpoint, key).get(name.lower())

    def tags(self, frisch: bool = False) -> dict[str, int]:
        """Alle Tags: Name (klein geschrieben) -> ID. `frisch` fragt paperless neu, damit ein eben
        angelegter Tag ohne Neustart gefunden wird."""
        if frisch:
            self._cache.pop("tags", None)
        return dict(self._ids("tags"))

    def tag_ids(self, namen: list[str]) -> list[int]:
        ids, fehlt = [], []
        for n in namen:
            i = self.id_von("tags", n)
            (ids.append(i) if i else fehlt.append(n))
        if fehlt:
            raise PaperlessFehler("TAG_FEHLT", ", ".join(fehlt))
        return ids

    def feld_ids(self, namen: dict[str, str]) -> dict[str, tuple[int, str]]:
        """schluessel -> (id, data_type)"""
        if "custom_fields" not in self._cache:
            self._cache["custom_fields_typ"] = {}
            objs = self.alle("custom_fields")
            self._cache["custom_fields"] = {o["name"].lower(): int(o["id"]) for o in objs}
            self._cache["custom_fields_typ"] = {o["name"].lower(): o.get("data_type", "") for o in objs}
        out, fehlt = {}, []
        for k, n in namen.items():
            i = self._cache["custom_fields"].get(n.lower())
            if i:
                out[k] = (i, self._cache["custom_fields_typ"].get(n.lower(), ""))
            else:
                fehlt.append(n)
        if fehlt:
            raise PaperlessFehler("FELD_FEHLT", ", ".join(fehlt))
        return out

    def cache_leeren(self) -> None:
        self._cache.clear()

    # ------------------------------------------------------------------ Dokumente
    def hochladen(self, datei: Path, tags: list[int], titel: str | None = None,
                  dateiname: str | None = None) -> str:
        """POST /api/documents/post_document/ – liefert die Task-UUID.
        dateiname: Name, unter dem paperless die Datei erhaelt (Originaldateiname dort)."""
        boundary = "nopaper" + uuid.uuid4().hex
        teile: list[bytes] = []

        def feld(name: str, wert: str):
            teile.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n"
                         f"{wert}\r\n".encode("utf-8"))

        for t in tags:
            feld("tags", str(t))
        if titel:
            feld("title", titel)
        name = re.sub(r'[\x00-\x1f"\\/:*?<>|]+', "_", dateiname or datei.name).strip(" .") or datei.name
        teile.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"document\"; "
                     f"filename=\"{name}\"\r\nContent-Type: application/pdf\r\n\r\n".encode("utf-8"))
        teile.append(datei.read_bytes())
        teile.append(f"\r\n--{boundary}--\r\n".encode())
        with self._open("POST", "/api/documents/post_document/", b"".join(teile),
                        f"multipart/form-data; boundary={boundary}", timeout=300) as r:
            res = json.loads(r.read().decode())
        return res if isinstance(res, str) else str(res.get("task_id", res))

    @staticmethod
    def _task_lesen(t: dict) -> tuple[str, int | None, str]:
        """Aufgabe aus paperless lesen – altes Format (bis 2.x: status 'SUCCESS', related_document, result)
        und neues Format (3.x: status 'success', related_document_ids, result_data)."""
        status = str(t.get("status") or "").upper()
        status = {"FAILED": "FAILURE", "ERROR": "FAILURE", "CANCELLED": "REVOKED", "CANCELED": "REVOKED",
                  "SUCCEEDED": "SUCCESS", "COMPLETED": "SUCCESS", "DONE": "SUCCESS"}.get(status, status)
        ergebnis = " ".join(json.dumps(t.get(k), ensure_ascii=False) for k in ("result", "result_data")
                            if t.get(k) not in (None, "", {}, []))
        doc = t.get("related_document")
        if doc in (None, ""):
            ids = t.get("related_document_ids") or []
            doc = ids[0] if isinstance(ids, list) and ids else None
        if doc in (None, "") and isinstance(t.get("result_data"), dict):
            rd = t["result_data"]
            doc = rd.get("document_id") or rd.get("document") or rd.get("id")
        if doc in (None, "") and status == "SUCCESS":
            m = re.search(r"document id (\d+)", ergebnis, re.I)
            doc = m.group(1) if m else None
        try:
            doc_id = int(doc) if doc not in (None, "") else None
        except (TypeError, ValueError):
            doc_id = None
        return status, doc_id, ergebnis

    def task_status(self, task_id: str) -> tuple[str, int | None, str]:
        """(status, dokument_id, ergebnis_text). 'UNBEKANNT', wenn paperless die Aufgabe nicht liefert."""
        res = self.json("GET", "/api/tasks/?" + urllib.parse.urlencode({"task_id": task_id}))
        items = res if isinstance(res, list) else (res.get("results") or [])
        # nur die Aufgabe mit genau dieser ID – falls paperless den Filter ignoriert, nicht die erstbeste nehmen
        items = [t for t in items if isinstance(t, dict) and str(t.get("task_id")) == task_id]
        if not items:
            return "UNBEKANNT", None, ""
        return self._task_lesen(items[0])

    def warte_auf_dokument(self, task_id: str, timeout_s: int = 1800, intervall: float = 5,
                           ohne_antwort_s: int = 180) -> int:
        """Wartet auf das Ende des Imports. Meldet alle 30 s den Stand. Bricht mit klarem Code ab, wenn
        paperless die Aufgabe nicht kennt oder 'fertig' ohne Dokument-ID meldet (unbekanntes Format)."""
        start = time.monotonic()
        letzte_meldung, unklar_seit = start, None
        while time.monotonic() - start < timeout_s:
            status, doc_id, ergebnis = self.task_status(task_id)
            if status == "SUCCESS" and doc_id:
                return doc_id
            if status in ("FAILURE", "REVOKED"):
                if "duplicate" in ergebnis.lower() or "duplikat" in ergebnis.lower():
                    raise PaperlessFehler("DUPLIKAT_PAPERLESS")
                raise PaperlessFehler("PAPERLESS_IMPORT_FEHLGESCHLAGEN")
            jetzt = time.monotonic()
            if status in ("UNBEKANNT", "SUCCESS"):
                unklar_seit = unklar_seit or jetzt
                if jetzt - unklar_seit >= ohne_antwort_s:
                    raise PaperlessFehler("PAPERLESS_AUFGABE_OHNE_DOKUMENT" if status == "SUCCESS"
                                          else "PAPERLESS_AUFGABE_NICHT_GEFUNDEN", task_id)
            else:
                unklar_seit = None
            if jetzt - letzte_meldung >= 30:
                letzte_meldung = jetzt
                log.info("warte auf paperless: Aufgabe %s Status %s (%d s)", task_id[:8], status or "-",
                         int(jetzt - start))
            time.sleep(intervall)
        raise Voruebergehend("Import in paperless dauert zu lange")

    def dokument(self, doc_id: int) -> dict:
        return self.json("GET", f"/api/documents/{doc_id}/")

    def metadaten(self, doc_id: int) -> dict:
        return self.json("GET", f"/api/documents/{doc_id}/metadata/")

    def hashes(self, doc_id: int, original: bool, ziel: Path | None = None) -> tuple[str, str]:
        """Datei streamen und SHA-256/MD5 bilden; optional in `ziel` schreiben (Arbeitsverzeichnis)."""
        sha, md5 = hashlib.sha256(), hashlib.md5()
        q = "?original=true" if original else ""
        f = open(ziel, "wb") if ziel else None
        try:
            with self._open("GET", f"/api/documents/{doc_id}/download/{q}", timeout=300) as r:
                for chunk in iter(lambda: r.read(1024 * 1024), b""):
                    sha.update(chunk)
                    md5.update(chunk)
                    if f:
                        f.write(chunk)
        finally:
            if f:
                f.close()
        return sha.hexdigest(), md5.hexdigest()

    def aendern(self, doc_id: int, daten: dict) -> None:
        self.json("PATCH", f"/api/documents/{doc_id}/", daten)

    def mit_feldwerten(self, bedingungen: list[tuple[int, str]]) -> list[int]:
        """IDs der Dokumente, bei denen ALLE genannten benutzerdefinierten Felder genau den Wert haben
        (Gross-/Kleinschreibung egal). Nutzt custom_field_query (paperless-ngx ab 2.13) nur in der einfachsten
        Form – ein Feld, Operator "icontains" – und vergleicht selbst genau: paperless kennt fuer Text kein
        "gleich ohne Gross-/Kleinschreibung", und die Schreibweise verknuepfter Bedingungen ist je Fassung
        verschieden."""
        fid0, wert0 = bedingungen[0]
        res = self.alle("documents", {"custom_field_query": json.dumps([fid0, "icontains", wert0], ensure_ascii=False),
                                      "fields": "id,custom_fields", "truncate_content": "true"})

        def gleich(a, b) -> bool:
            return str(a if a is not None else "").strip().lower() == str(b).strip().lower()
        out = []
        for d in res:
            werte = {f.get("field"): f.get("value") for f in d.get("custom_fields") or []}
            if all(gleich(werte.get(fid), wert) for fid, wert in bedingungen):
                out.append(int(d["id"]))
        return out

    def mit_tag(self, tag_id: int) -> list[int]:
        res = self.alle("documents", {"tags__id__all": str(tag_id), "fields": "id",
                                      "truncate_content": "true", "ordering": "added"})
        return [int(d["id"]) for d in res]
