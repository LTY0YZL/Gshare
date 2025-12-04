from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from .models import ChatGroup, DirectMessageThread, Message, Notification
from django.contrib.auth.models import User
from django.utils.text import slugify
from django.http import JsonResponse
from django.contrib.auth.models import User
from django.views.decorators.http import require_GET

from django.http import JsonResponse
from .models import Message, ChatGroup, TypingState, DirectMessageThread, LastRead
from core.utils.aws_s3 import presigned_url, upload_image_to_aws

from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required

from django.http import StreamingHttpResponse
from django.db.models import Q
import json, time
from django.utils import timezone


from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from .models import Message
import json



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
        chat_messages = room.messages.all().order_by('timestamp')
        
        # Update LastRead for this user
        LastRead.objects.update_or_create(
            user=request.user,
            group=room,
            defaults={'last_read_at': timezone.now()}
        )
        
        # Add presigned URLs
        for msg in chat_messages:
            msg.image_url = presigned_url(msg.image) if msg.image else None

        user_groups = ChatGroup.objects.filter(members=request.user)
        members = room.members.all()
        unread_notifications = request.user.notifications.filter(is_read=False)
        unread_notifications.update(is_read=True)

        DMs = DirectMessageThread.objects.filter(participants=request.user)
        dm_list = [
            {
                "id": dm.id,
                "other_user": dm.participants.exclude(id=request.user.id).first()
            }
            for dm in DMs
        ]

        return render(request, 'chat/chat_room.html', {
            'room_name': room_name,
            'room_code': room.group_code,
            'messages': chat_messages,
            'groups': user_groups,
            'members': members,
            'dm_list': dm_list,
            'user': request.user,
            'notifications': unread_notifications
        })
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
    
        # Update LastRead for this user
    LastRead.objects.update_or_create(
        user=request.user,
        thread=thread,
        defaults={'last_read_at': timezone.now()}
    )
    
    if not thread:
        messages.error(request, "Direct message thread does not exist or you do not have access.")
        return redirect('groups_page')
    else:
        messages_qs = Message.objects.filter(thread=thread).order_by('timestamp')
        for msg in messages_qs:
            msg.image_url = presigned_url(msg.image) if msg.image else None        
        other_user = thread.participants.exclude(id=request.user.id).first()
        user_groups = ChatGroup.objects.filter(members=request.user)
        DMs = DirectMessageThread.objects.filter(participants=request.user)
        
        unread_notifications = request.user.notifications.filter(is_read=False)
        
        unread_notifications.update(is_read=True)
        # for each DM, get the other participant
        dm_list = [
            {
                "id": dm.id,
                "other_user": dm.participants.exclude(id=request.user.id).first()
            }
            for dm in DMs
        ]
        return render(request, 'chat/chat_room.html', {'thread': thread, 'messages': messages_qs, 'other_user': other_user, 'groups': user_groups, 'dm_list': dm_list, 'user': request.user, 'notifications': unread_notifications})
        
@require_GET
def autocomplete_usernames(request):
    query = request.GET.get("q", "")
    results = []
    if query:
        # take only the top 10 results
        users = User.objects.filter(username__icontains=query)[:10]
        results = list(users.values_list("username", flat=True))
    return JsonResponse(results, safe=False)

# # views.py
# def load_chat_history(request, room_slug=None, thread_id=None):
#     try:
#         messages = []
#         if room_slug:
#             group = ChatGroup.objects.get(slug=room_slug)
#             msgs = group.messages.order_by('timestamp')
#         elif thread_id:
#             thread = DirectMessageThread.objects.get(id=thread_id)
#             msgs = thread.messages.order_by('created_at')

#         for msg in msgs:
#             image_url = presigned_url(msg.image) if msg.image else None
#             messages.append({
#                 'id': msg.id,
#                 'username': msg.sender.username,
#                 'message': msg.content,
#                 'image_url': image_url
#             })

#         return JsonResponse({'messages': messages})
#     except Exception as e:
#         # Always return JSON
#         return JsonResponse({'error': str(e)}, status=500)




# @login_required
# @require_POST
# def send_message(request):
#     content = request.POST.get("content", "").strip()
#     group_code = request.POST.get("group_id")
#     thread_id = request.POST.get("thread_id")
#     image_key = request.POST.get("image_key")  # optional S3 key

#     if not content and not image_key:
#         return JsonResponse({"error": "Message must contain text or image"}, status=400)

#     group = None
#     thread = None

#     try:
#         if group_code:
#             group = ChatGroup.objects.get(group_code=group_code)
#         if thread_id:
#             thread = DirectMessageThread.objects.get(id=thread_id)
#     except (ChatGroup.DoesNotExist, DirectMessageThread.DoesNotExist):
#         return JsonResponse({"error": "Invalid group or thread"}, status=400)

#     msg = Message.objects.create(
#         sender=request.user,
#         content=content,
#         group=group,
#         thread=thread,
#         image=image_key if image_key else None
#     )

#     return JsonResponse({
#         "status": "ok",
#         "id": msg.id,
#         "username": msg.sender.username,
#         "content": msg.content,
#         "image_url": presigned_url(msg.image) if msg.image else None,
#         "timestamp": msg.timestamp.isoformat()
#     })



# @login_required
# def sse_stream_messages(request):
#     group_id = request.GET.get("group_id")
#     thread_id = request.GET.get("thread_id")
#     last_id = int(request.GET.get("last_id", 0))

#     def event_stream():
#         while True:
#             filters = Q()
#             if group_id:
#                 filters &= Q(group_id=group_id)
#             if thread_id:
#                 filters &= Q(thread_id=thread_id)

#             new_msgs = Message.objects.filter(filters, id__gt=last_id).order_by("id")

#             if new_msgs.exists():
#                 for m in new_msgs:
#                     last_id = m.id
#                     data_dict = {
#                         'id': m.id,
#                         'username': m.sender.username,
#                         'content': m.content,
#                         'image_url': presigned_url(m.image) if m.image else None,
#                         'timestamp': m.timestamp.isoformat()
#                     }
#                     yield f"data: {json.dumps(data_dict)}\n\n"

#                 return  # forces client reconnect
#             time.sleep(1)

#     return StreamingHttpResponse(event_stream(), content_type='text/event-stream')

# @login_required
# def sse_stream_notifications(request):
#     last_id = int(request.GET.get("last_id", 0))

#     def event_stream():
#         while True:
#             new_notifs = Notification.objects.filter(user=request.user, id__gt=last_id).order_by("id")
#             if new_notifs.exists():
#                 for n in new_notifs:
#                     last_id = n.id
#                     data_dict = {
#                         'id': n.id,
#                         'message': n.message,
#                         'is_read': n.is_read,
#                         'created_at': n.created_at.isoformat()
#                     }
#                     yield f"data: {json.dumps(data_dict)}\n\n"
#                 return
#             time.sleep(1)

#     return StreamingHttpResponse(event_stream(), content_type='text/event-stream')

# @login_required
# @require_POST
# def set_typing(request):
#     group_id = request.POST.get("group_id")
#     thread_id = request.POST.get("thread_id")
#     is_typing = request.POST.get("is_typing") == "true"

#     typing_state, _ = TypingState.objects.update_or_create(
#         user=request.user,
#         thread_id=thread_id if thread_id else None,
#         group_id=group_id if group_id else None,
#         defaults={"is_typing": is_typing, "last_update": timezone.now()}
#     )
#     return JsonResponse({"status": "ok"})


# @login_required
# def sse_typing(request):
#     group_id = request.GET.get("group_id")
#     thread_id = request.GET.get("thread_id")
#     last_time = float(request.GET.get("last_time", 0))

#     def event_stream():
#         nonlocal last_time
#         while True:
#             new_typing = TypingState.objects.filter(
#                 last_update__gt=timezone.datetime.fromtimestamp(last_time),
#                 thread_id=thread_id if thread_id else None,
#                 group_id=group_id if group_id else None
#             )
#             if new_typing.exists():
#                 for t in new_typing:
#                     last_time = t.last_update.timestamp()
#                     yield f"event: typing\ndata: {json.dumps({'user': t.user.username, 'is_typing': t.is_typing})}\n\n"
#             time.sleep(1)

#     return StreamingHttpResponse(event_stream(), content_type='text/event-stream')



@csrf_exempt
@login_required
def send_message(request):
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    content = request.POST.get("content", "").strip()
    image_file = request.FILES.get("image_file")
    group_id = request.POST.get("group_id")
    thread_id = request.POST.get("thread_id")

    if not content and not image_file:
        return JsonResponse({"error": "No content"}, status=400)

    # Determine target type (DM or Group)
    group = None
    thread = None

    if thread_id:
        try:
            thread = DirectMessageThread.objects.get(id=thread_id)
        except DirectMessageThread.DoesNotExist:
            return JsonResponse({"error": "Thread not found"}, status=404)

    elif group_id:
        try:
            group = ChatGroup.objects.get(slug=group_id)
        except ChatGroup.DoesNotExist:
            return JsonResponse({"error": "Group not found"}, status=404)

    else:
        return JsonResponse({"error": "Must include group_id or thread_id"}, status=400)
    
    image_key = None
    if image_file:
        image_key = upload_image_to_aws(image_file, folder='chat')
    
    if not content and not image_key:
        return JsonResponse({"error": "Message must contain text or image"}, status=400)

    # Create message
    msg = Message.objects.create(
        sender=request.user,
        content=content,
        image=image_key,
        group=group,
        thread=thread
    )

    return JsonResponse({
        "status": "ok",
        "message": {
            "id": msg.id,
            "username": msg.sender.username,
            "content": msg.content,
            "image_url": presigned_url(msg.image) if msg.image else None,
            "timestamp": msg.timestamp.strftime("%Y-%m-%d %H:%M:%S")
        }
    })



@login_required
def list_messages(request):
    group_id = request.GET.get("group_id") or None
    thread_id = request.GET.get("thread_id") or None
    print("list_messages called with group_id:", group_id, "thread_id:", thread_id)

    # Convert thread_id to integer if present
    if thread_id is not None:
        try:
            thread_id = int(thread_id)
        except ValueError:
            thread_id = None

    if thread_id:
        # safe to fetch DM
        thread = DirectMessageThread.objects.get(id=thread_id)
        messages = thread.messages.all()
        print("Fetched messages for thread:", thread.id)
    elif group_id:
        group = ChatGroup.objects.get(slug=group_id)
        messages = group.messages.all()
        print("Fetched messages for group:", group.name)
    else:
        return JsonResponse({"error": "Missing group_id or thread_id"}, status=400)

    return JsonResponse({
        "messages": [
            {
                "id": m.id,
                "username": m.sender.username,
                "content": m.content,
                "image_url": presigned_url(m.image) if m.image else None,
                "timestamp": m.timestamp.strftime("%Y-%m-%d %H:%M:%S")
            }
            for m in messages
        ]
    })
    
@login_required
def json_notifications(request):
    user = request.user

    group_unreads = []
    dm_unreads = []

    # GROUPS
    for group in user.chat_groups.all():
        last_read = LastRead.objects.filter(user=user, group=group).first()
        if last_read:
            unread = group.messages.filter(
                timestamp__gt=last_read.last_read_at
            ).exclude(sender=user).count()
        else:
            unread = group.messages.exclude(sender=user).count()

        group_unreads.append({
            "group_id": group.id,
            "unread_count": unread
        })

    # DIRECT MESSAGES
    for thread in user.direct_message.all():
        last_read = LastRead.objects.filter(user=user, thread=thread).first()
        if last_read:
            unread = thread.messages.filter(
                timestamp__gt=last_read.last_read_at
            ).exclude(sender=user).count()
        else:
            unread = thread.messages.exclude(sender=user).count()  # FIXED

        dm_unreads.append({
            "thread_id": thread.id,
            "unread_count": unread
        })


    return JsonResponse({"groups": group_unreads, "threads": dm_unreads})

    

from django.http import JsonResponse, HttpResponseForbidden
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
import json

import json

@login_required
def edit_message(request, message_id):
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=400)

    try:
        msg = Message.objects.get(id=message_id)
    except Message.DoesNotExist:
        return JsonResponse({"error": "Message not found"}, status=404)

    if msg.sender != request.user:
        return HttpResponseForbidden("You cannot edit this message")

    data = json.loads(request.body)
    new_content = data.get("content", "").strip()

    if new_content == "" and not msg.image:
        return JsonResponse({"error": "Message cannot be empty"}, status=400)

    msg.content = new_content
    msg.save()

    return JsonResponse({
        "success": True,
        "message_id": msg.id,
        "content": msg.content,
        "image_url": msg.image,
    })


@login_required
def delete_message(request, message_id):
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=400)

    try:
        msg = Message.objects.get(id=message_id)
    except Message.DoesNotExist:
        return JsonResponse({"error": "Message not found"}, status=404)

    if msg.sender != request.user:
        return HttpResponseForbidden("You cannot delete this message")

    msg.content = "(deleted)"
    msg.image = None
    msg.save()

    return JsonResponse({"success": True, "message_id": msg.id})






        

