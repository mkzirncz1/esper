# -*- coding: utf-8 -*-
# Generated by Django 1.11 on 2018-09-26 15:14
from __future__ import unicode_literals

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('query', '0013_auto_20180825_2234'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='speaker',
            name='gender',
        ),
        migrations.RemoveField(
            model_name='speaker',
            name='identity',
        ),
        migrations.RemoveField(
            model_name='speaker',
            name='labeler',
        ),
        migrations.RemoveField(
            model_name='speaker',
            name='video',
        ),
        migrations.RemoveField(
            model_name='identity',
            name='thing',
        ),
        migrations.AddField(
            model_name='faceidentity',
            name='identity2',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, to='query.Identity'),
        ),
        migrations.AlterField(
            model_name='identity',
            name='name',
            field=models.CharField(default='', max_length=256),
            preserve_default=False,
        ),
        migrations.DeleteModel(
            name='Speaker',
        ),
    ]
