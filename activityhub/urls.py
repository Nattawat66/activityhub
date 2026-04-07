"""
URL configuration for activityhub project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
# from django.contrib import admin
# from django.urls import path , include

# urlpatterns = [
#     path('admin/', admin.site.urls),
#     path('users/', include('login_register.urls')),
#     path('',include('home.urls')),
#     path('approver/', include('approver.urls')),
#     path('post/', include('post.urls')),
#     path('user/', include('users.urls')),
# ]

# from django.conf import settings            
# from django.conf.urls.static import static 

# if settings.DEBUG:
#     urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('admin/', admin.site.urls),
    path('users/', include('login_register.urls')),
    path('', include('home.urls')),
    path('approver/', include('approver.urls')),
    path('post/', include(('post.urls', 'post'), namespace='post')),
    path('user/', include('users.urls')),
    path('activity/', include('activity_register.urls', namespace='activity_register')),
    path('chat/', include(('chat.urls', 'chat'), namespace='chat')),
    path("notifications/", include(("notifications.urls", "notifications"), namespace="notifications")),

]

# หมายเหตุ: ไม่ต้องใช้ static(MEDIA_URL) อีกต่อไป
# Cloudinary จัดการ URL ของ media files โดยตรง ทั้งใน development และ production

