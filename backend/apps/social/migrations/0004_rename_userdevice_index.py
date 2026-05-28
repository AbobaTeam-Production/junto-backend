from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('social', '0003_userdevice'),
    ]

    operations = [
        migrations.RenameIndex(
            model_name='userdevice',
            new_name='user_device_user_id_4ec133_idx',
            old_name='user_devices_user_id_idx',
        ),
    ]
