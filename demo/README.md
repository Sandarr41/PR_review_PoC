# Demo checkout

A tiny local "repo checkout" used for `README.md`'s demo command. Contains
the post-PR state of the two files touched in
[`tests/fixtures/sample.diff`](../tests/fixtures/sample.diff), so the Tool
Integration Layer and Context Retriever have real files to work against.

`user_service.py` has a deliberately seeded SQL-injection bug and
`utils.py` a deliberately seeded `None`-attribute bug — the same example
used in `docs/product-proposal.md`.
