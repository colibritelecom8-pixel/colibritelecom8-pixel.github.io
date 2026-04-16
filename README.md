# =========================
# PROJECT STRUCTURE
# =========================
# modsite/
# ├── manage.py
# ├── modsite/
# │   ├── __init__.py
# │   ├── settings.py
# │   ├── urls.py
# │   ├── asgi.py
# │   └── wsgi.py
# ├── core/
# │   ├── __init__.py
# │   ├── models.py
# │   ├── views.py
# │   ├── forms.py
# │   ├── urls.py
# │   ├── utils.py
# │   └── admin.py
# └── templates/
#     ├── base.html
#     ├── register.html
#     ├── login.html
#     ├── dashboard.html
#     ├── reports.html
#     └── shop.html

# =========================
# INSTALL
# =========================
# pip install django
# django-admin startproject modsite
# cd modsite
# python manage.py startapp core
# python manage.py migrate
# python manage.py createsuperuser
# python manage.py runserver

# =========================
# settings.py (IMPORTANT PARTS)
# =========================
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'core'
]

EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'
DEFAULT_FROM_EMAIL = 'noreply@example.com'

MEDIA_ROOT = 'media'
MEDIA_URL = '/media/'

# =========================
# core/models.py
# =========================
from django.db import models
from django.contrib.auth.models import User
import random

class Profile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    is_approved = models.BooleanField(default=False)
    email_code = models.CharField(max_length=6, blank=True)

class Report(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    nickname = models.CharField(max_length=100)
    date = models.DateField()
    description = models.TextField(blank=True)
    file = models.FileField(upload_to='reports/')

class Item(models.Model):
    name = models.CharField(max_length=100)
    chance = models.IntegerField(default=1)

class Case(models.Model):
    name = models.CharField(max_length=100)
    items = models.ManyToManyField(Item)

    def open_case(self):
        pool = []
        for item in self.items.all():
            pool.extend([item]*item.chance)
        return random.choice(pool)

class Log(models.Model):
    text = models.TextField()
    created = models.DateTimeField(auto_now_add=True)

# =========================
# core/forms.py
# =========================
from django import forms
from django.contrib.auth.models import User
from .models import Report

class RegisterForm(forms.ModelForm):
    password = forms.CharField(widget=forms.PasswordInput)

    class Meta:
        model = User
        fields = ['username', 'email', 'password']

class ReportForm(forms.ModelForm):
    class Meta:
        model = Report
        fields = ['nickname', 'date', 'description', 'file']

# =========================
# core/utils.py
# =========================
import random

def generate_code():
    return str(random.randint(100000, 999999))


def send_vk_message(text):
    print("[VK API]", text)  # заглушка

# =========================
# core/views.py
# =========================
from django.shortcuts import render, redirect
from django.contrib.auth import login, authenticate
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from .forms import RegisterForm, ReportForm
from .models import Profile, Report, Case, Log
from .utils import generate_code, send_vk_message


def register(request):
    if request.method == 'POST':
        form = RegisterForm(request.POST)
        if form.is_valid():
            user = form.save(commit=False)
            user.set_password(form.cleaned_data['password'])
            user.save()

            code = generate_code()
            Profile.objects.create(user=user, email_code=code)

            print(f"Email code: {code}")
            return redirect('login')
    else:
        form = RegisterForm()
    return render(request, 'register.html', {'form': form})

@login_required
def dashboard(request):
    profile = request.user.profile
    if not profile.is_approved:
        return render(request, 'dashboard.html', {'pending': True})
    return render(request, 'dashboard.html')

@login_required
def reports(request):
    if request.method == 'POST':
        form = ReportForm(request.POST, request.FILES)
        if form.is_valid():
            report = form.save(commit=False)
            report.user = request.user
            report.save()

            send_vk_message(f"New report: {report.nickname}")
            Log.objects.create(text=f"Report created by {request.user}")

    else:
        form = ReportForm()
    return render(request, 'reports.html', {'form': form})

@login_required
def shop(request):
    cases = Case.objects.all()
    result = None
    if 'open' in request.GET:
        case = Case.objects.get(id=request.GET['open'])
        result = case.open_case()
        Log.objects.create(text=f"{request.user} opened case")
    return render(request, 'shop.html', {'cases': cases, 'result': result})

# =========================
# core/admin.py
# =========================
from django.contrib import admin
from .models import Profile, Report, Item, Case, Log

admin.site.register(Profile)
admin.site.register(Report)
admin.site.register(Item)
admin.site.register(Case)
admin.site.register(Log)

# =========================
# core/urls.py
# =========================
from django.urls import path
from . import views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('register/', views.register, name='register'),
    path('reports/', views.reports, name='reports'),
    path('shop/', views.shop, name='shop'),
]

# =========================
# modsite/urls.py
# =========================
from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('core.urls')),
]

# =========================
# templates/base.html
# =========================
"""
<!DOCTYPE html>
<html>
<head>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
<style>
body { background:#1e1e1e; color:white }
.card { background:#2b2b2b }
.btn-primary { background:#00ff88; border:none }
</style>
</head>
<body>
<div class="container mt-4">
{% block content %}{% endblock %}
</div>
</body>
</html>
"""

# =========================
# templates/register.html
# =========================
"""
{% extends 'base.html' %}
{% block content %}
<h2>Регистрация</h2>
<form method="post">{% csrf_token %}
{{ form.as_p }}
<button class="btn btn-primary">Создать</button>
</form>
{% endblock %}
"""

# =========================
# templates/dashboard.html
# =========================
"""
{% extends 'base.html' %}
{% block content %}
{% if pending %}
<h3>Ожидает одобрения</h3>
{% else %}
<h3>Добро пожаловать</h3>
<a href="/reports/">Отчеты</a>
<a href="/shop/">Магазин</a>
{% endif %}
{% endblock %}
"""

# =========================
# templates/reports.html
# =========================
"""
{% extends 'base.html' %}
{% block content %}
<h2>Отчет</h2>
<form method="post" enctype="multipart/form-data">{% csrf_token %}
{{ form.as_p }}
<button class="btn btn-primary">Отправить</button>
</form>
{% endblock %}
"""

# =========================
# templates/shop.html
# =========================
"""
{% extends 'base.html' %}
{% block content %}
<h2>Кейсы</h2>
{% for case in cases %}
<div class="card p-3 mb-2">
<h4>{{ case.name }}</h4>
<a href="?open={{ case.id }}" class="btn btn-primary">Открыть</a>
</div>
{% endfor %}

{% if result %}
<h3>Вы получили: {{ result.name }}</h3>
{% endif %}
{% endblock %}
"""
