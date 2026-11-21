import csv
from src.config.settings import ARTIFACTS
from src.domain.spec import SPEC


def safe_cell(value):
    if isinstance(value, str) and (value.lstrip().startswith(('=', '+', '-', '@'))
                                   or value.startswith(('\t', '\r', '\n'))):
        return "'" + value
    return value


def _meta_rows(snapshot=None, extra=None):
    rows = [('报表口径', SPEC['title'])]
    if snapshot:
        rows.extend([
            ('数据批次', snapshot.get('dataset_name', '')),
            ('数据批次版本', snapshot.get('dataset_revision', '')),
            ('数据指纹', snapshot.get('snapshot_checksum', '')),
            ('报告日', snapshot.get('report_day') or ''),
            ('运行编号', snapshot.get('run_id', '')),
        ])
    if extra:
        rows.extend(extra)
    return rows


def write_report(run_id, attempt, result, snapshot_params=None, snapshot=None):
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    name = f'analysis-{run_id}-{attempt}.csv'
    path = ARTIFACTS / name
    staging = path.with_suffix('.tmp')
    with staging.open('w', encoding='utf-8-sig', newline='') as output:
        writer = csv.writer(output)
        writer.writerow([safe_cell(result['headline'])])
        writer.writerow([safe_cell(result.get('note', ''))])
        for label, value in _meta_rows(snapshot):
            if value not in ('', None):
                writer.writerow([safe_cell(label), safe_cell(value)])
        if snapshot_params:
            writer.writerow([])
            writer.writerow(['运行参数'])
            for key, value in snapshot_params.items():
                writer.writerow([safe_cell(key), safe_cell(value)])
        for metric in result['metrics']:
            writer.writerow([safe_cell(metric['label']), safe_cell(metric['value']), safe_cell(metric.get('detail', ''))])
        for table in result['tables']:
            writer.writerow([])
            writer.writerow([safe_cell(table['title'])])
            columns = table['columns']
            writer.writerow([safe_cell(column['label']) for column in columns])
            writer.writerows([safe_cell(row.get(column['key'])) for column in columns] for row in table['rows'])
        exclusions = result.get('exclusions') or []
        if exclusions:
            writer.writerow([])
            writer.writerow(['被排除市场及缺报日'])
            writer.writerow(['市场', '分组', '缺报日数', '缺报日期'])
            for item in exclusions:
                writer.writerow([safe_cell(item['market']), safe_cell(item['group']),
                                 item['missing_days'], safe_cell(item['missing'])])
    staging.replace(path)
    return name


def snapshot_meta(job, dataset_name):
    """Locked identity of a run's inputs, written into every export row header."""
    return {'run_id': job.id, 'dataset_name': dataset_name,
            'dataset_revision': job.dataset_revision,
            'snapshot_checksum': job.snapshot_checksum,
            'report_day': job.report_day.isoformat() if job.report_day else ''}
