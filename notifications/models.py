from django.conf import settings
from django.db import models
from post.models import Post


class Notification(models.Model):
    class Kind(models.TextChoices):
        REGISTER_REMINDER = "REGISTER_REMINDER", "แจ้งเตือนก่อนกิจกรรม (ผู้สมัคร)"
        SAVED_REMINDER = "SAVED_REMINDER", "แจ้งเตือนก่อนกิจกรรม (ผู้บันทึก)"
        OWNER_STATUS_REMINDER = "OWNER_STATUS_REMINDER", "แจ้งเตือนสถานะผู้สมัคร (ผู้สร้างกิจกรรม)"
        OWNER_FULL = "OWNER_FULL", "แจ้งเตือนกิจกรรมเต็ม (ผู้สร้างกิจกรรม)"
        SAVED_FULL = "SAVED_FULL", "แจ้งเตือนกิจกรรมเต็ม (ผู้บันทึก)"
        POST_UPDATED = "POST_UPDATED", "แจ้งเตือนเมื่อโพสต์มีการเปลี่ยนแปลง"
        POST_DELETED = "POST_DELETED", "แจ้งเตือนเมื่อโพสต์ถูกลบ"
        POST_HIDDEN = "POST_HIDDEN", "แจ้งเตือนเมื่อโพสต์ถูกซ่อน"
        OWNER_POSTED = "OWNER_POSTED", "แจ้งเตือนโพสต์ได้รับการอนุมัติ (ผู้สร้าง)"
        FOLLOWER_NEW_POST = "FOLLOWER_NEW_POST", "แจ้งเตือนเมื่อคนที่ติดตามโพสต์ใหม่"
        CHAT_MESSAGE = "CHAT_MESSAGE", "แจ้งเตือนข้อความแชทใหม่"
        ADMIN_NEW_POST = "ADMIN_NEW_POST", "แจ้งเตือนมีโพสต์ใหม่รออนุมัติ (แอดมิน)"
        ADMIN_NEW_REPORT = "ADMIN_NEW_REPORT", "แจ้งเตือนมีรายงานใหม่ (แอดมิน)"
        SYSTEM = "SYSTEM", "ระบบ"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    post = models.ForeignKey(
        Post,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="notifications",
    )

    kind = models.CharField(max_length=40, choices=Kind.choices, default=Kind.SYSTEM)
    title = models.CharField(max_length=255, blank=True, default="")
    message = models.TextField()
    link_url = models.CharField(max_length=255, blank=True, default="")

    trigger_date = models.DateField(null=True, blank=True)  # วันที่ที่ “ควรเด้ง” (กันซ้ำ)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "kind", "post", "trigger_date"],
                name="uniq_user_kind_post_triggerdate",
            )
        ]

    def __str__(self):
        return f"{self.user_id} - {self.kind} - {self.title}"
