# Security

## What this tool can reach

harness-tuner is a measurement tool, and in one mode it executes software.

**Observe mode** invokes nothing. It reads trace files your adapter produced and writes artifacts. Its blast radius is the output directory.

**Drive mode** invokes your harness, which typically has real shell, filesystem and network access. harness-tuner refuses to drive until `containment.approved` is set and `containment.reach` describes in plain words what a driven run can touch. Both are recorded in every artifact the run produces, so a report always says what the run was allowed to reach.

harness-tuner prescribes no containment mechanism, because it cannot know what your machine supports. It requires that somebody decided and wrote it down.

## Credentials

By default the engine holds none. `models.call_mode = "delegated"` means it writes a request file, your agent answers it with whatever provider and keys you already have, and the engine reads the answer back. There is no credential store, no provider SDK, and no network call.

Setting `models.call_mode = "direct"` opts into the engine making model calls itself. It then reads `HARNESS_TUNER_API_KEY` from the environment. Nothing writes that value to disk, and it is not included in any artifact.

## What ends up in artifacts

Traces can contain whatever your harness passed to its tools, which may include file contents, queries, and customer data. Before sharing a run directory, check `trace.jsonl` and the per-task traces.

If tool arguments must never be stored, your adapter can omit `args` entirely and supply only `args_digest`. Loop and rework detection work from the digest alone, because they only ever needed to know whether two calls were the same, not what they said. The `redacted_args` conformance fixture demonstrates this.

## The safety pack

`packs/safety` describes hostile *content*, not hostile capability. Each task checks that a harness declines something or holds a boundary. Running it does not ask your harness to do anything harmful, and the tasks are written so the instruction sits inside data the harness reads rather than in the prompt, which is where the real version arrives.

If you run this pack in drive mode, do it under containment. A harness that fails these tasks is, by definition, one that did something you did not ask it to.

## Reporting a vulnerability

Open a private security advisory through the repository's Security tab. Please do not open a public issue for anything exploitable.

Useful things to include: what you were running, which mode, and what happened. A trace that reproduces it is ideal, with anything sensitive redacted or reduced to digests.
