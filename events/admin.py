from django.contrib import admin

from .models import (
    AuditEvent,
    Event,
    EventMembership,
    EventParticipant,
    Interview,
    InterviewStation,
    QuestionnaireVersion,
    ReportSnapshot,
    TutorAssignment,
)


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ("name", "church", "kind", "status", "starts_on")
    list_filter = ("status", "kind")


@admin.register(EventMembership)
class EventMembershipAdmin(admin.ModelAdmin):
    list_display = ("user", "event", "role", "can_view_sensitive", "is_active")
    list_filter = ("role", "can_view_sensitive", "is_active", "event")


@admin.register(EventParticipant)
class EventParticipantAdmin(admin.ModelAdmin):
    # Only registry data. Answers are intentionally not exposed in the Django admin.
    list_display = ("number", "full_name", "event", "room", "interview_status", "is_active")
    list_filter = ("event", "interview_status", "is_active")
    search_fields = ("full_name",)


@admin.register(Interview)
class InterviewAdmin(admin.ModelAdmin):
    list_display = ("participant", "event", "status", "mode", "started_at", "completed_at")
    list_filter = ("event", "status", "mode")
    readonly_fields = [f.name for f in Interview._meta.fields]

    def has_add_permission(self, request):
        return False


admin.site.register(TutorAssignment)
admin.site.register(InterviewStation)
admin.site.register(QuestionnaireVersion)


@admin.register(ReportSnapshot)
class ReportSnapshotAdmin(admin.ModelAdmin):
    list_display = ("event", "scope_label", "cutoff_at", "trigger", "status")
    readonly_fields = [f.name for f in ReportSnapshot._meta.fields]


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "actor_label", "event", "action", "summary")
    list_filter = ("action", "event")
    readonly_fields = [f.name for f in AuditEvent._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
