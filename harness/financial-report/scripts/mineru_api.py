"""MinerU online precise API adapter. Credentials never go to upload/CDN hosts."""
from __future__ import annotations

import ipaddress
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import stat
import time
from urllib.parse import urlparse, quote, urljoin
import zipfile

import requests

API_BASE = 'https://mineru.net/api/v4'


class MinerUError(RuntimeError):
    pass


class NoNetrcAuth(requests.auth.AuthBase):
    def __call__(self, request):
        return request


def public_https(url):
    parsed = urlparse(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise MinerUError('Service returned an invalid HTTPS transfer URL')
    if parsed.hostname.lower() in {'localhost', 'localhost.localdomain'}:
        raise MinerUError('Local transfer URLs are not allowed')
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        address = None
    if address and not address.is_global:
        raise MinerUError('Private IP transfer URLs are not allowed')
    return url


class MinerUClient:
    def __init__(self, token=None, session=None, sleep=time.sleep, clock=time.monotonic):
        self.token = (token if token is not None else os.getenv('MINERU_API_TOKEN', '')).strip()
        if not self.token or self.token == 'YOUR_MINERU_API_TOKEN':
            raise MinerUError('MINERU_API_TOKEN is missing. Configure a MinerU token, not a Claude API key.')
        self.session = session or requests.Session()
        self.sleep, self.clock = sleep, clock

    def safe_message(self, message):
        text = str(message).replace(self.token, '[REDACTED]')
        text = re.sub(r'https?://\S+', '[URL]', text)
        return re.sub(r'(?i)Bearer\s+\S+', 'Bearer [REDACTED]', text)[:400]

    def api(self, method, path, body=None, timeout=60):
        attempts = 3 if method == 'GET' else 1
        for attempt in range(attempts):
            try:
                response = self.session.request(method, API_BASE + path,
                    headers={'Authorization': f'Bearer {self.token}', 'Content-Type': 'application/json'},
                    json=body, timeout=timeout, allow_redirects=False, auth=NoNetrcAuth())
                if response.status_code in {429, 500, 502, 503, 504} and attempt + 1 < attempts:
                    response.close()
                    self.sleep(min(2 ** attempt, 4))
                    continue
                if response.status_code in {401, 403}:
                    raise MinerUError('MinerU rejected the token (HTTP 401/403). Check its validity and permissions.')
                if response.status_code != 200:
                    raise MinerUError(f'MinerU API HTTP {response.status_code}; no request credentials were logged')
                payload = response.json()
                if not isinstance(payload, dict):
                    raise MinerUError('MinerU API returned a non-object envelope')
                if payload.get('code') != 0:
                    raise MinerUError(f'MinerU API code {self.safe_message(payload.get("code"))}: {self.safe_message(payload.get("msg", "request failed"))}')
                if not isinstance(payload.get('data'), dict):
                    raise MinerUError('MinerU API returned an invalid data envelope')
                return payload['data']
            except requests.RequestException as exc:
                if method == 'GET' and attempt + 1 < attempts:
                    self.sleep(min(2 ** attempt, 4))
                    continue
                raise MinerUError(f'MinerU network failure ({type(exc).__name__}); submission was not automatically repeated') from None
            except ValueError:
                raise MinerUError('MinerU returned invalid JSON') from None
            finally:
                if 'response' in locals():
                    response.close()

    def create_upload(self, name, data_id, model='vlm'):
        data = self.api('POST', '/file-urls/batch', {
            'files': [{'name': name, 'data_id': data_id}],
            'model_version': model, 'enable_formula': False, 'enable_table': True, 'language': 'ch',
        })
        batch_id = data.get('batch_id')
        urls = data.get('file_urls', [])
        if not isinstance(batch_id, str) or not batch_id or len(urls) != 1:
            raise MinerUError('Invalid upload allocation response')
        return batch_id, public_https(urls[0])

    def upload(self, url, pdf):
        try:
            with Path(pdf).open('rb') as stream:
                response = self.session.request('PUT', public_https(url), data=stream,
                    headers={}, timeout=(30, 180), allow_redirects=False, auth=NoNetrcAuth())
            try:
                if response.status_code not in {200, 201, 204}:
                    raise MinerUError(f'MinerU file upload failed (HTTP {response.status_code})')
            finally:
                response.close()
        except requests.RequestException as exc:
            raise MinerUError(f'File upload network failure ({type(exc).__name__}); use --resume to inspect the existing batch') from None

    def wait_result(self, batch_id, name, data_id, timeout=900, interval=5):
        deadline = self.clock() + timeout
        while self.clock() < deadline:
            data = self.api('GET', '/extract-results/batch/' + quote(batch_id, safe=''),
                            timeout=max(1, min(30, deadline - self.clock())))
            results = data.get('extract_result', [])
            matches = [r for r in results if r.get('data_id') == data_id]
            if not matches:
                matches = [r for r in results if r.get('file_name') == name and r.get('data_id') in (None, '')]
            if len(matches) > 1:
                raise MinerUError('Ambiguous batch result; refusing to select another document')
            if matches:
                result = matches[0]
                state = result.get('state')
                if state == 'done':
                    return public_https(result.get('full_zip_url', ''))
                if state == 'failed':
                    raise MinerUError('MinerU task failed: ' + self.safe_message(result.get('err_msg', 'unknown reason')))
                if state not in {'waiting-file', 'pending', 'running', 'converting'}:
                    raise MinerUError(f'Unknown MinerU task state: {self.safe_message(state)}')
            remaining = deadline - self.clock()
            if remaining > 0:
                self.sleep(min(interval, remaining))
        raise MinerUError('MinerU polling timed out. Resume the same run with --resume; do not create duplicate submissions.')

    def download(self, url, target, max_bytes=512 * 1024 * 1024):
        target = Path(target)
        part = target.with_suffix(target.suffix + '.partial')
        try:
            for _ in range(4):
                response = self.session.request('GET', public_https(url), headers={}, stream=True,
                    timeout=(30, 90), allow_redirects=False, auth=NoNetrcAuth())
                if response.status_code in {301, 302, 303, 307, 308}:
                    url = urljoin(url, response.headers.get('Location', ''))
                    response.close()
                    continue
                try:
                    if response.status_code != 200:
                        raise MinerUError(f'Result download failed (HTTP {response.status_code})')
                    total = 0
                    with part.open('wb') as stream:
                        for chunk in response.iter_content(1024 * 1024):
                            total += len(chunk)
                            if total > max_bytes:
                                raise MinerUError('Result archive exceeds the configured 512 MiB limit')
                            stream.write(chunk)
                    part.replace(target)
                    return
                finally:
                    response.close()
            raise MinerUError('Too many result download redirects')
        except requests.RequestException as exc:
            raise MinerUError(f'Result download network failure ({type(exc).__name__})') from None


def extract_archive(archive, destination, limit=512 * 1024 * 1024):
    destination = Path(destination).resolve()
    with zipfile.ZipFile(archive) as zipped:
        entries = zipped.infolist()
        if len(entries) > 10000 or sum(i.file_size for i in entries) > limit:
            raise MinerUError('Archive exceeds extraction limits')
        targets, seen = [], set()
        for entry in entries:
            name = entry.filename.replace('\\', '/')
            path = PurePosixPath(name)
            if path.is_absolute() or '..' in path.parts or PureWindowsPath(name).drive or ':' in name:
                raise MinerUError('Unsafe archive member path')
            if stat.S_ISLNK(entry.external_attr >> 16):
                raise MinerUError('Archive symlinks are not permitted')
            target = destination.joinpath(*path.parts).resolve()
            if not target.is_relative_to(destination) or str(target).casefold() in seen:
                raise MinerUError('Duplicate or escaping archive path')
            seen.add(str(target).casefold())
            targets.append((entry, target))
        for entry, target in targets:
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zipped.open(entry) as source, target.open('wb') as output:
                count = 0
                while chunk := source.read(1024 * 1024):
                    count += len(chunk)
                    if count > entry.file_size:
                        raise MinerUError('Archive member size mismatch')
                    output.write(chunk)
