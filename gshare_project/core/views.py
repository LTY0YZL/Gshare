
from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib import messages
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.conf import settings
from core.models import Items, Stores
from django.db.models import Q

# Create your views here.
def home(request):
    context = {
        'google_maps_api_key': settings.GOOGLE_MAPS_API_KEY,
        'location':{'lat': 40.7607, 'lng': -111.8939},
    }
    return render(request, 'home.html', context)

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
    store_filter = request.GET.get('Stores', 'All')
    price_filter = request.GET.get('Price-Range', 'Any')
    search_query = request.GET.get('Item_Search_Bar', '')

    items = Items.objects.using('gsharedb').all()

    if store_filter and store_filter != 'All':
        items = items.filter(store__name=store_filter)

    if price_filter and price_filter != 'Any':
        if price_filter == '100+':
            items = items.filter(price__gte=100)
        else:
            low, high = map(float, price_filter.split('-'))
            items = items.filter(price__gte=low, price__lte=high)

    if search_query:
        items = items.filter(name__icontains=search_query)

    context = {
        'items': items
    }
    
    return render(request, "cart.html", context)


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
    context = {
        'google_maps_api_key': settings.GOOGLE_MAPS_API_KEY,
        'location':{'lat': 40.7607, 'lng': -111.8939},
    }
    return render(request, "maps.html", context)