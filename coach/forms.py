from zoneinfo import available_timezones

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ValidationError

from .models import Profile
from .style import (
    ACCOUNT_TYPE_CHOICES, BIO_GOALS, BIO_ROLES, DEFAULT_INTENT, INTENTS, LENGTH_CHOICES, SLIDERS, VOICES,
)

User = get_user_model()

COMMON_TZ = [
    "Africa/Lagos", "Africa/Accra", "Africa/Nairobi", "Africa/Johannesburg", "Africa/Cairo",
    "Europe/London", "Europe/Paris", "America/New_York", "America/Chicago", "America/Los_Angeles",
    "America/Sao_Paulo", "Asia/Dubai", "Asia/Kolkata", "Asia/Singapore", "Asia/Tokyo", "Australia/Sydney", "UTC",
]


class SignupForm(forms.Form):
    email = forms.EmailField(label="Email")
    password = forms.CharField(label="Password", widget=forms.PasswordInput, min_length=8)
    agree = forms.BooleanField(
        label="I agree to the Terms of Service and Privacy Policy",
        error_messages={"required": "You need to accept the Terms and Privacy Policy to continue."},
    )

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
            "account_type", "x_username", "current_followers", "target_followers", "target_note", "niche", "goal", "audience", "offer", "tone", "admired", "voice_samples",
            "posts_per_day", "reply_target", "daily_minutes", "timezone", "email_reminders",
        ]
        labels = {
            "account_type": "What kind of account is this?",
            "current_followers": "Followers right now (optional)",
            "target_followers": "Followers you want (optional)",
            "target_note": "What does success look like?",
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
            "account_type": "This changes the voice of every post, reply, bio and plan.",
            "target_note": "Example: 1,000 followers and 5 client enquiries a month",
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
    link = forms.CharField(
        label="Post link", required=False, max_length=300,
        widget=forms.TextInput(attrs={"placeholder": "Paste an X post link", "inputmode": "url", "autocomplete": "off"}),
    )
    post_text = forms.CharField(
        label="Post text", required=False, widget=forms.Textarea(attrs={"rows": 4}), max_length=1500,
        help_text="Filled in when you fetch a link. You can also paste it.",
    )
    author = forms.CharField(label="Who wrote it?", required=False, max_length=40)
    author_bio = forms.CharField(label="Their bio", required=False, max_length=300)
    post_meta = forms.CharField(required=False, max_length=120, widget=forms.HiddenInput)
    top_replies = forms.CharField(
        label="Paste top replies", required=False, widget=forms.Textarea(attrs={"rows": 3}), max_length=1500,
        help_text="Paste a few top replies. Cadence checks what has already been said and finds the gap.",
    )
    intent = forms.ChoiceField(
        label="What do you want this reply to do?", choices=[(k, l) for k, l, _ in INTENTS],
        initial=DEFAULT_INTENT, widget=forms.RadioSelect,
    )
    voice = forms.ChoiceField(label="Voice", choices=VOICES, initial="my_voice", widget=forms.RadioSelect)
    length = forms.ChoiceField(label="Length", choices=LENGTH_CHOICES, initial="balanced", widget=forms.RadioSelect)
    instruction = forms.CharField(
        label="Anything else? (optional)", required=False, max_length=160,
        widget=forms.TextInput(attrs={"placeholder": "Example: Make it curious, not argumentative."}),
    )
    force = forms.BooleanField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.slider_fields = []
        for key, low, high in SLIDERS:
            name = f"s_{key}"
            self.fields[name] = forms.IntegerField(
                required=False, min_value=0, max_value=100, initial=50,
                widget=forms.NumberInput(attrs={"type": "range", "min": 0, "max": 100, "step": 5}),
            )
            self.slider_fields.append({"key": key, "low": low, "high": high, "field": self[name]})

    def clean(self):
        data = super().clean()
        if not (data.get("post_text") or data.get("link")):
            raise ValidationError("Paste a post link or the post text.")
        return data

    def sliders(self):
        out = {}
        for key, _, _ in SLIDERS:
            v = self.cleaned_data.get(f"s_{key}")
            out[key] = 50 if v is None else v
        return out

    def settings(self):
        d = self.cleaned_data
        return {"intent": d["intent"], "voice": d["voice"], "length": d["length"],
                "instruction": d.get("instruction", ""), "sliders": self.sliders()}


class BioForm(forms.Form):
    current_bio = forms.CharField(
        label="Your current bio", widget=forms.Textarea(attrs={"rows": 3, "id": "bioInput"}), max_length=300,
    )
    goal = forms.ChoiceField(
        label="What do you want your profile to do?", choices=BIO_GOALS, initial="personal_brand",
        widget=forms.RadioSelect,
    )
    role = forms.ChoiceField(label="What best describes you?", choices=BIO_ROLES, initial="founder")
    notes = forms.CharField(
        label="Things to keep, mention, or avoid", required=False, max_length=300,
        widget=forms.Textarea(attrs={"rows": 2, "placeholder": "Example: Keep 'brand designer' and my website. Don't mention my location."}),
    )
    use_data = forms.BooleanField(
        label="Use my Cadence data (archive, topics, best posts) to check my bio against what I actually post",
        required=False, initial=True,
    )


class RoadmapForm(forms.Form):
    days = forms.ChoiceField(
        label="Plan length",
        choices=[("30", "30 days"), ("60", "60 days")],
        initial="30",
        widget=forms.RadioSelect,
    )
    note = forms.CharField(
        label="Anything the plan should focus on (optional)",
        required=False,
        max_length=300,
        widget=forms.TextInput(attrs={"placeholder": "Example: I want clients, not just followers"}),
    )


class ArchiveForm(forms.Form):
    file = forms.FileField(label="tweets.js from your X archive")