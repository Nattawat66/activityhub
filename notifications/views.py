from datetime import timedelta
from django.apps import apps
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponseForbidden
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .models import Notification


def _get_registration_model():
    candidates = [
        ("activity_register", "ActivityRegistration"),
        ("activity_register", "Registration"),
        ("activity_register", "ActivityRegister"),
    ]
    for app_label, model_name in candidates:
        try:
            model = apps.get_model(app_label, model_name)
            if model:
                return model
        except LookupError:
            continue
    return None


def _capacity_status_text(post, reg_count: int | None = None) -> str:
    """
    คืนข้อความสถานะความจุ:
    - ไม่จำกัด (กรณี slots_available <= 0 หรือ None)
    - เต็มแล้ว / เหลือที่ว่าง
    """
    cap = getattr(post, "slots_available", None)
    if cap is None or cap <= 0:
        return "กิจกรรมนี้ไม่จำกัดจำนวน"

    if reg_count is None:
        # นับเฉพาะ ACTIVE registrations เท่านั้น (ไม่รวมผู้ที่ยกเลิก)
        try:
            from activity_register.models import ActivityRegistration
            reg_count = post.registrations.filter(status=ActivityRegistration.Status.ACTIVE).count()
        except Exception:
            try:
                reg_count = post.registrations.count()
            except Exception:
                reg_count = 0

    remaining = cap - reg_count
    if remaining <= 0:
        return f"ตอนนี้กิจกรรมเต็มแล้ว (สมัคร {reg_count}/{cap})"
    return f"ตอนนี้สมัครแล้ว {reg_count}/{cap} เหลือ {remaining} ที่"


def _ensure_activity_notifications(user):
    """
    สร้างแจ้งเตือนตามกติกา (lazy: สร้างตอนเปิดกระดิ่ง):
    1) ผู้บันทึก: เตือนก่อน 3 วัน และ 1 วัน + แนบสถานะที่นั่ง
    2) ผู้สมัคร: เตือนก่อน 1 วัน
    3) ผู้สร้างกิจกรรม: เตือนก่อน 2 วัน + แนบสถานะผู้สมัคร/ความจุ
    """
    today = timezone.localdate()
    d1 = today + timedelta(days=1)
    d3 = today + timedelta(days=3)

    Post = apps.get_model("post", "Post")

    def _remaining_slots(post) -> int | None:
        """
        คืนจำนวนที่เหลือ (int) หรือ None หากไม่จำกัด
        """
        cap = getattr(post, "slots_available", None)
        if cap is None or cap <= 0:
            return None
        try:
            from activity_register.models import ActivityRegistration
            reg_count = post.registrations.filter(status=ActivityRegistration.Status.ACTIVE).count()
        except Exception:
            try:
                reg_count = post.registrations.count()
            except Exception:
                reg_count = 0
        return cap - reg_count

    base_post_filter = dict(
        is_deleted=False,
        is_hidden=False,
        status="APPROVED",
    )

    # -------------------------
    # (1) Saved reminders: 3 วันก่อน + 1 วันก่อน (เฉพาะผู้จัดเก็บที่ยังไม่ได้สมัคร)
    # -------------------------
    from activity_register.models import ActivityRegistration as _AR

    for days_label, target_date in [(3, d3), (1, d1)]:
        saved_posts = user.saved_posts.filter(
            event_date__date=target_date,
            **base_post_filter,
        )
        for p in saved_posts:
            # ข้ามถ้าผู้ใช้สมัครกิจกรรมนี้แล้ว (ACTIVE) — ไม่ต้องเตือนผู้จัดเก็บ
            already_registered = _AR.objects.filter(
                user=user, post=p, status=_AR.Status.ACTIVE
            ).exists()
            if already_registered:
                continue
            remaining = _remaining_slots(p)
            # หากกิจกรรมเต็มแล้ว ไม่ต้องเตือนว่ายังสมัครได้
            if remaining is not None and remaining <= 0:
                continue
            status_text = _capacity_status_text(p)
            Notification.objects.get_or_create(
                user=user,
                post=p,
                kind=Notification.Kind.SAVED_REMINDER,
                trigger_date=target_date,
                defaults={
                    "title": p.title,
                    "message": f"กิจกรรมที่คุณจัดเก็บจะเริ่มในอีก {days_label} วัน คุณยังสามารถสมัครได้\n{status_text}",
                    "link_url": f"/post/{p.id}/",
                },
            )

    # -------------------------
    # (2) Register reminder: 1 วันก่อน (ผู้สมัคร — แจ้งเสมอไม่ว่ากิจกรรมจะเต็มหรือไม่)
    # -------------------------
    RegModel = _get_registration_model()
    if RegModel:
        reg_qs = RegModel.objects.filter(
            user=user, status=_AR.Status.ACTIVE
        ).select_related("post")
        for reg in reg_qs:
            p = getattr(reg, "post", None)
            if not p or not getattr(p, "event_date", None):
                continue
            if timezone.localdate(p.event_date) == d1 and (not p.is_deleted) and (not p.is_hidden) and (p.status == "APPROVED"):
                Notification.objects.get_or_create(
                    user=user,
                    post=p,
                    kind=Notification.Kind.REGISTER_REMINDER,
                    trigger_date=d1,
                    defaults={
                        "title": p.title,
                        "message": f"กิจกรรมที่คุณสมัครจะเริ่มในอีก 1 วัน: {p.title}",
                        "link_url": f"/post/{p.id}/",
                    },
                )

    # -------------------------
    # (3) Owner status reminder: 3 วัน + 1 วันก่อน (ผู้สร้างกิจกรรม)
    # -------------------------
    for days_label, target_date in [(3, d3), (1, d1)]:
        owner_posts = Post.objects.filter(
            organizer=user,
            event_date__date=target_date,
            **base_post_filter,
        )
        for p in owner_posts:
            try:
                reg_count = _AR.objects.filter(post=p, status=_AR.Status.ACTIVE).count()
            except Exception:
                reg_count = 0
            status_text = _capacity_status_text(p, reg_count=reg_count)
            Notification.objects.get_or_create(
                user=user,
                post=p,
                kind=Notification.Kind.OWNER_STATUS_REMINDER,
                trigger_date=target_date,
                defaults={
                    "title": p.title,
                    "message": f"เตือนเจ้าของกิจกรรม: กิจกรรมของคุณจะเริ่มในอีก {days_label} วัน\n{status_text}",
                    "link_url": f"/post/{p.id}/",
                },
            )


@login_required
@require_GET
def api_list_notifications(request):
    _ensure_activity_notifications(request.user)

    today = timezone.localdate()
    # แสดงเฉพาะแจ้งเตือนที่ถึง trigger_date แล้ว หรือไม่มี trigger_date (แจ้งทันที)
    from django.db.models import Q
    qs = (
        Notification.objects.filter(user=request.user)
        .filter(Q(trigger_date__lte=today) | Q(trigger_date__isnull=True))
        .order_by("-created_at")[:30]
    )
    data = []
    for n in qs:
        # determine post visibility for the current user so frontend can
        # show an indicator if the post was deleted/hidden/unapproved
        post_state = None
        can_view_post = True
        if n.post_id:
            try:
                p = n.post
                if getattr(p, 'is_deleted', False):
                    post_state = 'deleted'
                elif getattr(p, 'is_hidden', False):
                    post_state = 'hidden'
                elif getattr(p, 'status', None) != 'APPROVED':
                    post_state = 'unapproved'

                if post_state:
                    # only owner or superuser may view deleted/hidden/unapproved posts
                    if not (request.user.is_superuser or request.user.id == getattr(p.organizer, 'id', None)):
                        can_view_post = False
            except Exception:
                post_state = None

        data.append(
            {
                "id": n.id,
                "kind": n.kind,
                "title": n.title,
                "message": n.message,
                "link_url": n.link_url,
                "post_id": n.post_id,
                "is_read": n.is_read,
                "created_at": n.created_at.isoformat(),
                "post_state": post_state,
                "can_view_post": can_view_post,
            }
        )

    # นับเฉพาะ unread ที่ถึง trigger_date แล้ว
    from django.db.models import Q as _Q
    unread_count = Notification.objects.filter(
        user=request.user, is_read=False
    ).filter(
        _Q(trigger_date__lte=today) | _Q(trigger_date__isnull=True)
    ).count()
    return JsonResponse({"unread": unread_count, "items": data})


@login_required
@require_GET
def api_can_view_post(request):
    """Check if a post is viewable by the current user.

    Query params: ?post_id=123
    Returns: {post_state: 'deleted'|'hidden'|'unapproved'|None, can_view: bool}
    """
    post_id = request.GET.get('post_id')
    if not post_id:
        return JsonResponse({'error': 'missing post_id'}, status=400)

    try:
        Post = apps.get_model('post', 'Post')
        p = Post.objects.filter(id=post_id).first()
        if not p:
            return JsonResponse({'post_state': 'deleted', 'can_view': False})

        post_state = None
        if getattr(p, 'is_deleted', False):
            post_state = 'deleted'
        elif getattr(p, 'is_hidden', False):
            post_state = 'hidden'
        elif getattr(p, 'status', None) != 'APPROVED':
            post_state = 'unapproved'

        can_view = True
        if post_state:
            if not (request.user.is_superuser or request.user.id == getattr(p.organizer, 'id', None)):
                can_view = False

        return JsonResponse({'post_state': post_state, 'can_view': can_view})
    except Exception:
        return JsonResponse({'post_state': None, 'can_view': False})


@login_required
@require_GET
def api_chat_unread(request):
    """Return unread chat notification/message count for the current user."""
    try:
        # Prefer Notification rows for CHAT_MESSAGE
        unread_notifications = Notification.objects.filter(user=request.user, kind=Notification.Kind.CHAT_MESSAGE, is_read=False).count()
    except Exception:
        unread_notifications = 0

    # Also count unread ChatMessage rows as a fallback
    try:
        from chat.models import ChatMessage
        unread_messages = ChatMessage.objects.filter(room__members=request.user, is_read=False).exclude(sender=request.user).count()
    except Exception:
        unread_messages = 0

    # Use the max of both sources to be safe
    unread = max(unread_notifications, unread_messages)
    return JsonResponse({"chat_unread": unread})


@login_required
@require_POST
def api_mark_read(request, notif_id):
    try:
        n = Notification.objects.get(id=notif_id)
    except Notification.DoesNotExist:
        return JsonResponse({"ok": False}, status=404)

    if n.user_id != request.user.id:
        return HttpResponseForbidden("forbidden")

    n.is_read = True
    n.save(update_fields=["is_read"])
    unread_count = Notification.objects.filter(user=request.user, is_read=False).count()
    return JsonResponse({"ok": True, "unread": unread_count})


@login_required
@require_POST
def mark_notification_as_read(request):
    notif_id = request.POST.get("notif_id")
    if not notif_id:
        return JsonResponse({"success": False, "error": "Missing notification id"}, status=400)
    try:
        notif = Notification.objects.get(id=notif_id, user=request.user)
        notif.is_read = True
        notif.save(update_fields=["is_read"])
        # return updated unread count so frontend can update badge reliably
        unread_count = Notification.objects.filter(user=request.user, is_read=False).count()
        return JsonResponse({"success": True, "unread": unread_count})
    except Notification.DoesNotExist:
        return JsonResponse({"success": False, "error": "Notification not found"}, status=404)


@login_required
@require_POST
def api_mark_chat_read(request):
    """Mark chat-related notifications and ChatMessage rows as read for the current user.

    Accepts form-encoded `post_id` (for group activity chat) or `dm_email` (for DM chats).
    Returns JSON {ok: True}.
    """
    post_id = request.POST.get('post_id')
    dm_email = request.POST.get('dm_email')

    try:
        # mark Notification rows
        from notifications.models import Notification as NotifModel
        if post_id:
            link = f"/chat/activity/{post_id}/"
            NotifModel.objects.filter(user=request.user, kind=NotifModel.Kind.CHAT_MESSAGE, link_url=link, is_read=False).update(is_read=True)
            # also mark ChatMessage for the room as read
            try:
                from chat.models import ChatRoom, ChatMessage
                room = ChatRoom.objects.filter(post_id=post_id, room_type='GROUP').first()
                if room:
                    ChatMessage.objects.filter(room=room, is_read=False).exclude(sender=request.user).update(is_read=True)
            except Exception:
                pass

        elif dm_email:
            # DM link_url uses /chat/dm/<email>/ as created by notify_chat_message
            NotifModel.objects.filter(user=request.user, kind=NotifModel.Kind.CHAT_MESSAGE, link_url__startswith=f"/chat/dm/{dm_email}", is_read=False).update(is_read=True)
            try:
                from users.models import User
                from chat.models import ChatRoom, ChatMessage, ChatMembership
                other = User.objects.filter(email=dm_email).first()
                if other:
                    # find DM room shared by both
                    my_room_ids = set(ChatMembership.objects.filter(user=request.user).values_list('room_id', flat=True))
                    other_room_ids = set(ChatMembership.objects.filter(user=other).values_list('room_id', flat=True))
                    common = list(my_room_ids & other_room_ids)
                    room = ChatRoom.objects.filter(id__in=common, room_type='DM').first()
                    if room:
                        ChatMessage.objects.filter(room=room, is_read=False).exclude(sender=request.user).update(is_read=True)
            except Exception:
                pass

        return JsonResponse({"ok": True})
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=500)
