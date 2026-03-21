from django.contrib import admin
from .models import MediaItem


@admin.register(MediaItem)
class MediaItemAdmin(admin.ModelAdmin):
    list_display = ('title', 'room', 'source_type', 'status', 'progress', 'created_at')
    list_filter = ('source_type', 'status')
    search_fields = ('title',)
    readonly_fields = ('id', 'created_at')
