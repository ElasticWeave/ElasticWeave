# Distilled from Git Changes and Community Tools

Research date: 2026-07-01.

This skill was distilled from public GitHub commits and PRs that explicitly describe README/report prose as “AI slop,” “less AI,” “human voice,” or “less AI-generated,” plus community anti-slop tools.

## High-signal GitHub changes

These are representative cases used to extract the rules. The repeated pattern across these changes is not “make it casual.” It is “make it concrete, shorter, and reader-facing.”

### Detailed diffs reviewed

1. `Arin016/s3-outputstream` — “Rewrite README — human voice, no AI slop”  
   URL: https://github.com/Arin016/s3-outputstream/commit/4140635b2f84a14cde656a3d5e83606751515cfa  
   Observed move: removed a long table of contents, reduced architecture exposition, put the S3 OutputStream problem and a copyable usage example near the top, and replaced generic claims with a concrete memory claim.

2. `openriftapp/openrift` PR #166 — “docs: make README copy read less AI-generated”  
   URL: https://github.com/openriftapp/openrift/pull/166  
   Observed move: changed “Live, actively developed, and improving fast” to a calmer status line; expanded generic feature bullets into specific product facts; rewrote “shoehorned in” AI wording into a sharper non-goal.

3. `qiaeru/skill-claudemd` — “docs: remove AI writing patterns from README prose”  
   URL: https://github.com/qiaeru/skill-claudemd/commit/9d9b3e8ff9e5294b07c535b5eff675656a1b0a79  
   Observed move: changed passive voice to an actor-led sentence, dropped filler and adverbs, removed a “not just” contrast, and made the subject of each action explicit.

4. `Varybai/opensell-cli` — “docs: rewrite README prose in a plainer, human voice (en/zh/ja/ko)”  
   URL: https://github.com/Varybai/opensell-cli/commit/e2ae956180ef442ec839fec8341a0aa1128a4865  
   Observed move: removed agent-era marketing language, preserved tables and code, used active voice, named actors, and repeated the cleanup across multiple languages.

5. `jgsystemsconsulting/jgs-magic-sysmlv1-pro` — “docs: de-slop human-facing copy, remove em dashes”  
   URL: https://github.com/jgsystemsconsulting/jgs-magic-sysmlv1-pro/commit/4d6621f96c1cf68817e72529b00d62fda52c0415  
   Observed move: used a technical-writer pass, removed em dashes, preserved factual claims, and verified with an anti-slop cleaner.

6. `lrdmora/N_A_G-Narrative-Anchor-and-Guide` — “README voice pass — replace AI slop with human tone throughout”  
   URL: https://github.com/lrdmora/N_A_G-Narrative-Anchor-and-Guide/commit/d38a2740c62c3254b2b74127cdad2e3262b912be  
   Observed move: rewrote section intros in the author's voice and consolidated install instructions.

### Additional cases sampled

- `Zwin-ux/kiroween` — human-focused README, removed AI slop.
- `zidage/AlcedoStudio` — replaced AI-slop README with semi-human README.
- `praneet1503/GhostLink` — tried to make README “not ai”.
- `DevMinGeonPark/gaejo` — human voice, less AI-slop, cleaner.
- `ixchio/n0x` — human voice, no AI slop.
- `ShovalBenjer/phantom-reach` — stripped AI slop from README.
- `a-castellano/go-types` — removed AI slop from README.
- `Creeperman3000/Scifind` — removed AI slop README.
- `choccynix/AnthorISO` — removed AI slop README.
- `slayernominee/lift` — removed AI slop description.
- `Pranav00x/Pranav00x` — less AI, more human.
- `AdnanHussainTurki/servicio` — plainer, less AI-sounding README.
- `lexus-x/PPO-RL` — less AI, more human.
- `Quitetall/LamQuant-Lossless` — less AI, more signal.
- `AberrantWolf/dqx-proton-helper` — made README less AI-slop-y.
- `mikeritchie1/my_dashboard_demo` — plain dashes, tighter prose, less AI-sounding.
- `nadellasripad11/serenityux` — more human, less AI tone.
- `PranavAgarkar07/data-analytics-internship` — less AI-ish.
- `giganiga6767/UR5_VERIF_TOOL` — more casual, less AI-like.
- `decksaga/decksaga` — profile README less AI, more human.
- `NightEye000/exuscommerce` — English README less AI-generated.
- `BaavanshReddy/campus-event-scraper` — human voice.
- `BaavanshReddy/tinyl-compiler` — human voice.
- `BaavanshReddy/linux-filesystem` — human voice.
- `9bzero/countdown-timer` — human voice, varied style.
- `9bzero/2048-game` — human voice, varied style.
- `9bzero/markdown-editor` — human voice, varied style.
- `9bzero/image-color-extractor` — human voice, varied style.
- `dcadolph/slop-chop` — plain human voice.
- `payaljarviya/ab-test-analysis` — human voice.

## Community tools reviewed

### `hardikpandya/stop-slop`

URL: https://github.com/hardikpandya/stop-slop

Useful ideas:

- remove predictable AI tells;
- cut throat-clearing phrases;
- break binary contrasts;
- use active voice;
- vary rhythm;
- score directness, rhythm, trust, authenticity, and density.

### `stephenturner/skill-deslop`

URL: https://github.com/stephenturner/skill-deslop

Useful additions:

- broader trigger language: “deslop,” “de-AI,” “make it sound human”;
- scientific and technical writing exceptions;
- context-sensitive register;
- formatting tells such as bold-first bullet lists;
- examples for scientific prose and blog prose.

### `sigridjineth/ai-slop-cleaner`

URL: https://github.com/sigridjineth/ai-slop-cleaner

Useful guardrail:

- structural pattern scans can provide evidence;
- the rewrite itself should be full-text semantic inference;
- never clean prose by regex substitution;
- preserve meaning and grammar.

## Distilled rules

1. First screen matters more than total polish.
2. Cut generic claims unless a fact supports them.
3. Move usage before architecture in READMEs.
4. Put answers before background in reports.
5. Replace adjectives with mechanisms.
6. Keep caveats and non-goals.
7. Use active voice and name the actor.
8. Avoid formulaic contrast and triads.
9. Preserve code, citations, links, and numbers.
10. Rewrite sections, not sentences.
11. Do not use regex substitutions for cleanup.
12. A human voice is specific, not sloppy.
