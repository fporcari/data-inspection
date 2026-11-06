# The rule

The skill holds only if the agent reaches for it before looking. Paste this section
into the instructions the agent always loads: `~/.claude/CLAUDE.md` for Claude Code,
`~/.codex/AGENTS.md` for Codex, or a project's own `CLAUDE.md` / `AGENTS.md`.

```markdown
## Client data
- Anything that may be a client's data — a file that is not code, a database table or
  query result, an export, an attachment, pasted text — is client data until I say
  otherwise. Before reading, querying or analysing it, stop and invoke
  `data-inspection`: never look first and decide after.
- Until a strategy is agreed, see the shape (paths, headers, keys, column names and
  attributes), never the values.
- Client data reaches the model only from a business account of the model's provider
  with a data processing agreement — Claude: Team, Enterprise, API, Bedrock; OpenAI:
  API, ChatGPT Business or Enterprise — and only for a client whose processor agreement
  names that provider as a sub-processor; otherwise anonymise it first, or leave it out.
- Never copy client data into commits, PRs, issues, KB entries, memory or test fixtures.
```

The account list reflects the providers' published terms when this was written; check
them for your own plan before relying on it.
