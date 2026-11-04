# Current reviewed image publication

This directory is the publication record consumed by `transfer-private-image`.
Previous JSON records are preserved byte-for-byte under
[`previous-1a57a541/`](previous-1a57a541/) and [`previous-ce5727eb/`](previous-ce5727eb/).

The current source is merged demo-chat PR #12 (`66e7419f404f4ec4addc71014aabfcdd44f34688`),
which keeps one WebSocket close operation across timeout retries and concurrent
requests. The source main CI and [publication run 41663726176](https://github.com/Softbank-Hackathon-Finals/demo-chat/actions/runs/41663726176) passed.
The workflow built tracked source once, scanned that OCI artifact, published those
same bytes, and pulled the immutable digest for the isolated runtime packaging probe.

- Source archive hash was independently recomputed from `git archive` and matched.
- Registry index bytes matched the recorded digest (excluding CLI newline framing).
- Trivy inspected Debian and Python packages: zero fixable HIGH/CRITICAL findings
  and zero secret findings under the existing gate.
- Runtime UID 10001, source revision, bound attestation and packaging probe passed.
- No policy or scan exception was added. This record does not establish a cluster
  rollout, actual WebSocket cutover, application DB readiness or target S2 completion.

The exact source and digest are in `image-receipt.json`. Private ECR transfer runs
only after this record reaches protected `main`; its independent transfer receipt
is retained by the owning operator. The previous successful image and its evidence
remain available. This record update does not change a running application.
