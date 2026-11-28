#!/usr/bin/env python3
"""Offline data helpers imported by ci/base-refresh.py: strict JSON, hashes, OCI identity."""
import hashlib
import json
import os
from pathlib import Path
import re
import tarfile


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True) + '\n').encode()


def pairs(items):
    result = {}
    for key, value in items:
        require(key not in result, 'duplicate JSON field')
        result[key] = value
    return result


def decode(data):
    require(len(data) <= 16 * 1024 * 1024, 'oversized JSON')
    return json.loads(data, object_pairs_hook=pairs)


def hex_value(value, size=64):
    require(isinstance(value, str) and re.fullmatch('[0-9a-f]{' + str(size) + '}', value), 'invalid hash')
    return value


def digest(value):
    require(isinstance(value, str) and value.startswith('sha256:'), 'invalid image digest')
    hex_value(value[7:])
    return value


def read(path, limit=16 * 1024 * 1024):
    require(not path.is_symlink() and path.is_file() and path.stat().st_size <= limit, 'invalid data file: ' + str(path))
    return path.read_bytes()


def file_sha(path):
    require(not path.is_symlink() and path.is_file(), 'invalid artifact file')
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def image_identity(archive):
    """Inspect OCI headers/blobs as data; never unpack a rootfs or follow links."""
    with tarfile.open(archive, 'r:') as tar:
        members = {}
        for member in tar:
            name = member.name.removeprefix('./')
            if name in {'', '.'}:
                require(member.isdir(), 'unsafe OCI root entry')
                continue
            require(name not in members and not name.startswith('/') and '..' not in Path(name).parts, 'unsafe/duplicate OCI entry')
            require(member.isdir() or member.isfile(), 'OCI link/special entry')
            require(name in {'index.json', 'oci-layout', 'blobs', 'blobs/sha256'} or re.fullmatch('blobs/sha256/[0-9a-f]{64}', name), 'unexpected OCI entry')
            members[name] = member
            require(len(members) <= 10000, 'oversized OCI inventory')

        def blob(name, expected=None):
            require(name in members and members[name].isfile() and members[name].size <= 16 * 1024 * 1024, 'invalid OCI metadata')
            data = tar.extractfile(members[name]).read()
            if expected:
                require('sha256:' + sha(data) == expected, 'OCI metadata hash mismatch')
            return decode(data)

        require(blob('oci-layout') == {'imageLayoutVersion': '1.0.0'}, 'invalid OCI layout')
        index = blob('index.json')
        require(index.get('schemaVersion') == 2 and len(index.get('manifests', [])) == 1, 'one immutable image required')
        descriptor = index['manifests'][0]
        selected = digest(descriptor['digest'])
        require(members['blobs/sha256/' + selected[7:]].size == descriptor['size'], 'OCI descriptor size mismatch')
        manifest = blob('blobs/sha256/' + selected[7:], selected)
        # This one-machine image is deliberately single-platform. Refuse an
        # index instead of silently collapsing it during transport.
        require(manifest.get('mediaType') in {'application/vnd.oci.image.manifest.v1+json', 'application/vnd.docker.distribution.manifest.v2+json'}, 'multi-platform index HOLD')
        config_digest = digest(manifest['config']['digest'])
        config = blob('blobs/sha256/' + config_digest[7:], config_digest)
        require(config.get('architecture') == 'amd64' and config.get('os') == 'linux', 'wrong platform')
        for item in [manifest['config']] + manifest['layers']:
            value = digest(item['digest'])
            name = 'blobs/sha256/' + value[7:]
            require(name in members and members[name].isfile() and members[name].size == item['size'], 'missing OCI blob')
            stream = tar.extractfile(members[name])
            require('sha256:' + hashlib.file_digest(stream, 'sha256').hexdigest() == value, 'OCI blob hash mismatch')
        return {'manifestDigest': selected, 'indexDigest': None, 'platformDigest': selected, 'platform': 'linux/amd64'}


def output(values):
    with open(os.environ['GITHUB_OUTPUT'], 'a') as stream:
        for key, value in values.items():
            require(isinstance(key, str) and re.fullmatch('[a-z_][a-z0-9_]*', key), 'invalid output name')
            text = value if isinstance(value, str) else json.dumps(value, sort_keys=True, separators=(',', ':'))
            require('\n' not in text and '\r' not in text, 'output injection')
            stream.write(key + '=' + text + '\n')
