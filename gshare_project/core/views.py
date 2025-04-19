from django.shortcuts import render
from django.conf import settings

# Create your views here.
def home(request):
    context = {
        'google_maps_api_key': settings.GOOGLE_MAPS_API_KEY,
        'location':{'lat': 40.7607, 'lng': -111.8939},
    }
    return render(request, 'home.html', context)

def aboutus(request):
    return render(request, "aboutus.html")

def userprofile(request):
    return render(request, "userprofile.html")

def menu(request):
    return render(request, "menu.html")

def shoppingcart(request):
    return render(request, "shoppingcart.html")

def groups(request):
    return render(request, "groups.html")

def cart(request):
    return render(request, "cart.html")

def login(request):
    return render(request, "login.html")

def signup(request):
    return render(request, "signup.html")

def maps(request):
    context = {
        'google_maps_api_key': settings.GOOGLE_MAPS_API_KEY,
        'location':{'lat': 40.7607, 'lng': -111.8939},
    }
    return render(request, "maps.html", context)