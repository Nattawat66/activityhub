from django.urls import path
from . import views

app_name = "notifications"

urlpatterns = [
    path("api/list/", views.api_list_notifications, name="api_list"),
    path("api/chat-unread/", views.api_chat_unread, name="api_chat_unread"),
    path("api/can-view-post/", views.api_can_view_post, name="api_can_view_post"),
    path("api/read/<int:notif_id>/", views.api_mark_read, name="api_mark_read"),
    path("mark-as-read/", views.mark_notification_as_read, name="mark_notification_as_read"),
    path("api/mark-chat-read/", views.api_mark_chat_read, name="api_mark_chat_read"),
]
