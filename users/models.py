# Create your models here.
from django.db import models
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.utils.translation import gettext_lazy as _
from django.utils import timezone  # ✅ เพิ่ม (เกี่ยวข้องกับ soft delete)


class CustomUserManager(BaseUserManager):
    """
    Custom user model manager where email is the unique identifiers
    for authentication instead of usernames.
    """
    def create_user(self, email, password, **extra_fields):
        """
        Create and save a User with the given email and password.
        """
        if not email:
            raise ValueError(_('The Email must be set'))
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save()
        return user

    def create_superuser(self, email, password, **extra_fields):
        """
        Create and save a SuperUser with the given email and password.
        """
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('is_active', True)

        if extra_fields.get('is_staff') is not True:
            raise ValueError(_('Superuser must have is_staff=True.'))
        if extra_fields.get('is_superuser') is not True:
            raise ValueError(_('Superuser must have is_superuser=True.'))
        return self.create_user(email, password, **extra_fields)


class User(AbstractUser):
    # --- 1. สร้าง Choices สำหรับ Role ---
    class Role(models.TextChoices):
        USER = 'USER', 'User'
        APPROVER = 'APPROVER', 'Approver'
        ADMIN = 'ADMIN', 'Admin'

    username = None
    email = models.EmailField(_('email address'), unique=True, primary_key=True)

    # --- 2. เพิ่ม Field 'role' ---
    role = models.CharField(max_length=50, choices=Role.choices, default=Role.USER)

    # ✅ (เพิ่ม) Soft Delete
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []

    objects = CustomUserManager()

    def get_full_name(self):
        """
        Return the first_name plus the last_name, with a space in between.
        """
        return f"{self.first_name} {self.last_name}".strip()

    def soft_delete(self):
        """
        ✅ ปิดการใช้งาน + mark ว่าถูกลบ (soft delete)
        """
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.is_active = False
        self.save(update_fields=["is_deleted", "deleted_at", "is_active"])

    def __str__(self):
        return self.email


class Profile(models.Model):
    # เชื่อม Profile กับ User แบบหนึ่งต่อหนึ่ง
    user = models.OneToOneField(User, on_delete=models.CASCADE, primary_key=True)

    # รูปโปรไฟล์ / cover
    profile_picture = models.ImageField(
        upload_to='profile_pics/',
        null=True,
        blank=True,
        default='profile_pics/default.jpg'
    )
    # ถ้ามี field นี้อยู่แล้วใน migration สามารถคงชื่อ/พาธให้ตรงของเดิม
    cover_picture = models.ImageField(
        upload_to='cover_pics/',
        null=True,
        blank=True
    )

    # ข้อมูลส่วนตัว
    nickname = models.CharField(max_length=100, blank=True)
    birth_date = models.DateField(null=True, blank=True)
    phone_number = models.CharField(max_length=20, blank=True)
    address = models.TextField(blank=True)
    contact_info = models.CharField(
        max_length=255,
        blank=True,
        help_text="เช่น Line ID, Facebook"
    )
    email = models.EmailField(blank=True, null=True)
    gender = models.CharField(
        max_length=10,
        choices=[('ชาย', 'ชาย'), ('หญิง', 'หญิง'), ('อื่นๆ', 'อื่นๆ')],
        blank=True
    )
    phone = models.CharField(max_length=20, blank=True)
    address = models.TextField(blank=True)

    # ✅ ระบบติดตาม (โปรไฟล์ติดตามโปรไฟล์)
    followers = models.ManyToManyField(
        "self",
        symmetrical=False,
        related_name="following_profiles",
        blank=True
    )

    def followers_count(self):
        return self.followers.count()

    def following_count(self):
        return Profile.objects.filter(followers=self).count()

    def __str__(self):
        return f'{self.user.email} Profile'

