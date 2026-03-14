from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.core.serializers.json import DjangoJSONEncoder
from django.db.models import Avg, Q, Count, Exists, OuterRef, Value, BooleanField
from post.models import Post
from activity_register.models import ActivityReview, ActivityRegistration
from chat.models import ChatRoom
import json
from django.contrib.auth import get_user_model
from users.models import Profile  # ✅ เพิ่มเพื่อเช็คสถานะติดตาม
from django.utils import timezone
import datetime
import re
import difflib


def _normalize_search_query(raw):
    """
    Normalize user search input to help matching:
    - map Thai month names/abbreviations to month numbers and English short names
    - convert Buddhist year (พ.ศ.) to Gregorian year (ค.ศ.) and add both forms
    - return list of tokens to search for
    """
    if not raw:
        return []

    s = raw.strip()

    # map Thai month names to month numbers and english short names
    thai_months = {
        'มกราคม': ('01', 'Jan'), 'ม.ค.': ('01', 'Jan'), 'ม.ค': ('01', 'Jan'),
        'กุมภาพันธ์': ('02', 'Feb'), 'ก.พ.': ('02', 'Feb'), 'ก.พ': ('02', 'Feb'),
        'มีนาคม': ('03', 'Mar'), 'มี.ค.': ('03', 'Mar'), 'มี.ค': ('03', 'Mar'),
        'เมษายน': ('04', 'Apr'), 'เม.ย.': ('04', 'Apr'), 'เม.ย': ('04', 'Apr'),
        'พฤษภาคม': ('05', 'May'), 'พ.ค.': ('05', 'May'), 'พ.ค': ('05', 'May'),
        'มิถุนายน': ('06', 'Jun'), 'มิ.ย.': ('06', 'Jun'), 'มิ.ย': ('06', 'Jun'),
        'กรกฎาคม': ('07', 'Jul'), 'ก.ค.': ('07', 'Jul'), 'ก.ค': ('07', 'Jul'),
        'สิงหาคม': ('08', 'Aug'), 'ส.ค.': ('08', 'Aug'), 'ส.ค': ('08', 'Aug'),
        'กันยายน': ('09', 'Sep'), 'ก.ย.': ('09', 'Sep'), 'ก.ย': ('09', 'Sep'),
        'ตุลาคม': ('10', 'Oct'), 'ต.ค.': ('10', 'Oct'), 'ต.ค': ('10', 'Oct'),
        'พฤศจิกายน': ('11', 'Nov'), 'พ.ย.': ('11', 'Nov'), 'พ.ย': ('11', 'Nov'),
        'ธันวาคม': ('12', 'Dec'), 'ธ.ค.': ('12', 'Dec'), 'ธ.ค': ('12', 'Dec'),
    }

    tokens = []

    # find 4-digit years in query and convert BE->CE if looks like BE
    years = re.findall(r'\b(\d{4})\b', s)
    for y in years:
        try:
            yi = int(y)
            tokens.append(y)
            if yi > 2400:  # likely Buddhist year
                ce = yi - 543
                tokens.append(str(ce))
        except Exception:
            pass

    # replace thai month names with month numbers and english short name tokens
    for k, (mn, en) in thai_months.items():
        if k in s:
            tokens.append(k)
            tokens.append(mn)
            tokens.append(en)
            s = s.replace(k, ' ')

    # split remaining by non-word to get tokens
    more = re.split(r'\W+', s)
    for t in more:
        if t:
            tokens.append(t)

    # dedupe and lowercase
    seen = set()
    out = []
    for t in tokens:
        tl = t.strip()
        if not tl:
            continue
        tl_lower = tl.lower()
        if tl_lower in seen:
            continue
        seen.add(tl_lower)
        out.append(tl)

    return out


def index_view(request):
    if request.user.is_authenticated:
        return redirect('home:home')
    return render(request, 'home/index.html')


def map_view(request):
    return render(request, 'home/map.html')


def about_view(request):
    return render(request, 'home/about.html')


def about(request):
    User = get_user_model()

    volunteer_count = User.objects.filter(is_active=True).count()

    total_posts_count = Post.objects.filter(
        is_deleted=False,
        is_hidden=False,
        status=Post.Status.APPROVED
    ).count()

    context = {
        "volunteer_count": f"{volunteer_count:,}",
        "total_posts_count": f"{total_posts_count:,}",
    }
    return render(request, "home/about.html", context)


def home_view(request):
    """
    หน้า feed หลัก
    - รองรับการกรองตามหมวดหมู่ (category)
    - รองรับการค้นหา (search) ตามชื่อกิจกรรม / รายละเอียด / สถานที่ / ชื่อผู้จัด
    """
    posts = Post.objects.filter(
        status=Post.Status.APPROVED,
        is_hidden=False,
        is_deleted=False,
    )

    # ✅ จำนวนรีวิวต่อโพสต์ (reverse name = activity_reviews)
    posts = posts.annotate(review_count=Count('activity_reviews', distinct=True))

    # ✅ สถานะติดตาม (เชื่อมกับ Profile.followers)
    if request.user.is_authenticated:
        my_profile = getattr(request.user, "profile", None)
        if my_profile:
            follow_qs = Profile.objects.filter(
                user_id=OuterRef('organizer_id'),
                followers=my_profile
            )
            posts = posts.annotate(is_following=Exists(follow_qs))
        else:
            posts = posts.annotate(is_following=Value(False, output_field=BooleanField()))
    else:
        posts = posts.annotate(is_following=Value(False, output_field=BooleanField()))

    categories = [c[0] for c in Post.CATEGORY_CHOICES]

    selected_category = request.GET.get('category')
    search_query = request.GET.get('search', '').strip()

    is_scored = False  # ✅ ตัวแปรเช็คว่ามีการให้คะแนนเพื่อจัดเรียงหรือไม่

    if selected_category:
        posts = posts.filter(category=selected_category)

    if search_query:
        tokens = _normalize_search_query(search_query)

        # Build Q for tokens: match any token in several text fields
        q = Q()
        for t in tokens:
            token_q = (
                Q(title__icontains=t) |
                Q(description__icontains=t) |
                Q(location__icontains=t) |
                Q(organizer__first_name__icontains=t) |
                Q(organizer__last_name__icontains=t) |
                Q(organizer__email__icontains=t)
            )
            if t.isdigit() and len(t) == 4:
                try:
                    yi = int(t)
                    token_q |= Q(event_date__year=yi)
                except Exception:
                    pass
            q |= token_q

        posts = posts.filter(q).distinct()

        # Annotate relevance score using weighted matches
        if hasattr(posts, 'annotate') and tokens:
            from django.db.models import Case, When, IntegerField
            
            phrase = search_query.strip()
            token_exprs = []

            # --- 1. คะแนนพิเศษสูงสุด (10000) ใช้ icontains ทั้งหมด ---
            if '@' in phrase:
                token_exprs.append(Case(When(Q(organizer__email__icontains=phrase), then=Value(10000)), default=Value(0), output_field=IntegerField()))
            
            parts = phrase.split()
            if len(parts) >= 2:
                first = parts[0]
                last = parts[-1]
                name_cond = (
                    Q(organizer__first_name__icontains=first) & Q(organizer__last_name__icontains=last)
                ) | (
                    Q(organizer__first_name__icontains=last) & Q(organizer__last_name__icontains=first)
                )
                token_exprs.append(Case(When(name_cond, then=Value(10000)), default=Value(0), output_field=IntegerField()))
            elif len(parts) == 1:
                # กรณีค้นหาคำเดียว ขอแค่มีส่วนหนึ่งของชื่อหรือนามสกุลตรง ก็ให้คะแนนเต็ม
                exact_name_cond = Q(organizer__first_name__icontains=phrase) | Q(organizer__last_name__icontains=phrase)
                token_exprs.append(Case(When(exact_name_cond, then=Value(10000)), default=Value(0), output_field=IntegerField()))

            # --- 2. คะแนนระดับย่อยตาม Token (คำที่ถูกตัดแยก) ---
            for t in tokens:
                token_exprs.append(
                    Case(
                        When(Q(organizer__email__icontains=t), then=Value(50)),
                        When(Q(organizer__first_name__icontains=t) | Q(organizer__last_name__icontains=t), then=Value(40)),
                        When(Q(title__icontains=t), then=Value(10)),
                        When(Q(description__icontains=t), then=Value(5)),
                        When(Q(location__icontains=t), then=Value(3)),
                        default=Value(0),
                        output_field=IntegerField(),
                    )
                )

                if t.isdigit() and len(t) == 4:
                    try:
                        yi = int(t)
                        token_exprs.append(Case(When(Q(event_date__year=yi), then=Value(10)), default=Value(0), output_field=IntegerField()))
                    except Exception:
                        pass

            # --- 3. นำคะแนนมารวมกันและเรียงลำดับ (Order By Score) ---
            if token_exprs:
                score_expr = token_exprs[0]
                for expr in token_exprs[1:]:
                    score_expr = score_expr + expr
                
                # เรียงคะแนนจากมากไปน้อย (ถ้าคะแนนเท่ากันเรียงตามวันที่)
                posts = posts.annotate(score=score_expr).order_by('-score', '-created_at')
                is_scored = True  # ✅ มาร์คไว้ว่าจัดเรียงด้วยคะแนนแล้ว

        # If no DB hits, try a lightweight fuzzy match in Python
        if hasattr(posts, 'exists') and not posts.exists():
            candidates = Post.objects.filter(
                status=Post.Status.APPROVED,
                is_hidden=False,
                is_deleted=False,
            ).order_by('-created_at')[:300]

            def best_score(p):
                hay = ' '.join(filter(None, [p.title or '', p.description or '', p.location or '',
                                             (p.organizer.first_name or '') + ' ' + (p.organizer.last_name or '')]))
                hay_low = hay.lower()
                best = 0.0
                for t in tokens:
                    try:
                        score = difflib.SequenceMatcher(None, t.lower(), hay_low).ratio()
                        if score > best:
                            best = score
                    except Exception:
                        continue
                return best

            matched = []
            for p in candidates:
                try:
                    sc = best_score(p)
                    if sc >= 0.45:  # tolerant threshold
                        matched.append((sc, p))
                except Exception:
                    continue

            matched.sort(key=lambda x: x[0], reverse=True)
            posts = [m[1] for m in matched]

    # ✅ ป้องกันการเรียงลำดับทับคะแนนที่เราตั้งไว้ (ถ้ายังไม่มีการเรียงด้วยคะแนน ให้เรียงตามเวลา)
    if hasattr(posts, 'order_by'):
        if not is_scored:
            posts = posts.order_by('-created_at')
    else:
        # list of Post instances -> sort in-place by created_at desc (สำหรับกรณี fuzzy match)
        posts.sort(key=lambda p: getattr(p, 'created_at', datetime.datetime.min), reverse=True)

    # compute show_register for each post (hide when closed, full, or within 1 day of event)
    posts = list(posts)
    now = timezone.now()
    for p in posts:
        try:
            within_cutoff = False
            if p.event_date:
                within_cutoff = now <= (p.event_date - timezone.timedelta(days=1))
            else:
                within_cutoff = True

            p.show_register = bool(p.allow_register and within_cutoff and (not p.is_full()))
        except Exception:
            p.show_register = bool(p.allow_register)

    context = {
        'posts': posts,
        'categories': categories,
        'selected_category': selected_category,
        'search_query': search_query,
    }
    return render(request, 'home/homes.html', context)


def category_view(request):
    category_type = request.GET.get('type')

    posts = Post.objects.filter(
        status=Post.Status.APPROVED,
        is_hidden=False,
        is_deleted=False,
    )

    # ✅ จำนวนรีวิวต่อโพสต์
    posts = posts.annotate(review_count=Count('activity_reviews', distinct=True))

    # ✅ สถานะติดตาม
    if request.user.is_authenticated:
        my_profile = getattr(request.user, "profile", None)
        if my_profile:
            follow_qs = Profile.objects.filter(
                user_id=OuterRef('organizer_id'),
                followers=my_profile
            )
            posts = posts.annotate(is_following=Exists(follow_qs))
        else:
            posts = posts.annotate(is_following=Value(False, output_field=BooleanField()))
    else:
        posts = posts.annotate(is_following=Value(False, output_field=BooleanField()))

    posts = posts.order_by('-created_at')

    if category_type:
        posts = posts.filter(category=category_type)

    context = {
        'posts': posts,
        'selected_category': category_type,
    }
    return render(request, 'home/category.html', context)


@login_required
def post_detail_view(request, post_id):
    """
    รายละเอียดกิจกรรม (หน้า home) + สรุปรีวิวเหมือนหน้าใน app post
    """
    post = get_object_or_404(
        Post,
        id=post_id,
        status=Post.Status.APPROVED,
        is_hidden=False,
        is_deleted=False,
    )

    reviews = (
        ActivityReview.objects
        .filter(post=post)
        .select_related('user')
        .order_by('-created_at')
    )

    review_count = reviews.count()
    avg_rating = 0
    avg_rating_int = 0

    if review_count > 0:
        avg_rating = reviews.aggregate(avg=Avg('rating'))['avg'] or 0
        avg_rating_int = int(round(avg_rating))

    # ✅ ดึงสถานะลงทะเบียนของ user (ถ้ามี)
    my_reg = None
    can_register_again = False
    cooldown_until_iso = ''
    if request.user.is_authenticated:
        my_reg = ActivityRegistration.objects.filter(
            user=request.user,
            post=post,
        ).first()

        if my_reg and my_reg.status == ActivityRegistration.Status.CANCEL_PENDING:
            my_reg.finalize_cancel_if_expired()
            my_reg.refresh_from_db()

        # เงื่อนไขสมัครใหม่ได้: เคย CANCELED และ cooldown หมด, ยังไม่หมดเขตรับสมัคร, ยังไม่เต็ม, ยังไม่เลยวันกิจกรรม
        if my_reg and my_reg.status == ActivityRegistration.Status.CANCELED:
            now = timezone.now()
            if not my_reg.cooldown_until or now >= my_reg.cooldown_until:
                if post.allow_register and not post.is_full() and (not post.event_date or now <= post.event_date):
                    can_register_again = True
            if my_reg.cooldown_until:
                cooldown_until_iso = my_reg.cooldown_until.isoformat()

    has_chat_room = ChatRoom.objects.filter(post=post).exists()

    user_is_registered = False
    if my_reg and my_reg.status == ActivityRegistration.Status.ACTIVE:
        user_is_registered = True

    active_reg_count = post.active_registrations_count()
    is_organizer = request.user.is_authenticated and (request.user == post.organizer)
    can_access_chat = False
    if request.user.is_authenticated and post.create_group and has_chat_room:
        # ตรวจว่า post นี้ "ใช้ระบบสมัคร" หรือไม่
        uses_registration = post.allow_register or post.registrations.exists()
        if uses_registration:
            # กิจกรรมแบบรับสมัคร -> ต้องเป็นผู้สมัครหรือเจ้าของกิจกรรมเท่านั้น
            can_access_chat = user_is_registered or is_organizer
        else:
            # กิจกรรมไม่มีระบบสมัครแต่เปิดแชท -> แสดงปุ่มแชทได้เลย
            can_access_chat = True

    # ไม่ส่ง my_reg ถ้าเป็น CANCELED และสมัครใหม่ได้ (เพื่อไม่ให้ template แสดงสถานะยกเลิก)
    my_reg_for_template = my_reg
    if my_reg and my_reg.status == ActivityRegistration.Status.CANCELED and can_register_again:
        my_reg_for_template = None

    context = {
        'post': post,
        'reviews': reviews,
        'review_count': review_count,
        'avg_rating': avg_rating,
        'avg_rating_int': avg_rating_int,
        'my_reg': my_reg_for_template,
        'active_reg_count': active_reg_count,
        'is_full': post.is_full(),
        'has_chat_room': has_chat_room,
        'user_is_registered': user_is_registered,
        'can_access_chat': can_access_chat,
        'cancel_undo_until_iso': my_reg.cancel_undo_until.isoformat() if my_reg and my_reg.cancel_undo_until else '',
        'cooldown_until_iso': cooldown_until_iso,
        'can_register_again': can_register_again,
    }
    return render(request, 'home/post_detail.html', context)


def _get_events_from_posts():
    posts = Post.objects.filter(
        status=Post.Status.APPROVED,
        is_hidden=False,
        is_deleted=False,
        map_lat__isnull=False,
        map_lng__isnull=False,
    )

    events = []
    for p in posts:
        try:
            lat = float(p.map_lat)
            lng = float(p.map_lng)
        except (TypeError, ValueError):
            continue

        events.append({
            "id": p.id,
            "title": p.title,
            "lat": lat,
            "lng": lng,
            "location": p.location or "",
            "date": p.event_date.strftime("%d %b %Y") if p.event_date else "",
        })
    return events


def public_map_view(request):
    events = _get_events_from_posts()

    context = {
        "events_json": json.dumps(events, cls=DjangoJSONEncoder, ensure_ascii=False),
        "enable_geolocation": False,
    }
    return render(request, "home/map.html", context)


@login_required
def nearby_map_view(request):
    events = _get_events_from_posts()

    context = {
        "events_json": json.dumps(events, cls=DjangoJSONEncoder, ensure_ascii=False),
        "radius_km": 30,
    }
    return render(request, "home/map_nearby.html", context)