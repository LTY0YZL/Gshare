from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib import messages
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required

# Create your views here.
def home(request):
    return render(request, 'home.html')

def aboutus(request):
    return render(request, "aboutus.html")

@login_required
def userprofile(request):
    return render(request, "userprofile.html")

@login_required
def menu(request):
    return render(request, "menu.html")

@login_required
def shoppingcart(request):
    return render(request, "shoppingcart.html")

@login_required
def groups(request):
    return render(request, "groups.html")

@login_required
def cart(request):
    return render(request, "cart.html")

def login_view(request):
    if request.method == 'POST':
        username = request.POST['username']
        password = request.POST['password']
        user = authenticate(request, username=username, password=password)
        
        if user is not None:
            login(request, user)
            next_url = request.GET.get('next', 'home')
            return redirect(next_url)
        else:
            messages.error(request, "Invalid username or password.")
    
    return render(request, 'login.html')

def signup_view(request):
    if request.method == 'POST':
        username = request.POST['username']
        email = request.POST['email']
        password = request.POST['password']
        
        # Check if user already exists
        if User.objects.filter(username=username).exists():
            messages.error(request, "Username already taken.")
            return redirect('login')
            
        # Create user
        user = User.objects.create_user(username=username, email=email, password=password)
        login(request, user)
        return redirect('home')
    
    return redirect('login')

def logout_view(request):
    logout(request)
    return redirect('login')

@login_required
def maps(request):
    return render(request, "maps.html")