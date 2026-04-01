from datetime import date, datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.http import JsonResponse

from .forms import ActivityRegistrationForm, ActivityReviewForm
from .models import ActivityRegistration, ActivityReview
from chat.models import ChatMembership, ChatRoom
from post.models import Post

def _serialize_for_session(data: dict) -> dict:
    """แปลงค่าใน cleaned_data ให้เก็บใน session ได้"""
    if not isinstance(data, dict):
        return data

    result = {}
    for key, value in data.items():
        if isinstance(value, (date, datetime)):
            result[key] = value.isoformat()
        else:
            result[key] = value
    return result


def _finalize_expired_pending_for_user(user):
    qs = ActivityRegistration.objects.filter(
        user=user,
        status=ActivityRegistration.Status.CANCEL_PENDING,
    )
    for r in qs:
        r.finalize_cancel_if_expired()


# -----------------------------
# สมัครกิจกรรม
# -----------------------------
@login_required
def register_activity(request, post_id):
    post = get_object_or_404(Post, id=post_id)

    _finalize_expired_pending_for_user(request.user)

    # ✅ ถ้าเจ้าของปิดรับสมัคร
    if not post.allow_register:
        messages.error(request, "กิจกรรมนี้ปิดรับการสมัครแล้ว")
        return redirect("post:post_detail", post_id=post.id)

    # ✅ เช็คเต็ม (เฉพาะ ACTIVE)
    if post.is_full():
        # ปิดรับสมัครอัตโนมัติ
        if post.allow_register:
            post.allow_register = False
            post.save(update_fields=["allow_register"])
        messages.error(request, "กิจกรรมเต็มแล้ว")
        return redirect("post:post_detail", post_id=post.id)

    # ✅ ถ้าเคยมีประวัติ
    existing = ActivityRegistration.objects.filter(
        user=request.user,
        post=post,
    ).first()

    if existing:
        if existing.status == ActivityRegistration.Status.CANCEL_PENDING:
            messages.warning(request, "คุณอยู่ระหว่างการยกเลิกกิจกรรม (ยังย้อนกลับได้ภายใน 5 นาที)")
            return redirect("post:post_detail", post_id=post.id)

        if existing.status == ActivityRegistration.Status.CANCELED:
            # ถ้ายังอยู่ใน cooldown -> บล็อคการสมัคร
            if existing.cooldown_until and timezone.now() < existing.cooldown_until:
                mins = int((existing.cooldown_until - timezone.now()).total_seconds() // 60) + 1
                messages.error(request, f"คุณเพิ่งยกเลิกกิจกรรมนี้ สมัครใหม่ได้อีกประมาณ {mins} นาที")
                return redirect("post:post_detail", post_id=post.id)
            # ถ้าหมด cooldown แล้ว ปล่อยผ่านให้สมัครใหม่ได้ (รีใช้งาน record เดิม)

        if existing.status == ActivityRegistration.Status.ACTIVE:
            messages.info(request, "คุณสมัครกิจกรรมนี้ไปแล้ว")
            return redirect("post:post_detail", post_id=post.id)

    # ✅ ตรวจ “วันเดียวกัน” + “เวลาชนกัน”
    conflict_post = None
    same_day_post = None
    if post.event_date:
        same_day_regs = ActivityRegistration.objects.filter(
            user=request.user,
            status=ActivityRegistration.Status.ACTIVE,
            post__event_date__date=timezone.localtime(post.event_date).date(),
        ).select_related("post")

        for r in same_day_regs:
            other = r.post
            if not other.event_date:
                continue

            # ชนกัน: เวลาเริ่มตรงกัน
            if timezone.localtime(other.event_date) == timezone.localtime(post.event_date):
                conflict_post = other
                break
            else:
                same_day_post = other

    session_data = request.session.get("register_profile")

    if request.method == "POST":
        if conflict_post:
            messages.error(request, "คุณมีกิจกรรมอีกอันที่เวลาเดียวกัน กรุณายกเลิกหรือเลือกเพียงกิจกรรมเดียว")
            return redirect("post:post_detail", post_id=post.id)

        form = ActivityRegistrationForm(request.POST)
        if form.is_valid():
            # ถ้าเคยกดยกเลิกและ Cooldown หมดแล้ว ให้อัปเดต Record เดิม ไม่ต้องสร้างใหม่
            if existing and existing.status == ActivityRegistration.Status.CANCELED and (not existing.cooldown_until or timezone.now() >= existing.cooldown_until):
                for field, value in form.cleaned_data.items():
                    setattr(existing, field, value)
                existing.user = request.user
                existing.post = post
                existing.status = ActivityRegistration.Status.ACTIVE
                existing.cancel_reason = ''
                existing.cancel_reason_other = ''
                existing.canceled_at = None
                existing.cancel_undo_until = None
                existing.cooldown_until = None
                existing.save()
                registration = existing
            else:
                registration = form.save(commit=False)
                registration.user = request.user
                registration.post = post
                registration.status = ActivityRegistration.Status.ACTIVE
                registration.save()

            request.session["register_profile"] = _serialize_for_session(form.cleaned_data)

            # ดึงห้องแชทของกิจกรรมนี้ แล้วเพิ่ม user เข้าห้อง
            room = ChatRoom.objects.filter(room_type="GROUP", post=post).first()
            chat_room_url = None
            if room:
                ChatMembership.objects.get_or_create(
                    room=room,
                    user=request.user,
                    defaults={"is_admin": False},
                )
                chat_room_url = reverse("chat:activity_chat", args=[post.id])

            if post.is_full():
                post.allow_register = False
                post.save(update_fields=["allow_register"])
                try:
                    from notifications.models import Notification
                    Notification.objects.get_or_create(
                        user=post.organizer,
                        post=post,
                        kind=getattr(Notification.Kind, "OWNER_FULL", Notification.Kind.SYSTEM),
                        trigger_date=timezone.localdate(),
                        defaults={
                            "title": post.title,
                            "message": f"กิจกรรมของคุณเต็มแล้ว (สมัคร {post.active_registrations_count()}/{post.slots_available})",
                            "link_url": f"/post/{post.id}/",
                        },
                    )
                    # แจ้งผู้ที่บันทึกโพสต์ไว้ด้วย (ยกเว้นผู้ลงทะเบียนคนปัจจุบัน)
                    try:
                        saved_users = post.saves.exclude(id=request.user.id).all()
                        for u in saved_users:
                            Notification.objects.get_or_create(
                                user=u,
                                post=post,
                                kind=getattr(Notification.Kind, "SAVED_FULL", Notification.Kind.SYSTEM),
                                trigger_date=timezone.localdate(),
                                defaults={
                                    "title": post.title,
                                    "message": f"กิจกรรมที่คุณบันทึกไว้เต็มแล้ว ({post.active_registrations_count()}/{post.slots_available})",
                                    "link_url": f"/post/{post.id}/",
                                },
                            )
                    except Exception:
                        # หาก Notification model หรือความสัมพันธ์อื่น ๆ ยังไม่พร้อม ให้ข้ามเงียบ ๆ
                        pass
                except Exception:
                    pass

            if same_day_post and not conflict_post:
                messages.warning(request, f"แจ้งเตือน: ในวันเดียวกันคุณมีกิจกรรมอื่นอยู่แล้ว: {same_day_post.title}")

            messages.success(request, "สมัครเข้าร่วมกิจกรรมเรียบร้อยแล้ว")
            return render(
                request,
                "activity_register/register_form.html",
                {
                    "form": form,
                    "post": post,
                    "success": True,
                    "edit_mode": False,
                    "chat_room_url": chat_room_url,
                    "conflict_post": conflict_post,
                    "same_day_post": same_day_post,
                },
            )
    else:
        if session_data:
            form = ActivityRegistrationForm(initial=session_data)
            try: form.fields['email'].disabled = True
            except Exception: pass
        else:
            if existing and existing.status == ActivityRegistration.Status.CANCELED and (not existing.cooldown_until or timezone.now() >= existing.cooldown_until):
                form = ActivityRegistrationForm(instance=existing)
                try: form.fields['email'].disabled = True
                except Exception: pass
            else:
                profile = getattr(request.user, 'profile', None)
                initial = {}
                if profile:
                    initial = {
                        'first_name': request.user.first_name,
                        'last_name': request.user.last_name,
                        'nickname': profile.nickname,
                        'birth_date': profile.birth_date,
                        'gender': profile.gender,
                        'current_address': profile.address,
                        'phone': profile.phone,
                        'email': request.user.email,
                        'contact_channel': profile.contact_info,
                    }
                form = ActivityRegistrationForm(initial=initial)
                try: form.fields['email'].disabled = True
                except Exception: pass

    if conflict_post:
        messages.error(request, "คุณมีกิจกรรมอีกอันที่เวลาเดียวกัน กรุณายกเลิกหรือเลือกเพียงกิจกรรมเดียว")
    elif same_day_post:
        messages.warning(request, f"แจ้งเตือน: ในวันเดียวกันคุณมีกิจกรรมอื่นอยู่แล้ว: {same_day_post.title}")

    return render(
        request,
        "activity_register/register_form.html",
        {
            "form": form,
            "post": post,
            "success": False,
            "edit_mode": False,
            "chat_room_url": None,
            "conflict_post": conflict_post,
            "same_day_post": same_day_post,
        },
    )


# -----------------------------
# ยกเลิกกิจกรรม (เริ่มกระบวนการ)
# -----------------------------
@login_required
def cancel_activity(request, post_id):
    post = get_object_or_404(Post, id=post_id)
    reg = get_object_or_404(ActivityRegistration, user=request.user, post=post)

    _finalize_expired_pending_for_user(request.user)

    if reg.status != ActivityRegistration.Status.ACTIVE:
        messages.info(request, "ไม่สามารถยกเลิกได้ในสถานะปัจจุบัน")
        return redirect("post:post_detail", post_id=post.id)

    if not reg.can_cancel():
        messages.error(request, "ไม่สามารถยกเลิกได้ (เกินกำหนด 1 วันก่อนเริ่มกิจกรรม)")
        return redirect("post:post_detail", post_id=post.id)

    if request.method == "POST":
        reason = request.POST.get("reason", "")
        other = request.POST.get("other", "")

        valid_reasons = {k for k, _ in ActivityRegistration.CancelReason.choices}
        if reason not in valid_reasons:
            messages.error(request, "กรุณาเลือกเหตุผลการยกเลิก")
            return redirect("post:post_detail", post_id=post.id)

        if reason == ActivityRegistration.CancelReason.OTHER and not other.strip():
            messages.error(request, "กรุณาระบุเหตุผลเพิ่มเติม (อื่นๆ)")
            return redirect("post:post_detail", post_id=post.id)

        reg.start_cancel_pending(reason=reason, other=other)
        messages.warning(request, "ระบบรับคำขอยกเลิกแล้ว คุณสามารถยกเลิกการดำเนินการได้ภายใน 5 นาที")
        return redirect("post:post_detail", post_id=post.id)

    messages.info(request, "กรุณาส่งเหตุผลการยกเลิก")
    return redirect("post:post_detail", post_id=post.id)


# -----------------------------
# ยกเลิกการดำเนินการยกเลิก (undo ภายใน 5 นาที)
# -----------------------------
@login_required
@require_POST
def undo_cancel_activity(request, post_id):
    post = get_object_or_404(Post, id=post_id)
    reg = get_object_or_404(ActivityRegistration, user=request.user, post=post)

    ok = reg.undo_cancel()
    if ok:
        messages.success(request, "ยกเลิกการดำเนินการยกเลิกเรียบร้อยแล้ว")
    else:
        messages.error(request, "ไม่สามารถยกเลิกการดำเนินการได้ (อาจหมดเวลา 5 นาทีแล้ว)")

    return redirect("post:post_detail", post_id=post.id)


@login_required
@require_POST
def finalize_cancel_ajax(request, post_id):
    post = get_object_or_404(Post, id=post_id)
    reg = ActivityRegistration.objects.filter(user=request.user, post=post).first()
    if not reg:
        return JsonResponse({"error": "no_registration"}, status=400)

    if reg.status == ActivityRegistration.Status.CANCEL_PENDING:
        reg.finalize_cancel_if_expired()
        reg.refresh_from_db()

    return JsonResponse({
        "status": reg.status,
        "cooldown_until_iso": reg.cooldown_until.isoformat() if reg.cooldown_until else "",
    })


# -----------------------------
# แก้ไขโปรไฟล์ข้อมูลสมัครกิจกรรม
# -----------------------------
@login_required
def edit_register_profile(request):
    session_data = request.session.get("register_profile")

    last_reg = None
    if not session_data:
        last_reg = (
            ActivityRegistration.objects.filter(user=request.user)
            .order_by("-id")
            .first()
        )

    if request.method == "POST":
        form = ActivityRegistrationForm(request.POST)
        if form.is_valid():
            request.session["register_profile"] = _serialize_for_session(
                form.cleaned_data
            )
            messages.success(
                request,
                "บันทึกข้อมูลสำหรับสมัครกิจกรรมเรียบร้อยแล้ว "
                "ข้อมูลนี้จะถูกกรอกให้อัตโนมัติเมื่อสมัครกิจกรรมครั้งถัดไป",
            )
            return redirect("profile")
    else:
        if session_data:
            form = ActivityRegistrationForm(initial=session_data)
        elif last_reg:
            form = ActivityRegistrationForm(instance=last_reg)
        else:
            form = ActivityRegistrationForm()

    return render(
        request,
        "activity_register/edit_register_profile.html",
        {
            "form": form,
        },
    )


# -----------------------------
# กิจกรรมที่เคยเข้าร่วม
# -----------------------------
@login_required
def joined_activities(request):
    today = timezone.localdate()

    joined_posts = (
        Post.objects.filter(
            registrations__user=request.user,
            registrations__status=ActivityRegistration.Status.ACTIVE,
            event_date__lt=today,
        )
        .distinct()
        .order_by("-event_date")
    )

    registered_posts = (
        Post.objects.filter(
            registrations__user=request.user,
        )
        .distinct()
        .order_by("-event_date")
    )

    regs = ActivityRegistration.objects.filter(user=request.user).select_related('post').order_by('-id')
    reg_map = {r.post_id: r for r in regs}

    user_reviews = ActivityReview.objects.filter(
        user=request.user,
        post__in=joined_posts,
    )
    reviewed_post_ids = {r.post_id for r in user_reviews}
    
    registered_posts_list = list(registered_posts)
    filtered_registered = []
    for p in registered_posts_list:
        reg = reg_map.get(p.id)
        if reg and reg.status == ActivityRegistration.Status.CANCELED:
            if reg.canceled_at and p.event_date and reg.canceled_at < p.event_date:
                continue
        filtered_registered.append(p)

    context = {
        "joined_posts": joined_posts,
        "reviewed_post_ids": reviewed_post_ids,
        "registered_posts": filtered_registered,
        "reg_map": reg_map,
        "reg_map_keys": set(reg_map.keys()),
    }
    return render(request, "activity_register/joined_activities.html", context)


# -----------------------------
# สร้าง/แก้ไข รีวิวกิจกรรม
# -----------------------------
@login_required
def review_activity(request, post_id):
    post = get_object_or_404(Post, id=post_id)

    if getattr(post, 'is_deleted', False) or getattr(post, 'is_hidden', False):
        messages.error(request, "ไม่สามารถรีวิวได้ กิจกรรมนี้ถูกลบหรือซ่อนแล้ว")
        return redirect("activity_register:joined_activities")

    today = timezone.localdate()

    event_date = None
    if post.event_date:
        if isinstance(post.event_date, datetime):
            event_date = timezone.localtime(post.event_date).date()
        else:
            event_date = post.event_date

    if not event_date or event_date > today:
        messages.error(request, "ยังไม่สามารถรีวิวได้ ต้องรอให้กิจกรรมจบก่อน")
        return redirect("activity_register:joined_activities")

    if post.slots_available == 0:
        pass  
    else:
        has_any_registration = ActivityRegistration.objects.filter(post=post).exists()
        if has_any_registration or post.allow_register:
            has_registered = ActivityRegistration.objects.filter(
                post=post,
                user=request.user,
                status=ActivityRegistration.Status.ACTIVE,
            ).exists()
            if not has_registered:
                messages.error(request, "คุณยังไม่ได้เข้าร่วมกิจกรรมนี้ จึงไม่สามารถรีวิวได้")
                return redirect("activity_register:joined_activities")

    review, created = ActivityReview.objects.get_or_create(
        post=post,
        user=request.user,
    )

    if request.method == "POST":
        form = ActivityReviewForm(request.POST, request.FILES, instance=review)
        if form.is_valid():
            form.save()
            messages.success(request, "บันทึกรีวิวเรียบร้อยแล้ว")
            return redirect("post:post_detail", post_id=post.id)
    else:
        form = ActivityReviewForm(instance=review)

    return render(
        request,
        "activity_register/review_form.html",
        {
            "form": form,
            "post": post,
            "is_edit": not created,
        },
    )