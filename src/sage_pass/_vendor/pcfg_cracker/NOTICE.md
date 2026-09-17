# pcfg_cracker attribution

This directory contains the minimal guesser-library subset of
[`lakiw/pcfg_cracker`](https://github.com/lakiw/pcfg_cracker), retrieved from
the `master` branch on 2026-09-16. The upstream command-line entry point states
PCFG Guesser version 4.6 and carries the MIT license reproduced in `LICENSE`.
The downloaded source archive SHA-256 was
`84EA7EDA106A8AB46F2EDAD8711C23EE7A4D4DD2E34D8D8A148E806B4D1D3898`.

SAGE uses the upstream ruleset loader, parse-tree implementation and
probability priority queue, plus its OMEN Markov loader/enumerator modules.
OMEN is exposed as a separate generator rather than being mixed into
`pcfg_full`. SAGE does not redistribute the upstream `Rules` directory or any
training corpus/model derived from RockYou.
