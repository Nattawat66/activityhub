from django.db.models.signals import post_save, pre_save
from django.db import models
from django.dispatch import receiver
from django.utils import timezone
from datetime import timedelta

from post.models import Post
from activity_register.models import ActivityRegistration
from .models import Notification


def _capacity_status_text(post: Post, reg_count: int) -> str:
    cap = getattr(post, "slots_available", None)
    if cap is None or cap <= 0:
        return "กิจกรรมนี้ไม่จำกัดจำนวน"

    remaining = cap - reg_count
    if remaining <= 0:
        return f"ตอนนี้กิจกรรมเต็มแล้ว (สมัคร {reg_count}/{cap})"
    return f"ตอนนี้สมัครแล้ว {reg_count}/{cap} เหลือ {remaining} ที่"


def _registrant_names(post: Post, limit=10):
    regs = (
        ActivityRegistration.objects.filter(post=post, status=ActivityRegistration.Status.ACTIVE)
        .select_related('user')[:limit]
    )
    names = [r.user.get_full_name() or r.user.email for r in regs if r.user]
    return ", ".join(names)


def _schedule_reminders_for_post(post: Post):
    """Create scheduled Notification rows for a post:
    - organizer: 3 days and 1 day before (OWNER_STATUS_REMINDER)
    - savers: 3 days before (SAVED_REMINDER)
    - registrants: 1 day before (REGISTER_REMINDER)
    """
    if not post.event_date:
        return
    today = timezone.localdate()
    event_date = post.event_date.date() if hasattr(post.event_date, 'date') else post.event_date

    # helper to create message including status and current count/names
    cap = getattr(post, 'slots_available', None)
    active_count = ActivityRegistration.objects.filter(post=post, status=ActivityRegistration.Status.ACTIVE).count()
    status_text = _capacity_status_text(post, active_count)
    names = _registrant_names(post)

    # Organizer reminders (3d and 1d)
    for days_before in (3, 1):
        trigger = event_date - timedelta(days=days_before)
        if trigger >= today:
            Notification.objects.get_or_create(
                user=post.organizer,
                post=post,
                kind=Notification.Kind.OWNER_STATUS_REMINDER,
                trigger_date=trigger,
                defaults={
                    'title': post.title,
                    'message': f"เตือนเจ้าของกิจกรรม {days_before} วันก่อนเริ่ม:\n{status_text}\n",
                    'link_url': f"/post/{post.id}/",
                },
            )

    # Savers (those who saved/bookmarked) — 3 days + 1 day before
    # ข้ามผู้ที่สมัครแล้ว (ACTIVE) เพราะจะได้รับ REGISTER_REMINDER แทน
    registered_user_ids = set(
        ActivityRegistration.objects.filter(post=post, status=ActivityRegistration.Status.ACTIVE)
        .values_list('user_id', flat=True)
    )
    saved_user_ids = post.saves.values_list('pk', flat=True)
    for days_before in (3, 1):
        trigger_saved = event_date - timedelta(days=days_before)
        if trigger_saved >= today:
            for uid in saved_user_ids:
                if uid in registered_user_ids:
                    continue
                Notification.objects.get_or_create(
                    user_id=uid,
                    post=post,
                    kind=Notification.Kind.SAVED_REMINDER,
                    trigger_date=trigger_saved,
                    defaults={
                        'title': post.title,
                        'message': f"กิจกรรมที่คุณจัดเก็บจะเริ่มในอีก {days_before} วัน คุณยังสามารถสมัครได้\n{_capacity_status_text(post, active_count)}",
                        'link_url': f"/post/{post.id}/",
                    },
                )

    # Registrants — 1 day before (แจ้งเสมอไม่ว่ากิจกรรมจะเต็มหรือไม่ เพราะเขาสมัครแล้ว)
    trigger_reg = event_date - timedelta(days=1)
    if trigger_reg >= today:
        reg_user_ids = (
            ActivityRegistration.objects.filter(post=post, user__isnull=False, status=ActivityRegistration.Status.ACTIVE)
            .values_list('user_id', flat=True)
            .distinct()
        )
        for uid in reg_user_ids:
            Notification.objects.get_or_create(
                user_id=uid,
                post=post,
                kind=Notification.Kind.REGISTER_REMINDER,
                trigger_date=trigger_reg,
                defaults={
                    'title': post.title,
                    'message': f"กิจกรรมที่คุณสมัครจะเริ่มในอีก 1 วัน: {post.title}",
                    'link_url': f"/post/{post.id}/",
                },
            )


def _schedule_reminder_for_registration(reg: ActivityRegistration):
    """Schedule a 1-day-before reminder for a specific registrant."""
    post = reg.post
    if not post or not post.event_date or not reg.user:
        return
    today = timezone.localdate()
    event_date = post.event_date.date() if hasattr(post.event_date, 'date') else post.event_date
    trigger = event_date - timedelta(days=1)
    if trigger < today:
        return
    Notification.objects.get_or_create(
        user=reg.user,
        post=post,
        kind=Notification.Kind.REGISTER_REMINDER,
        trigger_date=trigger,
        defaults={
            'title': post.title,
            'message': f"กิจกรรมที่คุณสมัครจะเริ่มในอีก 1 วัน: {post.title}",
            'link_url': f"/post/{post.id}/",
        },
    )


# -------------------------
# (2) แจ้งผู้สร้างทันทีเมื่อกิจกรรมเต็ม
# -------------------------

# -------------------------
# (2) แจ้งผู้สร้างทันทีเมื่อกิจกรรมเต็ม และแจ้งเมื่อมีคนยกเลิก
# -------------------------
from django.db.models.signals import post_save
@receiver(post_save, sender=ActivityRegistration)
def notify_owner_when_full_or_cancel(sender, instance: ActivityRegistration, created, **kwargs):
    post = instance.post
    if not post or post.is_deleted or post.is_hidden or post.status != "APPROVED":
        return

    today = timezone.localdate()

    # If a new active registration was created, schedule 1-day-before reminder for that registrant
    if created and instance.status == ActivityRegistration.Status.ACTIVE:
        _schedule_reminder_for_registration(instance)

    # แจ้งเตือนเมื่อมีคนยกเลิก (CANCELED)
    if instance.status == ActivityRegistration.Status.CANCELED:
        Notification.objects.get_or_create(
            user=post.organizer,
            post=post,
            kind=Notification.Kind.SYSTEM,
            trigger_date=today,
            defaults={
                "title": post.title,
                "message": f"มีผู้สมัครยกเลิกการเข้าร่วมกิจกรรม: {instance.user.get_full_name() if instance.user else ''}",
                "link_url": f"/post/{post.id}/",
            },
        )
        # remove any scheduled 1-day reminder for this user
        if instance.user:
            Notification.objects.filter(
                user=instance.user, post=post, kind=Notification.Kind.REGISTER_REMINDER
            ).delete()

    # แจ้งเตือนเมื่อกิจกรรมเต็ม (นับเฉพาะ ACTIVE)
    cap = getattr(post, "slots_available", None)
    if cap is None or cap <= 0:
        return
    # นับเฉพาะ ACTIVE registrations เท่านั้น
    active_count = ActivityRegistration.objects.filter(post=post, status=ActivityRegistration.Status.ACTIVE).count()
    if active_count < cap:
        return
    status_text = _capacity_status_text(post, active_count)
    Notification.objects.get_or_create(
        user=post.organizer,
        post=post,
        kind=Notification.Kind.OWNER_FULL,
        trigger_date=today,
        defaults={
            "title": post.title,
            "message": f"กิจกรรมของคุณเต็มแล้ว\n{status_text}",
            "link_url": f"/post/{post.id}/",
        },
    )
    # notify savers/bookmarkers immediately that the activity is full
    saved_user_ids = post.saves.values_list('pk', flat=True)
    names = _registrant_names(post)
    for uid in saved_user_ids:
        if uid == getattr(post.organizer, 'pk', None):
            continue
        Notification.objects.get_or_create(
            user_id=uid,
            post=post,
            kind=Notification.Kind.SAVED_REMINDER,
            trigger_date=today,
            defaults={
                'title': post.title,
                'message': f"กิจกรรมเต็มแล้ว\n{status_text}\nผู้สมัคร: {names}",
                'link_url': f"/post/{post.id}/",
            },
        )


# -------------------------
# (4) แจ้งผู้สมัคร + ผู้จัดเก็บ เมื่อเจ้าของแก้โพสต์/เปลี่ยนวันที่
# -------------------------
@receiver(pre_save, sender=Post)
def snapshot_old_post(sender, instance: Post, **kwargs):
    if not instance.pk:
        instance._old = None
        return
    try:
        instance._old = Post.objects.get(pk=instance.pk)
    except Post.DoesNotExist:
        instance._old = None


@receiver(post_save, sender=Post)
def notify_users_when_post_updated(sender, instance: Post, created, **kwargs):
    """
    แจ้งเตือนเมื่อโพสต์มีการเปลี่ยนแปลง:
    - แก้ไข → แจ้งผู้สมัคร + ผู้จัดเก็บ
    - ซ่อน/ลบ (โดยแอดมิน) → แจ้งผู้สมัคร + เจ้าของโพสต์ด้วย
    """
    if created:
        # หากโพสต์ยังไม่ถูกอนุมัติ/ถูกซ่อน/ถูกลบ -> ไม่ส่งการแจ้งเตือน
        if instance.is_deleted or instance.is_hidden or instance.status != "APPROVED":
            return

        today = timezone.localdate()

        # แจ้งเจ้าของว่าโพสต์ของเขาได้รับการอนุมัติและเผยแพร่แล้ว
        Notification.objects.get_or_create(
            user=instance.organizer,
            post=instance,
            kind=Notification.Kind.OWNER_POSTED,
            trigger_date=today,
            defaults={
                "title": instance.title,
                "message": f"โพสต์กิจกรรมของคุณได้รับการอนุมัติและเผยแพร่แล้ว: \"{instance.title}\"",
                "link_url": f"/post/{instance.id}/",
            },
        )

        # แจ้งผู้ติดตาม (followers) ว่ามีกิจกรรมใหม่
        _notify_followers_new_post(instance)
        # schedule reminders for organizer/savers/registrants
        _schedule_reminders_for_post(instance)
        return

    old = getattr(instance, "_old", None)
    if old is None:
        return

    today = timezone.localdate()

    # ✅ ตรวจสอบว่าถูกลบ/ซ่อนหรือไม่ (admin action)
    was_deleted = not old.is_deleted and instance.is_deleted
    was_hidden = not old.is_hidden and instance.is_hidden

    if was_deleted or was_hidden:
        action_text = "ถูกลบ" if was_deleted else "ถูกซ่อน"
        kind = Notification.Kind.POST_DELETED if was_deleted else Notification.Kind.POST_HIDDEN

        # แจ้งผู้สมัคร
        reg_user_ids = (
            ActivityRegistration.objects.filter(post=instance, user__isnull=False)
            .values_list("user_id", flat=True)
            .distinct()
        )
        # แจ้งเจ้าของโพสต์ด้วย (กรณีแอดมินลบ)
        target_ids = set(reg_user_ids)
        if instance.organizer_id:
            target_ids.add(instance.organizer_id)

        for uid in target_ids:
            Notification.objects.get_or_create(
                user_id=uid,
                post=instance,
                kind=kind,
                trigger_date=today,
                defaults={
                    "title": instance.title,
                    "message": f"กิจกรรม \"{instance.title}\" {action_text}โดยผู้ดูแลระบบ",
                    "link_url": f"/post/{instance.id}/",
                },
            )
        return

    # หากสถานะถูกเปลี่ยนเป็น APPROVED (เช่น แอดมินอนุมัติโพสต์) -> แจ้งเจ้าของและผู้ติดตาม
    if old.status != instance.status and instance.status == "APPROVED":
        if instance.is_deleted or instance.is_hidden:
            pass
        else:
            today = timezone.localdate()
            Notification.objects.get_or_create(
                user=instance.organizer,
                post=instance,
                kind=Notification.Kind.OWNER_POSTED,
                trigger_date=today,
                defaults={
                    "title": instance.title,
                    "message": f"โพสต์กิจกรรมของคุณได้รับการอนุมัติและเผยแพร่แล้ว: \"{instance.title}\"",
                    "link_url": f"/post/{instance.id}/",
                },
            )
            _notify_followers_new_post(instance)
            _schedule_reminders_for_post(instance)

    # ✅ ตรวจสอบการแก้ไขฟิลด์
    changed = []

    if old.title != instance.title:
        changed.append("ชื่อกิจกรรม")
    if old.location != instance.location:
        changed.append("สถานที่")
    if old.description != instance.description:
        changed.append("รายละเอียด")
    if old.category != instance.category:
        changed.append("ประเภทกิจกรรม")
    if old.slots_available != instance.slots_available:
        changed.append("จำนวนที่รับสมัคร")
    if old.fee != instance.fee:
        changed.append("ค่าใช้จ่าย")
    if old.allow_register != instance.allow_register:
        changed.append("สถานะการเปิดรับสมัคร")
    if old.event_date != instance.event_date:
        old_dt = old.event_date.strftime("%d/%m/%Y %H:%M") if old.event_date else "-"
        new_dt = instance.event_date.strftime("%d/%m/%Y %H:%M") if instance.event_date else "-"
        changed.append(f"วันเวลา (เดิม {old_dt} → ใหม่ {new_dt})")

        # reschedule reminders for this post when event date changed
        Notification.objects.filter(
            post=instance,
            kind__in=(
                Notification.Kind.REGISTER_REMINDER,
                Notification.Kind.SAVED_REMINDER,
                Notification.Kind.OWNER_STATUS_REMINDER,
            ),
        ).delete()
        _schedule_reminders_for_post(instance)

    if not changed:
        return

    change_text = ", ".join(changed)

    # ผู้สมัคร
    reg_user_ids = (
        ActivityRegistration.objects.filter(post=instance, user__isnull=False)
        .values_list("user_id", flat=True)
        .distinct()
    )

    # ผู้จัดเก็บ (M2M)
    saved_user_ids = instance.saves.values_list("pk", flat=True)

    target_ids = set(reg_user_ids) | set(saved_user_ids)
    if instance.organizer_id in target_ids:
        target_ids.remove(instance.organizer_id)

    for uid in target_ids:
        Notification.objects.get_or_create(
            user_id=uid,
            post=instance,
            kind=Notification.Kind.POST_UPDATED,
            trigger_date=today,
            defaults={
                "title": instance.title,
                "message": f"เจ้าของกิจกรรมได้แก้ไขโพสต์: {change_text}\n",
                "link_url": f"/post/{instance.id}/",
            },
        )


# -------------------------
# (5) แจ้งผู้ติดตามเมื่อมีโพสต์ใหม่
# -------------------------
def _notify_followers_new_post(post: Post):
    """เมื่อผู้ใช้สร้างโพสต์ใหม่ แจ้งเตือนผู้ที่ติดตามอยู่"""
    try:
        from users.models import Profile
        organizer_profile = Profile.objects.get(user=post.organizer)
        # followers ของ organizer คือ Profile ที่อยู่ใน followers M2M
        follower_profiles = organizer_profile.followers.all()

        today = timezone.localdate()

        for fp in follower_profiles:
            Notification.objects.get_or_create(
                user=fp.user,
                post=post,
                kind=Notification.Kind.FOLLOWER_NEW_POST,
                trigger_date=today,
                defaults={
                    "title": post.title,
                    "message": f"{post.organizer.get_full_name() or post.organizer.email} ได้โพสต์กิจกรรมใหม่: \"{post.title}\"",
                    "link_url": f"/post/{post.id}/",
                },
            )
    except Exception:
        pass


# -------------------------
# (6) แจ้งเตือนแอดมิน/approver เมื่อมีโพสต์ใหม่รออนุมัติ หรือมีรายงานใหม่
# -------------------------
def _push_realtime(user, notif):
    """ส่ง push ผ่าน channel layer ทันที"""
    try:
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer

        channel_layer = get_channel_layer()
        payload = {
            'id': notif.id,
            'kind': notif.kind,
            'title': notif.title,
            'message': notif.message,
            'link_url': notif.link_url,
            'is_read': notif.is_read,
        }
        safe_email = str(user.pk).replace('@', '_').replace('.', '_')
        async_to_sync(channel_layer.group_send)(
            # f"notif_{user.id}", {"type": "notify", "payload": payload}
            f"notif_{safe_email}", {"type": "notify", "payload": payload}
        )
    except Exception:
        pass


def _get_admin_approver_users():
    """คืนรายชื่อผู้ใช้ที่มี role ADMIN/APPROVER หรือ is_superuser"""
    from users.models import User
    return User.objects.filter(
        is_active=True,
        is_deleted=False,
    ).filter(
        models.Q(role__in=[User.Role.ADMIN, User.Role.APPROVER]) | models.Q(is_superuser=True)
    )


def notify_admins_new_post(post):
    """แจ้งเตือนแอดมิน/approver ว่ามีโพสต์ใหม่รออนุมัติ"""
    admins = _get_admin_approver_users()
    organizer_name = post.organizer.get_full_name() or post.organizer.email

    for admin_user in admins:
        notif = Notification.objects.create(
            user=admin_user,
            post=post,
            kind=Notification.Kind.ADMIN_NEW_POST,
            title="มีกิจกรรมใหม่รออนุมัติ",
            message=f"{organizer_name} ส่งคำขอโพสต์กิจกรรม \"{post.title}\" รออนุมัติ",
            link_url="/approver/?main=approval",
        )
        _push_realtime(admin_user, notif)


def notify_admins_new_report(report_type, reporter, target_name, detail=""):
    """แจ้งเตือนแอดมิน/approver ว่ามีรายงานใหม่"""
    admins = _get_admin_approver_users()
    reporter_name = reporter.get_full_name() or reporter.email

    if report_type == "post":
        title = "มีรายงานโพสต์ใหม่"
        message = f"{reporter_name} รายงานโพสต์ \"{target_name}\""
    else:
        title = "มีรายงานบัญชีใหม่"
        message = f"{reporter_name} รายงานบัญชี {target_name}"

    if detail:
        message += f" — เหตุผล: {detail[:80]}"

    for admin_user in admins:
        notif = Notification.objects.create(
            user=admin_user,
            kind=Notification.Kind.ADMIN_NEW_REPORT,
            title=title,
            message=message,
            link_url="/approver/?main=manage&sub=reports",
        )
        _push_realtime(admin_user, notif)


# -------------------------
# (7) แจ้งเตือนข้อความแชทใหม่
# -------------------------
def notify_chat_message(sender_user, room, message_preview=""):
    """เรียกใช้จาก chat consumer/view เมื่อมีข้อความใหม่"""
    from chat.models import ChatMembership
    today = timezone.localdate()

    members = ChatMembership.objects.filter(room=room).exclude(user=sender_user)
    room_name = room.name or "ห้องแชท"

    for m in members:
        # ไม่ใช้ get_or_create กับ trigger_date เพื่อให้แจ้งทุกครั้ง
        notif = Notification.objects.create(
            user=m.user,
            kind=Notification.Kind.CHAT_MESSAGE,
            title=f"ข้อความใหม่จาก {sender_user.get_full_name() or sender_user.email}",
            message=message_preview[:100] if message_preview else "ส่งข้อความมาหาคุณ",
            link_url=f"/chat/dm/{sender_user.email}/" if room.room_type == "DM" else f"/chat/activity/{room.post_id}/" if room.post_id else "/chat/inbox/",
        )

        # ส่ง push ทันทีผ่าน channel layer ไปยังกลุ่มผู้ใช้ (notif_<user_id>)
        try:
            from asgiref.sync import async_to_sync
            from channels.layers import get_channel_layer

            channel_layer = get_channel_layer()
            payload = {
                'id': notif.id,
                'kind': notif.kind,
                'title': notif.title,
                'message': notif.message,
                'link_url': notif.link_url,
                'is_read': notif.is_read,
            }
            # async_to_sync(channel_layer.group_send)(f"notif_{m.user.id}", {"type": "notify", "payload": payload})
            safe_email = str(m.user.pk).replace('@', '_').replace('.', '_')
            async_to_sync(channel_layer.group_send)(
                f"notif_{safe_email}", {"type": "notify", "payload": payload}
            )
        except Exception:
            pass
