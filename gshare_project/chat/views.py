from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from .models import ChatGroup, DirectMessageThread, Message
from django.contrib.auth.models import User
from django.utils.text import slugify

# Create your views here.
@login_required
def groups_page(request):
    user_groups = ChatGroup.objects.filter(members=request.user)
    
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        try:
            other_user = User.objects.get(username=username)
            if other_user == request.user:
                messages.error(request, "You cannot start a direct message with yourself.")
                return redirect('groups_page')
            else:
                thread, created = DirectMessageThread.get_or_create_thread(request.user, other_user)
                if created:
                    messages.success(request, f"Direct message thread created with {other_user.username}.")
                else:
                    messages.info(request, f"Direct message thread already exists with {other_user.username}.")
                return redirect('direct_message', thread_id=thread.id)
        except User.DoesNotExist:
            messages.error(request, "User does not exist.")
            
    return render(request, 'chat/groups.html', {'groups': user_groups})

@login_required
def chat_room(request, room_name):
    try:
        room = ChatGroup.objects.get(slug=room_name)
        messages = room.messages.all().order_by('timestamp')
        user_groups = ChatGroup.objects.filter(members=request.user)
        members = room.members.all()
        
        DMs = DirectMessageThread.objects.filter(participants=request.user)
        # for each DM, get the other participant
        dm_list = [
            {
                "id": dm.id,
                "other_user": dm.participants.exclude(id=request.user.id).first()
            }
            for dm in DMs
        ]
        # show all the other users in the DM's then display all the DM's
        return render(request, 'chat/chat_room.html', {'room_name': room_name, 'room_code': room.group_code, 'messages': messages, 'groups': user_groups, 'members': members, 'dm_list':dm_list, 'user': request.user})
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
            return redirect('create_group')
        
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

@login_required
def direct_message(request, thread_id):
    print("Direct message view called with thread_id:", thread_id)
    thread = DirectMessageThread.objects.filter(id=thread_id, participants=request.user).first()
    if not thread:
        messages.error(request, "Direct message thread does not exist or you do not have access.")
        return redirect('groups_page')
    else:
        messages_qs = Message.objects.filter(thread=thread).order_by('timestamp')
        
        other_user = thread.participants.exclude(id=request.user.id).first()
        user_groups = ChatGroup.objects.filter(members=request.user)
        DMs = DirectMessageThread.objects.filter(participants=request.user)
        # for each DM, get the other participant
        dm_list = [
            {
                "id": dm.id,
                "other_user": dm.participants.exclude(id=request.user.id).first()
            }
            for dm in DMs
        ]
        return render(request, 'chat/chat_room.html', {'thread': thread, 'messages': messages_qs, 'other_user': other_user, 'groups': user_groups, 'dm_list': dm_list, 'user': request.user})
        
        
        

