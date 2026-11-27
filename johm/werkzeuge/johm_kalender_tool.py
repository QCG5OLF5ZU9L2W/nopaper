"""
title: Praxiskalender (Johm)
author: Praxis
version: 1.0
description: Liest den Nextcloud-Praxiskalender über Johm. Nur lesend, keine Reservierung und keine Buchung.
"""

import asyncio
import json
import urllib.error
import urllib.parse
import urllib.request

from pydantic import BaseModel, Field


class Tools:
    class Valves(BaseModel):
        johm_url: str = Field(
            default="http://127.0.0.1:3301",
            description="Adresse des Johm-Mailarbeitsplatzes auf diesem Rechner.",
        )
        johm_key: str = Field(
            default="",
            description="Inhalt der Datei /etc/nopaper-mail/chat-tool.key.",
        )

    def __init__(self):
        self.valves = self.Valves()

    async def praxiskalender(
        self,
        von: str,
        bis: str,
        art: str = "",
        nur_frei: bool = True,
        __user__: dict = None,
    ) -> str:
        """
        Liest den Terminkalender der Praxis aus Nextcloud. Für jede Frage nach freien oder belegten Praxisterminen dieses Werkzeug verwenden und keinen anderen Kalender.
        :param von: Erster Tag als JJJJ-MM-TT.
        :param bis: Letzter Tag als JJJJ-MM-TT, höchstens 31 Tage nach von.
        :param art: Kürzel des Terminrasters, wie es im Kalender steht, zum Beispiel Schw für Schwangere oder KV. Leer lassen für alle Arten. Die Variante mit vorangestelltem s (sSchw, sKV) wird mit gefunden.
        :param nur_frei: true zeigt nur freie Plätze. false zeigt auch belegte Termine mit ihren Einträgen.
        """
        if not self.valves.johm_key:
            return "Das Kalender-Werkzeug ist nicht eingerichtet: In den Einstellungen des Werkzeugs fehlt der Schlüssel."
        user = __user__ or {}
        query = urllib.parse.urlencode({"von": von, "bis": bis, "art": art, "frei": "1" if nur_frei else "0"})
        request = urllib.request.Request(
            self.valves.johm_url.rstrip("/") + "/intern/kalender?" + query,
            headers={
                "X-Johm-Key": self.valves.johm_key,
                "X-Johm-User-Id": str(user.get("id", "")),
                "X-Johm-User-Email": str(user.get("email", "")),
                "X-Johm-User-Role": str(user.get("role", "")),
            },
        )

        def fetch():
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(request, timeout=60) as response:
                return json.load(response)

        try:
            result = await asyncio.to_thread(fetch)
        except urllib.error.HTTPError as error:
            detail = error.read(300).decode("utf-8", "replace")
            return f"Der Praxiskalender konnte nicht gelesen werden ({error.code}): {detail} Es liegt keine Auskunft über Termine vor."
        except (OSError, ValueError):
            return "Der Praxiskalender ist nicht erreichbar. Es liegt keine Auskunft über Termine vor."
        if not result["entries"]:
            return f"Im Praxiskalender gibt es vom {von} bis {bis} keine passenden Einträge."
        note = " Die Liste ist gekürzt, bitte den Zeitraum verkleinern." if result["truncated"] else ""
        return (
            f"Praxiskalender {von} bis {bis}, {result['count']} Einträge.{note}\n"
            + "\n".join(result["entries"])
            + "\nNur diese Einträge wiedergeben, nichts ergänzen."
        )
