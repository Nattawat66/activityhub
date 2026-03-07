from django.urls import path
from . import views

app_name = 'post'

urlpatterns = [
    path('create/', views.create_post, name='create_post'),
    path('<int:post_id>/', views.post_detail_view, name='post_detail'),
    path('<int:post_id>/edit/', views.post_update_view, name='post_edit'),
    path('<int:post_id>/delete/', views.post_delete_view, name='post_delete'),
    path('<int:post_id>/unavailable/', views.post_unavailable, name='post_unavailable'),

    # ✅ ปิดรับสมัคร (กดจากหน้าแก้ไข)
    path('<int:post_id>/close-registration/', views.close_registration, name='close_registration'),

    path('map/', views.map_overview, name='map_overview'),

    # ✅ AJAX routes
    path('<int:post_id>/toggle-like/', views.toggle_like, name='toggle_like'),
    path('<int:post_id>/toggle-save/', views.toggle_save, name='toggle_save'),

    path('liked/', views.liked_posts_view, name='liked_posts'),
    path('saved/', views.saved_posts_view, name='saved_posts'),
]

