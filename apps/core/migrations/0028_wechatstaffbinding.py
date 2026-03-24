from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0027_sku_qiniu_image_keys'),
    ]

    operations = [
        migrations.CreateModel(
            name='WechatStaffBinding',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('is_active', models.BooleanField(default=True, verbose_name='是否启用')),
                ('bound_at', models.DateTimeField(auto_now_add=True, verbose_name='绑定时间')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='更新时间')),
                ('customer', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='staff_binding', to='core.wechatcustomer', verbose_name='微信客户身份')),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='wechat_staff_binding', to='core.user', verbose_name='后台用户')),
            ],
            options={
                'verbose_name': '微信员工绑定',
                'verbose_name_plural': '微信员工绑定',
                'db_table': 'wechat_staff_bindings',
            },
        ),
    ]
