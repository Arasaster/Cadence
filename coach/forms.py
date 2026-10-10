from zoneinfo import available_timezones

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ValidationError

from .models import Profile

User = get_user_model()

COMMON_TZ = [
    "Africa/Lagos", "Africa/Accra", "Africa/Nairobi", "Africa/Johannesburg", "Africa/Cairo",
    "Europe/London", "Europe/Paris", "America/New_York", "America/Chicago", "America/Los_Angeles",
    "America/Sao_Paulo", "Asia/Dubai", "Asia/Kolkata", "Asia/Singapore", "Asia/Tokyo", "Australia/Sydney", "UTC",
]


class SignupForm(forms.Form):
    email = forms.EmailField(label="Email")
    password = forms.CharField(label="Password", widget=forms.PasswordInput, min_length=8)

    def clean_email(self):
        email = self.cleaned_data["email"].lower().strip()
        if User.objects.filter(username=email).exists():
            raise ValidationError("An account with this email already exists. Log in instead.")
        return email


class LoginForm(AuthenticationForm):
    username = forms.EmailField(label="Email")

    def clean_username(self):
        return self.cleaned_data["username"].lower().strip()


class ProfileForm(forms.ModelForm):
    timezone = forms.ChoiceField(choices=[(t, t) for t in COMMON_TZ], label="Your timezone")

    class Meta:
        model = Profile
        fields = [
            "x_username", "niche", "goal", "audience", "offer", "tone", "admired", "voice_samples",
            "posts_per_day", "reply_target", "daily_minutes", "timezone", "email_reminders",
        ]
        labels = {
            "x_username": "Your X username",
            "niche": "What do you post about?",
            "goal": "Main goal on X",
            "audience": "Who do you want to reach?",
            "offer": "What do you offer? (optional)",
            "tone": "Your tone",
            "admired": "Accounts whose style you like (optional)",
            "voice_samples": "Paste 3 to 5 of your best posts (optional)",
            "posts_per_day": "Original posts per day",
            "reply_target": "Replies per day",
            "daily_minutes": "Minutes you can spend per day",
            "email_reminders": "Email me when each task is due",
        }
        help_texts = {
            "x_username": "Without the @. Used only to personalise your plan.",
            "niche": "Example: personal finance for Nigerian students",
            "audience": "Example: final-year students who feel behind",
            "offer": "Example: a thesis formatting tool, design services, a course",
            "admired": "Example: @naval, @dankoe",
            "voice_samples": "The more of your real writing the AI sees, the more the posts sound like you.",
            "reply_target": "Start with 15 to 25. Raise it once the habit sticks. Very high volumes can look like spam to X.",
            "daily_minutes": "The plan is sized to fit this.",
        }
        widgets = {
            "x_username": forms.TextInput(attrs={"placeholder": "yourhandle"}),
            "voice_samples": forms.Textarea(attrs={"rows": 5}),
            "posts_per_day": forms.NumberInput(attrs={"min": 1, "max": 5}),
            "reply_target": forms.NumberInput(attrs={"min": 0, "max": 100}),
            "daily_minutes": forms.NumberInput(attrs={"min": 10, "max": 240, "step": 5}),
        }

    def clean_x_username(self):
        return self.cleaned_data["x_username"].strip().lstrip("@")

    def clean_posts_per_day(self):
        v = self.cleaned_data["posts_per_day"]
        if not 1 <= v <= 5:
            raise ValidationError("Choose between 1 and 5 posts per day.")
        return v

    def clean_reply_target(self):
        v = self.cleaned_data["reply_target"]
        if not 0 <= v <= 100:
            raise ValidationError("Choose between 0 and 100 replies per day.")
        return v

    def clean_daily_minutes(self):
        v = self.cleaned_data["daily_minutes"]
        if not 10 <= v <= 240:
            raise ValidationError("Choose between 10 and 240 minutes.")
        return v


class ReplyForm(forms.Form):
    author = forms.CharField(label="Who wrote it? (optional)", required=False, max_length=40)
    post_text = forms.CharField(
        label="Paste the post you want to reply to",
        widget=forms.Textarea(attrs={"rows": 5}),
        max_length=1500,
    )


class ArchiveForm(forms.Form):
    file = forms.FileField(label="tweets.js from your X archive")
