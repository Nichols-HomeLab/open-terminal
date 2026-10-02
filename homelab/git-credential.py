#!/usr/bin/env python3
"""Supply the mounted GitHub credential only to GitHub HTTPS Git requests."""
import pathlib
import sys

if len(sys.argv) > 1 and sys.argv[1] == 'get':
    request = dict(line.rstrip('\n').split('=', 1) for line in sys.stdin if '=' in line)
    if request.get('protocol') == 'https' and request.get('host') == 'github.com':
        token = pathlib.Path('/run/secrets/homelab/github-token').read_text().strip()
        print('username=x-access-token')
        print('password=' + token)
