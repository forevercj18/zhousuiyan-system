import json
from django.core.management.base import BaseCommand

from apps.core.utils import run_data_consistency_checks, persist_data_consistency_check_result


class Command(BaseCommand):
    help = '执行数据一致性巡检（只读），输出问题摘要与明细。'

    def add_arguments(self, parser):
        parser.add_argument(
            '--json',
            action='store_true',
            dest='as_json',
            help='以 JSON 输出结果'
        )
        parser.add_argument(
            '--save',
            action='store_true',
            dest='save_result',
            help='将巡检结果保存到台账'
        )

    def handle(self, *args, **options):
        result = run_data_consistency_checks()
        if options.get('save_result'):
            run_record = persist_data_consistency_check_result(result, executed_by=None, source='command')
            self.stdout.write(self.style.SUCCESS(f'已保存巡检记录：#{run_record.id}'))
        if options.get('as_json'):
            self.stdout.write(json.dumps(result, ensure_ascii=False, indent=2))
            return

        self.stdout.write(self.style.NOTICE('=== 数据一致性巡检结果 ==='))
        self.stdout.write(f"问题总数: {result['total_issues']}")
        self.stdout.write(f"错误: {result['error_count']}  警告: {result['warning_count']}")
        type_counts = result.get('type_counts') or {}
        if type_counts:
            self.stdout.write('按类型统计:')
            for issue_type, count in sorted(type_counts.items(), key=lambda x: (-int(x[1] or 0), x[0])):
                self.stdout.write(f" - {issue_type}: {int(count or 0)}")

        if not result['issues']:
            self.stdout.write(self.style.SUCCESS('未发现异常。'))
            return

        for idx, issue in enumerate(result['issues'], start=1):
            prefix = f"[{idx}] [{issue.get('severity', '-').upper()}] {issue.get('type', '-')}: {issue.get('message', '')}"
            if issue.get('severity') == 'error':
                self.stdout.write(self.style.ERROR(prefix))
            else:
                self.stdout.write(self.style.WARNING(prefix))
            self.stdout.write(f"    meta={issue.get('meta', {})}")
