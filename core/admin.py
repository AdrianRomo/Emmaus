from django.contrib import admin

from .models import (
    AIDraft,
    Church,
    DiscipleshipPath,
    Enrollment,
    FollowUpRequest,
    Invitation,
    Lesson,
    LessonProgress,
    ParticipantProfile,
    ParticipantQuestion,
    PastoralNote,
    QuestionResponse,
    ReflectionAnswer,
    ReflectionQuestion,
    UserChurchRole,
)


class ReflectionQuestionInline(admin.TabularInline):
    model = ReflectionQuestion
    extra = 0


class LessonInline(admin.TabularInline):
    model = Lesson
    extra = 0


@admin.register(Church)
class ChurchAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "contact_email", "active", "created_at")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(UserChurchRole)
class UserChurchRoleAdmin(admin.ModelAdmin):
    list_display = ("user", "church", "role")
    list_filter = ("role", "church")
    search_fields = ("user__email", "user__first_name", "user__last_name", "church__name")


@admin.register(Invitation)
class InvitationAdmin(admin.ModelAdmin):
    list_display = ("email", "church", "role", "accepted_at", "expires_at", "created_at")
    list_filter = ("role", "church", "accepted_at")
    search_fields = ("email",)


@admin.register(ParticipantProfile)
class ParticipantProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "church", "assigned_mentor", "preferred_contact_method", "date_joined")
    list_filter = ("church", "preferred_contact_method")
    search_fields = ("user__email", "user__first_name", "user__last_name")


@admin.register(DiscipleshipPath)
class DiscipleshipPathAdmin(admin.ModelAdmin):
    list_display = ("title", "church", "is_default", "is_active", "created_at")
    list_filter = ("church", "is_default", "is_active")
    inlines = [LessonInline]


@admin.register(Lesson)
class LessonAdmin(admin.ModelAdmin):
    list_display = ("title", "path", "order", "is_active")
    list_filter = ("path__church", "is_active")
    inlines = [ReflectionQuestionInline]


admin.site.register(ReflectionQuestion)
admin.site.register(Enrollment)
admin.site.register(LessonProgress)
admin.site.register(ReflectionAnswer)
admin.site.register(ParticipantQuestion)
admin.site.register(QuestionResponse)
admin.site.register(FollowUpRequest)
admin.site.register(PastoralNote)
admin.site.register(AIDraft)
