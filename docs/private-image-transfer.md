# Private GHCR to private ECR

Keep the organization's package visibility policy unchanged. The
`transfer-private-image` workflow reads GHCR with its job-scoped `GITHUB_TOKEN`
and obtains an AWS publisher session through OIDC for 900 seconds. It creates
no long-lived registry key or shared Kubernetes pull secret.

The separate platform `infra/envs/private-registry` state owns one private ECR
repository and the publisher role. The foundation owns node/host permissions.
The workflow runs only on protected `main`. Its role trust also binds the
verified immutable organization/repository IDs and exact main branch.

## Configure and run

Store the following operator-private **identifiers**, not AWS access keys, in
repository Actions secrets so public logs mask them:

- `PRIVATE_IMAGE_ACCOUNT_ID`: the reviewed target account.
- `PRIVATE_IMAGE_PUBLISH_ROLE_ARN`: the dedicated publisher role.
- `PRIVATE_IMAGE_REPOSITORY_URI`: the exact private ECR `bravo/demo-chat` URI.

Run the workflow from `main` with `source_sha` and `source_digest` matching
the reviewed publication record. This first transfer is bound to the committed
`docs/evidence/image-publication-2026-11-03/` receipt, scan summary and registry
index from main. Future publications need a reviewed record update; arbitrary
input images or unrecorded digests are rejected.

1. Verify successful source scan/pull/runtime/provenance evidence and that the
   recorded full source SHA belongs to main. Archive only its tracked files.
2. Download the GHCR index and all child manifests/blobs with digest preservation.
   Hash every blob and resolve every referenced descriptor, including attestations.
3. Scan these bytes with the existing HIGH/CRITICAL fixable-vulnerability and
   secret gate. Only then request the AWS session and registry authentication.
4. Copy with `--all --preserve-digests`. An existing identical immutable tag is
   reused; a different digest at that tag fails without changing it.
5. Download all manifests/blobs from private ECR and compare inventories and
   hashes. Pull the exact ECR digest and run the image's packaging probe with
   networking disabled. No application database or cloud-service calls occur.

Artifacts contain only source/digest, bounded checks and source-file hashes.
They exclude private account/registry identifiers, Docker auth configuration,
AWS credentials and raw CLI errors. Login actions clean up their temporary
authentication when the job ends. The transferred digest stays identical to
the original GHCR digest, including the OCI provenance/SBOM manifests.

## Consumers and verification boundaries

EKS node credentials provide ECR pull authorization independently of application
IRSA. The existing EKS node ECR pull policy is retained. RKE2's foundation owner
adds exact-repository read permissions; its kubelet also needs an ECR credential
provider that obtains temporary credentials from the EC2 instance role.

A successful CI mirror/pull proves registry delivery, not Kubernetes scheduling,
credential refresh, application DB access or cutover readiness. Record actual
EKS and RKE2 pulls separately. A manual one-time registry login is not a durable
kubelet authentication configuration. Non-AWS destination clusters need their
own scoped private-access arrangement.

References: [AWS ECR authentication](https://docs.aws.amazon.com/AmazonECR/latest/userguide/registry_auth.html),
[OCI manifest support](https://docs.aws.amazon.com/AmazonECR/latest/userguide/image-manifest-formats.html),
[GitHub OIDC](https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-aws).
