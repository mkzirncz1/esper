# -*- coding: utf-8 -*-
# Generated by Django 1.11 on 2018-01-28 16:59
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('query', '0020_tvnews_shot_in_commercial'),
    ]

    operations = [
        migrations.AddField(
            model_name='tvnews_video',
            name='commercials_labeled',
            field=models.BooleanField(default=False),
        ),
    ]