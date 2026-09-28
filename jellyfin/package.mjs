import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';

const root = resolve(import.meta.dirname);
const dotnet = process.env.DOTNET ?? 'dotnet';
const artifacts = resolve(root, 'artifacts');
const project = resolve(root, 'Sesn.Jellyfin/Sesn.Jellyfin.csproj');
const icon = resolve(root, 'icon-512.png');
const timestamp = new Date().toISOString();
const builds = [
  { jellyfin: '10.11.11', framework: 'net9.0', extensions: '9.0.11', version: '0.1.11.0', abi: '10.11.0.0' },
  { jellyfin: '12.0.0', framework: 'net10.0', extensions: '10.0.11', version: '0.2.0.0', abi: '12.0.0.0' },
  { jellyfin: '12.1.0', framework: 'net10.0', extensions: '10.0.11', version: '0.2.1.0', abi: '12.1.0.0' },
];

mkdirSync(artifacts, { recursive: true });
const versions = [];
for (const build of builds) {
  const buildRoot = resolve(artifacts, `.build-${build.version}`);
  rmSync(buildRoot, { recursive: true, force: true });
  mkdirSync(buildRoot, { recursive: true });
  execFileSync(dotnet, [
    'build', project, '-c', 'Release',
    `-p:SesnTargetFramework=${build.framework}`,
    `-p:JellyfinVersion=${build.jellyfin}`,
    `-p:ExtensionsVersion=${build.extensions}`,
    `-p:Version=${build.version}`,
    `-p:AssemblyVersion=${build.version}`,
    `-p:FileVersion=${build.version}`,
  ], { stdio: 'inherit' });

  const dll = resolve(root, `Sesn.Jellyfin/bin/Release/${build.framework}/Sesn.Jellyfin.dll`);
  const meta = resolve(buildRoot, 'meta.json');
  writeFileSync(meta, `${JSON.stringify({
    category: 'General',
    changelog: 'Secure pairing, explicit viewer mapping, playback lifecycle tracking, and credential revocation.',
    description: 'Securely sync Jellyfin playback and completed watches with Sesn. Other Jellyfin users are ignored unless explicitly mapped.',
    guid: 'd214481e-2ec6-4b17-91db-b45b705a06aa',
    name: 'Sesn',
    overview: 'Track Jellyfin playback on Sesn.',
    owner: 'Object64',
    targetAbi: build.abi,
    timestamp,
    version: build.version,
    status: 0,
    autoUpdate: false,
    imagePath: 'icon-512.png',
    assemblies: ['Sesn.Jellyfin.dll'],
  }, null, 2)}\n`);

  const filename = `sesn-jellyfin_${build.version}_jellyfin-${build.jellyfin}.zip`;
  const zip = resolve(artifacts, filename);
  rmSync(zip, { force: true });
  execFileSync('zip', ['-j', '-9', zip, dll, meta, icon], { stdio: 'inherit' });
  const checksum = createHash('md5').update(readFileSync(zip)).digest('hex');
  versions.push({
    version: build.version,
    changelog: `Native build for Jellyfin ${build.jellyfin}.`,
    targetAbi: build.abi,
    sourceUrl: `https://sesn.io/downloads/jellyfin/${filename}`,
    checksum,
    timestamp,
  });
  rmSync(buildRoot, { recursive: true, force: true });
  console.log(`Built ${zip} (${checksum})`);
}

const manifest = [{
  guid: 'd214481e-2ec6-4b17-91db-b45b705a06aa',
  name: 'Sesn',
  description: 'Securely sync Jellyfin playback and completed watches with Sesn.',
  overview: 'Track Jellyfin playback on Sesn.',
  owner: 'Object64',
  category: 'General',
  imageUrl: 'https://sesn.io/icon-512.png',
  versions: versions.reverse(),
}];
writeFileSync(resolve(artifacts, 'manifest.json'), `${JSON.stringify(manifest, null, 2)}\n`);
