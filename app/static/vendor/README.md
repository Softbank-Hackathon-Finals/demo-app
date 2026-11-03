# QR module

`qrcode.mjs` is the unmodified distribution from `qrcode-generator` **2.0.4** (MIT).
The browser generates the QR locally; no address is sent to an external QR service.

- Upstream: https://github.com/kazuhikoarase/qrcode-generator
- Release source revision: `83b7e8fe3fddd3b0368dbafd6ce56995bd25e3c8`
- License: upstream `/LICENSE` at that revision, preserved in `qrcode-LICENSE.txt`
- npm archive integrity is pinned in `pnpm-lock.yaml`.
- `pnpm build` verifies this module byte-for-byte against the locked package and checks the license hash.
- To intentionally update the vendor artifact: review the locked version/license, then run `node scripts/qr-asset.mjs` and review the diff.
