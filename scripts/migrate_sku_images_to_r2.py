r"""
迁移 SKU 本地图片到 Cloudflare R2

用法：
    .\.venv\Scripts\python scripts\migrate_sku_images_to_r2.py --dry-run
    .\.venv\Scripts\python scripts\migrate_sku_images_to_r2.py
    .\.venv\Scripts\python scripts\migrate_sku_images_to_r2.py --overwrite
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
from apps.core.services.storage_service import StorageService  # noqa: E402


def migrate_sku_covers(*, dry_run=False, overwrite=False):
    migrated = 0
    skipped = 0
    failed = 0
    for sku in SKU.objects.exclude(image='').exclude(image__isnull=True).order_by('id'):
        if sku.image_key and not overwrite:
            skipped += 1
            continue
        if not getattr(sku, 'image', None):
            skipped += 1
            continue
        local_path = sku.image.path
        target_key = StorageService.generate_sku_upload_key(os.path.basename(local_path))
        if dry_run:
            print(f'[DRY-RUN] SKU {sku.code} -> {target_key}')
            migrated += 1
            continue
        try:
            result = StorageService.upload_local_file(local_path, target_key)
            sku.image_key = result['key']
            sku.save(update_fields=['image_key', 'updated_at'])
            print(f'[OK] SKU {sku.code} -> {result["key"]}')
            migrated += 1
        except Exception as exc:
            print(f'[FAIL] SKU {sku.code}: {exc}')
            failed += 1
    return migrated, skipped, failed


def migrate_sku_gallery(*, dry_run=False, overwrite=False):
    migrated = 0
    skipped = 0
    failed = 0
    for image in SKUImage.objects.exclude(image='').exclude(image__isnull=True).select_related('sku').order_by('id'):
        if image.image_key and not overwrite:
            skipped += 1
            continue
        if not getattr(image, 'image', None):
            skipped += 1
            continue
        local_path = image.image.path
        target_key = StorageService.generate_sku_upload_key(os.path.basename(local_path))
        if dry_run:
            print(f'[DRY-RUN] SKUImage #{image.id} {image.sku.code} -> {target_key}')
            migrated += 1
            continue
        try:
            result = StorageService.upload_local_file(local_path, target_key)
            image.image_key = result['key']
            image.save(update_fields=['image_key'])
            print(f'[OK] SKUImage #{image.id} {image.sku.code} -> {result["key"]}')
            migrated += 1
        except Exception as exc:
            print(f'[FAIL] SKUImage #{image.id} {image.sku.code}: {exc}')
            failed += 1
    return migrated, skipped, failed


def main():
    parser = argparse.ArgumentParser(description='迁移 SKU 本地图片到 Cloudflare R2')
    parser.add_argument('--dry-run', action='store_true', help='只输出迁移计划，不实际上传')
    parser.add_argument('--overwrite', action='store_true', help='覆盖已存在 image_key 的记录')
    args = parser.parse_args()

    if not StorageService.is_storage_enabled():
        raise SystemExit('Cloudflare R2 未配置，请先设置 R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY / R2_BUCKET / R2_ENDPOINT / R2_PUBLIC_DOMAIN')

    cover_result = migrate_sku_covers(dry_run=args.dry_run, overwrite=args.overwrite)
    gallery_result = migrate_sku_gallery(dry_run=args.dry_run, overwrite=args.overwrite)

    print('\n=== 迁移完成 ===')
    print(f'SKU 主图：迁移 {cover_result[0]}，跳过 {cover_result[1]}，失败 {cover_result[2]}')
    print(f'SKU 画廊：迁移 {gallery_result[0]}，跳过 {gallery_result[1]}，失败 {gallery_result[2]}')


if __name__ == '__main__':
    main()
