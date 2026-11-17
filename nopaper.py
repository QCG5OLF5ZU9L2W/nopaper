#!/usr/bin/python3 -I
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Startpunkt: /opt/nopaper/nopaper.py <befehl>"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
from nopaper.main import main  # noqa: E402

sys.exit(main())
