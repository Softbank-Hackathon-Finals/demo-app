# Current reviewed image publication

This directory is the publication record consumed by `transfer-private-image`.
The preceding record remains byte-for-byte under
[`previous-66e7419f/`](previous-66e7419f/); earlier records remain under
`previous-1a57a541/` and `previous-ce5727eb/`.

The current source is `8d82a9241d3894737778a77d58349e1aee54eca1`, including merged
PR #15 (attachment upload freeze) and #17 (immutable attachment retries).
It includes the environment response header needed by T18 and the existing 1012
WebSocket management API. The intentional Secrets Manager startup dependency remains.
[Publication run 41803312129](https://github.com/Softbank-Hackathon-Finals/demo-chat/actions/runs/41803312129)
completed successfully on that exact source. The workflow built tracked source once,
scanned the OCI artifact, copied the same bytes, and pulled the immutable digest for
its isolated runtime packaging probe.

- `git archive` SHA-256 was independently recomputed and matches `source-manifest.json`.
- Registry index bytes match the recorded digest; each tracked source file matches
  the exact source revision.
- Trivy reports zero fixable HIGH/CRITICAL vulnerabilities and zero secret findings
  under the existing policy (`ignore_unfixed: true`). This is not a claim of zero
  vulnerabilities of every severity or a lifetime security guarantee.
- UID10001, source revision, attestation manifest and runtime packaging probe passed.
- Private ECR transfer, destination cluster pull/start, TLS/DB/Secret access, live
  attachment delivery, T12 cutover and T15/T31 approved transformation remain separate.
  No running app or policy is changed by this publication record.

After the record reaches protected `main`, the existing main-only transfer workflow
can copy this exact source and index digest without rebuilding. Retain its independent
transfer receipt. A successful registry copy alone does not authorize a deployment or
prove AWS independence. No long-lived pull key or registry visibility change is added.
