"""Getting traces out of a harness.

Two ways in, negotiated at setup rather than prescribed:

- :mod:`.observer` reads traces the harness already produced. It invokes
  nothing, costs nothing extra, measures real usage, and is the only option for
  a harness with no scriptable entry point.
- :mod:`.driver` invokes the harness on a fixed task set. It is what repeated
  seeded trials, the e-process, and the verify command require.
"""
