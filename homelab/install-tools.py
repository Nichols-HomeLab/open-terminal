#!/usr/bin/env python3
"""Install reviewed, checksum-locked amd64 tools during image build only."""
import hashlib
import json
import lzma
import os
from pathlib import Path
import shutil
import sys
import tarfile
import tempfile
import urllib.request


for tool in json.loads(Path('/opt/homelab/tools.lock.json').read_text()):
    if len(sys.argv) > 1 and tool['name'] not in sys.argv[1:]:
        continue
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / 'download'
        urllib.request.urlretrieve(tool['url'], archive)
        digest = hashlib.new(tool['algorithm'], archive.read_bytes()).hexdigest()
        if digest != tool['sha']:
            raise SystemExit(f"Checksum mismatch: {tool['name']}")
        destination = Path('/usr/local/bin') / tool['name']
        if tool['kind'] == 'raw':
            shutil.copyfile(archive, destination)
        elif tool['kind'] == 'xz':
            destination.write_bytes(lzma.decompress(archive.read_bytes()))
        elif tool['kind'] == 'tar':
            with tarfile.open(archive) as tar:
                destination.write_bytes(tar.extractfile(tool['member']).read())
        else:
            root = Path('/opt') / tool['name']
            root.mkdir(exist_ok=True)
            with tarfile.open(archive) as tar:
                tar.extractall(root, filter='data')
            if tool['name'] == 'node':
                source = root / tool['member'] / 'bin'
                for executable in source.iterdir():
                    (Path('/usr/local/bin') / executable.name).symlink_to(executable)
            else:
                destination.symlink_to(root / tool['name'])
        destination.chmod(0o755)
        print(f"Verified {tool['name']} {tool['version']}")
