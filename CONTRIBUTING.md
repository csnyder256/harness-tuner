# Contributing

Thanks for helping. harness-tuner is a measurement tool, so the bar for changes is a little unusual: the question is never only "does this work", it is "how would we know if it stopped working".

## Good contributions

- **An adapter for a harness nobody has covered.** The most valuable thing here. Include a short note on what that harness exposes, which adapter level it reaches, and which metrics it cannot support, so somebody evaluating it knows before they run.
- **An evaluation pack for a domain the shipped ones miss.** Packs are TOML and need no code. Declare `requires` honestly, so a harness that cannot run a task is skipped rather than failed.
- **A metric definition, with a conformance fixture that pins it.** A metric without a fixture is a metric nobody can reimplement. See below.
- **A case where a finding was wrong**, with the trace that proves it. This is worth more than a feature.
- **A correction.** If a claim in the README or the guide is wrong or has gone stale, open an issue.

## Ground rules

**The core imports the standard library and nothing else.** CI enforces this by walking every import in `harness_tuner/`. The one exception is `harness_tuner/score/direct.py`, the optional direct-model-call path, and even that must degrade to a clear message when unavailable rather than breaking the install. If a change needs a dependency, it belongs in a separate tool that reads the artifacts.

**Unobserved is `null`, never zero.** This is the rule the whole project rests on. A metric that cannot be computed returns `unavailable` with a reason a human can act on. A metric that is undefined rather than unobserved also returns `unavailable` and says which. If you find yourself writing `or 0`, stop and look at what you are hiding.

**Detection is arithmetic, not judgement.** A new finding must be computable from trace records and must carry the exact steps it came from. The diagnostician model explains findings and names where in a harness to change something; it does not decide whether a finding is real.

**Proposals predict a direction, never a number.** "Expected gain: 12%" is unfalsifiable. A bound computed from the observed trace is not, and is more useful.

**No measurement of any harness or model gets published in this repository.** Not in the README, not in the docs, not as an example, not as an illustration. Every number this tool produces belongs to whoever ran it.

**No em-dashes.** CI checks by raw bytes, because `grep -P` for the codepoint fails silently when the locale is not UTF-8 and reads exactly like success.

## Adding a metric

1. Write the function in `harness_tuner/protocol/metrics.py`. Return `measured(...)` or `unavailable(reason, unit)`; there is no third option.
2. Add it to `REGISTRY`, to `AGGREGATION` with an explicit combiner, and to `LOWER_IS_BETTER` if smaller is better. A missing combiner fails the test suite, and this is deliberate: combiners used to be inferred from the unit string, which quietly summed per-task maxima into numbers that described no run that ever happened.
3. Add or extend a fixture in `conformance/fixtures/` that exercises it, including the case where it is unavailable.
4. Regenerate the expected values with `python -m harness_tuner conformance --write-expected`, then **read the diff**. That command makes the implementation the definition of correct, so it is only safe when you have checked the numbers by hand.
5. Add a test in `tests/` asserting the value you worked out yourself. The conformance suite compares the implementation against files the implementation generated, which proves nothing on its own; the hand-computed tests are what break that circle.

## Adding a setting

Every setting must have a consumer outside `harness_tuner/config`. Run:

```bash
python tools/check_config_wiring.py
```

The first time this ran against this project it found twelve of twenty-nine settings that were accepted, type-checked, validated against their legal values, documented, written into the run artifacts, and read by absolutely nothing. Each one looked finished from every angle except the one that mattered. Either wire your setting to something that acts on it, or delete it.

## Running everything

```bash
python -m pytest tests/ -q
python -m harness_tuner conformance
python tools/check_config_wiring.py
```

All three run in CI, on three operating systems and three Python versions, along with a container build that runs the conformance suite inside the image.
