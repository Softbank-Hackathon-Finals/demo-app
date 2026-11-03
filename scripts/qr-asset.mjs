// Keep a local, unmodified upstream module: the browser never calls a QR service.
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { createHash } from 'node:crypto';
const assets = [
  ['node_modules/qrcode-generator/dist/qrcode.mjs', 'app/static/vendor/qrcode.mjs'],
];
await mkdir('app/static/vendor', { recursive: true });
for (const [source, target] of assets) {
  const data = await readFile(source);
  if (process.argv.includes('--check')) {
    if (!data.equals(await readFile(target))) throw new Error(`Vendored QR artifact differs: ${target}`);
  } else await writeFile(target, data);
}
const license = await readFile('app/static/vendor/qrcode-LICENSE.txt');
if (createHash('sha256').update(license).digest('hex') !==
    '3a850fa5f08101db6f40676c2786e10bd2cd5fff7b12ffdf1e0c434d4e49d90c') {
  throw new Error('QR upstream license differs');
}
