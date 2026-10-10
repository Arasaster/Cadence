from django.contrib import admin

from .models import ArchiveStats, Plan, Profile, Task

admin.site.register(Profile)
admin.site.register(Plan)
admin.site.register(Task)
admin.site.register(ArchiveStats)
