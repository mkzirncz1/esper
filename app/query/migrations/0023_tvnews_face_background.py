# -*- coding: utf-8 -*-
# Generated by Django 1.11 on 2018-02-13 01:28
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('query', '0022_auto_20180205_1639'),
    ]

    operations = [
        migrations.AddField(
            model_name='tvnews_face',
            name='background',
            field=models.BooleanField(default=False),
        ),
    ]