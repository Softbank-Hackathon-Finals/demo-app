# Unmerged candidate image publication

`publish-candidate-image` accepts an authorized, unmerged `demo-chat` pull request.
The existing main-only publication and reviewed-image transfer paths stay intact.
Run this workflow **from protected main** after agreeing on the candidate with its
owner. Dispatch permission remains the repository's Actions write permission;
neither the AI candidate nor T15/T31 receives registry publisher credentials.

Supply the PR number, full base and source commit IDs, request ID and the SHA-256
of these exact bytes (no shell substitution containing credentials):

```sh
git diff --binary --full-index --no-ext-diff --no-renames BASE_SHA SOURCE_SHA --
```

The base must be an ancestor of main and of the candidate. The current fetched
PR head must match the requested source, which must not already be on main.
Allowed changes are regular files under `app/` and `pyproject.toml`, `uv.lock`,
`.python-version`. Workflow, Dockerfile, scripts, hidden files, credential paths,
symlinks and submodules fail closed. A candidate requiring a control-file change
needs a separate reviewed main PR first. Only the pinned candidate app/dependency
blobs and the trusted workflow revision's Dockerfile/.dockerignore enter the build.

The three jobs preserve these boundaries:

1. **Build:** contents-read permission only, no registry login or AWS session.
   Scan exported source/diff for secrets, build one linux/amd64 OCI index with
   provenance/SBOM, scan HIGH/CRITICAL fixable vulnerabilities and secrets, then run
   the trusted packaging probe in a network-disabled, read-only, non-root container
   without host mounts. The immutable Actions artifact binds repository, PR,
   base/source/workflow SHA, diff SHA, request and run/attempt to the image digest.
2. **GHCR:** fresh runner checks the exact run's artifact ID, receipt binding and
   every OCI blob/descriptor, rescans, and requires the existing package to be
   private. It never executes the candidate image. Copy and independent download
   must preserve the index, child manifests, layers and attestations exactly.
3. **ECR:** another fresh runner repeats verification/scanning before requesting
   the existing 900-second main-bound OIDC publisher role. It verifies the private
   GHCR download and copies without rebuilding to the exact private ECR repository.
   An identical immutable tag is reused; a conflicting tag fails closed. The final
   download must match all source bytes.

Both registries use `candidate-RUN_ID-ATTEMPT`. Consumers take the receipt's full
digest: T15 rehearses it and T31 deploys that same digest, without rebuilding or
merging the candidate to bypass this path. Registry delivery alone does not prove
T15 rehearsal or T31 deployment; their receipt fields remain false until their
respective owners supply execution evidence. Source/diff artifacts have a 14-day
retention period and contain no authentication files or raw scanner findings.

The exporter uses an [OCI directory](https://docs.docker.com/build/exporters/oci-docker/)
and [Trivy OCI image scanning](https://github.com/aquasecurity/trivy/blob/main/docs/guide/target/container_image.md).
Attestations retain the existing image-manifest representation (`oci-artifact=false`)
so the same digest contract and descriptor verification apply to GHCR and ECR.
