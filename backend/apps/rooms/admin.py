from django.contrib import admin
from .models import Room, RoomMember, ChatMessage


class RoomMemberInline(admin.TabularInline):
    model = RoomMember
    extra = 0
    readonly_fields = ('joined_at',)


@admin.register(Room)
class RoomAdmin(admin.ModelAdmin):
    list_display = ('invite_code', 'host', 'status', 'created_at', 'expires_at')
    list_filter = ('status',)
    search_fields = ('invite_code', 'host__username')
    inlines = [RoomMemberInline]


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = ('user', 'room', 'text', 'sent_at')
    list_filter = ('room',)
