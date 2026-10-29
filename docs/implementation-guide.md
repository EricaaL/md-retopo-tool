 Data-driven is the base: behaviour comes from declared data like manifests, specs, schemas, registries and config. Event-driven is used only at the seams, where parts must stay unaware of each other. Main execution paths are not event-driven. They dispatch by id through a registry, or run a planned graph or pipeline.

Below are the generic principles, drawn from the project rules plus all the OpenCat skills.

Core style
1. Declare it, don't code it
- Capabilities, permissions, routes, themes, model choices and pipeline steps are written as data: manifests, specs, JSON or YAML.
- Code reads that data. Adding a tier, theme or role means editing data, not writing new logic.
- Code holds the algorithm. Numbers, thresholds, colors, wording and prompts live in config or templates.

2. Each fact has one owner
- Every vocabulary or contract lives in exactly one file or registry. Everything else reads, generates or mirrors from it.
- Generated files are never edited by hand. A drift test fails CI when a mirror falls out of sync.

3. Registries instead of branching
- A new provider, engine, theme or tool means registering a new module. Don't add an if/elif or switch case.
- Lookup is by a stable identity key, like (owner, operation).
- A search index is only for discovery. The registry in memory is the live truth.

4. Contracts first, changes additive
- Change the schema or interface before the code.
- Never rename existing fields. Add alias keys or an adapter step instead.
- Every input gets validated against a schema. Don't trust validation done on the client.

5. Events only to decouple
- Use typed events (schema models, not loose dicts) for cross-module signals, lifecycle hooks and fire-and-forget UI updates.
- Never use events to read state back. When the caller needs the answer, make a direct call.
- Outbound delivery is at-least-once: a cursor advances only after the receiver confirms. Writes are idempotent through a client
  request id, so retries are safe.

Boundaries

6. Hard module boundaries, enforced by tests
- Siblings never import each other. They talk through dispatch by id, a service locator, or events.
- Extensions never reach into core internals. They use narrow interfaces.
- Import-boundary tests in CI fail any bypass.

7. Pure core, side effects at the edge
- Domain logic is pure functions over (state, time, randomness).
- I/O, HTTP and storage happen in one wiring layer.
- Only one component makes outbound calls. Data access stays in a store or repository layer, never in a request handler.

8. Lifecycle phases
- Load phase: declare and register only, no I/O.
- Ready phase: connect and warm caches.
- Unload phase: always clean up, even when it fails.
- Background loops build on one base worker plus a registry. Never hand-roll a loop.
- Hot reload works by unloading and loading again.

Safety

9. Deny by default, fail closed
- Permission checks run as an ordered rule chain where the first decisive rule wins, and the default is deny.
- Turning enforcement fully off needs two separate keys.
- An invalid spec is rejected when written. When a stored spec no longer parses on read, it is skipped and logged, never crashing
  the system.

10. Outside input is data, not instructions
- User input, scraped pages, LLM output and browser scores are untrusted. Validate, clamp and sanitize them.
- Outbound URLs get checked for SSRF, including on redirect.
- Every query filters by tenant.
- Secrets never go into git, logs or output, and URLs that carry tokens are redacted.

11. LLM is a narrow tool
- The model decides or extracts within a typed output. The server owns the prompts.
- Facts come only from trusted sources. Rephrase what the source says; never invent.
- A confidence gate decides whether to run, confirm or clarify. Long or risky plans need human confirmation.
- Bulk writes take two steps: a preview first, then the confirmed run.

12. Cost and resource limits
- Charge before any paid provider call. A cache hit costs nothing.
- Cache keys use a frozen formula.
- Fan-out is capped by a semaphore, loops by a max-iteration count, retries and replans by a budget.
- Async code never blocks: blocking calls go to a thread, and fire-and-forget tasks are supervised.

Reliability and process

13. Errors become values at the boundary
- One wrapper turns failures into structured error results with context. Nothing raw leaks to the client.
- Recoverable errors get one retry, then a replan that remembers the failure.

14. State has one owner layer
- Server data, shareable URL state, device preferences and transient UI state each live in one layer, never copied across.
- Saves and migrations carry a version, give new fields a default, and keep history.
- User data and system files stay apart, so updates never overwrite personal changes.

15. Rules backed by tests, docs agents read
- Every rule has a test, a CI check or a generator behind it. A rule that is only written down fades.
- Agent guidance lives in files loaded automatically and gets updated after each structural change.
- Verification needs evidence. A check that couldn't run is reported as blocked, never as a pass.

In short: data declares what exists, registries resolve it, contracts guard it, events decouple it, tests enforce it, and outside
input and LLMs are never trusted.