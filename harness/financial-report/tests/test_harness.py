"""Offline contract tests. No API credentials or financial vendor codes required."""
from __future__ import annotations
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import requests
from pypdf import PdfWriter
from business import load_schema, normalize_amount, validate_candidates, export_candidates
from cli import page_selection, parse_pdf
from mineru_api import MinerUClient, MinerUError, extract_archive
from runtime import read_json, write_json, sha256


class Response:
    def __init__(self, payload=None, status=200, binary=b'', headers=None):
        self.payload, self.status_code, self.binary = payload, status, binary
        self.headers = headers or {}
    def json(self):
        return self.payload
    def close(self):
        pass
    def iter_content(self, _):
        yield self.binary


def envelope(data):
    return Response({'code': 0, 'data': data})


def make_pdf(path, count=1):
    writer = PdfWriter()
    for _ in range(count):
        writer.add_blank_page(600, 800)
    with path.open('wb') as stream:
        writer.write(stream)


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.session = Mock()
        self.client = MinerUClient('test-secret-token', session=self.session, sleep=lambda _: None)

    def test_missing_token(self):
        with self.assertRaises(MinerUError):
            MinerUClient('')

    def test_authorization_only_on_api(self):
        self.session.request.side_effect = [
            envelope({'batch_id': 'batch1', 'file_urls': ['https://uploads.example.com/file']}),
            Response(status=200),
            envelope({'extract_result': [{'data_id': 'data1', 'state': 'done', 'full_zip_url': 'https://cdn.example.com/result.zip'}]}),
            Response(binary=b'zip'),
        ]
        with tempfile.TemporaryDirectory() as folder:
            pdf = Path(folder) / 'source.pdf'
            pdf.write_bytes(b'PDF')
            batch, url = self.client.create_upload('source.pdf', 'data1')
            self.client.upload(url, pdf)
            result = self.client.wait_result(batch, 'source.pdf', 'data1')
            self.client.download(result, Path(folder) / 'result.zip')
        calls = self.session.request.call_args_list
        self.assertEqual(calls[0].kwargs['headers']['Authorization'], 'Bearer test-secret-token')
        self.assertNotIn('Authorization', calls[1].kwargs['headers'])
        self.assertIn('Authorization', calls[2].kwargs['headers'])
        self.assertNotIn('Authorization', calls[3].kwargs['headers'])
        self.assertFalse(calls[0].kwargs['allow_redirects'])

    def test_no_repeat_on_uncertain_post(self):
        self.session.request.side_effect = requests.Timeout('test-secret-token')
        with self.assertRaises(MinerUError) as error:
            self.client.create_upload('source.pdf', 'data1')
        self.assertEqual(self.session.request.call_count, 1)
        self.assertNotIn('test-secret-token', str(error.exception))

    def test_auth_rejection(self):
        self.session.request.return_value = Response(status=401)
        with self.assertRaisesRegex(MinerUError, 'rejected the token'):
            self.client.api('GET', '/test')

    def test_api_error_redaction(self):
        self.session.request.return_value = Response({'code': 'A0202', 'msg': 'test-secret-token https://secret.example/key'})
        with self.assertRaises(MinerUError) as error:
            self.client.api('GET', '/test')
        self.assertNotIn('test-secret-token', str(error.exception))
        self.assertNotIn('secret.example', str(error.exception))

    def test_failed_remote_task(self):
        self.session.request.return_value = envelope({'extract_result': [{'data_id': 'x', 'state': 'failed', 'err_msg': 'bad file'}]})
        with self.assertRaisesRegex(MinerUError, 'task failed'):
            self.client.wait_result('b', 'f.pdf', 'x')

    def test_poll_retries_rate_limit(self):
        self.session.request.side_effect = [Response(status=429), envelope({'extract_result': [{'data_id': 'x', 'state': 'done', 'full_zip_url': 'https://cdn.example/a.zip'}]})]
        self.assertEqual(self.client.wait_result('b', 'f.pdf', 'x'), 'https://cdn.example/a.zip')
        self.assertEqual(self.session.request.call_count, 2)

    def test_poll_timeout(self):
        current = [0]
        self.client.clock = lambda: current[0]
        self.client.sleep = lambda seconds: current.__setitem__(0, current[0] + seconds)
        self.session.request.return_value = envelope({'extract_result': [{'data_id': 'x', 'state': 'running'}]})
        with self.assertRaisesRegex(MinerUError, 'Resume'):
            self.client.wait_result('b', 'f.pdf', 'x', timeout=10, interval=5)

    def test_wrong_document_never_selected(self):
        current = [0]
        self.client.clock = lambda: current[0]
        self.client.sleep = lambda seconds: current.__setitem__(0, current[0] + seconds)
        self.session.request.return_value = envelope({'extract_result': [{'data_id': 'wrong', 'file_name': 'f.pdf', 'state': 'done', 'full_zip_url': 'https://cdn.example/a.zip'}]})
        with self.assertRaisesRegex(MinerUError, 'timed out'):
            self.client.wait_result('b', 'f.pdf', 'x', timeout=1, interval=1)

    def test_reject_non_json_envelope(self):
        self.session.request.return_value = Response([])
        with self.assertRaisesRegex(MinerUError, 'non-object'):
            self.client.api('GET', '/test')


class ParseTests(unittest.TestCase):
    def test_page_selection(self):
        self.assertEqual(page_selection('3,1-2,2', 5), [1, 2, 3])
        for invalid in ['0', '3-2', '6', '1--2', 'x']:
            with self.assertRaises(ValueError):
                page_selection(invalid, 5)

    def test_zip_path_traversal_rejected_before_writes(self):
        with tempfile.TemporaryDirectory() as folder:
            archive = Path(folder) / 'archive.zip'
            with zipfile.ZipFile(archive, 'w') as zipped:
                zipped.writestr('normal.txt', 'ok')
                zipped.writestr('../escape.txt', 'bad')
            with self.assertRaises(MinerUError):
                extract_archive(archive, Path(folder) / 'out')
            self.assertFalse((Path(folder) / 'escape.txt').exists())
            self.assertFalse((Path(folder) / 'out' / 'normal.txt').exists())

    def test_offline_api_flow_maps_original_pages_and_resume(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / '源 文件.pdf'
            make_pdf(source, 4)
            content = [{'type': 'table', 'page_idx': 1, 'table_body': '<table><tr><td>资产总计</td><td>100.00</td></tr></table>'}]
            payload = io.BytesIO()
            with zipfile.ZipFile(payload, 'w') as zipped:
                zipped.writestr('full.md', '资产总计 100.00')
                zipped.writestr('document_content_list.json', json.dumps(content))
            client = Mock()
            client.create_upload.return_value = ('batch1', 'https://upload.example/f')
            client.wait_result.return_value = 'https://cdn.example/f.zip'
            client.download.side_effect = lambda _, path: Path(path).write_bytes(payload.getvalue())
            client.safe_message.side_effect = str
            output = root / '中文 results'
            result = parse_pdf(source, output, '2,4', client=client)
            self.assertEqual(result['status'], 'complete')
            self.assertEqual(read_json(output / 'evidence.json')['blocks'][0]['pdf_page'], 4)
            self.assertNotIn('https://upload', (output / 'parse-manifest.json').read_text(encoding='utf-8'))
            parse_pdf(source, output, resume=True, client=client)
            self.assertEqual(client.create_upload.call_count, 1)
            self.assertEqual(client.upload.call_count, 1)


class FinancialTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        make_pdf(self.root / 'source.pdf')
        write_json(self.root / 'evidence.json', {'blocks': [{'block_id': 'b0', 'pdf_page': 1, 'text': '资产总计 100.00 负债合计 60.00 所有者权益合计 40.00'}]})
        write_json(self.root / 'parse-manifest.json', {'status': 'complete', 'source_sha256': sha256(self.root / 'source.pdf'), 'source_page_count': 1, 'evidence_sha256': sha256(self.root / 'evidence.json')})
        fields = []
        for field, label, value in [('TOT_ASSETS', '资产总计', '100.00'), ('TOT_LIAB', '负债合计', '60.00'), ('TOT_SHRHLDR_EQY_INCL_MIN_INT', '所有者权益合计', '40.00')]:
            fields.append({'field': field, 'raw_label': label, 'raw_value': value, 'source_unit': 'yuan', 'pdf_page': 1, 'block_id': 'b0', 'confidence': 1, 'mapping_status': 'confirmed', 'mapping_basis': 'SYNTHETIC TEST ONLY', 'review_status': 'verified'})
        self.data = {'schema_version': 1, 'source': {'pdf_path': 'source.pdf', 'sha256': sha256(self.root / 'source.pdf'), 'page_count': 1, 'parse_manifest': 'parse-manifest.json'}, 'business_rules': {'status': 'confirmed', 'reference': 'SYNTHETIC TEST ONLY: NOT REAL WIND CODES', 'amount_tolerance': '0.01'}, 'records': [{'table': 'AShareBalanceSheet', 'scope': 'consolidated', 'period_label': '2023年12月31日', 'metadata': {'OBJECT_ID': 'TEST', 'S_INFO_WINDCODE': 'TEST', 'WIND_CODE': 'TEST', 'REPORT_PERIOD': '20231231', 'STATEMENT_TYPE': 'TEST', 'timetag': '1', 'seq': '1'}, 'fields': fields}], 'unmatched_items': []}
        self.path = self.root / 'candidates.json'

    def tearDown(self):
        self.temporary.cleanup()

    def validate(self):
        write_json(self.path, self.data)
        return validate_candidates(self.path)

    def test_schema_counts(self):
        self.assertEqual({k: len(v['columns']) for k, v in load_schema().items()}, {'AShareBalanceSheet': 182, 'AShareIncome': 114, 'AShareCashFlow': 126})

    def test_decimal_cleaning(self):
        self.assertEqual(normalize_amount('1,234.56', 'wan_yuan', 'TOT_ASSETS'), '12345600.0000')
        self.assertEqual(normalize_amount('(1,234.56)', 'yuan', 'TOT_ASSETS'), '-1234.5600')
        self.assertEqual(normalize_amount('0', 'yuan', 'TOT_ASSETS'), '0.0000')
        for empty in ['', None, '—', '-']:
            self.assertIsNone(normalize_amount(empty, 'yuan', 'TOT_ASSETS'))

    def test_invalid_amounts_and_units(self):
        for raw, unit, field in [('1,23', 'yuan', 'TOT_ASSETS'), ('NaN', 'yuan', 'TOT_ASSETS'), ('1.00001', 'yuan', 'TOT_ASSETS'), ('1', 'wan_yuan', 'S_FA_EPS_BASIC'), ('1e3', 'yuan', 'TOT_ASSETS'), ('10000000000000000', 'yuan', 'TOT_ASSETS')]:
            with self.assertRaises(ValueError):
                normalize_amount(raw, unit, field)

    def test_valid_record_can_export(self):
        self.assertEqual(self.validate()['status'], 'ready')
        result = export_candidates(self.path, self.root / 'export')
        self.assertEqual(result['status'], 'ready')
        self.assertTrue((self.root / 'export' / 'insert.sql').exists())
        self.assertIn('100.0000', (self.root / 'export' / 'AShareBalanceSheet.csv').read_text())

    def test_unconfirmed_codes_block_sql(self):
        self.data['business_rules']['status'] = 'pending'
        self.validate()
        result = export_candidates(self.path, self.root / 'export')
        self.assertEqual(result['status'], 'needs_review')
        self.assertFalse((self.root / 'export' / 'insert.sql').exists())
        self.assertFalse(list((self.root / 'export').glob('AShare*.csv')))

    def test_missing_required_metadata(self):
        del self.data['records'][0]['metadata']['STATEMENT_TYPE']
        self.assertEqual(self.validate()['status'], 'needs_review')

    def test_equation_failure(self):
        self.data['records'][0]['fields'][0]['raw_value'] = '60.00'
        result = self.validate()
        self.assertEqual(result['status'], 'needs_review')
        self.assertTrue(any('assets_liabilities_equity' in e for e in result['records'][0]['errors']))

    def test_missing_source_evidence(self):
        self.data['records'][0]['fields'][0]['pdf_page'] = 2
        self.assertEqual(self.validate()['status'], 'needs_review')

    def test_duplicate_numeric_unique_keys(self):
        duplicate = copy.deepcopy(self.data['records'][0])
        duplicate['metadata'].update({'OBJECT_ID': 'TEST2', 'REPORT_PERIOD': '20221231', 'timetag': '01', 'seq': '2'})
        self.data['records'].append(duplicate)
        result = self.validate()
        self.assertTrue(all(r['status'] == 'needs_review' for r in result['records']))

    def test_tampered_evidence(self):
        write_json(self.root / 'evidence.json', {'blocks': []})
        self.assertEqual(self.validate()['status'], 'needs_review')

    def test_invalid_date(self):
        self.data['records'][0]['metadata']['REPORT_PERIOD'] = '20230230'
        self.assertEqual(self.validate()['status'], 'needs_review')

    def test_invalid_or_unreviewed_field(self):
        self.data['records'][0]['fields'][0]['review_status'] = 'unreviewed'
        self.assertEqual(self.validate()['status'], 'needs_review')

    def test_existing_export_refused(self):
        self.validate()
        output = self.root / 'export'
        output.mkdir()
        with self.assertRaisesRegex(ValueError, 'already exists'):
            export_candidates(self.path, output)


if __name__ == '__main__':
    unittest.main(verbosity=2)
