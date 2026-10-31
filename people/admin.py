from django.contrib import admin

from .models import Person


@admin.register(Person)
class PersonAdmin(admin.ModelAdmin):
    """Read-only, superusers only. The product has no screen for people yet (decision 0002)."""

    list_display = ("full_name", "church", "email", "created_from", "user", "created_at")
    list_filter = ("church", "created_from")
    search_fields = ("first_name", "last_name", "email")
    readonly_fields = [f.name for f in Person._meta.fields]
    actions = None

    def has_view_permission(self, request, obj=None):
        return bool(request.user.is_superuser)

    def has_module_permission(self, request):
        return bool(request.user.is_superuser)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
