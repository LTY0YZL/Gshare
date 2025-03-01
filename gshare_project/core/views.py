from django.shortcuts import render

# Create your views here.
def homepage(request):
    return render(request, 'home.html')

def aboutus(request):
    return render(request, "aboutus.html")

def userprofile(request):
    return render(request, "userprofile.html")

def menu(request):
    return render(request, "menu.html")