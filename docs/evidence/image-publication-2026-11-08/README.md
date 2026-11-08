# Reviewed publication candidate · 2026-11-08

Source: `f47d4dd247a7dde6138404b25e63f7891af61282` on demo-app main.
[Official publication run](https://github.com/Softbank-Hackathon-Finals/demo-app/actions/runs/42441852331) succeeded. The tracked git archive SHA-256 was independently recomputed, the registry index matches the recorded digest, and the bounded Trivy report contains zero findings under the existing fixable HIGH/CRITICAL + secret policy. This does not claim zero vulnerabilities of all severities.

The workflow verified UID10001, source revision, attestations, immutable registry pull and runtime packaging. Its original artifacts are retained for 14 days. The raw report hash is retained in scan-summary.json; no credentials or source-provided log content is included here.

The original [2026-11-03 record](../image-publication-2026-11-03/) remains the default. Existing consumers are unchanged. Only after this record and the optional selector reach protected main may the transfer workflow select this directory explicitly. Source SHA, digest, scan, exact destination, temporary role, OCI blob and runtime verification remain mandatory.

The earlier [transfer attempt](https://github.com/Softbank-Hackathon-Finals/demo-app/actions/runs/42442264399) stopped at reviewed_publication_mismatch because it used the legacy default. It did not copy this image or authorize a rollout. AWS transfer, destination pull, DB/Secret/TLS access, workload rollout and migration are separate acceptance steps. No cluster, application, DB, registry visibility or IAM policy is changed by this record.
