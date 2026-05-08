from django.contrib import admin

from .models import Genre, Movie, MoodEntry, MoodList, WatchIntent


@admin.register(Genre)
class GenreAdmin(admin.ModelAdmin):
    list_display = ('slug', 'name_ru')
    search_fields = ('slug', 'name_ru')


@admin.register(Movie)
class MovieAdmin(admin.ModelAdmin):
    list_display = ('title_ru', 'year', 'kp_rating', 'is_series', 'kp_id')
    search_fields = ('title_ru', 'title_orig')
    list_filter = ('is_series', 'year')
    readonly_fields = ('created_at', 'updated_at')


class MoodEntryInline(admin.TabularInline):
    model = MoodEntry
    extra = 0
    autocomplete_fields = ('movie',)


@admin.register(MoodList)
class MoodListAdmin(admin.ModelAdmin):
    list_display = ('title', 'slug', 'position', 'hue')
    inlines = [MoodEntryInline]
    prepopulated_fields = {'slug': ('title',)}


@admin.register(WatchIntent)
class WatchIntentAdmin(admin.ModelAdmin):
    list_display = ('user', 'movie', 'created_at')
    autocomplete_fields = ('user', 'movie')
