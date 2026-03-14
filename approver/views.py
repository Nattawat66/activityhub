# approver/views.py

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponseForbidden
from django.urls import reverse
from django.utils import timezone

from post.models import Post
from users.models import User

from .models import PostReport, UserReport
from .forms import PostReportForm, UserReportForm
from notifications.signals import notify_admins_new_report


# --- Decorator ตรวจสอบ Role ---
def roles_required(allowed_roles=None):
    if allowed_roles is None:
        allowed_roles = []

    def decorator(view_func):
        def _wrapped_view(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect("login")  # หน้า login ของโปรเจกต์
            if allowed_roles and request.user.role not in allowed_roles:
                return HttpResponseForbidden("คุณไม่มีสิทธิ์เข้าถึงหน้านี้")
            return view_func(request, *args, **kwargs)

        return _wrapped_view

    return decorator


# -----------------------------
# แดชบอร์ดสำหรับ Approver / Admin
# -----------------------------
@login_required
@roles_required(allowed_roles=[User.Role.APPROVER, User.Role.ADMIN])
def approver_dashboard(request):
    """
    main_tab: manage / approve
    sub_tab:  accounts / posts / reports   (ใช้เฉพาะตอน main_tab = manage)
    """

    main_tab = request.GET.get("main", "manage")  # ค่าเริ่มต้น = จัดการบัญชี
    sub_tab = request.GET.get("sub", "accounts")  # ค่าเริ่มต้น = แท็บบัญชี

    # ข้อมูลผู้ใช้ทั้งหมด (ใช้ในแท็บ "บัญชี") แสดงเฉพาะที่ยัง active
    accounts = User.objects.filter(is_active=True).order_by("date_joined")

    # ✅ โพสต์ทั้งหมด (ใช้ในแท็บ "โพสต์") — ดึงทุกโพสต์ รวมที่ซ่อน/ลบ
    all_posts = (
        Post.objects.select_related("organizer")
        .all()
        .order_by("-created_at")
    )

    # โพสต์ที่รออนุมัติ (ใช้ในหัวข้อ "ยืนยันโพสต์") แสดงเฉพาะที่ไม่ถูกลบ/ซ่อน
    pending_posts = (
        Post.objects.select_related("organizer")
        .filter(status=Post.Status.PENDING, is_deleted=False, is_hidden=False)
        .order_by("-created_at")
    )

    # ✅ รายงานโพสต์/บัญชี: เอาเฉพาะที่รอดำเนินการ เพื่อให้แอดมินจัดการ
    post_reports = (
        PostReport.objects.select_related("post", "reporter")
        .filter(status=PostReport.Status.PENDING)
        .order_by("-created_at")
    )
    user_reports = (
        UserReport.objects.select_related("user", "reporter")
        .filter(status=UserReport.Status.PENDING)
        .order_by("-created_at")
    )

    context = {
        "main_tab": main_tab,
        "sub_tab": sub_tab,
        "accounts": accounts,
        "all_posts": all_posts,
        "pending_posts": pending_posts,
        "post_reports": post_reports,
        "user_reports": user_reports,
    }
    return render(request, "approver/dashboard.html", context)



# -----------------------------
# อนุมัติ / ปฏิเสธ โพสต์
# -----------------------------
@login_required
@roles_required(allowed_roles=[User.Role.APPROVER, User.Role.ADMIN])
def approve_post(request, post_id):
    if request.method == "POST":
        post = get_object_or_404(Post, id=post_id)
        post.status = Post.Status.APPROVED
        post.save()
        messages.success(request, f"อนุมัติโพสต์ '{post.title}' เรียบร้อยแล้ว")
    # กลับไปที่แท็บ "ยืนยันโพสต์"
    return redirect(f"{reverse('approver_dashboard')}?main=approve")


@login_required
@roles_required(allowed_roles=[User.Role.APPROVER, User.Role.ADMIN])
def reject_post(request, post_id):
    if request.method == "POST":
        post = get_object_or_404(Post, id=post_id)
        post.status = Post.Status.REJECTED
        post.save()
        messages.warning(request, f"ปฏิเสธโพสต์ '{post.title}' เรียบร้อยแล้ว")
    # กลับไปที่แท็บ "ยืนยันโพสต์"
    return redirect(f"{reverse('approver_dashboard')}?main=approve")


# -----------------------------
# ซ่อน / ลบโพสต์แบบ soft delete
# -----------------------------
@login_required
@roles_required(allowed_roles=[User.Role.APPROVER, User.Role.ADMIN])
def hide_post(request, post_id):
    if request.method == "POST":
        post = get_object_or_404(Post, id=post_id)
        post.is_hidden = True
        post.save()
        messages.success(request, f"ซ่อนโพสต์ '{post.title}' เรียบร้อยแล้ว")
    return redirect(f"{reverse('approver_dashboard')}?main=manage&sub=posts")


@login_required
@roles_required(allowed_roles=[User.Role.APPROVER, User.Role.ADMIN])
def soft_delete_post(request, post_id):
    if request.method == "POST":
        post = get_object_or_404(Post, id=post_id)
        post.is_deleted = True
        post.save()
        messages.success(request, f"ลบโพสต์ (แบบซ่อน) '{post.title}' เรียบร้อยแล้ว")
    return redirect(f"{reverse('approver_dashboard')}?main=manage&sub=posts")

@login_required
@roles_required(allowed_roles=[User.Role.APPROVER, User.Role.ADMIN])
def restore_post(request, post_id):
    if request.method == "POST":
        post = get_object_or_404(Post, id=post_id)
        post.is_hidden = False
        post.is_deleted = False
        post.save()
        messages.success(request, f"กู้คืนโพสต์ '{post.title}' เรียบร้อยแล้ว")
    return redirect(f"{reverse('approver_dashboard')}?main=manage&sub=posts")



# -----------------------------
# ปิดการใช้งานบัญชี (ไม่ลบจริง ใช้ is_active)
# -----------------------------
@login_required
@roles_required(allowed_roles=[User.Role.APPROVER, User.Role.ADMIN])
def deactivate_user(request, email):
    if request.method == "POST":
        user = get_object_or_404(User, email=email)
        user.is_active = False
        user.save()
        messages.success(request, f"ปิดการใช้งานบัญชีของ '{user.get_full_name() or user.username}' แล้ว")
    return redirect(f"{reverse('approver_dashboard')}?main=manage&sub=accounts")


# -----------------------------
# รายงานโพสต์ (ผู้ใช้ทั่วไป)
# -----------------------------
@login_required
def submit_post_report(request, post_id):
    if request.method != "POST":
        return redirect(request.META.get("HTTP_REFERER", reverse("home:home")))

    post = get_object_or_404(Post, id=post_id)

    form = PostReportForm(request.POST, request.FILES)
    if form.is_valid():
        report = form.save(commit=False)
        report.reporter = request.user
        report.post = post
        report.save()

        # แจ้งเตือนแอดมิน/approver
        try:
            notify_admins_new_report(
                report_type="post",
                reporter=request.user,
                target_name=post.title,
                detail=report.reason,
            )
        except Exception:
            pass

        messages.success(request, "ส่งรายงานโพสต์เรียบร้อย")
    else:
        messages.error(request, "ส่งรายงานไม่สำเร็จ: กรุณากรอกเหตุผลให้ครบ")

    return redirect(request.META.get("HTTP_REFERER", reverse("home:home")))


# -----------------------------
# รายงานบัญชี (ต้องรายงานจากหน้าโปรไฟล์คนนั้น)
# -----------------------------
@login_required
def submit_user_report(request, user_email):
    if request.method != "POST":
        return redirect(request.META.get("HTTP_REFERER", reverse("home:home")))

    target_user = get_object_or_404(User, email=user_email)

    if target_user.email == request.user.email:
        messages.error(request, "รายงานตัวเองไม่ได้")
        return redirect(request.META.get("HTTP_REFERER", reverse("home:home")))

    form = UserReportForm(request.POST, request.FILES)
    if form.is_valid():
        report = form.save(commit=False)
        report.reporter = request.user
        report.user = target_user
        report.save()

        # แจ้งเตือนแอดมิน/approver
        try:
            notify_admins_new_report(
                report_type="user",
                reporter=request.user,
                target_name=target_user.get_full_name() or target_user.email,
                detail=report.reason,
            )
        except Exception:
            pass

        messages.success(request, "ส่งรายงานบัญชีเรียบร้อย")
    else:
        messages.error(request, "ส่งรายงานไม่สำเร็จ: กรุณากรอกเหตุผลให้ครบ")

    return redirect(request.META.get("HTTP_REFERER", reverse("home:home")))


# -----------------------------
# จัดการรายงานโพสต์ (Approver/Admin) -> ซ่อน / ลบ
# -----------------------------
@login_required
@roles_required(allowed_roles=[User.Role.APPROVER, User.Role.ADMIN])
def handle_post_report_hide(request, report_id):
    if request.method == "POST":
        report = get_object_or_404(PostReport, id=report_id)

        report.post.is_hidden = True
        report.post.save(update_fields=["is_hidden"])

        report.status = PostReport.Status.RESOLVED
        report.action_note = "ซ่อนโพสต์"
        report.handled_by = request.user
        report.handled_at = timezone.now()
        report.save()

        messages.success(request, "ซ่อนโพสต์จากรายงานเรียบร้อยแล้ว")

    return redirect(f"{reverse('approver_dashboard')}?main=manage&sub=reports")


@login_required
@roles_required(allowed_roles=[User.Role.APPROVER, User.Role.ADMIN])
def handle_post_report_delete(request, report_id):
    if request.method == "POST":
        report = get_object_or_404(PostReport, id=report_id)

        report.post.is_deleted = True
        report.post.save(update_fields=["is_deleted"])

        report.status = PostReport.Status.RESOLVED
        report.action_note = "ลบโพสต์ (soft delete)"
        report.handled_by = request.user
        report.handled_at = timezone.now()
        report.save()

        messages.success(request, "ลบโพสต์ (soft delete) จากรายงานเรียบร้อยแล้ว")

    return redirect(f"{reverse('approver_dashboard')}?main=manage&sub=reports")


# -----------------------------
# จัดการรายงานบัญชี (Approver/Admin) -> ไม่ลบ / ปิดใช้งาน
# -----------------------------
@login_required
@roles_required(allowed_roles=[User.Role.APPROVER, User.Role.ADMIN])
def handle_user_report_reject(request, report_id):
    if request.method == "POST":
        report = get_object_or_404(UserReport, id=report_id)

        report.status = UserReport.Status.REJECTED
        report.action_note = "ไม่ลบ"
        report.handled_by = request.user
        report.handled_at = timezone.now()
        report.save()

        messages.success(request, "บันทึกผลว่าไม่ลบ (ไม่เข้าข่าย) เรียบร้อยแล้ว")

    return redirect(f"{reverse('approver_dashboard')}?main=manage&sub=reports")


@login_required
@roles_required(allowed_roles=[User.Role.APPROVER, User.Role.ADMIN])
def handle_user_report_deactivate(request, report_id):
    if request.method == "POST":
        report = get_object_or_404(UserReport, id=report_id)

        report.user.is_active = False
        report.user.save(update_fields=["is_active"])

        report.status = UserReport.Status.RESOLVED
        report.action_note = "ปิดการใช้งานบัญชี"
        report.handled_by = request.user
        report.handled_at = timezone.now()
        report.save()

        messages.success(request, "ปิดการใช้งานบัญชีจากรายงานเรียบร้อยแล้ว")

    return redirect(f"{reverse('approver_dashboard')}?main=manage&sub=reports")
