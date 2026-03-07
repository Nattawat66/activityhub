from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.urls import reverse
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.utils.timezone import localtime

from zoneinfo import ZoneInfo  # ✅ เพิ่ม

from users.models import User
from post.models import Post
from .models import ChatRoom, ChatMembership, ChatMessage
from django.db.models import Max

BKK_TZ = ZoneInfo("Asia/Bangkok")  # ✅ เพิ่ม


@login_required
def inbox_view(request):
    """
    กล่องข้อความ: แสดงทุกห้องแชทที่ user เป็นสมาชิก
    ทั้งแชทกลุ่มกิจกรรม และแชทส่วนตัว (DM)
    """
    # Query rooms where user is a member, annotate with last message time and order by it
    rooms_qs = (
        ChatRoom.objects
        .filter(members=request.user)
        .annotate(last_time=Max('messages__created_at'))
        .select_related('post')
        .order_by('-last_time', '-created_at')
    )

    rooms_data = []

    for room in rooms_qs:
        title = ''
        subtitle = ''
        avatar_url = None
        link_url = '#'

        if room.room_type == 'GROUP':
            post = room.post
            if post:
                if post.image:
                    avatar_url = post.image.url
                else:
                    try:
                        if post.organizer and hasattr(post.organizer, 'profile') and getattr(post.organizer.profile, 'profile_picture', None):
                            avatar_url = post.organizer.profile.profile_picture.url
                        elif getattr(post.organizer, 'profile_picture', None):
                            avatar_url = post.organizer.profile_picture.url
                        else:
                            avatar_url = None
                    except Exception:
                        avatar_url = None
            title = room.name or (post.title if post else 'แชทกลุ่มกิจกรรม')
            subtitle = 'แชทกลุ่มกิจกรรม'
            if post:
                link_url = reverse('chat:activity_chat', args=[post.id])

        else:  # DM
            other_membership = (
                ChatMembership.objects
                .filter(room=room)
                .exclude(user=request.user)
                .select_related('user', 'user__profile')
                .first()
            )
            other_user = other_membership.user if other_membership else None

            if other_user:
                title = other_user.get_full_name() or other_user.email
                subtitle = 'ข้อความส่วนตัว'
                if hasattr(other_user, 'profile') and other_user.profile.profile_picture:
                    avatar_url = other_user.profile.profile_picture.url
                link_url = reverse('chat:dm_chat', args=[other_user.email])
            else:
                title = 'แชทส่วนตัว'
                subtitle = 'DM'

        # get last message quickly (room.messages is related_name)
        last_msg = room.messages.order_by('-created_at').select_related('sender').first()

        if last_msg:
            content = last_msg.content or ''
            last_text = content[:60] + ('…' if len(content) > 60 else '')
            # convert to Bangkok timezone for display
            last_time = localtime(last_msg.created_at, timezone=BKK_TZ)
        else:
            last_text = ''
            # if annotated last_time exists, use it (should be equivalent), else None
            last_time = None

        rooms_data.append({
            'room': room,
            'room_id': room.id,
            'title': title,
            'subtitle': subtitle,
            'avatar_url': avatar_url,
            'link_url': link_url,
            'last_message': last_text,
            'last_time': last_time,
            'is_group': (room.room_type == 'GROUP'),
            'unread_count': ChatMessage.objects.filter(room=room, is_read=False).exclude(sender=request.user).count(),
        })

    # rooms_qs already ordered by last_time desc; keep current list order

    return render(request, 'chat/inbox.html', {'rooms': rooms_data})


@login_required
def activity_chat_view(request, post_id):
    """
    แชทกลุ่มของกิจกรรม
    - 1 โพสต์ = 1 ห้องแชท GROUP
    - ถ้ายังไม่เป็นสมาชิก จะเพิ่มเข้าห้องให้
    """
    post = get_object_or_404(Post, id=post_id)

    room, created = ChatRoom.objects.get_or_create(
        room_type='GROUP',
        post=post,
        defaults={
            'name': post.title,
            'created_by': post.organizer,
        }
    )

    ChatMembership.objects.get_or_create(
        room=room,
        user=request.user,
        defaults={'is_admin': False},
    )

    # เมื่อผู้ใช้เปิดห้อง ให้ทำเครื่องหมายข้อความที่ยังไม่อ่านเป็นอ่านแล้ว
    try:
        ChatMessage.objects.filter(room=room, is_read=False).exclude(sender=request.user).update(is_read=True)
    except Exception:
        pass

    # และทำเครื่องหมาย Notification ของแชทนี้เป็นอ่านแล้ว (ถ้ามี)
    try:
        from notifications.models import Notification
        if room.room_type == 'GROUP' and room.post_id:
            Notification.objects.filter(user=request.user, kind=Notification.Kind.CHAT_MESSAGE, link_url=f"/chat/activity/{room.post_id}/", is_read=False).update(is_read=True)
        elif room.room_type == 'DM':
            Notification.objects.filter(user=request.user, kind=Notification.Kind.CHAT_MESSAGE, link_url__startswith="/chat/dm/", is_read=False).update(is_read=True)
    except Exception:
        pass

    # รองรับ POST แบบเดิม (ข้อความล้วน) เผื่อ fallback
    if request.method == "POST":
        text = request.POST.get("content", "").strip()
        file = request.FILES.get("file")
        if text or file:
            ChatMessage.objects.create(
                room=room,
                sender=request.user,
                content=text,
                attachment=file
            )
        return redirect('chat:activity_chat', post_id=post.id)

    messages_qs = ChatMessage.objects.filter(
        room=room
    ).select_related('sender').order_by('created_at')

    # เมื่อเปิด DM ให้ทำเครื่องหมายข้อความที่ยังไม่อ่านเป็นอ่านแล้ว
    try:
        ChatMessage.objects.filter(room=room, is_read=False).exclude(sender=request.user).update(is_read=True)
    except Exception:
        pass

    try:
        from notifications.models import Notification
        Notification.objects.filter(user=request.user, kind=Notification.Kind.CHAT_MESSAGE, link_url__startswith=f"/chat/dm/", is_read=False).update(is_read=True)
    except Exception:
        pass

    return render(
        request,
        'chat/chat.html',
        {
            'room': room,
            'post': post,
            'other_user': None,
            'is_group': True,
            'chat_messages': messages_qs,  # ✅ เปลี่ยนชื่อกันชนกับ Django messages
        },
    )


@login_required
def dm_chat_view(request, email):
    """
    แชทส่วนตัว (DM) ระหว่างเรา กับ user อีกคนหนึ่ง
    - ถ้าไม่มีห้อง จะสร้างห้องใหม่ + membership 2 คน
    """
    other_user = get_object_or_404(User, email=email)

    if other_user == request.user:
        return redirect('profile')

    my_room_ids = set(
        ChatMembership.objects.filter(
            user=request.user
        ).values_list('room_id', flat=True)
    )

    other_room_ids = set(
        ChatMembership.objects.filter(
            user=other_user
        ).values_list('room_id', flat=True)
    )

    common_ids = list(my_room_ids & other_room_ids)
    room = None
    if common_ids:
        room = ChatRoom.objects.filter(
            id__in=common_ids,
            room_type='DM',
        ).first()

    if not room:
        room = ChatRoom.objects.create(
            room_type='DM',
            name='ข้อความส่วนตัว',
            created_by=request.user,
        )
        ChatMembership.objects.bulk_create([
            ChatMembership(room=room, user=request.user, is_admin=False),
            ChatMembership(room=room, user=other_user, is_admin=False),
        ])

    # รองรับ POST แบบเดิม (ข้อความล้วน) เผื่อ fallback
    if request.method == "POST":
        text = request.POST.get("content", "").strip()
        file = request.FILES.get("file")
        if text or file:
            ChatMessage.objects.create(
                room=room,
                sender=request.user,
                content=text,
                attachment=file
            )
        return redirect('chat:dm_chat', email=other_user.email)

    messages_qs = ChatMessage.objects.filter(
        room=room
    ).select_related('sender').order_by('created_at')

    return render(
        request,
        'chat/chat.html',
        {
            'room': room,
            'post': None,
            'other_user': other_user,
            'is_group': False,
            'chat_messages': messages_qs,  # ✅ เปลี่ยนชื่อกันชน
        },
    )


@login_required
@require_POST
def upload_message_view(request, room_id):
    """
    ✅ อัปโหลดรูป/ไฟล์ผ่าน HTTP แล้ว broadcast ไปห้องแชทผ่าน Channels
    """
    room = get_object_or_404(ChatRoom, id=room_id)

    # เช็คสมาชิกห้อง (กันยิงมั่ว)
    if not ChatMembership.objects.filter(room=room, user=request.user).exists():
        return JsonResponse({"ok": False, "error": "not_member"}, status=403)

    text = (request.POST.get("content") or "").strip()
    file = request.FILES.get("file")

    if not text and not file:
        return JsonResponse({"ok": False, "error": "empty"}, status=400)

    msg = ChatMessage.objects.create(
        room=room,
        sender=request.user,
        content=text,
        attachment=file
    )

    # ตรวจว่าเป็นรูปไหม
    is_image = False
    if msg.attachment:
        is_image = msg.is_image()

    # ✅ บังคับเวลาไทย
    dt_local = localtime(msg.created_at, timezone=BKK_TZ)

    payload = {
        "type": "chat_message",
        "message": msg.content or "",
        "sender_id": str(request.user.pk),
        "sender_name": request.user.get_full_name() or str(request.user.pk),
        "created_at": dt_local.strftime("%d/%m/%Y %H:%M"),
        "created_at_iso": dt_local.isoformat(),
        "file_url": msg.attachment.url if msg.attachment else "",
        "file_name": (msg.attachment.name.split("/")[-1] if msg.attachment else ""),
        "is_image": is_image,
    }

    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(f"chat_{room_id}", payload)

    # ✅ แจ้งเตือนข้อความแชทใหม่
    try:
        from notifications.signals import notify_chat_message
        notify_chat_message(request.user, room, text or "ส่งไฟล์แนบ")
    except Exception:
        pass

    # Return the same payload so the HTTP uploader can show the message immediately
    return JsonResponse({"ok": True, "payload": payload})


@login_required
@require_POST
def delete_chat_room_view(request, room_id):
    """
    ✅ ลบห้องแชทของตัวเอง (ไม่กระทบคนอื่น)
    ลบ 2 ชั้น: ยืนยันจาก JS ก่อน แล้วค่อย POST
    """
    room = ChatRoom.objects.filter(id=room_id).first()
    if not room:
        # respond with JSON for AJAX clients, otherwise redirect back to inbox
        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({"ok": False, "error": "not_found"}, status=404)
        messages.warning(request, "ไม่พบห้องแชทที่ต้องการดำเนินการ")
        return redirect('chat:inbox')
    membership = ChatMembership.objects.filter(room=room, user=request.user).first()

    if not membership:
        return JsonResponse({"ok": False, "error": "not_member"}, status=403)

    # ลบแค่ membership ของตัวเอง (ไม่ลบห้องจริง)
    membership.delete()

    # ถ้าห้องไม่มีสมาชิกเหลือ - ลบห้องจริง
    if not ChatMembership.objects.filter(room=room).exists():
        room.delete()

    return JsonResponse({"ok": True})
