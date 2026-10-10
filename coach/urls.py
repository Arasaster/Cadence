from django.contrib.auth import views as auth_views
from django.urls import path

from . import views
from .forms import LoginForm

urlpatterns = [
    path("", views.landing, name="landing"),
    path("signup/", views.signup, name="signup"),
    path("login/", auth_views.LoginView.as_view(authentication_form=LoginForm), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("setup/", views.onboarding, name="onboarding"),
    path("today/", views.today, name="today"),
    path("plan/generate/", views.generate, name="generate"),
    path("tasks/<int:pk>/toggle/", views.toggle_task, name="toggle_task"),
    path("replies/", views.replies, name="replies"),
    path("insights/", views.insights, name="insights"),
    path("cron/<str:job>/", views.cron, name="cron"),
]
