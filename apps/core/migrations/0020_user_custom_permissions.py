from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0019_alter_assemblyorder_status_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='custom_action_permissions',
            field=models.JSONField(blank=True, default=list, verbose_name='自定义业务动作权限'),
        ),
        migrations.AddField(
            model_name='user',
            name='custom_actions',
            field=models.JSONField(blank=True, default=list, verbose_name='自定义操作权限'),
        ),
        migrations.AddField(
            model_name='user',
            name='custom_modules',
            field=models.JSONField(blank=True, default=list, verbose_name='自定义模块权限'),
        ),
        migrations.AddField(
            model_name='user',
            name='permission_mode',
            field=models.CharField(
                choices=[('role', '固定角色'), ('custom', '自定义搭配')],
                default='role',
                max_length=20,
                verbose_name='权限模式',
            ),
        ),
    ]
