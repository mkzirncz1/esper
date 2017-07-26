# -*- coding: utf-8 -*-
# Generated by Django 1.10 on 2017-07-26 16:50
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('query', '0005_auto_20170721_1412'),
    ]

    operations = [
        migrations.CreateModel(
            name='FrameLabel',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=256)),
            ],
        ),
        migrations.AddField(
            model_name='frame',
            name='labels',
            field=models.ManyToManyField(to='query.FrameLabel'),
        ),
    ]
