# Prose style

Write documentation for a technical reader who may be in a hurry. Lead with
the result, then explain the mechanism and limits that help the reader use or
judge it.

Use clear, direct, and conversational language. Prefer concrete verbs and
name the component that performs an action. Address the reader as "you" in
instructions. Keep technical terms when they are more precise than a plain
substitute.

The general voice follows the
[Google developer documentation style guide](https://developers.google.com/style/tone).

Use sentence-case headings. Keep examples executable and claims testable.
State uncertainty, costs, and unsupported cases without promotional language.

## Avoid formulaic prose

Vary sentence length and structure. Prefer syntax that states the relationship
between ideas over syntax that merely adds one item after another.

Avoid enumerative parataxis. It often appears as:

- repeated inline lists;
- compound predicates;
- balanced coordinate clauses; or
- affirmative-negative sentence pairs.

Common frames include `It does A, B, and C`, `X does A and does B`, and
`It does A. It does not do B`. Use subordination when ideas have a causal,
conditional, temporal, concessive, or purposive relationship. Reserve inline
lists for items that form a meaningful set.

Replace the resultative frame `X, so you can Y` with the relationship that
makes Y possible:

```text
Because the repository includes the fixtures, you can regrade a saved run.
```

Do not repeat a grammatical frame across adjacent sentences or paragraphs.

Cut stock transitions, filler, chatbot closings, dramatic setup clauses, and
contrasts that turn two facts into a slogan. Limit em dashes rather than using
an em dash as a default transition. End on the final substantive point instead
of a summary that repeats it.

These rules describe text patterns. They do not identify who or what wrote the
text. A person can write formulaic prose, and an agent can write clean prose.

## Automated checks

[Vale](https://vale.sh/) parses Markdown before it applies the rules in
`styles/`. The `Geodata-*` styles cover project terminology, mechanics, and
formulaic voice patterns. The pinned Google package supplies the broader
developer-documentation rules. The Microsoft package adds guidance about clear,
direct technical writing. Rules that duplicate or conflict with local
conventions are disabled in `.vale.ini`.

The Readability package reports the Automated Readability Index (ARI) and
Flesch Reading Ease for each document. Both findings are suggestions, so they
appear in the advisory audit but don't fail CI. Use the scores to compare a
document before and after an edit. Technical names can keep a sound document
outside the package's general targets.

Improve readability by unpacking abstractions and making relationships clear.
Don't shorten sentences or replace precise terms only to improve a score.

[proselint](https://github.com/amperser/proselint) reports selected clichés,
hedges, redundant phrases, mixed metaphors, and commercial language. Its
findings are advisory because they require editorial judgment.

Run the commit-stage checks with the same command as CI:

```bash
uvx prek@0.4.11 run --all-files --show-diff-on-failure
```

Run the advisory checks through the pinned hook environments:

```bash
uvx prek@0.4.11 run vale-audit --all-files --hook-stage manual
uvx prek@0.4.11 run proselint --all-files --hook-stage manual
```

The normal Vale hook blocks errors in handwritten documentation, including the
benchmark rules in `SPEC.md`.

## Suppress a false positive

Prefer a narrow suppression and explain why the prose must keep its form.

```markdown
<!-- vale Geodata-Mechanics.Headings = NO -->
## A Heading That Is an External Name
<!-- vale Geodata-Mechanics.Headings = YES -->
```

Use `<!-- vale off -->` only for a whole block that Vale cannot parse usefully.
Do not suppress a finding only to make the check pass.

## Rule ownership

The repo owns the `Geodata-*` rules and their tests. They are adapted from the
Portolan prose rules at the commit named in `styles/NOTICE`. `vale sync`
downloads the three pinned packages into ignored directories.

Each custom rule has one failing and one passing example in
`tests/test_prose_styles.py`. Add both examples when you add or change a rule.
