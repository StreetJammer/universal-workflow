# Domain routing

Load only the relevant profile. Discover and use installed specialist skills; this coordinator does not replace an engine, browser, renderer, broker, or data source. Retain the user's existing stack. If the work spans profiles, combine only the relevant acceptance checks.

| Profile | Inspect first | Evidence of completion |
| --- | --- | --- |
| web | Existing design, routes, framework, data ownership, auth, deployment target | Build/types where applicable; actual important user journeys; responsive layouts; keyboard/focus; empty, loading, and failure states; console/network errors; visual inspection |
| game | Engine/version, target device, project structure, gameplay loop, asset pipeline | Compile/import; play the core loop; controls, collisions, win/lose/restart, save/load if relevant; target-device performance; build/package proof |
| software | Reproduction, architecture, API contracts, callers, dependencies | Relevant behavioral tests, error paths, compatibility, actual execution; regressions outside the edited function |
| research | Precise question, date sensitivity, source access, evidence standards | Primary-source verification, dates, claim/source mapping, reproducible calculations, explicit uncertainty and conflicting evidence |
| documents | Audience, format, source material, template, output requirements | Content accuracy, rendered page/slide/sheet inspection, formulas where relevant, usable final file |
| operations | Inputs, destination, permissions, recurrence, failure recovery | Dry run or isolated fixture, idempotency, duplicate prevention, error recovery, observable completion and bounded retries |
| general | Actual deliverable, constraints, available tools | Direct evidence that each acceptance criterion is met |

## Websites

Use the existing framework and design system. Resolve concrete interactions, content, accessibility, data states, and device layouts before declaring a page finished. A build does not prove navigation, forms, authentication, or a purchase flow works. Only use production credentials or publish when authorized. Use test data and reversible environments for evaluation. Do not memorize live credentials, private customer records, or rapidly changing prices as durable facts.

## Games

Use Unity, Godot, or the existing engine through its available integration. Separate mechanics, presentation, assets, and build acceptance. For a platformer, test grounded movement, jumping, collision, death/restart, and a full level traversal. For a desktop pet, verify dragging, input pass-through, animation transitions, window behavior, and idle resource use on the target OS. Frame-rate and latency claims require measured runs. Generated images are assets, not proof of an engine-ready rig or working animation. Record missing engine/build access as a verification gap.

## Software and APIs

Reproduce the issue and inspect contracts before editing. Keep tests focused on externally meaningful behavior. Verify dependency/API details against current authoritative documentation when uncertain. Do not convert a workaround into a universal rule without checking the failure's cause and environment. Store versions and provenance with tool/configuration lessons.

## Research and documents

Retrieve original sources, distinguish supplied claims from independently verified facts, and retain a concise source trail. Use up-to-date sources for changing information. Render created artifacts and inspect layout. Route regulated or high-stakes questions to appropriate evidence and human judgment; this skill grants no expertise, financial authority, or permission to act on an account.

## Operations and scheduled work

Specify trigger, scope, timezone, input, destination, deduplication, retry budget, stop condition, and success notification only when relevant. Confirm actual scheduler registration before saying a task is active. A recipe or an executable worker is not a running service. Preserve existing authorization; ask only for a genuinely missing scope or consequence. Keep untrusted input separate from commands and use argument arrays instead of shell interpolation.
