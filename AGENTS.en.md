# AGENTS.md – nopaper

[Deutsch](AGENTS.md) | **English**

This is a translation. The German file `AGENTS.md` is the authoritative version; if the two differ, the German text applies.

This file records **what** is being built and **which decisions have already been made**.
Anyone working on this project (human or coding agent) follows it. Deviations only after
consulting the project owner (who is also the data protection officer).

As of: 2026-11-20 (version 1.8.1; learning from bookings per E37, E39 not built yet; E40–E44 built in 1.4.1, E45 in 1.4.2/1.4.3, E46 in 1.4.4, E47 in 1.4.5, E48 in 1.4.6/1.4.7, E49 in 1.4.8, E50 in 1.4.9; E54 built in 1.4.10, E56 in 1.5.0, E57 in 1.5.2, E58 to E60 in 1.5.3, E61 in 1.6.0, E62 in 1.7.0, E63 in 1.7.1, E64 in 1.7.2, E65 in 1.7.3, E66 in 1.7.4, E67 in 1.8.0; E68 (return path for learning) postponed; E51–E53, E55 decided, not built yet)

---

**Target architecture since 2026-11-20 (E61):** paperless first, then nopaper. The scanner delivers to paperless, nopaper reacts to tags, writes everything back to paperless and keeps no data itself. The following sections describe the previous scanner mode, which stays until the new mode has been tried; where E61 differs, E61 applies.

## 1. Goal

`nopaper` is a pre-processing VM between the scanner and an **existing paperless-ngx**.

- **Invoices** are recognised, their header data is read and checked. They are created in paperless with a fixed
  tag set and get a **sidecar (CII XML)** with the paperless document ID and SHA-256. The invoice data is also
  written to **custom fields** in paperless.
- **All other scans** are created in paperless and get an **AI-checked, cleaned-up full text**.

## 2. Constraints (not negotiable)

| Item | Requirement |
|---|---|
| Data | Contains data under **Art. 9 GDPR** ⇒ processing **strictly local**. No cloud service, no external API, no telemetry. |
| Hardware | Debian (12/13) or Ubuntu LTS (24.04/26.04, E57), amd64, as a VM or directly on the machine. Designed for **CPU only, no GPU**. Guideline: 8 vCPUs, 16–24 GB RAM. |
| Volume | **about 50 documents per day at most** |
| paperless | **already exists** and is not installed by nopaper. Access only through the REST API (HTTPS). |
| Network | nopaper talks only to localhost (Ollama), the paperless host and – only if configured – the host of the accounting software kumero (E56). Internet only during installation. |

## 3. Architecture

```
Scanner
  ├─► eingang/rechnungen/   (scan profile "invoice")
  └─► eingang/dokumente/    (scan profile "document")
            │
            ▼
       nopaper VM
         1. File complete? (size stable) → SHA-256 of the original
         2. Local OCR (ocrmypdf/Tesseract, deu) → text only, the PDF is NOT uploaded
         3. Text model (Ollama): classification invoice yes/no
         4. Text model: clean-up of the OCR text (with digit invariant, see 5.4)
         5. For invoices: read header data + validation (see 7)
            Validation failed → fall back to the vision model on page 1, validate again
         6. Upload of the UNMODIFIED scanner original to paperless (post_document, with tags)
         7. Poll the task → paperless document ID
         8. Check: checksum from paperless metadata == checksum of the original
         9. Overwrite content (full text) through the API with the cleaned-up text
        10. For invoices: set custom fields, write sidecar to ausgang/rechnungsdaten/
        11. Clean up the incoming file (see 6.3)
            │
            ▼
       paperless-ngx (archive, search, user interface)

Second path – post-processing:
   Document in paperless with trigger tag ─► nopaper fetches the original via API ─► steps 2–5, 8–10
   ─► set invoice tag set, remove trigger tag
```

## 4. Decisions made

| # | Decision |
|---|---|
| E1 | Everything local, language models through **Ollama** on `127.0.0.1`. |
| E2 | **No GPU**: the text model works on the OCR text (default). The vision model is **only a fallback** for invoices whose validation fails. |
| E3 | The scanner delivers into **two folders** (invoice / document). The folder is the first classification, the AI checks against it. The folder is **not 100 % reliable**. |
| E4 | **Each PDF file is exactly one document.** Pages that belong together are in one file. No splitting, no separator sheets, no barcodes. |
| E5 | **The scanner original goes to paperless unmodified.** paperless does its own OCR and archive version as before. |
| E6 | The **AI text is used only for the full text** (field `content`) and is not written into a PDF. |
| E7 | The **tag set for invoices is static for now** and is defined in the **JSON configuration**. Likewise: tags for other documents, review tag, trigger tag. |
| E8 | **Documents already in paperless** that are invoices are processed after the fact through a **trigger tag**. |
| E9 | The invoice data is **also written to custom fields** in paperless. |
| E10 | Sidecars are written to an **output folder** and **picked up there as a batch**. |
| E11 | Configuration in **one JSON file** maintained by the owner. The API token is kept **in a separate file** with restrictive permissions. |
| E12 | The invoice recipient is **not read from the document** but set from the configuration (data minimisation). |
| E13 | **No line items** are read, because that is where the Art. 9 content would be. |
| E14 | **Models** (as of 11/2026): text `qwen3.5:9b`, vision fallback `qwen3-vl:8b-instruct`. **Switch off thinking mode** (`think: false`). Ollama loads only one model at a time (`OLLAMA_MAX_LOADED_MODELS=1`). Before production use, a comparison with 20–30 sample documents. |
| E15 | Defaults in version 1.0 (changeable through the configuration): **only XML files** go into the batch folder. nopaper **keeps no copy** of the sidecars, the pickup deletes them. The seller does **not become a correspondent** but a field. The incoming file is **deleted** after a confirmed upload. |
| E57 | **Ubuntu LTS is supported alongside Debian** (decided 2026-11-19, built in 1.5.2; changes the constraint "Hardware"). `install.sh` accepts Ubuntu 24.04/26.04 in addition to Debian; older Ubuntu releases are rejected (no Python ≥ 3.11), other Ubuntu releases run with a warning. On Ubuntu, `ocrmypdf`, `unpaper` and `tesseract-ocr-deu` are in the `universe` component; if it is missing, the installer enables it. **Network lock tighter on Ubuntu only:** there the services (nopaper and Ollama) allow only `127.0.0.1` and `::1` instead of `localhost` (= 127.0.0.0/8), because the DNS service `systemd-resolved` listens on `127.0.0.53` and the service could otherwise reach a DNS server, contrary to E47. Implementation: the unit files stay unchanged; on Ubuntu the installer places `05-ubuntu.conf` next to the allowance written by `nopaper-aktivieren` and writes the Ollama lock accordingly. Consequence on Ubuntu: Ollama must be reachable at `127.0.0.1` or `::1` (the default). **The Debian path stays unchanged.** The decisions on models and the GPU (E2, E14) are not affected. **Not yet tried on a real Ubuntu.** |
| E58 | **Long invoices: start to end instead of only the start** (decided 2026-11-19, built in 1.5.3; reason: wholesale invoices of ten and more pages whose totals page lay beyond the previous fixed limit of 12,000 characters). (1) The text budget for the analysis follows `modelle.kontext`: 12,000 characters per 8,192 tokens, never less than 12,000. With more memory (GPU), raise `modelle.kontext` and the whole invoice goes to the model. (2) If the text does not fit the budget, the model gets the start and the end (60 % / 40 %, cut at line boundaries, with an omission line in between) instead of only the start. (3) NuExtract in image mode: `modelle.nuextract_seiten` is limited to the number of pages that fit into the context (about 3,500 tokens per page image, at least 3 pages); the selection remains first pages plus the last (E30). (4) Anchoring and validation rules work on the complete text as before. With the default `kontext` of 8192 nothing changes for documents up to 12,000 characters and for three page images. Only character and page counts are logged. Classification alone (`einordnen`, first 6,000 characters) and the image fallback (page 1) stay as they are. **Not yet measured on real long invoices;** how much context fits into graphics memory next to a model has to be checked per machine (`ollama ps`). |
| E59 | **A subtotal confirmed by the tax table is a final amount** (decided 2026-11-19, built in 1.5.3; narrows E31). Reason: a restaurant receipt without a totals line – "Zwischensumme" and below it the payment line in bold with the same amount, which text recognition misread; the amount therefore stood in the text only next to "Zwischensumme", and three different readers (NuExtract on the image, the text model, the image fallback) ran into the same check. Rule: `BRUTTO_IST_ZWISCHENSUMME` is dropped if the VAT breakdown confirms the amount independently – every row arithmetically consistent, all net and tax amounts literally in the text, at least one row carrying tax, and net plus tax over all rows giving exactly the gross amount. A single tax-free row does not count (its basis is the amount in question itself). The page carry-over of a multi-page invoice is still reported, because there the breakdown gives the final amount, not the carry-over. |
| E60 | **Amounts with the printed sign** (decided 2026-11-19 by the person responsible: "as it is printed"; built in 1.5.3; follows from E38). Reason: a till receipt where the deposit refund exceeds the purchase – "zu zahlen −0,77", tax table negative. Anchoring used to compare amounts without their sign; an amount read as positive was consistent in itself and got `ok`. (1) Both prompts (standard and NuExtract) now ask for negative amounts as printed, also for net, tax and breakdown. (2) New validation rule `BRUTTO_VORZEICHEN`: if the gross amount stands in the text predominantly with a minus (before or directly after it) and the model returns it positive, or the model returns it negative and it never stands with a minus in the text, the document goes to review. The sign is never flipped automatically. If the amount stands equally often with and without a minus (e.g. a discount line next to the total), the rule reports nothing. Limits: if text recognition loses all minus signs, the rule does not apply; only the gross amount is checked. Still open: whether paperless and kumero accept negative amounts in the fields – to be checked on the first real document. |
| E61 | **Target architecture "paperless first": nopaper is stateless, everything lives in paperless** (decided 2026-11-20 by the person responsible; built in 1.6.0 as `betriebsart: "paperless"` next to the previous scanner mode; changes sections 1 and 3 as well as E10, E15, E34 and E56 for this mode). **Flow:** the scanner delivers directly to paperless (consume folder). paperless processes the document as always and assigns the trigger tag through a workflow: `tags.ausloeser_nachbearbeitung` for invoices, `tags.ausloeser_text` for everything else. nopaper only reacts to these tags (paths 2 and 3), fetches the original, reads it and writes everything back. **Reasons:** (1) intake does not depend on nopaper – if the AI machine fails, the documents are in paperless anyway. (2) Documents are there immediately. (3) paperless is improved continuously; its text is the yardstick, which nopaper only replaces if its own is not worse (E49). (4) Rebuilding the AI machine loses no data. **Rules of this mode:** (a) no incoming, outgoing or error folder and no database; nopaper only creates short-lived working files of one run. (b) **The sidecar is stored as a custom field on the document** (`felder.sidecar`, e.g. "Erkannt: Sidecar", a data type for long text; mandatory in this mode). (c) Full text, tags, fields, title and sidecar go to paperless in **one** call: either everything is there and the trigger is removed, or nothing has changed and the trigger stays. (d) The trigger tag says what is open; `nopaper status` asks paperless. (e) Attempt counters and waiting times are kept in memory only and start over after a restart; after `max_versuche` the trigger is removed and the review tag set, as before. (f) Duplicates (same seller, same invoice number) are searched through the Erkannt fields in paperless instead of an own index; this needs `felder.rechnungsnummer` and `felder.rechnungssteller`. If paperless cannot run the field query, the duplicate search is skipped and the document is processed anyway. (g) **nopaper does not deliver to kumero;** kumero fetches the sidecar from the field in paperless (still to be built on the kumero side). A `kumero` section in the configuration is an error in this mode. **Deliberately given up:** the cross-check between scan folder and AI (classification is decided by a human or a workflow in paperless through the tag) and the descriptive file name at upload (the title set by nopaper is what counts). **Transition:** scanner mode (`betriebsart: "scanner"`, the default) stays unchanged until the new mode has been tried on the reference installation; after that the scanner path, `state.py` and `kumero.py` are removed. **Open:** name and length limit of the long-text data type in paperless 3.x (`nopaper check` only rejects the types for short values); (since 1.8.1 the field query `custom_field_query` is limited to the documented basic form `[field, "icontains", value]`, the exact comparison is done in nopaper; the first real run had returned HTTP 400 with the operator `iexact`); protecting the sidecar field against accidental changes by users with edit permission; the pickup by kumero; full text by an image model (E49). **Not yet tried against a real paperless.** |
| E62 | **Appointment from a court summons into custom fields** (decided 2026-11-20 at the request of the person responsible; built in 1.7.0; extends E49). Reason: a court summons whose hearing date should be filterable and sortable in paperless. **Where:** in the full-text path (`tags.ausloeser_text`), i.e. in "paperless first" mode for every document that is not an invoice. **Fields** (each optional, only configured ones are written): `felder.termin` (date: day of the hearing), `felder.terminzeit` (text: time and room, e.g. "11:00 Uhr, Sitzungssaal V"), `felder.aktenzeichen` (text). Without these keys the full-text path stays as in E49. **Detection with fixed rules only, without a language model** (`termin.py`): (1) the letter must contain "Ladung", "geladen" or "Umladung" ("Einladung" does not count). (2) An appointment is a date preceded within 220 characters by a keyword (Termin, Verhandlung, geladen, anberaumt, bestimmt, Erörterung, Anhörung, Sitzung, verlegt) and followed within 80 characters by a time of day. Letter date, deadlines and dates of birth have no time and drop out. (3) Exactly one such day → the fields are written (`TERMIN_ERKANNT`). (4) No such day (`TERMIN_NICHT_ERKANNT`) or several different ones (`TERMIN_MEHRDEUTIG`, e.g. a rescheduling notice with old and new date) → the appointment fields stay empty and the document gets the review tag: a summons without a reliably read date must be looked at by a human. (5) The file number is only taken if exactly one labelled number (Az., Aktenzeichen, Geschäftszeichen) is in the text. **Why rules instead of a model:** a court date must not be invented; rules deliver only what is literally there, are fully testable and cost no model call. **Limits:** if text recognition misreads a digit of the date and the result is still a valid date, the wrong day ends up in the field – the summons itself is authoritative, the field does not replace deadline control. Unusual wording without a time is not recognised (review tag). So far checked on sample letters only. |
| E63 | **The invoice trigger no longer forces an invoice, and rejected writes are not repeated** (decided 2026-11-20 by the person responsible: "nopaper must not get stuck"; built in 1.7.1; changes E8 and 5.7). (1) If a document carries the tag from `tags.ausloeser_nachbearbeitung` and the model does not classify it as an invoice or receipt, nopaper writes nothing but the tags: trigger removed, review tag set (`KEINE_RECHNUNG` in the log, with the detected category). No title, no fields, no sidecar, no smoothing. Previously such a document was declared an invoice (`EINORDNUNG_UEBERSTIMMT`), because the tag counted as a human decision; in "paperless first" mode, however, a workflow assigns it too. (2) Whoever deliberately decides against the model calls `nopaper nachbearbeiten <id>`; only this manual call forces treatment as an invoice. (3) Errors that another attempt cannot fix – paperless rejects the request (HTTP 4xx except 401, 403, 404 and 408), no recognisable text, unsupported format – end processing at once: trigger removed, review tag set. Previously five attempts with growing waiting time were made. Temporary errors (network, 5xx, Ollama) are still retried. |
| E64 | **Handwritten invoice number** (decided 2026-11-20; built in 1.7.2; supplements E46). Reason: an invoice with a pre-printed "RE-Nr. ……" and a number written by hand; the field stayed empty. Text recognition does not read handwriting, so such a number can never be anchored in the text, and for small amounts a missing number did not even raise a validation error that would have triggered the image fallback. Rules, for scans only and only if the text contains a label for the invoice number (RE-Nr., Rechnungs-Nr., Rechnungsnummer, Beleg-Nr.): (1) if the number is missing after the first reading, the image model is asked about page 1 even if no other rule is red. (2) If only the fallback image model reads a number, it goes into the field (`RECHNUNGSNUMMER_AUS_BILD`) and the document goes to review (`RECHNUNGSNUMMER_NICHT_IM_TEXT`): a single reading of handwriting is not reliable enough. (3) If two different image models – NuExtract on the image and the fallback image model – independently read the same number, it counts as confirmed (`RECHNUNGSNUMMER_ZWEI_BILDLESUNGEN`, no validation error). A number from the text model does not count as an image reading. (4) If they read differently, the number of the first reading stays in the field and the document in review. (5) If a printed number in the text differs in only one digit, E46 applies unchanged. Both prompts now say that handwritten numbers are to be taken. **Limits:** two models can misread the same digit the same way (1 and 7); whether the models in use read the handwriting at all has not been measured. On machines without a GPU the additional image call costs minutes. |
| E65 | **Tax only if the document shows it** (decided 2026-11-20; built in 1.7.3; follows from E38). Reason: an invoice for 25.00 € plus 1.01 € postage, total 26.01 €, without VAT; the model returned net 25.00 and the postage as tax. The sum added up, no rule fired. Rule in `complete_vat`: if the model returns a tax amount but the document cannot show VAT, the arithmetic rate is neither 7 nor 19 %, and the model has not returned a consistent tax row at 7 or 19 % either, then tax is 0 and net = gross (hint `STEUER_NICHT_AUSGEWIESEN`). **"Can show VAT"** means: the text contains a percentage, or a tax word (MwSt, USt, Umsatzsteuer, netto, brutto, including usual misreadings) without an exemption note. "Steuernummer", "USt-IdNr.", "Finanzamt" and "Steuerberater" do not count as tax words; "umsatzsteuerfrei", "§ 4 Nr. …", "§ 19 UStG", "Kleinunternehmer" are exemption notes. If the rate is exactly 7 or 19 %, nothing is changed, because text recognition may then merely have lost the tax word. The exemption rule stays: above the small-amount limit a zero rate without a printed exemption reason still leads to review. Both prompts now state that postage, shipping and fees are not tax. |
| E66 | **Document date, date of birth and handwritten values** (decided 2026-11-20; built in 1.7.4; supplements E61 and E64). Reason: a form invoice with a handwritten "Berlin, d. …"; paperless had taken the printed date of birth of the treated person as the document date. (1) **Document date:** in "paperless first" mode nopaper sets the document date in paperless ("Date created") to the invoice date after reading an invoice – only if the date is reliable (none of the codes `FEHLT_RECHNUNGSDATUM`, `DATUM_NICHT_IM_TEXT`, `DATUM_MEHRDEUTIG`, `RECHNUNGSDATUM_IN_ZUKUNFT`). This happens in a separate second call after the main write; if paperless rejects it, the document still counts as processed. Can be switched off with `"dokumentdatum_setzen": false`. (2) **A date of birth is never the invoice date:** if the date returned by the model is labelled as a date of birth at every place it occurs in the text (geb., geboren, Geburtsdatum, *), it is discarded (`RECHNUNGSDATUM_VERWORFEN`); the document then goes to review because the date is missing. This also keeps that piece of information about the treated person out of field, title and sidecar (E13). (3) **Handwritten date and handwritten amount:** as with the invoice number (E64), a value that text recognition cannot confirm counts as confirmed if two different image models – NuExtract on the image and the fallback image model – independently read the same value (`DATUM_ZWEI_BILDLESUNGEN`, `BRUTTO_ZWEI_BILDLESUNGEN`). If they read differently, the document stays in review. **Limits:** two models can misread the same digit the same way. A date of birth without a label nearby is not recognised as such. Whether paperless 2.x accepts the date format of the second call has not been checked. |
| E67 | **Evaluating barcodes and QR codes** (decided 2026-11-20 by the person responsible; built in 1.8.0). A code is either read exactly or not at all; that sets it apart from text recognition and handwriting. **Reader:** `zbarimg` from the distribution package `zbar-tools` (installed by `install.sh`), on page images of the first three pages (300 dpi), without a language model (`codes.py`). If the program is missing or reading fails, processing continues unchanged. **Fields** (optional): `felder.barcode` (text) takes all barcodes, separated by " | "; `felder.qrcode` (long text) all QR codes. A short text field (128 characters) only gets as many whole codes as fit; a code is never cut off. Written in the invoice path and in the full-text path. **Payment QR code (EPC QR, "GiroCode") on invoices:** if exactly one is on the document, its content takes precedence over what a model has read, because the seller created it for the payment: IBAN (`IBAN_AUS_QR`) and payment reference (`VERWENDUNGSZWECK_AUS_QR`) are taken; if the amount matches gross or amount due, the amount counts as confirmed (`BETRAG_IM_QR`); if it differs from both, the document goes to review (`QR_BETRAG_ABWEICHEND`). This applies even without the two fields. With several payment codes on one document none is used. **Not part of this decision:** splitting documents at barcodes (E4 stays) and evaluating the fiscal QR code on till receipts. **Log:** never the content of a code. **Not yet tried with `zbarimg`:** what has been tested is the evaluation of its XML output on a reconstructed example and the logic with a placeholder reader; whether format and call options are right on the target machine will show on the first run. |
| E68 | **The return path for learning is postponed; it will run through paperless, not through a folder** (decided 2026-11-20 by the person responsible; changes E39, touches E37 and E51; nothing built). The corrections folder of E39 does not fit a stateless nopaper (E61): it would again be a data store outside paperless. **Order:** booking from the sidecar must work in kumero first (pickup from the field, E61 g); only then is the return path designed. **Direction for the later design:** after booking, the verified values are in paperless (the accounting software's booking fields, which nopaper only reads, E35); nopaper compares them there with its Erkannt fields. The rules of E39 remain valid in substance and are then to be transferred to fields: missing field = unverified, explicitly empty = not on the document, withdrawn booking = withdrawn example, no content in logs. **Open, to be settled in the design:** (1) how kumero expresses "verified, but not on the document" in a paperless field; (2) where supplier knowledge and learning examples (E37, E51) live if nopaper keeps nothing – for instance by nopaper querying the most recently booked documents of the same seller from paperless for each document instead of keeping its own profiles; (3) tenant separation (E51) then follows from the separate paperless instances. Until then nopaper does not learn from bookings; measuring continues through `nopaper test` with expected-value files. |
| E16 | Newly uploaded documents should not belong to the API user. nopaper therefore sets `owner` and permissions according to `dokumentrechte` in the configuration. Default: no owner, which matches an import through the paperless consume folder. For existing documents (post-processing) owner and permissions stay unchanged. |
| E17 | **Classification and invoice data in one model call** (saves about 40 s per document on CPU). Categories: `rechnung`, `gutschrift`, `kassenbon`, `bewirtung`, `versicherung`, `behoerde` (invoice-like) and `mahnung`, `bestellbestaetigung`, `buchungsbestaetigung`, `lieferschein`, `brief`, `sonstiges`. Additional tags per category through `kategorie_tags`. |
| E18 | **Digital PDFs** (real text layer, no page-filling scan images) are not recognised again; their text layer is used. Scans: ocrmypdf, then text **with layout** (`pdftotext -layout`) so that label and value stay on one line. |
| E19 | **Garbage lines** (handwriting, stains) and mostly unreadable pages are removed before the model call. The full text for paperless stays complete. |
| E20 | **Clean-up line by line:** if the model changes digits, only the affected lines are left raw. A separate, smaller model can be set for the clean-up (`modelle.glaettung`). |
| E21 | **Arithmetic repair:** if the VAT breakdown is consistent in itself and adds up exactly to the gross amount, net and tax totals are taken from it (note `SUMMEN_AUS_AUFSCHLUESSELUNG`). Otherwise nothing is ever repaired. |
| E22 | **Rules per category:** small amount ≤ €250 (§ 33 UStDV): no invoice number, no recipient and no tax number required. Insurance/authority: no tax number, the reference replaces the invoice number. Seller outside the EU: no German tax ID. |
| E23 | **No `--clean` (unpaper) with ocrmypdf.** On thermal receipts it erased thin strokes ("68,00" → "9,0", "0,43" → "0,49"). |
| E24 | **Second reading:** if validation rules are red, a scan is recognised a second time independently (greyscale, 300 dpi, Tesseract psm 6). Both texts together count as evidence for anchoring (note `ZWEITE_LESUNG`). Only then comes the vision fallback. |
| E25 | **Further arithmetic repairs** (only when consistent and evidenced): gross from the breakdown if its sum is in the text and the model value is not. Tax rate 7/19 % from base and tax. Zero rate without tax for insurance/authority. An exemption reason that is not in the text is replaced by the default of the category. A tax number with the wrong format (e.g. an institution code) and "invoice numbers" that are tax numbers are discarded. |
| E26 | **No clean-up of digital PDFs:** their text layer is free of errors. This saves 2–4 minutes per document. |
| E27 | The field `sicherheit` (confidence) is dropped. The model did not fill it in a meaningful way (0 or 1). |
| E28 | **Exchangeable extractor:** `modelle.extraktor` = `standard` (qwen with JSON schema) or `nuextract3` (numind/nuextract3, Apache-2.0, 4B, Ollama roles `template`/`instructions`). NuExtract reads either the OCR text or the first page images directly (170 dpi). Validation rules, repairs and anchoring are the same for all extractors. Anchoring checks against the OCR text in image mode as well (including the second reading). |
| E29 | Further models are pulled with `nopaper-modell-laden <model>`: lift the network lock briefly, pull, restore the lock safely. |
| E30 | **In image mode NuExtract gets the first pages plus the last one for long documents** (totals page), up to 3 pages by default (`modelle.nuextract_seiten`). Reason: a multi-page invoice where the carry-over of a page was read as the gross amount. |
| E31 | **Rule `BRUTTO_IST_ZWISCHENSUMME`:** if the gross amount appears only on lines with "Übertrag", "Zwischensumme", "Warenwert" or similar (carry-over, subtotal), the document is flagged. |
| E32 | **Insurance with insurance tax:** if "Versicherungsteuer" is in the text, nopaper sets zero rate, tax 0 and net = gross (§ 4 Nr. 10 UStG). Note `VERSICHERUNGSTEUER_KORRIGIERT`. |
| E33 | **Seller in agency sales:** if goods are sold in the name and for the account of a third party (petrol stations, postal agencies), that third party is the seller (e.g. the oil company instead of the station operator). NuExtract decides the same way. The expected values were corrected accordingly. |
| E34 | **Connection to the accounting software kumero** (formerly "Aequa"; the authoritative document there is BELEGEINGANG.md; changed 2026-11-17). nopaper → kumero: the "Erkannt" fields in paperless and the sidecar, which nopaper **sends to kumero itself** (E56) – this is the only outgoing call. kumero never calls nopaper. nopaper works without kumero as before. kumero → nopaper: correction files (E39); the way back is being described anew in kumero and will follow later. |
| E35 | **Field names with the prefix "Erkannt: "** (recognised): all fields nopaper writes in paperless carry this prefix (e.g. "Erkannt: Rechnungsdatum"). nopaper only reads the booking fields of the accounting software kumero and never writes them. |
| E36 | **Sidecars go to the pickup folder immediately**, also with check status "prüfen". The check happens through the booking in kumero; there is no approval step in paperless. |
| E37 | **Learning from booked documents** (corrected; the source is now E39 instead of a field comparison in paperless): the correction files yield (1) the hit rate in live operation, (2) supplier profiles (name, tax ID, IBAN; warning when the IBAN changes), (3) learning examples made of header text plus checked values, which NuExtract/qwen receive as an example for further documents of the same supplier. Header data only, no line items. A pseudonymisation by nopaper itself is not needed in internal operation (the accounting software sets the placeholders for the tenant's own data); it would be needed again only if training data were to leave the premises – which is not planned. If a correction file disappears, the example and the profile values derived from it are withdrawn. The learning examples are also the data set for a later adapter training (item 12). |
| E38 | **nopaper is neutral towards accounting.** It recognises **what is stated on the document**, always completely and regardless of who uses the document later and how: tax breakdown, tax IDs, payment method, amount tendered and change, due date, bank details. It **never** makes tax or accounting decisions (input tax deduction, account, period, time of outflow); that is the job of accounting. No setting and no learning signal may switch off the recognition of document statements or change it by type of tenant. Validation rules that describe what a document states (e.g. "insurance tax is not VAT", the small-amount threshold as a check of mandatory details) are document validation and stay. |
| E39 | **Correction folder as learning source and test set** (accounting software kumero, BELEGEINGANG.md; the format is being described anew there): nopaper reads the files `<uuid>.json` (schema `aequa-korrektur/1`) from a configurable folder – **read-only**, without asking paperless or kumero and without writing anything back. Rules: (1) `gebucht` (booked) contains only document statements checked by a human. (2) **Missing field = unchecked** → ignored for learning and measuring; **`null` = not on the document** → nopaper should recognise nothing here. If e.g. `steuer` is missing because the booking was made gross, nopaper learns nothing about the tax from it and keeps recognising it (E38). (3) The file is a superset of `*.soll.json`; `nopaper test` can use the folder directly as a test set. Everything booked after the last state of rules or model is the **holdout test**. (4) A file that has disappeared = a withdrawn example (reversal, re-booking, end of retention); nopaper keeps data derived from it no longer than the file itself. (5) Unknown schema version or unreadable file: skip and report, never guess. (6) The files are internal, non-anonymous data: same protection as the documents, no content in logs. |
| E40 | **nopaper creates nothing in paperless, and no scripts are run there.** Tags, fields and the API user are created by hand by the owner, who also enters the token. `nopaper check` reports what is missing (`TAG_FEHLT`, `FELD_FEHLT`); the service then does not start. (Replaces the earlier statement that nopaper creates missing tags/fields.) |
| E41 | **The check status in paperless is the inbox tag** that paperless sets itself: once it has been removed, the document counts as seen. The field `pruefstatus` is not written; `tags.pruefen` is optional. The check status remains in the sidecar. |
| E42 | **Fields in the owner's paperless:** `Erkannt: Belegart`, `Erkannt: Rechnungsnummer`, `Erkannt: Rechnungsdatum`, `Erkannt: Rechnungssteller`, `Erkannt: Betrag brutto`, `Erkannt: Betrag netto`, `Erkannt: Steuer_High`, `Erkannt: Steuer_Low`, `Erkannt: Zahlbetrag`, `Erkannt: Skonto`; in addition, at the owner's explicit request, the existing fields **`IBAN`** and **`Zahlungsziel`** without prefix (exception to E35). In post-processing nopaper overwrites these two fields. |
| E43 | **Tax by class:** `steuer_hoch` = sum of the tax amounts at rates of 10 % and above, `steuer_niedrig` = at rates above 0 and below 10 %. If the document does not show a class, the field stays empty (not 0). |
| E44 | **Payment details are document statements** (E38): change (tendered > total → paid = total, due 0); a till receipt or hospitality receipt with a payment remark counts as paid; due date from a date or from a period in days counted from the invoice date; **cash discount** per tier `rate:deadline:amount` (deadline as a date, tiers separated by `;`), only if "Skonto" and the rate appear literally in the text; the discount amount is never calculated by nopaper. In the sidecar as `#SKONTO#TAGE=…#PROZENT=…#` in the payment terms. |
| E45 | **paperless-ngx 2.x and 3.x are supported.** The owner's paperless uses the task format of 3.x (`status` in lower case, `related_document_ids`, `result_data`, `task_type`). Rules: match tasks only by their own `task_id`, never take the first task of the response; unknown responses lead to a clear error code after a short period, never to silent waiting; `nopaper check` shows the detected format. The fake server of the tests must cover both formats. The checksums `original_checksum`/`archive_checksum` are compared as MD5 (32) or SHA-256 (64) depending on their length (1.4.3). Still open: a real example of an import task (success and duplicate) from 3.x as confirmation; kumero keeps MD5 and SHA-256 of the original with each document; both have been in the sidecar since 1.5.0 (`md5-original`, `sha256-original`), but the MD5 can no longer be matched against paperless 3.x. |
| E46 | **A contradiction between the vision model and the text recognition is not decided silently.** If a receipt number from the vision model differs in exactly one digit from the number that follows a receipt label in the OCR text, the value of the vision model stays in the field (`RECHNUNGSNUMMER_ABWEICHEND`) and the document goes to review. Reason: first live run against paperless (2026-11-16), a petrol station receipt where image and OCR differed in the first digit; up to 1.4.3 the number was discarded. Lesson for expected values: never take them from the OCR text, only from the paper. |
| E47 | **The service makes no DNS queries.** The network lock (`IPAddressDeny=any`) allows only localhost, the IP(s) of the paperless host and – if configured – of the kumero host; a DNS server is deliberately not allowed (no side channel). `nopaper-aktivieren` resolves the names once and enters them permanently in `/etc/hosts`. Run it again if the IP of paperless or kumero changes. Reason: first start as a service (2026-11-16), `URLError` despite a passed `nopaper check`, because `check` runs outside the lock. Open: `check` should test reachability under the same conditions as the service. |
| E48 | **Title of invoices in paperless:** `invoice date supplier amount` (date YYYY-MM-DD for sorting, gross amount with decimal comma and currency), e.g. `2026-11-05 Muster Tankstelle 60,58 EUR`. The file is handed over under this name (`….pdf`) **already at upload**, because the scanner delivers only meaningless names (`img1.pdf`) and paperless takes the file name as title and original file name (1.4.7). Template in `titel_rechnung`; missing parts are omitted; without date and supplier the previous title stays. Applies to the scanner path and to post-processing (where an existing title is overwritten). paperless determines the file name on disk itself from its file name format. Documents that are not invoices keep their title. |
| E49 | **Third path: renew the full text of existing documents** (tag `tags.ausloeser_text`, e.g. `totext`). nopaper fetches the original, recognises and cleans up the text and writes back **only `content`**; then it removes the tag. No fields, no title, no further tags, no sidecar, owner unchanged. **Safeguard:** the text is replaced only if the new text is not worse (readable characters ≥ 90 % of the old text, share of readable characters at most 3 points lower); otherwise the old text stays and only the tag is removed. The measure works without a dictionary and detects loss and garbage, but not corrected typos – hence "not worse" instead of "better". If a document carries both triggers, only the invoice path runs. Limit: same recognition software as paperless (Tesseract); reading the page with a vision model makes sense only with a GPU and has not been decided. |
| E50 | **Payment reference** (field `Erkannt: Verwendungszweck`, key `verwendungszweck`): what the document says must be stated in the bank transfer (cash reference, file number, contract, customer or invoice number). **Hard rule:** only values that appear literally in the OCR text; a deviating model value is discarded, never corrected. As a substitute, a clearly labelled "Kassenzeichen" from the text. Better empty than wrong, because a payment with a wrong reference cannot be matched. |
| E51 | **Learning in two layers** (decided 2026-11-16, to be implemented together with learning from correction files, E37/E39). (1) **Shared layer – supplier knowledge:** identity of the supplier (name, VAT ID, tax number, address) and **layout rules** (e.g. "receipt number after 'Beleg-Nr.', format 000/000/00000", "total in the last totals block", "the seller is the leaseholder"). Only structured, derived statements, **not a single sentence from a document**. Applies to all tenants of a machine and is the basis of a possible open collection. (2) **Private layer per tenant:** document texts with checked values (learning examples), customer numbers, the tenant's own data. Never leaves the tenant and is never used when processing another tenant's documents (leakage through examples in the prompt; professional secrecy). Rules: entries arise only from booked documents; a layout rule applies only after several confirming bookings; **sole proprietors** (natural persons) stay in the private layer by default; profiles **never supply values**, only hints on where to read – anchoring in the current document applies unchanged. Confirmed bank details are kept per machine (warning on deviation) and never passed on (E52). |
| E52 | **Way back into the open swarm: document descriptions are shared, not contents** (decided 2026-11-16, not built yet). A document description says **how** a type of document is structured and **where** the details usually are: labels ("Beleg-Nr.", "Gesamtbetrag"), location (header, totals block, last page), **typical positions as relative distances** (position of the value relative to its label: to the right, below, in which column, distance as a share of the page width or in line heights; position of blocks relative to each other; for till receipts relative to anchor words instead of the page, because the length varies; each with a tolerance), format patterns (e.g. `000/000/00000`, never a real number), structure of the tax summary (columns, code letters), document kind, typical pitfalls (carry-over on page 1, "tendered/change", the seller is the leaseholder). **Never included:** values of any kind (amounts, numbers, dates, bank details), names and addresses of recipients, text from documents, contents of services or line items. This also rules out any passing on of IBANs. Rules: (1) Descriptions arise locally from booked documents and are exported as readable files; **no automatic sending**, a human checks and submits. (2) Submission and maintenance through Git (every contribution visible and reviewable), accepted only after confirmation by several independent contributors. (3) Retrieval as a signed file with the update or through a deliberate fetch; the network lock of the VM stays. (4) Local descriptions take precedence. (5) A description never supplies values, only hints on where to read; anchoring and validation rules apply unchanged – a wrong description leads to an empty or flagged field, not to a wrong value. (6) No model and no adapter is shared (a model counts as a further place of storage). (7) **Matching by both together:** printed form words (fingerprint of fixed labels) **and** company name or VAT ID of the supplier. The supplier as a key only for legal entities; sole proprietors (e.g. leaseholders) are never named, their documents find the description through the form words alone. If both features match, the description counts as reliably assigned; if only one fits, it is used as a hint with lower weight. (8) Prerequisite in nopaper: word positions from the text recognition (Tesseract TSV or a text layer with coordinates); the same basis serves the targeted re-reading of disputed fields as an image crop. |
| E53 | **Publication:** nopaper is published as free software on **GitHub**, licence **GNU AGPL, version 3 or later** (`AGPL-3.0-or-later`). Beforehand: remove all traces of real documents from code, tests and documentation (receipt numbers, tax numbers, merchant names from test runs, tag names and paths of the owner), clean history from the first commit, README for outsiders, clarifications (the sidecar is not an e-invoice; no check without a human; so far tried on one paperless 3.x). Expected-value files and test documents stay outside the repository. |
| E54 | **Tenants: one service per tenant, each tenant has its own paperless.** Separate per tenant: configuration, token, paperless address, incoming folder, output folder, error folder, state data, system user, network allowance (only to its own paperless), tags, fields, recipient, title template, private learning layer (E51). Shared: the program and Ollama with the models. The separation is one of the operating system (a separate user per service), not only of program logic. Known limits: shared computing time (one document after another), the administrator of the machine sees all tenants. **Implementation (1.4.10):** `nopaper-mandant anlegen <name>` (name 2–15 characters, a–z and 0–9), user `nopaper-<name>`, groups `nopaper-<name>-eingang`/`-abholung`, area `/srv/nopaper-<name>`, configuration `/etc/nopaper-mandanten/<name>/` (readable only by root and the service user), service template `nopaper@.service`, operation `nopaper --mandant <name> …`, start `nopaper-aktivieren <name>`. The default instance (`nopaper`, `/etc/nopaper`, `/srv/nopaper`) remains alongside. |
| E55 | **Scanner connection through WebDAV over HTTPS, received by nginx** (Debian package) on the nopaper VM. SMB is not offered (the Samba option of the installer was removed in 1.4.10), nor is FTPS. nginx accepts only `PUT` and writes the file into the incoming folder by renaming only after it has been received completely (no half files). Separate credentials per tenant and two destinations (invoice, document). The scanner may only drop files, not read, list or delete. Reference device: Epson DS-790WN (destination type "SharePoint (WebDAV)", HTTPS, user ≤ 30, password ≤ 20 characters, certificate validation only with an imported CA certificate, no port field). Open: where TLS ends (an upstream Caddy or the VM itself) and which WebDAV methods the device actually sends – to be checked on the device. |
| E56 | **Sidecars are delivered to the accounting software kumero** (order from kumero of 2026-11-17, implemented 1:1 in 1.5.0; changes E10, E15, E34 and the constraint "Network"). Optional section `kumero` in the configuration per tenant: `url` (HTTPS only), `mandant`, `token_datei`, optionally `ca_datei` (since 1.5.1). **Self-signed certificates and internal CAs are permitted if the certificate is stored in `ca_datei`**; validation is never switched off, a certificate that has not been stored is still rejected (like `paperless.ca_datei`). **Call:** `PUT {url}/m/{mandant}/api/v1/belegeingang/sidecars/{paperless_id}` with `Authorization: Bearer …`, `Content-Type: application/xml`, `Idempotency-Key` = SHA-256 of the content, `Accept: application/json`; body = the sidecar byte for byte, at most 1 MB, time limit 30 s, validated certificate, suitable for a reverse proxy. **Responses:** 200/201 with a matching `sha256` → delivered, the file is deleted; a deviating `sha256` is treated like 5xx. 401/403 (`KUMERO_TOKEN`) and 404 (`KUMERO_ZIEL`) → halt delivery, keep the sidecars, continue only after the token file has changed or the service was restarted. 413/422 → move to `abgelehnt/` with a companion file `*.abgelehnt.json` (only `code` and `zeit`), never send again. 429 → honour `Retry-After`. 5xx, timeout, no connection → retry after 1 min, 5 min, 15 min, then hourly, without an upper limit; warning when the oldest sidecar has been waiting for more than a day. **Queue:** the output folder; sent as a batch between two documents, the batch stops at the first temporary error. Delivery must never hold up or abort document processing. Without the section `kumero` everything stays as before (pickup folder). **Sidecar 1.1:** identifier `SCAN-SIDECAR/1.1`, new lines `belegart` and `md5-original`. **Tag "still to be booked":** optional `tags.zu_buchen`, set on every document processed as an invoice or receipt; kumero removes it after booking. **Token:** only in the token file, never in the configuration, logs or messages. **Network:** `nopaper-aktivieren` additionally allows exactly the kumero host (at address level; the network lock cannot restrict to port 443). **Check:** `nopaper check` calls `GET …/belegeingang/status` and reports `KUMERO_URL`, `KUMERO_TOKEN`, `KUMERO_ZIEL`, `KUMERO_NICHT_ERREICHBAR` as a warning – the service starts even if kumero cannot be reached; `nopaper status` shows waiting and rejected sidecars. Logs contain only the paperless ID, HTTP status, code and counters. **No other call to kumero**, no accounting decision (E38). Note for operation: once kumero is configured, nobody may pick up or delete files from the output folder by hand any more. Not yet tried against a real kumero. |

## 5. Processing in detail

### 5.1 Incoming
- A file counts as complete when its size has not changed for at least 10 s. Only `.pdf` is accepted (possibly `.tif`/`.jpg` later).
- The SHA-256 is calculated over the original file **immediately on arrival**. This file is never changed afterwards.
- Working copies (OCR result, page images) exist only in a private temporary directory and are deleted after the pass.

### 5.2 OCR
- `ocrmypdf -l deu --force-ocr` (or `--redo-ocr`) on a **copy**. The text is extracted from it (`pdftotext -layout`).
- The OCR PDF serves text recognition only. The original is always what gets uploaded (E5).

### 5.3 Classification
- The text model returns `rechnung` / `gutschrift` / `keine_rechnung` and a confidence (fixed JSON schema).
- Decision table:

| Folder | AI | Result |
|---|---|---|
| rechnungen | invoice | invoice path |
| dokumente | not an invoice | document path |
| rechnungen | not an invoice | document path **+ review tag**, no sidecar |
| dokumente | invoice | document path **+ review tag**, no sidecar |

- After a manual decision the human sets the trigger tag in paperless; the post-processing path follows (E8).

### 5.4 Clean-up of the full text
- Allowed: correcting recognition errors (e.g. "Rechnuug" → "Rechnung"), resolving hyphenation at line ends, joining torn lines and words, removing obvious OCR garbage.
- Forbidden: rephrasing, summarising, omitting or adding content, translating.
- **Digit invariant (hard rule):** the sequence of all digit groups (amounts, dates, numbers, IBAN, postcode) must be **identical** before and after the clean-up. On deviation the clean-up is **discarded** and the raw Tesseract text is used. This is logged (as a code only).
- Additional plausibility: the text length after clean-up must be between 80 % and 110 % of the raw version, otherwise it is discarded.
- Long documents are cleaned up page by page or in sections (context window 8k).

### 5.5 Invoice data
- Fixed JSON schema, `temperature 0`. Fields: document type, invoice number, invoice, delivery and due date, currency, seller (name, address, country, VAT ID, tax number), IBAN, BIC, payment reference, net, tax, gross, prepayment, amount due, VAT breakdown (rate, base, tax, exemption reason).
- Validation follows section 7. If it fails, the **fallback to the vision model** follows: page 1 as an image (about 150–200 dpi) to the vision model, same schema, validation again. The better result counts. If validation stays red, `pruefstatus=pruefen` applies and the document gets the review tag.

### 5.6 Hand-over to paperless
- `POST /api/documents/post_document/` with the original, the tags (IDs resolved from the configuration) and optionally the title.
- Poll `GET /api/tasks/?task_id=…` until the status is success or failure. On success: the related document = document ID.
- If a duplicate is detected (paperless rejects the checksum), the file goes to the error folder with the code `DUPLIKAT_PAPERLESS`.
- `GET /api/documents/{id}/metadata/`: `original_checksum` must match the locally calculated checksum.
- `PATCH /api/documents/{id}/`: `content` = cleaned-up text. For invoices also `custom_fields`.
- Optional `sha256-archiv`: stream and hash the archive version through the API (do not store it).

### 5.7 Post-processing of existing documents (E8)
- nopaper polls the API at a fixed interval for documents with the trigger tag.
- The original is fetched with `GET /api/documents/{id}/download/?original=true`, into the temporary work area only.
- Then follow steps 5.2–5.5, setting full text, fields and the invoice tag set, writing the sidecar and **removing the trigger tag**.
- No classification is made here, because the human has already decided.

## 6. Output

### 6.1 Sidecar
- File name: `<paperless id, 7 digits>.xml`, e.g. `0000042.xml`.
- Written **atomically** (temporary file with a dot prefix in the same folder, then `rename`). The pickup sees only complete files and must ignore files with a dot prefix.
- Format: **UN/CEFACT CII D16B**, profile **Factur-X/ZUGFeRD BASIC WL** (`urn:factur-x.eu:1p0:basicwl`). This is the EN 16931 data model without line items. It is explicitly **not an e-invoice** in the sense of § 14 UStG, but a bookkeeping aid.
- Provenance in `rsm:ExchangedDocument/ram:IncludedNote/ram:Content`. First line `SCAN-SIDECAR/1.0`, then lines `key=value`:
  `paperless-document-id`, `paperless-url`, `sha256-original`, `sha256-archiv`, `quelle` (`scanner`|`nachbearbeitung`), `extraktion-modell`, `extraktion-methode` (`text`|`bild`), `extraktion-software`, `extraktion-zeit`, `pruefstatus` (`ok`|`pruefen`), `pruefhinweise` (codes, comma-separated), `hinweis`.
- `sha256-original` is the hash of the scanner file and therefore of the original in paperless.

### 6.2 Custom fields in paperless
- Mapping and field names are in the configuration (`felder`). The fields must exist in paperless (E40); nopaper creates nothing.
- A field missing from the configuration is not written.

### 6.3 Incoming files after processing
- After a **confirmed** upload (ID received **and** checksum verified) the incoming file is **deleted**.
- On errors the file goes to `fehler/`, with a companion file `<name>.fehler.json` (codes only, no content).

## 7. Validation rules for invoices

| Code | Rule |
|---|---|
| `FEHLT_RECHNUNGSNUMMER`, `FEHLT_RECHNUNGSDATUM`, `FEHLT_BRUTTO`, `FEHLT_RECHNUNGSSTELLER`, `FEHLT_STEUERNUMMER_UND_USTID` | mandatory details present |
| `RECHNUNGSNUMMER_NICHT_IM_TEXT`, `BRUTTO_NICHT_IM_TEXT`, `IBAN_NICHT_IM_TEXT` | **Anchoring**: the value must appear literally in the OCR text (protection against hallucination) |
| `SUMME_NETTO_STEUER_BRUTTO`, `SUMME_UST_BASIS`, `SUMME_UST_BETRAG`, `UST_SATZ_RECHNUNG` | arithmetic check, tolerance 0.02 |
| `IBAN_PRUEFZIFFER`, `USTID_FORMAT`, `WAEHRUNG_FORMAT` | formats |
| `UST_BEFREIUNG_OHNE_GRUND` | 0 % only with an exemption remark |
| `DATUM_NICHT_IM_TEXT`, `DATUM_MEHRDEUTIG` | invoice date not in the text (e.g. a receipt stamp was taken) or only as a DD/MM date that could be swapped |
| `RECHNUNGSSTELLER_NICHT_IM_TEXT` | name not found in the text even fuzzily (≥ 60 %) |
| `RECHNUNGSNUMMER_FALSCHE_BEZEICHNUNG` | the number appears only after "Kunden-", "Auftrags-", "Bestell-Nr." (customer, order number) etc. |
| `VERSICHERUNGSTEUER_ALS_UST` | insurance tax was taken as VAT |
| `BRUTTO_VORZEICHEN` | sign of the gross amount contradicts the print (E60) |
| `RECHNUNGSDATUM_IN_ZUKUNFT`, `FAELLIGKEIT_VOR_RECHNUNGSDATUM` | date plausibility |
| `DUPLIKAT_VON_DOKUMENT_<id>` | same sender and same invoice number; the index stores only hashes |
| `EINORDNUNG_WIDERSPRUCH` | folder and AI disagree (5.3) |
| `BRUTTO_AUS_AUFSCHLUESSELUNG`, `UST_SATZ_BERECHNET`, `UST_NULLSATZ_KORRIGIERT`, `BEFREIUNGSGRUND_ERSETZT`, `STEUERNUMMER_VERWORFEN`, `RECHNUNGSNUMMER_VERWORFEN`, `ZWEITE_LESUNG`, `GLAETTUNG_UEBERSPRUNGEN`, `UST_AUFSCHLUESSELUNG_BERECHNET`, `NETTO_BERECHNET`, `SUMMEN_AUS_AUFSCHLUESSELUNG`, `BILDMODELL_VERWENDET`, `GLAETTUNG_VERWORFEN`, `GLAETTUNG_TEILWEISE_n`, `EINORDNUNG_UEBERSTIMMT` | notes only, not validation errors |

If at least one validation error is set, `pruefstatus=pruefen` applies and the document gets the review tag.

## 8. Configuration

Path: `/etc/nopaper/config.json` (root:nopaper, 0640). Token: `/etc/nopaper/paperless.token` (root:nopaper, 0640).
The configuration is **checked completely at start-up**. On errors the service does not start and reports a clear error message.

The current example is `config.example.json` in the repository. Keys and values are in German; the main sections are `paperless` (URL, token file, CA file), `eingang` (incoming folders), `ausgang` (output folder), `tags`, `kategorie_tags`, `felder` (custom fields), `empfaenger` (recipient), `dokumentrechte` (document permissions), `modelle` (models) and a few timing values.

- `dokumentrechte`: owner as well as groups and users with view or edit permission for **newly uploaded** documents (names as in paperless). Default: no owner.
- `sidecar_bei_pruefen`: write the sidecar also with `pruefstatus=pruefen` (default: yes). The pickup must evaluate the check status.
- Tags must exist in paperless (E40) and should have matching set to **"None"**, so that paperless does not assign them to other documents on its own.
- `paperless.url` must be `https://`. The only exception is localhost.

## 9. Security and data protection – obligations for every implementation

- **No network traffic** except to `127.0.0.1` (Ollama), to the paperless host and – if configured – to the kumero host (E56). Enforced with systemd `IPAddressDeny=any` with `IPAddressAllow=localhost` (on Ubuntu only `127.0.0.1 ::1`, E57) and the IP(s) of paperless. Ollama is bound to `127.0.0.1` and locked after the model download.
- **TLS with certificate validation**. TLS validation must never be switched off. An internal CA is specified with `ca_datei`.
- **Logs with metadata only**: document ID, file name of the incoming file, status, check codes, run time. **Never** log OCR text, invoice data, names or amounts, not even in debug mode or in exceptions.
- **No permanent copies** of documents or text on nopaper. Temporary data only in the private working directory (`PrivateTmp`), deleted after every pass.
- **State data** (e.g. SQLite) contains only IDs, hashes, status and attempt counters.
- **Least privilege** in paperless: a separate API user without admin rights, without delete permission, without password login. Needed are only: create, read and change documents, read tags and custom fields. Object permissions for documents with an owner come through a paperless workflow.
- **systemd hardening**: separate system user `nopaper`, `ProtectSystem=strict`, `ReadWritePaths` only for `/srv/nopaper`, `NoNewPrivileges`, empty `CapabilityBoundingSet`, `PrivateTmp`, `PrivateDevices`, umask 027.
- **Tests and development with sample documents only.** Real invoices must never appear in tests, fixtures, issues or chats.

## 10. Performance (guideline values, CPU)

| Step | Duration |
|---|---|
| OCR Tesseract | about 5–15 s/page |
| Classification + clean-up + reading, 7B text model | about 2–4 min/document |
| Vision fallback (page 1 only) | about 3–6 min |

With 50 documents per day this is roughly 2–3 h of computing time. Processing runs as a queue, **always only one document at a time**, so that RAM and CPU do not overflow.

## 11. Conventions for the implementation

- **Python ≥ 3.11, standard library only.** System tools through distribution packages (Debian, Ubuntu): `ocrmypdf`, `tesseract-ocr-deu`, `poppler-utils`, `zbar-tools`. No `pip install` on the target VM.
- One service `nopaper.service` with a main loop: watch the incoming folders (polling is enough), then work through the queue and poll the post-processing tag.
- **Idempotent**: every step can be resumed after a crash. The keys are the SHA-256 of the original and the paperless ID. An upload must never happen twice. Therefore the status is persisted before the upload, and before the next attempt the task ID or the paperless checksum is checked.
- **Error classes**: temporary errors (network, 5xx, Ollama) are retried with backoff, permanent errors lead to `fehler/` after 5 attempts.
- **Offline tests**: fake servers for the paperless API and for Ollama, synthetic sample PDFs. All tests must run without network.
- Messages, check codes and configuration keys are in German; identifiers in the code may be English.
- **Reusable from the prototype `rechnung-sidecar`**: JSON schema and prompt for the invoice data, `normalize`, `validate`, `complete_vat`, `build_cii`, the paperless API client (HTTPS required, hash streaming, tag resolution) and the duplicate index. The prototype worked after the import, nopaper works before it. Adapt the flow accordingly.

## 12. Open items

Items 1–4 are implemented with defaults in version 1.0 (E15). If the owner changes them, this file must be updated.

1. Content of the batch folder – default: XML only.
2. Pickup and retention – default: the pickup deletes, nopaper keeps no copy.
3. Correspondent – default: no, the seller is kept only as a field and in the sidecar.
4. Incoming files – default: delete after a confirmed upload.
5. **Concrete tag names** – entered by the owner in `config.json`.
6. **Scanner connection** – see E55 (WebDAV over HTTPS through nginx; not built yet).
7. **Validation of the XML** against the official Factur-X XSD and Schematron is still pending.
8. **Model comparison** with sample documents on the target VM (E14). Tool: `nopaper test --modell-text … --modell-bild …`, evaluation through `zusammenfassung.csv` per model.
9. **Findings from the first test document (hospitality receipt, 2026-11-11)** – implemented in version 1.1 (a: fuzzy anchoring + name correction in the prompt, b: rule stays, extended by OCR confusions O/D→0, c: garbage filter E19, d: category `bewirtung` E17):
   a. The seller is not checked against the text (OCR "Trattorla Esernpio"). Proposal: read invoice data from the cleaned-up text and anchor the name fuzzily.
   b. Anchoring the gross amount can confirm a misread amount ("58, 00"). The totals check catches that. This rule must not be weakened.
   c. Handwritten pages (hospitality record) produce OCR garbage in the full text. Names of persons are not read; that stays.
   d. Consider hospitality receipts as a category of their own with their own tag (e.g. "Bewirtung").

10. **Test set:** 20 typical documents with expected values (`*.soll.json`, not in the package, kept by the owner). With perfectly read data, 3 of 14 invoices are red, all rightly so (ambiguous US date, insurance without a date, OCR misreading in the date). Of 10 injected typical model errors, all are detected or repaired arithmetically.

11. **First real run (2026-11-12, qwen3.5:9b + qwen3-vl:8b, CPU):** classification 20/21 correct. Wrong was a ticket invoice → booking confirmation (prompt adjusted). 387 s per document on average, most of it clean-up. The main source of errors was the OCR of ocrmypdf (E23, E24). The model outputs of this run, recalculated with the rules of version 1.2: gross 13/15, net 11/15, tax 12/15 (previously 12/9/10).

12. **Own model (proposal, not decided yet):** training only as an adapter (LoRA) on an unchanged base model. Each generation is trained completely anew from the approved documents within the retention period; the previous generation is deleted. Each generation gets a provenance record (period, count, hash list of document IDs). Training uses header data only. Prerequisite is the approval workflow (still to be built). Yearly rhythm (retention ends with the calendar year, § 147 Abs. 4 AO). A model counts as a further place of storage (EDPB Opinion 28/2024).
13. **Learning from bookings** – decided (E37, E39), not built yet. Prerequisite: correction files of the accounting software kumero (BELEGEINGANG.md). Implemented in 1.4.1 after the first holdout run with three unknown till receipts (29 of 30 fields correct): E44 and receipt numbers in tax-number format with a label. Cash discount has not been measured on real documents yet (no document with a cash discount in the test set).

14. **Model comparison on CPU (2026-11-12/13, 20 documents, without vision fallback and clean-up for NuExtract):**

| Field | qwen3.5:9b (1.2, with clean-up + fallback) | NuExtract3 image | NuExtract3 text |
|---|---|---|---|
| Invoice yes/no | 18/19 | 19/19 | 18/19 |
| Category | 18/19 | 19/19 | 16/19 |
| Invoice number | 9/9 | 8/9 | 8/9 |
| Date | 13/14 | 13/14 | 10/14 |
| Gross | 14/14 | 13/14 | 13/14 |
| Net | 12/14 | 11/14 | 10/14 |
| Tax | 13/14 | 12/14 | 11/14 |
| Seller | 13/14 | 13/14 (14 after correcting the expected value) | 11/14 |
| Tax ID | 10/14 | 11/14 (12 after correcting the expected value) | 8/14 |
| IBAN | 14/14 | 14/14 | 14/14 |
| avg. analysis / avg. total | 99 s / 322 s | 172 s / 204 s | 98 s / 134 s |

Result: NuExtract in image mode is on a par with qwen, and probably better with the corrections from 1.4 (E30–E32). Text mode is weaker (a postal receipt as a letter, a petrol receipt as a credit note). On CPU image mode is slower than text; with a GPU this does not matter. All values come from the training set; an independent test set is pending.

## 13. Code layout (version 1.8.1)

| File | Content |
|---|---|
| `lib/nopaper/config.py` | load and check the JSON, field key → paperless data type |
| `lib/nopaper/ocr.py` | ocrmypdf on a copy, text with layout, second reading, page images via pdftoppm, text quality measure |
| `lib/nopaper/llm.py` | Ollama client, classification, clean-up (in sections, digit and length rule), invoice data from text or image, NuExtract |
| `lib/nopaper/invoice.py` | schema, prompt, `normalize`, `validate`, `complete_vat`, `build_cii`, `atomic_write` |
| `lib/nopaper/codes.py` | Barcodes and QR codes through `zbarimg`, payment QR code (E67) |
| `lib/nopaper/termin.py` | Appointment, time with room and file number from a court summons, fixed rules only (E62) |
| `lib/nopaper/paperless.py` | API client: upload (multipart), task polling for 2.x and 3.x, metadata, hash streaming, PATCH |
| `lib/nopaper/kumero.py` | delivery of the sidecars to the accounting software kumero: queue, retries, rejections, check (E56) |
| `lib/nopaper/state.py` | SQLite: incoming, post-processing, duplicate hashes |
| `lib/nopaper/pipeline.py` | flow of all three paths, retries, error folder |
| `lib/nopaper/testlauf.py` | test run **without paperless** (`nopaper test`): results per model under `/srv/nopaper/test/ergebnis`, with sample documents only |
| `lib/nopaper/main.py` | CLI: `run`, `once`, `check`, `status`, `nachbearbeiten <id>`, `test`, `paperless-skript` |
| `bin/` | `nopaper` (CLI wrapper), `nopaper-aktivieren`, `nopaper-mandant`, `nopaper-modell-laden` |
| `systemd/` | `nopaper.service` (default instance), `nopaper@.service` (tenants) |
| `tests/test_nopaper.py` | offline tests with fake paperless/Ollama |
