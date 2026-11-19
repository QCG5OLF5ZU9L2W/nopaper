# nopaper

[Deutsch](README.md) | **English**

Pre-processing for scanned documents in front of an **existing [paperless-ngx](https://github.com/paperless-ngx/paperless-ngx)** instance. Runs entirely locally on a machine with Debian or Ubuntu, with no cloud service; no GPU is needed.

- **Invoices and receipts** are recognised and read. The unmodified original goes to paperless with fixed tags, a descriptive title, a cleaned-up full text and the invoice data in custom fields. A sidecar file (CII XML) with the paperless ID and SHA-256 is written as well.
- **Other scans** go to paperless and get a cleaned-up full text.
- **Existing documents** in paperless can be read as invoices after the fact, or have only their full text renewed, by setting a tag.
- **Several tenants** can run separately on the same machine, each with its own paperless.

The language models run locally through [Ollama](https://ollama.com). Whatever a model reads is checked against the recognised text: a value that does not appear literally on the document is discarded or flagged for review.

nopaper is built for German documents. Prompts, validation rules (German VAT rates, tax numbers, small-amount invoices), log messages, configuration keys and the remaining documentation are in German.

## Status and limits

- For the version see `lib/nopaper/__init__.py`, for changes [`CHANGELOG.md`](CHANGELOG.md) (German).
- Tried out on **one** installation with paperless-ngx 3.x, German documents and CPU-only operation (about three minutes per document). The task format of paperless-ngx 2.x is supported but only covered by the offline tests.
- nopaper does not replace a check by a human. If the vision model and the text recognition both misread a value in the same way, no rule will notice.
- The sidecar is a bookkeeping aid in Factur-X/ZUGFeRD BASIC WL format and **not an e-invoice** in the sense of § 14 UStG (German VAT Act). It has not been validated against the official schema.
- nopaper reads header data only, no line items.
- No tax or legal advice.

Decisions and rules are recorded in [`AGENTS.md`](AGENTS.md) (German). They are binding for further development.

## Requirements

- Debian 12 or 13, or Ubuntu 24.04 LTS or 26.04 LTS (amd64), 8 vCPUs, 16–24 GB RAM, 60 GB disk (encryption recommended)
- a running paperless-ngx, reachable over HTTPS
- internet access only during installation (packages, Ollama, models)

There are no dependencies apart from distribution packages (`ocrmypdf`, `tesseract-ocr-deu`, `poppler-utils`; on Ubuntu from the `universe` component) and Ollama. The code uses only the Python standard library.

## Installation

As root, in the unpacked directory:

```bash
PAPERLESS_URL=https://paperless.example.org EMPFAENGER="Example Ltd" bash install.sh
```

The script installs the packages, Ollama with the models named in the configuration (several GB) and nopaper as a systemd service. The service is not started yet. After the models have been pulled, Ollama is restricted to localhost.

A self-extracting installer can be built with `werkzeuge/installer-bauen.sh`.

## Setting up paperless

nopaper creates nothing in paperless. Tags, fields and the API user must already exist.

1. Create an **API user** without admin rights. Required permissions: view, add and change documents; view tags, custom fields and tasks. No delete permission.
2. Create the **tags**, each with matching set to “None”, so that paperless does not assign them on its own.
3. Create the **custom fields**. Keys and data types:

| Key in `felder` | Data type | Content |
|---|---|---|
| `belegart` | Text | document kind: rechnung, kassenbon, bewirtung, versicherung, behoerde, gutschrift |
| `rechnungsnummer` | Text | invoice or receipt number |
| `rechnungsdatum`, `leistungsdatum`, `faelligkeit` | Date | invoice date, delivery date, due date |
| `rechnungssteller` | Text | name of the seller |
| `brutto`, `netto`, `steuer`, `zahlbetrag` | Monetary | gross, net, tax; `zahlbetrag` = amount still due |
| `steuer_hoch`, `steuer_niedrig` | Monetary | tax amount at rates of 10 % and above, and below 10 % |
| `skonto` | Text | cash discount as `rate:deadline:amount`, tiers separated by `;` |
| `iban`, `verwendungszweck` | Text | bank account and payment reference for the transfer |
| `pruefstatus` | Text | check status: ok / prüfen |

   Every key is optional. A field without a value stays empty.
4. Edit `/etc/nopaper/config.json` (address, recipient, tags, field names, document permissions) and put the token into `/etc/nopaper/paperless.token`.
5. Check and start:

```bash
nopaper check              # lists missing tags, fields and models
nopaper-aktivieren         # opens the network to the paperless host only, then starts the service
```

As an alternative, `nopaper paperless-skript` prints a script for the Django shell of paperless that creates the user, tags and fields. It is not required.

## The three paths

| Path | Trigger | What nopaper does |
|---|---|---|
| Scanner | PDF in `eingang/rechnungen` or `eingang/dokumente` | classify, read, hand over to paperless, fields, title, sidecar |
| Post-processing | tag from `tags.ausloeser_nachbearbeitung` on the document | read as invoice, fields, title, tags, sidecar; remove the tag |
| Full text | tag from `tags.ausloeser_text` on the document | recognise and clean up the text again, replace the full text only; remove the tag |

The full text is replaced only if the new text is not worse than the existing one.

## Directories

| Path | Purpose |
|---|---|
| `/srv/nopaper/eingang/rechnungen`, `…/dokumente` | drop folders for scans (group `nopaper-eingang`) |
| `/srv/nopaper/ausgang/rechnungsdaten` | sidecars for pickup (group `nopaper-abholung`); ignore files starting with `.tmp-` |
| `/srv/nopaper/fehler` | scans that could not be processed, with `*.fehler.json` (codes only) |
| `/srv/nopaper/arbeit` | scans in progress, deleted after the upload has been confirmed |
| `/srv/nopaper/zustand` | SQLite, containing only IDs, checksums and status |

An incoming file is deleted only after paperless has reported the document ID and the checksum of the original matches.

## Tenants

Each tenant has its own service, its own system user, its own folders, its own configuration and its own paperless. Only the program and the language models are shared.

```bash
nopaper-mandant anlegen kanzlei
# enter configuration and token under /etc/nopaper-mandanten/kanzlei/
nopaper --mandant kanzlei check
nopaper-aktivieren kanzlei
journalctl -u nopaper@kanzlei -f
nopaper-mandant liste
```

Limits: the tenants share the computing time, and the administrator of the machine can see all tenants.

## Delivery to the accounting software kumero

Optional. If the configuration contains a section `kumero`, nopaper sends every sidecar to kumero itself. The output folder then is the queue: a file stays there until kumero has accepted it. Nothing may be picked up or deleted there by hand any more.

```json
"kumero": {
  "url": "https://kumero.example.org",
  "mandant": "praxis",
  "token_datei": "/etc/nopaper/kumero.token"
}
```

- If kumero uses a self-signed certificate or an internal CA, additionally enter `"ca_datei": "/etc/nopaper/kumero-ca.pem"` and store the certificate there. For paperless the same exists as `paperless.ca_datei`. Certificate validation cannot be switched off.
- The token is kept only in the token file (root:nopaper, 0640). Afterwards run `nopaper-aktivieren` so that the service may reach the kumero host.
- `nopaper check` shows whether kumero is ready. `nopaper status` shows how many sidecars are waiting and how many were rejected.
- If kumero cannot be reached, document processing continues and the sidecars are sent later (after 1, 5, 15 minutes, then hourly).
- Rejected sidecars are stored under `ausgang/rechnungsdaten/abgelehnt/` with a companion file that names only the code.
- With `tags.zu_buchen` nopaper additionally sets a tag meaning "still to be booked" on every receipt.

## Operation

```bash
journalctl -u nopaper -f          # log: IDs, status and codes only
nopaper status                    # queue
nopaper once                      # one pass by hand
nopaper nachbearbeiten 1234       # process an existing document as an invoice right away
nopaper-modell-laden <model>      # pull another model; the network lock is restored afterwards
```

If the IP address of the paperless host changes, run `nopaper-aktivieren` again.

## Testing without paperless

```bash
nopaper test /path/to/sample-documents/       # folder or individual PDFs
nopaper test --als rechnung receipt.pdf
nopaper test --extraktor nuextract3 --nuextract-eingabe bild --modell-glaettung aus
nopaper test --aufraeumen                     # delete the test data
```

If a file `<name>.soll.json` with the expected values lies next to a PDF, the test run prints a hit rate per field. The results are stored under `/srv/nopaper/test/ergebnis/<model>/` and contain the document data in plain text. Test with sample documents only and clean up afterwards. Always take expected values from the paper, never from the recognised text.

Offline tests of the code, without network, paperless or Ollama:

```bash
python3 -I tests/test_nopaper.py
```

## Data protection

- Processing is strictly local. The service may reach only localhost and the paperless host (systemd `IPAddressDeny`), not even a DNS server.
- Logs contain metadata only, never text, names or amounts.
- No permanent copies of documents on the nopaper VM.
- The API user in paperless has neither admin nor delete permissions.
- Tests, examples and bug reports must not contain real documents.

## Licence

GNU Affero General Public License, version 3 or later (`AGPL-3.0-or-later`). See `LICENSE`.
