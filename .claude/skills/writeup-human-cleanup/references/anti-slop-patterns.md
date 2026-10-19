# Anti-slop Patterns

## Phrase patterns to remove

- Here's the thing
- Here's why this matters
- Let that sink in
- In today's fast-paced world
- It is worth noting
- At its core
- Moving forward
- In conclusion
- This report explores
- This comprehensive guide
- A robust and scalable solution
- Seamless integration
- Designed to empower
- Unlock the power of
- Navigate the complexities of
- Dive deep into
- Not just X, but Y
- Whether you're X or Y

## Structure patterns to break

### False contrast

Bad:

> This is not just a CLI. It is a complete platform.

Better:

> The CLI validates files, submits jobs, and streams logs from the server.

### Three-part rhythm

Bad:

> Fast, flexible, and powerful.

Better:

> Processes 10k rows in 18 seconds on a laptop.

### Bold-first bullet wall

Bad:

```markdown
- **Powerful:** ...
- **Seamless:** ...
- **Robust:** ...
```

Better:

```markdown
- Retries failed uploads three times.
- Writes rejected rows to `errors.csv`.
- Exits non-zero when validation fails.
```

### Passive actor hiding

Bad:

> The report was generated to analyze trends.

Better:

> We analyzed renewal, expansion, and churn data for Q2.

### False agency

Bad:

> The data tells a compelling story.

Better:

> The renewal data points to one problem: onboarding stalls after week two.

### Decorative metaphor

Bad:

> The lemma survived two attacks, but the synchronization tax ate the gain.

Better:

> Neither falsification test rejected the lemma, but added synchronization cost exceeded the
> measured gain.

Do not run a blind word blacklist. Keep conventional technical terms when they are defined and
more precise than a literal paraphrase. Remove metaphors that add drama, change from paragraph to
paragraph, or hide the measured relationship.

## Patterns common in translated Chinese business prose

- In today's rapidly changing era
- In-depth analysis
- Comprehensive empowerment
- Build a closed loop
- Ecosystem capabilities
- Enable business growth
- Key lever
- This article will show you
- Not only ... but also ...
- It is worth noting that
- In summary

Replacement rule for translated Chinese prose: name the actor, action, object, and measurable result.

Bad:

> This solution enables business growth through comprehensive capabilities.

Better:

> The script pulls orders every day at 9:00 a.m., checks for missing fields, and writes anomalies to a Feishu spreadsheet.
