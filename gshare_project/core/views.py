from django.shortcuts import render

# Create your views here.
def home(request):
    return render(request, 'home.html')

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