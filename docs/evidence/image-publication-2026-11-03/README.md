# Current reviewed image publication

This directory is the publication record consumed by `transfer-private-image`.
The previous source `ce5727eb` and its original access observations are preserved
byte-for-byte under [`previous-ce5727eb/`](previous-ce5727eb/).

The current source is merged demo-chat PR #10 (`1a57a5410a1d1a88d1884201407bcff8019e868d`),
which adds the authenticated WebSocket 1012 close API used by T12.
[Publication run 41656345415](https://github.com/Softbank-Hackathon-Finals/demo-chat/actions/runs/41656345415)
built tracked source once, scanned the OCI artifact, published those same bytes,
and pulled the immutable digest for the isolated runtime packaging probe.

- Source archive hash was independently recomputed from `git archive` and matched.
- Registry index bytes matched the recorded digest (excluding CLI newline framing).
- Trivy inspected Debian and Python packages: zero fixable HIGH/CRITICAL findings
  and zero secret findings under the existing gate.
- Runtime UID 10001, source revision, provenance/SBOM attestation and packaging probe passed.
- No policy or scan exception was added. This evidence does not establish a cluster
  rollout, actual WebSocket cutover, application DB readiness or target S2 completion.

The exact source and digest are in `image-receipt.json`. The new private ECR
transfer must run only after this record reaches protected `main`; its independent
transfer receipt will be retained by the owning operator. Existing images remain
available and no running application is changed by this record update.
