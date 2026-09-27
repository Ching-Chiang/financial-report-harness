"""Project command entrypoint: MinerU online parsing, validation, and export."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import shutil
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from runtime import doctor, read_json, sha256, write_json


def page_selection(text, count):
    if not text:
        return list(range(1, count + 1))
    pages = set()
    for segment in text.split(','):
        bounds = segment.strip().split('-')
        if len(bounds) not in (1, 2) or any(not b.isdigit() for b in bounds):
            raise ValueError('Pages must use one-based ranges such as 58-61,63-65,66-67')
        first, last = int(bounds[0]), int(bounds[-1])
        if first < 1 or last < first or last > count:
            raise ValueError(f'Page range outside 1..{count}: {segment}')
        pages.update(range(first, last + 1))
    return sorted(pages)


def extract_evidence(output: Path, page_map):
    from bs4 import BeautifulSoup
    files = sorted(p for p in (output / 'mineru').rglob('*.json') if p.name.endswith('_content_list.json') or p.name == 'content_list.json')
    if len(files) != 1:
        raise ValueError(f'Expected one MinerU content list, found {len(files)}; raw API output preserved for review')
    blocks = []
    for index, item in enumerate(read_json(files[0])):
        local_page = item.get('page_idx')
        if not isinstance(local_page, int) or not 0 <= local_page < len(page_map):
            raise ValueError(f'Unusable MinerU page index at block {index}')
        pieces = []
        for key in ('text', 'table_caption', 'table_body', 'table_footnote', 'image_caption', 'image_footnote'):
            value = item.get(key, '')
            if isinstance(value, list):
                value = '\n'.join(str(v) for v in value)
            if value:
                pieces.append(BeautifulSoup(str(value), 'html.parser').get_text(' ', strip=True))
        blocks.append({'block_id': f'b{index:05d}', 'pdf_page': page_map[local_page],
            'mineru_page_idx': local_page, 'type': item.get('type'), 'text': '\n'.join(pieces),
            'bbox': item.get('bbox'), 'raw': item})
    if not blocks or not any(b['text'] for b in blocks):
        raise ValueError('MinerU produced no usable text evidence')
    write_json(output / 'evidence.json', {'schema_version': 1, 'blocks': blocks})
    return files[0]


def parse_pdf(pdf: Path, output: Path, pages=None, model='vlm', timeout=900, interval=5, resume=False, client=None):
    from pypdf import PdfReader, PdfWriter
    from mineru_api import MinerUClient, extract_archive
    client = client or MinerUClient()
    manifest_path = output / 'parse-manifest.json'
    if resume:
        manifest = read_json(manifest_path)
        if not manifest.get('batch_id'):
            raise ValueError('No saved batch ID; cannot resume this run')
        if sha256(pdf) != manifest['source_sha256'] or sha256(output / 'source.pdf') != manifest['source_sha256']:
            raise ValueError('Resume source does not match the existing task')
        if sha256(output / 'input' / 'selected.pdf') != manifest['selected_sha256']:
            raise ValueError('Resume upload file has changed')
        page_map = manifest['selected_pdf_pages']
    else:
        if output.exists():
            raise ValueError('Output exists. Use --resume for an existing API batch, or choose a new directory.')
        pdf = pdf.resolve(strict=True)
        if pdf.suffix.lower() != '.pdf':
            raise ValueError('Expected PDF input')
        reader = PdfReader(pdf)
        if reader.is_encrypted:
            raise ValueError('Encrypted PDFs are not supported')
        page_map = page_selection(pages, len(reader.pages))
        if len(page_map) > 200:
            raise ValueError('MinerU precise API accepts at most 200 pages per request; select a smaller page range')
        output.mkdir(parents=True)
        (output / 'input').mkdir()
        shutil.copy2(pdf, output / 'source.pdf')
        selected = output / 'input' / 'selected.pdf'
        writer = PdfWriter()
        for page in page_map:
            writer.add_page(reader.pages[page - 1])
        with selected.open('wb') as stream:
            writer.write(stream)
        if selected.stat().st_size > 200 * 1024 * 1024:
            raise ValueError('Selected PDF exceeds the 200 MiB API limit')
        manifest = {'schema_version': 1, 'status': 'prepared', 'mode': 'mineru_online_api',
            'source_pdf': 'source.pdf', 'source_name': pdf.name, 'source_sha256': sha256(pdf),
            'source_page_count': len(reader.pages), 'selected_pdf_pages': page_map,
            'selected_sha256': sha256(selected), 'model_version': model,
            'started_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
        manifest['upload_name'] = 'document_' + manifest['selected_sha256'][:12] + '.pdf'
        manifest['data_id'] = manifest['selected_sha256']
        write_json(manifest_path, manifest)
    try:
        if not resume:
            batch_id, upload_url = client.create_upload(manifest['upload_name'], manifest['data_id'], model)
            manifest.update({'batch_id': batch_id, 'status': 'uploading'})
            write_json(manifest_path, manifest)
            client.upload(upload_url, output / 'input' / 'selected.pdf')
        manifest['status'] = 'waiting'
        manifest.pop('error', None)
        write_json(manifest_path, manifest)
        archive_url = client.wait_result(manifest['batch_id'], manifest['upload_name'], manifest['data_id'], timeout, interval)
        client.download(archive_url, output / 'result.zip')
        extract_archive(output / 'result.zip', output / 'mineru')
        content = extract_evidence(output, page_map)
        manifest.update({'status': 'complete', 'content_list': content.relative_to(output).as_posix(),
            'archive_sha256': sha256(output / 'result.zip'), 'evidence_sha256': sha256(output / 'evidence.json'),
            'finished_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())})
        write_json(manifest_path, manifest)
    except Exception as exc:
        manifest.update({'status': 'failed', 'error': client.safe_message(str(exc))})
        write_json(manifest_path, manifest)
        raise
    return manifest


def main():
    parser = argparse.ArgumentParser(description='Financial report harness using the MinerU online API')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('doctor')
    parse = sub.add_parser('parse')
    parse.add_argument('--pdf', type=Path, required=True)
    parse.add_argument('--output', type=Path, required=True)
    parse.add_argument('--pages', help='One-based original PDF pages')
    parse.add_argument('--model', choices=['vlm', 'pipeline'], default='vlm')
    parse.add_argument('--timeout', type=int, default=900)
    parse.add_argument('--poll-interval', type=int, default=5)
    parse.add_argument('--resume', action='store_true')
    for name in ('validate', 'export'):
        command = sub.add_parser(name)
        command.add_argument('--input', type=Path, required=True)
        command.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'doctor':
        result = doctor()
        code = 0 if result['status'] == 'ready' else 2
    elif args.command == 'parse':
        if args.timeout < 1 or args.poll_interval < 1:
            raise ValueError('Timeout and poll interval must be positive')
        result = parse_pdf(args.pdf, args.output.resolve(), args.pages, args.model, args.timeout, args.poll_interval, args.resume)
        code = 0
    else:
        from business import validate_candidates, export_candidates
        if args.command == 'validate':
            result = validate_candidates(args.input.resolve())
            write_json(args.output, result)
        else:
            result = export_candidates(args.input.resolve(), args.output.resolve())
        code = 0 if result['status'] == 'ready' else 3
    summary = {k: result[k] for k in ['status', 'mode', 'errors', 'global_errors', 'coverage', 'mineru_token_configured', 'network_tested', 'batch_id'] if k in result}
    if 'records' in result:
        summary['record_count'] = len(result['records'])
        summary['blocked_records'] = sum(r['status'] != 'ready' for r in result['records'])
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return code


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, ImportError) as exc:
        print(json.dumps({'status': 'error', 'message': str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2)
