# Reference design: isolated batch

Use when the same bounded contract applies to many independent items and the workflow needs
concurrency control, per-item retry, and a durable partial-result boundary.

## Shape

```text
enumerate → validate_manifest → bounded_map(item_worker) → per_item_check → deterministic_reduce
                                                                      ├─complete→ success
                                                                      └─partial→ checkpoint
```

## Manifest and isolation

Each item has a stable id, input pointer, output namespace, attempt counter, and status. Validate
duplicates and missing inputs before fan-out. Workers may not write shared aggregation files.

## Per-item behavior

- Retry only the failed item and only with new evidence.
- Cap attempts per item and for the batch as a whole.
- Record `accepted`, `failed`, `unresolved`, and `skipped` separately.
- If order matters, the reducer sorts by manifest order rather than completion time.
- The reducer rejects duplicate outputs and unexpected item ids.

## Concurrency

Pin concurrency from declared CPU/GPU/API limits in `resources.md`. Avoid launching enough workers
to make every item slower or to trigger service throttling. Backoff belongs in the tool wrapper,
not in independent model prompts.

## Completion

State whether acceptance requires all items or permits an explicit partial threshold. A global
timeout with unprocessed items is `partial/checkpoint`, never success by omission. Preserve the
manifest and frontier so a new bounded attempt can reconcile existing outputs.
