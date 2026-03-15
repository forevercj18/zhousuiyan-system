from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0020_user_custom_permissions'),
    ]

    operations = [
        migrations.CreateModel(
            name='PermissionTemplate',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=50, unique=True, verbose_name='模板名称')),
                ('base_role', models.CharField(choices=[('admin', '超级管理员'), ('manager', '业务经理'), ('warehouse_manager', '仓库主管'), ('warehouse_staff', '仓库操作员'), ('customer_service', '客服')], default='warehouse_staff', max_length=20, verbose_name='基础角色')),
                ('description', models.CharField(blank=True, max_length=200, verbose_name='说明')),
                ('modules', models.JSONField(blank=True, default=list, verbose_name='模块权限')),
                ('actions', models.JSONField(blank=True, default=list, verbose_name='操作权限')),
                ('action_permissions', models.JSONField(blank=True, default=list, verbose_name='业务动作权限')),
                ('is_active', models.BooleanField(default=True, verbose_name='是否启用')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='创建时间')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='更新时间')),
            ],
            options={
                'verbose_name': '权限模板',
                'verbose_name_plural': '权限模板',
                'db_table': 'permission_templates',
                'ordering': ['name'],
            },
        ),
    ]
