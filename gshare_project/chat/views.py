from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from .models import ChatGroup
from django.utils.text import slugify

# Create your views here.
@login_required
def groups_page(request):
    user_groups = ChatGroup.objects.filter(members=request.user)
    return render(request, 'chat/groups.html', {'groups': user_groups})

@login_required
def chat_room(request, room_name):
    try:
        room = ChatGroup.objects.get(slug=room_name)
        messages = room.messages.all().order_by('timestamp')
        user_groups = ChatGroup.objects.filter(members=request.user)
        members = room.members.all()
        return render(request, 'chat/chat_room.html', {'room_name': room_name, 'room_code': room.group_code, 'messages': messages, 'groups': user_groups, 'members': members})
    except ChatGroup.DoesNotExist:
        messages.error(request, "Chat room does not exist.")
        return redirect('groups_page')

@login_required
def create_group(request):
    user_groups = ChatGroup.objects.filter(members=request.user)
    if request.method == 'POST':
        group_name = request.POST.get('group_name', '').strip()
        if not group_name:
            messages.error(request, "Group name cannot be empty.")
            return redirect(request, 'create_group')
        
        if ChatGroup.objects.filter(name=group_name).exists():
            messages.error(request, 'A group with this name already exists.')
            return redirect('create_group')
        
        group = ChatGroup.objects.create(name=group_name, slug=slugify(group_name))
        group.members.add(request.user)
        messages.success(request, f"Group '{group_name}' created successfully. Group Code: {group.group_code}")
        return redirect('chat_room', room_name=group.slug)

    return render(request, 'chat/create_group.html', {'groups': user_groups})

@login_required
def join_group(request):
    if request.method == 'POST':
        group_code = request.POST.get('group_code', '').strip()
        try:
            group = ChatGroup.objects.get(group_code=group_code)
            group.members.add(request.user)
            messages.success(request, f"you have successfully joined the group '{group.name}'.")
            return redirect('chat_room', room_name=group.slug)
        except ChatGroup.DoesNotExist:
            messages.error(request, "Invalid group code.")
            return redirect('join_group')
    
    return render(request, 'chat/join_group.html')

