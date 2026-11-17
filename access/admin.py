from django.contrib import admin

from .models import RoleAssignment


@admin.register(RoleAssignment)
class RoleAssignmentAdmin(admin.ModelAdmin):
    """Read-only, superusers only: rows are written by their source's service, never by hand."""

    list_display = ("subject_user", "church", "scope_type", "scope_id", "role", "status", "start_at", "end_at")
    list_filter = ("church", "scope_type", "status")
    readonly_fields = [f.name for f in RoleAssignment._meta.fields]
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
