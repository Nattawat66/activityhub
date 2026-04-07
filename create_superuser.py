import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "activityhub.settings")
django.setup()

from django.contrib.auth import get_user_model

User = get_user_model()

# email = os.environ.get("DJANGO_SUPERUSER_USERNAME")
email = os.environ.get("DJANGO_SUPERUSER_EMAIL")
password = os.environ.get("DJANGO_SUPERUSER_PASSWORD")

if email and password:
    if not User.objects.filter(email=email).exists():
        User.objects.create_superuser(
            email=email,
            password=password
        )
        print("Superuser created")
    else:
        print("Superuser already exists")
else:
    print("Superuser credentials not provided")