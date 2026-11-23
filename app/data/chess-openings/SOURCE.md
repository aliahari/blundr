# Source

These files come from [lichess-org/chess-openings](https://github.com/lichess-org/chess-openings),
commit [`48a8c0e`](https://github.com/lichess-org/chess-openings/tree/48a8c0e733facfdc9a3723fe882e51ec28014074)
(2026-11-20): `a.tsv` to `e.tsv` (columns `eco`, `name`, `pgn`) and `COPYING.txt`, unmodified.

Licence: CC0 1.0 (public domain dedication), see `COPYING.txt`. The repo's README says: "As a
collection of facts, this data set is in the public domain."

Loaded into `opening_names` and `theory_moves` at startup by `app/services/opening_book.py`, which
rebuilds them whenever these files change. To update, replace the five TSVs with a newer commit's
files and update the commit above.
