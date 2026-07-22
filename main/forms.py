from django import forms
from django.contrib.auth.models import User
from django.contrib.auth.forms import UserCreationForm
from .models import UserProfile


# Custom registration form keeps account creation focused on the needs of this app.
class ChessTutorUserCreationForm(UserCreationForm):
    # Usernames are shortened from Django's default so the navbar stays clean.
    username = forms.CharField(
        min_length=3,
        max_length=15,
        help_text='Required. 3 to 15 characters. Letters, numbers and @/./+/-/_ only.',
        widget=forms.TextInput(
            attrs={
                'autofocus': True,
                'minlength': '3',
                'maxlength': '15',
                'placeholder': 'Username',
            }
        ),
        error_messages={
            'min_length': 'Username must be at least 3 characters long.',
            'max_length': 'Username must be 15 characters or fewer.',
            'required': 'Enter a username.',
        },
    )


    # Email is required because accounts are verified before full use.
    email = forms.EmailField(
        required=True,
        help_text='Required. This is used to verify your Chess Tutor account.',
        widget=forms.EmailInput(
            attrs={
                'placeholder': 'Email address',
            }
        ),
        error_messages={
            'required': 'Enter an email address.',
            'invalid': 'Enter a valid email address.',
        },
    )


    # Prevents duplicate accounts from being created with the same email address.
    def clean_email(self):
        email = self.cleaned_data['email'].lower()

        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError('An account already exists with this email address.')

        return email


    # Copies the cleaned email onto the User object before saving it.
    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data['email']

        if commit:
            user.save()

        return user



# Handles profile picture uploads from the settings screen.
class ProfilePictureForm(forms.ModelForm):
    avatar = forms.FileField(
        required=False,
        label='Profile picture',
        help_text='Upload a JPG, PNG or WebP image under 3 MB.',
        widget=forms.ClearableFileInput(
            attrs={
                'accept': 'image/png,image/jpeg,image/webp',
            }
        ),
    )

    class Meta:
        model = UserProfile
        fields = ['avatar']


    # Checks file type and size before accepting a profile picture.
    def clean_avatar(self):
        avatar = self.cleaned_data.get('avatar')

        if not avatar:
            return avatar

        allowed_extensions = ('.jpg', '.jpeg', '.png', '.webp')

        if not avatar.name.lower().endswith(allowed_extensions):
            raise forms.ValidationError('Upload a JPG, PNG or WebP image.')


        # The size limit keeps uploaded images sensible for a school project server.
        if avatar.size > 3 * 1024 * 1024:
            raise forms.ValidationError('Profile picture must be under 3 MB.')

        content_type = getattr(avatar, 'content_type', '')
        allowed_types = {'image/jpeg', 'image/png', 'image/webp'}

        if content_type and content_type not in allowed_types:
            raise forms.ValidationError('Upload a JPG, PNG or WebP image.')

        return avatar
