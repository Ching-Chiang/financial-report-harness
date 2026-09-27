"""Deterministic validation and export; no model calls or database connections."""
from __future__ import annotations

import csv
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
import unicodedata

from runtime import PACKAGE, read_json, sha256, write_json

TABLES = ('AShareBalanceSheet', 'AShareIncome', 'AShareCashFlow')
UNITS = {'yuan': Decimal(1), 'wan_yuan': Decimal(10000), 'yi_yuan': Decimal(100000000),
         'yuan_per_share': Decimal(1), 'shares': Decimal(1), 'wan_shares': Decimal(10000)}
EPS_FIELDS = {'S_FA_EPS_BASIC', 'S_FA_EPS_DILUTED'}
NULL_TOKENS = {'', '-', '--', '\u2014', '\u2013', '\u2212', 'N/A', '不适用', '未披露'}


def normalize_text(value):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', value or ''))


def normalize_amount(raw, unit, field):
    if unit not in UNITS:
        raise ValueError('Unsupported or unconfirmed unit')
    allowed = {'yuan_per_share'} if field in EPS_FIELDS else ({'shares', 'wan_shares'} if field == 'TOT_SHR' else {'yuan', 'wan_yuan', 'yi_yuan'})
    if unit not in allowed:
        raise ValueError(f'Unit {unit} is incompatible with {field}')
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ValueError('raw_value must be a string or null, never a float')
    text = unicodedata.normalize('NFKC', raw).strip()
    if text in NULL_TOKENS:
        return None
    if text.startswith('(') and text.endswith(')'):
        text = '-' + text[1:-1]
    text = text.replace('\u2212', '-')
    if not re.fullmatch(r'[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?', text):
        raise ValueError(f'Ambiguous numeric cell: {raw!r}')
    number = Decimal(text.replace(',', '')) * UNITS[unit]
    if not number.is_finite() or abs(number) >= Decimal('10000000000000000'):
        raise ValueError('Value exceeds DECIMAL(20,4)')
    result = number.quantize(Decimal('0.0001'))
    if result != number:
        raise ValueError('Rounding beyond four decimal places needs review')
    return format(result, '.4f')


def load_schema():
    import sqlglot
    from sqlglot import exp
    result = {}
    for path in sorted((PACKAGE / 'references' / 'sql').glob('*.sql')):
        create = sqlglot.parse_one(path.read_text(encoding='utf-8-sig'), read='mysql')
        if not isinstance(create, exp.Create) or not isinstance(create.this, exp.Schema):
            raise ValueError(f'Unsupported target DDL: {path.name}')
        table = create.this.this.name
        if table not in TABLES:
            raise ValueError(f'Unexpected target table: {table}')
        columns = {}
        for column in create.this.expressions:
            if not isinstance(column, exp.ColumnDef):
                continue
            kind = column.args['kind']
            constraints = column.args.get('constraints', [])
            required = any(isinstance(c.kind, exp.NotNullColumnConstraint) for c in constraints)
            comment = next((c.kind.this.this for c in constraints if isinstance(c.kind, exp.CommentColumnConstraint)), '')
            columns[column.name] = {'type': kind.sql(dialect='mysql'), 'required': required, 'comment': comment}
        business_key = ['S_INFO_WINDCODE', 'WIND_CODE', 'REPORT_PERIOD', 'STATEMENT_TYPE']
        if table != 'AShareBalanceSheet':
            business_key.append('S_INFO_COMPCODE')
        result[table] = {'columns': columns, 'unique_keys': [['OBJECT_ID'], ['timetag'], ['seq'], business_key], 'sql_sha256': sha256(path)}
    if set(result) != set(TABLES):
        raise ValueError('All three target DDL files are required')
    return result


def validate_shape(data):
    from jsonschema import Draft202012Validator
    schema = read_json(PACKAGE / 'references' / 'candidate.schema.json')
    errors = sorted(Draft202012Validator(schema).iter_errors(data), key=lambda e: str(list(e.path)))
    if errors:
        raise ValueError('; '.join(f'{list(e.path)}: {e.message}' for e in errors[:12]))


def validate_metadata(metadata, columns):
    errors = []
    cleaned = {}
    for field, value in metadata.items():
        if field not in columns or columns[field]['type'].startswith('DECIMAL') and field != 'IS_CALCULATION':
            errors.append(f'Invalid metadata field: {field}')
            continue
        if value is None:
            cleaned[field] = None
            continue
        column_type = columns[field]['type']
        if column_type.startswith('BIGINT'):
            if not isinstance(value, str) or not re.fullmatch(r'-?\d+', value) or not -(2**63) <= int(value) < 2**63:
                errors.append(f'{field}: expected signed 64-bit integer string')
                continue
            value = str(int(value))
        elif column_type.startswith('VARCHAR'):
            limit = int(column_type.split('(')[1].split(')')[0])
            if not isinstance(value, str) or not value.strip() or len(value) > limit:
                errors.append(f'{field}: invalid string or exceeds {limit} characters')
                continue
            if any(ord(ch) > 0xFFFF for ch in value):
                errors.append(f'{field}: contains characters unsupported by target utf8 charset')
            if field in {'REPORT_PERIOD', 'ANN_DT', 'ACTUAL_ANN_DT'}:
                try:
                    if not re.fullmatch(r'\d{8}', value):
                        raise ValueError()
                    datetime.strptime(value, '%Y%m%d')
                except ValueError:
                    errors.append(f'{field}: expected real date YYYYMMDD')
        elif field == 'IS_CALCULATION':
            if not isinstance(value, str) or not re.fullmatch(r'-?\d{1,5}', value):
                errors.append(f'{field}: expected DECIMAL(5,0) integer string')
                continue
        cleaned[field] = value
    for field, column in columns.items():
        if column['required'] and cleaned.get(field) is None:
            errors.append(f'Missing required field: {field}')
    return cleaned, errors


EQUATIONS = {
    'AShareBalanceSheet': [
        ('assets_liabilities_equity', 'TOT_ASSETS', [('TOT_LIAB', 1), ('TOT_SHRHLDR_EQY_INCL_MIN_INT', 1)], True),
        ('balance_total', 'TOT_ASSETS', [('TOT_LIAB_SHRHLDR_EQY', 1)], False),
        ('asset_components', 'TOT_ASSETS', [('TOT_CUR_ASSETS', 1), ('TOT_NON_CUR_ASSETS', 1)], False),
    ],
    'AShareIncome': [
        ('profit_after_tax', 'NET_PROFIT_INCL_MIN_INT_INC', [('TOT_PROFIT', 1), ('INC_TAX', -1)], True),
        ('profit_attribution', 'NET_PROFIT_INCL_MIN_INT_INC', [('NET_PROFIT_EXCL_MIN_INT_INC', 1), ('MINORITY_INT_INC', 1)], False),
    ],
    'AShareCashFlow': [
        ('cash_rollforward', 'CASH_CASH_EQU_END_PERIOD', [('CASH_CASH_EQU_BEG_PERIOD', 1), ('NET_INCR_CASH_CASH_EQU', 1)], True),
        ('cash_change', 'NET_INCR_CASH_CASH_EQU', [('NET_CASH_FLOWS_OPER_ACT', 1), ('NET_CASH_FLOWS_INV_ACT', 1), ('NET_CASH_FLOWS_FNC_ACT', 1), ('EFF_FX_FLU_CASH', 1)], True),
        ('operating_cash', 'NET_CASH_FLOWS_OPER_ACT', [('STOT_CASH_INFLOWS_OPER_ACT', 1), ('STOT_CASH_OUTFLOWS_OPER_ACT', -1)], False),
    ],
}


def check_equations(table, values, tolerance):
    checks = []
    for name, target, terms, required in EQUATIONS[table]:
        missing = [field for field in [target] + [field for field, _ in terms] if values.get(field) is None]
        if missing:
            checks.append({'name': name, 'status': 'not_checkable', 'required': required, 'missing': missing})
            continue
        rhs = sum((Decimal(values[field]) * sign for field, sign in terms), Decimal(0))
        difference = Decimal(values[target]) - rhs
        checks.append({'name': name, 'status': 'pass' if abs(difference) <= tolerance else 'fail',
                       'required': required, 'difference': str(difference), 'tolerance': str(tolerance)})
    return checks


def validate_candidates(input_path: Path):
    data = read_json(input_path)
    validate_shape(data)
    schema = load_schema()
    global_errors = []
    source = data['source']
    pdf = (input_path.parent / source['pdf_path']).resolve()
    manifest_path = (input_path.parent / source['parse_manifest']).resolve()
    if not pdf.is_file() or sha256(pdf) != source['sha256']:
        global_errors.append('Source PDF missing or SHA256 mismatch')
    manifest = read_json(manifest_path)
    if manifest.get('status') != 'complete' or manifest.get('source_sha256') != source['sha256']:
        global_errors.append('Parse manifest incomplete or points to a different PDF')
    evidence_path = manifest_path.parent / 'evidence.json'
    if sha256(evidence_path) != manifest.get('evidence_sha256'):
        global_errors.append('Evidence file differs from completed parse manifest')
    evidence = {block['block_id']: block for block in read_json(evidence_path)['blocks']}
    if source['page_count'] != manifest.get('source_page_count'):
        global_errors.append('Source page count differs from parse manifest')
    rules = data['business_rules']
    if rules['status'] != 'confirmed' or not rules.get('reference', '').strip():
        global_errors.append('Business code and ID rules are not confirmed')
    try:
        tolerance = Decimal(rules['amount_tolerance'])
        if not tolerance.is_finite() or tolerance < 0 or tolerance > Decimal('1.00'):
            raise ValueError()
    except (InvalidOperation, ValueError):
        raise ValueError('amount_tolerance must be a finite decimal between 0 and 1 yuan')
    results = []
    for index, record in enumerate(data['records']):
        table = record['table']
        columns = schema[table]['columns']
        cleaned, errors = validate_metadata(record['metadata'], columns)
        values = {}
        if record['scope'] != 'consolidated':
            errors.append('MVP accepts consolidated statements only')
        if record['period_label'].strip() == '':
            errors.append('Original period column heading missing')
        for item in record['fields']:
            name = item['field']
            if name in values:
                errors.append(f'Duplicate candidate field: {name}')
                continue
            if name not in columns or not columns[name]['type'].startswith('DECIMAL') or name == 'IS_CALCULATION':
                errors.append(f'Invalid amount field: {name}')
                continue
            try:
                values[name] = normalize_amount(item['raw_value'], item['source_unit'], name)
            except (InvalidOperation, ValueError) as exc:
                errors.append(f'{name}: {exc}')
                continue
            block = evidence.get(item['block_id'])
            if not block or block['pdf_page'] != item['pdf_page'] or not 1 <= item['pdf_page'] <= source['page_count']:
                errors.append(f'{name}: invalid evidence block or page')
            else:
                text = normalize_text(block['text'])
                if normalize_text(item['raw_label']) not in text:
                    errors.append(f'{name}: original label absent from evidence')
                raw = normalize_text(item['raw_value'])
                if raw and raw not in text:
                    errors.append(f'{name}: original value absent from evidence')
            if item['mapping_status'] != 'confirmed':
                errors.append(f'{name}: mapping needs review')
            if item['review_status'] != 'verified':
                errors.append(f'{name}: extraction needs review')
            if not item['mapping_basis'].strip():
                errors.append(f'{name}: mapping basis missing')
        if not any(value is not None for value in values.values()):
            errors.append('Record contains no non-null financial amounts')
        checks = check_equations(table, values, tolerance)
        for check in checks:
            if check['status'] == 'fail' or check['required'] and check['status'] == 'not_checkable':
                errors.append(f'Financial check {check["name"]}: {check["status"]}')
        cleaned.update(values)
        results.append({'index': index, 'table': table, 'record': cleaned, 'checks': checks,
                        'errors': errors, 'scope': record['scope'], 'period_label': record['period_label']})
    seen = {}
    for result in results:
        for key in schema[result['table']]['unique_keys']:
            values = tuple(result['record'].get(k) for k in key)
            if any(value is None for value in values):
                continue
            identity = (result['table'], tuple(key), values)
            if identity in seen:
                message = f'Duplicate unique key: {", ".join(key)}'
                result['errors'].append(message)
                seen[identity]['errors'].append(message)
            else:
                seen[identity] = result
    for result in results:
        result['status'] = 'ready' if not result['errors'] and not global_errors else 'needs_review'
    return {'schema_version': 1, 'status': 'ready' if results and all(r['status'] == 'ready' for r in results) else 'needs_review',
            'input_sha256': sha256(input_path), 'global_errors': global_errors,
            'source': source, 'schema_hashes': {name: entry['sql_sha256'] for name, entry in schema.items()},
            'records': results, 'unmatched_items': data['unmatched_items'],
            'coverage': {table: sum(r['table'] == table for r in results) for table in TABLES}}


def sql_literal(value, column_type):
    if value is None:
        return 'NULL'
    if column_type.startswith(('DECIMAL', 'BIGINT')):
        return str(value)
    return "CONVERT(X'" + str(value).encode('utf-8').hex() + "' USING utf8)"


def export_candidates(input_path: Path, output: Path):
    report = validate_candidates(input_path)
    if output.exists():
        raise ValueError('Export directory already exists. Use a new directory to avoid stale formal files.')
    output.mkdir(parents=True)
    write_json(output / 'quality-report.json', report)
    write_json(output / 'candidates.json', read_json(input_path))
    write_json(output / 'exceptions.json', {'global': report['global_errors'],
               'records': [{'index': r['index'], 'errors': r['errors']} for r in report['records'] if r['errors']],
               'unmatched_items': report['unmatched_items']})
    # A batch is released atomically: no partial formal export beside rejected records.
    if report['status'] != 'ready':
        return report
    schema = load_schema()
    statements = ['-- Validated candidate export. No overwrite/upsert semantics.', 'START TRANSACTION;']
    for table in TABLES:
        rows = [r['record'] for r in report['records'] if r['table'] == table]
        if not rows:
            continue
        columns = schema[table]['columns']
        names = list(columns)
        with (output / f'{table}.csv').open('w', encoding='utf-8', newline='') as stream:
            writer = csv.writer(stream)
            writer.writerow(names)
            for row in rows:
                writer.writerow([row.get(n) if row.get(n) is not None else '\\N' for n in names])
                sql_values = ','.join(sql_literal(row.get(n), columns[n]['type']) for n in names)
                statements.append(f'INSERT INTO `{table}` (' + ','.join(f'`{n}`' for n in names) + f') VALUES ({sql_values});')
    statements.append('COMMIT;')
    (output / 'insert.sql').write_text('\n'.join(statements) + '\n', encoding='utf-8')
    write_json(output / 'export-manifest.json', {'status': 'ready', 'input_sha256': report['input_sha256'],
                'csv_null': '\\N', 'encoding': 'UTF-8', 'schema_hashes': report['schema_hashes'],
                'files': {p.name: sha256(p) for p in output.iterdir() if p.is_file()}})
    return report
