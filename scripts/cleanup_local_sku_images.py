r"""
清理已迁移到 Cloudflare R2 后的本地 SKU 图片引用/文件

默认只做预览，不会修改数据库或删除文件。

用法：
    .\.venv\Scripts\python scripts\cleanup_local_sku_images.py --dry-run
    .\.venv\Scripts\python scripts\cleanup_local_sku_images.py --clear-fields
    .\.venv\Scripts\python scripts\cleanup_local_sku_images.py --clear-fields --delete-files
"""
import argparse
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings_dev')

import django  # noqa: E402

django.setup()

from apps.core.models import SKU, SKUImage  # noqa: E402


def cleanup_sku_covers(*, clear_fields=False, delete_files=False):
    processed = 0
    skipped = 0
    deleted = 0
    failed = 0
    for sku in SKU.objects.exclude(image='').exclude(image__isnull=True).order_by('id'):
        if not sku.image_key:
            skipped += 1
            continue
        local_field = sku.image
        if not local_field:
            skipped += 1
            continue
        local_path = ''
        try:
            local_path = local_field.path
        except Exception:
            local_path = ''
        print(f'[SKU] {sku.code} local={local_field.name} key={sku.image_key}')
        processed += 1
        if clear_fields:
            sku.image = None
            sku.save(update_fields=['image', 'updated_at'])
        if delete_files and local_path:
            try:
                if os.path.exists(local_path):
                    os.remove(local_path)
                    deleted += 1
            except Exception as exc:
                print(f'  [FAIL] 删除文件失败: {exc}')
                failed += 1
    return processed, skipped, deleted, failed


def cleanup_sku_gallery(*, clear_fields=False, delete_files=False):
    processed = 0
    skipped = 0
    deleted = 0
    failed = 0
    for image in SKUImage.objects.exclude(image='').exclude(image__isnull=True).select_related('sku').order_by('id'):
        if not image.image_key:
            skipped += 1
            continue
        local_field = image.image
        if not local_field:
            skipped += 1
            continue
        local_path = ''
        try:
            local_path = local_field.path
        except Exception:
            local_path = ''
        print(f'[SKUImage] #{image.id} {image.sku.code} local={local_field.name} key={image.image_key}')
        processed += 1
        if clear_fields:
            image.image = None
            image.save(update_fields=['image'])
        if delete_files and local_path:
            try:
                if os.path.exists(local_path):
                    os.remove(local_path)
                    deleted += 1
            except Exception as exc:
                print(f'  [FAIL] 删除文件失败: {exc}')
                failed += 1
    return processed, skipped, deleted, failed


def main():
    parser = argparse.ArgumentParser(description='清理已迁移到 Cloudflare R2 后的本地 SKU 图片')
    parser.add_argument('--dry-run', action='store_true', help='仅输出将清理的对象（默认行为）')
    parser.add_argument('--clear-fields', action='store_true', help='清空数据库中的本地 FileField 引用')
    parser.add_argument('--delete-files', action='store_true', help='删除本地磁盘文件（需搭配 --clear-fields 使用）')
    args = parser.parse_args()

    if args.delete_files and not args.clear_fields:
        raise SystemExit('--delete-files 必须和 --clear-fields 一起使用')

    clear_fields = bool(args.clear_fields)
    delete_files = bool(args.delete_files)

    cover_result = cleanup_sku_covers(clear_fields=clear_fields, delete_files=delete_files)
    gallery_result = cleanup_sku_gallery(clear_fields=clear_fields, delete_files=delete_files)

    print('\n=== 清理完成 ===')
    print(f'SKU 主图：处理 {cover_result[0]}，跳过 {cover_result[1]}，删除文件 {cover_result[2]}，失败 {cover_result[3]}')
    print(f'SKU 画廊：处理 {gallery_result[0]}，跳过 {gallery_result[1]}，删除文件 {gallery_result[2]}，失败 {gallery_result[3]}')
    if not clear_fields:
        print('当前为预览模式；如确认无误，请使用 --clear-fields 执行数据库清理。')


if __name__ == '__main__':
    main()
