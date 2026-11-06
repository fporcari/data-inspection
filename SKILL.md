---
name: data-inspection
description: Guard to pass BEFORE any client data reaches the model. Invoke before reading, querying, opening or analysing anything that may be a client's data — a file that is not code (xml, p7m, csv, xlsx, json, sql dump, export, attachment), a database table or query result, an MCP call over a client database, text the user is about to paste — and whenever the user hands over a path or a database reference to look at: "guarda questo xml", "analizza questa tabella", "controlla il db del cliente", "data-inspection <path>". Shows the shape without the values, asks whose data and what for, agrees which fields are sensitive, then works only through a view that keeps the values on this machine. Not for the repository's own code, docs or synthetic fixtures.
---

# Data inspection

A value that has entered the context has left the machine. Deciding after looking is
too late, so the check comes before the action, whatever the format — the way a
dangerous request is declined before it is carried out, not after.

`<skill-dir>` below is the folder this file lives in. The work folder is a private
temporary folder outside the repository: the session scratchpad where the agent has
one, otherwise `$TMPDIR/data-inspection/`.

## 1. Shape, never values

- **File**: `python3 <skill-dir>/scripts/shape.py <file>` prints element paths,
  attribute names, headers, keys, value classes, length ranges and counts. With
  Presidio (see below) each field also carries the personal data found in a sample of
  its values — `cod_fisc [IT_FISCAL_CODE 50/50]`, `note [PERSON 31/50]` — types and
  counts, never values. Exit 2 means the format is unknown or unparsable: ask the user
  what it is.
- **Database**: schema only — the database's own catalogue (`\d <table>`,
  `information_schema`), the ORM model, or a schema tool the agent has (with Sourcerer:
  `target_db_relationtree`, which carries column attributes, `encrypted` included). No
  `SELECT *`, no sample rows, no query tool that returns records.
- **Pasted text**: ask before the user pastes it. Already pasted → it is in; say so,
  and apply the rest of this skill to whatever comes next.

In this step never open, `cat`, `head` or `grep` the source itself.

## 2. Ask, in one round

Ask with the agent's question tool when it has one (`AskUserQuestion` in Claude Code),
otherwise in a single chat message, and wait for the answer.

- **Whose data**: which client, and is it authorised — a ticket, a processor agreement
  naming the model's provider as a sub-processor, a business account with that
  provider. "Synthetic", "mine" or "nothing personal in it" ends the skill: carry on
  normally.
- **Goal**: the question the work must answer. It decides which fields are needed.
- **Fields**: propose a classification from the shape — **sensitive** (identifies or
  describes a natural person: names, tax ids, addresses, contacts, birth dates, bank
  accounts, free text about a person, health, judicial), **needed**, **irrelevant**.
  Reason on names in any language or abbreviation (`cod_fisc`, `cf`, `rag_soc`,
  `nominativo`), on value classes and lengths, on the XSD or documentation when the
  format is known, and on the personal-data profile when there is one: a field named
  `codice` whose values are `IT_FISCAL_CODE 50/50` is sensitive whatever its name. The
  user confirms or corrects; their word wins.
- **Strategy**: propose the one from step 3 that fits the goal; the user picks.

## 3. Strategies — the values stay here

| Strategy | When | How |
|---|---|---|
| **Code to the data** | the answer is a verdict, a list of anomalies, a count | a script reads the raw source locally and prints only findings, located by id or position, never field values. The default for "why does this record fail" |
| **Projection** | a database — the first choice there | explicit columns only: ids plus the needed fields, never the default or all columns |
| **Anonymise on the fly** | records have to be seen | `anonymize.py` writes a copy with every sensitive field replaced; only the copy is read |
| **Aggregate** | distributions, totals | counts and sums, no group smaller than five |
| **Stop** | not authorised, or not needed after all | say so |

On a database the policy is a query that never selects personal data; anonymising
comes only after it, when the records' personal fields themselves are what the task
needs. Derive the query from the structure, never from a look at the rows:
- the personal columns come from the schema — the catalogue, the ORM model (names,
  labels, types, encryption flags), the step 2 classification;
- relation paths count too: a join that reaches a person's table brings their data in
  (in GenroPy: `@cliente_id.ragione_sociale`, `@anagrafica_id.cognome`);
- filter by ids or non-personal fields. A personal value in a `WHERE` clause or a query
  parameter (search by tax id, by surname) has itself reached the model: ask the user
  for the id, or let a local script resolve it;
- when the question is a count or a distribution, aggregate in the query (`GROUP BY`,
  `COUNT`; in GenroPy `group_by`, `countOnly`) instead of fetching rows.

Anonymising: write the classification agreed in step 2 as a map, field paths exactly as
`shape.py` printed them, into the work folder, then

```bash
python3 <skill-dir>/scripts/anonymize.py --map <map.json> --out-dir <work-folder>/anon <file> [<file> …]
```

| Action | Effect |
|---|---|
| `keep` | the value stays |
| `drop` | the element, attribute, column or key goes — attachments, free text without Presidio |
| `pseudo[:TYPE]` | consistent pseudonym; tax ids, VAT numbers and IBANs stay valid (check characters recomputed, an Italian IBAN's CIN is not), other values become `Persona_7f3a`, `utente_7f3a@esempio.invalid` |
| `text` | free text: Presidio finds the entities and pseudonymises them in place (needs Presidio) |

- A field the map leaves out stops the run before anything is written. `--default keep`
  is a decision to state to the user, not a shortcut.
- One run, one random key held in memory: the same value gets the same pseudonym across
  every file of the run, so pass together the files that must still join.
- With Presidio, `keep` fields are scanned again and a `residue in keep field` line
  reports personal data still there: take it back to the user.
- The script prints counts only. Read the copies, never the originals.
- Pseudonymised data is still personal data: the authorisation question still applies.

Every script written for the task, whatever the strategy:
- lives in the work folder, never in the repository, and takes the source as an
  argument;
- prints counts and findings only — no rows, no debug print of a value, and an error
  reports its type and position, never the offending value or the database's message;
- is shown to the user before its first run on real data.

## 4. Proceed

Say in one line: source, fields excluded, strategy. Then work only on what step 3
produced. A value turns out to be needed later → back to step 2 for that field; never
peek at it.

## Presidio, optional

The personal-data profile and the `text` action come from Presidio with the Italian
spaCy model (`it_core_news_md`); its Italian recognizers (codice fiscale, partita IVA,
carta d'identità, patente, passaporto) are on by default. It lives in a venv of its
own, `~/.local/share/data-inspection/venv` (or `$DATA_INSPECTION_VENV`), because its
`cryptography` pin breaks other packages of a shared Python; both scripts re-run
themselves under it when it exists. Setup:

```bash
python3 -m venv ~/.local/share/data-inspection/venv
~/.local/share/data-inspection/venv/bin/python -m pip install presidio-analyzer presidio-anonymizer openpyxl
~/.local/share/data-inspection/venv/bin/python -m spacy download it_core_news_md
```

Without it everything else works: no profile, and `text` refuses — map the field to
`drop`. Presidio itself warns there is no guarantee it finds everything: a name the
model does not know, or a tax id mangled in a free-text note, passes. The profile is
evidence for step 2, never a replacement for it.

## Limits

- The shape is metadata, not nothing: element and column names, lengths. A CSV or
  sheet whose first row is data, not a header, leaks that row as names. For CSV the
  script numbers the columns unless the header is certain; a sheet's first row is
  always taken as header, so ask before shaping a sheet that may have none.
- A JSON dict keyed by data is reported as `{keys}` when it has more than 20 keys or
  keys that do not read as field names; a small dict keyed by plain words (first names
  as keys) still prints them as paths. Ask before shaping JSON built that way.
- Nothing enforces this skill: it holds because the model stops before looking.
