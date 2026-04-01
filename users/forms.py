# users/forms.py
from django import forms
from .models import User, Profile


class UserUpdateForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['first_name', 'last_name']
        widgets = {
            'first_name': forms.TextInput(attrs={'class': 'form-control'}),
            'last_name': forms.TextInput(attrs={'class': 'form-control'}),
        }


class ProfileUpdateForm(forms.ModelForm):
    class Meta:
        model = Profile
        fields = [
            'profile_picture',
            'nickname',
            'birth_date',
            'address',
            'gender',
            'phone',
            'contact_info',
        ]
        widgets = {
            'profile_picture': forms.FileInput(
                attrs={
                    'id': 'id_profile_picture',
                    'style': 'display:none;',
                    'accept': 'image/*',
                }
            ),
            'nickname': forms.TextInput(attrs={'class': 'form-control'}),
            'birth_date': forms.DateInput(
                attrs={'type': 'date', 'class': 'form-control'}
            ),
            'address': forms.Textarea(
                attrs={'class': 'form-control', 'rows': 2}
            ),
            'gender': forms.Select(
                choices=[('ชาย', 'ชาย'), ('หญิง', 'หญิง'), ('อื่นๆ', 'อื่นๆ')],
                attrs={'class': 'form-select'}
            ),
            'phone': forms.TextInput(attrs={'class': 'form-control'}),
            'contact_info': forms.TextInput(attrs={'class': 'form-control'}),
        }


# ✅ เพิ่ม: ฟอร์มยืนยันลบบัญชี (2 ชั้น + รหัสผ่าน)
class DeleteAccountForm(forms.Form):
    confirm_1 = forms.BooleanField(
        required=True,
        label="ฉันเข้าใจว่าการลบจะทำให้บัญชีถูกปิดการใช้งานและซ่อนข้อมูล"
    )
    confirm_2 = forms.BooleanField(
        required=True,
        label="ฉันยืนยันว่าต้องการลบบัญชีนี้จริง ๆ"
    )
    password = forms.CharField(
        required=True,
        widget=forms.PasswordInput(attrs={'class': 'form-control'}),
        label="รหัสผ่านเพื่อยืนยัน"
    )
