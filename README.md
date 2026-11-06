# data-inspection

A skill for coding agents — Claude Code and Codex — that work on real client data:
database copies, exported files, electronic invoices, attachments. It makes the agent
stop before it looks, see the shape of the data instead of its values, agree with you
which fields are personal, and then work through a view that keeps the values on your
machine.

## Why

Developers who support business software debug on real data all the time. With an
agent in the loop, every value it reads is sent to the model's provider. Under the GDPR
that provider becomes a sub-processor of your client's data, which is fine only when
the client has authorised it, the account carries a data processing agreement, and you
send no more than the task needs. Most of the time the task needs no personal data at
all: an invoice that fails validation needs its structure, amounts and VAT codes, not
the customer's name.

A value that has entered the context has already left the machine, so the check has to
come before the read. No filter can know every format a person's data hides in, so the
check is the agent's own judgement, triggered by a rule and guided by this skill — the
same way it declines a harmful request before carrying it out.

## How it works

1. **Shape, never values.** `scripts/shape.py` prints element paths, headers, keys,
   value classes, length ranges and counts of a file — xml, signed p7m, csv, json,
   xlsx, sql dumps. For a database the agent reads only the schema or the ORM model.
   With [Presidio](https://github.com/data-privacy-stack/presidio) installed, each
   field also gets a profile of the personal data found in a sample of its values:
   `cod_fisc [IT_FISCAL_CODE 40/40]`, types and counts, never values.
2. **Questions, in one round.** Whose data is it, is it authorised, what question must
   the work answer, which fields are sensitive. "Synthetic" or "mine" ends the skill.
3. **A strategy that keeps the values local.**
   - *Code to the data*: a local script reads the source and prints only findings,
     located by id — "line 3: VAT nature N2.2 with a 22% rate".
   - *Projection*: on a database, a query that selects no personal column, derived
     from the schema, filtered by ids rather than by names or tax ids.
   - *Aggregate*: counts and totals, no group smaller than five.
   - *Anonymise on the fly*: `scripts/anonymize.py` writes copies from a field map
     (`keep`, `drop`, `pseudo`, `text`). Pseudonyms are consistent across files and
     stay valid where the program validates them: Italian tax ids, VAT numbers and
     IBANs get their check characters recomputed. A map that leaves a field out
     stops the run before anything is written.
   - *Stop*, when the data is not authorised or not needed after all.
4. **Proceed** on what step 3 produced, never on the originals.

## Install

```bash
git clone https://github.com/fporcari/data-inspection ~/.claude/skills/data-inspection
```

For Codex, link the same folder where it reads personal skills:

```bash
ln -s ~/.claude/skills/data-inspection ~/.agents/skills/data-inspection
```

Then paste the rule in [RULE.md](RULE.md) into the instructions the agent always loads
(`~/.claude/CLAUDE.md`, `~/.codex/AGENTS.md`). The skill's description can trigger it
on its own; the rule is what makes it a reflex.

Presidio is optional. It lives in a venv of its own, because its `cryptography` pin
breaks other packages of a shared Python; the scripts find it there and re-run
themselves under it:

```bash
python3 -m venv ~/.local/share/data-inspection/venv
~/.local/share/data-inspection/venv/bin/python -m pip install presidio-analyzer presidio-anonymizer openpyxl
~/.local/share/data-inspection/venv/bin/python -m spacy download it_core_news_md
```

Without it everything works except the profile and the `text` action.

## Check

```bash
python3 tests/check.py
```

Builds synthetic files, runs both scripts with and without Presidio, and fails if any
original value reaches their output or the anonymised copies, or if a fake tax id, VAT
number or IBAN does not validate.

## What it does not do

- **It does not enforce anything.** It holds because the agent stops before looking.
  If the agent misjudges what is client data, nothing blocks it.
- **It does not catch everything.** The shape is metadata, and Presidio itself says it
  cannot guarantee finding every piece of personal data; free text needs a human look.
- **It does not make you compliant.** Authorisation, contracts and accounts are yours to
  get right. Nothing here is legal advice.
- **It is Italian-first**: the Presidio model, the recognizers that matter most and the
  valid-fake generators are Italian. Other languages need their spaCy model and their
  own identifiers.

So far it has been tried on synthetic data with Claude Code and Codex, each given a
request designed to tempt a direct query on patients' health data; both stopped,
read only the schema and asked before touching a row.

## License

MIT
