
from django import forms
from .models import Post

class PostForm(forms.ModelForm):
    # widget วันที่/เวลา
    event_date = forms.DateTimeField(
        widget=forms.DateTimeInput(attrs={
            'type': 'datetime-local',
            'class': 'form-control'
        }),
        label="วันที่จัดกิจกรรม"
    )

    class Meta:
        model = Post
        fields = [
            'title',
            'location',
            'event_date',
            'fee',              # ✅ ค่าใช้จ่าย
            'description',
            'slots_available',
            'allow_register',   # ✅ เปิด/ปิดให้สมัคร
            'category',
            'image',
            'schedule',
            'map_lat',
            'map_lng',
            'create_group',     # ✅ เลือกสร้างกลุ่ม
        ]

        widgets = {
            'title': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'ชื่อกิจกรรม'
            }),
            'location': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'สถานที่จัดกิจกรรม'
            }),
            'description': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 3,
                'placeholder': 'คำอธิบาย/รายละเอียดกิจกรรม'
            }),
            'fee': forms.NumberInput(attrs={
                'class': 'form-control',
                'min': 0,
                'placeholder': 'ค่าใช้จ่าย (บาท) หากไม่มีเว้นว่างไว้'
            }),
            'slots_available': forms.NumberInput(attrs={
                'class': 'form-control',
                'min': 1,
                'placeholder': 'จำนวนคนที่รับสมัคร'
            }),
            'allow_register': forms.CheckboxInput(attrs={
                'class': 'form-check-input',
            }),
            'category': forms.Select(attrs={
                'class': 'form-select'
            }),
            'image': forms.ClearableFileInput(attrs={
                'class': 'form-control'
            }),
            'schedule': forms.ClearableFileInput(attrs={
                'class': 'form-control'
            }),
            'map_lat': forms.HiddenInput(),
            'map_lng': forms.HiddenInput(),
            'create_group': forms.CheckboxInput(attrs={
                'class': 'form-check-input',
            }),
        }
